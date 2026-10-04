# -*- coding: utf-8 -*-
"""资费查询页 · 筛选口径对账用例生成器（conformance cases）。

为什么是「独立重写」而不是调页面代码：拿页面验页面等于自己验自己，发现不了系统性算错。
本脚本照**语义**重写一套筛选逻辑，产出每个用例的期望条数，供浏览器侧逐条比对。
只复用 audit_data.py 里已做同步校验的 bw_info()（同一套判据的既有复现）。

用法：
    python conformance.py                       # 生成 cases.json（默认临时目录）
    python conformance.py out.json              # 指定输出路径
    python conformance.py out.json --net telecom   # 换一网生成（默认 move）

🔴 生成完**必须真的跑一遍消费侧**，否则这份文件毫无意义（历史教训）：
   本脚本从诞生起就没有消费方 —— 文档里曾写「页面里 fetch('/cases.json') 即可逐条比对」，
   而页面里**根本没有那段代码**。oracle 每天照常生成，对不上也没人知道。
   现在消费方是 `probes/tools/run_conformance.py`（+ page_conformance_check.js），
   它把用例灌进真实 DOM 控件、调页面自己的 apply()、读页面自己的 view.length：
        python probes/tools/run_conformance.py --net <网>
   ⚠️ 别在 template.html 里加 fetch 钩子 —— 生产页面不该为自检背调试代码，
      还会把用例文件暴露给访问者。

⚠️ 覆盖范围（**不是四网全量**，别被文件名误导）：
   · 默认覆盖 移动 / 电信 / 联通 / 广电 四网，条件见下面的 NETS_OK。
   ⚠️ 2026-10-03：地市筛选维度整块下线（页面上的「地市分布」面板与「地市」下拉都撤了，
      见 template.html 的注释）⇒ 本脚本里所有 `ct:*` / `ct=_none` 用例**同时作废**。
      这里一并删掉，并把原先「城市 × 其它维度」的两两/多重组合改由**大类 cat** 承担
      （同样是「先选一类，再看它和月费/时效/宽带怎么交叠」），否则删掉 ct 会顺带
      丢掉三四维交互的覆盖。留着一批页面上不存在的用例，对账会**整体**偏红 ——
      而那正是本机制最怕的失效方式：判据一红，人就学会忽略它。
   ⚠️ 2026-10-03 同时把**广电**放进了 NETS_OK。它此前被排除的理由只有一条：
      「上游没有地市粒度 ⇒ 给它生成城市用例会与页面正面矛盾」。城市用例没了，
      这条理由也就不存在了 —— 实测 `--net cbn` 83/83 与 oracle 完全一致，
      于是原来**完全没有 oracle 覆盖**的那一网补上了（页面级断言覆盖不到
      「筛选语义算错」，只有这一层能）。要再加网，先按同样方式实测再放行。

只断言「结构性」不变量（用例可生成、基准日期可解析），具体条数随上游数据每天变。
"""
import io, json, math, os, re, sys, tempfile
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import audit_data as A
# 大类顺序只有一份权威（构建脚本，页面也是按它出下拉），这里不另抄 —— 抄一份就多一处
# 会漂的地方，而漂的表现是「oracle 生成了一条页面上根本没有的大类用例」，对账恒失败
# 且**指错方向**（看起来像页面坏了）。
import tariff_monitor as T

# 以 `-` 开头的参数不是路径（比如手滑敲了 `--help`）—— 照单全收会凭空生成一个
# 名为 `--help` 的文件：它会出现在 git status 里，而很难联想到是这条命令干的。
_arg = sys.argv[1] if len(sys.argv) > 1 else ""
OUT = _arg if _arg and not _arg.startswith("-") else os.path.join(tempfile.gettempdir(), "cases.json")

_NET = "move"
if "--net" in sys.argv:
    _i = sys.argv.index("--net")
    if _i + 1 < len(sys.argv):
        _NET = sys.argv[_i + 1]
NETS_OK = ("move", "telecom", "unicom", "cbn")
if _NET not in NETS_OK:
    sys.exit("--net 只支持 %s" % "/".join(NETS_OK))

# ---------- 与模板逐字对应的判据 ----------
# ⚠️ 必须与 template.html 的 apply() 里那份**逐字段一致**。少了 cat / chx 两项，
#    「宽带」「加装包」这类词就会「页面搜得到、oracle 说搜不到」—— 一个方向永远对不上、
#    而且看起来像页面错了的偏差（2026-09-24 发现：这两项是后来加进页面搜索的，
#    当时没同步到这里；因为 conformance 平时没人跑，偏差一直没人发现）。
SEARCH_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "cat", "chx", "x", "bw", "ex", "vp")
BW_SPEED = re.compile(r"提速|光网|组网|FTTR")


