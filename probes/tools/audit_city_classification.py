# -*- coding: utf-8 -*-
"""地市分类端到端独立审计（2026-10-03）。

对象：面板行数据的 cty（城市专属）/ pw（不限地市）/ sc（板块）。
方法：**不导入 tariff_monitor**，按设计规范独立重实现期望值，
      与面板归档逐条对账；另做源码表外的「未知地市码」扫描。

层级：
  L1 绑定   面板行 ↔ 源快照条目（reportNo 双向唯一）
  L2 逐条   移动/电信（地市码→名 + 文案补雄安/华油）、联通（_cityNames/_allCity）
  L3 不变式 在售：cty/pw 恰好其一（CITY_NETS）；已下架：两者皆无；
            城市名 ⊆ 13 档；cbn 全无
  L4 未知码 源字段里出现、但码表没有的 4 位码（会被静默丢弃 → 必须人工看）
  L5 下拉   新口径模拟：每市条数 = 在售 cty 含该市；_none = 在售 pw

用法：python probes/tools/audit_city_classification.py [页面.html|html.gz]
退出码：0 = 全部通过；4 = 有差异。
"""
import gzip
import json
import os
import re
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "probes"))

import he_unicom_tariff as U  # 只取 CITY 常量清单（数据，非逻辑）

PAGE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    REPO, "cloud", "tariff", "page", "index.html.gz")
SNAP = os.path.join(REPO, "cloud", "tariff", "snapshots")

fails, notes = [], []


def log(msg):
    print(msg, flush=True)


def err(msg):
    fails.append(msg)
    log("   🔴 " + msg)


# ── 独立重实现的地市知识（数据表，与构建侧同源但独立转录）──────────────
HB_CITY = {"3100": "邯郸", "3110": "石家庄", "3120": "保定", "3121": "雄安新区",
           "3130": "张家口", "3140": "承德", "3150": "唐山", "3160": "廊坊",
           "3170": "沧州", "3180": "衡水", "3190": "邢台", "3350": "秦皇岛"}
CITY_ORDER = ["石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定",
              "张家口", "承德", "沧州", "廊坊", "衡水"]
CITY_EXTRA = ["雄安新区", "华北油田"]
CITY_ALL = set(CITY_ORDER) | set(CITY_EXTRA)
TEXT_CITY_LS = CITY_ORDER
TEXT_CITY_ALIAS = [("雄安新区", re.compile(r"雄安")), ("华北油田", re.compile(r"华北油田|华油"))]


def toks(v):
    return [t.strip() for t in str(v or "").split(",") if t.strip()]


def uniq(xs):
    out, seen = [], set()
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def mk(codes):
    return uniq(HB_CITY[c] for c in uniq(codes) if c in HB_CITY)


def text_cities(e):
    s = " ".join(str(e.get(k) or "") for k in
                 ("name", "tariffName", "applicablePeople", "otherContent"))
    out = [c for c in TEXT_CITY_LS if c in s]
    out += [n for n, pat in TEXT_CITY_ALIAS if pat.search(s)]
    return uniq(out)


def mv_where(e):
    """移动：按设计规范重实现（码权威 → 文案兜底/补专属档）。"""
    aa, ct, pv = toks(e.get("applicableArea")), toks(e.get("city")), toks(e.get("province"))
    if set(HB_CITY) <= set(uniq(aa + ct)):
        sc, cities = "hb", []
    else:
        cities = mk(aa + ct)
        if cities:
            sc = "hb"
        elif "000" in aa:
            sc, cities = "cn", []
        elif any(t.startswith("!") for t in aa):
            sc, cities = "cn", []
        else:
            letters = [t for t in aa if len(t) == 2 and t.isalpha()]
            if len(letters) >= 2:
                sc, cities = ("cn", []) if "HE" in letters else ("", [])
            elif len(letters) == 1:
                sc, cities = ("hb", []) if letters[0] == "HE" else ("", [])
            elif len(pv) >= 2:
                sc, cities = ("cn", []) if "311" in pv else ("", [])
            elif len(pv) == 1:
                sc, cities = ("hb", []) if pv[0] == "311" else ("", [])
            elif ct and all(t in HB_CITY for t in ct):
                sc, cities = "hb", mk(ct)
            elif not aa and not ct and not pv:
                sc, cities = "hb", []
            else:
                sc, cities = "", []
    # 文案互补（rows_of 规范）：无码 → 文案全量；有码 → 只补雄安/华油
    tb = text_cities(e)
    if not cities:
        cities = tb
    else:
        cities = uniq(list(cities) + [c for c in tb if c in CITY_EXTRA and c not in cities])
    return sc, cities


