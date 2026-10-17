# -*- coding: utf-8 -*-
"""资费明细列表的「密度」不变量测试（离线、毫秒级）。

2026-10-18 的一轮列表优化，量出来的病根是**行高**：四网快照、默认每页 80 行，
联通的目标客户文案最长 220 字，桌面上此前不截断，折 6–7 行 —— 单行 117px
（p50）、最多 251px，一屏 788px 只放得下 6 条；而移动文案短，同一张表 p50
只有 60px。「换一个网，翻页手感差一倍」就是这么来的。

修法是三层，缺一层都会以**静默**的方式坏掉（不报错，只是变难用）：

  ① CSS clamp 必须落在**顶层**（不能只写在 ≤640px 的媒体查询里）——
     否则桌面照旧不截断，改了个寂寞；而媒体查询里本来就有同一条规则，
     所以「搜到 -webkit-line-clamp:2」并不能证明桌面生效，必须先剥掉 @media。
  ② 截断就要能看全：真溢出的行补 title（syncApTitles 用几何判，不按字数猜），
     全文另有展开详情兜底。少了 title，「截断」就等于「丢数据」。
  ③ 搜索命中的行不能截断（.aph）—— 命中的词若落在第 4 行以后，行出现了
     却看不出为什么命中，比长文案本身更糟。这条覆盖规则必须在**窄屏那条
     之后且更具体**，否则 ≤640px 卡片视图里会被媒体查询盖掉。

另外锁一个今天真实踩到的坑：列筛选的取值键 data-cf 在 th 里的 **▽ 按钮**上，
不在 th 上。按 ``th[data-cf]`` 选会命中 0 个元素，整段逻辑静默空转 ——
markEmptyCols 第一版就是这么写的，表头一个都没标上。

用法：python tests/test_list_density.py   # 退出码 0 = 通过
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "cloud", "tariff", "template.html")


def fail(msg):
    print("  ✗ %s" % msg)
    return False


def strip_media(css):
    """剥掉所有 @media 块（含嵌套花括号），只留顶层规则。"""
    out = []
    i = 0
    n = len(css)
    while i < n:
        m = re.compile(r"@media\b").search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        j = css.find("{", m.end())
        if j < 0:
            break
        depth = 0
        k = j
        while k < n:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1
    return "".join(out)


def main():
    with open(TPL, encoding="utf-8") as f:
        src = f.read()

    ok = True
    top = strip_media(src)          # 顶层 CSS（桌面生效的那些）
    js = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # 剥注释，防注释假绿
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)

    # ① 桌面两行截断必须是顶层规则
    if not re.search(r"\.ap\s+\.apv\s*\{[^}]*-webkit-line-clamp\s*:\s*2", top):
        ok = fail("顶层 CSS 里没有「.ap .apv 两行 -webkit-line-clamp」—— "
                  "只写在 ≤640px 媒体查询里的话，桌面等于没截断")

    # ② 截断要有补偿：真溢出的行补 title，且用几何判据
    if "syncApTitles" not in js:
        ok = fail("找不到 syncApTitles —— 截断后没人补 title，被截掉的文案就彻底看不到了")
    else:
        m = re.search(r"function\s+syncApTitles\s*\(\)\s*\{(.{0,400}?)\n\}", js, re.S)
        body = m.group(1) if m else ""
        if "scrollHeight" not in body or "clientHeight" not in body:
            ok = fail("syncApTitles 没用 scrollHeight/clientHeight —— 按字数猜会"
                      "「没溢出却挂省略号 / 溢出却没有 title」")
        if not re.search(r"render\s*\(\s*\)[\s\S]{0,4000}?syncApTitles\s*\(\s*\)", js):
            ok = fail("render() 没有调用 syncApTitles —— 只有切网时才补 title，翻页就不补了")

    # ③ 搜索命中的行不截断，且覆盖规则要比窄屏那条更具体
    if ".aph" not in js:
        ok = fail("apCell 没给命中的行加 .aph —— 命中词落在第 3 行以后就看不见了")
    m = re.search(r"(table\s+tr\.row\s*>\s*td\.ap\.aph\s+\.apv)\s*\{", top)
    if not m:
        ok = fail("缺少「table tr.row>td.ap.aph .apv」这条覆盖规则：选择器不够具体时，"
                  "≤640px 的 tr.row>td.ap .apv 会把它盖掉，窄屏命中反而看不见")
    elif "-webkit-line-clamp" not in top[m.end():m.end() + 200]:
        ok = fail("覆盖规则里没有重置 -webkit-line-clamp")

    # ④ 列筛选取值键必须从 ▽ 按钮上取，不能按 th[data-cf] 选
    if re.search(r'querySelectorAll\(\s*"\.tblwrap\s+th\[data-cf\]', js):
        ok = fail('markEmptyCols 又按 th[data-cf] 选了 —— data-cf 在 th 里的 .cfx 按钮上，'
                  '这样选命中 0 个元素，整段静默空转')
    if '.cfx' not in js or 'dataset.cf' not in js:
        ok = fail("markEmptyCols 没从 .cfx 按钮读 data-cf")
    if "markEmptyCols" not in js:
        ok = fail("找不到 markEmptyCols —— 整列全空的表头会看着像渲染坏了")

    # ⑤ 空值格仍走 m0（窄屏靠它隐藏整列破折号），别在改字面量时弄丢
    if 'class="ap m0"' not in js:
        ok = fail("apCell 的空值分支没有 m0 —— 窄屏会重新冒出一排破折号")

    if ok:
        print("  ✓ 目标客户两行截断（顶层）· 截断补 title · 命中不截断 · 空列表头提示")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
