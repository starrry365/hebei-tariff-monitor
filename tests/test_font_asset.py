# -*- coding: utf-8 -*-
"""页面字体（Maple Mono，自托管）的资产与接线测试（离线、毫秒级）。

2026-10-20 用户要求把字体换成 https://github.com/subframe7536/maple-font。
这个站点有两条**不能破的既有约束**，所以「换字体」不是加一条 font-family 那么简单：

  ① 免责声明写着「不引用任何外部域名资源（断网可用，可在开发者工具中核对）」，
     并且 tests/test_disclaimer.py 会逐条扫外部 URL。⇒ 字体必须**自托管**，
     走同源相对路径，不能 @import Google Fonts、不能引 jsDelivr。
  ② 页面本身已经背着 2.4MB 的四网 sidecar，移动端还要能开。Maple 的
     **CN 版整包 134MB**（单 weight 也是几 MB）⇒ 只接拉丁子集（数字/英文/标点），
     CJK 由系统字体回退。这条如果哪天被人「顺手换成 CN 全量」，页面会直接废掉，
     所以这里钉一个总体积上限。

锁的东西：
  · 三个字重的 woff2 真在、真的是 woff2（magic `wOF2`）、总体积 < 400KB；
  · @font-face 的 url 必须是相对路径（出现 http/https/data: 就是违约）；
  · body 与 code 的字体栈里 "Maple Mono" 必须在**第一位**（放后面等于没换）；
  · font-display:swap（不许为了 229KB 卡住首屏）；
  · OFL 许可证随站点分发（SIL 的要求，不是可选项）；
  · 免责声明里那句「字体随站点托管」也还在。

用法：python tests/test_font_asset.py   # 退出码 0 = 通过
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARIFF = os.path.join(ROOT, "cloud", "tariff")
TPL = os.path.join(TARIFF, "template.html")
FONTS = os.path.join(TARIFF, "docs", "fonts")

MAX_TOTAL_KB = 400          # 三个字重的拉丁子集实测 229KB；留一倍余量
FACES = {"MapleMono-Regular.woff2": 400,
         "MapleMono-SemiBold.woff2": 600,
         "MapleMono-Bold.woff2": 700}

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
    return bool(cond)


def main():
    with open(TPL, encoding="utf-8") as f:
        src = f.read()
    # 剥掉注释再判，避免「注释里写了 Maple Mono」被当成生效
    js = re.sub(r"/\*.*?\*/", "", src, flags=re.S)

    # ── ① 字体文件 ────────────────────────────────────────────────
    total = 0
    for name in FACES:
        p = os.path.join(FONTS, name)
        if not check(os.path.exists(p), "缺字体文件 docs/fonts/%s" % name):
            continue
        size = os.path.getsize(p)
        total += size
        check(40 * 1024 <= size <= 120 * 1024,
              "%s 体积异常（%.0f KB，拉丁子集应在 40–120KB）" % (name, size / 1024))
        with open(p, "rb") as f:
            head = f.read(4)
        check(head == b"wOF2",
              "%s 不是真正的 woff2（magic=%r）—— 别是改了后缀的 ttf" % (name, head))
    check(total / 1024 <= MAX_TOTAL_KB,
          "字体总体积 %.0f KB 超过 %d KB —— 大概率有人把 CN 全量塞进来了（整个包 134MB）"
          % (total / 1024, MAX_TOTAL_KB))

    # ── ② 许可证（OFL 强制要求随分发保留）──────────────────────────
    lp = os.path.join(FONTS, "LICENSE.txt")
    if check(os.path.exists(lp), "缺 docs/fonts/LICENSE.txt —— SIL OFL 要求分发时保留许可"):
        with open(lp, encoding="utf-8", errors="replace") as f:
            lic = f.read()
        check("SIL Open Font License" in lic, "LICENSE.txt 不是 SIL OFL 文本")
        check("Maple Mono" in lic, "LICENSE.txt 缺版权行（Maple Mono Project Authors）")

    # ── ③ @font-face 的接线 ───────────────────────────────────────
    faces = re.findall(r"@font-face\{([^}]*)\}", js)
    check(len(faces) >= 3, "template 里 @font-face 少于 3 条（400/600/700 三个字重）")
    seen = set()
    for body in faces:
        m = re.search(r'url\(\s*"?([^")]+)"?\s*\)', body)
        u = m.group(1) if m else ""
        check(bool(u) and not re.match(r"(https?:|//|data:)", u),
              "@font-face 的 url 不是同源相对路径（%r）—— 外部字体 CDN 会违反"
              "「不引用任何外部域名资源」这条承诺" % u)
        check("woff2" in body, "@font-face 没声明 woff2 格式")
        check("font-display:swap" in body.replace(" ", ""),
              "@font-face 缺 font-display:swap —— 字体没到时会白屏等 229KB")
        w = re.search(r"font-weight:(\d+)", body)
        if w:
            seen.add(int(w.group(1)))
    for w in (400, 600, 700):
        check(w in seen, "@font-face 缺 %d 字重 —— 该字重的数字会回退到系统字体，"
                         "同一列里出现两种数字宽度" % w)

    # ── ④ 字体栈：必须排第一 ──────────────────────────────────────
    m = re.search(r"body\{[^}]*?font:400 [^;]*?;", js, re.S)
    if check(bool(m), "找不到 body 的 font 简写 —— 判据无法定位字体栈"):
        stack = m.group(0)
        first = stack.split("font:400", 1)[1].split(";", 1)[0]
        first = re.split(r"\d+px/\s*[\d.]+", first)[-1].strip().split(",")[0].strip().strip('"')
        check(first == "Maple Mono",
              "body 字体栈第一位不是 Maple Mono（实际 %r）—— 排到系统字体后面等于没换" % first)
    code_ff = re.search(r"\.dscl code\{font-family:([^;]+);", js)
    if code_ff:
        check(code_ff.group(1).strip().strip('"').startswith("Maple Mono"),
              "代码片段（.dscl code）没换成 Maple Mono")

    # ── ⑤ 免责声明与字体来源一致 ──────────────────────────────────
    m = re.search(r'<section class="view" id="v-ab".*?</section>', src, re.S)
    if check(bool(m), "找不到免责声明段落 #v-ab"):
        ab = m.group(0)
        check("Maple Mono" in ab, "免责声明没提字体来源（用户可在开发者工具里看到字体请求）")
        check("fonts/LICENSE.txt" in ab, "免责声明没指向字体许可证路径")
    check("fonts.googleapis.com" not in src and "fonts.gstatic.com" not in src
          and "cdn.jsdelivr.net" not in src,
          "页面里出现了外部字体服务 —— 断网即失效，且与免责声明冲突")

    if FAILS:
        print("  ✗ 字体资产 %d 项不通过：" % len(FAILS))
        for x in FAILS:
            print("     - %s" % x)
        return False
    print("  ✓ Maple Mono 自托管（3 字重 %.0f KB · woff2 · OFL 随附）· 相对路径 · "
          "栈内第一 · swap · 免责声明一致" % (total / 1024))
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
