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
- 城市：数据里**没有城市字段**，只能从名称 / 套餐名 / 目标客户 / 权益文本里认字面地名。
  这类「文本启发式」最怕两件事：把别的词当下辖市（假阳性），
  以及漏掉只在某一个字段里写了地名的条目（假阴性）。

本脚本把页面的判据在 Python 侧复现一遍，把「多算 / 少算」直接算成具体条数；
并额外校验 ``template.html`` 里的规则与这里是否**已经不同步**（改了一边忘了另一边是最大隐患）。

用法:
    python audit_data.py            # 体检并断言，有异常时退出码 1
    python audit_data.py --quiet    # 只输出结论行
数据来源：``docs/index.html`` 里那段 ``const NETS``（四网容器）里**移动**那一网的 rows
          （由 --render-only 或巡检生成）；``NET_LIVE`` 与 tariff_monitor.py 保持一致。
          其余三家接入后若要体检，把 net 参数化即可 —— 但别直接套用本脚本的判据：
          各网的字段名与城市口径未必与移动相同。

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

# ⚠️ 下面几条规则必须与 template.html 里 bwInfo() / cityTags() 的常量保持一致，
#    脚本末尾的 check_sync() 会从模板里读出来比对，不同步直接报错。
# ⚠️ 这两条必须与 template.html 里**逐字相同**（下面有自检），因为它们是「空值」的
#    判定表 —— 各网对「不适用」的写法不一样（广电写 '/'），而判定是跨网共用的。
BW_BADVAL = r"^(0|[-—－\/\\]|无|没有|否)$"
BW_NOTLINE = r"提速|电视|IPTV|检修|装机|调测|加速|绿色上网|调优|扩容|移机"
CITY_LS = ["石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"]
CT_ALIAS = {"雄安新区": r"雄安", "华北油田": r"华北油田|华油"}
# 河北 12 地市码 → 中文名。**必须与 template.html 的 HB_CITY_LB 完全一致**
# （页面拿它把条目级地市码 d.cty 翻成名字；两处漂移 ⇒ 对账用例全部对不上）。
HB_CITY_LB = {"3100": "邯郸", "3110": "石家庄", "3120": "保定",
              "3121": "省直辖（定州/辛集）", "3130": "张家口", "3140": "承德",
              "3150": "唐山", "3160": "廊坊", "3170": "沧州", "3180": "衡水",
              "3190": "邢台", "3350": "秦皇岛"}
# 本网数据源是否「数据本身不分城市」（联通/广电）。由 load_rows() 填充 ——
# 页面 cityTags() 的**第一步**就是判它，本脚本不跟着判的话，那两网的用例会全错。
ALL_PROVINCE = False
# 城市判定实际扫描的字段（顺序无关）
CITY_FIELDS = ("n", "t", "ap", "x")
# 页面搜索实际覆盖的字段（用于比对是否漏字段）
SEARCH_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "x", "bw", "ex", "vp")
ALL_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "x", "bw", "d", "du", "ex", "vp")

BADVAL_RE = re.compile(BW_BADVAL)
NOTLINE_RE = re.compile(BW_NOTLINE)

problems = []

# 本脚本体检的是**基准网（移动）**那一份数据。
# ⚠️ 别把它当成「tariff_monitor.NET_LIVE 的副本」——那边是**已接入网的元组**
#    （现在等于 ("move","unicom","cbn")），这里只要一个能取到 rows 的网名。
#    两边同名不同型，是接第二网时就留下的；改名会牵动 conformance.py 的文案，先留着但说清楚。
NET_LIVE = "move"


