# -*- coding: utf-8 -*-
"""
资讯工具注册表:正则命中即返回动态答案(时间/计算器离线;天气/汇率/词典可插拔默认关)。

命中优先级由 service.reply() 控制:精确匹配(用户显式教过) > 资讯工具 > 分词重合匹配。
所有 handler 返回字符串或 None;失败返回 None 落到普通回复(不影响主流程)。
在线工具复用 urllib + 5s 超时(不加 requests),校园网外网不稳时配置留空即禁用。
"""
import re
import urllib.parse
import urllib.request
from datetime import datetime

import config
from safe_eval import safe_eval

# 星期中文名(datetime.weekday():0=周一)
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# ---- 内置离线工具(零依赖,默认启用)----
_TIME_RE = re.compile(r"(现在|几点|什么时间|日期|几号|星期|年月日)")


def _handler_time(keyword):
    """时间/日期工具:返回当前时间文本(现在是 YYYY-MM-DD HH:MM 星期X)。"""
    now = datetime.now()
    return "现在是 %04d-%02d-%02d %02d:%02d %s" % (
        now.year, now.month, now.day, now.hour, now.minute, _WEEKDAYS[now.weekday()],
    )


# 计算器:只支持二元运算(方案范围),形如 1+2 / 3.5*2 / -4/2
_CALC_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)\s*$")


def _handler_calc(keyword):
    """计算器工具:用 safe_eval 白名单求值(禁裸 eval),除零/异常/超大数返回 None。"""
    m = _CALC_RE.match(keyword)
    if not m:
        return None
    val = safe_eval("%s %s %s" % (m.group(1), m.group(2), m.group(3)))
    if val is None or not isinstance(val, (int, float)):
        return None
    if isinstance(val, float) and val.is_integer():
        val = int(val)  # 整数结果去掉末尾 .0(如 1+1 显示 2 而不是 2.0)
    return str(val)


# ---- 在线工具(可插拔,配置了对应 API 才注册;默认关)----
def _fetch(url, timeout=5):
    """带超时的 GET 请求,失败/超时返回 None(在线工具不可用时静默降级)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    return text[:200] if text else None


def _make_online(pattern, api_key):
    """构造一个可插拔在线工具:命中 pattern 且 config 的 api_key 非空才注册。

    API 配置是 URL 模板,{keyword} 会被替换成 URL 编码后的输入关键词。
    """
    pat = re.compile(pattern)

    def handler(keyword):
        api = config.get(api_key)
        if not api:
            return None
        url = api.replace("{keyword}", urllib.parse.quote(keyword))
        return _fetch(url)

    return pat, handler


# ---- 工具注册表 ----
# TOOL = (name, compiled_regex, handler(keyword) -> Optional[str])
def _build_tools():
    tools = [
        ("time", _TIME_RE, _handler_time),
        ("calc", _CALC_RE, _handler_calc),
    ]
    # 在线工具:天气/汇率/词典,仅当对应 API 配置非空时注册(默认全空 = 全部禁用)
    online = (
        ("weather", r"(天气|气温|温度)", "WEATHER_API"),
        ("exchange", r"(汇率|美元|欧元|日元)", "EXCHANGE_API"),
        ("dict", r"(什么是|意思|释义)", "DICT_API"),
    )
    for name, pattern, api_key in online:
        if config.get(api_key):
            pat, handler = _make_online(pattern, api_key)
            tools.append((name, pat, handler))
    return tools


TOOLS = _build_tools()


def match_tool(kw):
    """遍历工具注册表,返回第一个命中的非空结果;全部未命中返回 None。"""
    if not kw:
        return None
    for name, pat, handler in TOOLS:
        if not pat.search(kw):
            continue
        try:
            ans = handler(kw)
        except Exception:
            ans = None
        if ans:
            return ans
    return None
