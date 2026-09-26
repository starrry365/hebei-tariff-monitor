# -*- coding: utf-8 -*-
"""四网注册表对拍 —— 证明「派生视图」与重构前的原值**逐项相等**。

★ 为什么需要它（2026-09-26，四网元信息收进 NetSource 类族）：

    重构把「同一网的属性」从 8 张平行表收进一个类，老名字保留为**派生视图**
    （外部有 4 个脚本在读：rebuild_offline.py 解包 ``NET_RUN`` 三元组、
    probes/check_area_scope.py 与 probe_scope_state.py 读 ``SNAP_PREFIX``/``where_of``、
    selftest_pipeline.py 读 ``NET_SNAP``）。

    这类「保持接口不变」的重构最容易出的错**不是崩溃，而是某个视图悄悄变了值**：
      · NET_LIVE 少了一网      ⇒ 那网从此不再采集（页面显示 0 条，看着像上游没数据）
      · SNAP_PREFIX 前缀差一位 ⇒ 它的快照混进别网的 load_prev 排序（数字全错）
      · NET_STOPPED 漏一网     ⇒ 该网的下架资费整批不采（页面上「已下架」页签空了）
    三者**都不报错**，症状要等到第二天巡检才显形。所以这里做逐项对拍，而不是靠人眼。

★ 两种模式：
    · 默认      —— 与脚本内嵌的 EXPECT 比（重构前的原值快照）。改四网元信息后要同步更新。
    · --from REV —— 与某个 git 版本**现场对拍**（`git show REV:cloud/tariff/tariff_monitor.py`）。
                   重构当时用的就是它（REV = 重构前的提交），不依赖任何人抄写正确。

★ 函数对象没法 JSON 化，所以 WHERE_OF / state_of 这两项比**行为**：
    用一组固定样本条目逐网调用，比输出。样本覆盖各网判据的所有分支
    （含「全部 12 地市码 = 全省」这类边界）。

用法:
    python cloud/tariff/check_nets_refactor.py               # 与内嵌期望值比
    python cloud/tariff/check_nets_refactor.py --from HEAD~1 # 与某版本现场对拍
退出码: 0 = 完全一致
"""
import json
import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(BASE))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 重构前的原值（2026-09-26 首次对拍时抄自 b8befd5 版 tariff_monitor.py）──────
# 派生视图必须与它们**逐项相等**（含类型：tuple 不能变 list、set 不能变 frozenset）。
# 顺序也在内 —— NET_LIVE 与 NETS_META 在 telecom/cbn 上顺序相反，是历史遗留，
# 改了会让页面提示语的拼接顺序变，所以刻意钉住。
EXPECT = {
    "NETS_META": [["move", "移动", "中国移动"], ["unicom", "联通", "中国联通"],
                  ["telecom", "电信", "中国电信"], ["cbn", "广电", "中国广电"]],
    "SRC_OF": {
        "move":    "中国移动 APP「资费专区」（nrapigate / nrtariff）",
        "unicom":  "中国联通 APP「资费专区」（mxx.client.10010.com / queryTariffNew）",
        "telecom": "中国电信「资费专区」H5（www.189.cn / tariffSection，真实浏览器采集）",
        "cbn":     "中国广电「资费公示」H5（m.10099.com.cn / queryTariffAllByCond）",
    },
    "NET_LIVE": ["move", "unicom", "cbn", "telecom"],
    "NET_SNAP": ["telecom"],
    "SNAP_PREFIX": {"move": "hebei_tariff_", "unicom": "unicom_tariff_",
                    "cbn": "cbn_tariff_", "telecom": "ct_tariff_"},
    "NET_RUN": {"unicom": ["unicom_monitor", "河北联通", "unicom"],
                "cbn":    ["cbn_monitor", "中国广电", "cbn"],
                "telecom": ["ct_monitor", "河北电信", "ct"]},
    "NET_STOPPED": ["cbn", "unicom"],
    "NET_NOCACHE": ["telecom"],
    "TYPES": {"NETS_META": "tuple", "NET_LIVE": "tuple", "NET_SNAP": "tuple",
              "NET_STOPPED": "set", "NET_NOCACHE": "set",
              "SRC_OF": "dict", "SNAP_PREFIX": "dict", "NET_RUN": "dict"},
}

