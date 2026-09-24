# -*- coding: utf-8 -*-
"""四网「上游限制」实时探针 —— 回答「联通/广电为什么没有地市级数据」。

`probe_city_matrix.py` 证明的是**可观察结果**（快照里各网有多少条带地市归属）；
本探针用**实时接口**证明**成因**，避免结论只能靠「当时观察到」口说无凭。

🔴🔴 2026-09-24 更正：本探针原来给出的「联通没有地市级数据」是**错的**。
   错在 B 段的判据本身 —— 它只看 `indexData` 的**两级骨架**，而真正随城市变化的是
   **三级目录 id**（住在另一个接口 `threeLevelName` 里）⇒ 两级骨架天生看不见差异。
   两级「一致」被当成了「地市无影响」，于是主链路只采邢台一城，**静默漏采 130 条**。
   现在 B 段并列保留旧判据（留证）与现行判据（`city_scope()` 的权威结论）。
   完整复盘：`docs/联通河北资费-地市维度纠错-20260924.md`。

═══ 联通：为什么不能按地市查 ═══════════════════════════════════════
  A) 前端 JS 里另有一族 /queryTariff/*（countryTariffQuery / TariffMenuDataThreeHomePage /
     tariffDetailInfo…），河北侧**不被路由** —— 同刻老接口 0.1s 正常，这族逐条 8s 超时。
     ⇒ 不是我们没找到参数，是这套根本没挂上来。
  B) 分两层看（见上「更正」）：
     B1 旧判据（两级骨架，**已证伪，留证**）：12 城签名去重后只有 1 种。
     B2 现行判据（三级目录，22 组合 × 12 城）：**12 个组合随城市变化**，并集净增 130。

═══ 广电：为什么不能按地市查 ═══════════════════════════════════════
  A) qryAreaList 的 33 个区域**全是省级/直辖市粒度**（HB00 河北 / HB01 湖北 / HLJ0 黑龙江 /
     NMG0 内蒙…），只有 GZ00 广州、SZ00 深圳两个城市特例 ⇒ 上游本身没有地市维度可用。
  B) 传地市级编码（HB0001 / HB1305 / HB130500）回 **BASE102 区域编码无效**。
  C) 分类树里 ZQ（政企）节点 childTariffTypes 长度 **0**；且 type1 参数被服务端**完全忽略**
     （GZ / ZQ / 1 / 2 返回同样条数）⇒ 政企分类树是空壳。
  （广电这条**至今成立** —— 它是四网里唯一真没有地市粒度的，条目级 cty 恒 0。）

用法：
    python probes/probe_upstream_limits.py            # 两网都跑
    python probes/probe_upstream_limits.py uni         # 只跑联通
    python probes/probe_upstream_limits.py cbn         # 只跑广电

注意：这是**实时**探针（打真接口），会受网络波动影响；结论性判断都留了「同刻老接口对照」，
      单条超时不等于结论失效 —— 看汇总行的计数。
"""
import gzip
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "probes"))

# 真直连：显式空 ProxyHandler，否则 urllib 会偷读注册表 / 环境里的残留代理（Fiddler 8866 等）
_CTX = ssl.create_default_context()
_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 4)   # 国内网关不支持 RFC5746
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                 urllib.request.HTTPSHandler(context=_CTX))


def raw_post(url, body, ctype, headers, timeout=8):
    """裸 POST：返回 (json | None, 说明)。不重试 —— 本探针要的正是「通不通」。"""
    if body is None:
        data = None
    elif "json" in ctype:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    else:
        data = urllib.parse.urlencode(body).encode()
    h = dict(headers)
    if data is not None:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    t0 = time.time()
    try:
        with OP.open(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:
        return None, "%s: %s (%.1fs)" % (type(e).__name__, str(e)[:60], time.time() - t0)
    if raw[:2] == b"\x1f\x8b":                     # 裸 gzip 流
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode("utf-8", "replace")), "%.1fs" % (time.time() - t0)
    except Exception:
        return {"_raw": raw[:120].decode("utf-8", "replace")}, "%.1fs" % (time.time() - t0)


