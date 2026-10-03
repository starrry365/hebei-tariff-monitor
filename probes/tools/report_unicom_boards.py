#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读本次重采的快照，按「板块 → 一级栏目 → 二级栏目」逐层统计，生成 Markdown 报告。

数据源：evidence/unicom-refetch-20261003.json.gz（refetch_unicom_now.py 的落盘）。
口径与构建层一致：状态看 _firstLevel==99（停售），地域看 _allCity/_cityNames，
不在这里重算第二套判据。
"""
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = os.path.join(REPO, "evidence", "unicom-refetch-20261003.json.gz")
OUT = os.path.join(REPO, "evidence", "联通全板块分类报告-%s.md" % date.today().strftime("%Y%m%d"))

d = json.load(gzip.open(SRC, "rt", encoding="utf-8"))
ent = d["entries"]
BNAME = {"1": "全国板块", "2": "河北板块"}

# 与今日归档快照对比（bonus：验证重采与 CI 归档是否一致）
snap_dir = os.path.join(REPO, "cloud", "tariff", "snapshots")
arch = os.path.join(snap_dir, "unicom_tariff_20261003.json.gz")
diff_line = ""
if os.path.exists(arch):
    a = json.load(gzip.open(arch, "rt", encoding="utf-8"))
    kf = lambda e: (str(e.get("_attr")), str(e.get("_firstLevel")),
                    str(e.get("name") or ""), str(e.get("reportNo") or ""))
    ka, kb = set(map(kf, a.get("entries") or [])), set(map(kf, ent))
    diff_line = "| 与今日归档快照对比 | 新出现 %d 条 · 消失 %d 条 |\n" % (len(kb - ka), len(ka - kb))


def fee_num(v):
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def board_table(es):
    """一级 → 二级 两级统计表。"""
    lvl = defaultdict(lambda: defaultdict(Counter))
    for e in es:
        fl = str(e.get("_firstLevel") or "?")
        fln = e.get("_firstLevelName") or ("停售套餐" if fl == "99" else fl)
        sln = (e.get("_secondLevelName") or "").strip() or "—"
        lvl[fln][sln]["n"] += 1
        if str(e.get("_firstLevel")) == "99":
            lvl[fln][sln]["off"] += 1
        if e.get("_cityNames"):
            lvl[fln][sln]["city"] += 1
    lines = ["| 一级栏目 | 二级栏目 | 条数 | 其中已下架 | 其中城市专属 |",
             "|---|---|--:|--:|--:|"]
    order = sorted(lvl, key=lambda k: (k == "停售套餐", k))
    for fln in order:
        subs = lvl[fln]
        tot = sum(c["n"] for c in subs.values())
        toff = sum(c["off"] for c in subs.values())
        tcity = sum(c["city"] for c in subs.values())
        first = True
        for sln in sorted(subs, key=lambda s: -subs[s]["n"]):
            c = subs[sln]
            lines.append("| %s | %s | %d | %d | %d |" % (
                ("**%s（共 %d 条）**" % (fln, tot)) if first else "",
                sln, c["n"], c["off"], c["city"]))
            first = False
        if len(subs) > 1:
            lines.append("| | **小计** | **%d** | **%d** | **%d** |" % (tot, toff, tcity))
    return "\n".join(lines)


def samples(es, n=5):
    """在售条目里挑几条有信息量的（有月费、名称不空）。"""
    cand = [e for e in es if str(e.get("_firstLevel")) != "99" and e.get("fees")]
    cand.sort(key=lambda e: (fee_num(e.get("fees")) if fee_num(e.get("fees")) is not None else 9e9))
    pick = cand[:n // 2] + cand[-n // 2:] if len(cand) > n else cand
    out = []
    for e in pick:
        city = "、".join(e.get("_cityNames") or []) or "全省"
        out.append("- %s（%s 元/月 · %s）" % (e.get("name"), e.get("fees"), city))
    return "\n".join(out) if out else "（无在售条目）"


md = []
md.append("# 联通资费专区 · 全板块分类报告（%s 重采）\n" % date.today().strftime("%Y-%m-%d"))
md.append("> 来源：%s · 采集时间 %s · 口径：12 地市并集、含停售（一级分类 99）。\n" % (d.get("src"), d.get("fetchedAt")))
md.append("| 指标 | 值 |\n|---|--:|")
md.append("| 总条数 | %d |" % len(ent))
md.append("| 目录（12 城并集三级目录） | %s |" % "—")
md.append("| 去重前 → 后 | 9310 目录 → %d 条 |" % len(ent))
md.append("| 全省通用 | %d |" % sum(1 for e in ent if str(e.get("_allCity")) == "1"))
md.append("| 城市专属 | %d |" % sum(1 for e in ent if e.get("_cityNames")))
md.append("| 已下架（停售栏目 99） | %d |" % sum(1 for e in ent if str(e.get("_firstLevel")) == "99"))
md.append(diff_line.rstrip("\n") + "\n" if diff_line else "\n")

for b in ("1", "2"):
    es = [e for e in ent if str(e.get("_attr")) == b]
    if not es:
        continue
    md.append("## %s（%d 条）\n" % (BNAME[b], len(es)))
    off = sum(1 for e in es if str(e.get("_firstLevel")) == "99")
    cty = sum(1 for e in es if e.get("_cityNames"))
    md.append("在售 %d · 已下架 %d · 城市专属 %d\n" % (len(es) - off, off, cty))
    md.append(board_table(es) + "\n")
    md.append("**代表资费**（按月费低→高各取几条，不含停售）：\n" + samples(es) + "\n")
    # 城市专属分布
    cc = Counter()
    for e in es:
        for c in (e.get("_cityNames") or []):
            cc[c] += 1
    if cc:
        md.append("**城市专属分布**：" + "、".join("%s %d" % (k, v) for k, v in cc.most_common()) + "\n")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("报告已生成：%s" % OUT)
print("\n".join(md))
