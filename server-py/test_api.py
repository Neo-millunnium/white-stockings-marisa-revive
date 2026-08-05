# -*- coding: utf-8 -*-
"""
web-marisa Python 后端接口验证脚本。

用法:先启动后端(在 server-py 目录下执行,需配置 REVIEW_SECRET)
    .venv\\Scripts\\python -m uvicorn main:app --host 127.0.0.1 --port 3000
再运行:
    python test_api.py

依赖:仅标准库 urllib + SQLAlchemy(直接读 SQLite 文件校验数据)。
覆盖:探活 / Status / Add 校验 / 审核流程(教学→pending→通过后生效)/
      Reply 命中与未命中 / 子集合并 vs 非子集新增 / Forget / 限流 /
      命中随机性 / 黑名单拒绝 / 数据库数据保留。

注意(新契约):
- 教学内容先进入待审队列(pending),不立即生效;审核通过(标记 approved)
  并 /Reload 后才可被 Reply 命中、计入 Status。
- 测试用 SQL 直连把测试行标记 approved + 调 /Reload 模拟"审核通过",
  避免依赖外部 AI 审核 API。
"""
import json
import os
import time
import urllib.parse
import urllib.request

from sqlalchemy import create_engine, text

import config

# 后端地址(正式端口 3000)
BASE = "http://127.0.0.1:3000"

# 数据库直连(读 SQLite 文件校验,与后端同一文件)
DB_ENGINE = create_engine(config.database_url())

# 未命中兜底话术(必须与后端一致)
MISS_ANSWER = "唔嗯...不懂你在说什么呢...教教我吧~"

# 手动审核/reload 的密钥(必须与 server-py/.env 的 REVIEW_SECRET 一致)
REVIEW_SECRET = config.get("REVIEW_SECRET") or "test_secret"

# 测试专用 answer(带 __TEST__ 前缀,避免与库里原有记忆冲突)
ANSWER_BASIC = "__TEST__苹果香蕉真的很好吃"
ANSWER_MERGE_BASE = "__TEST__MERGE_BASE__"
ANSWER_MERGE_ADD = "__TEST__MERGE_ADD__"
ANSWER_NONSUB = "__TEST__NONSUB__"
ANSWER_RANDOM_A = "__TEST__RANDOM_A__"
ANSWER_RANDOM_B = "__TEST__RANDOM_B__"
ANSWER_BLACK = "__TEST__BLACK_ANSWER__"
RATE_KEYWORD = "限流测试专用"
RATE_ANSWER = "__TEST__RATE__"

# 每次运行生成唯一的一组 IP,避免上一次运行残留的限流状态(窗口 60 秒)影响本次,
# 保证脚本可以连续多次运行而不相互干扰。
# 三个偏移量 0/80/160 彼此差均不在 [0,60) 内,确保 60 秒窗口内不会与上次运行撞 IP。
_run_seed = int(time.time()) ^ (os.getpid() & 0xffff)
MAIN_IP = "127.0.0.%d" % (2 + _run_seed % 250)              # 主测试 IP(教学用)
VALIDATE_IP = "127.0.0.%d" % (2 + (_run_seed + 80) % 250)   # 校验拒绝测试专用 IP
RATE_IP = "127.0.0.%d" % (2 + (_run_seed + 160) % 250)      # 限流测试专用 IP

# 原有数据中的回答(重写后应原样保留)
ORIGIN_ANSWERS = [
    "今天也是个好天气呢~",
    "我是白丝魔理沙,最喜欢偷书了!",
    "重构版后端工作正常!",
]

passed = 0
failed = 0


def sql(query):
    """执行 SQL(通过 SQLAlchemy 直连 SQLite)。

    - SELECT:返回首行首列的字符串或空串
    - UPDATE/INSERT/DELETE:执行后返回 ""(不取行)
    """
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text(query))
        if result.returns_rows:
            row = result.fetchone()
            return str(row[0]) if row else ""
        conn.commit()
        return ""


