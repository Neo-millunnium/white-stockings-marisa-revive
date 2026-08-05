# -*- coding: utf-8 -*-
"""
web-marisa 后端(Python 版)入口。
技术栈:FastAPI + SQLAlchemy + pymysql + jieba 分词。

启动命令(在 server-py 目录下):
    .venv\\Scripts\\python -m uvicorn main:app --host 127.0.0.1 --port 3100

API 契约(与 Go 版完全兼容):全部 POST + form-urlencoded,返回 JSON {code, data},
HTTP 状态恒为 200,业务码在 JSON 的 code 字段里。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form

import config
import database
import service

# 全局业务服务(启动时重建内存索引)
svc = service.MemoriseService()


@asynccontextmanager
async def lifespan(app):
    """应用启动钩子:先做数据库迁移(补齐新增列),再重建内存倒排索引。"""
    database.run_migrations()
    svc.reload()
    print("[marisa] 已启动,监听端口 %d,数据库 %s" % (config.http_port(), config.get("DB_NAME")))
    yield


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