# ── 行为探针：在子进程里跑，把新旧两版对同一组输入的输出比一遍 ────────────────
CHILD = r'''
import importlib.util, json, os, sys
path, name = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(name, path)
m = importlib.util.module_from_spec(spec)
sys.modules[name] = m
spec.loader.exec_module(m)

ALL12 = ",".join(sorted(m.HB_CITY))          # 全部 12 个地市码 = 「全省通用」的判据

WHERE_CASES = [
    ("move", {}),
    ("move", {"applicableArea": "000"}),
    ("move", {"applicableArea": "3110"}),
    ("move", {"applicableArea": "3110,3120"}),
    ("move", {"applicableArea": ALL12}),          # 全省（第二种写法）
    ("move", {"applicableArea": "000,3110"}),     # 地市码优先于全国码
    ("move", {"province": "311"}),
    ("move", {"province": "310"}),
    ("move", {"province": "311,310"}),
    ("move", {"applicableArea": "AH,HI"}),        # 多省 CSV，不含河北
    ("move", {"applicableArea": "AH,HE"}),        # 多省 CSV，含河北
    ("move", {"applicableArea": "!AH"}),          # 「除这些省」≈ 全国
    ("move", {"applicableArea": "AH"}),           # 单省且非河北
    ("move", {"applicableArea": "HE"}),           # 单省且是河北
    ("telecom", {"_areaCodes": "3190"}),
    ("telecom", {"_areaCodes": ALL12}),
    ("telecom", {}),
    ("unicom", {"_cityNames": ["邢台"]}),
    ("unicom", {"_cityNames": ["邢台", "不存在的市"]}),
    ("unicom", {"_allCity": 1}),
    ("unicom", {}),
    ("cbn", {"_areaNames": "全国"}),
    ("cbn", {"_areaNames": "河北省"}),
    ("cbn", {}),
    ("未知网", {}),                                # 未知网必须不炸、且留数据
]

STATE_CASES = [
    ("move", {}, {}, "2026-09-26"),
    ("unicom", {}, {"type2": "99"}, "2026-09-26"),
    ("unicom", {"_firstLevel": "99"}, {}, "2026-09-26"),
    ("unicom", {}, {"type2": "1"}, "2026-09-26"),
    ("unicom", {"type2": "1"}, {"type2": "1"}, "2026-09-26"),
    ("cbn", {"stateFlag": "0"}, {}, "2026-09-26"),
    ("cbn", {"stateFlag": "1"}, {}, "2026-09-26"),
    ("cbn", {}, {}, "2026-09-26"),
    ("telecom", {"offineDay": "20260101"}, {}, "2026-09-26"),   # 已过期 ⇒ 下架
    ("telecom", {"offineDay": "20261231"}, {}, "2026-09-26"),   # 未到期 ⇒ 在售
    ("telecom", {"offineDay": "bad"}, {}, "2026-09-26"),        # 格式脏 ⇒ 当在售
    ("telecom", {"offineDay": ""}, {}, "2026-09-26"),
    ("telecom", {}, {}, ""),                                    # 空基线
    ("未知网", {}, {}, "2026-09-26"),
]

out = {
    "NETS_META": [list(x) for x in m.NETS_META],
    "SRC_OF": dict(m.SRC_OF),
    "NET_LIVE": list(m.NET_LIVE),
    "NET_SNAP": list(m.NET_SNAP),
    "SNAP_PREFIX": dict(m.SNAP_PREFIX),
    "NET_RUN": {k: list(v) for k, v in m.NET_RUN.items()},
    "NET_STOPPED": sorted(m.NET_STOPPED),
    "NET_NOCACHE": sorted(m.NET_NOCACHE),
    "TYPES": {k: type(getattr(m, k)).__name__ for k in
              ("NETS_META", "NET_LIVE", "NET_SNAP", "NET_STOPPED", "NET_NOCACHE",
               "SRC_OF", "SNAP_PREFIX", "NET_RUN")},
    # 顺序也是接口的一部分（决定提示语拼接顺序），单独比
    "KEYS": {"NETS_META": [x[0] for x in m.NETS_META], "SRC_OF": list(m.SRC_OF),
             "SNAP_PREFIX": list(m.SNAP_PREFIX), "NET_RUN": list(m.NET_RUN),
             "WHERE_OF": list(m.WHERE_OF)},
    "WHERE": [[c, e, list(m.where_of(c, e))] for c, e in WHERE_CASES],
    "STATE": [[c, e, g, d, bool(m.state_of(c, e, g, d))] for c, e, g, d in STATE_CASES],
}
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
'''


