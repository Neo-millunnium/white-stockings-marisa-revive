# -*- coding: utf-8 -*-
"""
配置模块:负责读取 server-py/.env 中的配置项。
未配置时使用默认值(root 空密码 @127.0.0.1:3306/webmarisa,端口 3100)。
支持环境变量覆盖,便于部署时用系统变量调整。
"""
import os
from pathlib import Path

# 默认配置:SQLite(零依赖、零内存,适合单机/低配部署);如部署 MySQL/MariaDB 可改 DB_TYPE=mysql
_DEFAULTS = {
    "HTTP_PORT": "3000",        # 后端监听端口
    "DB_TYPE": "sqlite",        # sqlite 或 mysql
    "DB_FILE": "webmarisa.db",  # SQLite 文件(相对 server-py/ 目录)
    "DB_HOST": "127.0.0.1",     # mysql 时使用
    "DB_PORT": "3306",
    "DB_USER": "root",
    "DB_PASSWORD": "",
    "DB_NAME": "webmarisa",
    "REVIEW_SECRET": "",      # POST /Review 手动触发审核的密钥(为空=禁用该接口)
    # 资讯工具在线 API 模板(含 {keyword} 占位符,被替换成 URL 编码后的输入);
    # 默认空字符串 = 禁用对应在线工具(时间/计算器是离线工具,无需配置恒启用)
    "EXCHANGE_API": "",       # 汇率工具
    "DICT_API": "",           # 百科/词典工具(萌娘百科 extracts 模板,见 tools.py)
    "AMAP_KEY": "",           # 高德 Web 服务 key(配置后启用 IP 定位 + 实时天气工具)
    # ---- 功能开关(每个功能一个开关,on/off;默认 off = 功能未启用,行为与未实现时完全一致)----
    "FEATURE_FAVOR": "off",    # P2 好感度系统
    "FEATURE_THEME": "off",    # P3 话题感知
    "FEATURE_FLAG": "off",     # P4 对象判断 flag
    "FEATURE_MAID": "off",     # P5 多人协作 + 调教师权限
    "FEATURE_CODE": "off",     # P6 代码执行模板
    # P5 调教师权限:首个调教师 uid(env 配置;权限判定只信这个配置,不信任客户端自报身份)
    "MASTER_UID": "",
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

# 审核用的 DeepSeek key 也可放在 server-py/.env 的 DEEPSEEK_API_KEY(不入库)


def get(name: str) -> str:
    """读取配置项:环境变量 > .env > 默认值。"""
    return os.environ.get(name, _DEFAULTS[name])


def is_enabled(name: str) -> bool:
    """功能开关:FEATURE_<NAME> 配置为 on/1/true 时启用(大小写不敏感),默认关闭。"""
    return get("FEATURE_" + name.upper()).strip().lower() in ("on", "1", "true")


def http_port() -> int:
    """HTTP 监听端口。"""
    return int(get("HTTP_PORT"))


def database_url() -> str:
    """生成 SQLAlchemy 连接串。

    - DB_TYPE=sqlite(默认):sqlite:///<server-py 目录>/webmarisa.db,零依赖零内存
    - DB_TYPE=mysql:mysql+pymysql://user:pass@host:port/dbname
    """
    if get("DB_TYPE") == "mysql":
        return "mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8".format(
            user=get("DB_USER"),
            password=get("DB_PASSWORD"),
            host=get("DB_HOST"),
            port=get("DB_PORT"),
            name=get("DB_NAME"),
        )
    db_file = get("DB_FILE")
    if not os.path.isabs(db_file):
        db_file = str(Path(__file__).resolve().parent / db_file)
    return "sqlite:///" + db_file.replace("\\", "/")
