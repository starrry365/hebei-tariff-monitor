#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采样噪声护栏 —— 抓「总数没变、但整批换人」的那种假变化

与 change_guard.py 的分工（**互补，不重叠**）：
  change_guard 的 ``DEGRADE_RATIO`` 看的是**总数掉没掉**（n < n_old × 0.6 → 判降级）。
  本模块看的是**总数没掉的时候**：上游按随机子集轮换时，
  8000 条还是 8000 条，但 added / removed 可能各几百条 ——
  降级护栏完全抓不到，几百条假下架会一条不漏地放行。

  判据必须**同时**满足两条，缺一条都会误伤：
    · 比例高：moved / baseline > NOISE_RATIO(0.3)
        只看比例 → 基线 3 条的小网动 2 条就 67%，可那是真变化；
    · 绝对数大：moved ≥ NOISE_MIN_ABS(50)
        只看绝对数 → 8000 条的联通动 60 条（0.75%）太正常了，不该判噪声。
    两个都成立，才说明「上游这一轮吐的是另一批子集」。

  为什么必须拦：上游按随机子集返回时，「本轮没采到」与「业务下架」
  长得**一模一样**。放行就等于凭空报出几百条假下架 ——
  最贵的代价不是误报本身，而是真变化被淹没之后再也没人看了。

本模块**只提供判据**，不擅自动数据（--check 是只读体检）。要接入巡检，
在拿到一轮的 added/removed 之后、写 history 之前调 ``is_sampling_noise``，
命中就把这一网标成 note=noise 并冻结（与 degrade 同样处理）。

用法：
    python cloud/tariff/noise_guard.py --selfcheck   # 出厂检验
    python cloud/tariff/noise_guard.py --check       # 只读体检 history.json
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "history.json")

#: 变化量占比超过这个数（且绝对数达标）→ 判为采样噪声
NOISE_RATIO = float(os.getenv("NOISE_RATIO") or "0.3")
#: 绝对数下限：小网基线低，光看比例会天天误报
NOISE_MIN_ABS = int(os.getenv("NOISE_MIN_ABS") or "50")
#: history 里每条记录最多留几个样本（防 json 无限膨胀）
HIST_SAMPLE_LIMIT = int(os.getenv("HIST_SAMPLE_LIMIT") or "12")
#: 单条长文本（资费名 / 字段值）在 history 里的上限（字符）
DETAIL_TEXT_LIMIT = int(os.getenv("DETAIL_TEXT_LIMIT") or "200")


def is_sampling_noise(added, removed, baseline,
                      ratio=NOISE_RATIO, min_abs=NOISE_MIN_ABS):
    """整板块的 (added + removed) 是否达到「采样噪声」级别。

    ``baseline`` = 本轮**采到的**条目总数（不是上一轮）。
    返回 True 表示这一轮的 added/removed **不可信**，应冻结而不写进历史。
    """
    try:
        base = int(baseline or 0)
    except (TypeError, ValueError):
        return False
    if base <= 0:
        return False
    moved = int(added or 0) + int(removed or 0)
    if moved < min_abs:
        return False
    return (moved / float(base)) > ratio


def slim_sample(smp, limit=HIST_SAMPLE_LIMIT, text_limit=DETAIL_TEXT_LIMIT):
    """样本瘦身：限条数 + 截长文本。返回**新列表**，不改原对象。

    为什么限条数：样本只是给人看的「举几个例子」，留 12 条足够判断趋势；
    全部留下会让 history.json 随天数线性膨胀（每条资费名 + 分类 + 标记）。
    """
    out = []
    for s in (smp or [])[:max(0, int(limit))]:
        if not isinstance(s, dict):
            continue
        r = dict(s)
        n = r.get("n")
        if isinstance(n, str) and len(n) > text_limit:
            r["n"] = n[:text_limit] + "…"
        out.append(r)
    return out


def slim_history(items, sample_limit=HIST_SAMPLE_LIMIT,
                 text_limit=DETAIL_TEXT_LIMIT):
    """对 history 的 items 做瘦身，返回**新列表**（不改入参）。

    🔴 刻意做成纯函数：调用方自己决定写不写盘 —— 这样它能先 diff、
       先给人看，确认无误再落盘。自动改写历史数据是最不该「顺手」做的事。
    """
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        r = dict(it)
        r["smp"] = slim_sample(r.get("smp"), sample_limit, text_limit)
        out.append(r)
    return out