def request(method, path, data=None):
    """发起 form-urlencoded 请求,返回 (HTTP状态码, JSON)。"""
    body = urllib.parse.urlencode(data).encode() if data else b""
    url = BASE + "/" + path.lstrip("/")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def check(name, cond, detail=""):
    """统计并打印一条断言结果。"""
    global passed, failed
    if cond:
        passed += 1
        print("[PASS] %s %s" % (name, detail))
    else:
        failed += 1
        print("[FAIL] %s %s" % (name, detail))


def approve_and_reload(answer):
    """模拟审核通过:把该 answer 的行标记 approved,然后调 /Reload 重建索引。"""
    sql("UPDATE memorise SET review_status = 'approved' WHERE answer = '%s';" % answer)
    request("POST", "Reload", {"secret": REVIEW_SECRET})


def all_test_answers():
    """本次测试会产生的全部 answer(含限流测试的)。"""
    return [ANSWER_BASIC, ANSWER_MERGE_BASE, ANSWER_MERGE_ADD, ANSWER_NONSUB,
            ANSWER_RANDOM_A, ANSWER_RANDOM_B, ANSWER_BLACK] + [RATE_ANSWER + str(i) for i in range(12)]


def cleanup():
    """通过 Forget 接口清理本次测试产生的数据(同步维护后端内存索引)。"""
    for ans in all_test_answers():
        try:
            request("POST", "Forget", {"answer": ans})
        except Exception:
            pass
    # 清掉黑名单测试残留
    try:
        sql("DELETE FROM blacklist WHERE answer = '%s';" % ANSWER_BLACK)
        request("POST", "Reload", {"secret": REVIEW_SECRET})
    except Exception:
        pass


