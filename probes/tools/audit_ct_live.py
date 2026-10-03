# -*- coding: utf-8 -*-
"""电信快照 vs 实时收割（归一化后）逐条比对。

实时原始数据先过 ct_monitor.fetch_all() 归一化（与正式采集同一转换），
再与快照做同格式对账——避免「原始报文 vs 归一化字段」的假差异。

层级：
  L1 reportNo 集合（新增/消失）
  L2 共有条目全字段逐项比对
  L3 分类归属（type2Name）逐条比对
"""
import glob
import gzip
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAP_DIR = os.path.join(REPO, "cloud", "tariff", "snapshots")
LIVE = os.path.join(REPO, "evidence", "ct-live-normalized-20261003.json")

snap_files = sorted(glob.glob(os.path.join(SNAP_DIR, "ct_tariff_2026*.json.gz")))
snap = json.load(gzip.open(snap_files[-1], "rt", encoding="utf-8"))
live = json.load(open(LIVE, encoding="utf-8"))

snap_m = {str(e["reportNo"]): e for g in snap["groups"] for e in g.get("entries", [])}
live_m = {str(e["reportNo"]): e for g in live["groups"] for e in g.get("entries", [])}
print("快照 %s：%d 条 · 实时归一化：%d 条" % (snap.get("fetchedAt"), len(snap_m), len(live_m)))

s_keys, l_keys = set(snap_m), set(live_m)
new, gone = sorted(l_keys - s_keys), sorted(s_keys - l_keys)
print("L1 reportNo：新增 %d · 消失 %d" % (len(new), len(gone)))
for k in new[:5]:
    print("   + %s %s" % (k, live_m[k].get("name")))
for k in gone[:5]:
    print("   - %s %s" % (k, snap_m[k].get("name")))

all_fields = sorted(set().union(*[set(e) for e in snap_m.values()] + [set(e) for e in live_m.values()]))
diffs = {}
n_dup = 0
for k in sorted(s_keys & l_keys):
    a, b = snap_m[k], live_m[k]
    for f in all_fields:
        va = str(a.get(f) if a.get(f) is not None else "").strip()
        vb = str(b.get(f) if b.get(f) is not None else "").strip()
        if va != vb:
            diffs.setdefault(f, []).append((k, va[:60], vb[:60]))

print("L2 字段比对 %d 条 × %d 字段" % (len(s_keys & l_keys), len(all_fields)))
if not diffs:
    print("   ✅ 零差异")
else:
    for f, lst in sorted(diffs.items(), key=lambda kv: -len(kv[1])):
        print("   ⚠️ %s：%d 条不同（样例 %s: [%s] → [%s]）" % (f, len(lst), lst[0][0], lst[0][1], lst[0][2]))

cat_diff = [(k, snap_m[k].get("type2Name"), live_m[k].get("type2Name"))
            for k in sorted(s_keys & l_keys)
            if snap_m[k].get("type2Name") != live_m[k].get("type2Name")]
print("L3 分类归属漂移：%d 条" % len(cat_diff))
for k, a, b in cat_diff[:5]:
    print("   %s: %s → %s" % (k, a, b))

snap_cat = {g["type2Name"]: len(g["entries"]) for g in snap["groups"]}
live_cat = {g["type2Name"]: len(g["entries"]) for g in live["groups"]}
print("L0 分组条数：快照 %s · 实时 %s · %s"
      % (snap_cat, live_cat, "✅ 一致" if snap_cat == live_cat else "🔴 不一致"))

ok = not (new or gone or diffs or cat_diff) and snap_cat == live_cat
print("== 结论：%s ==" % ("✅ 电信采集与上游实时完全一致（同日双采零差异）" if ok
                        else "⚠️ 存在差异（快照 %s 采，实时较新，可能含上游当日变更）" % snap.get("fetchedAt")))
sys.exit(0 if ok else 4)
