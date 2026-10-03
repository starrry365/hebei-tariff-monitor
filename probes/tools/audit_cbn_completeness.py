# -*- coding: utf-8 -*-
"""广电采集完整性独立实测 —— 不信任任何历史结论，逐项重验。

验证点（对应 probes/he_cbn_tariff.py 文件头的三个历史前提 + 用户新怀疑）：
  V1 区域全集：qryAreaList 33 个区域里，除 ZZZZ/HB00 外是否还有河北用户相关的？
  V2 type1 忽略论：type1=GZ/ZQ/空/乱值，返回是否仍完全相同？（政企 ZQ 有没有独有数据）
  V3 无分页论：一次全量 vs 逐 type2 拉取并集，是否一致？响应里有没有分页字段？
  V4 分类树完备：树上叶子 code 集合 vs 明细数据 type2/type3 集合，双向差集。
  V5 与今日快照逐条对账：reportNo 双向差集 + 字段抽样。

用法：python probes/tools/audit_cbn_completeness.py
exit 0=完整；exit 4=发现缺口。
"""
import gzip
import json
import os
import sys
import time
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "probes"))
sys.path.insert(0, os.path.join(REPO, "probes", "tools"))

import he_cbn_tariff as C   # noqa: E402

fails = []
notes = []


def log(*a):
    print(*a, flush=True)


def ms():
    return int(time.time() * 1000)


# ── V1 区域全集 ──────────────────────────────────────────────────────────
log("== V1 区域全集 ==")
j = C._ok(C.post("/busi/qryAreaList",
                 {"channelId": C.CHANNEL, "timestamp": ms()}), "qryAreaList")
regional = (j.get("data") or {}).get("regionalList") or []
log("regionalList %d 个：" % len(regional))
for a in regional:
    log("   %-8s %s" % (a.get("areaCode") or a.get("code"), a.get("areaName") or a.get("name")))
codes = [a.get("areaCode") or a.get("code") for a in regional]
# 候选相关区域：全国 + 河北 + 可能的聚合区（京津冀等，按名字判）
cand = []
for a in regional:
    nm = (a.get("areaName") or a.get("name") or "")
    cd = a.get("areaCode") or a.get("code")
    if cd in ("ZZZZ", "HB00") or ("京" in nm and "津" in nm) or "华北" in nm:
        cand.append((cd, nm))
log("候选额外区域：%s" % (cand or "无（除 ZZZZ/HB00）"))

# ── V2 type1 忽略论 ─────────────────────────────────────────────────────
log("== V2 type1 忽略论（ZZZZ 与 HB00 各测）==")
for area in ("ZZZZ", "HB00"):
    res = {}
    for t1 in ("GZ", "ZQ", "XX"):
        body = {"channelId": C.CHANNEL, "applicableArea": area, "type1": t1, "timestamp": ms()}
        try:
            jj = C._ok(C.post("/goods/queryTariffAllByCond", body), "AllByCond t1=%r" % t1)
        except RuntimeError as e:
            res[t1] = None
            log("   %s type1=%-3r → 异常：%s" % (area, t1, str(e)[:90]))
            continue
        arr = jj.get("data") or []
        res[t1] = arr
        log("   %s type1=%-3r → %d 条（stateFlag: %s）"
            % (area, t1, len(arr), dict(Counter(str(x.get('stateFlag')) for x in arr))))
        time.sleep(0.4)
    base = res["GZ"]
    for t1, arr in res.items():
        if t1 == "GZ" or arr is None:
            continue
        if len(arr) != len(base):
            fails.append("V2: %s type1=%r 条数 %d ≠ GZ %d" % (area, t1, len(arr), len(base)))
            continue
        ids_a = {str(x.get("id") or x.get("tariffId") or x.get("reportNo")) for x in arr}
        ids_b = {str(x.get("id") or x.get("tariffId") or x.get("reportNo")) for x in base}
        if ids_a != ids_b:
            fails.append("V2: %s type1=%r 集合与 GZ 不同（独有 %d / 缺 %d）"
                         % (area, t1, len(ids_a - ids_b), len(ids_b - ids_a)))
