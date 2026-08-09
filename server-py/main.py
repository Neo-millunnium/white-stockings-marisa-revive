# -*- coding: utf-8 -*-
"""
web-marisa 后端(Python 版)入口:Litestar 路由 + 应用装配(create_app)。
技术栈:Litestar + SQLAlchemy 2(全异步,aiosqlite)+ jieba 分词 + SQLite(可选 MySQL)。

启动方式(在 server-py 目录下):
    python server.py            # 推荐:WebMarisaServer 统一入口(读 conf.yaml)
    .venv\\Scripts\\python -m uvicorn main:app --host 127.0.0.1 --port 3000  # ASGI 直连(测试/部署)

API 契约(与 Go 版完全兼容):全部 POST + form-urlencoded,返回 JSON {code, data},
HTTP 状态恒为 200,业务码在 JSON 的 code 字段里。
"""
import asyncio
import logging
from functools import partial
from typing import Annotated

from litestar import Litestar, get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body

import config
# uvicorn main:app 直连场景:先加载默认 conf.yaml 再初始化数据库/业务模块;
# 经 WebMarisaServer 启动时配置已由 server.py 加载过,此处 setdefault 不覆盖
config.load_yaml()
import database
import review
import service
import tools

# 全局业务服务(启动时重建内存索引)
svc = service.MemoriseService()

# 审核任务配置
REVIEW_INTERVAL = 3600    # 每 3600 秒(1 小时)执行一次
REVIEW_BATCH_LIMIT = 10   # 每次最多审核条数

_log = logging.getLogger("marisa.review")

# form-urlencoded 请求体整体解析为 dict(缺省 None,便于空 body 兜底为 {});
# 字段逐个 data.get("x", 默认值),语义与原 FastAPI Form(默认值) 完全一致
Form = Annotated[dict[str, str] | None, Body(media_type=RequestEncodingType.URL_ENCODED)]

# POST 路由统一约定:HTTP 状态恒 200(Litestar POST 默认 201,契约要求 200,
# 业务码在 JSON 的 code 字段);handler 全部为协程,直接跑在事件循环上
post200 = partial(post, status_code=200)


