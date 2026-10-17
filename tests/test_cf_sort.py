# -*- coding: utf-8 -*-
"""列筛选面板「清单排序」的契约测试（离线、毫秒级，需要 node）。

2026-10-17 用户报：月费面板的取值顺序是 0/20/10/15/30/5/6/100/50/19.9 元，
看不出价格梯度。查下去发现面板**只有一种序**——条数降序，写死在 cfOpen 里：

    _cfItems=Object.keys(_cfMp).sort((a,b)=> _cfMp[b]-_cfMp[a] || (a<b?-1:1))

条数序对「细分 / 目标客户」是对的，对「金额 / 容量 / 时长 / 时间档」是噪音。
补排序时最容易犯的错是**拿字符串直接比**：

    "100 元" < "19.9 元" < "20 元"      ← 不是金额序
    "1000M"  < "300M"                   ← 不是带宽序
    "500MB"  < "2TB"                    ← 差了两个数量级还被判成更小

所以每列先给一个 key（取值 → 可比较数值），没有 key 的列才退回 Collator。
这个文件做两件事：

  ① **真的跑一遍**：把 template 里 @cfsort-begin/end 之间的纯逻辑抽出来，
     交给 node 用真实取值跑 cfSortBy，断言输出序列（不是拿正则猜行为）；
  ② 静态锁住接线：排序偏好落哪个键、面板里有没有那个下拉、旧的 inline sort
     有没有被彻底换掉、勾选有没有走合并、空白是否恒沉底。

用法：python tests/test_cf_sort.py   # 退出码 0 = 通过
"""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "cloud", "tariff", "template.html")

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
    return bool(cond)


def node_bin():
    cand = [os.environ.get("NODE_BIN"), shutil.which("node"), shutil.which("nodejs")]
    cand.append(os.path.expanduser(
        r"~/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"))
    for c in cand:
        if c and os.path.exists(c):
            return c
    return None


def extract_block(src):
    m = re.search(r"/\* @cfsort-begin \*/(.*?)/\* @cfsort-end \*/", src, re.S)
    return m.group(1) if m else None


# 真实的取值形态（与页面 COLF_DEF 的 get 输出一致：「19.9 元」「500MB」「含宽带」）
JS_HARNESS = r"""
const D = {
  fee : {"0 元":838,"0.01 元":8,"0.1 元":32,"1 元":52,"10 元":388,
         "19.9 元":63,"20 元":475,"100 元":71,"0.5 元":1,"0.9 元":1,"":9},
  bw  : {"300M":30,"600M":14,"1000M":29,"2000M":26,"500M":8,
         "含宽带":13,"提速/组网":2,"":5150},
  gb  : {"500MB":1,"1GB":2,"1.5GB":3,"2TB":4,"<1MB":5,"":6},
  on  : {"待上架":1,"今天上架":2,"30 天内":3,"30 天前":4},
  off : {"已过期":1,"60 天内到期":2,"60 天以上":3,"长期":4},
  ap  : {"日本":1,"阿联酋":1,"韩国":1,"澳大利亚":1}
};
const R = {};
for (const k in D) {
  const items = Object.keys(D[k]);
  for (const m of ["count", "countr", "val", "valr"]) {
    const got = cfSortBy(k, items, D[k], m);
    R[k + "|" + m] = got;
    if (got.length !== items.length) R.err = "排序改变了条目数：" + k + "|" + m;
    const s = new Set(got);
    if (s.size !== items.length || items.some(v => !s.has(v)))
      R.err2 = "排序丢了/重复了取值：" + k + "|" + m;
  }
}
const probe = ["b", "a"];
cfSortBy("ap", probe, D.ap, "val");
R.inputIntact = JSON.stringify(probe);
const again = cfSortBy("ap", Object.keys(D.ap), D.ap, "val");
R.deterministic = JSON.stringify(again) === JSON.stringify(R["ap|val"]);
process.stdout.write(JSON.stringify(R));
"""


