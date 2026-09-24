# -*- coding: utf-8 -*-
"""资费页面数据体检 —— 改完 template.html 或跑完巡检后执行，专抓「判定类」错误。

为什么需要它
------------
页面的筛选/排序判断写在前端 JS 里，而源数据有几个字段既不干净也不完整：

- ``brandwidth``（宽带）：3671 条为空，有值的里还混着 ``'0'`` / ``'-'`` / ``'无'`` 占位符；
  真含宽带的「河北移动1000M融合宽带」「动感地带芒果卡（宽带版）」反而常常留空。
  判据一旦写成「非空即含」，就会静默多算 116 条、少算 36 条 —— 肉眼翻 3878 行发现不了。
- ``onlineDay`` / ``offineDay``：页面按 8 位数字解析，格式一变（如 ``2003-05-17``）解析就返回 null，
  所有时间筛选会**静默漏掉**那些条目，而页面照样正常渲染。
- 城市：判据在**构建期**（tariff_monitor.WHERE_OF + text_cities）算好写进行数据
  （`d.cty` = 中文名列表）。页面只读。本脚本不再复现那套判据（**复现一份就等于又开一份
  会漂的副本** —— 它已经漂过一次：页面判据升级成「地市码优先」而这里没跟上，
  37 个地市用例恒失败却没人发现，因为平时没人跑）。
  现在改成查**结果**：取值域是否封闭（每个值都能在页面的下拉里选到）、
  「没有 cty 的行」是不是真的从文案里也认不出地市（即文案兜底有没有被漏跑）。

本脚本把页面的判据在 Python 侧复现一遍，把「多算 / 少算」直接算成具体条数；
并额外校验 ``template.html`` 里的规则与这里是否**已经不同步**（改了一边忘了另一边是最大隐患）。

用法:
    python audit_data.py            # 体检并断言，有异常时退出码 1
    python audit_data.py --quiet    # 只输出结论行
数据来源：``docs/index.html`` 里那段 ``const NETS``（四网容器）里的 rows
          （由 --render-only 或巡检生成）。``NET_LIVE`` 只决定**默认**体检哪一网；
          要体检别的网，用 ``python audit_data.py --net cbn`` 显式指定。
          ★ 城市类判据对**三网**成立（移动 / 电信 / 联通 —— 各自判据不同，见 tariff_monitor
            WHERE_OF），只有广电没有地市维度；对它跑城市类判据会得到「全部 0 命中」，
            那不是错，而是「本网没有这个维度」—— 脚本会据此只提示、不判失败。

⚠️ 只断言「结构性」不变量（城市表无零命中、常量与模板同步），
   不断言具体条数 —— 条数随上游数据每天变，断言必假警报。
"""
import json
import math
import os
import re
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "docs", "index.html")
TPL = os.path.join(BASE, "template.html")
sys.path.insert(0, BASE)
# 判据的**唯一权威**就在这个模块里（构建期判据、地市清单、文案兜底规则）。
# 本脚本不再自己留一份副本 —— 见文件头「城市」那段：留副本的代价是两份各自漂。
import tariff_monitor as T      # noqa: E402

# ⚠️ 下面两条规则必须与 template.html 里 bwInfo() 的常量保持一致，
#    脚本末尾的 check_sync() 会从模板里读出来比对，不同步直接报错。
# ⚠️ 必须与 template.html 里**逐字相同**，因为它们是「空值」的判定表 ——
#    各网对「不适用」的写法不一样（广电写 '/'），而判定是跨网共用的。
BW_BADVAL = r"^(0|[-—－\/\\]|无|没有|否)$"
BW_NOTLINE = r"提速|电视|IPTV|检修|装机|调测|加速|绿色上网|调优|扩容|移机"
# 本网自己的数据基线（YYYY-MM-DD），由 load_rows() 填充。四网理论上可不同。
NET_BASE = ""
# 页面搜索实际覆盖的字段（用于比对是否漏字段）—— 必须与 template.html apply() 里那份一致。
# cat / chx 是后来加进页面搜索的（搜「加装包」应命中原分类写「流量包」的条目），
# 加的时候漏了这里 ⇒「搜得到却报缺口」的假警报（2026-09-24 补）。
SEARCH_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "cat", "chx", "x", "bw", "ex", "vp")
ALL_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "x", "bw", "d", "du", "ex", "vp")

