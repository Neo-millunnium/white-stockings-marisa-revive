# -*- coding: utf-8 -*-
"""
业务服务:教学/回复/忘记/状态 + 内存倒排索引 + 每 IP 限流 + 最近未命中记录。

设计要点:
- 启动时把全表加载成内存倒排索引(分词 -> 记忆ID 集合),Add/Delete 时增量维护,
  Reply 直接查索引而不是全表扫描。
- 数据量小且单进程运行,索引一致性不做复杂处理,仅加简单互斥锁。
"""
import json
import os
import random
import re
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
REPLY_RATE_LIMIT = 30        # 每 IP 每分钟最多回复次数
RATE_WINDOW = 60             # 限流时间窗口(秒)
MISS_LOG_MAX = 50            # 内存中保留的最近未命中关键词条数

# ---- 教学分类(源自 2010 年原始 QQ 调教 bot 的 teach 指令体系)----
# 原版玩法:teach word / teach sentence / teach syntax / teach logic / teach greeting 分门别类教学,
# teach auto 自动判定分类,exit 中止教学。网页版(iris/gin/1.0.0 分支)已简化掉分类,此处按原版语义恢复。
TEACH_CATEGORIES = ("word", "sentence", "syntax", "logic", "greeting", "auto")
# 问候词表:teach auto 自动归类为 greeting 的命中词(原始关键词或任一分词命中即算)
GREETING_WORDS = (
    "你好", "您好", "hello", "hi", "嗨", "哈喽", "在吗", "在不在", "在不",
    "早安", "早上好", "晚安", "晚上好", "好久不见", "新年好", "新年快乐",
    "拜拜", "再见", "谢谢", "多谢", "感谢", "辛苦了", "hey",
)

# 各教学分类的回复命中重合度阈值(分类影响匹配灵敏度):
# - word 词汇:0.4 最严谨(与旧版一致)
# - sentence 句型:0.3 放宽——教的是"完整句子",沾边就答
# - logic 逻辑:0.2 最灵敏——"提到关键词就答"的条件式记忆
# - syntax 文法:0.4 但先忽略虚词——教的是句子结构,语气词不干扰匹配
# - greeting 问候语:0.4 + 精确匹配优先(见 reply)
# 未分类(unclassified/旧数据)保持 0.4,行为与旧版完全一致
CATEGORY_THRESHOLDS = {
    "word": 0.4,
    "sentence": 0.3,
    "syntax": 0.4,
    "logic": 0.2,
    "greeting": 0.4,
    "": 0.4,
}
# 语气助词/结构虚词:syntax 文法类匹配时从分词里剔除(教的"结构"不受语气词干扰)
SYNTAX_STOPWORDS = frozenset(
    "的 地 得 了 着 过 吗 呢 啊 吧 呀 嘛 哦 哈 啦 哟 呗 么".split()
)

# ---- 深夜催睡(产品逻辑,勿删)----
# 凌晨 3:50 ~ 6:00 之间不回复正常内容,只输出固定催睡话术(与"2010 年调教 bot"同款玩法)
SLEEP_START = (3, 50)        # (时, 分) 催睡开始
SLEEP_END = (6, 0)           # (时, 分) 催睡结束(6:00 整恢复正常)
SLEEP_ANSWER = "喂!都这个点了还不去睡觉?!熬夜会变丑的,明天还要一起偷书呢,快去睡!"

# 加载 jieba 默认词典(启动时初始化)
jieba.initialize()

# ---- 违禁词正则前处理 ----
# 从 banned_words.txt 加载正则列表(每行一个,支持 Python re 语法;# 开头为注释)。
# 教学 Add 时先匹配回答,命中直接拒绝并拉黑,不进入待审队列(减少 AI 审核负担)。
_BANNED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banned_words.txt")
_banned_patterns = []


