#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联通「资费专区」数据源适配器 —— 把 probes/he_unicom_tariff.py 的采集结果
归一化成与移动（nrapigate）**同构**的 ``{groups, entries}`` 结构。

为什么要有这一层：
  tariff_monitor.py 的 ``rows_of()`` / ``index_rows()`` / ``diff_rows()`` 全是照着
  移动那套写的（``g["type2"]`` / ``g["tariffAttr"]`` / ``e["name"]`` / 12 个 KEY_FIELDS）。
  联通接口的字段名、分组层级都不一样，但**语义能一一对上** —— 与其在页面侧到处写
  「如果是联通就……」，不如在这里一次性抹平，让两网共用同一条渲染与变更检测链路。

对接关系（完整表见 probes/he_unicom_tariff.py 的 ``FIELD_MAP``）：
  feesStandard→fees   commonData→data     minute→call         useScope→applicablePeople
  saleChnl→channel    startDate→onlineDay  endDate→offineDay   serviceContent→otherContent
  extraFees→extraFees validPeriod→validPeriod                  broadBand→brandwidth
  一级分类号→type2（1..5 与移动 ZFLX **同名同义**，故页面「类型」列两网一致）

★ 采**12 个地市的并集**（2026-09-24 修正，此前写的是「只采一城」）。
  🔴🔴 旧结论「河北联通资费与 cityId 无关」**已证伪** —— 那是**抽样假阴性**：
  旧 ``city_drift()`` 只抽了 (套餐/移网) 与 (加装包/权益包) 两个组合，而这两个
  **恰好全省一致**。全量实测：22 个 (一级×二级) 组合里 **8 个随城市变化**，
  12 个地市**每个都有专属条目**（雄安「雄安工地0元50G流量包」、沧州 41 条
  「华油专属」、保定「保定理工学院5G随行专网」…）；单城(邢台)三级目录 8991 个
  → 12 城并集 9121 个，**单城静默漏 130 个**（不报错、只是少）。
  现在由 ``city_scope()`` 做全组合 × 全地市的判据（旧的 ``city_drift()`` 已转调它）。
  明细不必按城市重复拉：``operateData`` 按 id 解析、cityId 不设门槛，
  条目上带 ``cities`` 记录该资费出现在哪些地市。
★ 默认**收全「停售套餐」**（一级分类 99，实测 3092 条，endDate 全部已过期）——
  需求是「各运营商下架的资费也要收集全」，由 ``fetch_all(include_stopped=True)`` 控制
  （默认 True，见下方 ``fetch_all`` 文档），停售那批进页面的「已下架」页签，
  不与在售混在一起。⚠️ 探针 ``he_unicom_tariff.collect()`` 的默认值仍是 False，
  两者语义相反，别按探针默认值推断主链路行为。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(BASE))      # cloud/tariff → cloud → 仓库根
sys.path.insert(0, os.path.join(REPO, "probes"))

try:
    import he_unicom_tariff as U
except Exception as _e:                            # pragma: no cover
    U, _U_ERR = None, _e
else:
    _U_ERR = None

SRC = "中国联通 APP「资费专区」（mxx.client.10010.com / queryTariffNew）"


def regroup(entries):
    """把扁平 entries 按 (tariffAttr, 一级分类) 收成移动那套 groups 结构。

    ★ 一条资费只归一处。采集侧已按 reportNo 去过重，这里再用 seen 兜一次底 ——
      同一条若落进两个 group，index_rows 会给出 `键` 与 `键#2` 两条，
      每日变更检测随即凭空报出「新增 1 / 下线 1」的假变更。
    """
    groups, seen = {}, set()
    for e in entries:
        rn = str(e.get("reportNo") or "").strip()
        k = rn or ("_" + str(id(e)))
        if k in seen:
            continue
        seen.add(k)
        key = (str(e.get("_attr") or ""), str(e.get("_firstLevel") or ""))
        g = groups.setdefault(key, {"type2": key[1],
                                    "type2Name": (U.type_name(key[1]) if U else ""),
                                    "tariffAttr": key[0], "entries": []})
        g["entries"].append(e)
    return [groups[k] for k in sorted(groups)]


def fetch_all(workers=6, city=None, include_stopped=True):
    """采集 + 归一化。返回与 ``tariff_monitor.fetch_all()`` 同构的对象；失败返回 None。

    ``include_stopped`` 默认 **True**（需求：「各运营商下架的资费也要收集全」）。
    停售那批（一级分类 ``99``，实测 3092 条）由 ``tariff_monitor.state_of()`` 标成
    「已下架」、进页面的「已下架」页签，不再与在售混在一起 —— 这样既收全了，
    又不会污染在售清单的月费/流量排序与每日变更检测（它们走不同页签）。

    🔴 老注释说「99 那类 3879 条里 3874 条 endDate 已过期」——**已不成立**：
    2026-09-22 实测该批 ``endDate`` 过期条数为 **0**，也就是说下架信息只能靠
    「它被归到 99 类」，不能靠日期。判据据此改成分类归属（见 state_of）。
    """
    if U is None:
        raise RuntimeError("probes/he_unicom_tariff.py 导入失败：%s" % _U_ERR)
    raw = U.collect(city or U.CITY, workers=workers, include_stopped=include_stopped)
    if not raw:
        return None
    ent = raw.get("entries") or []
    cities = raw.get("cities") or []
    return {"province": raw.get("province"), "provinceName": raw.get("provinceName"),
            "fetchedAt": raw.get("fetchedAt"), "endpoint": raw.get("endpoint"),
            "src": SRC, "cityId": raw.get("cityId"), "cityName": raw.get("cityName"),
            "cities": cities,
            "includeStopped": include_stopped,
            # ★ 声明「采集口径已覆盖全省」—— 页面据此把该网条目按「全省」处理，不再按地市筛。
            #   🔴🔴 理由已更正（2026-09-24）：**不是因为上游没有城市维度** ——
            #   实测 22 个 (一级×二级) 组合里 8 个随 cityId 变化，12 个地市**每个都有
            #   专属条目**（雄安「雄安工地0元50G流量包」、沧州 41 条「华油专属」…），
            #   单城(邢台) 三级目录 8991 个 → 12 城并集 9121 个，**单城会静默漏 130 个**。
            #   现在的 `_cities` 字段记录了每条资费出现在哪些地市（供页面标注「仅 XX 市」）。
            #   仍声明 allProvince=True 的原因：数据已是并集，再按地市筛只会得到子集，
            #   反而漏条；页面若要展示地市归属，请读条目上的 `cities` 而不是按名猜。
            "allProvince": True,
            "groups": regroup(ent), "entries": ent}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    o = fetch_all()
    if not o:
        print("采集失败")
        sys.exit(1)
    print("联通：%d 条 · %d 组 · %s" % (len(o["entries"]), len(o["groups"]), o["fetchedAt"]))
    for g in o["groups"]:
        print("   attr=%s type2=%-3s %-12s %5d 条"
              % (g["tariffAttr"], g["type2"], g["type2Name"], len(g["entries"])))
    # 要落盘就重定向 stdout —— 别在这里拼相对路径落文件：
    # 层级算错一级就会写进仓库里污染 git status（本文件初版就这么错过一次）。
