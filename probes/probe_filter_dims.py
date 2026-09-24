# -*- coding: utf-8 -*-
"""筛选维度口径探针：**页面侧**的三个维度到底有没有按新口径落盘。

与已有探针的分工（问的不是同一件事，别混）：
  · probe_coverage_axes / probe_unicom_axes —— 问**采集**：该采的是不是都去采了。
  · 本探针 —— 问**构建**：采回来的东西有没有被正确归类到筛选维度上。
    它失效时接口全 200、采集零失败，**唯一症状是页面上某一类永远筛不出来**。

三条判据（任一不成立即退出码 2）：
  1) 联通「停售套餐」必须按二级栏目还原成真实分类，不得整体归成 cat=套餐。
     判据：cat 落在 CAT_ORDER 之外（含「其他」）的条数为 0，且停售条目里
     cat != 套餐 的那部分必须占多数 —— 只写「其他=0」会被「全改成套餐」骗过去
     （那正是 2026-09-24 的旧错：3177 条被一律归成套餐）。
  2) 细分（ty）的取值域必须是**上游真实栏目**的子集，不得凭空造词。
     联通：ty ⊆ {一级栏目} ∪ {二级栏目}；其余三网没有二级栏目 ⇒ ty ⊆ {一级栏目}。
     域为空也算失败（那条细分维度等于废了，且没有任何报错）。
  3) 板块（sect）只在「本网上游 tariffAttr 真有两档」时落盘。
     落盘 ⇒ 该网至少出现 2 个 ATTR_CN 取值；不落盘 ⇒ 上游取值 < 2（或取不到）。
     只查「有没有 sect」会漏掉「只有一档也照样落盘」那种没信息量的维度。

用法:
    python probes/probe_filter_dims.py
"""
import glob
import gzip
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
CT = os.path.join(REPO, "cloud", "tariff")
sys.path.insert(0, CT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import tariff_monitor as T          # noqa: E402  常量与归一化函数：不另抄一份

SNAP = {
    "move":    ("hebei_tariff_*.json.gz",   "move"),
    "unicom":  ("unicom_tariff_*.json.gz",  "unicom"),
    "telecom": ("ct_tariff_*.json.gz",      "telecom"),
    "cbn":     ("cbn_tariff_*.json.gz",     "cbn"),
}


def newest(pat):
    """最新一份快照。🔴 禁止硬编码日期：CI 的快照名带当天日期，
       写死一天 ⇒ 第二天起这个探针**静默不检查**（glob 不到就当全过）。"""
    fs = sorted(glob.glob(os.path.join(CT, "snapshots", pat)))
    return fs[-1] if fs else None


def load_json(path):
    if path.endswith(".gz"):
        return json.load(gzip.open(path, "rt", encoding="utf-8"))
    return json.load(io.open(path, encoding="utf-8"))


def page_rows():
    """取已构建页面的四网 rows（本机预览 docs/index.html 或入库归档）。"""
    p = os.path.join(CT, "docs", "index.html")
    if not os.path.exists(p):
        p = os.path.join(CT, "page", "index.html.gz")
    if not os.path.exists(p):
        return None, "找不到页面产物（docs/index.html 与 page/index.html.gz 都没有）"
    s = gzip.open(p, "rt", encoding="utf-8").read() if p.endswith(".gz") \
        else io.open(p, encoding="utf-8").read()
    m = re.search(r"const\s+NETS\s*=", s)
    if not m:
        return None, "页面里找不到 `const NETS=`"
    nets, _ = json.JSONDecoder().raw_decode(s[m.end():])
    return {k: ((v or {}).get("rows") or []) for k, v in (nets or {}).items()}, os.path.basename(p)


def main():
    rows_by_net, page = page_rows()
    if rows_by_net is None:
        print("🔴 " + page)
        return 2
    if not any(rows_by_net.values()):
        print("🔴 页面里四网 rows 全空 —— 先跑一次构建")
        return 2

    bad = []
    print("页面产物：%s" % page)

    # ---- 判据 1：联通停售还原 ----
    f = newest(SNAP["unicom"][0])
    if not f:
        bad.append("找不到联通快照")
    else:
        o = load_json(f)
        up_l1 = set()
        up_l2 = set()
        stopped = 0
        for g in o.get("groups", []):
            l1 = str(g.get("type2Name") or "").strip()
            if l1:
                up_l1.add(l1)
            for e in g.get("entries", []):
                l2 = str(e.get("type3Name") or "").strip()
                if l2:
                    up_l2.add(l2)
                if l1 == T.STOPPED_L1:
                    stopped += 1
        rs = rows_by_net.get("unicom") or []
        other = sum(1 for r in rs if r.get("cat") not in T.CAT_ORDER)
        stopped_rows = [r for r in rs if r.get("st")]
        not_pkg = sum(1 for r in stopped_rows if r.get("cat") != "套餐")
        print("\n[1] 联通「停售套餐」还原（快照 %s）" % os.path.basename(f))
        print("    上游停售条目 %d · 页面停售行 %d" % (stopped, len(stopped_rows)))
        print("    cat 落在 CAT_ORDER 之外的：%d" % other)
        print("    停售行里 cat != 套餐 的：%d / %d" % (not_pkg, len(stopped_rows)))
        if other:
            bad.append("联通有 %d 条的 cat 不在 CAT_ORDER 内（归类没还原）" % other)
        if stopped_rows and not_pkg * 2 < len(stopped_rows):
            bad.append("联通停售行里 cat!=套餐 只占 %d/%d —— 疑似又被整体归成套餐了"
                       % (not_pkg, len(stopped_rows)))

    # ---- 判据 2：细分（ty）取值域 ----
    print("\n[2] 细分（ty）取值域 ⊆ 上游栏目")
    for net, (pat, _) in sorted(SNAP.items()):
        rs = rows_by_net.get(net) or []
        if not rs:
            continue
        tys = sorted({str(r.get("ty") or "").strip() for r in rs if r.get("ty")})
        f = newest(pat)
        allow = set()
        if f:
            o = load_json(f)
            for g in o.get("groups", []):
                l1 = str(g.get("type2Name") or "").strip()
                if l1:
                    allow.add(l1)
                allow.add(str(T.ZFLX.get(str(g.get("type2")), "") or "").strip())
                if net == "unicom":
                    for e in g.get("entries", []):
                        l2 = str(e.get("type3Name") or "").strip()
                        if l2:
                            allow.add(l2)
        allow.discard("")
        alien = [t for t in tys if t not in allow]
        print("    %-8s 细分 %2d 档 · 上游栏目 %2d 个 · 越界 %d"
              % (net, len(tys), len(allow), len(alien)))
        if alien:
            print("       越界取值：%s" % "、".join(alien[:8]))
        if not tys:
            bad.append("%s 细分域为空 —— 那条维度等于废了，且没有任何报错" % net)
        elif not allow:
            bad.append("%s 取不到上游栏目集合，无法核对（快照缺失？）" % net)
        elif alien:
            bad.append("%s 细分域出现上游没有的取值：%s" % (net, "、".join(alien[:5])))

    # ---- 判据 3：板块（sect）显隐 ----
    print("\n[3] 板块（sect）只在本网真有两档时落盘")
    for net, (pat, _) in sorted(SNAP.items()):
        rs = rows_by_net.get(net) or []
        if not rs:
            continue
        secs = sorted({str(r.get("sect") or "").strip() for r in rs if r.get("sect")})
        f = newest(pat)
        attrs = set()
        if f:
            o = load_json(f)
            attrs = {str(g.get("tariffAttr") or "").strip() for g in o.get("groups", [])}
        upstream2 = len(attrs & set(T.ATTR_CN)) >= 2
        print("    %-8s 页面 %d 档 %s · 上游 tariffAttr 命中 ATTR_CN 的 %d 个"
              % (net, len(secs), secs or "（无）", len(attrs & set(T.ATTR_CN))))
        if secs and len(secs) < 2:
            bad.append("%s 只有 1 个板块档却落了盘 —— 这一格没有信息量，不该出现" % net)
        if secs and not upstream2:
            bad.append("%s 落了 sect 但上游 tariffAttr 不足两档 —— 判据与构建期不一致" % net)
        if not secs and upstream2:
            bad.append("%s 上游有两档板块却没落 sect —— 页面上这一格会整个消失" % net)
        for s in secs:
            if s not in set(T.ATTR_CN.values()):
                bad.append("%s 出现了 ATTR_CN 之外的板块取值：%s" % (net, s))

    print()
    if bad:
        for b in bad:
            print("🔴 " + b)
        print("\n筛选维度口径：失败（%d 项）" % len(bad))
        return 2
    print("✅ 筛选维度口径：三项判据全部成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
