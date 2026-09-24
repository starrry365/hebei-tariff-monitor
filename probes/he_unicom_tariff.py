#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""河北联通「资费专区」接口探针 / 采集器

页面：https://imgxx.client.10010.com/zifeizhuanqu/   （联通 App 内嵌 H5，Vue2 + webpack）
接口家族：POST https://mxx.client.10010.com/servicequerybusiness/queryTariffNew/*

  1. indexData            provinceId, cityId
       → data.levelList[]       一级分类 {firstLevel, firstLevelName, secondLevels[]}
                                二级在 secondLevels[] 里 {secondLevel, secondLevelName}
  2. threeLevelName       tariffAttributes, firstLevel, secondLevel, provinceId, cityId
       → data.dataList[]        三级菜单 [{id, name}]，id 就是要喂给 operateData 的
       → code=0001 表示「当前目录下暂无资费信息」（正常空目录，不是错误）
  3. operateData/<ids>    page, size, provinceId, cityId
       → data.dataList[]        资费产品 {reportNo, name, firstLevelType, secondLevelType, detailsList[]}
       → data.detailList[]      平铺的明细（字段最全，见下）
  4. cityList             provinceId           河北地市列表（未在抓包里出现，本探针已补测）

★ 零凭证：**不需要 cookie、不需要登录、没有任何签名**。
  实测只带 content-type + Referer/Origin + x-requested-with 就能拿到 code=0000。
  （对比移动那套 nrapigate：body 是 AES-256-CBC 密文，要本地解密。）

★ behaviorId 是纯客户端埋点 id，**服务端不校验**：
  JS 里 = 16 位随机（字符集 A-Z1-9）+ YYYYMMDDHHMMSS + 毫秒，存在 localStorage。
  所以每次自己造一个即可，不必从抓包复制。

★ tariffAttributes：1 = 全国/跨省目录，2 = 本省目录（页面上的「全国」「本省」切换）。

★★ operateData 的 URL 路径是**多个三级 id 用 `_` 拼接**，body 里的 size 只是「这次拼几个」：
     js: level3.slice((page-1)*size, page*size).join("_")
   所以 **size 由客户端决定、可以调大** —— 默认 5 时几百个三级目录要发上百次请求，
   调成 BATCH（默认 20）后请求数降到个位数。这是本探针相对页面行为的主要改动。

★ detailsList[] 明细字段（已实测全量）：
   reportNo name codeType feesStandard otherFees extraFees minute commonData dataUnit
   sms orientTraffic orientTrafficUnit iptv broadBand equityCoupon serviceContent
   useScope validPeriod onlinePeriod saleChnl unsubscribe startDate endDate
   contractDuty otherDesc feeUnit
   与移动那套的对应关系见 FIELD_MAP。

用法：
  python he_unicom_tariff.py cities                河北地市列表（cityList）
  python he_unicom_tariff.py menu    [--city 185]  一级/二级分类
  python he_unicom_tariff.py tree    [--city 185]  三级菜单树
  python he_unicom_tariff.py dump    out.json [--city 185] [--workers 4]
  python he_unicom_tariff.py one     <三级id>      单条明细（调试用）

依赖：仅标准库（urllib）。**必须禁掉环境代理**——本机有 V2Ray/残留代理，
      urllib 默认会读注册表，不显式传 ProxyHandler({}) 会静默走代理。