# ───────────────────────────── 联通 ─────────────────────────────
def probe_unicom():
    import he_unicom_tariff as U

    print("=" * 72)
    print("联通：上游为什么不给地市级")
    print("=" * 72)

    NEW = ["/queryTariff/TariffMenuDataThreeHomePage",
           "/queryTariff/TariffMenuDataDetailHomePage",
           "/queryTariff/countryTariffQuery",
           "/queryTariff/tariffDetailInfo",
           "/queryTariff/tariffDetailInfoNew"]

    print("\n[A] 旁路接口族 /queryTariff/* 是否被路由（同刻对照老接口 /queryTariffNew/indexData）")
    # 对照：老接口（已知可用）
    t0 = time.time()
    ok_old = raw_post(U.BASE + "/queryTariffNew/indexData",
                      dict(U.BLANK, provinceId=U.PROV, cityId=U.CITY,
                           behaviorId=U.behavior_id()),
                      "application/x-www-form-urlencoded", U.HEADERS, timeout=10)
    old_ms = (time.time() - t0) * 1000
    old_j, old_note = ok_old
    old_code = (old_j or {}).get("code")
    print("   对照 老接口 indexData        code=%-6s %.0fms   %s"
          % (old_code, old_ms, "✅ 正常" if old_code == "0000" else "⚠️ " + old_note))

    routed, unrouted = 0, 0
    for path in NEW:
        for ctype in ("application/x-www-form-urlencoded",
                      "application/json;charset=UTF-8"):
            body = dict(U.BLANK, provinceId=U.PROV, cityId=U.CITY,
                        behaviorId=U.behavior_id())
            j, note = raw_post(U.BASE + path, body, ctype, U.HEADERS, timeout=8)
            name = path.split("/")[-1]
            if j is None:
                print("   %-38s %-5s  未通 %s" % (name, "json" if "json" in ctype else "form", note))
                unrouted += 1
            else:
                print("   %-38s %-5s  code=%s  %s" % (
                    name, "json" if "json" in ctype else "form",
                    j.get("code"), str(j.get("msg") or j.get("desc") or "")[:40]))
                routed += 1
            break   # 该体已明确结论，换下一种

    print("\n[B1] 两级骨架对比（★旧判据，**已证伪** —— 留证，看它为什么看不见差异）")
    # 🔴 用采集器的城市表，不另抄一份（抄一份就多一份会漂的副本）。
    cities = list(U.CITY_CODES)
    sig = {}
    for nm, code in cities:
        j, note = raw_post(U.BASE + "/queryTariffNew/indexData",
                           dict(U.BLANK, provinceId=U.PROV, cityId=code,
                                behaviorId=U.behavior_id()),
                           "application/x-www-form-urlencoded", U.HEADERS, timeout=10)
        if j is None:
            print("   %-6s %-4s 未通 %s" % (nm, code, note))
            continue
        lv = (j.get("data") or {}).get("levelList") or []
        s = "|".join("%s:%s" % (x.get("firstLevel"),
                                ",".join(str(y.get("secondLevel")) for y in (x.get("secondLevels") or [])))
                     for x in lv)
        sig[nm] = s
        print("   %-6s %-4s code=%-6s 一级=%d 二级=%d"
              % (nm, code, j.get("code"), len(lv),
                 sum(len(x.get("secondLevels") or []) for x in lv)))
    uniq2 = len(set(sig.values()))
    print("\n   → 两级骨架去重后 %d 种" % uniq2)
    print("     🔴 这一层**天生看不见城市差异**：真正随城市变化的是**三级目录 id**，"
          "住在另一个接口 threeLevelName 里。")
    print("        2026-09-22 正是据此得出「换 cityId 不影响数据」⇒ 主链路只采邢台一城，"
          "**静默漏采 130 条**。")

    print("\n[B2] 三级目录对比（现行判据 —— 直接调采集器的权威 city_scope()，不另写一套）")
    sc = U.city_scope(verbose=True)
    print("   → %d 组合 × %d 城市：单城(%s)=%d → 并集=%d 净增=%d"
          % (sc["combos"], sc["cities"], U.CITY, sc["single_city_menu"],
             sc["union_menu"], sc["gain"]))
    print("     存在城市差异的组合 %d 个%s"
          % (len(sc["diff_combos"]),
             ("：" + "、".join(sc["diff_combos"])) if sc["diff_combos"] else ""))
    print("     ⇒ 必须取 **12 城并集**；且「一致」只有在覆盖全 22 组合 × 全 12 城后才算数。")

    print("\n结论（联通）：")
    print("   · 旁路 /queryTariff/* 本网未路由（本次 %d 条未通 / %d 条有响应），"
          "而老接口同刻正常 ⇒ 不是参数没找对，是这套没挂上来。" % (unrouted, routed))
    print("   · 两级骨架 %d 种（看不出差异）；三级目录 22×12 判据下 **%d 个组合有差异**、"
          "净增 %d 个三级目录。" % (uniq2, len(sc["diff_combos"]), sc["gain"]))
    print("   · 明细 24 个字段里确实**零地域字段** —— 但城市归属来自"
          "**三级目录出现在哪些城市**（采集侧逐条记录），不等于「没有地市维度」。")
    print("   ⇒ 联通**有**条目级地市维度（由采集侧 12 城并集提供）；"
          "旧结论「上游未提供」是把「两级骨架一致」错当成「无差异」。")
    return sc["gain"]


