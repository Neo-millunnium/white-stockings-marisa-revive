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
