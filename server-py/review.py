# -*- coding: utf-8 -*-
"""
AI 内容审核模块:每小时定时调用 DeepSeek API 审核教学内容。

设计:
- 教学 Add 后标记 review_status=pending,不立即生效(先审后玩)
- 每小时批量取最多 REVIEW_BATCH_LIMIT 条 pending 内容,调 DeepSeek 判定
- 违规(涉政/涉黄/涉赌/诈骗/广告等)标记 rejected 并删行进黑名单
- 正常标记 approved 并加入内存索引;审核 API 失败保持 pending 下轮重试

HTTP 客户端为 aiohttp(全异步):review_text 是协程,调用方必须 await。
DeepSeek API key 从环境变量 DEEPSEEK_API_KEY 读取(不硬编码),未配置时审核任务自动禁用,
功能退化为"只标记 pending 不审核"(教学不受影响)。
"""
import json
import os

import aiohttp

_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
_MODEL = "deepseek-chat"

# 模块级共享会话(懒加载,首次调用时在当前事件循环内创建;shutdown 时 close_session 回收)
_session: "aiohttp.ClientSession | None" = None


def _get_session() -> aiohttp.ClientSession:
    """取共享 aiohttp 会话(懒加载;已关闭时重建)。"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _session


async def close_session():
    """关闭共享会话(Litestar on_shutdown 调用)。"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None

# 审核用提示词:要求模型只输出 JSON,判定是否违规
# 注意:JSON 示例里的花括号必须转义成 {{ }},否则 .format() 会当占位符
_REVIEW_PROMPT = (
    "你是一名中文内容安全审核员。判断下面这段聊天机器人教学内容是否违规。\n"
    "违规类型包括:政治敏感、色情低俗、暴力恐怖、赌博、诈骗、毒品、违法广告引流、人身攻击辱骂。\n"
    "正常闲聊、角色扮演、东方Project梗、日常对话均属合规。\n"
    "只输出 JSON,格式:{{\"violate\": true/false, \"reason\": \"简短原因或空\"}}\n"
    "不要输出任何其他文字。\n\n"
    "关键词:{keyword}\n回答:{answer}"
)

def _api_key() -> str:
    """读取 DeepSeek API key:环境变量优先,其次读 server-py/.env(双保险,不依赖 import 顺序)。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    # 兜底:从 server-py/.env 读(与 config.py 加载逻辑一致,避免单独 import review 时拿不到)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "DEEPSEEK_API_KEY":
                    return value.strip()
    except OSError:
        pass
    return ""


async def review_text(keyword: str, answer: str) -> dict:
    """调用 DeepSeek 审核单条内容,返回 {"violate": bool, "reason": str}(协程)。

    异常(网络/限流/格式错)时抛异常,由调用方决定保持 pending 重试。
    """
    key = _api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,审核不可用")

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "你只输出 JSON,不输出多余内容。"},
            {"role": "user", "content": _REVIEW_PROMPT.format(keyword=keyword, answer=answer)},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }
    async with _get_session().post(
        _DEEPSEEK_URL,
        json=payload,
        headers={"Authorization": "Bearer " + key},
    ) as resp:
        # content_type=None:容忍个别网关返回非 application/json 的 Content-Type
        data = await resp.json(content_type=None)
    content = data["choices"][0]["message"]["content"].strip()
    # 模型可能包在 ```json ... ``` 里,剥掉
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # 容错:模型偶尔输出单引号/尾逗号的类 JSON,用 ast.literal_eval 兜底
        import ast
        parsed = ast.literal_eval(content)
    return {
        "violate": bool(parsed.get("violate", False)),
        "reason": str(parsed.get("reason", "")),
    }
