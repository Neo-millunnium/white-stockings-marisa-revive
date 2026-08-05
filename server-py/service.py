# -*- coding: utf-8 -*-
"""
业务服务:教学/回复/忘记/状态 + 内存倒排索引 + 每 IP 限流 + 最近未命中记录。

设计要点:
- 启动时把全表加载成内存倒排索引(分词 -> 记忆ID 集合),Add/Delete 时增量维护,
  Reply 直接查索引而不是全表扫描。
- 数据量小且单进程运行,索引一致性不做复杂处理,仅加简单互斥锁。
- 分词用 jieba(加载时 jieba.initialize(),不需要自定义词典)。
"""
import random
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

import jieba
from sqlalchemy import text

import models
from database import SessionLocal

# ---- 业务常量 ----
NOT_FOUND_ANSWER = "唔嗯...不懂你在说什么呢...教教我吧~"  # 未命中的兜底话术(与 Go 版一致)
REPLY_THRESHOLD = 0.4        # 回复命中重合度阈值(与 Go 版一致)
KEYWORD_MAX_LEN = 50         # 关键词最大长度(去首尾空白后校验)
ANSWER_MAX_LEN = 500         # 回答最大长度(去首尾空白后校验)
ADD_RATE_LIMIT = 10          # 每 IP 每分钟最多教学次数
RATE_WINDOW = 60             # 限流时间窗口(秒)
MISS_LOG_MAX = 50            # 内存中保留的最近未命中关键词条数

# ---- 深夜催睡(产品逻辑,勿删)----
# 凌晨 3:50 ~ 6:00 之间不回复正常内容,只输出固定催睡话术(与"2010 年调教 bot"同款玩法)
SLEEP_START = (3, 50)        # (时, 分) 催睡开始
SLEEP_END = (6, 0)           # (时, 分) 催睡结束(6:00 整恢复正常)
SLEEP_ANSWER = "喂!都这个点了还不去睡觉?!熬夜会变丑的,明天还要一起偷书呢,快去睡!"

# 加载 jieba 默认词典(启动时初始化)
jieba.initialize()


@dataclass
class MemoryEntry:
    """内存中的一条记忆(启动时从库加载,Add/Delete 时同步维护)。"""
    memory_id: int
    tokens: list        # 分词列表(有序去重)
    answer: str
    raw_keyword: str    # 用户教学时的原始关键词(精确匹配用)
    hit_count: int = 0


class MemoryIndex:
    """内存倒排索引:分词 -> 记忆ID 集合。"""

    def __init__(self):
        self._index = defaultdict(set)  # 分词 -> {memory_id, ...}
        self._memories = {}             # memory_id -> MemoryEntry
        self._lock = threading.Lock()   # 简单互斥锁,保证单进程内一致性

    def rebuild(self, session):
        """启动时从数据库全量重建索引。"""
        with self._lock:
            self._index.clear()
            self._memories.clear()
            for row in session.query(models.Memorise).all():
                entry = self._to_entry(row)
                self._memories[entry.memory_id] = entry
                for tok in entry.tokens:
                    self._index[tok].add(entry.memory_id)

    @staticmethod
    def _to_entry(row):
        """把 ORM 行转成内存条目;旧数据的 raw_keyword/hit_count 可能为 NULL,做兜底。"""
        raw = row.raw_keyword or ""
        hits = row.hit_count or 0
        tokens = split_keyword(row.keyword) if row.keyword else []
        return MemoryEntry(row.memory_id, tokens, row.answer or "", raw, hits)

    def add(self, entry):
        """教学后增量加入索引。"""
        with self._lock:
            self._memories[entry.memory_id] = entry
            for tok in entry.tokens:
                self._index[tok].add(entry.memory_id)

    def remove(self, memory_id):
        """删除后从索引移除。"""
        with self._lock:
            entry = self._memories.pop(memory_id, None)
            if entry is None:
                return
            for tok in entry.tokens:
                ids = self._index.get(tok)
                if ids:
                    ids.discard(memory_id)
                    if not ids:
                        del self._index[tok]

    def search(self, tokens):
        """按输入分词查索引,返回所有至少含一个分词的记忆条目(供重合度计算)。"""
        with self._lock:
            ids = set()
            for tok in tokens:
                ids |= self._index.get(tok, set())
            return [self._memories[i] for i in ids if i in self._memories]

    def all_entries(self):
        """返回全部记忆条目副本(教学合并判断 / 精确匹配用)。"""
        with self._lock:
            return list(self._memories.values())

    def count(self):
        """当前记忆总条数。"""
        return len(self._memories)


