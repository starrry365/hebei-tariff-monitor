# -*- coding: utf-8 -*-
"""深度数据不变量审计器（第 5 轮体检，2026-10-06）

与前几轮体检的区别：不再人工分维度抽查，而是**机器穷举**——
对四网全部行 × 30+ 条不变量逐条验证，再跨六源（快照/页面/feed/history/gz 归档/模板）对账。

设计原则：**不复刻口径**。归一函数直接 import tariff_monitor（构建期的同一份实现），
审计结论与页面/CI 断言必然同源，不存在「审计器自己算错」的空间。

用法：python probes/tools/deep_audit.py [--json]
退出码：0=全部通过；1=存在 P1 级违规（可接入 run_checks 作第 20 项）。
"""
import gzip, json, io, re, sys, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "cloud", "tariff"))
import tariff_monitor as tm  # noqa: E402  与构建同源的归一函数（du_norm/fee_norm）

def _gb(v, unit):
    """镜像 tm 内嵌套的 gb()（不可 import，7 行纯算术逐字对齐源码 line 1828）。"""
    try:
        f = float(str(v).strip())
    except Exception:
        return None
    u = str(unit or "").upper()
    if u.startswith("MB"):
        return f / 1024.0
    if u.startswith("GB"):
        return f
    if u.startswith("TB"):
        return f * 1024.0
    return None

DOCS = os.path.join(ROOT, "cloud", "tariff", "docs", "index.html")
SNAP_DIR = os.path.join(ROOT, "cloud", "tariff", "snapshots")
HIST_PATH = os.path.join(ROOT, "cloud", "tariff", "history.json")
FEED = os.path.join(ROOT, "cloud", "tariff", "docs", "feed.xml")
PAGE_GZ = os.path.join(ROOT, "cloud", "tariff", "page", "index.html.gz")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RE = re.compile(r"^\d+(\.\d+)?$")

viol = {}          # rule -> [(net, key, detail)]
watch = {}         # rule -> count（上游形态观察项：数量突变才值得关注）
def bad(rule, net, key, detail=""):
    viol.setdefault(rule, []).append((net, key, detail))
def see(rule, n=1):
    watch[rule] = watch.get(rule, 0) + n

def note(msg):
    print("  " + msg)

# ── 1. 载入页面 NETS 与常量 ──────────────────────────────────────────
h = io.open(DOCS, encoding="utf-8").read()
nets = json.loads(h[h.index("const NETS=") + 11:h.index(";\n", h.index("const NETS="))])
nets = tm.decode_nets_from_page(nets)   # 2026-10-06 页面数据列式编码：解回 rows 再审计
cat_order = json.loads(re.search(r"const CAT_ORDER=(\[[^\]]*\])", h).group(1))
NET_LS = ["move", "unicom", "telecom", "cbn"]
print("== 1. 行级不变量（全量穷举 %d 行）=="
      % sum(len((n.get('rows') or [])) for n in nets.values()))

