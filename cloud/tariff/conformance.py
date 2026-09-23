# -*- coding: utf-8 -*-
"""资费查询页 · 筛选口径对账用例生成器（conformance cases）。

为什么是「独立重写」而不是调页面代码：拿页面验页面等于自己验自己，发现不了系统性算错。
本脚本照**语义**重写一套筛选逻辑，产出每个用例的期望条数，供浏览器侧逐条比对。
只复用 audit_data.py 里已做同步校验的 bw_info() / city_tags()（同一套判据的既有复现）。

用法：
    python conformance.py                # 生成 cases.json（默认写到系统临时目录）
    python conformance.py out.json       # 指定输出路径

拿到 cases.json 后，与页面产物一起放进一个只含这两份文件的目录、起本地服务，
     把产物拷成 index.html、cases.json 放同级，python -m http.server 8123 --bind 127.0.0.1
页面里 fetch('/cases.json') 即可逐条比对（file:// 下 fetch 会被 CORS 拦，故走同源 HTTP）。
⚠️ 务必 md5 校验拷过去的 index.html 与真产物字节一致，否则验的是另一个文件。

只断言「结构性」不变量（用例可生成、基准日期可解析），具体条数随上游数据每天变。

★★ 覆盖范围：**只有移动那一网**（经 A.load_rows()，即 NET_LIVE="move"）。
   四网改造后页面容器是 {move, unicom, telecom, cbn}，本脚本只对 move 生成用例 ——
   其余各网的筛选一条都没对账。这不是疏漏，而是判据不同：
     · 联通的「城市」维度**不存在**（数据与 cityId 无关，页面上地市选项已置灰），
       硬套移动那套 city_tags() oracle 会产出「邢台 N 条」这种与页面（全部 4806 条）
       正面矛盾的期望值 —— 一个必然失败、且指错方向的用例；
     · 广电同理：它只有「全国 / 河北省」两级**地区**（两份数据交集为 0，都采），
       条目里没有地市字段，页面按 allProvince 处理 ⇒ 城市用例同样不适用；
     · 其余各维（类型 / 月费 / 上下架 / 含宽带 / 关键词）三网同构，已在
       联通、广电接入时用浏览器逐项实测过（见 `probes/tools/page_walk_check.js` 的遍历结果）。
   故联通/广电侧验收 = 浏览器逐项实测 + CI 的逐网容器断言，**不是**本脚本。
"""
import io, json, math, os, re, sys, tempfile
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import audit_data as A

# 以 `-` 开头的参数不是路径（比如手滑敲了 `--help`）—— 照单全收会凭空生成一个
# 名为 `--help` 的文件：它会出现在 git status 里，而很难联想到是这条命令干的。
_arg = sys.argv[1] if len(sys.argv) > 1 else ""
OUT = _arg if _arg and not _arg.startswith("-") else os.path.join(tempfile.gettempdir(), "cases.json")

# ---------- 与模板逐字对应的判据 ----------
SEARCH_FIELDS = ("n", "t", "ap", "ch", "r", "ty", "x", "bw", "ex", "vp")
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
    raw, rows = A.load_rows()
    dm = re.search(r"数据基线 ([\d-]+)", raw)
    date = dm.group(1) if dm else ""
    base = to_date(date.replace("-", ""))
    print("数据基线 %s → BASE=%s · %d 条" % (date, base, len(rows)))
    print("⚠️  仅覆盖【移动】那一网（NET_LIVE=%r）—— 联通/广电没有地市维度"
          "（城市判据不适用），不在本脚本对账范围内" % A.NET_LIVE)
    if base is None:
        sys.exit("!! 数据基线解析失败，无法建立基准")

    m = build_oracle(rows, base)
    ty_vals = sorted({d.get("ty") for d in rows if d.get("ty")})
    cities = A.CITY_LS + list(A.CT_ALIAS)

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