async def _review_loop():
    """后台审核任务:每小时运行一次,最多审核 REVIEW_BATCH_LIMIT 条 pending。"""
    while True:
        try:
            # 首次运行前也等待一个完整周期,避免启动即审核
            await asyncio.sleep(REVIEW_INTERVAL)
            # 到点执行(review_pending 为全异步协程:DB 走 aiosqlite,DeepSeek 走 aiohttp,
            # 不阻塞事件循环)
            try:
                result = await svc.review_pending(limit=REVIEW_BATCH_LIMIT)
                _log.info("AI 审核完成: 审 %d 条,拒 %d 条,失败 %d 条",
                          result["reviewed"], result["rejected"], result["errors"])
            except Exception as e:
                _log.error("AI 审核异常: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            _log.error("审核任务循环异常: %s", e)
            await asyncio.sleep(3600)  # 异常后 1 小时再试


_review_task: "asyncio.Task | None" = None


async def _on_startup():
    """应用启动钩子:迁移 -> 重建索引 -> 启动后台审核任务。"""
    global _review_task
    # 迁移走同步引擎(DDL 一次性操作),to_thread 挪出事件循环
    await asyncio.to_thread(database.run_migrations)
    await svc.reload()
    print("[marisa] 已启动,监听端口 %d,数据库 %s" % (config.http_port(), config.get("DB_NAME")))
    # 启动后台审核任务(deepseek key 未配置时自动跳过,见 review.py)
    _review_task = asyncio.create_task(_review_loop())


async def _on_shutdown():
    """应用关闭钩子:取消后台审核任务,回收 aiohttp 共享会话。"""
    if _review_task is not None:
        _review_task.cancel()
        try:
            await _review_task
        except asyncio.CancelledError:
            pass
    await tools.close_session()
    await review.close_session()


def _hello() -> dict:
    return {"code": 200, "message": "hello Marisa~"}


@get("/")
async def index() -> dict:
    """首页探活(GET):返回 hello Marisa~(与 Go 版一致)。"""
    return _hello()


@post200("/")
async def index_post() -> dict:
    """首页探活(POST):与 GET 返回相同(与 Go 版一致)。"""
    return _hello()


@post200("/Add")
async def add(data: Form = None) -> dict:
    """教学:接收 form 字段 ip/keyword/answer/category,返回 {code, data}。

    category:word/sentence/syntax/logic/greeting/auto(默认 auto,自动判定)。
    uid:教学者匿名身份(identity.ts 生成,P2 好感/P5 留痕,老客户端可不传);
    flag:对象判断条件(P4,如 user:<uid>/favor:high/time:night,仅 FEATURE_FLAG 开启时生效)。
    """
    data = data or {}
    return await svc.add(data.get("ip", ""), data.get("keyword", ""), data.get("answer", ""),
                         data.get("category", "auto"), data.get("uid", ""), data.get("flag", "all"))


@post200("/Reply")
async def reply(data: Form = None) -> dict:
    """回复:接收 form 字段 ip/keyword,命中返回回答,未命中返回兜底话术。

    ip 用于每 IP 限流(每 IP 每分钟最多 REPLY_RATE_LIMIT 次),由前端 getIp() 传入;
    uid 为匿名身份(话题上下文/好感/flag 判定用,老客户端可不传)。
    """
    data = data or {}
    return await svc.reply(data.get("ip", ""), data.get("keyword", ""), data.get("uid", ""))


@post200("/Forget")
async def forget(data: Form = None) -> dict:
    """忘记:按 answer 精确删除,返回 success。

    uid(FEATURE_MAID 开启时):非调教师只能删自己教的行,历史 NULL 数据仅调教师可删。
    """
    data = data or {}
    return await svc.forget(data.get("answer", ""), data.get("uid", ""))


@post200("/Status")
async def status() -> dict:
    """状态:返回 {code, data},data 为知识条数(int,契约不变)。"""
    return await svc.status()


@post200("/Categories")
async def categories() -> dict:
    """分类统计:返回 {code, data:{word, sentence, syntax, logic, greeting, unclassified}}。"""
    return await svc.categories()


@post200("/Greeting")
async def greeting() -> dict:
    """开场白:随机返回一条 greeting 分类记忆(用户访问网站时由前端自动发送)。"""
    return await svc.greeting_rand()


@post200("/Hint")
async def hint() -> dict:
    """提示线索:随机返回一条已审核通过的记忆(对应前端 hint 指令)。"""
    return await svc.hint()


@post200("/Misses")
async def misses(data: Form = None) -> dict:
    """待学习清单:返回被问过 >=2 次且尚未学会的未命中关键词,按次数降序。

    对应前端 miss 指令(展示"别人问了但没答上"的词,引导教学)。
    ip 复用回复限流防刷(直接调 svc 的限流方法,不通过 reply)。
    """
    data = data or {}
    ip = data.get("ip", "")
    if not svc._check_reply_rate(ip or ""):
        return {"code": 429, "data": "问得太频繁了,歇一歇吧~"}
    return await svc.misses()


def _feature_off():
    """功能开关关闭时的统一返回:路由保留、业务码 400,避免前端 404 处理差异。"""
    return {"code": 400, "data": "这个功能还没有开启哦~"}


@post200("/Favor")
async def favor(data: Form = None) -> dict:
    """好感度(P2,FEATURE_FAVOR):查询某 uid 的好感分数/等级/计数。

    关闭时返回 400「功能未开启」,前端据此隐藏好感展示与心跳。
    """
    if not config.is_enabled("FAVOR"):
        return _feature_off()
    data = data or {}
    return await svc.favor(data.get("uid", ""))


@post200("/Active")
async def active(data: Form = None) -> dict:
    """心跳(P2,FEATURE_FAVOR):上报在线秒数累计好感,每 uid 每分钟限 1 次(复用限流)。"""
    if not config.is_enabled("FAVOR"):
        return _feature_off()
    data = data or {}
    uid = data.get("uid", "")
    seconds = data.get("seconds", "0")
    if not (uid or "").strip():
        return {"code": 400, "data": "参数不合法:缺少 uid"}
    try:
        secs = int(seconds or "0")
    except ValueError:
        return {"code": 400, "data": "参数不合法:seconds 需为整数"}
    if secs <= 0 or secs > 86400:
        return {"code": 400, "data": "参数不合法:seconds 需在 1-86400 之间"}
    return await svc.active(uid, data.get("ip", ""), secs)


@post200("/Block")
async def block(data: Form = None) -> dict:
    """屏蔽/解除屏蔽(P5,FEATURE_MAID):仅调教师(MASTER_UID / teacher 表)可用。"""
    if not config.is_enabled("MAID"):
        return _feature_off()
    data = data or {}
    return await svc.block(data.get("uid", ""), data.get("target_uid", ""), data.get("action", "block"))


@post200("/Admin/Delete")
async def admin_delete(data: Form = None) -> dict:
    """调教师删除任意条目(P5,FEATURE_MAID):复用增强后的 forget(调教师不受留痕限制)。"""
    if not config.is_enabled("MAID"):
        return _feature_off()
    data = data or {}
    uid = data.get("uid", "")
    if not await svc._is_master(uid):
        return {"code": 400, "data": "无权限"}
    return await svc.forget(data.get("answer", ""), requester_uid=uid)


@post200("/Review")
async def review_pending_now(data: Form = None) -> dict:
    """手动触发一次 AI 审核(不等待每小时定时任务)。

    需要 secret 与 config 的 REVIEW_SECRET 一致;REVIEW_SECRET 为空时禁用。
    审核逻辑与定时任务相同:取最多 REVIEW_BATCH_LIMIT 条 pending 审核。
    """
    data = data or {}
    expected = config.get("REVIEW_SECRET")
    if not expected or data.get("secret", "") != expected:
        return {"code": 403, "data": "无权限"}
    result = await svc.review_pending(limit=REVIEW_BATCH_LIMIT)
    return {"code": 200, "data": result}


@post200("/Reload")
async def reload_index(data: Form = None) -> dict:
    """手动从数据库重建内存索引(运维/测试用:审核状态在库中变更后同步)。

    同样需要 secret 校验;REVIEW_SECRET 为空时禁用。
    """
    data = data or {}
    expected = config.get("REVIEW_SECRET")
    if not expected or data.get("secret", "") != expected:
        return {"code": 403, "data": "无权限"}
    await svc.reload()
    return {"code": 200, "data": "reloaded"}


def create_app() -> Litestar:
    """应用装配:路由 + 启动/关闭钩子。WebMarisaServer 与 uvicorn main:app 共用。"""
    return Litestar(
        route_handlers=[
            index, index_post, add, reply, forget, status, categories, greeting, hint, misses,
            favor, active, block, admin_delete, review_pending_now, reload_index,
        ],
        on_startup=[_on_startup],
        on_shutdown=[_on_shutdown],
    )


# ASGI 入口(uvicorn main:app 直连用;WebMarisaServer 则调 create_app() 装配)
app = create_app()


if __name__ == "__main__":
    # python main.py 等价于 python server.py(统一走 WebMarisaServer)
    from server import WebMarisaServer

    WebMarisaServer().run()