def ct_where(e):
    toks_a = toks(e.get("_areaCodes"))
    cities = [] if set(HB_CITY) <= set(uniq(toks_a)) else mk(toks_a)
    tb = text_cities(e)
    if not cities:
        cities = tb
    else:
        cities = uniq(list(cities) + [c for c in tb if c in CITY_EXTRA and c not in cities])
    return "hb", cities


def uc_where(e):
    return "hb", uniq(n for n in (e.get("_cityNames") or []) if n in CITY_ALL)


def load_page(path):
    op = gzip.open if path.endswith(".gz") else open
    html = op(path, "rt", encoding="utf-8").read()
    i = html.index("const NETS=") + len("const NETS=")
    nets, _ = json.JSONDecoder().raw_decode(html[i:])
    return nets


def load_snap(prefix):
    fs = sorted(glob_snap(prefix))
    if not fs:
        err("找不到快照 %s" % prefix)
        return None
    return json.load(gzip.open(fs[-1], "rt", encoding="utf-8"))


def glob_snap(prefix):
    import glob as _g
    return _g.glob(os.path.join(SNAP, prefix + "_2026*.json.gz"))


# ════════════════════════════════════════════════════════════════════════
log("== 地市分类端到端独立审计 ==")
log("页面：%s" % PAGE)
nets = load_page(PAGE)

SRC = {"move": ("hebei_tariff", mv_where),
       "unicom": ("unicom_tariff", uc_where),
       "telecom": ("ct_tariff", ct_where)}

diff_cty = diff_pw = diff_sc = 0
checked = 0

for code, (prefix, fn) in SRC.items():
    log("== %s：绑定 + 逐条重算 ==" % code)
    rows = nets[code]["rows"]
    snap = load_snap(prefix)
    if snap is None:
        continue
    ent = ([e for g in snap.get("groups", []) for e in g.get("entries", [])]
           if code == "move" else
           snap.get("entries") or [e for g in snap.get("groups", []) for e in g.get("entries", [])])
    by_rn = {}
    dup_src = 0
    for e in ent:
        rn = str(e.get("reportNo") or "").strip()
        if not rn:
            continue
        if rn in by_rn:
            dup_src += 1
        by_rn[rn] = e
    if dup_src:
        notes.append("%s 源 reportNo 重复 %d 条（取后者）" % (code, dup_src))
    rns = [str(r.get("r") or "").strip() for r in rows]
    no_rn = sum(1 for x in rns if not x)
    if no_rn:
        err("%s 面板 %d 行没有绑定键 r" % (code, no_rn))
    dup_panel = len(rns) - len(set(rns))
    if dup_panel:
        err("%s 面板 r 重复 %d 条" % (code, dup_panel))
    orphans = [x for x in set(rns) if x and x not in by_rn]
    if orphans:
        err("%s 面板 %d 行在源里找不到（样例 %s）" % (code, len(orphans), orphans[:3]))

    # 源里没进面板的条目：期望是 sc=""（与河北无关被丢弃）
    dropped = [e for rn, e in by_rn.items() if rn not in set(rns)]
    bad_drop = []
    for e in dropped:
        sc, _ = fn(e)
        if sc != "":
            bad_drop.append(e)
    if bad_drop:
        err("%s 源有 %d 条未入面板但 sc≠“”（样例 %s）"
            % (code, len(bad_drop), [str(e.get("reportNo")) for e in bad_drop[:3]]))
    else:
        log("   源 %d 条 · 面板 %d 行 · 源独有 %d 条全部 sc=“”（合法丢弃）"
            % (len(by_rn), len(rows), len(dropped)))

    for r in rows:
        e = by_rn.get(str(r.get("r") or "").strip())
        if e is None:
            continue
        checked += 1
        sc, cities = fn(e)
        st = bool(r.get("st"))
        exp_cty = [] if st else cities
        exp_pw = 1 if (not st and not cities and code in ("move", "unicom", "telecom")) else None
        got_cty = list(r.get("cty") or [])
        got_pw = r.get("pw")
        if got_cty != exp_cty:
            diff_cty += 1
            if diff_cty <= 3:
                err("%s r=%s cty 面板=%s 期望=%s" % (code, r.get("r"), got_cty, exp_cty))
        if (got_pw or None) != (exp_pw or None):
            diff_pw += 1
            if diff_pw <= 3:
                err("%s r=%s pw 面板=%s 期望=%s" % (code, r.get("r"), got_pw, exp_pw))
        if r.get("sc") != sc:
            diff_sc += 1
            if diff_sc <= 3:
                err("%s r=%s sc 面板=%s 期望=%s" % (code, r.get("r"), r.get("sc"), sc))

log("== 逐条结果：比对 %d 行 · cty 差异 %d · pw 差异 %d · sc 差异 %d =="
    % (checked, diff_cty, diff_pw, diff_sc))
