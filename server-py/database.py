# -*- coding: utf-8 -*-
"""
数据库模块:创建 SQLAlchemy 引擎与会话工厂,并提供启动时自动迁移。
迁移逻辑:检查 memorise 表是否缺少新增列,缺失则执行 ALTER TABLE(幂等,不修改已有数据)。
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import config
import models

# 全局引擎(惰性连接)与会话工厂
engine = create_engine(config.database_url(), echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# 新增列清单:列名 -> 建列语句(MariaDB/MySQL 语法)
_ADD_COLUMNS = {
    "raw_keyword": "ALTER TABLE memorise ADD COLUMN raw_keyword TEXT NULL",
    "hit_count": "ALTER TABLE memorise ADD COLUMN hit_count INT NOT NULL DEFAULT 0",
    "created_at": "ALTER TABLE memorise ADD COLUMN created_at DATETIME NULL",
    "updated_at": "ALTER TABLE memorise ADD COLUMN updated_at DATETIME NULL",
}


def _existing_columns(conn) -> set:
    """查询 memorise 表当前全部列名(统一转小写比较)。"""
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'memorise'"
        ),
        {"db": config.get("DB_NAME")},
    ).fetchall()
    return {str(r[0]).lower() for r in rows}


def run_migrations():
    """启动时自动执行迁移:为 memorise 表补齐新增列,可重复执行。"""
    with engine.connect() as conn:
        cols = _existing_columns(conn)
        for name, ddl in _ADD_COLUMNS.items():
            if name.lower() not in cols:
                conn.execute(text(ddl))
        conn.commit()