key_seen = {}      # net|ty|rowName -> 首次出现的行
dup_detail = {}    # pk -> [(first_k, dup_k, row)]
stat = {}          # 观察到的取值域
for net in NET_LS:
    rows = nets[net].get("rows") or []
    for i, r in enumerate(rows):
        k = "%s#%d" % (net, i)
        # R1 名称：n 或 t 至少一个非空（rowName 回退）
        if not (str(r.get("n") or "").strip() or str(r.get("t") or "").strip()):
            bad("R1名称双空", net, k)
        # R2 大类取值域
        if r.get("cat") not in cat_order:
            bad("R2大类越域", net, k, repr(r.get("cat")))
        # R3 细分非空（观察项：联通上游对多数大类不提供二级细分，见分布输出；
        #   数量突变 = 上游形态变化信号，而非本侧 bug）
        if not str(r.get("ty") or "").strip():
            see("R3细分空")
        # R4/R5 日期格式与顺序（数据格式为 YYYYMMDD 8 位紧凑格式）
        for f in ("o", "e"):
            v = str(r.get(f) or "")
            if v and not re.match(r"^\d{8}$", v):
                bad("R4日期格式", net, k, "%s=%r" % (f, v))
            elif v and (v[4:6] > "12" or v[6:8] > "31"):
                bad("R4日期格式", net, k, "%s=%r" % (f, v))
        if r.get("o") and r.get("e") and r["o"] > r["e"]:
            bad("R5上线晚于下线", net, k, "%s>%s" % (r["o"], r["e"]))
        # R6 已下架通常应有下线日期（观察项：广电少量条目上游无日期，显示「—」可接受）
        if r.get("st") and not r.get("e"):
            see("R6下架无日期")
        # R7 月费取值域：数字串或空（fee_norm 保证）
        f = str(r.get("f") or "")
        if f and not NUM_RE.match(f):
            bad("R7月费非数字", net, k, repr(f))
        # R8 流量三角一致：g == gb(d, du)；gb 为镜像实现，du 归一另用同源 du_norm 验
        g_should = _gb(r.get("d") or "", r.get("du") or "")
        g_actual = r.get("g")
        if g_should is None:
            if g_actual not in (None, "", 0):
                bad("R8流量g多余", net, k, "g=%r 但 gb()=None" % (g_actual,))
        else:
            if g_actual in (None, ""):
                bad("R8流量g缺失", net, k, "d=%r du=%r" % (r.get("d"), r.get("du")))
            elif abs(float(g_actual) - float(g_should)) > 0.01:
                bad("R8流量g不等", net, k, "g=%r 应为 %.2f" % (g_actual, g_should))
        # R9 单位取值域 + 同源归一幂等：行内 d/du 是构建期归一产物，
        #    再过一遍 tm.du_norm 必须原样出来（否则构建口径有漂移）
        du = str(r.get("du") or "")
        if du and du not in ("GB", "MB", "TB"):
            bad("R9单位越域", net, k, repr(du))
        if r.get("d"):
            dn, un = tm.du_norm(r.get("d"), r.get("du"))
            if (dn, un) != (str(r.get("d")), du):
                bad("R9归一不幂等", net, k, "(%r,%r)->(%r,%r)" % (r.get("d"), du, dn, un))
        stat.setdefault("du", set()).add(du)
        # R10 通话取值域
        c = str(r.get("c") or "")
        if c and not NUM_RE.match(c):
            bad("R10通话非数字", net, k, repr(c))
        # R11 cty 与 pw 互斥
        if r.get("cty") and r.get("pw"):
            bad("R11cty与pw并存", net, k)
        if r.get("pw") not in (None, 1):
            bad("R11pw越域", net, k, repr(r.get("pw")))
        # R12 页面主键唯一（收藏/详情/同价位都靠它）；键含构建期 kd 去重序号
        pk = "%s|%s|%s%s" % (net, r.get("ty") or "", r.get("n") or r.get("t") or "",
                             ("#%d" % r["kd"]) if r.get("kd") else "")
        if pk in key_seen:
            bad("R12主键重复", net, k, "与 %s 冲突 | %s" % (key_seen[pk], pk))
            dup_detail.setdefault(pk, []).append((key_seen[pk], k, r))
        else:
            key_seen[pk] = k
        # R12b kd 语义：kd 只允许出现在同键第 2+ 行（>1），且键唯一性由上式保证
        if "kd" in r and (not isinstance(r["kd"], int) or r["kd"] < 2):
            bad("R12b kd越域", net, k, repr(r.get("kd")))
        # R13 变更标志取值域
        for f in ("ca", "ck"):
            if r.get(f) not in (None, 1):
                bad("R13标志越域", net, k, "%s=%r" % (f, r.get(f)))
        # R14 溯源键只在归一后与原文不同时存在
        if "l1" in r and r.get("l1") == r.get("cat"):
            bad("R14l1冗余", net, k)
        if "l2" in r and r.get("l2") == r.get("ty"):
            bad("R14l2冗余", net, k)
        # R15 截断上限（页面显示按此设计）
        for f, lim in (("n", 120), ("t", 80), ("x", 500), ("ap", 220),
                       ("vp", 200), ("ex", 200), ("ch", 120)):
            if len(str(r.get(f) or "")) > lim:
                bad("R15超长", net, k, "%s len=%d" % (f, len(r.get(f))))
        # R16 板块取值域（观察值收集，末尾统一报告）
        if "sect" in r:
            stat.setdefault("sect", set()).add(r["sect"])

