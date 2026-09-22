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


def collect(city=CITY, workers=4, include_stopped=False, check_drift=True, verbose=True):
    """全量采集：遍历 (attr × 一级 × 二级) 拿三级 id，再分批拉明细。

    ★ 只采**一个城市**即可：实测「全国」与「本省」两个目录的三级菜单与明细内容
      在石家庄/邢台/唐山三城**逐条相同**（77/77、161/161、801/801，明细报告号内容
      也完全一致）⇒ cityId 只影响「能不能办」，不影响「有什么」。12 城各采一遍
      会得到 12 份完全相同的副本。city_drift() 会对这点做抽查，一旦上游改成
      按城市分数据，那里会报警。
    """
    t0 = time.time()
    if check_drift:
        log("抽查 cityId 无关性（「只采一城」的前提）：")
        city_drift()
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
    log("一级 %d 个（跳过 %s）· (attr×一级×二级) 组合 %d 个 · 并发 %d"
        % (len(levels), "/".join(skipped) or "无", len(pairs), workers))

    # 1) 并发取三级菜单
    def one(p):
        a, f, s, fn, sn = p
        lst = get_level3(a, f, s, city)
        return {"attr": a, "firstLevel": f, "secondLevel": s,
                "firstLevelName": fn, "secondLevelName": sn, "level3": lst}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        groups = list(ex.map(one, pairs))
    hit = [g for g in groups if g["level3"]]
    n3 = sum(len(g["level3"]) for g in hit)
    log("有三级目录的组合 %d 个，三级菜单共 %d 个" % (len(hit), n3))
    log("（空目录 %d 个，属正常；code=0001）" % (len(groups) - len(hit)))

    # 2) 每个组合内部按 BATCH 分批拉明细
    jobs = []
    for gi, g in enumerate(hit):
        ids = [x.get("id") for x in g["level3"] if x.get("id")]
        for i in range(0, len(ids), BATCH):
            jobs.append((gi, ids[i:i + BATCH]))
    log("明细请求 %d 次（每批 %d 个 id）" % (len(jobs), BATCH))

    entries = []
    def job(j):
        gi, ids = j
        det, _ = get_detail(ids, city)
        return gi, det

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for gi, det in ex.map(job, jobs):
            for e in det:
                e["_attr"] = hit[gi]["attr"]
                e["_firstLevel"] = hit[gi]["firstLevel"]
                e["_secondLevel"] = hit[gi]["secondLevel"]
                e["_firstLevelName"] = hit[gi]["firstLevelName"]
                e["_secondLevelName"] = hit[gi]["secondLevelName"]
                entries.append(normalize(e))

    # 按 reportNo 去重：同一资费会在多个三级目录下重复出现（实测邢台 8978 → 7899）
    seen, uniq = set(), []
    for e in entries:
        k = str(e.get("reportNo") or "").strip() or json.dumps(e, ensure_ascii=False, sort_keys=True)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    log("去重：%d → %d 条（重复 %d）" % (len(entries), len(uniq), len(entries) - len(uniq)))

    with _LOCK:
        _STAT["ok"] = len(uniq)
    log("采集完成：%d 条明细 / %.0fs · 请求 %d（空目录 %d · 失败 %d）"
        % (len(uniq), time.time() - t0, _STAT["req"], _STAT["empty"], _STAT["err"]))
    return {"province": PROV, "provinceName": "河北", "cityId": city,
            "cityName": dict((c.get("cityCode"), c.get("cityName"))
                             for c in get_cities()).get(city, ""),
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "endpoint": BASE + "/queryTariffNew/operateData/<ids>",
            "skippedStopped": not include_stopped,
            "deduped": len(entries) - len(uniq),
            "groups": groups, "entries": uniq}


def log(msg):
    print(msg, flush=True)


def city_drift(combos=None, cities=None):
    """抽查「数据与 cityId 无关」这个前提是否还成立。

    本探针只采一个城市的前提就是它。上游一旦改成按城市分数据，
    「只采一城」会**静默漏掉其余城市**——不会报错，只是数据不全。
    所以这里每次采集前抽查 2 个组合 × 3 个城市，不一致就在日志里点名。
    """
    combos = combos or [("1", "1", "1001"), ("2", "2", "2004")]
    cities = cities or [("石家庄", "188"), ("邢台", "185"), ("唐山", "181")]
    out = []
    for attr, f, s in combos:
        sigs = {}
        for nm, code in cities:
            sigs[nm] = [x.get("id") for x in get_level3(attr, f, s, code)]
        ref = list(sigs)[0]
        same = all(set(sigs[n]) == set(sigs[ref]) for n in sigs)
        out.append({"combo": "attr=%s first=%s second=%s" % (attr, f, s),
                    "counts": dict((n, len(sigs[n])) for n in sigs), "same": same})
        log("   [drift] %s → %s  %s" % (
            out[-1]["combo"], out[-1]["counts"],
            "三城一致" if same else "⚠️ 不一致！cityId 已影响数据，单城采集会漏"))
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd", choices=["cities", "menu", "tree", "dump", "one", "drift"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--city", default=CITY)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--include-stopped", action="store_true",
                    help="连「停售套餐」一起采（默认排除，那类 99.87%% 已过期）")
    a = ap.parse_args()

    if a.cmd == "drift":
        print("== cityId 无关性抽查（本探针「只采一城」的前提）==")
        res = city_drift()
        sys.exit(0 if all(r["same"] for r in res) else 2)

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