def num(v):
    try:
        f = float(str(v))
    except Exception:
        return None
    return None if math.isnan(f) else f


def to_date(s):
    """返回「天数序号」（date.toordinal，1 天 = 1）。"""
    s = str("" if s is None else s).strip()
    if not re.fullmatch(r"\d{8}", s):
        return None
    y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    if m < 1 or m > 12 or d < 1 or d > 31:
        return None
    try:
        return date(y, m, d).toordinal()
    except ValueError:
        return None


def bw_speed(d):
    return bool(BW_SPEED.search((d.get("n") or "") + " " + (d.get("x") or "")))


def build_oracle(rows, base):
    def match(d, c):
        # 大类（四网统一口径）与细分（上游原始分类）是**两级**，都要能对账：
        # 联通 2026-09-24 重做筛选后，「停售套餐」不再整体归成 cat=套餐，
        # 而是按二级栏目还原成真实分类 ⇒ 细分域里已经没有「套餐」这个取值。
        # 跨网通用的用例因此改用 cat（恒有四网统一的那几档），ty 用例只走本网实际取值。
        if c["cat"] and d.get("cat") != c["cat"]:
            return False
        if c["ty"] and d.get("ty") != c["ty"]:
            return False
        # ⚠️ 2026-10-03：这里原先有一段 `ct` 判据（地市：不限地市(pw) ＋ 该市专属）。
        #    地市筛选维度整块下线后，页面 match() 里已无这一段，oracle 必须同步删掉 ——
        #    留着的后果是**期望值比页面少**，几十条对账用例集体偏红（而页面是对的）。
        if c["pf"]:
            lo, hi = [float(x) for x in c["pf"].split(",")]
            f = num(d.get("f"))
            # 2026-10-04 页面档位改半开 (lo,hi]（列表审查 G1/F1：边界值归上一档，
            # 0 元独立档）。oracle 是复现，必须跟页面同一语义 —— 否则 pf:0,10
            # 这类用例集体偏红（f=0 与 f=10 的档位归属变了）。仅 0 元（lo=hi=0）
            # 仍是闭区间 [0,0]。
            if lo == hi:
                if f != lo:
                    return False
            elif f is None or f <= lo or f > hi:
                return False
        if c["on"]:
            dt = to_date(d.get("o"))
            if c["on"] == "none":
                if dt:
                    return False
            else:
                if dt is None:
                    return False
                ag = base - dt
                if ag < 0 or ag > float(c["on"]):
                    return False
        if c["off"]:
            dt = to_date(d.get("e"))
            if dt is None:
                return False
            # ★ |lf| <= N（2026-10-04 对齐页面判据）：页面 match() 的「N 天内下线」
            #   早就改成了绝对值 —— 在售页签匹配「即将下线」（lf>=0）、已下架页签
            #   匹配「刚下线」（lf<0）、「全部」页签两者都算（见 template.html
            #   match() 那段 🔴 注释）。oracle 还停在旧的 lf<0 拒绝 ⇒ off 类用例
            #   期望系统性偏小（实测 cbn off=90 期望 73 / 页面 87），此前一直被
            #   「页面缺控件」的加载竞态掩盖，竞态修复后现形。
            lf = dt - base
            if abs(lf) > float(c["off"]):
                return False
        if c["bw"] == "line" and not A.bw_info(d):
            return False
        if c["bw"] == "speed" and not bw_speed(d):
            return False
        if c["kw"]:
            s = " ".join(str(d.get(k) or "") for k in SEARCH_FIELDS).lower()
            if c["kw"].lower() not in s:
                return False
        return True

    return lambda d, c: match(d, c)