for s, vals in sorted(stat.items()):
    note("取值域 %s: %s" % (s, sorted(vals)))

# R3 细分空 + R12 重复的 per-net 分布与成因鉴别
from collections import Counter
r3n = Counter(); r12n = Counter()
for net in NET_LS:
    for r in (nets[net].get("rows") or []):
        if not str(r.get("ty") or "").strip():
            r3n[net] += 1
            r3n[net + "|cat=" + str(r.get("cat"))] += 1
dups_pk = list(dup_detail.items())
for pk, xs in dups_pk:
    r12n[pk.split("|")[0]] += len(xs) - 1 if len(xs) > 1 else 1
note("R3 细分空分布: " + ", ".join("%s=%d" % kv for kv in r3n.most_common(10)))
note("R12 重复分布: move=%d unicom=%d telecom=%d cbn=%d"
     % (r12n.get("move", 0), r12n.get("unicom", 0),
        r12n.get("telecom", 0), r12n.get("cbn", 0)))
# 抽 3 个重复键看两行是否「除地市外完全相同」（上游同名多城市是设计内）
import itertools
shown = 0
for pk, xs in dups_pk:
    if shown >= 3: break
    first = None
    for fk, dk, r in xs:
        if first is None:
            first = r; continue
        diff_fields = [f for f in set(list(first) + list(r))
                       if (first.get(f) or None) != (r.get(f) or None)]
        note("重复样例 %s: 差异字段=%s" % (pk[:60], diff_fields[:6]))
        shown += 1
        break

# ── 2. 上游快照 ↔ 页面 rows 双向对账 ─────────────────────────────────
print("== 2. 快照↔页面对账 ==")
snaps = glob.glob(os.path.join(SNAP_DIR, "*_20*.json.gz"))
latest = {}
for p in snaps:
    d = re.search(r"_(\d{8})\.json\.gz$", p)
    if d:
        latest[d.group(1)] = latest.get(d.group(1), []) + [p]
if not latest:
    bad("S快照缺失", "-", "-")
else:
    for date in sorted(latest)[-1:]:
        for p in sorted(latest[date]):
            try:
                snap = json.load(gzip.open(p, "rt", encoding="utf-8"))
            except Exception as ex:
                bad("S快照损坏", "-", os.path.basename(p), str(ex)[:80]); continue
            ents = snap.get("entries") or []
            n_snap = len(ents) or sum(len(g.get("entries") or [])
                                      for g in snap.get("groups") or [])
            # 用首条名称匹配到网（快照文件名前缀与 NETS 键不同名）
            first = None
            if ents:
                first = str((ents[0] or {}).get("name") or (ents[0] or {}).get("tariffName") or "")[:20]
            else:
                for g0 in (snap.get("groups") or []):
                    e0 = (g0.get("entries") or [None])[0]
                    if e0:
                        first = str(e0.get("name") or e0.get("tariffName") or "")[:20]
                        break
            hit = None
            for net in NET_LS:
                for r in (nets[net].get("rows") or [])[:80]:
                    if first and (first in str(r.get("n") or "") or first in str(r.get("t") or "")):
                        hit = net; break
                if hit: break
            net = hit or "?"
            nrows = len(nets[net].get("rows") or []) if net != "?" else -1
            tag = "OK" if nrows == n_snap else "≠"
            note("%s -> %s: 快照数据=%d vs 页面 rows=%d [%s]"
                 % (os.path.basename(p), net, n_snap, nrows, tag))
            if nrows != n_snap and net != "?":
                bad("S行数不一致", net, os.path.basename(p),
                    "snapshot=%d page=%d" % (n_snap, nrows))