BADVAL_RE = re.compile(BW_BADVAL)
NOTLINE_RE = re.compile(BW_NOTLINE)

problems = []

# 默认体检的网（可用 --net 覆盖）。
# ⚠️ 别把它当成「tariff_monitor.NET_LIVE 的副本」——那边是**已接入网的元组**
#    （现在是 ("move","unicom","cbn","telecom")），这里只要一个能取到 rows 的网名。
#    两边同名不同型，是接第二网时就留下的；改名会牵动 conformance.py 的文案，先留着但说清楚。
NET_LIVE = "move"


def load_rows(net=None):
    """取 ``net``（默认 NET_LIVE）那一网的 rows。

    四网改造后页面里的容器是 ``const NETS={move:{...},unicom:{...},...}``。
    体检口径（城市归属、宽带判定）**只对有条目级地市的网成立**（移动 / 电信），
    所以默认取移动；要体检别的网必须显式传 net —— 别指望判据能通用。
    """
    net = net or NET_LIVE
    if not os.path.exists(HTML):
        sys.exit("找不到 %s —— 先跑 python tariff_monitor.py --render-only" % HTML)
    raw = open(HTML, encoding="utf-8").read()
    m = re.search(r"const\s+NETS\s*=", raw)
    if not m:
        sys.exit("页面里找不到 `const NETS=`")
    nets, _ = json.JSONDecoder().raw_decode(raw[m.end():])
    live_net = (nets or {}).get(net) or {}
    rows = live_net.get("rows") or []
    if not rows:
        sys.exit("NETS[%r].rows 为空 —— 那一网的数据没灌进去？（已接入：%s）"
                 % (net, "/".join(sorted(nets))))
    global NET_BASE
    # ★ 基线取**本网自己的** base 字段，而不是全页正则的第一个「数据基线」——
    #   四网基线理论上可以不同（某网某天采失败会沿用前一天），
    #   拿别网的基线算相对天数会**静默**偏移，时间筛选全错还看不出来。
    NET_BASE = str(live_net.get("base") or "")
    return raw, rows


def bw_info(x):
    """复现 template.html 的 bwInfo()：返回 ('field', 速率) / ('name', '含宽带') / None"""
    v = str(x.get("bw") or "").strip()
    n = x.get("n") or ""
    if NOTLINE_RE.search(n):
        return None
    if v and not BADVAL_RE.match(v) and "不涉及" not in v and not NOTLINE_RE.search(v):
        return ("field", v)
    if "宽带" in n:
        return ("name", "含宽带")
    return None


def city_tags(x):
    """该行的城市归属 —— 直接读构建期写好的 ``d.cty``（页面 ``cityTags()`` 也一样）。

    🔴 这里**曾经**是一份「独立复现」：把页面那套文本启发式在 Python 侧又写一遍，
       再用 check_sync() 比对两边的常量字符串。那次它漂了 —— 页面判据升级成
       「上游地市码优先」而这边没跟上，conformance 的 37 个地市用例**恒失败**
       （石家庄：期望 57 / 实际 141，页面是对的），而因为平时没人跑，
       这个偏差安静地存在了很久 —— 典型的「有检查，但检查本身错了」。

       2026-09-24 起判据全在构建期（tariff_monitor.WHERE_OF + text_cities），
       页面与本脚本都只读结果 ⇒ 结构上消灭了「两份副本互相漂」这件事。
       本脚本改为查**结果的性质**（取值域 / 兜底有没有漏跑），见 check_city()。
    """
    return list(x.get("cty") or [])


