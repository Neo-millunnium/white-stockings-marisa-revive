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
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

import jieba
from sqlalchemy import text

import config
import models
from database import SessionLocal
from safe_eval import safe_eval
from tools import match_tool

# ---- 业务常量 ----
NOT_FOUND_ANSWER = "唔嗯...不懂你在说什么呢...教教我吧~"  # 未命中的兜底话术(与 Go 版一致)
REPLY_THRESHOLD = 0.4        # 回复命中重合度阈值(与 Go 版一致)
KEYWORD_MAX_LEN = 50         # 关键词最大长度(去首尾空白后校验)
ANSWER_MAX_LEN = 500         # 回答最大长度(去首尾空白后校验)
ADD_RATE_LIMIT = 10          # 每 IP 每分钟最多教学次数
REPLY_RATE_LIMIT = 30        # 每 IP 每分钟最多回复次数
RATE_WINDOW = 60             # 限流时间窗口(秒)
MISS_LOG_MAX = 50            # 内存中保留的最近未命中关键词条数
MISS_LIST_MAX = 20           # 待学习清单最多返回的条数(按 miss_count 降序)
# ---- 防分布式注入(换 IP 多来源攻击)----
GLOBAL_ADD_RATE_LIMIT = 100      # 全站教学合计:每分钟最多 100 次(防多 IP 打爆审核队列)
ANSWER_FP_WINDOW = 600           # 回答内容指纹统计窗口(秒)= 10 分钟
ANSWER_FP_MAX_IPS = 5            # 同一回答指纹窗口内被 >=5 个不同 IP 提交即拒绝(防批量刷库)
ANSWER_FP_MAX_TOTAL = 20         # 同一回答指纹窗口内总提交 >=20 也拒绝(防单 IP 换关键词刷)
IP_REJECT_BAN_WINDOW = 1800      # IP 信誉统计窗口(秒)= 30 分钟
IP_REJECT_BAN_THRESHOLD = 5      # 窗口内教学被拒 >=5 次
IP_OK_MIN = 3                    # 且教学成功 <3 次 -> 临时拉黑该 IP 教学

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

# ---- P2 好感度(F-I,FEATURE_FAVOR)----
# 每次成功对话 / 教学 / 在线时长都加分,等级由分数映射(高好感解锁 flag=favor:* 专属回答)
FAVOR_SCORE_TALK = 1        # 每次成功对话 +1
FAVOR_SCORE_TEACH = 3       # 每次教学成功 +3
FAVOR_SCORE_ACTIVE = 1      # 每 60s 在线 +1(心跳合并计)
FAVOR_PRAISE_WORDS = ("喜欢", "可爱", "厉害", "棒", "最好", "谢谢")  # 夸奖词:命中额外 +2
# 等级表:(分数下限, 等级名);level 取满足 score >= 阈值的最后一个下标
FAVOR_LEVELS = [(0, "冷淡"), (50, "普通"), (150, "好感"), (300, "亲密"), (500, "恋慕")]

# ---- P4 对象判断 flag 的时段窗口(FEATURE_FLAG)----
# 注意 night(22:00-5:00)/ dawn(5:00-8:00) 跨午夜,判断时要处理 start >= end 的情况
FLAG_TIME_WINDOWS = {
    "dawn": (5, 0, 8, 0),
    "day": (8, 0, 18, 0),
    "dusk": (18, 0, 22, 0),
    "night": (22, 0, 5, 0),
}

# P3 话题感知(FEATURE_THEME):每用户(uid 或 ip)最近维护的对话轮数
THEME_CONTEXT_MAXLEN = 8   # 每用户最多保留多少轮输入分词
THEME_TOPIC_MAX = 5        # 主题词最多取几个(top5)

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
    flag: str = "all"   # 对象判断条件(P4):all/user:<uid>/favor:* /time:*(旧数据 NULL 视为 all)


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
        """把 ORM 行转成内存条目;旧数据的 raw_keyword/hit_count/review_status/category/flag 可能为 NULL,做兜底。"""
        raw = row.raw_keyword or ""
        hits = row.hit_count or 0
        status = row.review_status or "approved"  # 旧数据无审核列,视为已通过
        tokens = split_keyword(row.keyword) if row.keyword else []
        return MemoryEntry(row.memory_id, tokens, row.answer or "", raw, hits, status,
                           row.category or "", row.flag or "all")

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


