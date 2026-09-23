# -*- coding: utf-8 -*-
"""把四网数据导出到桌面：xlsx（概览 + 四网分表 + 合计）+ 每网一份 CSV + 原始 JSON

★ 列口径**不是另写一套**，而是从生成的页面里取 ``const NETS=`` —— 页面「导出 CSV」
  用的就是这份数据，所以导出的表与页面上的表逐列同源，不会出现「表格里一个数、
  页面上另一个数」。CSV_COLS 的 26 列定义照抄 template.html，派生列的判据
  （每元流量 / 宽带判定 / 城市归属）复用 audit_data.py 里那份**与页面同步**的复刻。

用法：
    python probes/tools/export_desktop.py             # 默认桌面 河北四网资费专区-<数据基线日期>
    python probes/tools/export_desktop.py <输出目录>
"""
import csv
import gzip
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARIFF = os.path.join(BASE, "cloud", "tariff")
sys.path.insert(0, TARIFF)
sys.path.insert(0, os.path.join(TARIFF))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import audit_data as AD  # noqa: E402  复用与页面同步的 bw_info / city_tags
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

HTML = os.path.join(TARIFF, "docs", "index.html")
SNAP = os.path.join(TARIFF, "snapshots")
NET_CN = {"move": "移动", "unicom": "联通", "telecom": "电信", "cbn": "广电"}
NET_ORDER = ["move", "unicom", "telecom", "cbn"]
SC_LB = {"hb": "河北", "cn": "全国"}
# 与 template.html 的 CSV_COLS 逐列对应（26 列）
COLS = ["地域", "状态", "归属", "月费(元)", "流量(GB)", "流量原文", "流量单位",
        "每元流量(GB)", "通话(分)", "名称", "套餐名", "类型大类", "类型细分",
        "宽带字段", "宽带判定", "城市", "目标客户", "渠道档", "办理渠道原文",
        "报备编号", "上架日", "下线日", "本次变更", "超套资费", "有效期", "权益说明"]


def num(v):
    """复现 template.html 的 num()：空串 / 非数字 → None"""
    try:
        s = str(v or "").strip()
        if not s:
            return None
        f = float(s)
        return None if f != f else f
    except Exception:
        return None


def fmt_d(s):
    s = str(s or "")
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if re.match(r"^\d{8}$", s) else ""


def per_val(d):
    """复现 perVal()：月费 0 且含流量 → ∞（不是 null，否则排序时沉底）"""
    f = num(d.get("f"))
    g = d.get("g")
    if not g or f is None or f < 0:
        return None
    return float("inf") if f == 0 else g / f


def row_out(net, d, all_province):
    AD.ALL_PROVINCE = all_province
    pv = per_val(d)
    bi = AD.bw_info(d)
    return [
        SC_LB.get(d.get("sc"), ""),
        "已下架" if d.get("st") else "在售",
        d.get("ow") or "",
        d.get("f") or "",
        "" if d.get("g") is None else round(d["g"], 3),
        d.get("d") or "",
        d.get("du") or "",
        "" if pv is None else ("∞" if pv == float("inf") else round(pv, 3)),
        d.get("c") or "",
        d.get("n") or "",
        d.get("t") or "",
        d.get("cat") or "",
        d.get("ty") or "",
        d.get("bw") or "",
        ("含宽带" if bi and bi[0] == "field" else ("含宽带(按名称判定)" if bi else "不含宽带")),
        "/".join(AD.city_tags(d, all_province)),
        d.get("ap") or "",
        d.get("chx") or "",
        d.get("ch") or "",
        d.get("r") or "",
        fmt_d(d.get("o")),
        fmt_d(d.get("e")),
        ("新增" + ("/" if d.get("ca") and d.get("ck") else "") + "字段变更"
         if d.get("ck") else ("新增" if d.get("ca") else "")),
        d.get("ex") or "",
        d.get("vp") or "",
        d.get("x") or "",
    ]


def load_nets():
    raw = open(HTML, encoding="utf-8").read()
    m = re.search(r"const\s+NETS\s*=", raw)
    if not m:
        sys.exit("页面里找不到 const NETS= —— 先跑 tariff_monitor.py")
    nets, _ = json.JSONDecoder().raw_decode(raw[m.end():])
    # 页面上的「数据基线日期」：所有网共用同一天
    dm = re.search(r"数据基线\s*(\d{4}-\d{2}-\d{2})", raw)
    date = dm.group(1) if dm else ""
    return nets, date


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)


