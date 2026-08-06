# -*- coding: utf-8 -*-
"""防分布式注入功能回归测试(内容指纹 / IP 信誉 / 全局教学限流)。

用法(需先在隔离实例上跑,勿连生产 3000):
    cd server-py
    DB_FILE=test_webmarisa.db nohup .venv/bin/python3 -m uvicorn main:app --port 3101 >/tmp/marisa_test.log 2>&1 &
    .venv/bin/python3 test_antiinject.py   # 连 127.0.0.1:3101,耗时约 70s(含等全局窗口)

通过标准:
- 内容指纹:同一回答被第 6 个不同 IP 提交时拒绝(允许 5 个,防误伤)
- IP 信誉:同 IP 连续教黑名单回答,5 次 400 后第 6 次 429
- 全局教学限流:窗口清空后 120 个不同 IP 提交,前 100 个 200,后 20 个 429
"""
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:3101"


def add_code(ip, keyword, answer):
    data = urllib.parse.urlencode({
        "ip": ip, "keyword": keyword, "answer": answer, "category": "auto",
    }).encode()
    req = urllib.request.Request(BASE + "/Add", data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["code"]


def main():
    # 1. 内容指纹:同一回答被 >=5 个不同 IP 提交 -> 第 6 个起拒绝
    shared = "批发的魔理沙周边今天打五折快来买呀"
    fp_codes = [add_code("2.2.2.%d" % i, "刷库关键词%d" % i, shared) for i in range(1, 7)]
    fp_ok = fp_codes[:5] == [200] * 5 and fp_codes[5] == 400

    # 2. IP 信誉:同 IP 连续教黑名单回答 -> 5 次 400 后第 6 次 429
    ip_codes = [add_code("9.9.9.9", "坏词%d" % i, "出售海洛因加微信") for i in range(1, 7)]
    ip_ok = ip_codes[:5] == [400] * 5 and ip_codes[5] == 429

    # 3. 全局教学限流:等窗口清空后,120 个不同 IP -> 前 100 个 200,后 20 个 429
    time.sleep(62)
    ok_n = denied_n = 0
    for i in range(1, 121):
        c = add_code("3.3.3.%d" % i, "全局限流关键词%d" % i, "全局限流测试回答内容编号%d" % i)
        if c == 200:
            ok_n += 1
        elif c == 429:
            denied_n += 1
    global_ok = (ok_n, denied_n) == (100, 20)

    print("指纹去重  : %s  %s" % (fp_codes, "PASS" if fp_ok else "FAIL"))
    print("IP 信誉   : %s  %s" % (ip_codes, "PASS" if ip_ok else "FAIL"))
    print("全局限流  : 200=%d 429=%d  %s" % (ok_n, denied_n, "PASS" if global_ok else "FAIL"))
    ok = fp_ok and ip_ok and global_ok
    print("==========")
    print("通过 3 项,失败 0 项" if ok else "存在失败项!")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
