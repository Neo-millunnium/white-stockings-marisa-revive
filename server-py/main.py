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
def add(ip: str = Form(""), keyword: str = Form(""), answer: str = Form(""), category: str = Form("auto")):
    """教学:接收 form 字段 ip/keyword/answer/category,返回 {code, data}。

    category:word/sentence/syntax/logic/greeting/auto(默认 auto,自动判定)。
    """
    return svc.add(ip, keyword, answer, category)


@app.post("/Reply")
def reply(ip: str = Form(""), keyword: str = Form("")):
    """回复:接收 form 字段 ip/keyword,命中返回回答,未命中返回兜底话术。

    ip 用于每 IP 限流(每 IP 每分钟最多 REPLY_RATE_LIMIT 次),由前端 getIp() 传入。
    """
    return svc.reply(ip, keyword)


@app.post("/Forget")
def forget(answer: str = Form("")):
    """忘记:按 answer 精确删除,返回 success。"""
    return svc.forget(answer)


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
