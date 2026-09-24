#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：模板里**每条 `position:sticky` 的规则都必须自带实色背景**。

【为什么需要这条】
  2026-09-24 用户截图顶部那条乱码：
      「0 元 —  数据总览  全国 通话尊享服…  资费明细  个人 —  变化历史  20 数据说明」
  根因**不是**数据错，而是吸顶的「视图页签条」忘了给背景（`.bar` 有 `glass`，
  `#vtab` 没有）⇒ 滚动时表格行从它下面透上来，与页签文字叠在一起。

  ★ 这一条**既有检查全都照不出来**：
    · `rect.top` 正常（透明元素的几何完全正确）；
    · `elementFromPoint` 也正常（命中测试返回最上层元素，与透明度无关）；
    · 事件绑定、可点性、异常数、连跑一致性 —— 全部正常。
    唯一能表达它的量就是**背景色的 alpha**。

【判据】
  A. `<style>` 里每条声明 `position:sticky` 的规则块，必须同时声明
     `background:`，且取值是**实色**（`var(--card) / --card2 / --card3` 或
     `#hex` / `rgb(...)` 不带 alpha 通道）。
     ⇒ 出现 `var(--glass*)`、`rgba(...,<1)`、`transparent`、`none` 一律判失败 ——
        半透明 + blur 仍然会让滚动内容透出来（只是糊一点），不是合格的吸顶面。
  B. 至少要有 1 条吸顶规则（**判据不能空转**：正则写错、选择器改名导致一条都没匹配到，
     必须报错而不是"通过"）。
  C. 页面里存在唯一的吸顶根 `#deck`（视图页签 ＋ 筛选工具条同盒）——
     防止有人再把它拆成几层各自 sticky（那是这次事故的结构成因）。

用法：python probes/probe_sticky_layers.py [--template cloud/tariff/template.html]
退出码：0 通过 / 2 失败
"""
import argparse
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 允许的实色写法：这三个变量在 :root / [data-theme=dark] 里都定义为 100% 不透明，
# 写成 hex 也说明作者是有意给实色。
OPAQUE_VAR = re.compile(r"var\(\s*--card[0-9]?\s*\)")
OPAQUE_LIT = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))\s*$")
BAD_HINT = re.compile(r"--glass|transparent|rgba\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*(?!1\s*\))")


def rules(css):
    """取出所有「最内层」规则块 (selector, decls)。@media 的外壳不会包进 selector。"""
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css, re.S)


def norm(decls):
    return re.sub(r"\s+", " ", decls.replace("\n", " ")).strip()


def background_value(decls):
    m = re.search(r"(?:^|;|\s)background\s*:\s*([^;]+)", decls)
    return m.group(1).strip() if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=os.path.join(ROOT, "cloud", "tariff", "template.html"))
    a = ap.parse_args()

    src = io.open(a.template, encoding="utf-8", newline="").read()
    try:
        css = src[src.index("<style>") + 7: src.index("</style>")]
    except ValueError:
        print("!! 模板里找不到 <style>…</style>：%s" % a.template)
        return 2

    def clean_sel(sel):
        # 正则取到的是「上一个块结束到本块 { 之间」的全部文本，会带上前面的注释，
        # 显示前去掉注释、压平空白，只留选择器本身。
        sel = re.sub(r"/\*.*?\*/", " ", sel, flags=re.S)
        return re.sub(r"\s+", " ", sel).strip()

    sticky = [(clean_sel(sel), norm(d)) for sel, d in rules(css)
              if "position:sticky" in norm(d).replace("position: sticky", "position:sticky")]

    bad, ok = [], []
    for sel, d in sticky:
        bg = background_value(d)
        if bg is None:
            bad.append((sel, "**没有 background**"))
            continue
        if OPAQUE_VAR.search(bg) or OPAQUE_LIT.match(bg):
            ok.append((sel, bg))
        else:
            bad.append((sel, bg))

    print("=" * 72)
    print("吸顶层不透明体检（每条 position:sticky 的规则都要自带实色背景）")
    print("=" * 72)
    for sel, bg in ok:
        print("  OK   %-28s background: %s" % (sel, bg))
    for sel, bg in bad:
        print("  !!   %-28s background: %s" % (sel, bg))
    print("-" * 72)
    print("  吸顶规则 %d 条（合格 %d / 不合格 %d）" % (len(sticky), len(ok), len(bad)))

    problems = []
    if bad:
        for sel, bg in bad:
            problems.append("吸顶层 %r 的背景不是实色（%s）⇒ 滚动时内容会从它下面透上来与它叠成乱码"
                            % (sel, bg))
    if not sticky:
        problems.append("**一条 position:sticky 规则都没匹配到** —— 判据空转了（选择器改名？"
                        "正则坏了？）。吸顶层必须存在，不能把「没找到」当「通过」。")
    if 'id="deck"' not in src:
        problems.append('模板里没有唯一的吸顶根 #deck —— 视图页签与筛选工具条被拆成了几层各自 '
                        'position:sticky，这正是 2026-09-24 那次事故的结构成因。')
    if "--card:" not in src:
        problems.append(":root 里没有定义 --card（吸顶层的实色面）—— 判据的合格写法失去依据。")

    print("=" * 72)
    if problems:
        for p in problems:
            print("  ❌ %s" % p)
        print("\n结论：失败（%d 个问题）" % len(problems))
        return 2
    print("  ✅ 通过：%d 条吸顶规则全部自带实色背景，且存在唯一吸顶根 #deck" % len(ok))
    print("     判据含义：吸顶层不透明 ⇒ 滚动内容不会从它下面透出来（不会叠成乱码）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
