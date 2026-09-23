# -*- coding: utf-8 -*-
"""离线重建多网页面：移动读当日快照，其余各网读本地缓存（首次或 --fresh 时采集）。

**为什么需要它**（而不是直接跑 `tariff_monitor.py`）：
完整 `main()` 会写快照、写 `changes/<今天>.md`、覆盖 `state.json`。
云端每天 06:00 已经跑过一轮，本地为了「看一眼效果」再补跑一次，会以
**本地那份基准**生成一份不一样的变更报告，把云端的真实结果盖掉。

**它和 `--render-only` 的区别**：
`--render-only` 从现有页面容器里抽数据重渲染（数据是死的，已归档的那一刻）；
本脚本**真的去采**（但**不落快照**）—— 改了适配器的归一化 / 字段映射之后，
要验证效果就必须重新采，那时用它。

⚠️ `--render-only` 还**只对页面里已有数据的网有意义**：新接入一网时页面里
   根本没有它，只能走本脚本；反过来，用 `--render-only` 重建反而会把
   新接的那网**从页面里弄丢**（它按现有容器重建）。

```bash
python rebuild_offline.py              # 全部用缓存重建（秒级）
python rebuild_offline.py --fresh      # 强制重采「非移动」的全部网并更新缓存
python rebuild_offline.py --fresh cbn  # 只重采广电，其余仍用缓存（改了一个适配器时最省事）
```

⚠️ 缓存只是**迭代便利**，不是真相来源：正式发布与每日巡检仍走
`net_round()` 的实时采集 + 快照链路。
⚠️ 本脚本**只调 `build_html` 且 archive=False**：不碰 `snapshots/` `changes/`
`state.json`，也**不覆盖入库的官方归档 `page/index.html.gz`** ——
那是「与快照同源」的那一份，用本地缓存重建的结果盖掉它，归档就再也无法自证了。
需要刷新归档走 `tariff_monitor.py --render-only`（数据取自官方页面产物）。
"""
import io
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tariff_monitor as T      # noqa: E402

argv = sys.argv[1:]
fresh = "--fresh" in argv
# --fresh 后面可以跟网名，只重采列出的那几网（其余仍读缓存）。
# 不带网名 = 全部重采。名字从 NET_RUN 取，写错了就是个空表（= 全部重采），不会静默漏采。
only = [a for a in argv if not a.startswith("-") and a in T.NET_RUN]


def cache_path(code):
    """缓存按网一份（与 SNAP_PREFIX 同理：混在一起会互相盖）。"""
    return os.path.join(BASE, "." + code + "_cache.json")


# 「99999999」= 未来的日子 ⇒ load_prev 取「今天之前最近的一份」，即当日那份。
mv, p = T.load_prev("99999999")
if not mv:
    sys.exit("!! 找不到移动快照，先跑一次 tariff_monitor.py")
print("移动快照:", os.path.basename(p or ""), "·",
      sum(len(g["entries"]) for g in mv["groups"]), "条")

srcs = {"move": mv}
for code, (mod_name, cn, _tag) in T.NET_RUN.items():
    path = cache_path(code)
    # ★ 适配器若是**纯本地转换**（电信：读浏览器采集产物，不发网络请求），
    #   一律不进缓存 —— 缓存永远不会失效，采集产物更新后被缓存挡住是**静默**的
    #   （2026-09-23 实测踩到：.telecom_cache.json 停在 09-22）。
    nocache = code in T.NET_NOCACHE
    # 只要列了网名就只重采列出来的；没列（--fresh 单独用）就全部重采
    want_fresh = fresh and (not only or code in only)
    d = None
    if nocache:
        print(f"{cn}：纯本地转换网，跳过缓存直读适配器（{path} 已废弃，可删）")
    elif not want_fresh and os.path.exists(path):
        try:
            d = json.load(io.open(path, encoding="utf-8"))
            print(f"{cn}缓存:", len(d.get("entries") or []), "条 ·", d.get("fetchedAt"))
        except Exception as e:
            print(f"{cn}缓存不可用（{type(e).__name__}: {e}），改为采集")
            d = None
    if d is None:
        t0 = time.time()
        mod = __import__(mod_name)
        # ★ 与 net_round 的口径**逐字保持一致**：支持 include_stopped 的网要传 True，
        #   否则本地重建出来的页面会少掉整批下架资费 —— 而它与线上页面的差异
        #   不会有任何提示，看起来只是「今天少了一些条目」。
        d = (mod.fetch_all(include_stopped=True) if code in T.NET_STOPPED
             else mod.fetch_all())
        if not d:
            sys.exit(f"!! {cn}采集失败")
        if not nocache:
            io.open(path, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
        print(f"{cn}采集: {len(d['entries'])} 条 · {time.time() - t0:.0f}s"
              + ("" if nocache else "（已缓存）"))
    srcs[code] = d

def count_of(code, o):
    """条数：移动那网的快照是 {groups:[{entries:[]}]}，其余各网是扁平 {entries:[]}。

    统一取 entries 会在移动那网恒返回 0 —— 打印成「move 0 条」看着像没接上数据，
    其实只是形状不同。别让一句汇总把人对整条链路失去信心。
    """
    if not o:
        return 0
    if o.get("entries") is not None:
        return len(o["entries"])
    return sum(len(g.get("entries") or []) for g in o.get("groups") or [])


nets = " · ".join("%s %d 条" % (c, count_of(c, srcs.get(c)))
                  for c in ("move",) + tuple(T.NET_RUN))
# 🔴 archive=False：**绝不覆盖入库的官方归档** page/index.html.gz。
#   本脚本的数据来自本地采集缓存（不入库），而归档进 git 的那一份必须与
#   snapshots/ 同源 —— 否则归档无法自证，而改动是静默的（只表现为 git 里一个二进制变化）。
n = T.build_html(srcs,
                 f"本机重建：移动取当日快照 · 其余各网为本地缓存或实时采集（{nets}）", None,
                 archive=False)
print("已完成，合计 %d 条（%s）" % (n, nets))