def sheet(wb, title, rows, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(COLS)
    for r in rows:
        ws.append(r)
    head = PatternFill("solid", fgColor="DDEBF7")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(COLS)), max(2, len(rows) + 1))
    widths = {"名称": 42, "套餐名": 30, "目标客户": 40, "办理渠道原文": 26,
              "权益说明": 50, "超套资费": 24, "有效期": 20, "类型细分": 14,
              "报备编号": 20, "城市": 16, "宽带字段": 16, "宽带判定": 18}
    for i, c in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 12)
    return ws


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else None
    nets, date = load_nets()
    if not out:
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        out = os.path.join(desk, "河北四网资费专区-%s" % (date or "最新").replace("-", ""))
    os.makedirs(out, exist_ok=True)

    per, total = {}, []
    for code in NET_ORDER:
        nd = (nets or {}).get(code) or {}
        rows = nd.get("rows") or []
        ap = bool(nd.get("allProvince"))
        out_rows = [row_out(code, d, ap) for d in rows]
        per[code] = out_rows
        total.extend([[NET_CN[code]] + r for r in out_rows]) if False else None
        print("  %-4s %5d 条" % (NET_CN[code], len(out_rows)))

    # CSV：每网一份 + 合计一份
    allrows = []
    for code in NET_ORDER:
        for r in per[code]:
            allrows.append([NET_CN[code]] + r)
        write_csv(os.path.join(out, "%s_%d条.csv" % (NET_CN[code], len(per[code]))),
                  per[code])
    allrows.sort(key=lambda r: (r[0],))
    grand = sum(len(per[c]) for c in NET_ORDER)
    write_csv(os.path.join(out, "四网合计_%d条.csv" % grand), allrows)

    # xlsx：概览 + 四网分表 + 合计（合计多一列「运营商」）
    wb = Workbook()
    ov = wb.active
    ov.title = "概览"
    ov.append(["河北四网资费专区 · 数据基线 " + (date or "-")])
    ov["A1"].font = Font(bold=True, size=14)
    ov.append([])
    ov.append(["运营商", "条数", "在售", "已下架", "河北", "全国"])
    for c in ov[3]:
        c.font = Font(bold=True)
    for code in NET_ORDER:
        rows = per[code]
        i_sc, i_st = COLS.index("地域"), COLS.index("状态")
        ov.append([NET_CN[code], len(rows),
                   sum(1 for r in rows if r[i_st] == "在售"),
                   sum(1 for r in rows if r[i_st] == "已下架"),
                   sum(1 for r in rows if r[i_sc] == "河北"),
                   sum(1 for r in rows if r[i_sc] == "全国")])
    ov.append(["合计", grand,
               sum(1 for r in allrows if r[1 + COLS.index("状态")] == "在售"),
               sum(1 for r in allrows if r[1 + COLS.index("状态")] == "已下架"),
               sum(1 for r in allrows if r[1 + COLS.index("地域")] == "河北"),
               sum(1 for r in allrows if r[1 + COLS.index("地域")] == "全国")])
    for c in ov[len(NET_ORDER) + 4]:
        c.font = Font(bold=True)
    ov.append([])
    ov.append(["说明", "列口径与线上页面「导出 CSV」完全一致（26 列）；"
                     "「城市」列联通/广电恒空 —— 上游没有地市级数据，不是采集漏了。"])
    ov.column_dimensions["A"].width = 12
    ov.column_dimensions["B"].width = 80

    for code in NET_ORDER:
        sheet(wb, NET_CN[code], per[code])

    ws = wb.create_sheet("合计")
    ws.append(["运营商"] + COLS)
    for r in allrows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(COLS) + 1), len(allrows) + 1)
    for i, name in enumerate(["运营商"] + COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = {
            "名称": 42, "套餐名": 30, "目标客户": 40, "办理渠道原文": 26,
            "权益说明": 50, "超套资费": 24, "有效期": 20}.get(name, 12)
    wb.save(os.path.join(out, "河北四网资费专区.xlsx"))

    # 原始 JSON（解压后，供二次加工）
    for code, tag in (("move", "移动"), ("unicom", "联通"),
                      ("telecom", "电信"), ("cbn", "广电")):
        src = os.path.join(SNAP, "%s_tariff_%s.json.gz"
                           % ({"move": "hebei"}.get(code, code),
                              (date or "").replace("-", "")))
        if not os.path.exists(src):
            continue
        with gzip.open(src, "rb") as f:
            o = json.loads(f.read().decode("utf-8"))
        with open(os.path.join(out, "原始_%s.json" % tag), "w", encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False, separators=(",", ":"))

    print("\n输出目录：%s" % out)
    print("合计 %d 条（%s）" % (grand, " · ".join(
        "%s %d" % (NET_CN[c], len(per[c])) for c in NET_ORDER)))
    for fn in sorted(os.listdir(out)):
        print("  %-34s %10d B" % (fn, os.path.getsize(os.path.join(out, fn))))


if __name__ == "__main__":
    main()