if diff_cty or diff_pw or diff_sc:
    err("存在逐条差异")

# ── L3 不变式 ─────────────────────────────────────────────────────────
log("== L3 不变式 ==")
for code in ("move", "unicom", "telecom", "cbn"):
    rows = nets[code]["rows"]
    both = [r for r in rows if r.get("cty") and r.get("pw")]
    sale_neither = [r for r in rows if not r.get("cty") and not r.get("pw")
                    and not r.get("st") and code in ("move", "unicom", "telecom")]
    stop_with = [r for r in rows if (r.get("cty") or r.get("pw")) and r.get("st")]
    unknown = {c for r in rows for c in (r.get("cty") or [])} - CITY_ALL
    cbn_bad = [r for r in rows if code == "cbn" and (r.get("cty") or r.get("pw"))]
    line = "   %s：并存 %d · 在售皆无 %d · 下架带归属 %d · 越界城市 %s" % (
        code, len(both), len(sale_neither), len(stop_with), sorted(unknown) or "无")
    log(line)
    if both:
        err("%s %d 行 cty/pw 并存" % (code, len(both)))
    if sale_neither:
        err("%s %d 条在售既无 cty 也无 pw" % (code, len(sale_neither)))
    if stop_with:
        err("%s %d 条已下架带归属（设计：刻意不写）" % (code, len(stop_with)))
    if unknown:
        err("%s 越界城市名 %s" % (code, sorted(unknown)))
    if cbn_bad:
        err("cbn %d 行带地市（上游无此维度）" % len(cbn_bad))

# ── L4 未知地市码扫描 ────────────────────────────────────────────────
log("== L4 源字段未知 4 位码扫描（码表外的码会被静默丢，必须人工确认）==")
unknown_hits = Counter()
unknown_samples = {}
for code, (prefix, _fn) in (("move", ("hebei_tariff", 0)), ("telecom", ("ct_tariff", 0))):
    snap = load_snap(prefix)
    if snap is None:
        continue
    ent = ([e for g in snap.get("groups", []) for e in g.get("entries", [])]
           if code == "move" else
           snap.get("entries") or [e for g in snap.get("groups", []) for e in g.get("entries", [])])
    for e in ent:
        fields = ([e.get("applicableArea"), e.get("city"), e.get("province")]
                  if code == "move" else [e.get("_areaCodes")])
        for t in uniq(toks(" ".join(str(f or "") for f in fields))):
            if re.fullmatch(r"\d{4}", t) and t not in HB_CITY:
                unknown_hits[(code, t)] += 1
                unknown_samples.setdefault((code, t), str(e.get("reportNo") or e.get("reportno") or "?"))
if unknown_hits:
    for (code, t), n in sorted(unknown_hits.items()):
        log("   ⚠️ %s 码 %s 出现 %d 条（样例 r=%s）" % (code, t, n, unknown_samples[(code, t)]))
    notes.append("码表外 4 位码 %d 种（见上，人工确认是否新地市/新区）" % len(unknown_hits))
else:
    log("   无码表外 4 位码")

# ── L5 下拉口径模拟（2026-10-03 定版：条数 = 该市专属）────────────────
log("== L5 下拉口径模拟（条数 = 在售 cty 含该市；_none = 在售 pw）==")
for code in ("move", "unicom", "telecom"):
    rows = nets[code]["rows"]
    sale = [r for r in rows if not r.get("st")]
    pw_n = sum(1 for r in sale if r.get("pw"))
    has = any(r.get("cty") for r in rows)
    if not has:
        log("   %s：无地市维度（页面自动隐藏），在售 %d" % (code, len(sale)))
        continue
    per = {c: sum(1 for r in sale if c in (r.get("cty") or [])) for c in CITY_ORDER + CITY_EXTRA}
    tot = pw_n + len([r for r in sale if r.get("cty")])
    log("   %s：在售 %d = pw %d + 专属 %d · 各市专属 %s"
        % (code, len(sale), pw_n, len([r for r in sale if r.get("cty")]),
           {k: v for k, v in per.items() if v}))
    zero = [c for c, v in per.items() if v == 0]
    if zero:
        log("      （专属为 0 的档：%s —— 页面将显示（0）并置灰，与筛选结果一致）" % zero)
    if pw_n + len([r for r in sale if r.get("cty")]) != len(sale):
        err("%s 在售 = pw + 专属 不闭合" % code)

log("== 结论：%s ==" % ("✅ 地市分类全部正确" if not fails else "⚠️ %d 处差异，见上" % len(fails)))
if notes:
    log("（备注：%s）" % "；".join(notes))
sys.exit(0 if not fails else 4)