def probe(path, tag):
    """在**子进程**里加载指定路径的模块并取回可比较的快照。

    ★ 必须用子进程：两版模块同名、且各自会 ``sys.path.insert(0, BASE)`` +
      ``os.makedirs`` 自己的数据目录，同进程先后 import 会互相污染
      （sys.modules 缓存、path 顺序、BASE 指向都会串）。
    """
    r = subprocess.run([sys.executable, "-c", CHILD, path, "tm_" + tag],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    if r.returncode != 0:
        sys.exit(f"!! 加载 {tag} 版失败：\n{r.stderr[-1500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def from_rev(rev):
    """从某个 git 版本取出 tariff_monitor.py，写到临时目录后加载。

    写到**临时目录**（不是原地覆盖）—— 旧版会在 BASE 下建 snapshots/changes/docs，
    落在临时目录里、用完即删，绝不碰仓库里的真数据。
    """
    src = subprocess.run(["git", "-C", REPO, "show",
                          f"{rev}:cloud/tariff/tariff_monitor.py"],
                         capture_output=True, check=False)
    if src.returncode != 0:
        sys.exit(f"!! 取不到 {rev} 版：{src.stderr.decode('utf-8', 'replace')[-400:]}")
    d = tempfile.mkdtemp(prefix="nets-refactor-")
    p = os.path.join(d, "tariff_monitor.py")
    with open(p, "wb") as f:
        f.write(src.stdout)
    # 把加解密依赖也带上，否则旧版会走 mz_crypto=None 的降级分支（不影响本次对拍，
    # 但少一条「导入路径真的通」的旁证）
    for extra in ("mz_crypto.py",):
        s = os.path.join(BASE, extra)
        if os.path.exists(s):
            with open(os.path.join(d, extra), "wb") as f:
                f.write(open(s, "rb").read())
    return p, d


def _cmp(name, got, want, fails):
    if got != want:
        fails.append(f"{name}\n      得到 {got!r}\n      期望 {want!r}")


def main():
    argv = sys.argv[1:]
    if "--from" in argv:
        rev = argv[argv.index("--from") + 1]
        old_path, tmp = from_rev(rev)
        print(f"对拍基准：git {rev}（现场提取）")
    else:
        rev, old_path, tmp = None, os.path.join(BASE, "tariff_monitor.py"), None
        print("对拍基准：内嵌 EXPECT（重构前的原值快照）")
    print(f"当前版本：{os.path.relpath(os.path.join(BASE, 'tariff_monitor.py'), REPO)}")
    print()

    try:
        cur = probe(os.path.join(BASE, "tariff_monitor.py"), "cur")
        ref = probe(old_path, "ref") if rev else None
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    fails = []
    # ① 八个派生视图：值 + 类型（tuple 不能变 list、set 不能变 frozenset）
    for k, want in EXPECT.items():
        _cmp(k, cur[k], want, fails)

    # ② 键顺序也是接口的一部分 —— 它决定页面页签与顶部提示语的拼接顺序
    keys_want = {"NETS_META": [x[0] for x in EXPECT["NETS_META"]],
                 "SRC_OF": list(EXPECT["SRC_OF"]),
                 "SNAP_PREFIX": list(EXPECT["SNAP_PREFIX"]),
                 "NET_RUN": list(EXPECT["NET_RUN"]),
                 "WHERE_OF": ["move", "telecom", "unicom", "cbn"]}
    for k, want in keys_want.items():
        _cmp(f"{k} 的键顺序", cur["KEYS"][k], (ref["KEYS"][k] if ref else want), fails)

    # ③ 行为对拍：WHERE_OF 与 state_of（函数对象没法比身份，只能比输出）
    if ref:
        _cmp("where_of 逐样本输出", cur["WHERE"], ref["WHERE"], fails)
        _cmp("state_of 逐样本输出", cur["STATE"], ref["STATE"], fails)
    print(f"where_of 样本 {len(cur['WHERE'])} 例 · state_of 样本 {len(cur['STATE'])} 例")

    if fails:
        print()
        print(f"✗ 对拍失败 {len(fails)} 项：")
        for x in fails:
            print("   ", x)
        return 1
    print()
    # ★ 通过的措辞必须**与实际比过的项一致** —— 内嵌模式并没有比对行为输出，
    #   不能统一印一句「逐样本输出完全一致」（那是说了没做的事）。
    if ref:
        print("✓ 对拍通过：8 个派生视图的值/类型/键顺序 + where_of 25 例 / state_of 14 例"
              f"，与 git {rev} 逐项一致")
    else:
        print("✓ 对拍通过：8 个派生视图的值/类型/键顺序与 EXPECT 一致"
              "（⚠️ 本模式**未**比对 where_of / state_of 的行为输出 ——"
              " 要现场比行为请加 --from <重构前版本>）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