def scan(items):
    """只读体检：哪些记录看起来是采样噪声、历史能瘦掉多少。"""
    noisy, oversized, total_sample = [], 0, 0
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        n = int(it.get("n") or 0)
        a, r = int(it.get("a") or 0), int(it.get("r") or 0)
        if is_sampling_noise(a, r, n):
            noisy.append({"d": it.get("d"), "net": it.get("net"),
                          "n": n, "moved": a + r,
                          "pct": round((a + r) / float(n) * 100, 1)})
        s = it.get("smp") or []
        total_sample += len(s)
        if len(s) > HIST_SAMPLE_LIMIT:
            oversized += 1
    return {"records": len(items), "total_sample": total_sample,
            "oversized": oversized, "noisy": noisy}


def _selftest():
    fails = []

    def ck(name, got, want):
        if got != want:
            fails.append("%s: 得到 %r，期望 %r" % (name, got, want))

    # ① 典型噪声：8000 条里换掉 3000 条（37.5%）→ 是噪声
    ck("8000/3000=噪声", is_sampling_noise(1500, 1500, 8000), True)
    # ② 总数没掉、动得也不多 → 正常
    ck("8000/20=正常", is_sampling_noise(10, 10, 8000), False)
    ck("8000/400=正常(仅5%)", is_sampling_noise(200, 200, 8000), False)
    # ③ 小网高比例但绝对数不够 → **不是**噪声（这正是绝对数下限存在的理由）
    ck("3条动2条=真变化", is_sampling_noise(1, 1, 3), False)
    ck("49条在下限外", is_sampling_noise(25, 24, 100), False)
    ck("50条踩下限", is_sampling_noise(25, 25, 100), True)
    # ④ 基线为 0 / None 不能炸
    ck("基线0", is_sampling_noise(100, 100, 0), False)
    ck("基线None", is_sampling_noise(100, 100, None), False)
    # ⑤ 比例边界：0.3 是「大于」不是「大于等于」
    ck("正好30%不算", is_sampling_noise(150, 150, 1000), False)
    ck("30.1%算", is_sampling_noise(151, 150, 1000), True)
    # ⑥ 样本瘦身：限条数 + 截长文本 + 不改原对象
    src = [{"n": "汉" * 300, "ty": "套餐", "k": "a"}] * 30
    sl = slim_sample(src)
    ck("样本限 12 条", len(sl), 12)
    ck("长文本被截", len(sl[0]["n"]) <= DETAIL_TEXT_LIMIT + 1, True)
    ck("原对象未被改", len(src[0]["n"]), 300)
    # ⑦ history 瘦身是纯函数
    items = [{"d": "2026-09-30", "smp": [{"n": "x"}] * 99}]
    sh = slim_history(items)
    ck("瘦身后 12 条", len(sh[0]["smp"]), 12)
    ck("原 items 未被改", len(items[0]["smp"]), 99)
    # ⑧ 体检能认出噪声记录
    rep = scan([{"d": "2026-09-30", "net": "河北联通", "n": 8000, "a": 1500, "r": 1500},
                {"d": "2026-09-30", "net": "河北移动", "n": 5000, "a": 3, "r": 1}])
    ck("体检认出 1 条噪声", len(rep["noisy"]), 1)
    ck("体检噪声网名", rep["noisy"][0]["net"], "河北联通")
    ck("体检噪声占比", rep["noisy"][0]["pct"], 37.5)

    if fails:
        print("采样噪声护栏自测失败 %d 项：" % len(fails))
        for x in fails:
            print("  ✗", x)
        return 1
    print("采样噪声护栏自测通过（比例+绝对数双条件 / 边界 / 瘦身纯函数 / 体检）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="采样噪声护栏与历史瘦身")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="只读体检 history.json（不改任何文件）")
    args = ap.parse_args()

    if args.selfcheck:
        return _selftest()
    if args.check:
        try:
            with open(HIST, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print("::error::读不到 history.json：%s" % e)
            return 2
        items = d.get("items") if isinstance(d, dict) else d
        rep = scan(items)
        print("history.json 体检（只读，未改动任何文件）")
        print("  记录数：%d" % rep["records"])
        print("  样本总数：%d（超 %d 条的记录 %d 条）"
              % (rep["total_sample"], HIST_SAMPLE_LIMIT, rep["oversized"]))
        print("  判据：moved/基线 > %.0f%% 且 moved ≥ %d"
              % (NOISE_RATIO * 100, NOISE_MIN_ABS))
        if rep["noisy"]:
            print("  ⚠ 疑似采样噪声的记录 %d 条：" % len(rep["noisy"]))
            for n in rep["noisy"]:
                print("     %s %s：%s 条中动了 %s 条（%s%%）"
                      % (n["d"], n["net"], n["n"], n["moved"], n["pct"]))
        else:
            print("  ✅ 没有疑似采样噪声的记录")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