log("   （若上方各 type1 条数一致则忽略论仍成立）")

# ── V3 无分页论 + V4 分类树完备 ─────────────────────────────────────────
log("== V3/V4 逐 type2 拆拉 vs 一次全量（ZZZZ / HB00）==")
tree_all = {}
for area in ("ZZZZ", "HB00"):
    jj = C._ok(C.post("/goods/queryTariffCondition",
                      {"channelId": C.CHANNEL, "applicableArea": area, "timestamp": ms()}),
               "Condition %s" % area)
    leaves, t3leaves = [], []

    def walk(nodes, lv=1):
        for n in nodes or []:
            c, nm = n.get("typeCode"), n.get("typeName")
            if c:
                (t3leaves if lv >= 3 else leaves).append((c, nm, lv))
            walk(n.get("childTariffTypes"), lv + 1)
    walk(jj.get("data"))
    tree_all[area] = (leaves, t3leaves)
    log("   %s 树：二级叶子 %d · 三级叶子 %d" % (area, len(leaves), len(t3leaves)))

    full = C._ok(C.post("/goods/queryTariffAllByCond",
                        {"channelId": C.CHANNEL, "applicableArea": area,
                         "type1": "GZ", "timestamp": ms()}), "AllByCond full")
    arr = full.get("data") or []
    # 分页字段探测
    meta = {k: v for k, v in full.items() if k not in ("status", "message", "data")}
    log("   一次全量 %d 条 · 响应额外字段：%s" % (len(arr), meta or "无"))
    # 逐 type2 拆拉
    t2s = sorted({str(x.get("type2") or x.get("tariffType") or "") for x in arr})
    if not t2s or t2s == [""]:
        # 找出明细里的类型字段名
        log("   ⚠️ 明细无 type2 字段，字段名：%s" % sorted(arr[0].keys()) if arr else "   （空）")
        t2s = sorted({c for c, _, _ in leaves})
    union, per = [], {}
    for t2 in t2s:
        jj2 = C._ok(C.post("/goods/queryTariffAllByCond",
                           {"channelId": C.CHANNEL, "applicableArea": area,
                            "type1": "GZ", "type2": t2, "timestamp": ms()}),
                    "AllByCond t2=%s" % t2)
        a2 = jj2.get("data") or []
        per[t2] = len(a2)
        union.extend(a2)
        time.sleep(0.3)
    ids_full = {str(x.get("id") or x.get("reportNo")) for x in arr}
    ids_uni = {str(x.get("id") or x.get("reportNo")) for x in union}
    log("   拆拉并集 %d 条（%s）" % (len(union), per))
    if ids_uni - ids_full:
        fails.append("V3: %s 拆拉比全量多 %d 条（全量漏！）：%s"
                     % (area, len(ids_uni - ids_full), list(ids_uni - ids_full)[:5]))
    if ids_full - ids_uni:
        notes.append("V3: %s 全量比拆拉多 %d 条（type2 为空/特殊？）"
                     % (area, len(ids_full - ids_uni)))
    # 树 vs 明细
    det_t2 = {str(x.get("type2") or x.get("tariffType") or "") for x in arr}
    tree_t2 = {c for c, _, _ in leaves}
    log("   树二级 %s · 明细 type2 %s" % (sorted(tree_t2), sorted(det_t2)))
    if tree_t2 - det_t2:
        notes.append("V4: %s 树上有但明细无 type2：%s" % (area, sorted(tree_t2 - det_t2)))
    if det_t2 - tree_t2:
        fails.append("V4: %s 明细有但树上无 type2：%s" % (area, sorted(det_t2 - tree_t2)))

