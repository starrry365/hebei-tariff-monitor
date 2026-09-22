# -*- coding: utf-8 -*-
"""中国广电 APP「资费专区」探针 / 采集器（河北）。

来源页面：https://m.10099.com.cn/costNotice/  （「资费公示」H5，SPA）
接口前缀：https://m.10099.com.cn/contact-web/api

★ 与移动/联通最大的不同：**不需要抓包**。这是公开的 H5 页面，
  打开 DevTools 就能看到全部接口；App 反而抓不到（见文末）。

## 接口族（2026-09-22 实测）

| 端点 | 入参 | 出参 |
|---|---|---|
| `goods/queryTariffCondition` | `channelId` `applicableArea` `timestamp` | `data[]` = 分类树（type1→type2→type3，每级带 code+中文名） |
| `busi/qryAreaList` | `channelId` `timestamp` | `data.regionalList[]`（33 个：26 省 + 广深等） |
| `goods/queryTariffNames` | 同上 | 资费名列表 |
| `goods/queryTariffAllByCond` | + `type1`（必填）`type2` `type3`（可选） | `data[]` = **全量明细**（一次返回，无分页） |

## 四个「不知道就会踩」的点

1. 🔴 **`access` 请求头：实测**不被服务端校验**（2026-09-22 17:2x 对照实验更正）。
   抓包里同一批会话出现了**三个不同的值**（H5 两次、App 原生一次），
   于是做了对照：`queryTariffCondition` / `queryTariffAllByCond` 两个端点 ×
   `ZZZZ`/`HB00` 两种区域 × 四种取值（旧值 / 新值 / App 值 / **完全不带**），
   **全部 HTTP 200 + status=000000，数据条数逐一致**。
   ⇒ 早期「少了它直接失败」的记录**不成立**（当时很可能把别的失败归因到了它头上）。
   脚本仍发送该头（万一将来开校验），并对 `status != "000000"` **显式报错**（fail-closed）
   —— 这条兜底逻辑与 access 无关，**必须保留**：上游改协议时绝不静默返回空列表。
2. 🔴 **`type1` 是必填但值被忽略**：传 `GZ`（公众）或 `ZQ`（政企）或 `1`/`2`
   返回条数**完全相同**（河北一律 131）。只有 `type2` / `type3` 真能过滤。
   ⇒ 不要按 type1 分别采（会得到两份全同副本），一次拿全即可。
   这与联通「参数能传但数据不分」是同一类陷阱的另一面：
   **这里反过来 —— 参数必填但没用**。
3. 🔴🔴 **`applicableArea` 是真的分级，而且两级的交集为 0**：
   `ZZZZ`（全国）204 条全是 `areaNames="全国"`；`HB00`（河北）131 条全是 `areaNames="河北省"`
   —— 逐条比对 **交集 = 0**。所以「河北用户能办的资费」＝ **全国 + 河北 两份都要采**，
   少采一份就少一半。（对照联通：那是「一份数据换个城市还是它」，这里是「各是各的」。）
4. 🔴 **价格单位是「分」**：`productPrice=1000` + `productPriceUnit="月"` 对应
   「192流量卡10元套餐」⇒ 1000 分 = 10 元。
   而移动/联通的 `fees` 单位是**元**（移动样例 30/540/39；联通 ≤10 元 1456 条与页面实测吻合）
   ⇒ 归一化时**必须 `/100`**，否则页面月费筛选（阈值 0/10/30/60/100 元）全错。

## 在售口径

`stateFlag`：`"0"` 的 60 条（河北）**100% 的 `offlineDay` 已过期**；`"1"` 是在售。
⇒ 与移动的 `isPublic=1`、联通的「排除停售套餐一级」一样，**用服务端状态位**，
而不是自己去解析日期。默认只采 `stateFlag="1"`（`include_stopped=True` 可强制全采）。

⚠️ `stateFlag="1"` 里仍有 29 条 `offlineDay` 已过期（河北）—— 这两个信号**不总一致**，
   而状态位是服务端的权威判断，以它为准。（页面照常显示日期，用户能自己看到。）

## 日期格式

接口给的是 `2024年09月27日`，而页面 `toDate()` **只认 `^\\d{8}$`**
（不认就 fail-closed 把这条过滤掉，时间筛选会**静默**少数据）⇒ 必须转成 `20240927`。

## 为什么不用抓 App

实测 Fiddler 侧（2026-09-22 16:04-16:05）：`app/m/partner/h5.10099.com.cn`
出现 164 次、**全部**是
`Proxy Configuration Script specified an unreachable proxy` ——
请求压根没经过 Fiddler，一条会话都没留下。
而 H5 站本身就够用，**没必要去折腾 App**。
"""
import gzip
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.request