def split_keyword(keyword_str):
    """把库里逗号连接的 keyword 拆成分词列表(保持原顺序,过滤空串)。"""
    return [t for t in keyword_str.split(",") if t]


def cut_keyword(keyword):
    """jieba 分词 + 有序去重。"""
    return list(dict.fromkeys(jieba.lcut(keyword)))


def overlap_ratio(existing_tokens, input_tokens):
    """重合度 = 已有词条分词中出现在输入分词里的比例(与 Go 版 overlapRatio 一致)。"""
    if not existing_tokens:
        return 0.0
    input_set = set(input_tokens)
    matched = sum(1 for t in existing_tokens if t in input_set)
    return matched / len(existing_tokens)


def in_sleep_window(now=None):
    """是否处于深夜催睡时段(凌晨 SLEEP_START ~ SLEEP_END)。

    支持注入 now 参数便于测试;默认取当前时间。
    """
    now = now or datetime.now()
    cur = (now.hour, now.minute)
    # 单日区间,不跨午夜:start <= t < end
    return SLEEP_START <= cur < SLEEP_END


class MemoriseService:
    """记忆服务:对 HTTP 层提供 Add/Reply/Forget/Status。"""

    def __init__(self):
        self.index = MemoryIndex()
        # 每 IP 的教学时间戳队列(限流用),带独立锁
        self._add_logs = defaultdict(deque)
        self._rate_lock = threading.Lock()
        # 最近未命中的输入 (时间, 关键词),最多保留 50 条,仅内存不落库。
        # 后续可基于它做"待学习清单",当前不需要暴露接口。
        self.recent_misses = deque(maxlen=MISS_LOG_MAX)

    def reload(self):
        """启动时从数据库重建内存索引。"""
        session = SessionLocal()
        try:
            self.index.rebuild(session)
        finally:
            session.close()

    # ---- 限流(轻量防滥用) ----
    def _check_rate_limit(self, ip):
        """检查该 IP 最近 RATE_WINDOW 秒内教学次数是否已达上限,返回是否放行。"""
        now = time.time()
        with self._rate_lock:
            q = self._add_logs[ip]
            # 清理超出窗口的时间戳
            while q and now - q[0] >= RATE_WINDOW:
                q.popleft()
            if len(q) >= ADD_RATE_LIMIT:
                return False
            q.append(now)
            return True

    # ---- 教学 ----
    def add(self, ip, keyword, answer):
        """教学:限流 -> 校验 -> 分词 -> 子集合并或新增 -> 入库并更新索引。"""
        # 1. 防滥用限流(每 IP 每分钟最多 ADD_RATE_LIMIT 次)
        if not self._check_rate_limit(ip or ""):
            return {"code": 429, "data": "教学太频繁了,休息一下吧~"}
        # 2. 输入校验(新增逻辑:关键词/回答非空,长度限制)
        kw = (keyword or "").strip()
        ans = (answer or "").strip()
        if not kw:
            return {"code": 400, "data": "参数不合法:关键词不能为空"}
        if len(kw) > KEYWORD_MAX_LEN:
            return {"code": 400, "data": "参数不合法:关键词长度不能超过%d" % KEYWORD_MAX_LEN}
        if not ans:
            return {"code": 400, "data": "参数不合法:回答不能为空"}
        if len(ans) > ANSWER_MAX_LEN:
            return {"code": 400, "data": "参数不合法:回答长度不能超过%d" % ANSWER_MAX_LEN}
        # 3. 分词(有序去重);分词结果为空时用原始关键词兜底
        tokens = cut_keyword(kw) or [kw]
        # 4. 合并逻辑改进(修复原 bug):仅当新分词集合完全包含于某条已有记忆的分词集合(子集)时才合并;
        #    合并后的 keyword 为"已有词条 + 新词"的有序去重;否则新增一条记忆。
        stored_tokens = tokens
        for entry in self.index.all_entries():
            existing = entry.tokens
            if existing and set(tokens) <= set(existing):
                stored_tokens = list(dict.fromkeys(existing + tokens))  # 有序去重
                break
        # 5. 入库
        session = SessionLocal()
        try:
            now = datetime.now()
            row = models.Memorise(
                ip=ip or "",
                keyword=",".join(stored_tokens),
                answer=ans,
                raw_keyword=kw,
                hit_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)  # 取回自增主键 memoryId
            # 6. 同步更新内存索引
            self.index.add(MemoryEntry(row.memory_id, stored_tokens, ans, kw, 0))
            return {
                "code": 200,
                "data": {
                    "ip": ip or "",
                    "keyword": ",".join(stored_tokens),
                    "answer": ans,
                },
            }
        finally:
            session.close()

    # ---- 回复 ----
    def reply(self, keyword):
        """回复:先精确匹配 raw_keyword;否则分词查索引按重合度 >= 40% 命中,多条随机选一条。"""
        # 深夜催睡:凌晨 3:50 ~ 6:00 之间不对话,只输出固定催睡话术
        if in_sleep_window():
            return {"code": 200, "data": {"answer": SLEEP_ANSWER}}
        kw = (keyword or "").strip()
        if not kw:
            # 空输入直接按未命中处理(与 Go 版行为一致)
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        # 1. 精确匹配优先:raw_keyword 与输入完全相等时直接返回该条(权重最高)
        exact = [e for e in self.index.all_entries() if e.raw_keyword and e.raw_keyword == kw]
        if exact:
            chosen = max(exact, key=lambda e: e.memory_id)  # 多条时取最新一条
            self._bump_hit(chosen)
            return {"code": 200, "data": {"answer": chosen.answer}}
        # 2. 分词后查倒排索引,收集重合度达到阈值的候选
        tokens = cut_keyword(kw) or [kw]
        candidates = []
        for entry in self.index.search(tokens):
            if overlap_ratio(entry.tokens, tokens) >= REPLY_THRESHOLD:
                candidates.append(entry)
        # 3. 未命中:记录最近未命中的关键词(内存,最多 50 条),返回兜底话术
        if not candidates:
            self.recent_misses.append((time.time(), kw))
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        # 4. 命中多条时随机选一条(新增,原实现固定返回第一条)
        chosen = random.choice(candidates)
        self._bump_hit(chosen)
        return {"code": 200, "data": {"answer": chosen.answer}}

    def _bump_hit(self, entry):
        """命中计数 +1:同步更新内存与数据库。"""
        entry.hit_count += 1
        session = SessionLocal()
        try:
            session.execute(
                text(
                    "UPDATE memorise SET hit_count = hit_count + 1, updated_at = :t "
                    "WHERE memoryId = :id"
                ),
                {"t": datetime.now(), "id": entry.memory_id},
            )
            session.commit()
        except Exception:
            # 命中计数只是统计信息,失败不影响回复功能
            pass
        finally:
            session.close()

    # ---- 忘记 ----
    def forget(self, answer):
        """按 answer 精确删除(可删除多条同 answer 的记录),成功返回 success。"""
        ans = (answer or "").strip()
        session = SessionLocal()
        try:
            rows = session.query(models.Memorise).filter(models.Memorise.answer == ans).all()
            ids = [r.memory_id for r in rows]
            # 先从内存索引移除,避免删除过程中被 Reply 命中到已删数据
            for mid in ids:
                self.index.remove(mid)
            for r in rows:
                session.delete(r)
            session.commit()
            return {"code": 200, "data": "success"}
        finally:
            session.close()

    # ---- 状态 ----
    def status(self):
        """返回当前记忆总条数。"""
        return {"code": 200, "data": self.index.count()}
