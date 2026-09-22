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

★ 只采**一个城市**：实测河北联通资费与 cityId 无关（石家庄/邢台/唐山 的三级菜单与明细
  逐条相同）。探针里有 ``city_drift()`` 每次抽查这个前提，一旦上游改成按城市分数据会报警。
★ 默认**排除「停售套餐」**（一级分类 99）：实测那类 3879 条里 3874 条 endDate 已过期，
  属历史资费；移动那套用 isPublic=1 本身也不含停售，排除它两网口径才一致。
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


def fetch_all(workers=6, city=None):
    """采集 + 归一化。返回与 ``tariff_monitor.fetch_all()`` 同构的对象；失败返回 None。"""
    if U is None:
        raise RuntimeError("probes/he_unicom_tariff.py 导入失败：%s" % _U_ERR)
    raw = U.collect(city or U.CITY, workers=workers)
    if not raw:
        return None
    ent = raw.get("entries") or []
    return {"province": raw.get("province"), "provinceName": raw.get("provinceName"),
            "fetchedAt": raw.get("fetchedAt"), "endpoint": raw.get("endpoint"),
            "src": SRC, "cityId": raw.get("cityId"), "cityName": raw.get("cityName"),
            # ★ 声明「本网数据不分城市」—— 页面据此把该网全部条目按「全省通用」处理。
            #   实测河北联通换 cityId 查到的三级菜单与明细**逐条相同**，cityId 只影响
            #   「能不能办」，不影响「有什么」。不声明的话，页面会拿名称文本去猜地市，
            #   结果是「选任何地市都 0 条」—— 一个看起来像 bug、其实是拿不存在的
            #   维度去筛的错。声明了就不会有人再去猜。
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
