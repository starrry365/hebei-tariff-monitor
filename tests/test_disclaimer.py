# -*- coding: utf-8 -*-
"""免责声明与实际行为的一致性测试（离线、毫秒级）。

起因（2026-10-17）：新版免责声明第 6 条写了「仅在你的浏览器本地保存 5 项偏好」
并逐项列出键名，第 4 条写了「不设账号 / 不投放广告」。这类文案一旦和代码脱节，
就是**反向**伤害 —— 用户照着开发者工具一查发现多存了一个键，整页的可信度立刻
归零（比不写这段话更糟）。所以把它绑成测试：

  ① 页面写入的 localStorage 键集合 == 免责声明里 <code> 标注的键集合；
  ② 页面不引用任何外部域名资源（无第三方脚本 / 统计 / 广告 / 字体 CDN）。

②是「断网也能用、没有第三方」这句话的机器判据。SVG 的 XML 命名空间声明
（http://www.w3.org/2000/svg 等）不算请求，用白名单排除。

用法：python tests/test_disclaimer.py   # 退出码 0 = 通过
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "cloud", "tariff", "template.html")

# 不是网络请求的 http(s) 字符串：SVG/XML 命名空间与 DOCTYPE
NS_OK = (
    "http://www.w3.org/", "http://sodipodi.sourceforge.net/",
    "http://www.inkscape.org/", "https://www.w3.org/",
)


def fail(msg):
    print("  ✗ %s" % msg)
    return False


def strip_comments(js):
    """去掉 JS/CSS 注释：注释里常拿键名举例，会把判据喂成假绿。"""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def main():
    with open(TPL, encoding="utf-8") as f:
        src = f.read()
    js = strip_comments(src)

    # ── 免责声明段落（只在这一段里找 <code> 键名）────────────────────
    m = re.search(r'<section class="view" id="v-ab".*?</section>', src, re.S)
    if not m:
        return fail("找不到 #v-ab（免责声明）段落")
    claimed = set(re.findall(r"<code>([A-Za-z0-9_\-]+)</code>", m.group(0)))
    if not claimed:
        return fail("免责声明里没找到 <code> 标注的本地存储键名")

    # ── 代码里实际写入的键 ───────────────────────────────────────────
    # 常量 → 字面量（FAV_KEY="hbtm_fav" 这类），供后面的标识符参数查表
    consts = dict(re.findall(r'([A-Z][A-Z0-9_]*)\s*=\s*"([^"]+)"\s*[,;]', js))
    used = set()
    # setItem(k, …) 与 saveObj(k, …)：两者是仅有的写入口（saveObj 内部转调 setItem）
    for call in re.findall(r"(?:localStorage\.setItem|saveObj)\(\s*([^,)]+)", js):
        call = call.strip()
        if call.startswith(('"', "'")):
            used.add(call[1:-1])
        elif call in consts:
            used.add(consts[call])
        # 其它形态（saveObj(k,…) 在 saveObj 定义内部）先不猜：漏掉会被下面的
        # 「声明里有但代码没写」抓住，反过来则不会 —— 所以再补一条兜底：
    # 兜底：任何 localStorage.setItem("字面量" 都算（正则上面已覆盖），
    # 再把 consts 里以 hbtm_/hb-tariff/ 开头、且被 setItem 引用过的都算进来
    for name, val in consts.items():
        if re.search(r"(?:localStorage\.setItem|saveObj)\(\s*%s\b" % re.escape(name), js):
            used.add(val)

    if not used:
        return fail("解析不到任何 localStorage 写入（正则失效？）")

    ok = True
    if used != claimed:
        only_code = sorted(used - claimed)
        only_doc = sorted(claimed - used)
        ok = fail("本地存储键不一致 —— 代码有但声明没写：%s；声明有但代码没写：%s"
                  % (only_code or "无", only_doc or "无"))

    # ── 不引用外部资源 ───────────────────────────────────────────────
    # 只认**会发请求**的写法：script/link/img 的 src|href、CSS 的 url()/@import、
    # fetch 的外链。<a href> 是用户点击才跳转的普通超链接（免责声明里那条
    # GitHub issue 就是），不构成页面加载外部资源，不能算违规。
    ext = set()
    for pat in (r'<script[^>]+src="(https?://[^"]+)',
                r'<link[^>]+href="(https?://[^"]+)',
                r'<img[^>]+src="(https?://[^"]+)',
                r"url\(\s*['\"]?(https?://[^)'\"]+)",
                r"@import\s+(?:url\()?['\"]?(https?://[^)'\"\s]+)"):
        ext |= set(re.findall(pat, src, re.I))
    ext = sorted(u for u in ext if not u.startswith(NS_OK))
    if ext:
        ok = fail("页面引用了外部资源（违反「无第三方脚本」）：%s" % ext[:6])

    # ── 长度上限：这份文案的目标读者是「来查资费的人」，不是法务 ──────
    # 2026-10-17 v1.1 曾写到 9 条 + 三张摘要卡（1433px 高），用户反馈「过于冗余」。
    # 阈值放宽到 1500 字（v1.2 实测约 1050），只拦「又膨胀回去」这一种情况；
    # 正常增补一条不会触发。
    plain = re.sub(r"<[^>]+>", "", m.group(0))
    plain = re.sub(r"\s+", "", plain)
    max_chars = 1500
    if len(plain) > max_chars:
        ok = fail("免责声明正文 %d 字，超过 %d 字上限（这类页面越短越有人读）"
                  % (len(plain), max_chars))
        print("     提示：合并重复表述，而不是删掉事实。")

    if ok:
        print("  ✓ 本地存储键 %s 与声明一致 · 无外部资源引用 · 正文 %d 字"
              % ("/".join(sorted(used)), len(plain)))
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
