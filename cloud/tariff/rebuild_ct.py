#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机重建页面（四网全量）：移动=当日快照 · 联通/广电=本地缓存 · 电信=浏览器采集缓存。

与 rebuild_offline.py 的差别：**不依赖 NET_RUN 注册** —— 电信的采集必须借真实
浏览器（瑞数 WAF），不能进 main() 的每日自动轮询（云端没有浏览器，跑了必失败）。
所以电信暂不注册 NET_RUN / NET_LIVE，由本脚本单独把 fetch_all() 的产物塞进
build_html()。等哪天把「真实浏览器采集」搬上云端（或有别的合规通路）再注册。

用法：
    python rebuild_ct.py        # 重建 docs/index.html（含电信）
"""
import io
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tariff_monitor as T      # noqa: E402
import ct_monitor as CT         # noqa: E402


def count_of(code, o):
    if not o:
        return 0
    if o.get("entries") is not None:
        return len(o["entries"])
    return sum(len(g.get("entries") or []) for g in o.get("groups") or [])


mv, p = T.load_prev("99999999")
if not mv:
    sys.exit("!! 找不到移动快照，先跑一次 tariff_monitor.py")

srcs = {"move": mv}
for code in ("unicom", "cbn"):
    path = os.path.join(BASE, "." + code + "_cache.json")
    d = json.load(io.open(path, encoding="utf-8"))
    print(code, "缓存:", len(d.get("entries") or []), "条")
    srcs[code] = d

t0 = __import__("time").time()
srcs["telecom"] = CT.fetch_all()
print("电信: %d 条 · %.0fs" % (len(srcs["telecom"]["entries"]), __import__("time").time() - t0))

nets = " · ".join("%s %d 条" % (c, count_of(c, srcs.get(c))) for c in ("move", "unicom", "telecom", "cbn"))
n = T.build_html(srcs, "本机重建：移动=当日快照 · 联通/广电=本地缓存 · 电信=真实浏览器采集（河北 609906）", None)
print("已完成，合计 %d 条（%s）" % (n, nets))
