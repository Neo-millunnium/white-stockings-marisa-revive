# -*- coding: utf-8 -*-
"""
数据库模块:创建 SQLAlchemy 引擎与会话工厂,并提供启动时自动建表/迁移。

- SQLite(默认,DB_TYPE=sqlite):启动时 CREATE TABLE IF NOT EXISTS(含全部列),无需迁移
- MySQL/MariaDB(DB_TYPE=mysql):检查 memorise 表缺少的新增列,执行 ALTER TABLE(幂等)
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import config
import models

# 引擎参数:SQLite 需要 check_same_thread=False(FastAPI 多线程访问)
_engine_kwargs = {"echo": False}
if config.get("DB_TYPE") == "mysql":
    _engine_kwargs["pool_pre_ping"] = True
else:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

# 全局引擎(惰性连接)与会话工厂
engine = create_engine(config.database_url(), **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# 新增列清单(仅 MySQL 需要:列名 -> ALTER 语句)
_ADD_COLUMNS = {
    "raw_keyword": "ALTER TABLE memorise ADD COLUMN raw_keyword TEXT NULL",
    "hit_count": "ALTER TABLE memorise ADD COLUMN hit_count INT NOT NULL DEFAULT 0",
    "created_at": "ALTER TABLE memorise ADD COLUMN created_at DATETIME NULL",
    "updated_at": "ALTER TABLE memorise ADD COLUMN updated_at DATETIME NULL",
    "review_status": "ALTER TABLE memorise ADD COLUMN review_status VARCHAR(16) NULL DEFAULT 'pending'",
    "category": "ALTER TABLE memorise ADD COLUMN category VARCHAR(16) NULL",
}


def _existing_columns_mysql(conn) -> set:
    """MySQL:从 information_schema 查询 memorise 表当前列名。"""
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'memorise'"
        ),
        {"db": config.get("DB_NAME")},
    ).fetchall()
    return {str(r[0]).lower() for r in rows}


def run_migrations():
    """启动时自动建表/迁移,可重复执行。

    - SQLite:create_all 全量建表(幂等,表已存在则跳过)
    - MySQL:补齐新增列
    """
    if config.get("DB_TYPE") != "mysql":
        # SQLite:新库直接建全量表;若文件里已有旧表(缺列),同样补列保证兼容
        with engine.connect() as conn:
            models.Base.metadata.create_all(engine)
            conn.commit()
        with engine.connect() as conn:
            cols = {r[1].lower() for r in conn.execute(text("PRAGMA table_info(memorise)")).fetchall()}
            for name, ddl in _ADD_COLUMNS.items():
                if name.lower() not in cols:
                    conn.execute(text(ddl.replace("ALTER TABLE memorise ADD COLUMN ", "ALTER TABLE memorise ADD COLUMN ")))
            conn.commit()
        return
    # MySQL:补齐新增列 + 建黑名单表
    with engine.connect() as conn:
        cols = _existing_columns_mysql(conn)
        for name, ddl in _ADD_COLUMNS.items():
            if name.lower() not in cols:
                conn.execute(text(ddl))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS blacklist ("
            "answer VARCHAR(500) NOT NULL PRIMARY KEY, "
            "created_at DATETIME NULL)"
        ))
        conn.commit()
