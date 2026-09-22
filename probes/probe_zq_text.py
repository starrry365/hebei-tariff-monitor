# -*- coding: utf-8 -*-
"""四网「个人 / 政企」分档：用 applicablePeople（目标客户）文本自动归类。

这是 2026-09-22 深挖结论（**政企资费在四网公开渠道都取不到**：分类树空壳 / 明细恒 0 /
门户不公开）的旁证 —— 唯一能拿到的政企信号就是这行文本，且**未标注占比 84%~99%**，
所以它**不足以**支撑一个可信的「政企资费」筛选层（这也是当初决定不做政企层的原因之一）。

用法：
    python probes/probe_zq_text.py              # 用最新一份快照
    python probes/probe_zq_text.py 20260922     # 指定日期

判据（只用上游给的文本，不猜）：
    both = 同时提到「政企」与「公众/个人/家庭」（如「公众客户/政企客户均可办理」）
    zq   = 只提到「政企」
    gr   = 只提到「公众 / 个人 / 家庭」
    none = 两者都没提（**未标注**）
"""
import collections
import glob
import gzip
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "cloud", "tariff", "snapshots")

NET_FILE = [("移动", "hebei_tariff_%s.json.gz"),
            ("联通", "unicom_tariff_%s.json.gz"),
            ("广电", "cbn_tariff_%s.json.gz"),
            ("电信", "ct_tariff_%s.json.gz")]


def newest_day():
    ds = []
    for p in glob.glob(os.path.join(SNAP, "*_tariff_*.json.gz")):
        m = re.search(r"_(\d{8})\.json\.gz$", p)
        if m:
            ds.append(m.group(1))
    if not ds:
        raise SystemExit("快照目录里没有任何 *_tariff_YYYYMMDD.json.gz：%s" % SNAP)
    return max(ds)


def entries_of(o):
    out = []
    for g in o.get("groups") or []:
        out.extend(g.get("entries") or [])
    if not out:
        out = o.get("entries") or []
    return out


def classify(t):
    t = str(t or "")
    hz = "政企" in t
    hg = ("公众" in t) or ("个人" in t) or ("家庭" in t)
    if hz and hg:
        return "both"
    if hz:
        return "zq"
    if hg:
        return "gr"
    return "none"


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else newest_day()
    print("基线日期：%s\n" % day)
    summary = {}
    for net, tpl in NET_FILE:
        p = os.path.join(SNAP, tpl % day)
        if not os.path.exists(p):
            print("== %s：快照缺失 ==" % net)
            continue
        o = json.load(gzip.open(p, "rt", encoding="utf-8"))
        es = entries_of(o)
        n = len(es)
        stat = collections.Counter()
        samp = collections.defaultdict(list)
        apm = collections.Counter()
        for e in es:
            t = str(e.get("applicablePeople") or "")
            k = classify(t)
            stat[k] += 1
            if len(samp[k]) < 4:
                samp[k].append((str(e.get("name") or e.get("tariffName") or "")[:30], t[:52]))
            if k in ("zq", "both"):
                apm[t[:70]] += 1
        summary[net] = (n, stat)
        print("== %s  %d 条 ==" % (net, n))
        for k, lb in (("zq", "纯政企"), ("both", "政企/公众均可"), ("gr", "纯公众"), ("none", "未标注")):
            print("   %-12s %5d  (%5.1f%%)" % (lb, stat[k], 100.0 * stat[k] / n if n else 0))
        for k, lb in (("zq", "纯政企"), ("both", "均可")):
            for nm, t in samp[k]:
                print("        [%s] %s | %s" % (lb, nm, t))
        if apm:
            print("   政企相关目标客户原文:")
            for k, v in apm.most_common(8):
                print("        %4d  %s" % (v, k))
        print()

    print("=== 汇总（纯政企 + 均可 的占比，即「撑得起政企层」的信号强度）===")
    for net, (n, stat) in summary.items():
        zq = stat["zq"] + stat["both"]
        print("  %-4s  政企相关 %3d / %5d = %5.1f%%   （未标注 %5.1f%%）"
              % (net, zq, n, 100.0 * zq / n if n else 0,
                 100.0 * stat["none"] / n if n else 0))
    print()
    print("结论：政企信号几乎全靠一行自由文本，且未标注占绝对多数 ⇒ 不能据它做筛选层。")


if __name__ == "__main__":
    main()