def check_fields(rows):
    """字段级体检：空值、类型、格式。"""
    out = []
    for k in ("o", "e"):
        bad, blank = [], 0
        for x in rows:
            s = str(x.get(k) or "").strip()
            # ★ 空是**合法状态**，不是格式错：页面下拉里本就有「无上架日期」一档
            #   （`<option value="none">`，筛选写成 `if(on==="none"){if(dt)return false}`），
            #   广电上游对 9 条根本不给上下线日（`_day()` 对空值返回 ""）——
            #   把它算失败等于**每天必红**，而它并不是错。
            #   ⚠️ 真正危险的是「非空但页面解析不出」的值（如 `2026/09/24`）：
            #      页面 toDate() 返回 null ⇒ 那条悄悄落进「无上架日期」桶里，无人察觉。
            #      所以这里只拦「非空且不是 8 位真实日历日」。
            if not s:
                blank += 1
                continue
            if not re.fullmatch(r"\d{8}", s):
                bad.append(x)
                continue
            try:
                # 光有 8 位数字不够：2 月 30 日、4 月 31 日这种「数字合法但日历不存在」的值，
                # 页面侧 Date.UTC 会**静默归一化**到下个月 ⇒ 相对天数与时间筛选整体偏移。
                # 页面 toDate() 已加回读校验，这里用同样的口径拦下。
                date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except ValueError:
                bad.append(x)
        out.append(("%s 日期格式（空，或 8 位真实日历日）" % k, len(bad), 0))
        if blank:
            print("      （%s 空值 %d 条 —— 上游未提供，页面按「无上架日期」处理）" % (k, blank))
    for k, name in (("f", "月费"), ("c", "通话")):
        bad = [x for x in rows if str(x.get(k) or "").strip()
               and not re.fullmatch(r"[0-9.]+", str(x.get(k)).strip())]
        out.append(("%s 非纯数字" % name, len(bad), 0))
    units = sorted({str(x.get("du") or "").strip().upper() for x in rows} - {""})
    # ★ 归一后（见 tariff_monitor.du_norm）单位**只允许**是 GB / MB / TB 三档之一。
    #   这里刻意用精确匹配而不是前缀匹配：前缀匹配会放过 `GB起` / `MB级` 这类
    #   「看着像、其实认不出」的写法 —— 它们正是当初让联通 100M 被漏掉的同类。
    extra = [u for u in units if u not in ("GB", "MB", "TB")]
    out.append(("流量单位取值域（只允许 GB / MB / TB）", len(extra), 0))
    return out, units, extra


def check_bw(rows):
    """宽带判定：多算 / 少算 / 误伤三件事。"""
    kept_field = [x for x in rows if bw_info(x) and bw_info(x)[0] == "field"]
    kept_name = [x for x in rows if bw_info(x) and bw_info(x)[0] == "name"]
    # 旧「非空即含」口径下会被多算的
    holders = [x for x in rows if str(x.get("bw") or "").strip()]
    false_pos = [x for x in holders if not bw_info(x)]
    # 名称命中排除词、但 bw 里写着真实速率的（= 可能被排除词误伤，需人工过目）
    risk = [x for x in rows
            if NOTLINE_RE.search(x.get("n") or "")
            and str(x.get("bw") or "").strip()
            and not BADVAL_RE.match(str(x.get("bw")).strip())
            and not NOTLINE_RE.search(str(x.get("bw")))]
    # 判为含宽带、但名称里写着排除词的（= 规则漏判，必须为 0）
    inconsistent = [x for x in rows if bw_info(x) and NOTLINE_RE.search(x.get("n") or "")]
    return kept_field, kept_name, holders, false_pos, risk, inconsistent


def check_g(rows):
    """d + dataUnit → g 的换算是否自洽。

    ⚠️ 必须显式拦 NaN/Inf：`abs(nan - nan) > 1e-6` 恒为 False，
    只写「差值超限才算错」的话，`gb()` 一旦产出非有限值会**静默通过**，
    而页面会把它渲染成「NaNMB」这类字面垃圾。
    """
    bad = []
    for x in rows:
        d, u, g = str(x.get("d") or "").strip(), str(x.get("du") or "").strip().upper(), x.get("g")
        if g is not None and (not isinstance(g, (int, float)) or not math.isfinite(g)):
            bad.append(x)
            continue
        if not d:
            if g is not None:
                bad.append(x)
            continue
        try:
            v = float(d)
        except ValueError:
            bad.append(x)
            continue
        if not math.isfinite(v):        # 源值本身就是 nan / inf
            bad.append(x)
            continue
        exp = v / 1024.0 if u.startswith("MB") else (v * 1024.0 if u.startswith("TB") else v)
        if g is None or abs(float(g) - exp) > 1e-6:
            bad.append(x)
    return bad