BASE = "https://m.10099.com.cn/contact-web/api"
REF = "https://m.10099.com.cn/costNotice/"
# 页面 JS 里的常量（长期固定，实测跨小时复用有效）。见文件头第 1 条。
ACCESS = "43a62542065e7a72b20576829b049d9e"
CHANNEL = "cd_20220914_514144"

# tariffAttr 沿用移动那套语义：1=全国/跨省目录，2=本省目录。
# 这样页面侧（含 a1 列与变更报告的 attr）不用为广电再分叉一次。
AREAS = [("1", "ZZZZ", "全国"), ("2", "HB00", "河北省")]

HBEI = "HB00"


def _opener():
    """真直连 opener：显式传空 ProxyHandler，否则 urllib 会偷读注册表/环境里的代理。"""
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=ctx))


def log(*a):
    print(*a)
    sys.stdout.flush()


def post(path, body, timeout=25, retry=2):
    """POST 一个 JSON，返回解析后的 dict（自动解裸 gzip 流）。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdr = {
        "Content-Type": "application/json;charset=UTF-8",
        "access": ACCESS,
        "Origin": "https://m.10099.com.cn",
        "Referer": REF,
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
    }
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(BASE + path, data=data, headers=hdr, method="POST")
            with _opener().open(req, timeout=timeout) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":          # 裸 gzip 流
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:                   # noqa: BLE001
            last = e
            if i < retry:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError("请求 %s 失败：%s: %s" % (path, type(last).__name__, last))


def _ok(j, what):
    """★ fail-closed：任何非 000000 都抛错。

    别写成 `return j.get("data") or []` —— 那样 access 失效（或上游改协议）时
    会返回空列表，下游把它当成「今天资费全下架了」，于是：
    既报了假变更，又不会有人发现凭证已经不对。
    """
    if str(j.get("status")) != "000000":
        raise RuntimeError("%s 返回异常：status=%r message=%r（access 头失效？）"
                           % (what, j.get("status"), j.get("message")))
    return j


def areas():
    """地区表。用于确认河北代码（写死 HB00 是偷懒，上游改了就对不上）。"""
    j = _ok(post("/busi/qryAreaList", {"channelId": CHANNEL, "timestamp": _ms()}), "qryAreaList")
    lst = (j.get("data") or {}).get("regionalList") or []
    return [(x.get("areaCode"), x.get("areaName")) for x in lst]


def type_names():
    """分类树 → {typeCode: 中文名}。

    不硬编码中文名：上游改名/加分类时自动跟上。
    （联通那边是采集侧写死 1..5 的中文映射，广电的 code 是 GZ_TC_5G 这种，
      写死迟早与上游漂移。）
    """
    j = _ok(post("/goods/queryTariffCondition",
                 {"channelId": CHANNEL, "applicableArea": "ZZZZ",
                  "timestamp": _ms()}), "queryTariffCondition")
    out = {}

    def walk(nodes):
        for n in nodes or []:
            c, nm = n.get("typeCode"), n.get("typeName")
            if c and nm:
                out[c] = nm
            walk(n.get("childTariffTypes"))
    walk(j.get("data"))
    return out


def _ms():
    return int(time.time() * 1000)


def fetch_area(area_code, include_stopped=False):
    """采一个地区的全部在售资费。

    `type1` 必填但服务端忽略其值（见文件头第 2 条）⇒ 传 "GZ" 即可拿到该地区全部。
    """
    j = _ok(post("/goods/queryTariffAllByCond",
                 {"channelId": CHANNEL, "applicableArea": area_code,
                  "type1": "GZ", "timestamp": _ms()}), "queryTariffAllByCond")
    arr = j.get("data") or []
    if not include_stopped:
        arr = [x for x in arr if str(x.get("stateFlag")) == "1"]
    return arr


_D = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


def _day(v):
    """2024年09月27日 → 20240927（页面 toDate 只认 8 位数字）。"""
    m = _D.match(str(v or "").strip())
    if not m:
        return ""
    return "%s%02d%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def _yuan(v):
    """分 → 元。1000 → '10'；保留小数但不留多余的 0（27.5 元 → '27.5'）。"""
    try:
        f = float(v) / 100.0
    except Exception:                            # noqa: BLE001
        return ""
    return str(int(f)) if f == int(f) else ("%.2f" % f).rstrip("0").rstrip(".")


def normalize(e, names, attr):
    """广电 → 移动那套字段名（页面行 / 变更检测都只认那套）。"""
    r = {
        "name": e.get("productName") or "",
        "tariffName": e.get("productName") or "",
        "fees": _yuan(e.get("productPrice")),
        "data": e.get("domesticTraffic"),
        "dataUnit": e.get("domesticTrafficUnit"),
        "call": e.get("domesticCall"),
        "applicablePeople": e.get("applicablePeople") or "",
        "channel": e.get("saleChannel") or "",
        "onlineDay": _day(e.get("onlineDay")),
        "offineDay": _day(e.get("offlineDay")),
        "reportNo": e.get("filingNumber") or "",
        "otherContent": " · ".join(x for x in (e.get("otherContent"), e.get("rights")) if x),
        # 广电把「套外资费」单独放 tariffAttr（如「套外国内流量3元/GB…」）——
        # 正是移动 extraFees 的语义，直接对位。
        "extraFees": e.get("tariffAttr") or e.get("operatExplain") or "",
        "validPeriod": e.get("validPeriod") or "",
        "brandwidth": e.get("bandwidth") or "",
        # 分组与显示用
        "type2": e.get("parentTypeCode") or e.get("type2") or "",
        "type2Name": names.get(e.get("parentTypeCode")) or names.get(e.get("type2")) or "其他",
        "tariffAttr": attr,
        "stateFlag": e.get("stateFlag"),
    }
    # 渠道里「（深圳地区除外）」这类在 ch 里会被截断，但完整值已在 ap/saleChannel 各留一份；
    # 这里只把「全国 / 河北省」这类地区名单独留一份，页面不需要，但审计时有用。
    r["_areaNames"] = e.get("areaNames") or ""
    return r


def collect(include_stopped=False, check_drift=True, verbose=True):
    """采全国 + 河北，归一化后返回 {groups, entries, ...}（与移动/联通同构）。"""
    if check_drift:
        check_area_split()
    names = type_names()
    if verbose:
        log("分类名映射 %d 个：%s" % (len(names), " ".join(sorted(names.values()))))
    entries, stat = [], {}
    for attr, code, label in AREAS:
        arr = fetch_area(code, include_stopped=include_stopped)
        stat[label] = len(arr)
        if verbose:
            log("  %s(%s)：%d 条" % (label, code, len(arr)))
        for e in arr:
            entries.append(normalize(e, names, attr))
    # 去重（按 id 语义的 reportNo；两份地区理论上不重叠，实测交集为 0，这里是保险）
    seen, uniq = set(), []
    for e in entries:
        k = str(e.get("reportNo") or "").strip() or ("_" + str(id(e)))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    if verbose and len(uniq) != len(entries):
        log("  去重：%d → %d" % (len(entries), len(uniq)))

    groups, gmap = [], {}
    for e in uniq:
        key = (str(e.get("tariffAttr")), str(e.get("type2")))
        g = gmap.get(key)
        if not g:
            g = {"type2": key[1], "type2Name": e.get("type2Name"), "tariffAttr": key[0],
                 "entries": []}
            gmap[key] = g
            groups.append(g)
        g["entries"].append(e)
    groups.sort(key=lambda g: (g["tariffAttr"], g["type2"]))
    return {"province": "HB00", "provinceName": "河北省", "areaStat": stat,
            "endpoint": BASE + "/goods/queryTariffAllByCond",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "includeStopped": include_stopped,
            "groups": groups, "entries": uniq}


def check_area_split():
    """🔴 前提抽查：全国与河北**不应该有交集**。

    这是「两份都要采」这条结论的地基。上游哪天改成「河北数据包含全国」，
    再两份相加就会**静默翻倍**（变更检测随即报出几千条新增）。
    """
    try:
        a = {x.get("id") for x in fetch_area("ZZZZ")}
        b = {x.get("id") for x in fetch_area(HBEI)}
    except Exception as e:                       # noqa: BLE001
        log("  [drift] 抽查失败（不影响采集）：%s" % e)
        return
    inter = len(a & b)
    log("  [drift] 全国 %d 条 / 河北 %d 条 / 交集 %d 条 %s"
        % (len(a), len(b), inter, "（应为 0）" if inter == 0 else "🔴 交集非 0，检查地区语义！"))


# ---------------- CLI ----------------

def _dump(path, o):
    io.open(path, "w", encoding="utf-8").write(json.dumps(o, ensure_ascii=False))


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "show").lower()
    if cmd == "areas":
        for c, n in areas():
            print("%-8s %s" % (c, n))
        return
    if cmd == "names":
        for c, n in sorted(type_names().items()):
            print("%-16s %s" % (c, n))
        return
    if cmd == "raw":
        arr = fetch_area(sys.argv[2] if len(sys.argv) > 2 else HBEI)
        print(json.dumps(arr[:3], ensure_ascii=False, indent=1)[:3000])
        print("...\n共 %d 条" % len(arr))
        return
    o = collect(include_stopped=("--all" in sys.argv))
    if cmd == "dump":
        p = sys.argv[2] if len(sys.argv) > 2 else "cbn.json"
        _dump(p, o)
        log("已写出 %s（%d 条）" % (p, len(o["entries"])))
    else:
        log("合计 %d 条" % len(o["entries"]))
        for g in o["groups"]:
            log("  attr=%s %-14s %4d 条" % (g["tariffAttr"], g["type2Name"], len(g["entries"])))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
