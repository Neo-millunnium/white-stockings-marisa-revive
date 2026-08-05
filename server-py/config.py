# -*- coding: utf-8 -*-
"""
配置模块:负责读取 server-py/.env 中的配置项。
未配置时使用默认值(root 空密码 @127.0.0.1:3306/webmarisa,端口 3100)。
支持环境变量覆盖,便于部署时用系统变量调整。
"""
import os
from pathlib import Path

# 默认配置(与 Go 版 Config/config.ini 保持一致;验证阶段端口用 3100,3000 被旧 Go 版占用)
_DEFAULTS = {
    "HTTP_PORT": "3100",   # 后端监听端口
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "3306",
    "DB_USER": "root",
    "DB_PASSWORD": "",     # 本机 root 空密码
    "DB_NAME": "webmarisa",
}

# 加载 server-py/.env(若存在);用 setdefault 保证已有环境变量优先
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip())


def get(name: str) -> str:
    """读取配置项:环境变量 > .env > 默认值。"""
    return os.environ.get(name, _DEFAULTS[name])


def http_port() -> int:
    """HTTP 监听端口。"""
    return int(get("HTTP_PORT"))


def database_url() -> str:
    """拼出 SQLAlchemy 连接串(MySQL/MariaDB + pymysql 驱动)。"""
    return "mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8".format(
        user=get("DB_USER"),
        password=get("DB_PASSWORD"),
        host=get("DB_HOST"),
        port=get("DB_PORT"),
        name=get("DB_NAME"),
    )