def load_rows():
    """取「移动」那一网的 rows。

    四网改造后页面里的容器是 ``const NETS={move:{...},unicom:{...},...}``。
    本脚本体检的是**数据**，而体检口径（城市归属、宽带判定）只对移动那网成立，
    所以只取 NETS[NET_LIVE].rows。
    其余三家接入后若也要体检，把 net 参数化即可 —— 但注意各网的字段名与
    城市口径未必与移动相同，别直接拿本脚本的判据套上去。
    """
    if not os.path.exists(HTML):
        sys.exit("找不到 %s —— 先跑 python tariff_monitor.py --render-only" % HTML)
    raw = open(HTML, encoding="utf-8").read()
    m = re.search(r"const\s+NETS\s*=", raw)
    if not m:
        sys.exit("页面里找不到 `const NETS=`")
    nets, _ = json.JSONDecoder().raw_decode(raw[m.end():])
    live_net = (nets or {}).get(NET_LIVE) or {}
    rows = live_net.get("rows") or []
    if not rows:
        sys.exit("NETS[%r].rows 为空 —— 移动那网的数据没灌进去？" % NET_LIVE)
    # 顺带把「本网是否不分城市」存到全局：city_tags() 的判据第一步就要它。
    # 不在这里取，就得让每个调用方都多传一个参数 —— 那样 conformance.py 也跟着改，
    # 而它俩迟早漂移（两个脚本各自传参，漏一个就是静默算错）。
    global ALL_PROVINCE
    ALL_PROVINCE = bool(live_net.get("allProvince"))
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


def city_tags(x, all_province=None):
    """复现 template.html 的 cityTags()：返回城市列表（可多值）。

    🔴 2026-09-23 修正：页面的判据已升级为「**上游地市码 ``d.cty`` 优先**，文本只兜底」，
       而本脚本原先**只复现了文本那一段** —— 结果 conformance 的 37 个地市用例
       **恒失败**（石家庄：期望 57 / 页面 141，差值是 cty 字段带来的，页面是对的）。

       这是「独立复现」类脚本的固有风险：页面判据一升级，这里不跟着改，
       对账就从「对得上」滑成「永远差一截」；又因为平时没人跑，
       看上去只是噪音，于是这个偏差安静地存在了很久。
       ⚠️ 改 template.html 的 cityTags() 时**必须同步这里**。
    """
    ap = ALL_PROVINCE if all_province is None else all_province
    if ap:
        return []                      # 数据源自己声明「不分城市」⇒ 一律全省通用
    cs = x.get("cty") or []
    if cs:                             # 上游声明的适用范围，以它为准
        return [HB_CITY_LB[c] for c in cs if c in HB_CITY_LB]
    s = " ".join(str(x.get(k) or "") for k in CITY_FIELDS)
    out = [c for c in CITY_LS if c in s]
    for k, pat in CT_ALIAS.items():
        if re.search(pat, s):
            out.append(k)
    return out


def check_fields(rows):
    """字段级体检：空值、类型、格式。"""
    out = []
    for k in ("o", "e"):
        bad = []
        for x in rows:
            s = str(x.get(k) or "").strip()
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
        out.append(("%s 日期格式（8 位数字 + 真实日历日）" % k, len(bad), 0))
    for k, name in (("f", "月费"), ("c", "通话")):
        bad = [x for x in rows if str(x.get(k) or "").strip()
               and not re.fullmatch(r"[0-9.]+", str(x.get(k)).strip())]
        out.append(("%s 非纯数字" % name, len(bad), 0))
    units = sorted({str(x.get("du") or "").strip().upper() for x in rows} - {""})
    extra = [u for u in units if not u.startswith(("GB", "MB", "TB"))]
    out.append(("流量单位是否都被 gb() 覆盖", len(extra), 0))
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


