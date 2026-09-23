# -*- coding: utf-8 -*-
"""探针：`moduleList` 遗漏影响面量化

发现：getTariffListInfo 的返回里，系列(bean)下**有两种明细容器**：
    nonModuleList  —— 采集脚本目前**只取了这个**
    moduleList[]   —— 形如 [{"moduleName": "流量模组套餐", "tariffList": [...]}]
                      港澳台/国际资费的条目**全在这里**，nonModuleList 是空的
本脚本：
  1. 遍历 18 个组合，统计每个组合 nonModuleList / moduleList 各有多少条
  2. 确认 moduleList 条目的字段与 nonModuleList 是否同构（能否直接并进去）
  3. 打印样例，供人工核对
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import mz_crypto  # noqa: E402
import ssl  # noqa: E402
import urllib.request  # noqa: E402

ROOT = "https://h.app.coc.10086.cn/website/nrapigate/"
REF = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
PROV = "311"
HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": UA,
    "Origin": "https://h.app.coc.10086.cn",
    "Referer": REF,
    "x-requested-with": "com.greenpoint.android.mc10086.activity",
    "x-qen": "1", "x-app-version": "1.0.2", "channelid": "CHINA_APP",
}
_ctx = ssl.create_default_context()
_ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx),
                                     urllib.request.ProxyHandler({}))
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}
ATTR_CN = {"1": "全网资费", "2": "河北资费", "3": "attr3"}
KEY_FIELDS = ["fees", "data", "dataUnit", "call", "applicablePeople", "channel",
              "onlineDay", "offineDay", "otherContent", "extraFees", "validPeriod",
              "brandwidth"]


def call(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ROOT + path, data=data, headers=HEADERS, method="POST")
    with OPENER.open(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "replace").strip()
    j = json.loads(txt)
    if isinstance(j, dict) and set(j.keys()) == {"body"}:
        j = json.loads(mz_crypto.decrypt(j["body"]))
    return j


def fetch(a, t1, t2):
    """全量翻页，返回 (nonModule 条目, module 条目, 系列数)"""
    nm, md, beans_n, page = [], [], 0, 1
    while True:
        r = call("nrtariff/new/Tariff/getTariffListInfo",
                 {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                  "tariffAttr": a, "type1": t1, "type2": t2,
                  "page": page, "limit": 100, "fistLimit": 100})
        d = r.get("data") if isinstance(r, dict) else None
        if not isinstance(d, dict):
            break
        for b in d.get("beans") or []:
            beans_n += 1
            nm.extend(b.get("nonModuleList") or [])
            for m in b.get("moduleList") or []:
                md.extend(m.get("tariffList") or [])
        pg = d.get("page") or {}
        if page >= (pg.get("pages") or 1):
            break
        page += 1
    return nm, md, beans_n


def main():
    t2 = call("nrtariff/new/Tariff/getType2List", {"province": PROV, "isPublic": "1"})
    combos = (t2.get("data") if isinstance(t2, dict) else None) or []
    print("组合数 %d\n" % len(combos))
    print("%-22s %-12s %8s %8s %8s" % ("栏目", "type2", "系列", "nonModule", "module"))
    print("-" * 66)
    tot_nm = tot_md = 0
    all_md = []
    for c in combos:
        a, t1, t2v = (str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2")))
        nm, md, bn = fetch(a, t1, t2v)
        tot_nm += len(nm)
        tot_md += len(md)
        all_md.extend(md)
        tag = "" if not md else "   ◀◀ moduleList 有货"
        print("%-22s %-12s %8d %8d %8d%s"
              % ("%s·%s" % (ATTR_CN.get(a, a), ZFLX.get(t2v, t2v)),
                 "t1=%s/t2=%s" % (t1, t2v), bn, len(nm), len(md), tag))
    print("-" * 66)
    print("合计 nonModule=%d  module=%d  →  若只取 nonModule 会漏 %d 条"
          % (tot_nm, tot_md, tot_md))

    if not all_md:
        print("\nmoduleList 无数据，无需修采集")
        return
    print("\n" + "=" * 66)
    print("字段同构性检查（moduleList 条目 vs KEY_FIELDS）")
    print("=" * 66)
    keys = set()
    for e in all_md[:200]:
        keys |= set(e.keys())
    print("moduleList 条目字段数：%d" % len(keys))
    miss = [f for f in KEY_FIELDS if f not in keys]
    print("KEY_FIELDS 缺失：%s" % (miss or "无 —— 与 nonModuleList 同构，可直接并入"))
    print("有 name 的条目：%d / %d" % (sum(1 for e in all_md if e.get("name")), len(all_md)))
    print("有 tariffName 的：%d / %d"
          % (sum(1 for e in all_md if e.get("tariffName")), len(all_md)))
    print("有 reportNo 的：%d / %d" % (sum(1 for e in all_md if e.get("reportNo")), len(all_md)))
    print("\n样例 3 条：")
    for e in all_md[:3]:
        print("  " + json.dumps({k: e.get(k) for k in
                                 ("name", "tariffName", "fees", "data", "dataUnit",
                                  "call", "applicablePeople", "applicableArea",
                                  "channel", "onlineDay", "offineDay", "reportNo")},
                                ensure_ascii=False))
    print("\nmoduleName 分布（前 15）：")
    return all_md


if __name__ == "__main__":
    main()