try:
    # 清理上次运行残留(保证幂等)
    cleanup()

    # ---- 1. 首页探活:GET 与 POST 都应返回 hello Marisa ----
    s, body = request("GET", "/")
    check("GET / 探活", s == 200 and body.get("code") == 200 and body.get("message") == "hello Marisa~", str(body))
    s, body = request("POST", "/")
    check("POST / 探活", s == 200 and body.get("code") == 200 and body.get("message") == "hello Marisa~", str(body))

    # ---- 2. 状态基线(动态,应 >= 原库条数 4) ----
    s, body = request("POST", "Status")
    baseline = body.get("data")
    check("Status 基线", s == 200 and body.get("code") == 200 and isinstance(baseline, int) and baseline >= 4, str(body))

    # ---- 3. 输入校验(新增):空关键词 / 空回答 / 超长关键词 / 超长回答 ----
    s, body = request("POST", "Add", {"ip": VALIDATE_IP, "keyword": "", "answer": "x"})
    check("Add 校验-空关键词", s == 200 and body.get("code") == 400 and "关键词" in str(body.get("data")), str(body))
    s, body = request("POST", "Add", {"ip": VALIDATE_IP, "keyword": "x", "answer": ""})
    check("Add 校验-空回答", s == 200 and body.get("code") == 400 and "回答" in str(body.get("data")), str(body))
    s, body = request("POST", "Add", {"ip": VALIDATE_IP, "keyword": "长" * 51, "answer": "x"})
    check("Add 校验-超长关键词", s == 200 and body.get("code") == 400 and "关键词" in str(body.get("data")), str(body))
    s, body = request("POST", "Add", {"ip": VALIDATE_IP, "keyword": "x", "answer": "长" * 501})
    check("Add 校验-超长回答", s == 200 and body.get("code") == 400 and "回答" in str(body.get("data")), str(body))

    # 校验拒绝不应影响记忆条数
    s, body = request("POST", "Status")
    check("Status 校验拒绝后不变", body.get("data") == baseline, str(body))

    # ---- 4. 教学(Add):入库为 pending,不立即生效(新契约) ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "苹果香蕉", "answer": ANSWER_BASIC})
    d = body.get("data", {})
    check("Add 教学", s == 200 and body.get("code") == 200 and d.get("answer") == ANSWER_BASIC
          and d.get("keyword") == "苹果,香蕉" and d.get("ip") == MAIN_IP, str(body))
    s, body = request("POST", "Status")
    check("Status 教学后不变(pending 不入索引)", body.get("data") == baseline, str(body))
    # pending 内容不可回复
    s, body = request("POST", "Reply", {"ip": MAIN_IP, "keyword": "苹果香蕉"})
    check("Reply pending 未命中", body.get("code") == 10001, str(body))
    # 数据库里确实是 pending
    st = sql("SELECT review_status FROM memorise WHERE answer = '%s';" % ANSWER_BASIC)
    check("数据库标记 pending", st == "pending", "status=" + st)

    # ---- 5. 审核通过后生效:精确匹配命中 ----
    approve_and_reload(ANSWER_BASIC)
    s, body = request("POST", "Status")
    check("Status 审核通过后 +1", body.get("data") == baseline + 1, str(body))
    s, body = request("POST", "Reply", {"ip": MAIN_IP, "keyword": "苹果香蕉"})
    check("Reply 精确匹配命中", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") == ANSWER_BASIC, str(body))
    # hit_count 应被累加(数据库中该行 >= 1)
    hits = sql("SELECT hit_count FROM memorise WHERE answer = '%s';" % ANSWER_BASIC)
    check("Reply 后 hit_count 累加", hits.isdigit() and int(hits) >= 1, "hit_count=" + hits)

    # ---- 6. 合并-子集:新分词集合完全包含于已有词条时合并(基于已过审内容) ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "草莓西瓜葡萄", "answer": ANSWER_MERGE_BASE})
    d = body.get("data", {})
    check("Add 合并基准词条", s == 200 and body.get("code") == 200 and d.get("keyword") == "草莓,西瓜,葡萄", str(body))
    approve_and_reload(ANSWER_MERGE_BASE)
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "草莓西瓜", "answer": ANSWER_MERGE_ADD})
    d = body.get("data", {})
    check("Add 子集合并", s == 200 and body.get("code") == 200 and d.get("keyword") == "草莓,西瓜,葡萄", str(body))
    approve_and_reload(ANSWER_MERGE_ADD)
    # 合并后的新词条应能被精确匹配命中(返回新答案)
    s, body = request("POST", "Reply", {"ip": MAIN_IP, "keyword": "草莓西瓜"})
    check("Reply 命中合并词条", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") == ANSWER_MERGE_ADD, str(body))

    # ---- 7. 非子集:只是部分重合时,应新增而不是合并 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "香蕉牛奶", "answer": ANSWER_NONSUB})
    d = body.get("data", {})
    check("Add 非子集新增", s == 200 and body.get("code") == 200 and d.get("keyword") == "香蕉,牛奶", str(body))
    approve_and_reload(ANSWER_NONSUB)
    s, body = request("POST", "Status")
    check("Status 四条教学审核后 +4", body.get("data") == baseline + 4, str(body))

    # ---- 8. 命中随机性:教两条同前缀词条,Reply 多次应出现不同答案 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "西瓜牛奶", "answer": ANSWER_RANDOM_A})
    check("Add 随机A", s == 200 and body.get("code") == 200, str(body))
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "西瓜豆浆", "answer": ANSWER_RANDOM_B})
    check("Add 随机B", s == 200 and body.get("code") == 200, str(body))
    approve_and_reload(ANSWER_RANDOM_A)
    approve_and_reload(ANSWER_RANDOM_B)
    answers = set()
    random_ok = True
    for i in range(20):
        s, body = request("POST", "Reply", {"ip": MAIN_IP, "keyword": "西瓜"})
        ans = body.get("data", {}).get("answer")
        if not (s == 200 and body.get("code") == 200 and ans in (ANSWER_RANDOM_A, ANSWER_RANDOM_B)):
            random_ok = False
        answers.add(ans)
    check("Reply 随机(20 次均正常)", random_ok, str(answers))
    check("Reply 随机性出现不同答案", len(answers) >= 2, "答案集合=" + str(answers))

    # ---- 9. 忘记(Forget):按 answer 精确删除 ----
    s, body = request("POST", "Forget", {"answer": ANSWER_MERGE_ADD})
    check("Forget 忘记", s == 200 and body.get("code") == 200 and body.get("data") == "success", str(body))
    s, body = request("POST", "Status")
    check("Status 忘记后 -1", body.get("data") == baseline + 5, str(body))

    # ---- 10. 限流:同一 IP 连续 11 次 Add,第 11 次返回业务码 429 ----
    rate_ok = True
    for i in range(11):
        s, body = request("POST", "Add", {"ip": RATE_IP, "keyword": RATE_KEYWORD, "answer": RATE_ANSWER + str(i)})
        if i < 10:
            if not (s == 200 and body.get("code") == 200):
                rate_ok = False
        else:
            if not (s == 200 and body.get("code") == 429 and "频繁" in str(body.get("data"))):
                rate_ok = False
    check("限流(前10次成功,第11次429)", rate_ok, str(body))

    # ---- 10.5 回复限流:同一 IP 连续 31 次 Reply,第 31 次应 429 ----
    reply_rate_ok = True
    for i in range(31):
        s, body = request("POST", "Reply", {"ip": RATE_IP, "keyword": "限流测试专用"})
        if i < 30:
            # 前 30 次:放行(可能命中或未命中,但不应 429)
            if body.get("code") == 429:
                reply_rate_ok = False
        else:
            # 第 31 次:应 429
            if not (s == 200 and body.get("code") == 429 and "频繁" in str(body.get("data"))):
                reply_rate_ok = False
    check("Reply 限流(前30次成功,第31次429)", reply_rate_ok, str(body))

    # ---- 11. 黑名单:被拒回答再次教学直接拒绝 ----
    # 模拟:该回答曾在审核中被拒,进了黑名单库;Reload 后再次 Add 应被 400 拒绝
    sql("INSERT OR IGNORE INTO blacklist (answer, created_at) VALUES ('%s', datetime('now'));" % ANSWER_BLACK)
    request("POST", "Reload", {"secret": REVIEW_SECRET})
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "黑名单关键词", "answer": ANSWER_BLACK})
    check("黑名单拒绝同回答", s == 200 and body.get("code") == 400 and "拒绝" in str(body.get("data")), str(body))
    sql("DELETE FROM blacklist WHERE answer = '%s';" % ANSWER_BLACK)
    request("POST", "Reload", {"secret": REVIEW_SECRET})
    # 黑名单删除后,同回答可再次教学(pending)
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "黑名单关键词", "answer": ANSWER_BLACK})
    check("黑名单移除后可再教学", s == 200 and body.get("code") == 200, str(body))
    # 清理这条,避免污染(此时是 pending,直接 SQL 删 + Reload)
    sql("DELETE FROM memorise WHERE answer = '%s';" % ANSWER_BLACK)
    request("POST", "Reload", {"secret": REVIEW_SECRET})

    # ---- 12. 数据库现有数据保留 ----
    cnt = int(sql("SELECT COUNT(*) FROM memorise;") or 0)
    check("数据库总条数保留", cnt >= baseline, "db条数=%d baseline=%d" % (cnt, baseline))
    for oa in ORIGIN_ANSWERS:
        exists = sql("SELECT COUNT(*) FROM memorise WHERE answer = '%s';" % oa)
        check("原有数据保留: %s..." % oa[:12], exists == "1", "exists=" + exists)

finally:
    # 无论成败都清理测试数据,把库恢复原状
    try:
        cleanup()
        s, body = request("POST", "Status")
        print("清理后 Status:", body)
    except Exception as e:
        print("清理异常(可能是服务未启动):", e)
    try:
        cnt = int(sql("SELECT COUNT(*) FROM memorise;") or 0)
        print("清理后数据库总条数:", cnt)
    except Exception as e:
        print("数据库查询异常:", e)

print("=" * 40)
print("通过 %d 项,失败 %d 项" % (passed, failed))
raise SystemExit(1 if failed else 0)