def _load_banned_patterns():
    """(重新)加载违禁词正则列表。文件缺失/全注释时为空列表(前处理不生效)。"""
    global _banned_patterns
    patterns = []
    try:
        with open(_BANNED_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    patterns.append(re.compile(line))
                except re.error:
                    pass  # 单条正则写错不影响其他条
    except OSError:
        pass
    _banned_patterns = patterns
    return patterns


_load_banned_patterns()


def match_banned(text_to_check):
    """返回第一个命中的违禁词正则对象,未命中返回 None。"""
    for pat in _banned_patterns:
        if pat.search(text_to_check or ""):
            return pat
    return None


@dataclass
class MemoryEntry:
    """内存中的一条记忆(启动时从库加载,Add/Delete 时同步维护)。"""
    memory_id: int
    tokens: list        # 分词列表(有序去重)
    answer: str
    raw_keyword: str    # 用户教学时的原始关键词(精确匹配用)
    hit_count: int = 0
    review_status: str = "pending"  # pending/approved/rejected(旧数据 NULL 视为 approved)
    category: str = ""  # 教学分类 word/sentence/syntax/logic/greeting(旧数据 NULL 视为未分类)


class MemoryIndex:
    """内存倒排索引:分词 -> 记忆ID 集合。"""

    def __init__(self):
        self._index = defaultdict(set)  # 分词 -> {memory_id, ...}
        self._memories = {}             # memory_id -> MemoryEntry
        self._lock = threading.Lock()   # 简单互斥锁,保证单进程内一致性

    def rebuild(self, session):
        """启动时从数据库全量重建索引(只加载已过审 approved 的内容)。"""
        with self._lock:
            self._index.clear()
            self._memories.clear()
            for row in session.query(models.Memorise).all():
                entry = self._to_entry(row)
                if entry.review_status != "approved":
                    continue  # pending 未过审、rejected 被拒的内容都不进入索引,不可回复
                self._memories[entry.memory_id] = entry
                for tok in entry.tokens:
                    self._index[tok].add(entry.memory_id)

    @staticmethod
    def _to_entry(row):
        """把 ORM 行转成内存条目;旧数据的 raw_keyword/hit_count/review_status/category 可能为 NULL,做兜底。"""
        raw = row.raw_keyword or ""
        hits = row.hit_count or 0
        status = row.review_status or "approved"  # 旧数据无审核列,视为已通过
        tokens = split_keyword(row.keyword) if row.keyword else []
        return MemoryEntry(row.memory_id, tokens, row.answer or "", raw, hits, status, row.category or "")

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


def overlap_ratio(existing_tokens, input_tokens, stopwords=None):
    """重合度 = 已有词条分词中出现在输入分词里的比例(与 Go 版 overlapRatio 一致)。

    stopwords 非空时(syntax 文法类)先从两侧剔除虚词再计算,语气词不干扰结构匹配。
    """
    if not existing_tokens:
        return 0.0
    if stopwords:
        existing_tokens = [t for t in existing_tokens if t not in stopwords]
        input_tokens = [t for t in input_tokens if t not in stopwords]
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


def detect_category(raw_keyword, tokens):
    """teach auto 的自动分类:原始关键词或任一分词命中问候词表 -> greeting,否则 -> word。

    其余分类(word/sentence/syntax/logic)机器无法可靠区分,auto 只做二选一,与 2010 原版\"不确定就 teach auto\"的语义一致。
    """
    text = (raw_keyword or "").strip().lower()
    toks = [t.lower() for t in (tokens or [])]
    for w in GREETING_WORDS:
        wl = w.lower()
        if wl in text or wl in toks:
            return "greeting"
    return "word"


class MemoriseService:
    """记忆服务:对 HTTP 层提供 Add/Reply/Forget/Status。"""

    def __init__(self):
        self.index = MemoryIndex()
        # 每 IP 的教学/回复时间戳队列(限流用,教学与回复独立计数),带独立锁
        self._add_logs = defaultdict(deque)
        self._reply_logs = defaultdict(deque)
        self._rate_lock = threading.Lock()
        # 最近未命中的输入 (时间, 关键词),最多保留 50 条,仅内存不落库。
        # 后续可基于它做"待学习清单",当前不需要暴露接口。
        self.recent_misses = deque(maxlen=MISS_LOG_MAX)
        # 审核黑名单:被拒回答原文的集合(启动时从库加载;add 时先查,命中即拒)
        self.blacklist = set()

    def reload(self):
        """启动时从数据库重建内存索引 + 加载黑名单。"""
        session = SessionLocal()
        try:
            self.index.rebuild(session)
            self._load_blacklist(session)
        finally:
            session.close()

    def _load_blacklist(self, session):
        """把 blacklist 表全部读进内存(回答原文精确匹配)。"""
        self.blacklist = {row.answer for row in session.query(models.Blacklist.answer).all()}

    # ---- 限流(轻量防滥用) ----
    def _check_rate_limit(self, ip, logs, limit):
        """通用限流检查:某 IP 在 RATE_WINDOW 秒窗口内是否已达 limit 次,返回是否放行。

        logs 是 {ip: deque[时间戳]} 字典(教学/回复各自独立维护)。
        """
        now = time.time()
        with self._rate_lock:
            q = logs[ip]
            # 清理超出窗口的时间戳
            while q and now - q[0] >= RATE_WINDOW:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def _check_add_rate(self, ip):
        """教学限流:每 IP 每分钟最多 ADD_RATE_LIMIT 次。"""
        return self._check_rate_limit(ip, self._add_logs, ADD_RATE_LIMIT)

    def _check_reply_rate(self, ip):
        """回复限流:每 IP 每分钟最多 REPLY_RATE_LIMIT 次。"""
        return self._check_rate_limit(ip, self._reply_logs, REPLY_RATE_LIMIT)

    # ---- 教学 ----
    def add(self, ip, keyword, answer, category="auto"):
        """教学:限流 -> 校验 -> 分词 -> 子集合并或新增 -> 入库并更新索引。

        category:word/sentence/syntax/logic/greeting/auto(auto 由问候词表自动判定)。
        """
        # 深夜催睡:凌晨 3:50 ~ 6:00 之间不接受教学(与 Reply 拦截一致)
        if in_sleep_window():
            return {"code": 400, "data": SLEEP_ANSWER}
        # 1. 防滥用限流(每 IP 每分钟最多 ADD_RATE_LIMIT 次)
        if not self._check_add_rate(ip or ""):
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
        # 2.5 教学分类校验:teach word/sentence/syntax/logic/greeting/auto;不合法直接拒绝
        cat = (category or "auto").strip().lower()
        if cat not in TEACH_CATEGORIES:
            return {"code": 400, "data": "教学分类不合法,可选:word/sentence/syntax/logic/greeting/auto"}
        # 3. 黑名单检查:该回答曾被 AI 审核拒绝过,直接拒绝教学(防止同一句违规反复提交)
        if ans in self.blacklist:
            return {"code": 400, "data": "这个回答好像不太妙,魔理沙拒绝记住它~"}
        # 3.5 违禁词前处理:正则命中违禁词的回答,直接拉黑 + 拒绝,不进待审队列
        if match_banned(ans):
            # 拉黑:入库 + 内存,防止换个关键词再教同一句
            try:
                session = SessionLocal()
                now = datetime.now()
                session.add(models.Blacklist(answer=ans, created_at=now))
                session.commit()
                self.blacklist.add(ans)
                session.close()
            except Exception:
                pass
            return {"code": 400, "data": "这个回答好像不太妙,魔理沙拒绝记住它~"}
        # 4. 分词(有序去重);分词结果为空时用原始关键词兜底
        tokens = cut_keyword(kw) or [kw]
        # 4.5 分类落值:auto 由问候词表自动判定(命中 -> greeting,否则 -> word)
        resolved_cat = detect_category(kw, tokens) if cat == "auto" else cat
        # 5. 合并逻辑改进(修复原 bug):仅当新分词集合完全包含于某条已有记忆的分词集合(子集)时才合并;
        #    合并后的 keyword 为"已有词条 + 新词"的有序去重;否则新增一条记忆。
        stored_tokens = tokens
        for entry in self.index.all_entries():
            existing = entry.tokens
            if existing and set(tokens) <= set(existing):
                stored_tokens = list(dict.fromkeys(existing + tokens))  # 有序去重
                break
        # 6. 入库(仅待审队列):新内容标记 pending,不进入内存索引、不可回复;
        #    每天 4:00 AI 审核通过后才进索引生效,被拒的回答进黑名单。
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
                review_status="pending",
                category=resolved_cat,
            )
            session.add(row)
            session.commit()
            session.refresh(row)  # 取回自增主键 memoryId
            # 注意:不调用 self.index.add —— pending 内容不进索引,审核通过才生效
            return {
                "code": 200,
                "data": {
                    "ip": ip or "",
                    "keyword": ",".join(stored_tokens),
                    "answer": ans,
                    "category": resolved_cat,
                },
            }
        finally:
            session.close()

    # ---- 回复 ----
    def reply(self, ip, keyword):
        """回复:先精确匹配 raw_keyword;否则分词查索引按重合度 >= 40% 命中,多条随机选一条。"""
        # 深夜催睡:凌晨 3:50 ~ 6:00 之间不对话,只输出固定催睡话术
        if in_sleep_window():
            return {"code": 200, "data": {"answer": SLEEP_ANSWER}}
        # 防滥用限流(每 IP 每分钟最多 REPLY_RATE_LIMIT 次)
        if not self._check_reply_rate(ip or ""):
            return {"code": 429, "data": "问得太频繁了,歇一歇吧~"}
        kw = (keyword or "").strip()
        if not kw:
            # 空输入直接按未命中处理(与 Go 版行为一致)
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        # 1. 精确匹配优先:raw_keyword 与输入完全相等时直接返回该条(权重最高)
        exact = [e for e in self.index.all_entries() if e.raw_keyword and e.raw_keyword == kw]
        if exact:
            # 多条时:greeting 分类优先(问候场景最先应答),同分类取最新一条
            chosen = max(exact, key=lambda e: (e.category == "greeting", e.memory_id))
            self._bump_hit(chosen)
            return {"code": 200, "data": {"answer": chosen.answer}}
        # 2. 分词后查倒排索引,收集重合度达到阈值的候选
        #    阈值按词条分类动态取:word 0.4 / sentence 0.3 / logic 0.2 /
        #    syntax 0.4(去虚词后算)/ greeting 0.4(另有精确匹配优先)
        tokens = cut_keyword(kw) or [kw]
        candidates = []
        for entry in self.index.search(tokens):
            cat = entry.category or ""
            threshold = CATEGORY_THRESHOLDS.get(cat, 0.4)
            stopwords = SYNTAX_STOPWORDS if cat == "syntax" else None
            if overlap_ratio(entry.tokens, tokens, stopwords) >= threshold:
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

    # ---- 分类统计 ----
    def categories(self):
        """各教学分类的记忆条数(仅已生效的 approved 索引),旧数据无分类计为 unclassified。"""
        stats = {c: 0 for c in ("word", "sentence", "syntax", "logic", "greeting")}
        stats["unclassified"] = 0
        for e in self.index.all_entries():
            cat = e.category or ""
            if cat in stats:
                stats[cat] += 1
            else:
                stats["unclassified"] += 1
        return {"code": 200, "data": stats}

    # ---- hint 提示线索 ----
    def hint(self):
        """随机返回一条已审核通过的记忆作为\"提示线索\"。

        对应界面的 hint 指令(查看其他人自定义的内容提示或小小线索)。
        知识库为空时返回兜底话术(业务码 10001)。
        """
        entries = [e for e in self.index.all_entries() if e.review_status != "rejected"]
        if not entries:
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        chosen = random.choice(entries)
        # 展示形式:关键词 -> 回答(让别人知道可以教什么/别人教过什么)
        return {
            "code": 200,
            "data": {
                "keyword": chosen.raw_keyword or chosen.answer,
                "answer": chosen.answer,
            },
        }

    # ---- AI 内容审核 ----
    def review_pending(self, limit=None, reviewer=None):
        """批量审核待审核内容(每天 4:00 定时调用)。

        - 取最多 limit 条 pending 内容(默认 REVIEW_DAILY_LIMIT,由调用方传入)
        - reviewer 为可调用对象 f(keyword, answer) -> {"violate": bool, "reason": str};
          未传则用 review.review_text
        - 违规:回答原文写入黑名单(库 + 内存),并从数据库删除(彻底清除,不可回复)
        - 正常:标记 approved,并加入内存索引(此时才真正生效、可被回复)
        - 审核抛异常:保持 pending,下轮重试
        返回 {"reviewed": n, "rejected": m, "errors": e} 便于日志。
        """
        import review as review_mod

        reviewer = reviewer or review_mod.review_text
        session = SessionLocal()
        try:
            pending = (
                session.query(models.Memorise)
                .filter(models.Memorise.review_status == "pending")
                .order_by(models.Memorise.memory_id)
                .limit(limit)
                .all()
            )
            reviewed = rejected = errors = 0
            for row in pending:
                try:
                    verdict = reviewer(row.keyword or "", row.answer or "")
                except Exception:
                    errors += 1
                    continue  # API 失败保持 pending,下轮重试
                if verdict.get("violate"):
                    # 违规:回答原文进黑名单(库+内存),删行,不进索引
                    now = datetime.now()
                    bl = models.Blacklist(answer=row.answer or "", created_at=now)
                    session.add(bl)
                    session.delete(row)
                    self.blacklist.add(row.answer or "")
                    rejected += 1
                else:
                    # 通过:标记 approved 并加入内存索引,此时才真正生效
                    row.review_status = "approved"
                    row.updated_at = datetime.now()
                    tokens = split_keyword(row.keyword) if row.keyword else []
                    self.index.add(MemoryEntry(
                        row.memory_id, tokens, row.answer or "",
                        row.raw_keyword or "", row.hit_count or 0, "approved", row.category or "",
                    ))
                reviewed += 1
            session.commit()
            return {"reviewed": reviewed, "rejected": rejected, "errors": errors}
        finally:
            session.close()
