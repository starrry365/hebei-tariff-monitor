# -*- coding: utf-8 -*-
"""归档内联 HIST 的转义回归测试（离线、秒级）。

背景（2026-10-17 事故）：``inline_hist`` 最初用**字符串**做 ``re.sub`` 的替换值，
而 ``re.sub`` 的替换串会再解释一遍反斜杠转义 —— ``js_json`` 输出里的 ``"\\n"``
（JSON 的换行转义）被还原成**裸换行**，内联进 ``<script>`` 后就是「字符串字面量
跨行」⇒ 整页语法错误。表现：解包归档后页面只剩静态骨架（rows=0、时间线空），
而占位符检查、NETS 字节比对全部照常通过 —— 只有真在浏览器里打开才看得见。

本测试锁死三条不变量：
  ① 内联后 ``const HIST_SRC=""``（归档自包含标记）；
  ② 内联数据是**合法 JSON**（裸换行会让 json 解析失败）；
  ③ 往返内容一致（原文里的换行 / ``<`` / ``&`` 一个字符都不能变）。
第二、三条正是 re.sub 吃转义时会同时破掉的东西。

用法：python tests/test_hist_inline.py   # 退出码 0 = 通过
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cloud", "tariff"))
import tariff_monitor as tm                     # noqa: E402

# 覆盖三种「会踩到转义」的字符：换行、尖括号（js_json 会转成 \u003c）、&
ITEMS = [
    {"ts": "2026-10-17 06:12:00", "d": "2026-10-17", "code": "move",
     "net": "河北移动", "n": 5266, "a": 1, "r": 0, "c": 2,
     "smp": [{"k": "add", "n": "第1条\n第2条", "x": "<p>权益</p> & 备注"}]},
    {"ts": "2026-10-16 06:12:00", "d": "2026-10-16", "code": "unicom",
     "net": "河北联通", "n": 3177, "a": 0, "r": 1, "c": 0, "smp": []},
]


def main():
    old_docs = tm.DOCS
    tmp = tempfile.mkdtemp(prefix="hist_inline_")
    try:
        tm.DOCS = tmp
        tm.DATA_DIR = os.path.join(tmp, "data")
        rel = tm.write_hist_sidecar(ITEMS)

        page = ('<html><script>\nconst HIST_SRC="%s";\nlet HIST=null;\n'
                'function loadHist(){}\n</script></html>' % rel)
        out = tm.inline_hist(page)

        # ① 自包含标记
        assert 'const HIST_SRC=""' in out, "HIST_SRC 未置空（归档仍指向 sidecar）"

        # ② 合法 JSON：re.sub 吃转义后这里会抛 JSONDecodeError
        i = out.index("let HIST=") + len("let HIST=")
        got = json.JSONDecoder().raw_decode(out[i:])[0]

        # ③ 内容往返一致
        assert got == ITEMS, "内联数据与源数据不一致（转义被改动）"
        txt = out[i:out.index(";", i)]
        assert "\n" not in txt, "内联 JSON 里出现裸换行 —— 会让 <script> 语法错误"
        assert "\\u003c" in txt, "js_json 的 < 转义丢失（内联 <script> 有提前闭合风险）"

        print("hist 内联回归通过：%d 条 · 合法 JSON · 换行/尖括号/& 往返一致"
              % len(got))
        return 0
    finally:
        tm.DOCS, tm.DATA_DIR = old_docs, os.path.join(old_docs, "data")
        try:
            for f in os.listdir(os.path.join(tmp, "data")):
                os.remove(os.path.join(tmp, "data", f))
            os.rmdir(os.path.join(tmp, "data"))
            os.rmdir(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
