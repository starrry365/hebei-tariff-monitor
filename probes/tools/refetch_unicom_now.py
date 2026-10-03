#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地重新采集联通全板块（12 地市并集、含停售），落盘 JSON 备查 + 打印分板块概览。

为什么不直接重定向 unicom_monitor.__main__：
  那个入口只打印组级概览，不落盘；报告阶段还要二次分析，这里一次性把
  fetch_all() 的完整结构存进 evidence/，后面的统计脚本直接读文件，不用重采。

用法：
  python probes/tools/refetch_unicom_now.py
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import unicom_monitor as M  # noqa: E402

OUT = os.path.join(REPO, "evidence", "unicom-refetch-%s.json.gz" % __import__("datetime").date.today().strftime("%Y%m%d"))

o = M.fetch_all(workers=6)
if not o:
    print("!! 采集失败（fetch_all 返回 None）")
    sys.exit(1)

ent = o["entries"]
with gzip.open(OUT, "wt", encoding="utf-8") as f:
    json.dump(o, f, ensure_ascii=False)
print("已落盘：%s（%.0f KB）" % (OUT, os.path.getsize(OUT) / 1024))
print("联通 %s：%d 条 · %d 组 · fetchedAt=%s" % (o.get("provinceName"), len(ent), len(o["groups"]), o.get("fetchedAt")))

# —— 分板块概览（attr → 一级栏目）——
BNAME = {"1": "全国", "2": "河北"}
boards = {}
for e in ent:
    a = str(e.get("_attr") or "?")
    boards.setdefault(a, {}).setdefault(str(e.get("_firstLevel") or "?"), 0)
    boards[a][str(e.get("_firstLevel") or "?")] += 1
for a in sorted(boards):
    print("== 板块 %s（%s）%d 条 ==" % (a, BNAME.get(a, a), sum(boards[a].values())))
    for fl in sorted(boards[a], key=lambda x: (x == "99", x)):
        nm = U_name = ""
        try:
            nm = M.U.type_name(fl) if fl != "99" else "停售套餐"
        except Exception:
            pass
        print("   一级 %-3s %-14s %5d 条" % (fl, nm, boards[a][fl]))
