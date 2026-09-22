#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国广电「资费公示」数据源适配器 —— 把 probes/he_cbn_tariff.py 的采集结果
归一化成与移动（nrapigate）/ 联通（queryTariffNew）**同构**的 ``{groups, entries}``。

为什么要有这一层：与 unicom_monitor.py 完全同理 —— tariff_monitor.py 的
``rows_of()`` / ``index_rows()`` / ``diff_rows()`` 全是照着移动那套字段名写的，
广电的字段名与服务端语义都不同，但**语义能一一对上**。与其在页面侧到处写
「如果是广电就……」，不如在这里一次性抹平（探针里的 ``normalize()`` 已经把
字段名换成了移动那套，这里只做「分组 + 声明」。

对接关系（完整表见 probes/he_cbn_tariff.py 的 ``normalize()``）：
  productName→name/tariffName   productPrice/100→fees（🔴 接口单位是**分**）
  domesticTraffic→data          domesticTrafficUnit→dataUnit
  domesticCall→call             applicablePeople→applicablePeople
  saleChannel→channel           onlineDay/offlineDay→onlineDay/offineDay（转 8 位）
  filingNumber→reportNo         rights/otherContent→otherContent
  tariffAttr→**extraFees**（套外资费，不是分类号！）  validPeriod→validPeriod
  bandwidth→brandwidth          parentTypeCode→type2（GZ_TC_5G 这类，非数字）

★ 与另两网最大的不同：**不需要抓包**。这是公开的 H5 公示页（m.10099.com.cn/costNotice/），
  打开 DevTools 就能看到全部接口；App 反而抓不到（Fiddler 侧 164 次
  `unreachable proxy`，一条会话都没留下）。见 probes/he_cbn_tariff.py 文末。

★ **两份数据都要采**：``applicableArea`` 是真的分级，而且
  `ZZZZ`（全国）与 `HB00`（河北）**交集为 0** —— 不是包含关系，各是各的。
  只采一份就少一半。探针里有 ``check_area_split()`` 每次抽查这个前提。

★ **在售口径用服务端状态位** ``stateFlag == "1"``（默认只采在售），
  与移动的 ``isPublic=1``、联通的「排除停售套餐一级」对齐；别自己解析日期。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(BASE))      # cloud/tariff → cloud → 仓库根
sys.path.insert(0, os.path.join(REPO, "probes"))

try:
    import he_cbn_tariff as C
except Exception as _e:                            # pragma: no cover
    C, _C_ERR = None, _e
else:
    _C_ERR = None

SRC = "中国广电「资费公示」H5（m.10099.com.cn / queryTariffAllByCond）"


def regroup(entries):
    """把扁平 entries 按 (tariffAttr, 一级分类) 收成移动那套 groups 结构。

    ★ 一条资费只归一处。采集侧已按 reportNo 去过重，这里再用 seen 兜一次底 ——
      同一条若落进两个 group，index_rows 会给出「键」与「键#2」两条，
      每日变更检测随即凭空报出「新增 1 / 下线 1」的假变更。

    ★ 分组键用 **type2 代码**（`GZ_TC_5G` 这种）而不是中文名：广电的两级菜单里
      `GZ_TC_KD` 与 `GZ_TSQTTC_KD` **同名都叫「宽带」**（`GZ_TC_GH` / `GZ_TSQTTC_GH`
      同样都叫「固话」）。按代码分组两者各是各的组；页面类型下拉是
      `[...new Set(rs.map(d=>d.ty))]` 去重的，所以「宽带」在 UI 上仍只出现一次、
      勾选后两类都命中 —— 这正是想要的（筛选正确，只是两类合看）。
      若按中文名分组，两组会被强行合并，`type2` 代码就丢了。
    """
    groups, seen = {}, set()
    for e in entries:
        rn = str(e.get("reportNo") or "").strip()
        k = rn or ("_" + str(id(e)))
        if k in seen:
            continue
        seen.add(k)
        key = (str(e.get("tariffAttr") or ""), str(e.get("type2") or ""))
        g = groups.setdefault(key, {"type2": key[1],
                                    "type2Name": e.get("type2Name") or "其他",
                                    "tariffAttr": key[0], "entries": []})
        g["entries"].append(e)
    return [groups[k] for k in sorted(groups)]


def fetch_all(include_stopped=False):
    """采集 + 归一化。返回与 ``tariff_monitor.fetch_all()`` 同构的对象；失败返回 None。"""
    if C is None:
        raise RuntimeError("probes/he_cbn_tariff.py 导入失败：%s" % _C_ERR)
    # check_drift 保持开启：它每次抽查「全国 ∩ 河北 = ∅」这个前提（两条请求，很便宜）。
    # 上游哪天把河北改成「包含全国」，再两份相加就会**静默翻倍** —— 变更检测会
    # 一口气报出上百条新增，而没人会想到是地区语义变了。
    raw = C.collect(include_stopped=include_stopped, check_drift=True, verbose=False)
    if not raw:
        return None
    ent = raw.get("entries") or []
    return {"province": raw.get("province"), "provinceName": raw.get("provinceName"),
            "fetchedAt": raw.get("fetchedAt"), "endpoint": raw.get("endpoint"),
            "src": SRC, "areaStat": raw.get("areaStat"),
            # 落进快照，好让事后（探针 / 排障）能分辨「这批没采下架」是
            # 「开关没开」还是「上游确实没有」—— 少了它只能靠猜。
            "includeStopped": bool(raw.get("includeStopped") or include_stopped),
            # ★ 声明「本网数据不分城市」—— 同 unicom_monitor，理由见那边注释。
            #   广电的更彻底：接口只有「全国 / 河北省」两档地区，条目里**没有地市字段**，
            #   页面拿名称文本去猜地市只会得到 0 条（拿不存在的维度在筛）。
            "allProvince": True,
            "groups": regroup(ent), "entries": ent}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    o = fetch_all("--all" in sys.argv)
    if not o:
        print("采集失败")
        sys.exit(1)
    print("广电：%d 条 · %d 组 · %s" % (len(o["entries"]), len(o["groups"]), o["fetchedAt"]))
    print("  地区分布：%s" % (o.get("areaStat") or {}))
    for g in o["groups"]:
        print("   attr=%s type2=%-16s %-8s %4d 条"
              % (g["tariffAttr"], g["type2"], g["type2Name"], len(g["entries"])))
    # 要落盘就重定向 stdout —— 别在这里拼相对路径落文件：
    # 层级算错一级就会写进仓库里污染 git status（unicom_monitor 初版就这么错过一次）。