def main():
    raw, rows = A.load_rows(_NET)
    # 基线取本网自己的 base，而不是全页第一个「数据基线」（四网基线理论上可不同）
    date = A.NET_BASE or ((re.search(r"数据基线 ([\d-]+)", raw) or [None, ""])[1] or "")
    base = to_date(date.replace("-", ""))
    print("网 %s · 数据基线 %s → BASE=%s · %d 条" % (_NET, date, base, len(rows)))
    print("可对账网别 %s（run_checks.py 的 NETS_CONF 逐网调用本脚本）" % "、".join(NETS_OK))
    if base is None:
        sys.exit("!! 数据基线解析失败，无法建立基准")

    m = build_oracle(rows, base)
    ty_vals = sorted({d.get("ty") for d in rows if d.get("ty")})
    # 大类取值：只取**页面上真会出现**的那些 —— 页面 renderNet() 按
    # `CAT_ORDER.filter(c=>有条数)` 建下拉，所以这里也用同一口径取交集。
    # 取多了 ⇒ 灌值时报「#cat 无此取值」，整个用例被跳过（跳过 ≠ 通过）。
    present = {d.get("cat") for d in rows if d.get("cat")}
    cat_vals = [c for c in T.CAT_ORDER if c in present]
    print("   大类取值（CAT_ORDER ∩ 本网有条数）：%s" % "、".join(cat_vals))

    def C(**kw):
        c = dict(kw=kw.get("kw", ""), cat=kw.get("cat", ""), ty=kw.get("ty", ""),
                 pf=kw.get("pf", ""), on=kw.get("on", ""), off=kw.get("off", ""),
                 bw=kw.get("bw", ""))
        return c

    cases = []          # (标签, 条件)
    def add(tag, c):
        cases.append({"tag": tag, "q": c})

    # 1) 各筛选项的每个取值（单条件）
    add("kw:空", C())
    for k in ("移动", "宽带", "1000M", "2000M", "超套", "IPTV", "低消", "雄安",
              "校园", "权益", "5G", "zzz不存在", "MONTH", "month", "·", "（"):
        add("kw:" + k, C(kw=k))
    for t in ty_vals:
        add("ty:" + t, C(ty=t))
    for c in cat_vals:
        add("cat:" + c, C(cat=c))
    # 2026-10-04：档位值随页面 FEE_TIERS 更新（0,30/0,60 → 10,30/30,60，
    # 补 60,100）—— 用例灌的是真实 DOM，选项不存在就是「无此取值」被跳过。
    for p in ("0,0", "0,10", "10,30", "30,60", "60,100", "100,99999"):
        add("pf:" + p, C(pf=p))
    for o in ("7", "30", "90", "180", "365", "none"):
        add("on:" + o, C(on=o))
    for o in ("30", "60", "90"):
        add("off:" + o, C(off=o))
    for b in ("line", "speed"):
        add("bw:" + b, C(bw=b))

    # 2) 两两组合（覆盖跨维度交互）
    for t in ty_vals:
        add("ty=%s+bw=line" % t, C(ty=t, bw="line"))
        add("ty=%s+on=30" % t, C(ty=t, on="30"))
    # ★ 这里原先是「城市 × {宽带 / 搜索 / 下线时间}」那三组。地市维度下线后由**大类**
    #   顶上：形状完全同构（先选一个类目，再看它与宽带 / 搜索词 / 时效怎么交叠），
    #   而且 cat 是四网统一口径，比 ty 更适合跨网复用。
    for c in cat_vals:
        add("cat=%s+bw=line" % c, C(cat=c, bw="line"))
        add("cat=%s+kw=宽带" % c, C(cat=c, kw="宽带"))
        add("cat=%s+off=90" % c, C(cat=c, off="90"))
    for p in ("0,0", "0,10", "30,60"):
        add("pf=%s+on=90" % p, C(pf=p, on="90"))

    # 3) 三重及以上 —— ★ 一律用 cat（四网统一口径）而不是 ty（上游原始分类）：
    #   联通的细分域在口径修正后已无「套餐」这一档，写 ty=套餐 会让用例在页面上
    #   「无此取值」而被跳过 —— 跳过 ≠ 通过，对账会静默少覆盖几条。
    #   用 cat_vals 判一下存在性：本网没有「套餐」这一档时整条不加，而不是加一条
    #   注定被跳过的用例（那会让「用例数」这个数字失去意义）。
    if "套餐" in cat_vals:
        add("三:cat=套餐+pf=10,30+kw=校园", C(cat="套餐", pf="10,30", kw="校园"))
        add("三:cat=套餐+off=90+kw=宽带", C(cat="套餐", off="90", kw="宽带"))
        add("四:cat=套餐+pf=30,60+on=365+kw=流量",
            C(cat="套餐", pf="30,60", on="365", kw="流量"))
        add("五:cat=套餐+bw=line+pf=30,60+on=365",
            C(cat="套餐", bw="line", pf="30,60", on="365"))
    add("四:bw=speed+pf=10,30+on=180", C(bw="speed", pf="10,30", on="180"))
    # 反向：必然为 0 的组合
    add("零:bw=speed+kw=zzz", C(bw="speed", kw="zzz"))
    add("零:oof off=30+on=7+kw=宽带", C(off="30", on="7", kw="宽带"))

    got = []
    for c in cases:
        n = sum(1 for d in rows if m(d, c["q"]))
        got.append({"tag": c["tag"], "q": c["q"], "expect": n})

    io.open(OUT, "w", encoding="utf-8").write(json.dumps(got, ensure_ascii=False, indent=1))
    print("已生成 %d 个用例 → %s" % (len(got), OUT))
    print("零结果用例：%d 个（含预期为 0 的边界用例）" % sum(1 for g in got if g["expect"] == 0))
    for g in got[:6]:
        print("   %-22s %s" % (g["tag"], g["expect"]))


if __name__ == "__main__":
    main()
