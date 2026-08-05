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
REVIEW_HOUR = 4          # 每天 4:00 执行
REVIEW_DAILY_LIMIT = 100  # 每次最多审核条数

_log = logging.getLogger("marisa.review")


async def _review_loop():
    """后台审核任务:每天 REVIEW_HOUR:00 运行,最多审核 REVIEW_DAILY_LIMIT 条。"""
    while True:
        try:
            now = datetime.now()
            # 计算到下次 4:00 的秒数
            next_run = now.replace(hour=REVIEW_HOUR, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            wait = (next_run - now).total_seconds()
            _log.info("下次 AI 审核: %s (%.0f 分钟后)", next_run.strftime("%m-%d %H:%M"), wait / 60)
            await asyncio.sleep(wait)
            # 到点执行
            try:
                result = svc.review_pending(limit=REVIEW_DAILY_LIMIT)
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
def add(ip: str = Form(""), keyword: str = Form(""), answer: str = Form("")):
    """教学:接收 form 字段 ip/keyword/answer,返回 {code, data}。"""
    return svc.add(ip, keyword, answer)


@app.post("/Reply")
def reply(keyword: str = Form("")):
    """回复:接收 form 字段 keyword,命中返回回答,未命中返回兜底话术。"""
    return svc.reply(keyword)


@app.post("/Forget")
def forget(answer: str = Form("")):
    """忘记:按 answer 精确删除,返回 success。"""
    return svc.forget(answer)


@app.post("/Status")
def status():
    """状态:返回当前记忆总条数。"""
    return svc.status()


@app.post("/Hint")
def hint():
    """提示线索:随机返回一条已审核通过的记忆(对应前端 hint 指令)。"""
    return svc.hint()