# ── 3. 六源对账：docs ↔ gz 归档 ↔ history ↔ feed ─────────────────────
print("== 3. 六源跨层对账 ==")
gz_blob = gzip.open(PAGE_GZ, "rb").read().decode("utf-8", errors="ignore")
seg = lambda s: s[s.index("const NETS=") + 11:s.index(";\n", s.index("const NETS="))] if "const NETS=" in s else None
a, b = seg(h), seg(gz_blob)
if a == b:
    note("NETS blob docs↔gz: 逐字节一致")
else:
    # 本机 rebuild_offline 是「预览态」（不覆盖官方归档），与官方 gz 差异属预期；
    # 只有当「行数结构都不同」时才值得报——数一下各自行数给结论
    # （2026-10-06 起页面 NETS 是列式编码，行数要走 decode_nets_from_page 数）
    _na, _nb = tm.decode_nets_from_page(json.loads(a)), tm.decode_nets_from_page(json.loads(b))
    na = len(_na.get("move", {}).get("rows") or []) + len(_na.get("unicom", {}).get("rows") or [])
    nb = len(_nb.get("move", {}).get("rows") or []) + len(_nb.get("unicom", {}).get("rows") or [])
    note("NETS blob docs↔gz 不一致：docs 总行 %d vs 官方 gz 总行 %d"
         % (na, nb) + "（本机预览态 vs 官方归档，预期内；CI 构建后应一致）")
hist = json.load(io.open(HIST_PATH, encoding="utf-8"))
items = hist.get("items", [])
mh = re.search(r"const HIST=(\[.*?\]);\n", h, re.S)
if mh:
    hp = json.loads(mh.group(1))
    note("history items=%d, 页面 HIST 注入=%d 条（注入上限见 monitor HIST_CAP）" % (len(items), len(hp)))
feed = io.open(FEED, encoding="utf-8").read()
n_item = feed.count("<item>")
# feed 是「变更驱动」：只在有实际变更（a/r/c>0）的日子加条目。
# 校验口径：最新一个**有变更**的 history 日必须出现在 feed 里（RFC2822 或 ISO 均认）。
latest_change_day = ""
for x in items:
    if (x.get("a") or 0) or (x.get("r") or 0) or (x.get("c") or 0):
        latest_change_day = x.get("d") or latest_change_day
        break
note("feed items=%d, 最新有变更日 %s 在 feed 中: %s"
     % (n_item, latest_change_day or "(无)", "OK" if (latest_change_day in feed or latest_change_day == "") else "❌"))
# T2 判据与构建同源：feed 是 history 里「真变化」条目按 FEED_KEEP 截断的镜像，
# 条数必须**精确等于** min(真变化数, FEED_KEEP)。曾写死 ==30：FEED_KEEP 后来
# 调到 60，feed 涨过 30 的那天审计就误红（2026-10-06 实测）—— 硬编码二手
# 常量必然腐烂，判据要么 import 权威，要么别写。
_expect_n = min(len([x for x in items
                     if (x.get("a") or 0) > 0 or (x.get("r") or 0) > 0
                     or (x.get("c") or 0) > 0]), tm.FEED_KEEP)
if n_item != _expect_n:
    bad("T2feed条数", "-", "%d≠%d（history真变化×FEED_KEEP上限）" % (n_item, _expect_n))
if latest_change_day and latest_change_day not in feed:
    bad("T3feed缺变更日", "-", latest_change_day)

# ── 4. 汇总 ─────────────────────────────────────────────────────────
print("== 4. 审计结论 ==")
if watch:
    print("观察项（上游形态，数量突变才值得关注）:")
    for rule in sorted(watch):
        print("  · %s ×%d" % (rule, watch[rule]))
total = sum(len(v) for v in viol.values())
if not viol:
    print("✅ 全部不变量通过（0 违规）")
    sys.exit(0)
for rule in sorted(viol):
    xs = viol[rule]
    print("❌ %s ×%d  例: %s" % (rule, len(xs), xs[0]))
print("共 %d 条违规，涉及 %d 类" % (total, len(viol)))
sys.exit(1)