def check_city(rows):
    """城市判定：各市条数 / 并集 / 多市 / 字段来源 / 假阳性排查。

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

    # 字段来源组合：N=n T=套餐名 A=目标客户 X=权益
    combo = {}
    xonly = []
    for x, _ in hit_any:
        # 有地市码的条目是**上游直接声明**的适用范围，不做文本归因
        #（cty 不在任何文本字段里，逐字段判会得到全 "-" 的假象）。
        if x.get("cty"):
            combo["cty码"] = combo.get("cty码", 0) + 1
            continue
        # 逐字段判：该字段单独出现时能否命中城市（N=n T=套餐名 A=目标客户 X=权益）
        f = {k: city_tags({k: x.get(k)}) for k in CITY_FIELDS}
        key = "".join(k.upper() if f[k] else "-" for k in CITY_FIELDS)
        combo[key] = combo.get(key, 0) + 1
        if f["x"] and not f["n"] and not f["t"] and not f["ap"]:
            xonly.append(x)
    stat["combo"] = combo
    stat["xonly"] = xonly

    # 假阳性排查：命中处前后各 6 字的上下文（每市去重后留若干条供人工过目）
    ctx = {}
    for x, tg in hit_any:
        for c in tg:
            pat = CT_ALIAS.get(c, c)
            for k in CITY_FIELDS:
                fv = x.get(k) or ""
                for mo in re.finditer(pat, fv):
                    s = fv[max(0, mo.start() - 6):mo.end() + 6]
                    ctx.setdefault(c, [])
                    if s not in ctx[c]:
                        ctx[c].append("%s|…%s…" % (k, s))
    stat["ctx"] = ctx

    # 城市表是否漏掉河北其他行政区域
    others = {}
    for w in ("定州", "辛集", "雄安", "华油", "华北油田"):
        others[w] = sum(1 for x in rows
                        if w in " ".join(str(x.get(k) or "") for k in CITY_FIELDS))
    stat["others"] = others
    return stat


def check_sync():
    """模板里的规则常量是否与本脚本一致（防止只改一边）。"""
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

    m3 = re.search(r"const CITY_LS=\[(.*?)\];", tpl)
    if not m3:
        errs.append("模板里找不到 CITY_LS 常量")
    else:
        tpl_ls = re.findall(r'"([^"]+)"', m3.group(1))
        if tpl_ls != CITY_LS:
            errs.append("CITY_LS 与模板不一致：模板=%r 脚本=%r" % (tpl_ls, CITY_LS))

    m4 = re.search(r"const CT_ALIAS=\{(.*?)\};", tpl)
    if not m4:
        errs.append("模板里找不到 CT_ALIAS 常量")
    else:
        pairs = re.findall(r'"([^"]+)":/([^/]+)/', m4.group(1))
        tpl_alias = {k: v for k, v in pairs}
        if tpl_alias != CT_ALIAS:
            errs.append("CT_ALIAS 与模板不一致：模板=%r 脚本=%r" % (tpl_alias, CT_ALIAS))

    # ★ 地市码表：页面用它把 d.cty 翻成城市名，本脚本用它复现判据。
    #   2026-09-23 补上这一项 —— 那次 drift 的正是这张表（页面已改用 cty 优先，
    #   而「规则同步」只比 CITY_LS/CT_ALIAS 两个**文本**常量，照样报 OK）。
    #   只比常量不比**判据结构**，等于给一张过期的地图盖了个合格的章。
    m5 = re.search(r"const HB_CITY_LB=(\{.*?\});", tpl, re.S)
    if not m5:
        errs.append("模板里找不到 HB_CITY_LB 常量（地市码表）")
    else:
        pairs5 = re.findall(r'"(\d{4})":"([^"]+)"', m5.group(1))
        tpl_lb = {k: v for k, v in pairs5}
        if tpl_lb != HB_CITY_LB:
            errs.append("HB_CITY_LB 与模板不一致：模板=%r 脚本=%r" % (tpl_lb, HB_CITY_LB))

    # ★ 判据结构：页面是不是仍然是「cty 优先、文本兜底」？
    #   只比常量的漏洞就在这里 —— 哪天页面又换判据（比如改回纯文本，或加第三层），
    #   四个常量全都一致，对账却悄悄失效。用一段**特征代码**把结构钉住。
    if "d.cty||[]" not in tpl:
        errs.append("模板的 cityTags() 里找不到 `d.cty||[]` —— 地市判据结构已变，"
                    "本脚本的 city_tags() 复现必须同步改")
    return errs


def main():
    quiet = "--quiet" in sys.argv
    raw, rows = load_rows()
    print("数据基线 %s · %d 条 · 页面 %.2f MB"
          % ((re.search(r"数据基线 ([\d-]+)", raw) or [None, "?"])[1],
             len(rows), len(raw.encode("utf-8")) / 1048576))

    def rep(title, items, expect=0):
        ok = len(items) == expect
        print("  %-34s %s" % (title, ("OK " if ok else "!! ") + str(len(items))))
        if not ok:
            problems.append(title)
        return ok

    print("\n[0] 页面元信息")
    raw_base = (re.search(r"数据基线 ([\d-]+)", raw) or [None, ""])[1]
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

    print("\n[5] 城市判定（复现 template.html 的 cityTags）")
    st = check_city(rows)
    # 零命中分两类判：**12 市**有地市码，恒应为非 0（为 0 ⇒ 城市名写错或 cty 映射断了）；
    # 「雄安新区 / 华北油田」**没有地市码**，只能靠文本兜底 —— 判据改成「地市码优先」
    # 之后，这些条目一旦都带上了码，它们就会是 0。那是判据升级的正常结果，
    # 不是拼写错误 ⇒ 只提示，不判失败（否则升级判据当天这里会红，逼人回滚）。
    zero = [c for c in CITY_LS if st["counts"].get(c, 0) == 0]
    zero_alias = [c for c in CT_ALIAS if st["counts"].get(c, 0) == 0]
    for c in CITY_LS + list(CT_ALIAS):
        print("      %-6s %4d" % (c, st["counts"].get(c, 0)))
    print("      命中任一城市 %d 条 · 全省通用（四字段都没提地市）%d 条"
          % (st["hit_any"], st["none"]))
    print("      多市条目 %d 条：%s"
          % (len(st["multi"]),
             "；".join("%s%s" % ((x.get("n") or "")[:20], tg) for x, tg in st["multi"]) or "（无）"))
    combo_txt = "  ".join("%s=%d" % (k, v) for k, v in sorted(st["combo"].items(), key=lambda z: -z[1]))
    print("      命中来源（cty码 = 上游地市字段；N名称 T套餐名 A目标客户 X权益 = 文本兜底）: %s"
          % combo_txt)
    print("      　文案提示：「--AP-」= 地名只在目标客户里，只看名称会静默漏掉；"
          "「NT--」= 目标客户写的是通用话术，只读该字段同样会漏 ⇒ 必须取并集。")
    if st["xonly"]:
        print("      ⚠️ 权益独有命中 %d 条 —— template.html 注释里「权益贡献 0 条独有命中」已过期，请更新"
              % len(st["xonly"]))
        if not quiet:
            for x in st["xonly"][:3]:
                print("        - %s" % (x.get("n") or "")[:44])
    if not quiet:
        print("      上下文抽样（每市去重，人工过目有无把别的词当城市）:")
        for c in CITY_LS + list(CT_ALIAS):
            for s in st["ctx"].get(c, [])[:3]:
                print("        %-6s %s" % (c, s))
    print("      河北其他地名（0 = 城市表未漏）: %s"
          % ", ".join("%s %d" % (k, v) for k, v in st["others"].items()))
    rep("城市表存在零命中的城市（疑拼写错误）", zero, 0)
    if zero_alias:
        print("      ⚠️ 专属区域零命中：%s —— 判据改为「地市码优先」后属正常结果"
              "（这些区域没有地市码，只在条目没带码时才走文本兜底）" % "、".join(zero_alias))

    print("\n[6] 规则同步（脚本 ↔ template.html）")
    errs = check_sync()
    if errs:
        for e in errs:
            print("  !! %s" % e)
        problems.append("规则未同步")
    else:
        print("  OK  BW_BADVAL / BW_NOTLINE / CITY_LS / CT_ALIAS / HB_CITY_LB 与模板一致，"
              "且地市判据仍为「cty 优先 + 文本兜底」")

    print()
    if problems:
        print("结论：发现 %d 处问题 → %s" % (len(problems), "；".join(problems)))
        return 1
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
