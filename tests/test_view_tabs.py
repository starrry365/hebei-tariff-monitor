# -*- coding: utf-8 -*-
"""视图页签一致性测试（离线、毫秒级）。

2026-10-17 新增「免责声明」页签时的教训：视图名在两个地方各写一份 ——
模板里的 ``<button data-v="…">`` 与脚本里的 ``const VIEWS=[…]``，而深链
白名单 ``readHash`` 里**又**写了一份 ``["ov","list","hist"]``。三处一漂移，
症状全是「静默」的：

  · VIEWS 漏了某页签 ⇒ 点它能进（setView 的越界判断会退回 list），
    但 `body[data-view]` 与高亮不会正确切换，看着像「点了没反应」；
  · 模板多了页签而 VIEWS 没加 ⇒ 直接落回明细页，不报错；
  · readHash 白名单没跟上 ⇒ 分享出去的 ``#v=ab`` 链接打开落在明细页，
    链接本身看着完全正常。

所以本测试锁死三条不变量：
  ① ``#vtab`` 里的 data-v 集合 == ``const VIEWS=[…]`` 的元素集合（且无重复）；
  ② 每个视图名都有对应的 ``<section class="view" id="v-<名字>">``；
  ③ 深链白名单不再另写一份字面量（readHash 必须引用 VIEWS）。

用法：python tests/test_view_tabs.py   # 退出码 0 = 通过
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "cloud", "tariff", "template.html")


def fail(msg):
    print("  ✗ %s" % msg)
    return False


def main():
    with open(TPL, encoding="utf-8") as f:
        src = f.read()

    ok = True

    # ① 页签栏里的 data-v
    m = re.search(r'<nav[^>]*id="vtab"[^>]*>(.*?)</nav>', src, re.S)
    if not m:
        return fail("找不到 #vtab 页签栏")
    # class 要容得下 "tab on"（默认页签带高亮类），否则会漏掉它
    tab_v = re.findall(r'<button[^>]*class="tab[^"]*"[^>]*data-v="([^"]+)"', m.group(1))
    if not tab_v:
        return fail("#vtab 里没有 data-v 页签")

    m = re.search(r"const\s+VIEWS\s*=\s*\[([^\]]*)\]", src)
    if not m:
        return fail("找不到 const VIEWS=[…]")
    view_v = [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]

    if len(set(tab_v)) != len(tab_v):
        ok = fail("页签 data-v 有重复：%s" % tab_v)
    if set(tab_v) != set(view_v):
        ok = fail("页签与 VIEWS 不一致：模板 %s vs 脚本 %s" % (tab_v, view_v))
    if sorted(tab_v) != sorted(view_v):
        # 顺序也要一致：页签按 VIEWS 顺序高亮/遍历时，错位会让人以为漏了某页
        ok = fail("页签与 VIEWS 顺序不同：模板 %s vs 脚本 %s" % (tab_v, view_v))

    # ② 每个视图都要有对应 section
    for v in view_v:
        if not re.search(r'<section[^>]*class="view[^"]*"[^>]*id="v-%s"' % re.escape(v), src):
            ok = fail("视图 %s 缺少 <section class=\"view\" id=\"v-%s\">" % (v, v))

    # ③ 深链白名单必须引用 VIEWS，而不是再写一份字面量
    # ★ 先剥注释再判：注释里本来就会写「白名单直接用 VIEWS」这样的说明，
    #   在带注释的原文里搜 "VIEWS" 会**永远命中**（本测试第一版就这么假绿了）。
    js = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
    m = re.search(r'const\s+vv\s*=\s*p\.get\("v"\)\s*;(.{0,300}?)setView\(v{1,2}\)', js, re.S)
    if not m:
        ok = fail("readHash 里找不到 v 参数处理")
    elif "VIEWS" not in m.group(1):
        ok = fail("readHash 的 v 白名单没引用 VIEWS（另写的字面量会漏掉新页签）")

    if ok:
        print("  ✓ 页签 %s · 视图 %s · 深链白名单引用 VIEWS"
              % ("/".join(tab_v), "/".join(view_v)))
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
