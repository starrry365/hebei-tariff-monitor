# -*- coding: utf-8 -*-
"""用「用户当前打开的 URL」的 channelId 重跑一遍移动资费专区全量，逐栏目核对「全不全」。
直接复用 tar自己_monitor 的 fetch_all（含声明对账守卫 + 两容器 + fistLimit=5000），
只把 HEADERS 的 Referer 换成用户 URL。输出每个 (全网/河北 × 栏目) 的系列/条目 + 缺口。
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tariff_monitor as tm

USER_URL = ("https://h.app.coc.10086.cn/cmcc-app/pc-pages/tariffZonePers.html"
            "?pageId=834148205904408576&prov=311&channelId=P00000010686")
tm.REF = USER_URL
tm.HEADERS = {**tm.HEADERS, "Referer": USER_URL}
ZFLX = tm.ZFLX
ATTR_CN = {"1": "全网", "2": "河北", "3": "attr=3"}

print("Referer(channelId) =", USER_URL.split("&channelId=")[-1], "\n")
res = tm.fetch_all(workers=4)
if not res:
    print("抓取失败"); sys.exit(1)

print("\n===== 逐栏目核对 =====")
total = 0
for g in res["groups"]:
    a, t1 = g["tariffAttr"], g["type1"]
    t2 = g["type2"]
    ent = len(g["entries"])
    total += ent
    flag = ""
    if g["series"] and not g["entries"]:
        flag = "  ◀◀ 有系列零条"
    if g.get("short"):
        flag += "  ⚠️缺%d" % g["short"]
    print("attr=%s(%s) %s(%-6s) 系列=%-4d 条目=%-5d%s"
          % (a, ATTR_CN.get(a), t2, ZFLX.get(t2), len(g["series"]), ent, flag))
print("\n总计=%d 条；声明对账缺口=%d" % (total, res.get("short", 0)))