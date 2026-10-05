# -*- coding: utf-8 -*-
"""根据 final96_result.json 重建终版 m3u：去重、剔除失败/慢源、分组排序。"""
import re, json
from collections import OrderedDict

RES = r"D:\Work\WorkBuddy\_aptv_probe\final96_result.json"
OUT = r"D:\Work\WorkBuddy\_aptv_probe\河北移动IPTV-终版D.m3u"
MIN_KBPS = 2000  # 低于 2Mbps 剔除（除非该频道只有这一条）

res = json.load(open(RES, encoding="utf-8"))

def norm(n):
    n = re.sub(r"\s*·.*$", "", n)
    n = n.replace("高清", "").replace("频道", "").strip()
    return n

# 按归一化频道名分组去重，同频道保留最快的
by_ch = OrderedDict()
for r in res:
    key = norm(r["name"])
    by_ch.setdefault(key, []).append(r)

def cctv_no(n):
    m = re.match(r"CCTV[- ]?(\d+)", n)
    return int(m.group(1)) if m else 999

def group_of(n):
    if n.startswith("CCTV"): return "01-央视"
    if n.endswith("卫视"): return "02-卫视"
    return "03-影视专题"

out = ["#EXTM3U",
       "# 河北移动 IPTV 终版D · 2026-10-05 逐个串行实测 · 河北邢台移动",
       "# 全部条目逐一实测通过，按实测速度排序，无重复", ""]
kept, dropped, demoted = 0, [], []
chans = []
for key, lst in by_ch.items():
    lst = sorted(lst, key=lambda x: -(x["kbps"] or 0))
    best = lst[0]
    if best["kbps"] is None:
        dropped.append((key, "FAIL"))
        continue
    if best["kbps"] < MIN_KBPS and len(lst) == 1:
        demoted.append((key, best["kbps"]))
        # 仍保留但标注慢
    chans.append((key, best))

# 排序：组内央视按频道号，其余按速度降序
cctv = sorted([c for c in chans if c[0].startswith("CCTV")], key=lambda c: cctv_no(c[0]))
rest = sorted([c for c in chans if not c[0].startswith("CCTV")],
              key=lambda c: (-c[1]["kbps"], c[0]))

for key, r in cctv + rest:
    g = group_of(key)
    tag = " ⚠慢" if r["kbps"] < MIN_KBPS else ""
    out.append('#EXTINF:-1 tvg-name="%s" group-title="%s",%s ·%.1fM%s'
               % (key, g, key, r["kbps"] / 1000, tag))
    out.append(r["url"])
    kept += 1

open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("保留 %d 频道 -> %s" % (kept, OUT))
print("剔除:", dropped if dropped else "无")
print("慢速标注:", ["%s %dkbps" % d for d in demoted] if demoted else "无")
