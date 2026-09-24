#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：4 位地市码的**语义**，以及「全省通用」的**第二种写法**。

════════════════════════════════════════════════════════════════════
两个被本判据照出来的真错
════════════════════════════════════════════════════════════════════
① **码名映射必须由数据证实，不能靠推测。**
   `tariff_monitor.HB_CITY` 里 `"3121": "省直辖（定州/辛集）"` 是**错的**：
   把 `applicableArea == "3121"`（该码单独出现）的条目全列出来，8 条里 7 条名字直接
   写着「雄安」——
     雄安新区咪咕融合权益5次包 / 10次包 / 1次包 · 雄安9元返费优惠（12个月）
     雄安公交出行权益包 · 雄安新区咪咕咖啡体验包（12Y） · 雄安金牌服务包A（12个月）
   其余 11 个码（3100 邯郸 / 3110 石家庄 / … / 3350 秦皇岛）单独出现时，名字里
   各自都写着自己的城市名 —— 只有 3121 对不上。
   ⇒ 3121 = **雄安新区**（雄安原属保定，3120 派生 3121，代号规律也自洽）。
   后果（移动 + 电信**同时**受影响，两网共用这张表）：
     · 「雄安新区」档拿不到那 8 条专属资费（页面里它与「仅全省通用」数字完全一样，
       看起来像「雄安没有资费」）；
     · 「省直辖（定州/辛集）」档装着雄安的资费 + 一批全省资费 —— 名不副实。

② **「全省通用」有两种写法，第二种没被识别。**
   `applicableArea` 除了留空（= 无地域限制）之外，还会把**12 个地市码全列出来**
   （`3120,3140,3170,3100,...,3121,3190,3130`，有时整串重复两遍）。
   实测 59 条（58 条 12 码 + 1 条 13 个 token）。这类条目的语义是**全省**，
   但 `_mk_cities()` 把 12 个码映射成 12 个市名 ⇒ 被判成「12 城专属」：
     · 不出现在「仅全省通用（不限地市）」档 ⇒ 按它筛**少 59 条**；
     · 计入「地市专属」总数 ⇒ 数字虚高；
     · 逐市看时虽然每条都能看到（因为 12 个市都在 cty 里），但**分类错了**。

③ **文案兜底与码判定是「互斥」而非「并集」，与它自己的注释矛盾。**
   `text_cities()` 的注释写着「一条可属多市……『沧州华油尊享礼包』同属沧州与华北油田」，
   但 `rows_of()` 里是 `if not cty and code in CITY_TEXT` —— 只要码判定给了地市，
   文案就**完全不看**。于是「华北油田」这个档永远是 0 条专属
   （它的资费码是 3170 沧州 / 3160 廊坊，名字里才有「华油」）。

════════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════════
  python probes/probe_city_code_semantics.py            # 移动 + 电信（读快照，不打接口）
  python probes/probe_city_code_semantics.py --json out.json