def check_search(rows):
    """页面搜索字段集 vs 全字段：找出「搜不到」的缺口。"""
    gaps = {}
    for kw in ("2000M", "1000M", "宽带", "超套", "光网", "护苗"):
        def hit(k):
            return sum(1 for x in rows if kw.lower() in str(x.get(k, "")).lower())
        page = sum(1 for x in rows
                   if kw.lower() in " ".join(str(x.get(k, "")) for k in SEARCH_FIELDS).lower())
        full = sum(1 for x in rows
                   if kw.lower() in " ".join(str(x.get(k, "")) for k in ALL_FIELDS).lower())
        if full > page:
            gaps[kw] = (page, full)
    return gaps


def check_city(rows, net_code):
    """城市归属：各市条数 / 取值域 / 文案兜底有没有漏跑。

    返回 dict 供 main 打印。断言只放在「结构性」项上（见 main）。
    """
    stat = {}
    cnt = {}
    hit_any = []
    for x in rows:
        tg = city_tags(x)
        if tg:
            hit_any.append((x, tg))
            for c in tg:
                cnt[c] = cnt.get(c, 0) + 1
    stat["counts"] = cnt
    stat["hit_any"] = len(hit_any)
    stat["none"] = len(rows) - len(hit_any)
    stat["multi"] = [(x, tg) for x, tg in hit_any if len(tg) > 1]

    # ★ 取值域：构建期写进行数据的每个地市名，页面下拉里必须**选得到**。
    #   选不到的取值在页面上等于不存在（下拉里没有那一项），点不到、也筛不出 ——
    #   而数据里明明有 ⇒ 一批条目永远看不到，且没有任何提示。
    #   页面下拉 = CITY_ORDER ∪ CITY_EXTRA（由构建脚本注入，见 buildCtOptions）。
    bad = sorted({c for _x, tg in hit_any for c in tg} - set(T.CITY_ALL))
    stat["bad_names"] = bad

    # ★ 文案兜底有没有被漏跑（只对有文案兜底的网：移动 / 电信）：
    #   构建期的规则是「上游字段没给出地市时才查文案」⇒ 一条没有 cty 的行，
    #   从文案里也应当认不出地市。认得出而 cty 为空 = rows_of 里那一步漏跑了
    #   （症状是这批条目一律落进「全省通用」，即使用户选具体地市也看不到它们）。
    miss = [x for x in rows
            if not x.get("cty") and T.text_cities(x)] if (net_code or "") in T.CITY_TEXT else []
    stat["fallback_miss"] = miss
    return stat


