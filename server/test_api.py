# -*- coding: utf-8 -*-
"""
web-marisa 后端接口验证脚本(针对 gin 重构版)
用法:先启动 server.exe(在 server 目录下),再运行 python test_api.py
依赖:仅标准库 urllib + 本机 MariaDB 客户端(用于测试数据的清理与构造)
"""
import json
import subprocess
import urllib.parse
import urllib.request

MYSQL = r"D:\tools\mariadb-10.6.27-winx64\bin\mysql.exe"
BASE = "http://127.0.0.1:3000"

# 测试用的独立 answer 值,避免与库中原有记忆冲突
ANSWER_BASIC = "苹果香蕉真的很好吃"
ANSWER_MERGE_BASE = "__TEST_BASE__"
ANSWER_MERGE_ADD = "__TEST_ADD__"
MISS_ANSWER = "唔嗯...不懂你在说什么呢...教教我吧~"

failed = 0
passed = 0


def sql(query):
    # 用 stdin 传 SQL(避免 Windows 命令行参数把中文弄乱),并显式指定 utf8mb4
    proc = subprocess.run(
        [MYSQL, "-h", "127.0.0.1", "-P", "3306", "-u", "root",
         "--default-character-set=utf8mb4", "webmarisa"],
        input=query.encode("utf-8"), capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))


def request(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else b""
    # 注意:避免 BASE 末尾斜杠 + path 组合成 "//",否则 gin 会做路径修正重定向
    url = BASE + "/" + path.lstrip("/")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("[PASS] %s %s" % (name, detail))
    else:
        failed += 1
        print("[FAIL] %s %s" % (name, detail))


def cleanup():
    # 清掉本次测试产生的所有数据,把库恢复成原始状态
    for ans in (ANSWER_BASIC, ANSWER_MERGE_BASE, ANSWER_MERGE_ADD):
        sql("DELETE FROM memorise WHERE answer = '%s';" % ans)


try:
    cleanup()  # 清理上次运行残留
    # 构造合并测试的基准词条:草莓,西瓜,葡萄(三个分词)
    sql("INSERT INTO memorise (ip, keyword, answer) VALUES ('127.0.0.1', '草莓,西瓜,葡萄', '%s');"
        % ANSWER_MERGE_BASE)

    # ---- 1. 首页探活:GET 与 POST 都应返回 hello Marisa ----
    s, body = request("GET", "/")
    check("GET /", s == 200 and body.get("code") == 200 and body.get("message") == "hello Marisa~", str(body))
    s, body = request("POST", "/")
    check("POST /", s == 200 and body.get("code") == 200 and body.get("message") == "hello Marisa~", str(body))

    # ---- 2. 状态:记忆条数(原始 2 条 + 基准词条 1 条) ----
    s, body = request("POST", "Status")
    baseline = body.get("data")
    check("Status 基线", s == 200 and body.get("code") == 200 and baseline == 3, str(body))

    # ---- 3. 回复未命中:返回业务码 10001 与兜底回答 ----
    s, body = request("POST", "Reply", {"keyword": "阿巴阿巴不存在的词"})
    check("Reply 未命中",
          s == 200 and body.get("code") == 10001 and body.get("data", {}).get("answer") == MISS_ANSWER, str(body))

    # ---- 4. 教学(Add):分词后入库,keyword 回显为分词结果 ----
    s, body = request("POST", "Add", {"ip": "127.0.0.1", "keyword": "苹果香蕉", "answer": ANSWER_BASIC})
    d = body.get("data", {})
    check("Add 教学", s == 200 and body.get("code") == 200 and d.get("answer") == ANSWER_BASIC
          and d.get("keyword") == "苹果,香蕉" and d.get("ip") == "127.0.0.1", str(body))

    # ---- 5. 回复命中:应直接返回刚教学的回答 ----
    s, body = request("POST", "Reply", {"keyword": "苹果香蕉"})
    check("Reply 命中(直接返回答案)", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") == ANSWER_BASIC, str(body))

    # ---- 6. 教学合并(修复 bug#1):与"草莓,西瓜,葡萄"重合 2/3>=0.6,应去重合并,
    #          把库中原有的"葡萄"合并进新词条 ----
    s, body = request("POST", "Add", {"ip": "127.0.0.1", "keyword": "草莓西瓜", "answer": ANSWER_MERGE_ADD})
    d = body.get("data", {})
    merged = d.get("keyword")
    check("Add 合并(bug#1)", s == 200 and body.get("code") == 200 and merged == "草莓,西瓜,葡萄", str(body))

    # ---- 7. 合并后的词条可被命中 ----
    s, body = request("POST", "Reply", {"keyword": "草莓西瓜"})
    check("Reply 命中合并词条", s == 200 and body.get("code") == 200
          and body.get("data", {}).get("answer") in (ANSWER_MERGE_BASE, ANSWER_MERGE_ADD), str(body))

    # ---- 8. 忘记(Forget):按 answer 删除 ----
    s, body = request("POST", "Forget", {"answer": ANSWER_MERGE_ADD})
    check("Forget 忘记", s == 200 and body.get("code") == 200 and body.get("data") == "success", str(body))

    # ---- 9. 状态:合并测试条目被忘记后,剩余 = 基线(3) + 教学词条(1) = 4 ----
    s, body = request("POST", "Status")
    check("Status 回归", s == 200 and body.get("code") == 200 and body.get("data") == baseline + 1, str(body))

finally:
    cleanup()
    s, body = request("POST", "Status")
    print("最终 Status:", body)

print("=" * 40)
print("通过 %d 项,失败 %d 项" % (passed, failed))
raise SystemExit(1 if failed else 0)
