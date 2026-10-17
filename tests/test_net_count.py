# -*- coding: utf-8 -*-
"""「本网几条」这条判据的回归测试（离线、秒级）。

背景（2026-10-15 分源拆分之后）：四网 rows 改成按需加载，于是**启动那一刻
四网都没数据**。而页面里有两处必须在数据到位前就回答「本网有没有数据 / 几条」：

  · 网别导航的「N 条」徽章；
  · 默认落地哪个网（NET_DEF = 第一个「已接入」的网）—— 它是移动那网抓挂时的
    唯一退路，写死成 move 的话，一进页面就是一片空白且没有任何提示。

这两处原来都是数 ``rowsOf()``。拆分之后数出来一律是 0：徽章全写「0 条 /
尚未接入」，退路退化成 NET_LS[0]。**页面上看不出坏**（正常轮次四网都有数据，
结果恰好还是 move），所以只能靠测试钉住。

本测试锁三条：
  ① 元信息 n 由 rows 现算（net_payload）—— 它是上面两处唯一可用的答案来源；
  ② 页面不得再出现「n 有就用 n、否则数 rowsOf」这种**就地内联**的判据
     （两处各写一遍就会各错一次），统一走 netCount()；
  ③ online() 必须基于 netCount()，不能数 rows。

用法：python tests/test_net_count.py   # 退出码 0 = 通过
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cloud", "tariff"))
import tariff_monitor as tm                     # noqa: E402

TPL = open(os.path.join(ROOT, "cloud", "tariff", "template.html"),
           encoding="utf-8").read()


def check(msg, cond):
    if not cond:
        raise AssertionError(msg)
    print("  ✓ " + msg)


def main():
    # ① 元信息 n 与 rows 同源
    payload = tm.net_payload({
        "move": {"rows": [{"t": "甲"}, {"t": "乙"}], "base": "2026-10-15",
                 "ts": "2026-10-15 06:12", "src": "x"},
        "unicom": {"rows": [], "base": "2026-10-15", "ts": "2026-10-15 06:13",
                   "src": "y"},
    })
    check("net_payload 的 n = rows 行数（移动 2 条）", payload["move"]["n"] == 2)
    check("空网的 n = 0（不是缺键）", payload["unicom"]["n"] == 0)

    # ② 判据只有一个出口
    check("存在 netCount() 单一出口", "function netCount(" in TPL)
    check("netCount 优先取元信息 n",
          re.search(r"if\(N\.n!=null\)return N\.n\|0", TPL) is not None)
    check("netCount 在没有 n 的老页面上退回数 rows",
          re.search(r"function netCount\([\s\S]{0,220}rowsOf\(c\)\.length", TPL)
          is not None)
    check("徽章走 netCount（不再就地写 n!=null?…:rowsOf）",
          "netCount(c)+" in TPL and "n.n!=null?n.n:rowsOf(c).length" not in TPL)

    # ③ online 不得数 rows
    check("online 基于 netCount",
          re.search(r"const online=n=>netCount\(n\)>0", TPL) is not None)
    check("online 不再直接数 rowsOf",
          re.search(r"const online=n=>rowsOf\(n\)", TPL) is None)

    # ④ 退路本身还在（别哪天被顺手删掉）
    check("NET_DEF 仍取「第一个已接入的网」，不是写死 move",
          re.search(r"NET_DEF=\(NET_LS\.filter\(online\)\[0\]\)\|\|NET_LS\[0\]",
                    TPL) is not None)

    print("\n全部通过：本网条数判据单源、启动期可答、默认网退路完好")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
