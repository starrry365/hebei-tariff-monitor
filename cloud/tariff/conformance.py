# -*- coding: utf-8 -*-
"""资费查询页 · 筛选口径对账用例生成器（conformance cases）。

为什么是「独立重写」而不是调页面代码：拿页面验页面等于自己验自己，发现不了系统性算错。
本脚本照**语义**重写一套筛选逻辑，产出每个用例的期望条数，供浏览器侧逐条比对。
只复用 audit_data.py 里已做同步校验的 bw_info() / city_tags()（同一套判据的既有复现）。

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
   · **有条目级地市归属的网**（移动 / 电信 / 联通）→ 本脚本可对账，含地市维度的用例。
   · **广电**：上游只有「全国 / 河北省」两档、条目里没有地市 ⇒ `city_tags()` 恒空，
     硬套会给它生成「邢台 N 条」这种与页面正面矛盾的期望值（页面全部按「全省通用」算）。
     故**不为它**生成城市用例（其余各维同构，已用 `probes/tools/page_walk_check.js`
     的遍历逐项实测过）。
   ⚠️ 2026-09-24 修正：联通**已加入**可对账名单。此前它被归到「没有地市维度」那一类，
      依据是数据源自报的 allProvince —— 而那个声明是错的（实测 22 个栏目组合里 12 个
      随城市变化、12 个地市各有专属条目）。判据改成「数据里有没有 d.cty」之后，
      联通有条件可对账，而且**正需要**对账：它的地市归属来自采集侧的 12 城目录归属，
      是全链路里唯一一条「不是直接读上游字段」的判据。

只断言「结构性」不变量（用例可生成、基准日期可解析），具体条数随上游数据每天变。
"""
import io, json, math, os, re, sys, tempfile
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import audit_data as A
# 地市清单只有一份权威（构建脚本），这里不另抄 —— 抄一份就多一处会漂的地方，
# 而漂的表现是「oracle 生成了一条页面上根本不存在的用例」，对账恒失败且指错方向。
import tariff_monitor as T

# 以 `-` 开头的参数不是路径（比如手滑敲了 `--help`）—— 照单全收会凭空生成一个
# 名为 `--help` 的文件：它会出现在 git status 里，而很难联想到是这条命令干的。
_arg = sys.argv[1] if len(sys.argv) > 1 else ""
OUT = _arg if _arg and not _arg.startswith("-") else os.path.join(tempfile.gettempdir(), "cases.json")

# 有地市维度的网才能用本脚本对账（见文件头「覆盖范围」）。默认移动。
_NET = "move"
if "--net" in sys.argv:
    _i = sys.argv.index("--net")
    if _i + 1 < len(sys.argv):
        _NET = sys.argv[_i + 1]
NETS_OK = ("move", "telecom", "unicom")
if _NET not in NETS_OK:
    sys.exit("--net 只支持 %s（广电上游没有地市维度，拿它跑地市用例必然对不上）"
             % "/".join(NETS_OK))

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
        if c["ty"] and d.get("ty") != c["ty"]:
            return False
        if c["ct"]:
            tg = A.city_tags(d)
            if c["ct"] == "_none":
                if tg:
                    return False
            elif c["ct"] not in tg:
                return False
        if c["pf"]:
            lo, hi = [float(x) for x in c["pf"].split(",")]
            f = num(d.get("f"))
            if f is None or f < lo or f > hi:
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
            lf = dt - base
            if lf < 0 or lf > float(c["off"]):
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
    print("覆盖 %s（上游有条目级地市码）" % "、".join(NETS_OK))
    if base is None:
        sys.exit("!! 数据基线解析失败，无法建立基准")

    m = build_oracle(rows, base)
    ty_vals = sorted({d.get("ty") for d in rows if d.get("ty")})
    # ★ 只给「本网真能取到」的地市档生成用例。页面 renderDims 会把 **0 条**的档
    #   置灰，并在 apply 里加了保险丝（置灰档当没筛）—— 所以那类档在页面上
    #   根本筛不出东西。**照旧为它们生成用例，对账必然失败，且失败指向错误方向**
    #   （期望 0、实际 = 全部条数，看起来像「地市筛选整体失效」）。
    #   典型的就是「雄安新区 / 华北油田」：判据改成「地市码优先」后它们没有码，
    #   只在条目连码都没有时才走文本兜底，实测恒 0 条。
    ccount = {}
    for d in rows:
        tg = A.city_tags(d)
        if tg:
            for c in tg:
                ccount[c] = ccount.get(c, 0) + 1
    cities_all = list(T.CITY_ORDER) + list(T.CITY_EXTRA)
    cities = [c for c in cities_all if ccount.get(c)]
    skipped = [c for c in cities_all if not ccount.get(c)]
    if skipped:
        print("⏭️  跳过页面上会被置灰的地市档（本网 0 条）: %s" % "、".join(skipped))

    def C(**kw):
        c = dict(kw=kw.get("kw", ""), ty=kw.get("ty", ""), ct=kw.get("ct", ""),
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
    for c in cities:
        add("ct:" + c, C(ct=c))
    add("ct:_none", C(ct="_none"))
    for p in ("0,0", "0,10", "0,30", "0,60", "100,99999"):
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
    for c in cities:
        add("ct=%s+bw=line" % c, C(ct=c, bw="line"))
        add("ct=%s+kw=宽带" % c, C(ct=c, kw="宽带"))
        add("ct=%s+off=90" % c, C(ct=c, off="90"))
    for p in ("0,0", "0,10", "0,60"):
        add("pf=%s+ct=石家庄" % p, C(pf=p, ct="石家庄"))
        add("pf=%s+on=90" % p, C(pf=p, on="90"))

    # 3) 三重及以上
    add("三:ct=石家庄+pf=0,10+bw=line", C(ct="石家庄", pf="0,10", bw="line"))
    add("三:ct=_none+on=30+bw=line", C(ct="_none", on="30", bw="line"))
    add("三:ty=套餐+pf=0,30+kw=校园", C(ty="套餐", pf="0,30", kw="校园"))
    add("三:ct=邯郸+off=90+kw=宽带", C(ct="邯郸", off="90", kw="宽带"))
    add("四:ct=唐山+pf=0,60+on=365+kw=流量",
        C(ct="唐山", pf="0,60", on="365", kw="流量"))
    add("四:ct=_none+bw=speed+pf=0,30+on=180",
        C(ct="_none", bw="speed", pf="0,30", on="180"))
    add("五:ty=套餐+ct=保定+pf=0,60+on=365+bw=line",
        C(ty="套餐", ct="保定", pf="0,60", on="365", bw="line"))
    # 反向：必然为 0 的组合
    add("零:ct=衡水+bw=speed+kw=zzz", C(ct="衡水", bw="speed", kw="zzz"))
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