"""
import argparse
import json
import os
import random
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://mxx.client.10010.com/servicequerybusiness"
REF = "https://imgxx.client.10010.com/"
PROV = "018"          # 河北
CITY = "185"          # 邢台（抓包时用户所在城市）
BATCH = 20            # operateData 一次拼几个三级 id（越大请求越少，注意 URL 长度）
ATTRS = ("1", "2")    # 1=全国/跨省，2=本省

# ★★ 河北 12 个地市（cityList 实测全量，2026-09-24）
#    🔴🔴 旧结论「联通资费与 cityId 无关、只采一城」**已证伪**（2026-09-24）：
#        `threeLevelName` 返回的三级目录 id 集合**逐城不同** —— 22 个 (一级×二级) 组合里
#        8 个存在城市差异；12 个地市**每个都有别的城市没有的专属条目**
#        （雄安：「雄安工地0元50G流量包」「雄安拆迁专用40元赠费包」；
#          沧州 41 条「华油专属」；保定「保定理工学院5G随行专网」…）。
#        全量对账：单城(邢台)=8991 个三级目录 → 12 城并集=9121，净增 130。
#        ⇒ 必须**取 12 城并集**，否则静默漏条（不报错、只是少）。
#    ✅ 好消息：`operateData` 是**按 id 解析**的，cityId 不设门槛（用邢台 cityId 能取到
#       雄安专属条目）⇒ 明细只需按并集 id 拉**一遍**，不必按城市重复拉 12 遍。
#    ⚠️ 旧 `city_drift()` 为什么没发现：它只抽 (1-1001 套餐/移网) 与 (2-2004 加装包/权益包)
#       两个组合 —— 这两个**恰好全省一致**。抽样点选在无差异维度上 = 假阴性。
CITY_CODES = (("石家庄", "188"), ("唐山", "181"), ("秦皇岛", "182"), ("邯郸", "186"),
              ("邢台", "185"), ("保定", "187"), ("张家口", "184"), ("承德", "189"),
              ("沧州", "180"), ("廊坊", "183"), ("衡水", "720"), ("雄安", "782"))

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
                   "Mobile Safari/537.36"),
    "Origin": REF.rstrip("/"),
    "Referer": REF,
    "x-requested-with": "com.sinovatech.unicom.ui",
}

# 公共空参数：抓包里每次请求都原样带着，看着像埋点占位，实测可留可去（这里照抄以求稳）
BLANK = {"duanlianjieabc": "", "channelCode": "", "serviceType": "", "saleChannel": "",
         "externalSources": "", "contactCode": "", "ticket": "", "ticketPhone": "",
         "ticketChannel": ""}

_LOCK = threading.Lock()
_STAT = {"req": 0, "ok": 0, "empty": 0, "err": 0}

# 联通明细字段 → 移动那套的字段名（页面/变更检测都用移动那套，便于复用）
FIELD_MAP = {
    "feesStandard": "fees",        # 月费
    "commonData": "data",          # 通用流量
    "dataUnit": "dataUnit",        # 流量单位
    "minute": "call",              # 通话分钟
    "useScope": "applicablePeople",  # 目标客户（适用范围）
    "saleChnl": "channel",         # 办理渠道
    "startDate": "onlineDay",      # 上线日
    "endDate": "offineDay",        # 下线日
    "serviceContent": "otherContent",  # 权益说明
    "extraFees": "extraFees",      # 超套资费
    "validPeriod": "validPeriod",  # 有效期
    "broadBand": "brandwidth",     # 宽带
}

# ★★ 「停售套餐」整类都是已下线资费 —— 实测 3879 条里 3874 条 endDate 已过期
#    （99.87%），而「在售」那 5099 条零过期。所以排除它不是取舍、是有硬证据的清理：
#    它们是历史资费，留在页面上只会污染「月费/流量」排序和每日变更检测。
#    移动那套的 nrtariff 用 isPublic=1，本身就不含停售，这样两网口径才一致。
#    想留就加 --include-stopped（脚本与快照都支持）。
STOPPED_FIRST = "99"

# 联通特有、页面不显示的长文本：归一化时丢掉，否则快照体积白涨
#   （serviceContent 已进 otherContent，这几个纯冗余）
DROP_LONG = ("contractDuty", "otherDesc", "unsubscribe")


def normalize(e):
    """把联通明细字段名就地映射成移动那套，页面侧因此无需为联通分叉。"""
    for src, dst in FIELD_MAP.items():
        if dst not in e and src in e:
            e[dst] = e[src]
    e["tariffName"] = e.get("name") or ""
    e["tariffAttr"] = e.get("_attr") or ""
    e["type2"] = e.get("_firstLevel") or ""          # 一级分类号，对应移动的 type2
    e["type2Name"] = e.get("_firstLevelName") or ""
    e["type3Name"] = e.get("_secondLevelName") or ""
    for k in DROP_LONG:
        e.pop(k, None)
    return e


def type_name(first_level):
    """一级分类号 → 名称，与移动那套的 ZFLX 对齐（99 是联通独有的停售类）。"""
    return TYPES.get(str(first_level), "其他")


TYPES = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
         "5": "标准资费", "99": "停售套餐"}


def _opener():
    """真直连 opener：显式传空 ProxyHandler，否则 urllib 会偷读注册表/环境代理。"""
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=ctx))


_OP = _opener()


def behavior_id():
    """16 位随机（A-Z1-9）+ 时间戳+毫秒 —— 与前端 JS 同构。服务端不校验。"""
    pool = "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"
    rnd = "".join(random.choice(pool) for _ in range(16))
    return rnd + time.strftime("%Y%m%d%H%M%S") + "%03d" % random.randint(0, 999)


def post(path, data, retry=2, timeout=20):
    """POST 一个 form，返回解析后的 JSON；失败重试。path 可以是完整 URL 或相对路径。"""
    url = path if path.startswith("http") else BASE + path
    payload = dict(BLANK)
    payload.update(data)
    payload.setdefault("behaviorId", behavior_id())
    body = urllib.parse.urlencode(payload).encode()
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=HEADERS, method="POST")
            with _OP.open(req, timeout=timeout) as r:
                raw = r.read()
            with _LOCK:
                _STAT["req"] += 1
            if raw[:2] == b"\x1f\x8b":          # 裸 gzip 流
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1))
    with _LOCK:
        _STAT["err"] += 1
    return {"code": "-1", "msg": "请求失败: %s: %s" % (type(last).__name__, last)}


def get_cities(prov=PROV):
    """地市列表（cityList）。

    🔴 参数名是 **``provinceCode``**，不是 ``provinceId`` —— 传错会直接回
    ``{"code":"0001","desc":"参数错误"}``（本探针实测踩过；同族的
    indexData / threeLevelName / operateData 用的却都是 ``provinceId``，唯独这个不同）。
    响应结构：``data.cityList[]``，每项 ``{cityCode, cityName}``。
    前端会把「678/顺德」这条历史特殊城市过滤掉（广东），这里保持一致。
    """
    r = post("/queryTariffNew/cityList", {"provinceCode": prov})
    d = r.get("data") if isinstance(r, dict) else None
    lst = (d or {}).get("cityList") or []
    return [c for c in lst
            if not (str(c.get("cityCode")) == "678" and c.get("cityName") == "顺德")]


def get_menu(city=CITY):
    """一级/二级分类骨架。返回 (levelList, meta)"""
    r = post("/queryTariffNew/indexData", {"provinceId": PROV, "cityId": city})
    if r.get("code") != "0000":
        return [], r
    return (r.get("data") or {}).get("levelList") or [], r


def get_level3(attr, first, second, city=CITY):
    """某个 (attr, 一级, 二级) 下的三级菜单 id 列表。空目录返回 []。"""
    r = post("/queryTariffNew/threeLevelName",
             {"tariffAttributes": attr, "firstLevel": first, "secondLevel": second,
              "provinceId": PROV, "cityId": city})
    if r.get("code") == "0001":
        with _LOCK:
            _STAT["empty"] += 1
        return []
    if r.get("code") != "0000":
        return []
    return (r.get("data") or {}).get("dataList") or []


def get_detail(ids, city=CITY, page=1):
    """operateData：ids 是三级 id 列表，会被拼成 <id1>_<id2>_... 放进 URL 路径。"""
    if not ids:
        return [], None
    seg = "_".join(ids)
    r = post("/queryTariffNew/operateData/" + seg,
             {"page": page, "size": len(ids), "provinceId": PROV, "cityId": city})
    if r.get("code") != "0000":
        return [], r
    d = r.get("data") or {}
    return d.get("detailList") or [], r


def collect(city=CITY, workers=4, include_stopped=False, check_drift=True, verbose=True,
            cities=None):
    """全量采集：遍历 (attr × 一级 × 二级) 拿三级 id，再分批拉明细。

    ★★ 三级菜单必须取 **12 城并集**（2026-09-24 修正）。
       旧版「只采邢台一城」是错的：实测 22 个 (一级×二级) 组合里 8 个随城市变化，
       12 个地市**每个都有专属条目**，单城漏 130 个三级目录（雄安、沧州、保定…）。
       为什么以前没发现：旧 `city_drift()` 只抽了两个**恰好全省一致**的组合 ——
       抽样点选在无差异维度上，是**假阴性**。判据已换成 `city_scope()`（全组合 × 全地市）。

    ★ 明细**不必**按城市重复拉：`operateData` 按 id 解析，cityId 不设门槛
      （已实测：用邢台 cityId 能取到雄安专属条目）。所以内部把同一组合的 id
      按「出现在哪些城市」分组，每组只拉一次 —— 既拿全了，又能给每条打上
      `_cities`（该资费出现在哪些地市），供页面标注「仅 XX 市」。
    """
    t0 = time.time()
    cities = cities or list(CITY_CODES)
    if check_drift:
        log("判据：三级目录是否随 cityId 变化（全组合 × %d 地市）：" % len(cities))
        city_scope(verbose=True)
    levels, meta = get_menu(city)
    if not levels:
        log("分类骨架获取失败: %s" % str(meta)[:180])
        return None
    pairs, skipped = [], []
    for lv in levels:
        if not include_stopped and str(lv.get("firstLevel")) == STOPPED_FIRST:
            skipped.append(lv.get("firstLevelName"))
            continue
        for sub in lv.get("secondLevels") or []:
            for a in ATTRS:
                pairs.append((a, lv.get("firstLevel"), sub.get("secondLevel"),
                              lv.get("firstLevelName"), sub.get("secondLevelName")))
    log("一级 %d 个（跳过 %s）· (attr×一级×二级) 组合 %d 个 · 并发 %d · 地市 %d 个"
        % (len(levels), "/".join(skipped) or "无", len(pairs), workers, len(cities)))

    # 1) 并发取三级菜单 —— 逐城取并集（这是本次修正的核心）
    def one(p):
        a, f, s, fn, sn = p
        merged, id_cities = {}, {}
        for nm, code in cities:
            for x in get_level3(a, f, s, code):
                i = x.get("id")
                if not i:
                    continue
                merged.setdefault(i, {"id": i, "name": x.get("name")})
                id_cities.setdefault(i, set()).add(code)
        return {"attr": a, "firstLevel": f, "secondLevel": s,
                "firstLevelName": fn, "secondLevelName": sn,
                "level3": list(merged.values()),
                "id_cities": dict((k, sorted(v)) for k, v in id_cities.items())}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        groups = list(ex.map(one, pairs))
    hit = [g for g in groups if g["level3"]]
    n3 = sum(len(g["level3"]) for g in hit)
    log("有三级目录的组合 %d 个，三级菜单（12 城并集）共 %d 个" % (len(hit), n3))
    log("（空目录 %d 个，属正常；code=0001）" % (len(groups) - len(hit)))

    # 2) 同一组合内，按「出现在哪些城市」分组，每组按 BATCH 分批拉明细（不按城市重复拉）
    jobs = []
    for gi, g in enumerate(hit):
        by_city = {}
        for x in g["level3"]:
            key = tuple(g["id_cities"].get(x["id"]) or [])
            by_city.setdefault(key, []).append(x["id"])
        for key, ids in by_city.items():
            for i in range(0, len(ids), BATCH):
                jobs.append((gi, ids[i:i + BATCH], list(key)))
    log("明细请求 %d 次（每批 ≤%d 个 id；已按城市归属分组）" % (len(jobs), BATCH))

    entries = []
    rep_cities = {}                          # reportNo -> set(城市码)：该资费出现在哪些地市
    def job(j):
        gi, ids, cl = j
        det, _ = get_detail(ids, city)
        return gi, det, cl

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for gi, det, cl in ex.map(job, jobs):
            for e in det:
                e["_attr"] = hit[gi]["attr"]
                e["_firstLevel"] = hit[gi]["firstLevel"]
                e["_secondLevel"] = hit[gi]["secondLevel"]
                e["_firstLevelName"] = hit[gi]["firstLevelName"]
                e["_secondLevelName"] = hit[gi]["secondLevelName"]
                e["_cities"] = cl
                # ★ 同一 reportNo 可能从多个三级目录命中（各批城市集不同）——
                #   它出现在哪些城市应是**并集**，不能取「第一次见到的那个」：
                #   否则一条全省资费若先被某个城市的批次捞到，就会被误标成「仅 XX 市」。
                rn = str(e.get("reportNo") or "").strip()
                if rn:
                    rep_cities.setdefault(rn, set()).update(cl)
                entries.append(normalize(e))

    # 按 reportNo 去重：同一资费会在多个三级目录下重复出现（实测邢台 8978 → 7899）
    seen, uniq = set(), []
    for e in entries:
        k = str(e.get("reportNo") or "").strip() or json.dumps(e, ensure_ascii=False, sort_keys=True)
        if k in seen:
            continue
        seen.add(k)
        if k in rep_cities:                  # 用并集覆盖（见上：不能取首次命中的那个）
            e["_cities"] = sorted(rep_cities[k])
        uniq.append(e)
    log("去重：%d → %d 条（重复 %d）" % (len(entries), len(uniq), len(entries) - len(uniq)))

    # 地市覆盖面小结（这条日志就是「有没有漏城市专属资费」的日常判据）
    all_codes = set(c for _nm, c in cities)
    n_all = sum(1 for e in uniq if len(e.get("_cities") or []) >= len(all_codes))
    n_city = sum(1 for e in uniq if 0 < len(e.get("_cities") or []) < len(all_codes))
    n_none = sum(1 for e in uniq if not e.get("_cities"))
    log("地市覆盖：全省 %d 条 · 城市专属 %d 条 · 无城市标记 %d 条"
        % (n_all, n_city, n_none))
    dist = {}
    for e in uniq:
        cs = e.get("_cities") or []
        if 0 < len(cs) < len(all_codes):
            for c in cs:
                dist[c] = dist.get(c, 0) + 1
    if dist:
        nm_of = dict((c, nm) for nm, c in cities)
        log("   城市专属条目分布：%s"
            % "、".join("%s %d" % (nm_of.get(c, c), n) for c, n in sorted(dist.items())))

    with _LOCK:
        _STAT["ok"] = len(uniq)
    log("采集完成：%d 条明细 / %.0fs · 请求 %d（空目录 %d · 失败 %d）"
        % (len(uniq), time.time() - t0, _STAT["req"], _STAT["empty"], _STAT["err"]))
    return {"province": PROV, "provinceName": "河北",
            "cityId": city,                    # 取明细时用的 cityId（不设门槛，仅占位）
            "cityName": dict((c.get("cityCode"), c.get("cityName"))
                             for c in get_cities()).get(city, ""),
            # ★ 实际覆盖的地市（三级菜单按这些城市取并集）
            "cities": [{"cityCode": c, "cityName": nm} for nm, c in cities],
            "cityScope": "union",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "endpoint": BASE + "/queryTariffNew/operateData/<ids>",
            "skippedStopped": not include_stopped,
            "deduped": len(entries) - len(uniq),
            "groups": groups, "entries": uniq}


def log(msg):
    print(msg, flush=True)


def city_drift(combos=None, cities=None):
    """🔴🔴 已被 `city_scope()` 取代 —— 保留此名只为兼容旧调用点，内部转调。

    历史教训（务必别重犯）：本函数旧版只抽 ``[("1","1","1001"), ("2","2","2004")]``
    两个组合 × 3 个城市，而这两个组合**恰好全省一致**，于是给出「cityId 无关」的
    **假阴性**结论，直接导致主链路「只采邢台一城」漏掉 130 个三级目录。
    抽样点选在无差异维度上 = 测了等于没测。新判据见 `city_scope()`。
    """
    return city_scope(combos=combos, cities=cities)


def city_scope(combos=None, cities=None, verbose=True, workers=8):
    """判据：三级目录 id 集合是否随 cityId 变化（决定「要不要取 12 城并集」）。

    与旧 city_drift 的区别：
      ① 不再是 2 个组合，而是**全部 22 个 (一级×二级) 组合**（从 indexData 现取）
      ② 不再是 3 个城市，而是**全部 12 个地市**
      ③ 除了「是否一致」，还输出**并集净增**与**各城专属条目名** —— 这三样才是
         能让人一眼看懂「漏了什么」的证据；只说「不一致」等于没说
      ④ 并发跑（默认 8）—— 528 个请求串行要十几分钟，那种探针没人会真的跑

    返回 dict，可直接进日志/报告。
    """
    combos = combos or []
    cities = cities or list(CITY_CODES)
    if not combos:
        levels, _ = get_menu(CITY)
        combos = []
        for lv in levels:
            for sub in lv.get("secondLevels") or []:
                combos.append((str(lv.get("firstLevel")), str(sub.get("secondLevel")),
                               lv.get("firstLevelName"), sub.get("secondLevelName")))

    def cell(p):
        (fl, sl), (nm, code) = p
        m = {}
        for a in ATTRS:                   # attr 语义未明，两个都取以减少遗漏
            for x in get_level3(a, fl, sl, code):
                if x.get("id"):
                    m[x["id"]] = x.get("name")
        return (fl, sl), code, m

    tasks = [((fl, sl), c) for (fl, sl, _f, _s) in combos for c in cities]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        cells = list(ex.map(cell, tasks))
    per = {}
    for (fl, sl), code, m in cells:
        per.setdefault((fl, sl), {})[code] = m

    base = CITY
    single, union = set(), set()
    exclusive = {}                            # code -> [(name, combo)]
    diff_combos = []
    for (fl, sl, fln, sln) in combos:
        d = per[(fl, sl)]
        u = set()
        for c in d:
            u |= set(d[c])
        s0 = set(d.get(base, {}))
        single |= s0
        union |= u
        if any(set(d[c]) != set(d[list(d)[0]]) for c in d):
            diff_combos.append("%s/%s" % (fln, sln))
        for nm, code in cities:
            mine = set(d.get(code, {}))
            others = set()
            for nm2, c2 in cities:
                if c2 != code:
                    others |= set(d.get(c2, {}))
            only = mine - others
            if only:
                exclusive.setdefault(code, []).extend(
                    (d[code].get(i), "%s/%s" % (fln, sln)) for i in only)

    out = {"combos": len(combos), "cities": len(cities),
           "single_city_menu": len(single), "union_menu": len(union),
           "gain": len(union) - len(single),
           "diff_combos": diff_combos, "exclusive": exclusive}
    if verbose:
        log("   [city_scope] %d 组合 × %d 城市 · 单城(%s)=%d → 并集=%d  净增=%d"
            % (len(combos), len(cities), base, len(single), len(union), out["gain"]))
        if diff_combos:
            log("   [city_scope] 存在城市差异的组合 %d 个：%s"
                % (len(diff_combos), "、".join(diff_combos)))
        for nm, code in cities:
            items = exclusive.get(code) or []
            if items:
                log("   [city_scope] %s 专属 %d 条 · 例：%s"
                    % (nm, len(items), str(items[0][0])[:40]))
        if not diff_combos:
            log("   [city_scope] ⚠️ 全部一致 —— 但仍须留意：抽样组合必须覆盖 3/99 等易变档，"
                "否则又是假阴性")
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd", choices=["cities", "menu", "tree", "dump", "one", "drift", "scope"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--city", default=CITY)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--include-stopped", action="store_true",
                    help="连「停售套餐」一起采（默认排除，那类 99.87%% 已过期）")
    a = ap.parse_args()

    if a.cmd == "scope":
        print("== 三级目录地市覆盖面审计（全组合 × 全地市）==")
        r = city_scope()
        print(json.dumps({k: (v if k != "exclusive" else {c: len(v[c]) for c in v})
                          for k, v in r.items()}, ensure_ascii=False, indent=1)[:2500])
        sys.exit(0 if r["diff_combos"] else 2)

    if a.cmd == "drift":
        print("== ⚠️ drift 已废弃：旧版只抽 2 个「恰好一致」的组合，是假阴性。")
        print("   请改用 scope（全 22 组合 × 全 12 地市）。以下转调 scope：==")
        r = city_scope()
        sys.exit(0 if not r["diff_combos"] else 2)

    if a.cmd == "cities":
        cs = get_cities()
        print("河北地市 %d 个：" % len(cs))
        for c in cs:
            print("   ", json.dumps(c, ensure_ascii=False)[:140])
        return

    if a.cmd == "menu":
        levels, meta = get_menu(a.city)
        print("code =", meta.get("code"), meta.get("msg"))
        for lv in levels:
            print("  一级 %-3s %s  (二级 %d 个)"
                  % (lv.get("firstLevel"), lv.get("firstLevelName"),
                     len(lv.get("secondLevels") or [])))
            for s in (lv.get("secondLevels") or []):
                print("      %-6s %s" % (s.get("secondLevel"), s.get("secondLevelName")))
        return

    if a.cmd == "tree":
        levels, _ = get_menu(a.city)
        for lv in levels:
            for s in (lv.get("secondLevels") or []):
                for attr in ATTRS:
                    lst = get_level3(attr, lv.get("firstLevel"), s.get("secondLevel"), a.city)
                    print("attr=%s %s/%s → 三级 %d 个"
                          % (attr, lv.get("firstLevelName"), s.get("secondLevelName"), len(lst)))
                    for x in lst[:6]:
                        print("      %s  %s" % (x.get("id"), x.get("name")))
        return

    if a.cmd == "one":
        det, raw = get_detail([a.arg], a.city)
        print("code =", (raw or {}).get("code"), (raw or {}).get("msg"))
        print(json.dumps(det, ensure_ascii=False, indent=2)[:3000])
        return

    out = collect(a.city, a.workers, include_stopped=a.include_stopped)
    if not out:
        sys.exit(1)
    dst = a.arg or "unicom_tariff.json"
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print("已写出", dst, os.path.getsize(dst), "字节")


if __name__ == "__main__":
    main()