# ---- 工具结果人设话术(魔理沙:元气 / 自称天才 / だぜ / 借书梗)----
# 每个工具的前缀+后缀;未知工具返回裸文本
_TOOL_TALK = {
    "time": ("魔法时钟告诉我:", "。可别把时间浪费掉,魔法研究要紧だぜ!"),
    "calc": ("本天才的心算魔法给出了答案:", "。这种程度,对本天才来说小菜一碟だぜ!"),
    "weather": ("我让迷你八卦炉感应了一下天气:", "。嗯,本天才的预报从没错过!"),
    "dict": ("我去书库查了查(顺便借走几本):", "。这些可都是书里写的,不是我瞎编的!"),
    "exchange": ("我看了看魔法世界的汇率:", "。钱的事,交给本天才就好!"),
}


def wrap_tool_talk(name, ans):
    """给工具结果套上魔理沙人设话术;未知工具返回裸文本。"""
    prefix, suffix = _TOOL_TALK.get(name, ("", ""))
    return "%s%s%s" % (prefix, ans, suffix)


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
        self._active_logs = defaultdict(deque)   # 好感心跳限流(P2,每 uid 每分钟 1 次)
        self._rate_lock = threading.Lock()
        # P3 话题感知(FEATURE_THEME):uid 或 ip -> 最近 THEME_CONTEXT_MAXLEN 轮输入分词,仅内存
        self._context = defaultdict(lambda: deque(maxlen=THEME_CONTEXT_MAXLEN))
        # ---- 防分布式注入状态(均仅内存,重启清零)----
        self._add_global_logs = deque()          # 全站教学合计时间戳队列
        self._answer_fps = {}                     # 回答指纹 -> deque[(ts, ip)]
        self._ip_ok = defaultdict(int)            # IP -> 窗口内教学成功次数
        self._ip_rejects = defaultdict(deque)     # IP -> deque[被拒时间戳]
        self._ip_bans = {}                        # IP -> ban 到期时间戳
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

    def _check_active_rate(self, uid):
        """好感心跳限流(P2,FEATURE_FAVOR):每 uid 每分钟最多 1 次,防挂机刷分。"""
        return self._check_rate_limit(uid, self._active_logs, 1)

    # ---- 防分布式注入(全局限流 / 内容指纹 / IP 信誉)----
    @staticmethod
    def _fp_of_answer(ans):
        """回答内容指纹:归一化(去空白/标点/转小写)后 sha256。"""
        norm = re.sub(r"[\s\W_]+", "", ans, flags=re.UNICODE).lower()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    def _check_global_add_rate(self):
        """全局教学限流:全站所有来源合计每分钟 <= GLOBAL_ADD_RATE_LIMIT 次。"""
        now = time.time()
        with self._rate_lock:
            q = self._add_global_logs
            while q and now - q[0] >= RATE_WINDOW:
                q.popleft()
            if len(q) >= GLOBAL_ADD_RATE_LIMIT:
                return False
            q.append(now)
            return True

    def _check_answer_fp(self, ip, ans):
        """回答内容指纹去重:窗口内同一指纹被过多不同 IP / 总次数提交 -> 拒绝。

        返回 (是否放行, 指纹)。
        """
        fp = self._fp_of_answer(ans)
        now = time.time()
        with self._rate_lock:
            q = self._answer_fps.get(fp)
            if q is None:
                self._answer_fps[fp] = deque([(now, ip)])
                return True, fp
            while q and now - q[0][0] >= ANSWER_FP_WINDOW:
                q.popleft()
            ips = {p for _, p in q}
            if len(ips) >= ANSWER_FP_MAX_IPS or len(q) >= ANSWER_FP_MAX_TOTAL:
                return False, fp
            q.append((now, ip))
            return True, fp

    def _record_reject(self, ip):
        """记录一次教学拒绝(用于 IP 信誉)。"""
        now = time.time()
        with self._rate_lock:
            q = self._ip_rejects[ip]
            while q and now - q[0] >= IP_REJECT_BAN_WINDOW:
                q.popleft()
            q.append(now)

    def _check_ip_ban(self, ip):
        """IP 信誉:窗口内被拒多且成功少 -> 临时拉黑教学。

        返回 (是否放行, ban 到期时间戳)。
        """
        now = time.time()
        with self._rate_lock:
            until = self._ip_bans.get(ip, 0)
            if until > now:
                return False, until
            q = self._ip_rejects[ip]
            while q and now - q[0] >= IP_REJECT_BAN_WINDOW:
                q.popleft()
            if len(q) >= IP_REJECT_BAN_THRESHOLD and self._ip_ok.get(ip, 0) < IP_OK_MIN:
                self._ip_bans[ip] = now + IP_REJECT_BAN_WINDOW
                return False, now + IP_REJECT_BAN_WINDOW
            return True, 0

    # ---- P2 好感度(F-I,FEATURE_FAVOR)----
    @staticmethod
    def _favor_level_for_score(score):
        """分数 -> 等级下标(取 score >= 阈值的最深一档)。"""
        level = 0
        for i, (threshold, _name) in enumerate(FAVOR_LEVELS):
            if score >= threshold:
                level = i
            else:
                break
        return level

    def _favor_level(self, uid):
        """查询某 uid 当前好感等级(无记录返回 0)。"""
        if not uid:
            return 0
        session = SessionLocal()
        try:
            row = session.query(models.Favorability).filter(models.Favorability.uid == uid).first()
            return row.level if row else 0
        except Exception:
            return 0
        finally:
            session.close()

    def _favor_score(self, uid):
        """查询某 uid 当前好感分数(theme 打分选择器用;无 uid/异常返回 0)。"""
        if not uid:
            return 0
        session = SessionLocal()
        try:
            row = session.query(models.Favorability).filter(models.Favorability.uid == uid).first()
            return row.score if row else 0
        except Exception:
            return 0
        finally:
            session.close()

    def _touch_favor(self, uid, ip, talk=0, teach=0, active_seconds=0, bonus=0):
        """好感累计(FEATURE_FAVOR 关闭时无操作):按 uid 主键 upsert 分数与计数。

        talk/teach 为对话/教学次数(各乘 FAVOR_SCORE_* 加分),bonus 为额外加分;
        active_seconds 按整分钟计分(seconds // 60 * FAVOR_SCORE_ACTIVE)。
        老客户端无 uid 时不记录(按 ip 兜底的意义仅在于区分,不累计)。
        """
        if not config.is_enabled("FAVOR"):
            return
        if not uid:
            return
        session = SessionLocal()
        try:
            row = session.query(models.Favorability).filter(models.Favorability.uid == uid).first()
            if row is None:
                row = models.Favorability(uid=uid, ip=ip or "", score=0, talk_count=0,
                                          teach_count=0, active_seconds=0, level=0,
                                          last_active_at=datetime.now())
                session.add(row)
            row.ip = ip or ""
            row.score += talk * FAVOR_SCORE_TALK + teach * FAVOR_SCORE_TEACH + bonus
            if active_seconds > 0:
                row.active_seconds += active_seconds
                row.score += active_seconds // 60 * FAVOR_SCORE_ACTIVE
            row.talk_count += talk
            row.teach_count += teach
            row.last_active_at = datetime.now()
            row.level = self._favor_level_for_score(row.score)
            session.commit()
        except Exception:
            pass  # 好感只是统计信息,失败不影响主流程
        finally:
            session.close()

    def favor(self, uid):
        """查询某 uid 的好感信息(FEATURE_FAVOR 关闭时由路由层 400 拦截)。"""
        uid = (uid or "").strip()
        empty = {"uid": uid, "score": 0, "level": 0, "level_name": FAVOR_LEVELS[0][1],
                 "talk_count": 0, "teach_count": 0, "active_seconds": 0}
        if not uid:
            return {"code": 200, "data": empty}
        session = SessionLocal()
        try:
            row = session.query(models.Favorability).filter(models.Favorability.uid == uid).first()
            if row is None:
                return {"code": 200, "data": empty}
            level_name = FAVOR_LEVELS[row.level][1] if 0 <= row.level < len(FAVOR_LEVELS) else FAVOR_LEVELS[0][1]
            return {"code": 200, "data": {
                "uid": uid, "score": row.score, "level": row.level, "level_name": level_name,
                "talk_count": row.talk_count, "teach_count": row.teach_count,
                "active_seconds": row.active_seconds,
            }}
        except Exception:
            return {"code": 200, "data": empty}
        finally:
            session.close()

    def active(self, uid, ip, seconds):
        """心跳(P2):上报在线秒数并累计好感(每 uid 每分钟限 1 次,路由层已校验参数)。"""
        if not self._check_active_rate(uid):
            return {"code": 429, "data": "太频繁了,歇一歇吧~"}
        self._touch_favor(uid, ip, active_seconds=seconds)
        return {"code": 200, "data": "ok"}

    # ---- P3 话题感知(FEATURE_THEME)----
    def _topic_tokens(self, key):
        """从某用户最近 N 轮输入分词里提取主题词:词频 >=2 或 top5,剔除虚词。

        返回主题词列表(可能是空),供打分选择器做主题契合加权。
        """
        q = self._context.get(key)
        if not q:
            return []
        freq = defaultdict(int)
        for toks in q:
            for t in toks:
                if t in SYNTAX_STOPWORDS:
                    continue
                freq[t] += 1
        if not freq:
            return []
        ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        result = [w for w, c in ranked if c >= 2]
        for w, _c in ranked[:THEME_TOPIC_MAX]:
            if w not in result:
                result.append(w)
        return result

    def _pick_candidate(self, candidates, topic_tokens, favor_score):
        """打分选择器(FEATURE_THEME):主题契合 0.5 + 好感 0.3 加权,最高者胜出。

        全 0(无主题 / 无好感差异)时回退 random.choice,保持现有"随机选一条"行为;
        FEATURE_THEME 关闭时也直接 random.choice(与旧版完全一致)。
        """
        if not candidates:
            return None
        if not config.is_enabled("THEME"):
            return random.choice(candidates)
        scored = []
        for e in candidates:
            s = overlap_ratio(e.tokens, topic_tokens) * 0.5
            s += min(favor_score or 0, 10) / 10 * 0.3
            scored.append((s, e))
        best = max(scored, key=lambda x: x[0])
        if best[0] > 0:
            return best[1]
        return random.choice(candidates)

    # ---- P4 对象判断 flag(FEATURE_FLAG)----
    @staticmethod
    def _valid_flag(flag):
        """flag 合法值:all / user:<uid> / favor:high|medium|low / time:dawn|day|dusk|night。"""
        if flag == "all":
            return True
        if flag.startswith("user:"):
            return len(flag) > len("user:")
        if flag in ("favor:high", "favor:medium", "favor:low"):
            return True
        if flag in ("time:dawn", "time:day", "time:dusk", "time:night"):
            return True
        return False

    def _flag_matches(self, entry, uid, now=None):
        """判断一条带 flag 的记忆是否匹配当前 uid / 好感等级 / 时间段。

        all 恒 True;user 精确匹配 uid;favor 按好感等级映射(low<=1 / medium=2 / high>=3);
        time 按 FLAG_TIME_WINDOWS(night/dawn 跨午夜,start>=end 时按"跨日"判断)。
        """
        flag = entry.flag or "all"
        if not flag or flag == "all":
            return True
        now = now or datetime.now()
        if flag.startswith("user:"):
            return bool(uid) and uid == flag[len("user:"):]
        if flag.startswith("favor:"):
            level = self._favor_level(uid)
            want = flag[len("favor:"):]
            if want == "high":
                return level >= 3
            if want == "medium":
                return level == 2
            if want == "low":
                return level <= 1
            return True
        if flag.startswith("time:"):
            wp = FLAG_TIME_WINDOWS.get(flag[len("time:"):])
            if not wp:
                return True
            cur = now.hour * 60 + now.minute
            start = wp[0] * 60 + wp[1]
            end = wp[2] * 60 + wp[3]
            if start < end:
                return start <= cur < end
            return cur >= start or cur < end  # 跨午夜(如 night 22:00 ~ 次日 5:00)
        return True

    # ---- P5 多人协作 + 调教师权限(FEATURE_MAID)----
    def _is_blocked(self, uid):
        """该 uid 是否被调教师屏蔽(屏蔽只拦教学,不拦提问)。"""
        if not uid:
            return False
        session = SessionLocal()
        try:
            return session.query(models.BlockedUser).filter(models.BlockedUser.uid == uid).first() is not None
        except Exception:
            return False
        finally:
            session.close()

    def _is_master(self, uid):
        """调教师判定:仅服务端配置 MASTER_UID 或 teacher 表成员(不信任客户端自报身份)。"""
        if not uid:
            return False
        if uid == config.get("MASTER_UID"):
            return True
        session = SessionLocal()
        try:
            return session.query(models.Teacher).filter(models.Teacher.uid == uid).first() is not None
        except Exception:
            return False
        finally:
            session.close()

    def block(self, uid, target_uid, action="block"):
        """屏蔽 / 解除屏蔽目标 uid(仅调教师可用;FEATURE_MAID 关闭时由路由层 400 拦截)。"""
        if not self._is_master(uid):
            return {"code": 400, "data": "无权限"}
        target_uid = (target_uid or "").strip()
        if not target_uid:
            return {"code": 400, "data": "参数不合法:缺少 target_uid"}
        session = SessionLocal()
        try:
            if action == "block":
                row = session.query(models.BlockedUser).filter(models.BlockedUser.uid == target_uid).first()
                if row is None:
                    session.add(models.BlockedUser(uid=target_uid, blocked_by=uid, created_at=datetime.now()))
            elif action == "unblock":
                session.query(models.BlockedUser).filter(models.BlockedUser.uid == target_uid).delete()
            else:
                return {"code": 400, "data": "参数不合法:action 只能是 block/unblock"}
            session.commit()
            return {"code": 200, "data": "success"}
        except Exception:
            return {"code": 400, "data": "操作失败"}
        finally:
            session.close()

    # ---- 教学 ----
    def add(self, ip, keyword, answer, category="auto", uid="", flag="all"):
        """教学:限流 -> 校验 -> 分词 -> 子集合并或新增 -> 入库并更新索引。

        category:word/sentence/syntax/logic/greeting/auto(auto 由问候词表自动判定)。
        uid:教学者匿名身份(P5 留痕 + P2 好感);flag:对象判断条件(P4,仅 FEATURE_FLAG 开启时校验)。
        老客户端不传 uid/flag 时按默认处理,行为与旧版一致。
        """
        # 深夜催睡:凌晨 3:50 ~ 6:00 之间不接受教学(与 Reply 拦截一致)
        if in_sleep_window():
            return {"code": 400, "data": SLEEP_ANSWER}
        # 1. 防滥用限流(每 IP 每分钟最多 ADD_RATE_LIMIT 次)
        if not self._check_add_rate(ip or ""):
            return {"code": 429, "data": "教学太频繁了,休息一下吧~"}
        # 1.5 全局教学限流(全站合计,防多 IP 分布式注入打爆审核队列)
        if not self._check_global_add_rate():
            return {"code": 429, "data": "教学太频繁了,休息一下吧~"}
        # 1.6 IP 信誉:教学被拒率高且成功少的 IP 临时拉黑
        ok_ip, _until = self._check_ip_ban(ip or "")
        if not ok_ip:
            return {"code": 429, "data": "教学太频繁了,休息一下吧~"}
        # 1.7 调教师屏蔽检查(FEATURE_MAID 开启时):被屏蔽的 uid 禁止教学
        if config.is_enabled("MAID") and uid and self._is_blocked(uid):
            return {"code": 400, "data": "你被禁止教学了"}
        # 2.5 教学分类校验:teach word/sentence/syntax/logic/greeting/auto;不合法直接拒绝
        cat = (category or "auto").strip().lower()
        if cat not in TEACH_CATEGORIES:
            return {"code": 400, "data": "教学分类不合法,可选:word/sentence/syntax/logic/greeting/auto"}
        # 2.6 输入校验:greeting 是单条问候语,允许关键词为空(只教一句话);
        #     其余分类必须是"关键词 -> 回答"问答对,两者都非空
        is_greeting = cat == "greeting"
        kw = (keyword or "").strip()
        ans = (answer or "").strip()
        if not kw and not is_greeting:
            return {"code": 400, "data": "参数不合法:关键词不能为空"}
        if len(kw) > KEYWORD_MAX_LEN:
            return {"code": 400, "data": "参数不合法:关键词长度不能超过%d" % KEYWORD_MAX_LEN}
        if not ans:
            return {"code": 400, "data": "参数不合法:回答不能为空"}
        if len(ans) > ANSWER_MAX_LEN:
            return {"code": 400, "data": "参数不合法:回答长度不能超过%d" % ANSWER_MAX_LEN}
        # 2.7 对象判断 flag 校验(FEATURE_FLAG 开启时;关闭时忽略参数,恒为 all,行为与旧版一致)
        #     兼容前端可选的 @ 前缀(教 `关键词`回答`@user:<uid> 与 `...`user:<uid> 等价)
        flag_val = "all"
        if config.is_enabled("FLAG"):
            flag_val = (flag or "all").strip().lstrip("@")
            if not self._valid_flag(flag_val):
                return {"code": 400, "data": "参数不合法:flag 只能是 all / user:<uid> / favor:high|medium|low / time:dawn|day|dusk|night"}
        # 3. 黑名单检查:该回答曾被 AI 审核拒绝过,直接拒绝教学(防止同一句违规反复提交)
        if ans in self.blacklist:
            self._record_reject(ip or "")
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
            self._record_reject(ip or "")
            return {"code": 400, "data": "这个回答好像不太妙,魔理沙拒绝记住它~"}
        # 3.7 回答内容指纹:同一回答被大量不同 IP / 总次数提交 -> 疑似批量刷库,拒绝
        ok_fp, _fp = self._check_answer_fp(ip or "", ans)
        if not ok_fp:
            self._record_reject(ip or "")
            return {"code": 400, "data": "这个回答已经有很多人教过啦,魔理沙不重复记~"}
        # 4. 分词(有序去重);分词结果为空时用原始关键词兜底
        tokens = cut_keyword(kw) or [kw]
        # 4.5 分类落值:auto 由问候词表自动判定(命中 -> greeting,否则 -> word)
        resolved_cat = detect_category(kw, tokens) if cat == "auto" else cat
        # 4.6 greeting 语义:不是问答对,是单条问候语(开场白)。
        #     关键词不参与检索——存储时清空 keyword/分词/raw_keyword,只保留 answer。
        #     前端 teach greeting 单轮输入,auto 判定为 greeting 时同样只取 answer 作为问候语。
        if resolved_cat == "greeting":
            tokens = []
            stored_tokens = []
            kw = ""  # raw_keyword 一并清空,问候语不参与任何问答匹配
        # 5. 合并逻辑改进(修复原 bug):仅当新分词集合完全包含于某条已有记忆的分词集合(子集)时才合并;
        #    合并后的 keyword 为"已有词条 + 新词"的有序去重;否则新增一条记忆。
        #    greeting 空分词不参与合并(空集是任何集合的子集,会误并入已有词条)。
        stored_tokens = tokens
        for entry in self.index.all_entries():
            existing = entry.tokens
            if tokens and existing and set(tokens) <= set(existing):
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
                flag=flag_val if config.is_enabled("FLAG") else "all",
                uid=(uid or "").strip() if config.is_enabled("MAID") else None,
            )
            session.add(row)
            session.commit()
            session.refresh(row)  # 取回自增主键 memoryId
            # 防注入:教学成功计数(IP 信誉用,窗口内不清零,重启清零)
            with self._rate_lock:
                self._ip_ok[ip or ""] += 1
            # 好感度:教学成功 +3(P2,仅 FEATURE_FAVOR 开启且 uid 非空时生效)
            if config.is_enabled("FAVOR"):
                self._touch_favor(uid, ip, teach=1)
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
    def reply(self, ip, keyword, uid=""):
        """回复:先精确匹配 raw_keyword;否则分词查索引按重合度 >= 40% 命中。

        uid(P2/P3/P4 身份层):老客户端不传时按 ip 兜底做上下文键;功能关闭时
        行为与旧版完全一致(精确匹配 -> 资讯工具 -> 重合命中随机选一条)。
        """
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
        # 身份键:uid 优先、ip 兜底(话题上下文等按用户维度记录状态)
        key = (uid or "").strip() or (ip or "")
        # P3 话题感知(FEATURE_THEME):把本轮输入分词推入上下文队列(关闭时完全不维护)
        if config.is_enabled("THEME"):
            self._context[key].append(cut_keyword(kw) or [kw])
            # 防内存膨胀:用户键超过 1 万时丢弃最早插入的键(dict 保序)
            if len(self._context) > 10000:
                try:
                    self._context.pop(next(iter(self._context)))
                except (StopIteration, RuntimeError):
                    pass
        # 1. 精确匹配优先:raw_keyword 与输入完全相等时直接返回该条(权重最高)
        #    greeting 类不是问答对(raw_keyword 为空),天然不参与精确匹配
        #    FEATURE_FLAG 开启时:带条件的精确匹配词条也要过 _flag_matches,避免私密台词漏给别人
        exact = [e for e in self.index.all_entries() if e.raw_keyword and e.raw_keyword == kw]
        if config.is_enabled("FLAG"):
            exact = [e for e in exact if (e.flag or "") == "all" or self._flag_matches(e, uid)]
        if exact:
            chosen = max(exact, key=lambda e: e.memory_id)  # 多条时取最新一条
            self._bump_hit(chosen)
            self._touch_favor(uid, ip, talk=1, bonus=2 if any(w in kw for w in FAVOR_PRAISE_WORDS) else 0)
            answer_text = self._render_answer(chosen)
            return {"code": 200, "data": {"answer": answer_text}}
        # 1.5 资讯工具:用户没显式教过时,时间/计算器等工具兜底。
        #     优先级低于精确匹配(教过的关键词永远赢过工具)、高于分词重合;
        #     深夜催睡在最顶部已拦截,工具不会在催睡时段触发。
        tool_result = match_tool(kw, ip)
        if tool_result:
            tool_name, tool_ans = tool_result
            return {"code": 200, "data": {"answer": wrap_tool_talk(tool_name, tool_ans)}}
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
        # 3. 未命中:记录最近未命中的关键词(内存,最多 50 条)+ 落库聚合(待学习清单),返回兜底话术
        if not candidates:
            self.recent_misses.append((time.time(), kw))
            self._miss_upsert(kw)
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        # 4. 命中后选择(FEATURE_FLAG 分组 -> FEATURE_THEME 打分选择器):
        #    - flag 开启:带条件(flag != all)的候选先看有没有满足条件的;有则从中选,
        #      无则回落到普通(all)候选,保证带条件的词条不命中时普通回答不受影响;
        #    - 否则全部候选直接参与选择;
        #    - theme 关闭时 _pick_candidate 回退 random.choice,行为与旧版完全一致。
        if config.is_enabled("FLAG"):
            flagged = [e for e in candidates if (e.flag or "") != "all"]
            unflagged = [e for e in candidates if not (e.flag or "") or (e.flag or "") == "all"]
            matched = [e for e in flagged if self._flag_matches(e, uid)]
            if matched:
                pool = matched
            elif unflagged:
                pool = unflagged
            else:
                # 全部候选都带条件且当前用户不满足:该问题对 ta 无可用回答,按未命中处理
                # (不回落全部候选,避免把别人的私密台词/定向台词漏出来)
                self.recent_misses.append((time.time(), kw))
                self._miss_upsert(kw)
                return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        else:
            pool = candidates
        topic_tokens = self._topic_tokens(key) if config.is_enabled("THEME") else []
        favor_score = self._favor_score(uid) if config.is_enabled("THEME") else 0
        chosen = self._pick_candidate(pool, topic_tokens, favor_score)
        self._bump_hit(chosen)
        self._touch_favor(uid, ip, talk=1, bonus=2 if any(w in kw for w in FAVOR_PRAISE_WORDS) else 0)
        answer_text = self._render_answer(chosen)
        return {"code": 200, "data": {"answer": answer_text}}

    def _render_answer(self, entry):
        """输出前的最终回答文本(FEATURE_CODE 开启且为 logic 分类时渲染 {表达式} 模板)。"""
        if config.is_enabled("CODE") and (entry.category or "") == "logic":
            return self._render_template(entry.answer)
        return entry.answer

    def _render_template(self, answer):
        """把 logic 分类回答里的 {表达式} 占位符替换为白名单求值结果(某段失败保留原文)。"""
        if not answer:
            return answer

        def _sub(m):
            val = safe_eval(m.group(1))
            return m.group(0) if val is None else str(val)

        return re.sub(r"\{([^{}]+)\}", _sub, answer)

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

    # ---- 待学习清单 ----
    def _miss_upsert(self, kw):
        """未命中关键词落库聚合(待学习清单):count+1、更新 last_seen,首次写入 first_seen。

        关键词做归一化(去空白/标点/转小写)后存储,避免 "xx " 与 "xx" 重复计数;
        全符号输入归一化后为空时保留原文,避免落空行。DB 异常不影响主流程(统计信息)。
        """
        norm = re.sub(r"[\s\W_]+", "", kw, flags=re.UNICODE).lower() or kw
        session = SessionLocal()
        try:
            now = datetime.now()
            row = session.query(models.MissKeyword).filter(models.MissKeyword.keyword == norm).first()
            if row:
                row.miss_count += 1
                row.last_seen = now
            else:
                session.add(models.MissKeyword(
                    keyword=norm, miss_count=1, first_seen=now, last_seen=now, resolved_at=None,
                ))
            session.commit()
        except Exception:
            pass
        finally:
            session.close()

    def _resolve_misses(self, row, session=None):
        """把已学会的未命中关键词标记为已解决(审核通过时调用),不再出现在待学习清单。

        判定条件(任一满足即 resolve,与教学合并同哲学):
        - miss.keyword == row.raw_keyword(精确)
        - set(cut_keyword(miss.keyword)) ⊆ set(split_keyword(row.keyword))(分词子集)

        session 可传入 review_pending 的会话(避免跨会话写锁冲突),不传则自开一个;
        DB 异常 try/except 兜底,不影响审核主流程。
        """
        own = session is None
        if own:
            session = SessionLocal()
        try:
            approved_raw = (row.raw_keyword or "").strip()
            approved_tokens = set(split_keyword(row.keyword)) if row.keyword else set()
            if not approved_raw and not approved_tokens:
                return
            now = datetime.now()
            misses = session.query(models.MissKeyword).filter(
                models.MissKeyword.resolved_at.is_(None)
            ).all()
            for miss in misses:
                if approved_raw and miss.keyword == approved_raw:
                    miss.resolved_at = now
                elif approved_tokens:
                    miss_tokens = set(cut_keyword(miss.keyword))
                    if miss_tokens and miss_tokens <= approved_tokens:
                        miss.resolved_at = now
            if own:
                session.commit()
        except Exception:
            pass
        finally:
            if own:
                session.close()

    def misses(self):
        """待学习清单:返回被问过 >=2 次且尚未学会(resolved_at IS NULL)的未命中关键词。

        按 miss_count 降序、last_seen 倒序,最多返回 MISS_LIST_MAX 条。
        """
        session = SessionLocal()
        try:
            rows = (
                session.query(models.MissKeyword)
                .filter(
                    models.MissKeyword.resolved_at.is_(None),
                    models.MissKeyword.miss_count >= 2,
                )
                .order_by(
                    models.MissKeyword.miss_count.desc(),
                    models.MissKeyword.last_seen.desc(),
                )
                .limit(MISS_LIST_MAX)
                .all()
            )
            return {
                "code": 200,
                "data": {
                    "list": [
                        {
                            "keyword": r.keyword,
                            "count": r.miss_count,
                            "last_seen": r.last_seen.strftime("%Y-%m-%d %H:%M") if r.last_seen else "",
                        }
                        for r in rows
                    ]
                },
            }
        except Exception:
            # DB 异常时返回空清单,不影响主流程
            return {"code": 200, "data": {"list": []}}
        finally:
            session.close()

    # ---- 忘记 ----
    def forget(self, answer, requester_uid=""):
        """按 answer 精确删除(可删除多条同 answer 的记录),成功返回 success。

        requester_uid(FEATURE_MAID 开启时):非调教师只能删自己教的行(uid == requester_uid),
        历史 NULL 数据仅调教师可删;关闭时行为与旧版完全一致(删除全部匹配行)。
        """
        ans = (answer or "").strip()
        session = SessionLocal()
        try:
            rows = session.query(models.Memorise).filter(models.Memorise.answer == ans).all()
            # P5 多人协作权限:非调教师只能删自己的行(留痕 uid == requester_uid)
            if config.is_enabled("MAID"):
                if not self._is_master(requester_uid):
                    rows = [r for r in rows if r.uid and r.uid == requester_uid]
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

    # ---- greeting 开场白 ----
    def greeting_rand(self):
        """随机返回一条 greeting 分类记忆作为开场白(用户访问网站时由前端自动发送)。

        对应 2010-2011 原版语义:greeting 类教学的是"开场白"——bot 主动说的欢迎语,
        不是等用户问候才应答。无 greeting 词条时返回业务码 10001(前端静默跳过)。
        """
        entries = [e for e in self.index.all_entries() if e.category == "greeting"]
        if not entries:
            return {"code": 10001, "data": {"answer": NOT_FOUND_ANSWER}}
        chosen = random.choice(entries)
        return {
            "code": 200,
            "data": {
                "keyword": chosen.raw_keyword or "",
                "answer": chosen.answer,
            },
        }

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
                        row.flag or "all",
                    ))
                    # 待学习清单:该关键词已学会,把对应未命中记录标记为已解决(用同一会话避免写锁冲突)
                    self._resolve_misses(row, session)
                reviewed += 1
            session.commit()
            return {"reviewed": reviewed, "rejected": rejected, "errors": errors}
        finally:
            session.close()
