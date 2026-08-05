# -*- coding: utf-8 -*-
"""
web-marisa Python 后端接口验证脚本。

用法:先启动后端(在 server-py 目录下执行)
    .venv\\Scripts\\python -m uvicorn main:app --host 127.0.0.1 --port 3100
再运行:
    python test_api.py

依赖:仅标准库 urllib + 本机 MariaDB 客户端(用于校验数据库数据)。
覆盖:探活 / Status / Add 校验 / Reply 命中与未命中 / 子集合并 vs 非子集新增 /
      Forget / 限流(11 次 Add 第 11 次 429)/ 命中随机性 / 数据库数据保留。
"""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

# 本机 MariaDB 客户端路径(与 server/test_api.py 一致)
MYSQL = r"D:\tools\mariadb-10.6.27-winx64\bin\mysql.exe"
# 后端地址(验证阶段固定 3100 端口)
BASE = "http://127.0.0.1:3100"

# 未命中兜底话术(必须与后端一致)
MISS_ANSWER = "唔嗯...不懂你在说什么呢...教教我吧~"

# 测试专用 answer(带 __TEST__ 前缀,避免与库里原有记忆冲突)
ANSWER_BASIC = "__TEST__苹果香蕉真的很好吃"
ANSWER_MERGE_BASE = "__TEST__MERGE_BASE__"
ANSWER_MERGE_ADD = "__TEST__MERGE_ADD__"
ANSWER_NONSUB = "__TEST__NONSUB__"
ANSWER_RANDOM_A = "__TEST__RANDOM_A__"
ANSWER_RANDOM_B = "__TEST__RANDOM_B__"
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
    """执行 SQL(通过 mysql.exe 的 stdin 传入,避免 Windows 命令行编码问题)。"""
    proc = subprocess.run(
        [MYSQL, "-h", "127.0.0.1", "-P", "3306", "-u", "root",
         "--default-character-set=utf8mb4", "-B", "-N", "webmarisa"],
        input=query.encode("utf-8"), capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
    return proc.stdout.decode("utf-8", errors="replace").strip()


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


def all_test_answers():
    """本次测试会产生的全部 answer(含限流测试的)。"""
    return [ANSWER_BASIC, ANSWER_MERGE_BASE, ANSWER_MERGE_ADD, ANSWER_NONSUB,
            ANSWER_RANDOM_A, ANSWER_RANDOM_B] + [RATE_ANSWER + str(i) for i in range(12)]


def cleanup():
    """通过 Forget 接口清理本次测试产生的数据(同步维护后端内存索引)。"""
    for ans in all_test_answers():
        try:
            request("POST", "Forget", {"answer": ans})
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

    # ---- 4. 教学(Add):分词后入库,keyword 回显为分词结果 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "苹果香蕉", "answer": ANSWER_BASIC})
    d = body.get("data", {})
    check("Add 教学", s == 200 and body.get("code") == 200 and d.get("answer") == ANSWER_BASIC
          and d.get("keyword") == "苹果,香蕉" and d.get("ip") == MAIN_IP, str(body))
    s, body = request("POST", "Status")
    check("Status 教学后 +1", body.get("data") == baseline + 1, str(body))

    # ---- 5. 回复命中(精确匹配优先):输入与 raw_keyword 完全相等 ----
    s, body = request("POST", "Reply", {"keyword": "苹果香蕉"})
    check("Reply 精确匹配命中", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") == ANSWER_BASIC, str(body))
    # hit_count 应被累加(数据库中该行 >= 1)
    hits = sql("SELECT hit_count FROM memorise WHERE answer = '%s';" % ANSWER_BASIC)
    check("Reply 后 hit_count 累加", hits.isdigit() and int(hits) >= 1, "hit_count=" + hits)

    # ---- 6. 合并-子集:新分词集合完全包含于已有词条时合并 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "草莓西瓜葡萄", "answer": ANSWER_MERGE_BASE})
    d = body.get("data", {})
    check("Add 合并基准词条", s == 200 and body.get("code") == 200 and d.get("keyword") == "草莓,西瓜,葡萄", str(body))
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "草莓西瓜", "answer": ANSWER_MERGE_ADD})
    d = body.get("data", {})
    check("Add 子集合并", s == 200 and body.get("code") == 200 and d.get("keyword") == "草莓,西瓜,葡萄", str(body))
    # 合并后的新词条应能被精确匹配命中(返回新答案)
    s, body = request("POST", "Reply", {"keyword": "草莓西瓜"})
    check("Reply 命中合并词条", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") == ANSWER_MERGE_ADD, str(body))

    # ---- 7. 非子集:只是部分重合时,应新增而不是合并 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "香蕉牛奶", "answer": ANSWER_NONSUB})
    d = body.get("data", {})
    check("Add 非子集新增", s == 200 and body.get("code") == 200 and d.get("keyword") == "香蕉,牛奶", str(body))
    s, body = request("POST", "Status")
    check("Status 四条教学后 +4", body.get("data") == baseline + 4, str(body))

    # ---- 8. 命中随机性:教两条同前缀词条,Reply 多次应出现不同答案 ----
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "西瓜牛奶", "answer": ANSWER_RANDOM_A})
    check("Add 随机A", s == 200 and body.get("code") == 200, str(body))
    s, body = request("POST", "Add", {"ip": MAIN_IP, "keyword": "西瓜豆浆", "answer": ANSWER_RANDOM_B})
    check("Add 随机B", s == 200 and body.get("code") == 200, str(body))
    answers = set()
    random_ok = True
    for i in range(20):
        s, body = request("POST", "Reply", {"keyword": "西瓜"})
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
    s, body = request("POST", "Status")
    check("Status 限流10条后 +10", body.get("data") == baseline + 15, str(body))

    # ---- 11. 数据库现有数据保留 ----
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
