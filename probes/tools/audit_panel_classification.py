#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面板（page/index.html.gz 归档页）联通行分类正确性复核。

独立于构建链路复核「面板行的每个分类字段是否与源快照一致」：
  · 绑定：行 r(reportNo) ↔ 源条目 reportNo，两侧都必须唯一；
  · sect（板块）：源 _attr → ATTR_CN（1=全国资费 2=本省资费）；
  · st（已下架）：源 _firstLevel==99；
  · cat/ty（大类/细分）：🔴 联通停售桶要按二级栏目还原真实分类
    （cat_src = 二级栏目名，细分留空），在售 = 一级/二级照抄 —— 在复核侧
    从**源字段**重算，不读面板的 l1/l2，然后与面板比对；
  · l1/l2（溯源）：只在还原发生时写（停售行 l1=停售套餐、l2=原二级）；
  · cty/pw（地市）：在售才写 —— _cityNames → cty，_allCity → pw=1，已下架两者皆无；
  · sc：联通恒 hb（无省份维度）。
另对各网做轻量总账：条数、cat 分布、「其他」必须为 0。

用法：
  python probes/tools/audit_panel_classification.py
"""
import gzip
import json
import os
import sys
from collections import Counter
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tariff_monitor as T  # noqa: E402  （只借 type_cat/ATTR_CN 常量，不借行构造）

ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    REPO, "cloud", "tariff", "page", "index.html.gz")
SNAP_DIR = os.path.join(REPO, "cloud", "tariff", "snapshots")
PREFIX = {"move": "hebei_tariff_", "unicom": "unicom_tariff_",
          "telecom": "ct_tariff_", "cbn": "cbn_tariff_"}
ATTR_CN = {"1": "全国资费", "2": "本省资费", "3": "集团资费"}  # 3=集团，2026-10-03 起


def log(m):
    print(m, flush=True)


def latest_snapshot(prefix):
    cand = []
    for nm in os.listdir(SNAP_DIR):
        if nm.startswith(prefix) and nm.endswith(".json.gz"):
            cand.append((nm[len(prefix):-len(".json.gz")], os.path.join(SNAP_DIR, nm)))
    return max(cand) if cand else (None, None)


# ── 1. 解包面板 ──────────────────────────────────────────────────────────
opener = gzip.open if ARCHIVE.endswith(".gz") else open
html = opener(ARCHIVE, "rt", encoding="utf-8").read()
key = "const NETS="
i = html.index(key) + len(key)
nets, _end = json.JSONDecoder().raw_decode(html[i:])
nets = T.decode_nets_from_page(nets)   # 2026-10-06 页面数据列式编码：解回 rows
log("面板归档解包：四网 %s" % {k: len(v.get("rows") or []) for k, v in nets.items()})

fails = []

# ── 2. 各网轻量总账 ──────────────────────────────────────────────────────
log("== 各网总账（面板 vs 源快照）==")
for code, pfx in PREFIX.items():
    day, snap = latest_snapshot(pfx)
    rows = nets.get(code, {}).get("rows") or []
    if not snap:
        log("   %s：面板 %d 条，本地无快照（跳过源比对）" % (code, len(rows)))
        continue
    src = json.load(gzip.open(snap, "rt", encoding="utf-8"))
    # 移动快照没有扁平 entries（条目在 groups[].entries 里），两形态都兼容
    ent = src.get("entries") or []
    if not ent:
        ent = [e for g in (src.get("groups") or []) for e in (g.get("entries") or [])]
    cat = Counter(r.get("cat") for r in rows)
    other = cat.get("其他", 0)
    log("   %s：面板 %d · 快照(%s) %d · %s%s"
        % (code, len(rows), day, len(ent),
           "cat：" + " · ".join("%s %d" % kv for kv in sorted(cat.items())),
           " 🔴「其他」%d 条" % other if other else ""))
    if len(rows) != len(ent):
        fails.append("%s 条数不一致：面板 %d vs 快照 %d" % (code, len(rows), len(ent)))
    if other:
        fails.append("%s cat「其他」%d 条（映射表漏了）" % (code, other))
    base = (nets.get(code) or {}).get("base")
    if base and base.replace("-", "") != day:
        fails.append("%s 面板基线 %s ≠ 最新快照 %s（归档可能滞后）" % (code, base, day))

# ── 3. 联通行逐条复核 ────────────────────────────────────────────────────
log("== 联通逐条复核（分类核心）==")
day, snap = latest_snapshot(PREFIX["unicom"])
src = json.load(gzip.open(snap, "rt", encoding="utf-8"))
by_rn = {}
for e in src["entries"]:
    rn = str(e.get("reportNo") or "").strip()
    if rn in by_rn:
        fails.append("源快照 reportNo 重复：%s" % rn)
    by_rn[rn] = e
rows = nets["unicom"]["rows"]
rn_seen = {}
bad = Counter()
examples = {}


def note(fld, r, msg):
    bad[fld] += 1
    examples.setdefault(fld, []).append(msg)


for r in rows:
    rn = str(r.get("r") or "").strip()
    if not rn:
        note("r", r, "行无 reportNo：%s" % r.get("n"))
        continue
    if rn in rn_seen:
        note("r", r, "面板行 reportNo 重复：%s" % rn)
    rn_seen[rn] = 1
    e = by_rn.get(rn)
    if e is None:
        note("bind", r, "面板行在源快照找不到：%s %s" % (rn, r.get("n")))
        continue
    # —— 从源字段独立重算期望值 ——
    # 细分期望按 2026-10-03 重设计规范（三规则，与 rows_of 联通分支同源但独立实现）：
    #   ① 与大类同名（标准资费）→ 留空；② 港澳台前缀剥离；③ 「其他」→「其他加装」。
    # 停售条目上游只有「原一级」一层，细分留空。
    UC_TY_NORM = {"国际/港澳台加装包": "加装包",
                  "国际/港澳台移网套餐": "移网套餐",
                  "其他": "其他加装"}
    fl = str(e.get("_firstLevel") or "")
    l1 = str(e.get("_firstLevelName") or "").strip()
    l2 = str(e.get("_secondLevelName") or e.get("type3Name") or "").strip()
    if l1 == "停售套餐":
        cat_src, sub = (l2 or l1), ""
    elif l2:
        cat_src, sub = l1, l2
    else:
        cat_src, sub = l1, l1
    if sub == cat_src:
        sub = ""
    sub = UC_TY_NORM.get(sub, sub)
    exp = {
        "cat": T.type_cat(cat_src),
        "ty": sub,
        "st": 1 if fl == "99" else None,
        "sect": ATTR_CN.get(str(e.get("_attr") or "").strip()),
        "sc": "hb",
    }
    got = {"cat": r.get("cat"), "ty": r.get("ty") or "", "st": r.get("st"),
           "sect": r.get("sect"), "sc": r.get("sc")}
    for k in exp:
        if got[k] != exp[k]:
            note(k, r, "%s %s：面板 %r ≠ 期望 %r" % (rn, r.get("n"), got[k], exp[k]))
    # 溯源 l1/l2：只在还原（停售）或改名/剥前缀时写；「细分与大类同名」不算还原
    exp_l1 = l1 if l1 != cat_src else None
    exp_l2 = l2 if (l2 and l2 != sub and l2 != l1) else None
    if r.get("l1") != exp_l1:
        note("l1", r, "%s：面板 l1=%r ≠ 期望 %r" % (rn, r.get("l1"), exp_l1))
    if r.get("l2") != exp_l2:
        note("l2", r, "%s：面板 l2=%r ≠ 期望 %r" % (rn, r.get("l2"), exp_l2))
    # 地市：在售三形态互斥完备；已下架两者皆无
    st = fl == "99"
    cty = r.get("cty")
    pw = r.get("pw")
    if st:
        if cty or pw:
            note("cty/st", r, "%s：已下架却带地市(cty=%r pw=%r)" % (rn, cty, pw))
    else:
        src_cty = [n for n in (e.get("_cityNames") or []) if n in T.CITY_ALL]
        if src_cty:
            if cty != src_cty:
                note("cty", r, "%s %s：面板 cty=%r ≠ 源 %r" % (rn, r.get("n"), cty, src_cty))
            if pw:
                note("pw", r, "%s：城市专属却带 pw=1" % rn)
        elif e.get("_allCity"):
            if cty:
                note("cty", r, "%s：全省通用却带 cty=%r" % (rn, cty))
            if pw != 1:
                note("pw", r, "%s：全省通用但 pw=%r ≠ 1" % (rn, pw))
        else:
            note("cty", r, "%s：源条目无任何地市标记（采集侧漏标）" % rn)
    if r.get("a1") != str(e.get("_attr") or "").strip():
        note("a1", r, "%s：面板 a1=%r ≠ 源 _attr" % (rn, r.get("a1")))

# 面板有、源没有的 reportNo
orphan = set(rn_seen) - set(by_rn)
if orphan:
    note("bind", None, "面板行 reportNo 不在源快照：%d 个，例 %s" % (len(orphan), list(orphan)[:5]))

log("面板联通行 %d 条 · reportNo 唯一 %s · 源 %d 条"
    % (len(rows), "是" if not any(b == "r" for b in bad) else "否", len(by_rn)))
log("逐条比对：%s" % ("✅ 全部一致（cat/ty/sect/st/l1/l2/cty/pw/sc/a1 十个维度 × %d 条）" % len(rows)
                      if not bad else "🔴 有差异"))
for fld, n in bad.most_common():
    log("   🔴 %s：%d 条 · 例：%s" % (fld, n, "; ".join(examples[fld][:3])))
    fails.append("联通行 %s 差异 %d 条" % (fld, n))

# 大类分布（面板 vs 重算）
cat_p = Counter(r.get("cat") for r in rows)
cat_e = Counter()
for e in src["entries"]:
    l1 = str(e.get("_firstLevelName") or "").strip()
    l2 = str(e.get("_secondLevelName") or e.get("type3Name") or "").strip()
    cat_e[T.type_cat(l2 or l1 if l1 == "停售套餐" else l1)] += 1
log("大类分布（面板 vs 重算）：" +
    "；".join("%s %d/%d" % (c, cat_p.get(c, 0), cat_e.get(c, 0))
              for c in sorted(set(cat_p) | set(cat_e), key=lambda x: -(cat_p.get(x, 0)))))
if cat_p != cat_e:
    fails.append("大类分布不一致")

# ── 4. 电信逐条复核（本省/集团板块，2026-10-03 集团块接入）───────────────
log("== 电信逐条复核（本省/集团板块）==")
day_t, snap_t = latest_snapshot(PREFIX["telecom"])
src_t = json.load(gzip.open(snap_t, "rt", encoding="utf-8"))
ent_t = src_t.get("entries") or []
if not ent_t:
    ent_t = [e for g in (src_t.get("groups") or []) for e in (g.get("entries") or [])]
by_rn_t = {}
for e in ent_t:
    rn = str(e.get("reportNo") or "").strip()
    if rn in by_rn_t:
        fails.append("电信源快照 reportNo 重复：%s" % rn)
    by_rn_t[rn] = e
attrs_t = {str(g.get("tariffAttr") or "").strip() for g in (src_t.get("groups") or [])}
sect_on_t = len(attrs_t & set(ATTR_CN)) >= 2   # 与 rows_of 同判据：两档才落盘
rows_t = nets["telecom"]["rows"]
bad_t = Counter()
ex_t = {}


def note_t(fld, msg):
    bad_t[fld] += 1
    ex_t.setdefault(fld, []).append(msg)


for r in rows_t:
    rn = str(r.get("r") or "").strip()
    e = by_rn_t.get(rn)
    if e is None:
        note_t("bind", "%s %s：面板行不在源快照" % (rn, r.get("n")))
        continue
    l1 = str(e.get("type2Name") or "").strip()
    l2 = str(e.get("type3Name") or "").strip()   # 集团 81 条有二级，其余空
    exp = {
        "cat": T.type_cat(l1),
        "ty": l2 or l1,   # 无二级 → 细分退回一级（与移动/省级电信同语义）
        "sect": ATTR_CN.get(str(e.get("tariffAttr") or "").strip()) if sect_on_t else None,
        # 电信下架 = 下线日早于基线日（TelecomNet.stopped_of 的独立重实现）
        "st": 1 if (str(e.get("offineDay") or "").strip().isdigit()
                    and len(str(e.get("offineDay")).strip()) == 8
                    and str(e.get("offineDay")).strip() < day_t) else None,
        "a1": str(e.get("tariffAttr") or "").strip(),
        "sc": "hb",
    }
    got = {"cat": r.get("cat"), "ty": r.get("ty") or "", "sect": r.get("sect"),
           "st": r.get("st"), "a1": r.get("a1"), "sc": r.get("sc")}
    for k in exp:
        if got[k] != exp[k]:
            note_t(k, "%s %s：面板 %r ≠ 期望 %r" % (rn, r.get("n"), got[k], exp[k]))
    # 电信没有停售桶/细分改名，l1/l2 溯源恒不应出现
    if r.get("l1") or r.get("l2"):
        note_t("l1/l2", "%s：电信行带了溯源 l1=%r l2=%r" % (rn, r.get("l1"), r.get("l2")))

orphan_t = set(str(r.get("r") or "").strip() for r in rows_t) - set(by_rn_t)
if orphan_t:
    note_t("bind", "面板行 reportNo 不在源快照：%d 个，例 %s" % (len(orphan_t), list(orphan_t)[:5]))
log("电信行 %d 条（本省 %d · 集团 %d）· 源 %d 条 · 板块两档 %s"
    % (len(rows_t), sum(1 for r in rows_t if r.get("sect") == "本省资费"),
       sum(1 for r in rows_t if r.get("sect") == "集团资费"), len(by_rn_t),
       "开" if sect_on_t else "关"))
log("逐条比对：%s" % ("✅ 全部一致（cat/ty/sect/st/a1/sc × %d 条）" % len(rows_t)
                    if not bad_t else "🔴 有差异"))
for fld, n in bad_t.most_common():
    log("   🔴 %s：%d 条 · 例：%s" % (fld, n, "; ".join(ex_t[fld][:3])))
    fails.append("电信行 %s 差异 %d 条" % (fld, n))

log("== 结论：%s ==" % ("✅ 面板数据分类全部正确" if not fails else "🔴 %d 项问题" % len(fails)))
for f in fails:
    log("   - " + f)
sys.exit(0 if not fails else 4)
