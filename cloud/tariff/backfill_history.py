# -*- coding: utf-8 -*-
"""把既有的 ``changes/*.md`` 解析成 ``history.json``（页面「变化历史」的数据源）。

为什么需要回填：history.json 是**从今天起**由 ``write_report()`` 逐次追加的，
而 changes/ 里已经躺着前几天的真实变更报告。不回填的话，页面时间线的起点
就是「部署当天」，看起来像「这个站刚建、之前什么都没发生」—— 明明有历史却装作没有。

★ md 是**权威来源**：同 (日期, 网) 的既有记录会被 md 里的数字覆盖 —— md 可能被
  当天更晚的一轮巡检重写过。没有对应 md 的记录（例如基线那轮）不动，不会被抹掉。
★ 幂等：按 (日期, 网) 归并，跑一百遍结果一样。
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CHG = os.path.join(BASE, "changes")
HIST = os.path.join(BASE, "history.json")

# 报告文件名前缀 → 网 code。移动的报告**没有前缀**（历史上就这一家）。
TAG_CODE = {"": "move", "unicom": "unicom", "cbn": "cbn", "ct": "telecom"}
CN_NET = {"move": "河北移动", "unicom": "河北联通", "telecom": "河北电信", "cbn": "中国广电"}

RE_TS = re.compile(r"本次抓取：\s*([\d\-: ]+)")
RE_N = re.compile(r"条目数：\s*\d+\s*→\s*\*\*(\d+)\*\*")
RE_CNT = re.compile(r"新增\s*\*\*(\d+)\*\*\s*·\s*下线\s*\*\*(\d+)\*\*\s*·\s*字段变更\s*\*\*(\d+)\*\*")
RE_H2 = re.compile(r"^##\s+(新增资费|下线/下架资费|关键字段变更)", re.M)
RE_ITEM = re.compile(r"^-\s+\*\*(.+?)\*\*\s*〔(.+?)〕")


def parse(md_path, tag):
    """解析一份变更报告 → 一条 history 记录（解析不出关键行就返回 None）。"""
    txt = open(md_path, encoding="utf-8").read()
    if "首版基线" in txt and not RE_CNT.search(txt):
        return None                      # 基线报告没有增删改计数，不进时间线
    m_ts, m_n, m_c = RE_TS.search(txt), RE_N.search(txt), RE_CNT.search(txt)
    if not (m_ts and m_n and m_c):
        print("   ! 跳过（关键行缺失）：%s" % os.path.basename(md_path))
        return None
    a, r, c = (int(x) for x in m_c.groups())

    # 按二级标题切段，逐段抽「- **名称** 〔类型〕」
    smp, cur = [], ""
    for line in txt.split("\n"):
        h = RE_H2.match(line)
        if h:
            cur = h.group(1)
            continue
        if cur:
            it = RE_ITEM.match(line)
            if it:
                k = {"新增资费": "a", "下线/下架资费": "r", "关键字段变更": "c"}[cur]
                cap = {"a": 6, "r": 4, "c": 4}[k]
                if sum(1 for x in smp if x["k"] == k) < cap:
                    smp.append({"n": it.group(1)[:60], "ty": it.group(2), "k": k})
    d = m_ts.group(1).strip()[:10]
    return {"ts": m_ts.group(1).strip()[:19], "d": d, "code": TAG_CODE[tag],
            "net": CN_NET[TAG_CODE[tag]], "n": int(m_n.group(1)),
            "a": a, "r": r, "c": c, "smp": smp,
            "src": "backfill"}           # 标出来源：回填的，不是运行时逐次追加的


def load_hist():
    try:
        with open(HIST, encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict) and isinstance(h.get("items"), list):
            return h
    except Exception:
        pass
    return {"schema": 1, "items": []}


def main():
    h = load_hist()

    # ① 既有记录先按 (日期, 网) 归并、保留**最后一条**。
    #    一天跑多轮会在文件里留下同一天同网的多条，时间线上表现为「同一天两套
    #    互相矛盾的数字」；后一轮的比对基线也是「上一个不同日期的快照」，
    #    数字本身就覆盖前一轮，所以留后一条是对的。
    items, seen, dup = [], {}, 0
    for x in h["items"]:
        k = (x.get("d"), x.get("code"))
        if k in seen:
            dup += 1
            items[seen[k]] = x
        else:
            seen[k] = len(items)
            items.append(x)

    # ② changes/*.md 覆盖同 (日期, 网) 的既有记录（见模块头注释：md 是权威来源）
    md, skip = {}, 0
    for p in sorted(glob.glob(os.path.join(CHG, "*.md"))):
        stem = os.path.basename(p)[:-3]
        day = stem.rsplit("-", 3)[-3:]          # 2026-09-22
        if len(day) != 3 or not day[0].isdigit():
            skip += 1
            continue
        tag = stem[:-(len("-".join(day)) + 1)]  # 去掉 "-2026-09-22"
        tag = tag.strip("-")
        if tag not in TAG_CODE:
            print("   ! 未知网前缀「%s」，跳过 %s" % (tag, stem))
            skip += 1
            continue
        rec = parse(p, tag)
        if not rec:
            continue
        md[(rec["d"], rec["code"])] = rec

    add, repl = [], 0
    for k, rec in md.items():
        if k in seen:
            items[seen[k]] = rec
            repl += 1
        else:
            seen[k] = len(items)
            items.append(rec)
            add.append(rec)

    items.sort(key=lambda x: (x.get("ts") or "", x.get("code") or ""))
    h["schema"] = 1
    h["items"] = items
    # newline="\n"：与 tariff_monitor.hist_append 保持一致，避免 Windows 写出
    # CRLF 而 CI（Linux）写 LF，让这个每天提交的文件整篇显示为「已修改」。
    with open(HIST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)
    print("回填：新增 %d 条 · 按 md 覆盖 %d 条 · 归并同天重复 %d 条（无关文件 %d），"
          "history.json 现有 %d 条" % (len(add), repl, dup, skip, len(items)))
    for r in add:
        print("   %s  %-6s 条数 %-5s 新增 %-3s 下线 %-3s 变更 %-3s 样本 %d"
              % (r["ts"], r["net"], r["n"], r["a"], r["r"], r["c"], len(r["smp"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
