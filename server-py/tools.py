# -*- coding: utf-8 -*-
"""
资讯工具注册表:正则命中即返回动态答案(时间/计算器离线;天气/汇率/词典可插拔默认关)。

命中优先级由 service.reply() 控制:精确匹配(用户显式教过) > 资讯工具 > 分词重合匹配。
所有 handler 返回字符串或 None;失败返回 None 落到普通回复(不影响主流程)。
在线工具复用 urllib + 5s 超时(不加 requests),校园网外网不稳时配置留空即禁用。
"""
import json
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


def _handler_time(keyword, ip=None):
    """时间/日期工具:返回当前时间文本(现在是 YYYY-MM-DD HH:MM 星期X)。"""
    now = datetime.now()
    return "现在是 %04d-%02d-%02d %02d:%02d %s" % (
        now.year, now.month, now.day, now.hour, now.minute, _WEEKDAYS[now.weekday()],
    )


# 计算器:只支持二元运算(方案范围),形如 1+2 / 3.5*2 / -4/2
_CALC_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)\s*$")


def _handler_calc(keyword, ip=None):
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
def _fetch(url, timeout=5, max_len=200):
    """带超时的 GET 请求,失败/超时返回 None(在线工具不可用时静默降级)。

    max_len 控制返回文本截断长度:文本类工具(汇率/词典)用默认 200 足够;
    需要解析 JSON 的工具(天气/百科)必须传足量长度,否则截断会破坏 JSON。
    带浏览器 UA:部分站点(如萌娘百科)会拒绝 Python-urllib 默认 UA。
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (marisa-bot; web-marisa)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    return text[:max_len] if text else None


def _make_online(pattern, api_key):
    """构造一个可插拔在线工具:命中 pattern 且 config 的 api_key 非空才注册。

    API 配置是 URL 模板,{keyword} 会被替换成 URL 编码后的输入关键词。
    """
    pat = re.compile(pattern)

    def handler(keyword, ip=None):
        api = config.get(api_key)
        if not api:
            return None
        url = api.replace("{keyword}", urllib.parse.quote(keyword))
        return _fetch(url)

    return pat, handler


# ---- 天气工具(高德,可插拔:AMAP_KEY 配置后启用)----
# IP 定位:拿调用方 IP 的城市 adcode;天气:按 adcode 查实时天气
_AMAP_IP_API = "https://restapi.amap.com/v3/ip?key=%s&ip=%s"
_AMAP_WEATHER_API = "https://restapi.amap.com/v3/weather/weatherInfo?key=%s&city=%s&extensions=base"
_WEATHER_RE = re.compile(r"(天气|气温|温度|下雨|下雪|阴天|晴天)")


def _handler_weather(keyword, ip=None):
    """天气工具(高德):用请求方 IP 定位城市,返回当地实时天气文本。

    链路:IP 定位(v3/ip,免费)-> 城市 adcode -> 实时天气(v3/weather)。
    任一步失败/无 key 返回 None,落到普通回复,不影响主流程。
    """
    key = config.get("AMAP_KEY")
    if not key:
        return None
    # 1. IP 定位(ip 为空时高德按出口 IP 定位,至少能返回省份)
    geo = _fetch(_AMAP_IP_API % (key, urllib.parse.quote(ip or "")), max_len=2000)
    if not geo:
        return None
    try:
        geo_data = json.loads(geo)
    except Exception:
        return None
    if str(geo_data.get("status")) != "1":
        return None
    adcode = (geo_data.get("adcode") or "").strip()
    city_name = (geo_data.get("city") or geo_data.get("province") or "").strip()
    if not adcode:
        return None
    # 2. 查实时天气
    wea = _fetch(_AMAP_WEATHER_API % (key, adcode), max_len=4000)
    if not wea:
        return None
    try:
        wea_data = json.loads(wea)
        lives = wea_data.get("lives") or []
        if not lives:
            return None
        lv = lives[0]
        return "%s: %s %s℃%s风" % (
            lv.get("city") or city_name,
            lv.get("weather", ""),
            lv.get("temperature", ""),
            lv.get("winddirection", ""),
        )
    except Exception:
        return None


# ---- 百科工具(萌娘百科,可插拔:DICT_API 配置后启用)----
# MediaWiki extracts 摘要 API:带 redirects/exintro,返回词条开头摘要纯文本
_DICT_RE = re.compile(r"(什么是|什么意思|释义|介绍一下|是谁|百科)")


def _handler_dict(keyword, ip=None):
    """百科工具(萌娘百科):取词条开头摘要(已审核的社区内容),返回前 120 字。

    DICT_API 是 URL 模板,{keyword} 替换为 URL 编码后的输入;
    词条不存在(pages 为 -1)或摘要为空返回 None,落到普通回复。
    """
    api = config.get("DICT_API")
    if not api:
        return None
    # 从问句中提取词条名:去掉"什么是/介绍一下"等前缀与"是什么/是谁"等后缀
    q = re.sub(r"^(什么是|什么是|介绍一下|解释一下|说说|介绍|查一下|百度)", "", keyword)
    q = re.sub(r"(是什么意思|什么意思|是啥意思|是什么|是啥|是谁|怎么用|怎么玩|百科)$", "", q)
    q = q.strip(" \t的?呢啊")
    if not q:
        return None
    url = api.replace("{keyword}", urllib.parse.quote(q))
    raw = _fetch(url, max_len=4000)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        pages = (data.get("query") or {}).get("pages") or {}
        if not pages:
            return None
        page = next(iter(pages.values()))
        title = page.get("title") or keyword
        extract = (page.get("extract") or "").strip()
        if not extract:
            return None  # 词条不存在(页 id 为 -1)或摘要为空(重定向/消歧义)
        extract = re.sub(r"\s+", " ", extract)
        return "「%s」: %s" % (title, extract[:120])
    except Exception:
        return None


# ---- 工具注册表 ----
# TOOL = (name, compiled_regex, handler(keyword, ip) -> Optional[str])
def _build_tools():
    tools = [
        ("time", _TIME_RE, _handler_time),
        ("calc", _CALC_RE, _handler_calc),
    ]
    # 在线工具:天气/汇率/词典,仅当对应 API 配置非空时注册(默认全空 = 全部禁用)
    online = (
        ("weather", _WEATHER_RE, _handler_weather, "AMAP_KEY"),
        ("exchange", r"(汇率|美元|欧元|日元)", None, "EXCHANGE_API"),
        ("dict", _DICT_RE, _handler_dict, "DICT_API"),
    )
    for name, pattern, handler, api_key in online:
        if not config.get(api_key):
            continue
        if handler is None:
            pat, handler = _make_online(pattern, api_key)
        else:
            pat = re.compile(pattern)
        tools.append((name, pat, handler))
    return tools


TOOLS = _build_tools()


def match_tool(kw, ip=None):
    """遍历工具注册表,返回第一个命中的非空结果;全部未命中返回 None。

    ip 为请求方 IP,透传给需要定位的工具(如天气)。
    """
    if not kw:
        return None
    for name, pat, handler in TOOLS:
        if not pat.search(kw):
            continue
        try:
            ans = handler(kw, ip)
        except Exception:
            ans = None
        if ans:
            return ans
    return None
