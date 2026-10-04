#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据三轮实测结果重建 m3u：去重、择优、分组、排序"""
import json, re, os

BASE = r"D:\Work\WorkBuddy\_aptv_probe"
res = json.load(open(os.path.join(BASE, "multi_round_result.json"), encoding="utf-8"))

def norm(name):
    n = re.split(r"\s*·", name)[0].strip()
    n = re.sub(r"[（(].*?[)）]", "", n).replace("★", "").strip()
    n = n.replace("高清4M", "").replace("高清", "")  # CCTV-16高清4M/黑龙江卫视高清 归并
    return n

def cat(name):
    if re.match(r"^CCTV", name, re.I):
        return "央视"
    if name.endswith("卫视"):
        return "卫视"
    return "专题"

def cctv_order(name):
    m = re.match(r"^CCTV\s*5\+?", name, re.I)
    n = name.upper().replace("CCTV", "").strip()
    if "5+" in n: return 5.5
    try: return float(re.sub(r"[^0-9]", "", n) or 99)
    except: return 99

seen = {}
for r in res:
    host = re.match(r"https?://([^/]+)", r["url"]).group(1)
    r["host"] = host
    r["ch"] = norm(r["name"])
    r["cat"] = cat(r["ch"])

dead = [r for r in res if r["ok_n"] == 0]
slow = [r for r in res if 0 < r["med"] < 1000]

# 每频道择优：主选=med 最高且 ok_n==3；备用=med>=2000 且异 host 且 ok_n==3
final = []
stats = {"dead": len(dead), "slow": 0}
for ch in sorted(set(r["ch"] for r in res)):
    cands = [r for r in res if r["ch"] == ch and r["ok_n"] == 3 and r["med"] >= 1000]
    if not cands:
        # 放宽：ok_n==2 且 med>=1500
        cands = [r for r in res if r["ch"] == ch and r["ok_n"] >= 2 and r["med"] >= 1500]
    if not cands:
        stats["slow"] += 1
        continue
    cands.sort(key=lambda x: -x["med"])
    main = cands[0]
    picks = [main]
    for c in cands[1:]:
        if c["host"] != main["host"] and c["med"] >= 2000:
            picks.append(c)
        if len(picks) >= 2:
            break
    for j, c in enumerate(picks):
        mb = c["med"] / 1000.0
        label = "%s ·%.1fMbps%s" % (ch, mb, "" if j == 0 else " 备")
        if c["ok_n"] < 3:
            label += "(测2轮)"
        final.append({"ch": ch, "cat": c["cat"], "label": label,
                      "url": c["url"], "med": c["med"], "rank": j})

out = ["#EXTM3U",
       "# 河北移动 IPTV · 三轮实测精选版 v3 | 2026-10-04",
       "# 每条URL经3轮真实拉流复测；主选=中位速度最高，标[备]=异主机热备",
       "# 已剔除：死链 %d 条、三轮中位<1Mbps 慢速 %d 条" % (stats["dead"], stats["slow"]), ""]

def fmt_group(g):
    return g

for g, title in [("央视", "央视"), ("卫视", "卫视"), ("专题", "影视·专题")]:
    items = [f for f in final if f["cat"] == g]
    if g == "央视":
        items.sort(key=lambda x: (x["rank"], cctv_order(x["ch"])))
        # 主选在前：先按频道号排主选，再备
        mains = sorted([f for f in items if f["rank"] == 0], key=lambda x: cctv_order(x["ch"]))
        backs = sorted([f for f in items if f["rank"] > 0], key=lambda x: cctv_order(x["ch"]))
        items = mains + backs
    else:
        mains = sorted([f for f in items if f["rank"] == 0], key=lambda x: -x["med"])
        backs = sorted([f for f in items if f["rank"] > 0], key=lambda x: -x["med"])
        items = mains + backs
    out.append("# --- %s（%d 台）---" % (title, len(set(f['ch'] for f in items))))
    for f in items:
        out.append('#EXTINF:-1 tvg-name="%s" group-title="%s",%s' % (f["ch"], title, f["label"]))
        out.append(f["url"])
    out.append("")

n_url = sum(1 for l in out if l.startswith("http"))
n_ch = len(set(f["ch"] for f in final))
out.append("# 合计: %d 频道 / %d 条URL（含热备）" % (n_ch, n_url))
open(os.path.join(BASE, "河北移动IPTV-三轮实测精选版.m3u"), "w", encoding="utf-8").write("\n".join(out) + "\n")

print("频道 %d / URL %d -> 河北移动IPTV-三轮实测精选版.m3u" % (n_ch, n_url))
print("剔除: 死链%d 慢速频道%d" % (stats["dead"], stats["slow"]))
print("\n频道清单:")
for ch in sorted(set(f["ch"] for f in final),
                 key=lambda c: (0 if c.startswith("CCTV") else 1 if c.endswith("卫视") else 2, c)):
    ms = [f for f in final if f["ch"] == ch and f["rank"] == 0][0]
    extra = " (+备)" if len([f for f in final if f["ch"] == ch]) > 1 else ""
    print("  %-14s %.1fMbps%s" % (ch, ms["med"] / 1000.0, extra))