def run_js(block):
    nb = node_bin()
    if not nb:
        return None, "找不到 node（设 NODE_BIN 环境变量，或装 node）"
    js = block + "\n" + JS_HARNESS
    p = subprocess.run([nb, "-"], input=js.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        return None, p.stderr.decode("utf-8", "replace")[:400]
    try:
        return json.loads(p.stdout.decode("utf-8")), None
    except Exception as e:                       # noqa: BLE001
        return None, "输出不是 JSON：%s / %s" % (e, p.stdout[:300])


def seq(got, want):
    return got == want


def main():
    with open(TPL, encoding="utf-8") as f:
        src = f.read()
    block = extract_block(src)
    if not check(block, "template 里找不到 @cfsort-begin / @cfsort-end 标记对 —— "
                        "behavior 测试靠它抽取纯逻辑，标记丢了就只剩正则断言"):
        report()
        return False

    # ── ① 行为：真跑一遍排序 ────────────────────────────────────────
    R, err = run_js(block)
    if err:
        check(False, "node 执行排序逻辑失败：%s" % err)
    else:
        check(not R.get("err"), "排序改变了条目数（%s）" % R.get("err"))
        check(not R.get("err2"), "排序丢值/重值（%s）" % R.get("err2"))
        # 金额：数值序而非字符串序（"100 元" 必须排在 "19.9 元" 之后）
        check(seq(R["fee|val"], ["0 元", "0.01 元", "0.1 元", "0.5 元", "0.9 元",
                                 "1 元", "10 元", "19.9 元", "20 元", "100 元", ""]),
              "月费按金额升序不对：%s" % R["fee|val"])
        check(seq(R["fee|valr"], ["100 元", "20 元", "19.9 元", "10 元", "1 元",
                                  "0.9 元", "0.5 元", "0.1 元", "0.01 元", "0 元", ""]),
              "月费按金额降序不对：%s" % R["fee|valr"])
        # 条数序：空白必须**恒沉底**，哪怕它的条数比某个真实取值还多（这里 9 > 8）
        check(seq(R["fee|count"], ["0 元", "20 元", "10 元", "100 元", "19.9 元",
                                   "1 元", "0.1 元", "0.01 元", "0.5 元", "0.9 元", ""]),
              "条数降序不对（或空白没沉底）：%s" % R["fee|count"])
        # 同条数（0.5 元 / 0.9 元 各 1 条）必须按金额排，不能按 Collator 的
        # 整数段比 —— numeric:true 会把 "0.29" 切成 [0,'.',29]，排到 0.5 之后
        check(seq(R["fee|countr"], ["0.5 元", "0.9 元", "0.01 元", "0.1 元", "1 元",
                                    "19.9 元", "100 元", "10 元", "20 元", "0 元", ""]),
              "条数升序不对（同条数没按金额兜底？）：%s" % R["fee|countr"])
        # 带宽：1000M 必须排在 300M 之后，两个无容量档位沉在数字之后
        check(seq(R["bw|val"], ["300M", "500M", "600M", "1000M", "2000M",
                                "含宽带", "提速/组网", ""]),
              "宽带按容量升序不对：%s" % R["bw|val"])
        # 流量：单位必须归一（500MB < 1GB < 2TB），"<1MB" 最小
        check(seq(R["gb|val"], ["<1MB", "500MB", "1GB", "1.5GB", "2TB", ""]),
              "流量按容量升序不对（单位没归一？）：%s" % R["gb|val"])
        # 时间档：按语义档位而非字面
        check(seq(R["on|val"], ["待上架", "今天上架", "30 天内", "30 天前"]),
              "上架档位序不对：%s" % R["on|val"])
        check(seq(R["off|val"], ["已过期", "60 天内到期", "60 天以上", "长期"]),
              "下线档位序不对：%s" % R["off|val"])
        # 无 key 的列：只要求确定性（拼音序依赖 ICU，不同构建可能不同）
        check(R.get("deterministic") is True,
              "目标客户两次排序结果不一致 —— 面板顺序必须是确定的")
        check(R.get("inputIntact") == '["b","a"]',
              "cfSortBy 改写了入参数组（应返回副本）")

    # ── ② 静态：接线与 UI ──────────────────────────────────────────
    js = re.sub(r"/\*.*?\*/", "", src, flags=re.S)   # 剥注释，防「注释里提到」算通过
    check(re.search(r"const CFS_KEY\s*=\s*\"hbtm_cfsort\"", js),
          'CFS_KEY 不是 "hbtm_cfsort"（改名就必须同步免责声明）')
    check("hbtm_cfsort" in js and 'localStorage.setItem(CFS_KEY' in js,
          "排序偏好没有落盘（换个列再回来就丢了选择）")
    m = re.search(r'<section class="view" id="v-ab".*?</section>', src, re.S)
    check(m and "<code>hbtm_cfsort</code>" in m.group(0),
          "新增的 hbtm_cfsort 没写进免责声明 —— 用户一查开发者工具就会发现多存了东西")
    for k in ("fee", "gb", "call", "bw", "on", "off"):
        pat = r"\b%s\s*:\s*\{\s*lb" % k
        check(re.search(pat, js) is not None,
              "CF_ORD 缺 %s —— 这列会退回字符串序（100 元 排在 19.9 元 前面）" % k)
    check("Intl.Collator(\"zh-Hans-CN\",{numeric:true" in js.replace(" ", ""),
          "中文比对没用 Intl.Collator / 没开 numeric —— 名称里 \"10 元\" 会排到 \"9 元\" 前")
    check("_cfMp[b]-_cfMp[a]||(a<b?-1:1)" not in js.replace(" ", ""),
          "cfOpen 里又出现了旧的 inline 排序（_cfMp[b]-_cfMp[a]||(a<b?-1:1)）—— "
          "面板排序必须只走 cfSortBy，否则两套顺序并存")
    check(re.search(r"_cfItems\s*=\s*cfSortBy\(", js) is not None,
          "cfOpen 没通过 cfSortBy 生成清单")
    check('class="cfs"' in js and "条数 ↓" in js and "条数 ↑" in js,
          "面板工具栏没有排序下拉（.cfs / 条数与取值四态）")
    check("cfApplySoon" in js and re.search(r"cfSet\(_cfKey,\[\.\.\.s\]\);cfApplySoon\(\)", js),
          "勾选没走 cfApplySoon —— 连勾 8 个值会连卡 8 次全量重渲")
    check("未筛 · 全部" in js,
          "空勾选还写着「已勾 0 / N 项」—— 看着像结果为空，实际是全部保留")
    check(re.search(r'class="cfn" data-v=', js) is not None and "只看这一项" in js,
          "条数不可点（「只看 0 元」还得先勾它再反选）")
    check("e.preventDefault()" in js and "e.stopPropagation()" in js,
          "条数点击没拦住 label 默认行为 —— 会把 checkbox 顺手取反")
    check("cfCount()" in js,
          "勾选后计数不刷新（不重渲清单是刻意的，计数得单独维护）")
    check(re.search(r"#cfpop\{[^}]*width:320px", src) is not None,
          "面板没加宽到 320px —— 排序下拉 + 两个按钮 + 计数会挤成一行糊")

    report()
    return not FAILS


def report():
    if FAILS:
        print("  ✗ 列筛选排序契约 %d 项不通过：" % len(FAILS))
        for m in FAILS:
            print("     - %s" % m)
    else:
        print("  ✓ 排序四态 · 六列自然序（金额/容量/时长/档位） · 空白恒沉底 · 偏好落盘 "
              "· 合并勾选 · 只看此项 · 文案不自相矛盾")


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
