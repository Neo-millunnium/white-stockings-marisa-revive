# -*- coding: utf-8 -*-
"""
web-marisa 后端(Python 版)入口。
技术栈:FastAPI + SQLAlchemy + jieba 分词 + SQLite(可选 MySQL)。

启动命令(在 server-py 目录下):
    .venv\\Scripts\\python -m uvicorn main:app --host 127.0.0.1 --port 3000

API 契约(与 Go 版完全兼容):全部 POST + form-urlencoded,返回 JSON {code, data},
HTTP 状态恒为 200,业务码在 JSON 的 code 字段里。
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Form

import config
import database
import service

# 全局业务服务(启动时重建内存索引)
svc = service.MemoriseService()

# 审核任务配置
REVIEW_INTERVAL = 3600    # 每 3600 秒(1 小时)执行一次
REVIEW_BATCH_LIMIT = 10   # 每次最多审核条数

_log = logging.getLogger("marisa.review")


async def _review_loop():
    """后台审核任务:每小时运行一次,最多审核 REVIEW_BATCH_LIMIT 条 pending。"""
    while True:
        try:
            # 首次运行前也等待一个完整周期,避免启动即审核
            await asyncio.sleep(REVIEW_INTERVAL)
            # 到点执行
            try:
                result = svc.review_pending(limit=REVIEW_BATCH_LIMIT)
                _log.info("AI 审核完成: 审 %d 条,拒 %d 条,失败 %d 条",
                          result["reviewed"], result["rejected"], result["errors"])
            except Exception as e:
                _log.error("AI 审核异常: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            _log.error("审核任务循环异常: %s", e)
            await asyncio.sleep(3600)  # 异常后 1 小时再试


@asynccontextmanager
async def lifespan(app):
    """应用启动钩子:迁移 -> 重建索引 -> 启动后台审核任务。"""
    database.run_migrations()
    svc.reload()
    print("[marisa] 已启动,监听端口 %d,数据库 %s" % (config.http_port(), config.get("DB_NAME")))
    # 启动后台审核任务(deepseek key 未配置时自动跳过,见 review.py)
    task = asyncio.create_task(_review_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# FastAPI 应用
app = FastAPI(title="web-marisa Python 后端", lifespan=lifespan)


@app.get("/")
@app.post("/")
def index():
    """首页探活:GET 和 POST 都返回 hello Marisa~(与 Go 版一致)。"""
    return {"code": 200, "message": "hello Marisa~"}


@app.post("/Add")
def add(ip: str = Form(""), keyword: str = Form(""), answer: str = Form(""),
        category: str = Form("auto"), uid: str = Form(""), flag: str = Form("all")):
    """教学:接收 form 字段 ip/keyword/answer/category,返回 {code, data}。

    category:word/sentence/syntax/logic/greeting/auto(默认 auto,自动判定)。
    uid:教学者匿名身份(identity.ts 生成,P2 好感/P5 留痕,老客户端可不传);
    flag:对象判断条件(P4,如 user:<uid>/favor:high/time:night,仅 FEATURE_FLAG 开启时生效)。
    """
    return svc.add(ip, keyword, answer, category, uid, flag)


@app.post("/Reply")
def reply(ip: str = Form(""), keyword: str = Form(""), uid: str = Form("")):
    """回复:接收 form 字段 ip/keyword,命中返回回答,未命中返回兜底话术。

    ip 用于每 IP 限流(每 IP 每分钟最多 REPLY_RATE_LIMIT 次),由前端 getIp() 传入;
    uid 为匿名身份(话题上下文/好感/flag 判定用,老客户端可不传)。
    """
    return svc.reply(ip, keyword, uid)


@app.post("/Forget")
def forget(answer: str = Form(""), uid: str = Form("")):
    """忘记:按 answer 精确删除,返回 success。

    uid(FEATURE_MAID 开启时):非调教师只能删自己教的行,历史 NULL 数据仅调教师可删。
    """
    return svc.forget(answer, uid)


@app.post("/Status")
def status():
    """状态:返回 {code, data},data 为知识条数(int,契约不变)。"""
    return svc.status()


@app.post("/Categories")
def categories():
    """分类统计:返回 {code, data:{word, sentence, syntax, logic, greeting, unclassified}}。"""
    return svc.categories()


@app.post("/Greeting")
def greeting():
    """开场白:随机返回一条 greeting 分类记忆(用户访问网站时由前端自动发送)。"""
    return svc.greeting_rand()


@app.post("/Hint")
def hint():
    """提示线索:随机返回一条已审核通过的记忆(对应前端 hint 指令)。"""
    return svc.hint()


@app.post("/Misses")
def misses(ip: str = Form("")):
    """待学习清单:返回被问过 >=2 次且尚未学会的未命中关键词,按次数降序。

    对应前端 miss 指令(展示"别人问了但没答上"的词,引导教学)。
    ip 复用回复限流防刷(直接调 svc 的限流方法,不通过 reply)。
    """
    if not svc._check_reply_rate(ip or ""):
        return {"code": 429, "data": "问得太频繁了,歇一歇吧~"}
    return svc.misses()


def _feature_off():
    """功能开关关闭时的统一返回:路由保留、业务码 400,避免前端 404 处理差异。"""
    return {"code": 400, "data": "这个功能还没有开启哦~"}


@app.post("/Favor")
def favor(uid: str = Form(""), ip: str = Form("")):
    """好感度(P2,FEATURE_FAVOR):查询某 uid 的好感分数/等级/计数。

    关闭时返回 400「功能未开启」,前端据此隐藏好感展示与心跳。
    """
    if not config.is_enabled("FAVOR"):
        return _feature_off()
    return svc.favor(uid)


@app.post("/Active")
def active(uid: str = Form(""), ip: str = Form(""), seconds: str = Form("0")):
    """心跳(P2,FEATURE_FAVOR):上报在线秒数累计好感,每 uid 每分钟限 1 次(复用限流)。"""
    if not config.is_enabled("FAVOR"):
        return _feature_off()
    if not (uid or "").strip():
        return {"code": 400, "data": "参数不合法:缺少 uid"}
    try:
        secs = int(seconds or "0")
    except ValueError:
        return {"code": 400, "data": "参数不合法:seconds 需为整数"}
    if secs <= 0 or secs > 86400:
        return {"code": 400, "data": "参数不合法:seconds 需在 1-86400 之间"}
    return svc.active(uid, ip, secs)


@app.post("/Block")
def block(uid: str = Form(""), target_uid: str = Form(""), action: str = Form("block")):
    """屏蔽/解除屏蔽(P5,FEATURE_MAID):仅调教师(MASTER_UID / teacher 表)可用。"""
    if not config.is_enabled("MAID"):
        return _feature_off()
    return svc.block(uid, target_uid, action)


@app.post("/Admin/Delete")
def admin_delete(uid: str = Form(""), answer: str = Form("")):
    """调教师删除任意条目(P5,FEATURE_MAID):复用增强后的 forget(调教师不受留痕限制)。"""
    if not config.is_enabled("MAID"):
        return _feature_off()
    if not svc._is_master(uid):
        return {"code": 400, "data": "无权限"}
    return svc.forget(answer, requester_uid=uid)


@app.post("/Review")
def review(secret: str = Form("")):
    """手动触发一次 AI 审核(不等待每小时定时任务)。

    需要 secret 与 config 的 REVIEW_SECRET 一致;REVIEW_SECRET 为空时禁用。
    审核逻辑与定时任务相同:取最多 REVIEW_BATCH_LIMIT 条 pending 审核。
    """
    expected = config.get("REVIEW_SECRET")
    if not expected or secret != expected:
        return {"code": 403, "data": "无权限"}
    result = svc.review_pending(limit=REVIEW_BATCH_LIMIT)
    return {"code": 200, "data": result}


@app.post("/Reload")
def reload_index(secret: str = Form("")):
    """手动从数据库重建内存索引(运维/测试用:审核状态在库中变更后同步)。

    同样需要 secret 校验;REVIEW_SECRET 为空时禁用。
    """
    expected = config.get("REVIEW_SECRET")
    if not expected or secret != expected:
        return {"code": 403, "data": "无权限"}
    svc.reload()
    return {"code": 200, "data": "reloaded"}