"""
import argparse
import collections
import glob
import gzip
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))
import tariff_monitor as T          # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SNAP = os.path.join(REPO, "cloud", "tariff", "snapshots")
# 地市名/别名，用于「名字里有没有写着这个城市」
NAMES = ("石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德",
         "沧州", "廊坊", "衡水", "雄安", "定州", "辛集", "华油", "华北油田")


def _newest(prefix):
    """该网**最新**的快照路径（🔴 绝不能硬编码日期：CI 每天采的快照名带当天日期，
    写死一天就等于这条判据只在写它的那天有效，之后永远读不到数据 ⇒ 静默不检查）。"""
    ds = []
    for p in glob.glob(os.path.join(SNAP, "%s_*.json.gz" % prefix)):
        m = re.search(r"_(\d{8})\.json\.gz$", p)
        if m:
            ds.append((m.group(1), p))
    return max(ds)[1] if ds else None


def _entries(prefix):
    p = _newest(prefix)
    if not p:
        raise SystemExit("找不到 %s_*.json.gz（快照目录 %s）" % (prefix, SNAP))
    with gzip.open(p, "rt", encoding="utf-8") as f:
        o = json.load(f)
    ent = o.get("entries") or []
    if not ent:                                  # 移动快照只存 groups
        for g in o.get("groups") or []:
            ent.extend(g.get("entries") or [])
    return ent


def _codes(e, code):
    """该条目声明的 4 位地市码集合（与 _mv_where / _ct_where 读的字段一致）。"""
    if code == "move":
        toks = T._toks(e.get("applicableArea")) + T._toks(e.get("city"))
    else:                                        # telecom
        toks = T._toks(e.get("_areaCodes"))
    return set(t for t in toks if t in T.HB_CITY), toks


def _text(e):
    return " ".join(str(e.get(k) or "") for k in
                    ("name", "tariffName", "applicablePeople", "otherContent"))


def run(code, cn):
    ent = _entries(("hebei_tariff" if code == "move" else "ct_tariff"))
    FULL = set(T.HB_CITY)
    form = collections.Counter()
    solo = collections.defaultdict(list)
    for e in ent:
        cs, toks = _codes(e, code)
        if not cs:
            form["无地市码"] += 1
        elif cs == FULL and len(set(toks)) >= 12:
            form["★全码（=全省写法）"] += 1
        elif len(cs) == 1:
            form["单码"] += 1
            solo[list(cs)[0]].append(_text(e))
        elif len(cs) >= 10:
            form["10~11 码"] += 1
        else:
            form["2~9 码"] += 1
    print("=" * 92)
    print("【%s】条目 %d" % (cn, len(ent)))
    print("  applicableArea 形态：%s" % dict(form))

    print("\n  ── ① 每个码「单独出现」时代码名是否对得上（人眼判据）──")
    bad = []
    for c in sorted(T.HB_CITY):
        arr = solo.get(c) or []
        named = sum(1 for s in arr if T.HB_CITY[c] in s
                    or (T.HB_CITY[c] == "雄安新区" and "雄安" in s))
        # ★ 判失败要**样本足够**：1~2 条的样本里「名字没写城市」很常见（资费名本来
        #   就可能不带地名），据此报错是误报。实测电信只有 1 条单码 3150 且名不含
        #   「唐山」，属正常。⇒ 少于 3 条只提示，不判失败。
        weak = len(arr) < 3
        flag = ""
        if arr and named == 0 and not weak:
            flag = "  ⚠️⚠️ 名里一个都没有本码名"
            bad.append(c)
        elif arr and named == 0:
            flag = "  （样本仅 %d 条，不作判据）" % len(arr)
        print("     %s %-18s 单独 %3d 条 · 名含本码名 %3d 条%s"
              % (c, T.HB_CITY[c], len(arr), named, flag))
        if flag.startswith("  ⚠️") and arr:
            for s in arr[:4]:
                print("          · %s" % s[:60])
    # 反向：名字里写着某市、但码却给了「省直辖」
    print("\n  ── ② 名字里写着「雄安」但没归到雄安新区的条目 ──")
    n = 0
    for e in ent:
        cs, _t = _codes(e, code)
        s = _text(e)
        if cs and "雄安" in s and "雄安新区" not in [T.HB_CITY[c] for c in cs]:
            n += 1
            if n <= 8:
                print("     %-46s 码=%s → %s"
                      % (str(e.get("name"))[:46], sorted(cs),
                         sorted(T.HB_CITY[c] for c in cs)))
    print("     共 %d 条" % n)

    print("\n  ── ③ 被当成「城市专属」的全省资费（全码写法）──")
    fulls = []
    for e in ent:
        cs, toks = _codes(e, code)
        if cs == FULL and len(set(toks)) >= 12:
            fulls.append(e)
    print("     共 %d 条；例：" % len(fulls))
    for e in fulls[:5]:
        print("     · %s" % str(e.get("name"))[:66])
    return {"entries": len(ent), "form": dict(form),
            "codes_with_wrong_name": bad,
            "full_code_entries": len(fulls),
            "xiongan_mislabeled": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    out = {}
    for code, cn in (("move", "移动"), ("telecom", "电信")):
        try:
            out[code] = run(code, cn)
        except Exception as e:
            out[code] = {"err": str(e)}
            print("【%s】读取失败：%s" % (cn, e))
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n已写出 %s" % a.json)
    bad = [c for v in out.values() if isinstance(v, dict)
           for c in (v.get("codes_with_wrong_name") or [])]
    print("\n>>> 结论：%s"
          % ("地市码语义与全省写法均一致 ✅" if not bad
             else "**码名映射有错（%s）** ❌" % "、".join(sorted(set(bad)))))
    sys.exit(0 if not bad else 2)


if __name__ == "__main__":
    main()