# ── V5 与今日快照对账 ───────────────────────────────────────────────────
log("== V5 与今日快照对账 ==")
import glob  # noqa: E402
snap = sorted(glob.glob(os.path.join(REPO, "cloud", "tariff", "snapshots",
                                     "cbn_tariff_2026100*.json.gz")))[-1]
d = json.load(gzip.open(snap, "rt", encoding="utf-8"))
snap_by_rn = {}
for e in d["entries"]:
    snap_by_rn.setdefault(str(e.get("reportNo") or "").strip(), []).append(e)
# 实时侧用同一键：filingNumber（normalize 的 reportNo 来源；raw 的 id 是 UUID，不能混用）
live_raw = []
for area, attr in (("ZZZZ", "1"), ("HB00", "2")):
    jj = C._ok(C.post("/goods/queryTariffAllByCond",
                      {"channelId": C.CHANNEL, "applicableArea": area,
                       "type1": "GZ", "timestamp": ms()}), "AllByCond %s" % area)
    for x in jj.get("data") or []:
        x["_attr"] = attr
        live_raw.append(x)
live_by_rn = {}
for x in live_raw:
    live_by_rn.setdefault(str(x.get("filingNumber") or "").strip(), []).append(x)
log("   快照 %d · 实时 %d · 键（filingNumber）快照独有 %d · 实时独有 %d"
    % (len(d["entries"]), len(live_raw),
       len(set(snap_by_rn) - set(live_by_rn)), len(set(live_by_rn) - set(snap_by_rn))))
if set(live_by_rn) - set(snap_by_rn):
    fails.append("V5: 实时比快照多 %d 条（漏采！）：%s"
                 % (len(set(live_by_rn) - set(snap_by_rn)),
                    sorted(set(live_by_rn) - set(snap_by_rn))[:8]))
if set(snap_by_rn) - set(live_by_rn):
    notes.append("V5: 快照比实时多 %d 条（上游下架？）：%s"
                 % (len(set(snap_by_rn) - set(live_by_rn)),
                    sorted(set(snap_by_rn) - set(live_by_rn))[:8]))
# 字段级：实时过同一个 normalize 再比（同日双采应当零差异）
names = C.type_names()
diff_ct = Counter()
diff_ex = {}
for rn, xs in live_by_rn.items():
    for x in xs:
        e = C.normalize(x, names, x["_attr"])
        # 快照同 reportNo 可能多条（不同区域），按 (reportNo, tariffAttr, name) 匹配
        cands = snap_by_rn.get(rn) or []
        hit = None
        for s in cands:
            if s.get("name") == e.get("name") and s.get("tariffAttr") == e.get("tariffAttr"):
                hit = s
                break
        if hit is None:
            diff_ct["bind"] += 1
            diff_ex.setdefault("bind", []).append("%s %s：实时条在快照无同名同区匹配" % (rn, e.get("name")))
            continue
        for k in ("name", "fees", "data", "dataUnit", "call", "onlineDay", "offineDay",
                  "type2", "type2Name", "stateFlag", "extraFees", "validPeriod", "brandwidth"):
            if hit.get(k) != e.get(k):
                diff_ct[k] += 1
                diff_ex.setdefault(k, []).append(
                    "%s %s：快照 %r ≠ 实时 %r" % (rn, str(e.get("name"))[:18], hit.get(k), e.get(k)))
log("   字段级（13 字段 × %d 条实时）：%s"
    % (len(live_raw), "✅ 零差异" if not diff_ct else "🔴 " + str(dict(diff_ct))))
for k, n in diff_ct.most_common():
    log("      %s：%d · 例：%s" % (k, n, "; ".join(diff_ex[k][:2])))
    fails.append("V5 字段 %s 差异 %d" % (k, n))

log("== 结论 ==")
for n in notes:
    log("  ℹ️ %s" % n)
if fails:
    for f in fails:
        log("  🔴 %s" % f)
    log("  ⚠️ 发现 %d 个缺口，exit 4" % len(fails))
    sys.exit(4)
log("  ✅ 五项验证均无缺口，exit 0")