def check_sync(raw, rows):
    """规则是否「只有一份权威」（防止页面与判据各留一份副本后各自漂）。

    ★ 这里**不再**做「本脚本的常量 vs 模板的常量」那种字符串比对：那套比对的前提是
      「两边各留一份副本」，而 2026-09-23 的教训正是——两份副本漂了、比对本身没覆盖到
      漂的那一项，于是照样报 OK（详见 city_tags() 的注释）。
      现在地市判据只有构建期一份，页面与对账脚本都读结果。
      所以本函数改查**「那份唯一的权威有没有被完整地送到页面」**：
        · 模板里有没有两个注入占位符（丢了 ⇒ 页面 CITY_ORDER 是 undefined ⇒ 下拉空）；
        · 页面里注入进来的清单是否与构建脚本逐字一致；
        · 构建脚本能产出的每个地市名，是否都在可选项里（否则那个值永远选不到）；
        · 页面的 cityTags() 是不是仍然「只读 d.cty」（判据结构的锚点）。
    """
    tpl = open(TPL, encoding="utf-8").read()
    errs = []
    m1 = re.search(r"const BW_BADVAL=/(.+?)/;", tpl)
    m2 = re.search(r"const BW_NOTLINE=/(.+?)/;", tpl)
    if not m1 or not m2:
        return ["模板里找不到 BW_BADVAL / BW_NOTLINE 常量"]
    if m1.group(1) != BW_BADVAL:
        errs.append("BW_BADVAL 与模板不一致：模板=%r 脚本=%r" % (m1.group(1), BW_BADVAL))
    if m2.group(1) != BW_NOTLINE:
        errs.append("BW_NOTLINE 与模板不一致：模板=%r 脚本=%r" % (m2.group(1), BW_NOTLINE))

    for ph in ("__CITY_ORDER__", "__CITY_EXTRA__"):
        if ph not in tpl:
            errs.append("模板里找不到占位符 %s —— 地市下拉会变成空的"
                        "（CITY_ORDER 为 undefined，页面直接 ReferenceError）" % ph)

    # 页面里**注入后**的清单（读的是生成物 docs/index.html，不是模板 —— 模板里是占位符）
    m3 = re.search(r"const CITY_ORDER=(\[.*?\]);", raw)
    m4 = re.search(r"const CITY_EXTRA=(\[.*?\]);", raw)
    for name, mo, want in (("CITY_ORDER", m3, list(T.CITY_ORDER)),
                           ("CITY_EXTRA", m4, list(T.CITY_EXTRA))):
        if not mo:
            errs.append("页面里找不到注入后的 const %s=（构建脚本漏填占位符？）" % name)
            continue
        try:
            got = json.loads(mo.group(1))
        except ValueError as e:
            errs.append("页面里 %s 不是合法 JSON：%s" % (name, e))
            continue
        if got != want:
            errs.append("%s 与构建脚本不一致：页面=%r 脚本=%r" % (name, got, want))

    # 构建脚本可能产出的每个地市名都必须能选到（文案兜底那几个也不例外）。
    missing = set(T.TEXT_CITY_LS) | {n for n, _p in T.TEXT_CITY_ALIAS} | set(T.CITY_ORDER)
    missing -= set(T.CITY_ALL)
    if missing:
        errs.append("这些地市名构建期可能产出、但下拉里没有（永远选不到）：%s"
                    % "、".join(sorted(missing)))

    # ★ 判据结构：页面只能「读」地市，不能自己再算一遍。
    if "function cityTags(d){return d.cty||[]}" not in tpl.replace("\n", ""):
        errs.append("模板的 cityTags() 不再是「只读 d.cty」—— 地市判据又出现了第二份副本，"
                    "本脚本查的那些「结果性质」也不再覆盖它，请先想清楚再改")
    return errs