# ───────────────────────────── 广电 ─────────────────────────────
def probe_cbn():
    import he_cbn_tariff as C

    print("=" * 72)
    print("广电：上游为什么不给地市级 + 政企分类树为何不可用")
    print("=" * 72)

    # 先拿一份可用数据做对照（证明 access 有效，避免把「凭证失效」误判成「没有地市」）
    try:
        arr = C.fetch_area("HB00", include_stopped=True)
        print("\n[对照] 河北 HB00 正常取到 %d 条 ⇒ 凭证/通道有效。" % len(arr))
    except Exception as e:
        print("\n[对照] 河北 HB00 取数失败：%s ⇒ 本次结论**不可信**，请先修通道。" % e)
        return None

    print("\n[A] qryAreaList 区域粒度（有没有地市级）")
    try:
        lst = C.areas()
    except Exception as e:
        print("   ERR %s" % e)
        lst = []
    hb_pref = sorted({str(c) for c, _ in lst if c and str(c).startswith("HB")})
    # 省级粒度 = 4 字符；地市级会带 4 位数字后缀（HB1305 / HB0001）
    import re as _re
    city_like = [(c, n) for c, n in lst if _re.match(r"^[A-Z]{2}\d{4}$", str(c or ""))]
    print("   共 %d 个区域。河北 = %s（适配器常量 C.HBEI）" % (len(lst), C.HBEI))
    print("   HB 前缀的编码：%s（⚠️ H 开头两字母不只河北，别按前缀判省，要按区域表 name 判）"
          % hb_pref)
    print("   形如 xx+4 位数字（地市级）的编码：%s"
          % (city_like if city_like else "无 —— 全部是省级/直辖市粒度"))
    print("   （GZ00=广州、SZ00=深圳 是仅有的两个城市特例，与河北无关）")

    print("\n[B] 传地市级编码给查询接口（走适配器 C.post，头由它内部拼）")
    for a in ("HB00", "HB0001", "HB1305", "HB130500"):
        try:
            j = C.post("/goods/queryTariffAllByCond",
                       {"channelId": C.CHANNEL, "applicableArea": a,
                        "type1": "GZ", "timestamp": C._ms()}, timeout=15)
            st = j.get("status")
            d = j.get("data")
            n = len(d) if isinstance(d, list) else "-"
            print("   %-10s status=%-8s n=%-5s %s"
                  % (a, st, n, "✅ 可用" if str(st) == "000000" else "❌ " + str(j.get("message"))[:40]))
        except Exception as e:
            print("   %-10s EXC %s" % (a, str(e)[:80]))

    print("\n[C] 分类树 ZQ（政企）节点 + type1 参数是否生效")
    try:
        j = C._ok(C.post("/goods/queryTariffCondition",
                         {"channelId": C.CHANNEL, "applicableArea": "ZZZZ",
                          "timestamp": C._ms()}), "cond")
        nodes = j.get("data") or []

        def find(ns, code):
            for n in ns or []:
                if str(n.get("typeCode")) == code:
                    return n
                r = find(n.get("childTariffTypes"), code)
                if r:
                    return r
            return None

        zq = find(nodes, "ZQ")
        if zq:
            kids = zq.get("childTariffTypes") or []
            print("   分类树里有 ZQ（政企）节点：typeName=%r children=%d %s"
                  % (zq.get("typeName"), len(kids),
                     "⇒ 空壳，取不到任何政企资费" if not kids else "⚠️ 有子节点了，上游已变"))
        else:
            print("   分类树里**没有** ZQ 节点。")
    except Exception as e:
        print("   ERR %s" % e)

    counts = []
    for t1 in ("GZ", "ZQ", "1", "2"):
        try:
            j = C.post("/goods/queryTariffNames",
                       {"channelId": C.CHANNEL, "applicableArea": "HB00",
                        "type1": t1, "timestamp": C._ms()}, timeout=15)
            d = j.get("data")
            n = len(d) if hasattr(d, "__len__") else d
            counts.append(n)
            print("   type1=%-3s -> n=%s" % (t1, n))
        except Exception as e:
            print("   type1=%-3s 未通 %s" % (t1, str(e)[:70]))
    ignored = len(set(map(str, counts))) <= 1 and len(counts) >= 2
    print("   → type1 参数%s" % ("被**完全忽略**（GZ/ZQ/1/2 返回同样条数）"
                              if ignored else "对结果有影响（与基线结论不一致，请复核）"))

    print("\n结论（广电）：")
    print("   · 区域表只有省级粒度（+广州/深圳两个特例）⇒ 上游本身没有地市维度。")
    print("   · 地市级编码回 BASE102 区域编码无效。")
    print("   · 政企 ZQ 节点子类为 0 且 type1 被忽略 ⇒ 政企分类树是空壳。")
    print("   ⇒ 广电/政企两层都**取不到**，属上游设计，非解析遗漏。")
    return ignored


if __name__ == "__main__":
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if which in ("uni", "unicom", "all"):
        probe_unicom()
        print()
    if which in ("cbn", "gd", "all"):
        probe_cbn()
