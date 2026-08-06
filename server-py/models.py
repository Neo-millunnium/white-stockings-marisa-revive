# -*- coding: utf-8 -*-
"""
ORM 模型:Memorise 记忆表。
表名固定为单数 memorise(与旧版 gorm SingularTable 行为一致)。
"""
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

# SQLAlchemy 声明式基类
Base = declarative_base()


class Memorise(Base):
    """记忆实体,对应数据库表 memorise。"""

    __tablename__ = "memorise"

    memory_id = Column("memoryId", Integer, primary_key=True, autoincrement=True)  # 主键自增
    ip = Column("ip", String(15), nullable=True)        # 教学来源 IP
    keyword = Column("keyword", Text, nullable=True)    # 逗号连接的分词结果
    answer = Column("answer", Text, nullable=True)      # 回答内容
    # 以下为本次重写新增列(启动时由 migration 自动 ALTER TABLE 补齐,不修改已有数据)
    raw_keyword = Column("raw_keyword", Text, nullable=True)              # 用户教学时的原始关键词(用于精确匹配)
    hit_count = Column("hit_count", Integer, nullable=False, default=0, server_default="0")  # 被命中次数
    created_at = Column("created_at", DateTime, nullable=True)            # 创建时间
    updated_at = Column("updated_at", DateTime, nullable=True)            # 更新时间
    # AI 审核状态:pending=待审核 approved=已通过 rejected=已拒绝(仅 SQLite/MySQL 新列,旧数据 NULL 视为 approved)
    review_status = Column("review_status", String(16), nullable=True, default="pending")
    # 教学分类(源自 2010 年原始 QQ 调教 bot 的 teach 指令体系):
    # word/sentence/syntax/logic/greeting;NULL 或 auto 判定后落值,旧数据 NULL 视为未分类
    category = Column("category", String(16), nullable=True)
    # 对象判断 flag(P4,FEATURE_FLAG):all(默认)/ user:<uid> / favor:high|medium|low / time:dawn|day|dusk|night
    # 教学时给词条挂条件:只对特定人 / 好感阶段 / 时段生效;旧数据 NULL 视为 all
    flag = Column("flag", String(32), nullable=True, default="all")
    # 教学者 uid(P5,FEATURE_MAID):多人协作留痕,用于调教师权限判定(非调教师只能删自己教的行)
    uid = Column("uid", String(64), nullable=True)


class Blacklist(Base):
    """审核黑名单:被 AI 审核拒绝的回答(按 answer 原文精确匹配)。

    教学 Add 时先查黑名单,命中直接拒绝(防止同一句违规内容反复提交)。
    """
    __tablename__ = "blacklist"

    answer = Column("answer", String(500), primary_key=True)  # 被拒的回答原文
    created_at = Column("created_at", DateTime, nullable=True)  # 加入黑名单时间


class MissKeyword(Base):
    """待学习清单:被问过但没答上的未命中关键词(聚合计数,跨重启保留)。

    未命中时 reply() 里 _miss_upsert 做 count+1;教学审核通过后 _resolve_misses
    把已学会的关键词置 resolved_at,从清单隐藏(未 resolve 且 miss_count >= 2 才展示)。
    """
    __tablename__ = "miss_keyword"

    id = Column("id", Integer, primary_key=True, autoincrement=True)  # 主键自增
    keyword = Column("keyword", Text, nullable=False, unique=True)    # 归一化后的未命中关键词(去空白/标点)
    miss_count = Column("miss_count", Integer, nullable=False, default=1)  # 累计未命中次数
    first_seen = Column("first_seen", DateTime, nullable=True)        # 首次出现时间
    last_seen = Column("last_seen", DateTime, nullable=True)          # 最近一次出现时间
    resolved_at = Column("resolved_at", DateTime, nullable=True)      # 学会时间;NULL = 待学


class Favorability(Base):
    """好感度(P2,FEATURE_FAVOR):按匿名 uid 记录好感分数与统计,回复倾向随好感变化。

    uid 是客户端自报的匿名身份(localStorage UUID),仅当"区分用户"用,不作可信权限;
    ip 兜底记录最近来源。level 由 score 按 FAVOR_LEVELS 阈值映射回写。
    """
    __tablename__ = "favorability"

    uid = Column("uid", String(64), primary_key=True)   # cookie UUID 主标识
    ip = Column("ip", String(15), nullable=True)        # 最近来源 IP(辅助/兜底)
    score = Column("score", Integer, nullable=False, default=0)
    talk_count = Column("talk_count", Integer, nullable=False, default=0)
    teach_count = Column("teach_count", Integer, nullable=False, default=0)
    active_seconds = Column("active_seconds", Integer, nullable=False, default=0)
    level = Column("level", Integer, nullable=False, default=0)  # 派生,由 score 映射
    last_active_at = Column("last_active_at", DateTime, nullable=True)


class Teacher(Base):
    """调教师(P5,FEATURE_MAID):被授予调教权限的 uid(可删任意条目 / 屏蔽用户)。

    首个调教师由服务端 env 配置 MASTER_UID 指定;本表用于二次授权。
    """
    __tablename__ = "teacher"

    uid = Column("uid", String(64), primary_key=True)
    role = Column("role", String(16), nullable=False, default="master")
    created_at = Column("created_at", DateTime, nullable=True)


class BlockedUser(Base):
    """被屏蔽用户(P5,FEATURE_MAID):被调教师屏蔽后禁止教学。

    屏蔽判定只作为教学拦截(Add 时检查),不影响提问。
    """
    __tablename__ = "blocked_user"

    uid = Column("uid", String(64), primary_key=True)
    blocked_by = Column("blocked_by", String(64), nullable=True)
    created_at = Column("created_at", DateTime, nullable=True)