def main():
    quiet = "--quiet" in sys.argv
    net = NET_LIVE
    if "--net" in sys.argv:
        i = sys.argv.index("--net")
        net = (sys.argv[i + 1] if i + 1 < len(sys.argv) else "") or NET_LIVE
    raw, rows = load_rows(net)
    print("体检网别 %s（数据基线 %s · %d 条 · 页面 %.2f MB）"
          % (net, NET_BASE or (re.search(r"数据基线 ([\d-]+)", raw) or [None, "?"])[1],
             len(rows), len(raw.encode("utf-8")) / 1048576))

    def rep(title, items, expect=0):
        ok = len(items) == expect
        print("  %-34s %s" % (title, ("OK " if ok else "!! ") + str(len(items))))
        if not ok:
            problems.append(title)
        return ok

    print("\n[0] 页面元信息")
    raw_base = NET_BASE or (re.search(r"数据基线 ([\d-]+)", raw) or [None, ""])[1]
    base_ok = False
    mb = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw_base or "")
    if mb:
        try:
            date(*(int(g) for g in mb.groups()))
            base_ok = True
        except ValueError:
            base_ok = False
    print("      数据基线 %r（页面相对天数与上架/下线时间筛选的基准）" % raw_base)
    rep("数据基线不可解析（会让时间筛选静默失效）", range(0 if base_ok else 1), 0)

    print("\n[1] 字段格式")
    out, units, extra = check_fields(rows)
    for name, n, exp in out:
        rep(name, range(n), exp)
    print("      流量单位: %s" % (", ".join(units) or "（无）"))

    print("\n[2] 宽带判定（复现 template.html 的 bwInfo）")
    f, n_, holders, fp, risk, incons = check_bw(rows)
    print("      判为含宽带: %d 条（字段 %d + 名称兜底 %d）" % (len(f) + len(n_), len(f), len(n_)))
    print("      旧口径(bw 非空即含)=%d 条 → 现 %d 条（剔除 %d：占位符 + 提速/服务类）"
          % (len(holders), len(f) + len(n_), len(fp)))
    print("      其中名称写着提速/装机/调测… 的: %d 条（未计入）" % len(risk))
    if not quiet:
        for x in fp[:5]:
            print("        - %r  %s" % (x.get("bw"), (x.get("n") or "")[:42]))
    rep("规则自相矛盾（判为含却命中排除词）", incons, 0)

    print("\n[3] 流量换算 d+unit → g")
    rep("不一致", check_g(rows), 0)

    print("\n[4] 搜索覆盖（页面字段 vs 含 d/du 的全字段）")
    gaps = check_search(rows)
    for kw, (p, fl) in gaps.items():
        print("      搜 %-8s 页面 %-4d 全字段 %-4d  ← 缺口 %d" % (kw, p, fl, fl - p))
    rep("存在搜不到的字段缺口", gaps, 0)

    print("\n[5] 城市归属（读构建期写好的 d.cty —— 页面/本脚本都不再自己算）")
    st = check_city(rows, net)
    allc = list(T.CITY_ORDER) + [c for c in T.CITY_EXTRA if st["counts"].get(c, 0)]
    zero = [c for c in T.CITY_ORDER if st["counts"].get(c, 0) == 0]
    for c in allc:
        print("      %-14s %4d" % (c, st["counts"].get(c, 0)))
    print("      命中任一城市 %d 条 · 全省通用（构建期一条地市都没判出来）%d 条"
          % (st["hit_any"], st["none"]))
    if st["multi"]:
        print("      多市条目 %d 条：%s"
              % (len(st["multi"]),
                 "；".join("%s%s" % ((x.get("n") or "")[:20], tg) for x, tg in st["multi"][:4])))
    # ★ 取值域：写进行数据的每个地市名都必须能在页面下拉里选到。
    #   选不到 ⇒ 这批条目永远看不到（下拉里没有那一项），而数据里明明有，无任何提示。
    rep("地市取值不在页面下拉里（写进去也选不到）", st["bad_names"], 0)
    # ★ 文案兜底有没有漏跑（只对移动/电信：见 tariff_monitor.CITY_TEXT）。
    rep("文案能认出地市、却没有 cty（rows_of 漏跑兜底）", st["fallback_miss"], 0)
    # ★ 零命中只在**默认网（移动）**算失败：那边的 12 个地市都有条目级地市码，
    #   某个市恒 0 ⇒ 是城市名写错或 cty 映射断了，必须拦。
    #   其它网地市覆盖本来就稀疏 —— 实测电信 11 个市有码、廊坊没有；联通 139/8042 条
    #   才带城市归属。那是上游/采集就没给，不是我们的 bug ⇒ 只提示不判失败（否则每天误报）。
    if net == NET_LIVE:
        rep("城市表存在零命中的城市（疑拼写错误）", zero, 0)
    elif zero and st["hit_any"]:
        print("      ⚠️ %s 网零命中城市：%s —— 覆盖率低于默认网属正常"
              "（上游/采集没给这些市的地市归属），**不判失败**" % (net, "、".join(zero)))
    if not st["hit_any"]:
        print("      ⚠️ 本网一条地市归属都没有 ⇒ 页面不出地市这一层（数据驱动，非错误）")

    print("\n[6] 规则同步（唯一权威 ↔ 页面）")
    errs = check_sync(raw, rows)
    if errs:
        for e in errs:
            print("  !! %s" % e)
        problems.append("规则未同步")
    else:
        print("  OK  BW_BADVAL / BW_NOTLINE 与模板一致；地市清单注入完整且与构建脚本逐字一致；"
              "cityTags() 仍为「只读 d.cty」")

    print()
    if problems:
        print("结论：发现 %d 处问题 → %s" % (len(problems), "；".join(problems)))
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
