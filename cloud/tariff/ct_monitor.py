#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""河北电信「资费专区」适配器 —— 第四网（www.189.cn / tariffSection.do）。

════════════════════════════════════════════════════════════════════
🔴🔴 本网**不能用脚本直连采集**（与其他三网的根本差异）
════════════════════════════════════════════════════════════════════
接口 `tariffSection.do` 被**瑞数类 JS 挑战 WAF** 保护：
  - 请求体 = Base64(AES-128-ECB-PKCS7(JSON))，密钥 `telecom_wap_2018`
    （已破解并逐字节自证，见 probes/he_ct_tariff.py 与 evidence/telecom_tariff_capture_20260922.json）；
  - 但 **API 端点本身也回 412**（curl/requests 同样 412），响应体是 JS 挑战页
    ⇒ 纯脚本**无法**建立会话；
  - 且 WAF 会识别自动化浏览器（navigator.webdriver=true ⇒ 直接 400 空响应，
    chrome-devtools MCP 的受管 Chromium 就是这么被拒的）。

✅ 可行路线（2026-09-22 本机 + **同日云端 CI** 双向实测全通）：
  **真实 Chrome + 远程调试口**（启动项只给 --remote-debugging-port，
  不给 --enable-automation ⇒ navigator.webdriver=false）：
    1. 启动：chrome --remote-debugging-port=9223 --user-data-dir=<临时目录>
       · Windows 本机：直接跑 chrome.exe；
       · 🟢 Linux CI：**同样可行**（2026-09-22 在 ubuntu-latest 上实测通过）——
         用 Xvfb 起**有头** Chrome（不是 --headless：headless 的 UA 带
         HeadlessChrome，等于自己把难度调高），挑战照样自动过。
         ⇒ 所以电信**并不是「云端采不到」**，它只是「没有浏览器就采不到」。
         采集脚本：`probes/tools/ct_browser/ci_grab.sh`（CI 每日调用）。
    2. CDP 导航到 https://www.189.cn/wapportalweb/rateZone/index.html?provCode=609906
       （挑战自动通过，页面标题「资费专区」；chrome-devtools MCP 连 own-Chrome 都过不了，
        真实 Chrome 一次过 —— 别再走 MCP 的弯路）
    3. 在页面上下文执行 JS（页面已加载 CryptoJS）：
       CryptoJS.AES.encrypt(Utf8.parse(plain), Utf8.parse('telecom_wap_2018'),
         {mode: ECB, padding: Pkcs7}).toString() 造包体 → fetch(tariffSection.do)
    4. 响应是**明文 JSON**（不加密），code=W_0000，sessionid 由服务端下发
       （请求里给空串也能过）。
  采集脚本见 `probes/tools/ct_browser/`（`ci_grab.sh` + `cdp_desktop_step.py` +
  `harvest_hb.js`），原始产物 = 本目录 `.ct_raw.json`（fetch_all 的唯一输入）。

⚠️ **采不到时的行为**：本模块只做纯转换，采不到就抛错；调用方
  （`tariff_monitor.net_round`）会识别出来并退回**仓库快照渲染**，
  页面照样有四网、基线日期照实。电信是「机会性采集」，不是硬依赖。

**省份代码**：硬编码在页面组件 Index-1f2bc0ae.js 里
  （provinceCode 6 位，如 北京=609001）；**河北 = 609906**。
  ⚠️ 请求体里的 provCode 与 URL ?provCode= 是同一体系（6 位省码）；
     早期抓包明文里的 `1000000037` 是**集团**视图的省码，也有效（树带二级分类）。
  ⚠️ 查询参数 `type`：**type=1 才是「按 lable1Id 过滤」**；type≠1（0/2/3）
     会忽略 lable1Id 返回全量 884 条 —— 别拿它做分类轮询，会采到 5 份全同副本。

**tariffAttr（1/2/3）**：省级 1/2/3 与是否过期**零相关**（2026-09-22 全量核对：无一过期条目），
  语义未定；页面模板根本不使用 a1 字段，省级统一置 "2" 与其他网对齐。
  ★ **"3" 现在有确定语义了（2026-10-03）**：集团资费公示（provCode=1000000037，
  harvest_hb.js 的 jt 块另族 GET 接口采的，`_normalize_jt` 归一）——
  tariff_monitor.ATTR_CN["3"]="集团资费"，电信因此凑齐「本省/集团」两档板块。
  ── 归一化出口两族 ──
    · _normalize：省级（结构化字段 fees/feesUnit/onlineDay…）
    · _normalize_jt：集团（HTML 详情族 jbxx/ffnr/report_no，宁空勿错）
════════════════════════════════════════════════════════════════════

输出与 unicom/cbn 适配器同构：``fetch_all() → {fetchedAt, groups:[{tariffAttr,
type2, type2Name, entries:[]}], entries:[]}``，供 ``tariff_monitor.rows_of()``
与 ``build_html()`` 消费（entry 字段名对齐移动那套：name/fees/data/dataUnit/
call/applicablePeople/channel/onlineDay/offineDay/reportNo/otherContent/...）。
"""
import datetime
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, ".ct_raw.json")
RAW_GZ = RAW + ".gz"

PROV = "609906"
PROV_NAME = "河北"
PROV_URL = "https://www.189.cn/wapportalweb/rateZone/index.html?provCode=" + PROV
EP = "https://www.189.cn/wapportalweb/wapportalweb/tariffSection.do"
OK_CODE = "W_0000"

# 费用单位 → 是否「月费」口径（f 参与页面月费筛选/排序，单位不对就宁空勿错）
_MONTHLY = re.compile(r"^元/(每?1?月|月)(/.*)?$")
# dataUnit 归一：MB / M → GB（页面 gb() 只认 MB/GB/TB 前缀，这里在源头统一成 GB）
_SEC_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def log(m):
    print(time.strftime("[%H:%M:%S] ") + m, flush=True)


def _day(v):
    """'2025-10-30 00:00:00' → '20251030'（页面 toDate 只认 8 位数字）。"""
    m = _SEC_RE.match(str(v or "").strip())
    return ("%s%s%s" % m.groups()) if m else ""


def _gb(v, unit):
    """流量统一成 GB 字符串：'500','MB' → '0.49'；'' → '0'。"""
    u = str(unit or "").strip().upper()
    try:
        f = float(str(v).strip())
    except Exception:
        return "0", "GB"
    if not f:  # 0 或 0.0
        return "0", "GB"
    if u.startswith("GB"):
        g = f
    elif u in ("MB", "M"):  # 'M' 实测 7 条，语境均为流量 MB
        g = f / 1024.0
    elif u.startswith("TB"):
        g = f * 1024.0
    else:  # 未知单位：保 0，不猜
        return "0", "GB"
    return (str(int(g)) if g == int(g) else ("%.2f" % g).rstrip("0").rstrip(".")), "GB"


def _fee(v, unit):
    """月费口径才给数：('10','元/月')→'10'；(任何,'元/次'|'积分/次'|'')→''。

    🔴 单位不是「月」的（一次性/积分/最低消费外）绝不能塞进 f ——
    页面月费筛选（0/10/30/60/100 元阈值）会把『充100元返100元』当成月费 100。
    原文费用语义并进 otherContent，页面详情里仍可见。
    """
    u = str(unit or "").strip()
    if not _MONTHLY.match(u):
        return ""
    try:
        f = float(str(v).strip())
    except Exception:
        return ""
    return str(int(f)) if f == int(f) else ("%.2f" % f).rstrip("0").rstrip(".")


def _normalize(e, lable1_name, lable1_id):
    """zoneTitleList 条目 → 移动那套 entry 字段名（rows_of 的消费契约）。"""
    fees = _fee(e.get("fees"), e.get("feesUnit"))
    data, du = _gb(e.get("data"), e.get("dataUnit"))
    x = str(e.get("otherContent") or "").strip()
    # 费用原文（非月费口径时页面 f 为空，详情里必须还能看到真实费用）。
    # 🔴 原判据 `(fv and uv)` 要求金额与单位**同时非空**——实测 9 条「fees=0
    #    且无单位」的条目（免费体验类）费用信息整条丢失。改为只要金额非空
    #    就进注记（单位空则不拼接），残缺也比静默丢失强（2026-10-04 审查项）。
    fv, uv = str(e.get("fees") or "").strip(), str(e.get("feesUnit") or "").strip()
    fee_note = (fv + " " + uv).strip() if fv else ""
    if fee_note and fee_note not in x:
        x = ("费用：" + fee_note + "；" + x) if x else ("费用：" + fee_note)
    r = {
        "name": str(e.get("name") or "").strip(),
        "tariffName": str(e.get("name") or "").strip(),
        "fees": fees,
        "data": data,
        "dataUnit": du,
        "call": str(e.get("call") or "0").strip() or "0",
        "applicablePeople": str(e.get("applicablePeople") or "").strip()
                            or (PROV_NAME + "电信用户"),
        "channel": str(e.get("channel") or "").strip(),
        "onlineDay": _day(e.get("onlineDay")),
        # 🔴 键名是 offineDay（没有 f）—— rows_of 用的就是这拼写，改了就断
        "offineDay": _day(e.get("offlineDay")),
        "reportNo": str(e.get("reportNo") or e.get("seqNo") or "").strip(),
        "otherContent": x,
        "extraFees": "",          # 该网无「套外资费」独立字段（responsibility 是违约责任，语义不同）
        "validPeriod": str(e.get("validPeriod") or "").strip(),
        "brandwidth": str(e.get("bandwidth") or "").strip(),
        # 分组与显示用
        "type2": lable1_id,
        "type2Name": lable1_name,
        "tariffAttr": "2",        # 页面不用 a1；与其他网行对齐
        "_tariffAttrRaw": str(e.get("tariffAttr") or ""),   # 原值留给审计（1/2/3 语义未定）
        "_province": str(e.get("applicableAreaLabel") or PROV_NAME),
        # ★ 地域码单独留一份（tariff_monitor.scope_of 要读）：原来只留了文字标签，
        #   而「邢台」要靠 3190 这个码判 —— 884 条里有 26 条声明了地市码（含 3190），
        #   只认文字会在名称没写地市时漏判。文字标签仍保留，两者互补。
        "_areaCodes": str(e.get("applicableArea") or ""),
    }
    return r


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _jt_sec(jb, label):
    """jbxx 详情 HTML 里取「<span class="dark">标签：</span>值</p>」的值（去内嵌标签）。"""
    m = re.search(label + r"：</span>(.*?)</p>", jb, re.S)
    return _strip_tags(m.group(1)) if m else ""


def _jt_table(ffnr):
    """ffnr 内容表格 → {表头: 值}。表头行 <th>、值行 <td>，两行各自拆格配对。"""
    heads, vals = [], []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", str(ffnr or ""), re.S):
        cells = re.findall(r"<t([hd])[^>]*>(.*?)</t\1>", row, re.S)
        if not cells:
            continue
        kinds = [k for k, _ in cells]
        texts = [_strip_tags(v) for _, v in cells]
        if "h" in kinds and not vals:
            heads = texts
        elif all(k == "d" for k in kinds) and not vals:
            # ★ `and not vals`（2026-10-04 审查项）：表头后出现多行值行时取第一行，
            #   与 heads 的「首个」语义对齐 —— 原写法后者静默覆盖前者，留下哪行
            #   全凭上游排布。
            vals = texts
    return dict(zip(heads, vals))


def _normalize_jt(e, lable1_name, lable1_id, l2name):
    """集团条目（newTarifZone3Title，HTML 详情族）→ 移动字段名契约。

    与省级 `tariffSection.do` 的结构化字段不同族：详情是一坨 HTML ——
      · jbxx：资费类型 / 资费标准 / 适用范围 / 适用地区 / 销售渠道 /
        上下线时间 / 有效期限 / 退订方式 / 在网要求 / 违约责任
      · ffnr：内容表格（语音 / 通用流量 / 短信 / 带宽 / IPTV / 权益 …）
    解析不出来的一律留空（宁空勿错），费用原文进 otherContent 保底可见。
    """
    jb = str(e.get("jbxx") or "")
    std = _jt_sec(jb, "资费标准")                       # '0元/1月' / '169元/7天' / '50元'
    m = re.match(r"\s*([\d.]+)\s*(元.*)?", std)
    fv, uv = (m.group(1), (m.group(2) or "").strip()) if m else ("", "")
    fees = _fee(fv, uv)
    tab = _jt_table(e.get("ffnr"))
    # 语音 '0分钟' → 0；通用流量 '500MB' → (500, MB)；'畅享' 等无数值 → 0（不猜）
    call = next((re.sub(r"\D", "", v) for k, v in tab.items() if "语音" in k and re.sub(r"\D", "", v)), "0")
    data, du = "0", "GB"
    for k, v in tab.items():
        if "流量" in k:
            md = re.match(r"\s*([\d.]+)\s*(MB|M|GB|TB)", v, re.I)
            if md:
                data, du = _gb(md.group(1), md.group(2))
            break
    on, off = "", ""
    mm = re.search(r"(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})", _jt_sec(jb, "上下线时间"))
    if mm:
        on, off = mm.group(1), mm.group(2)
    x = str(e.get("other_content") or "").strip()
    oth = str(e.get("others") or "").strip()
    if oth and oth not in x:
        x = (x + "；" + oth) if x else oth
    # 非月费口径的费用原文必须可见（同 _normalize 的 fee_note 规则：
    # 金额非空即写、单位空则不拼接 —— 2026-10-04 对齐，见那边 🔴 注释）
    fee_note = (fv + " " + uv).strip() if fv else ""
    if fee_note and fee_note not in x:
        x = ("费用：" + fee_note + "；" + x) if x else ("费用：" + fee_note)
    return {
        "name": str(e.get("name") or "").strip(),
        "tariffName": str(e.get("name") or "").strip(),
        "fees": fees,
        "data": data,
        "dataUnit": du,
        "call": call or "0",
        "applicablePeople": _jt_sec(jb, "适用范围") or "全国电信用户",
        "channel": _jt_sec(jb, "销售渠道"),
        "onlineDay": _day(on),
        # 🔴 键名是 offineDay（没有 f）—— rows_of 用的就是这拼写，改了就断
        "offineDay": _day(off),
        "reportNo": str(e.get("report_no") or e.get("id") or "").strip(),
        "otherContent": x,
        "extraFees": str(e.get("extra_fees") or "").strip(),
        "validPeriod": _jt_sec(jb, "有效期限"),
        "brandwidth": next((v for k, v in tab.items() if "带宽" in k), ""),
        # 分组与显示用
        "type2": lable1_id,
        "type2Name": lable1_name,
        # 二级归属：接口不在条目里带，采集侧用 lable2Id 反查建 map（81/524 条有），
        # 其余细分留空 → rows_of 退回一级栏目名（与移动/省级电信同语义）
        "type3Name": str(l2name or ""),
        "tariffAttr": "3",        # 3 = 集团（ATTR_CN["3"]="集团资费"，见 tariff_monitor）
        "_tariffAttrRaw": "3",
        "_province": "集团（全国）",
        # 集团条目适用地区=全国，无地市码；文案兜底照走（构建侧统一判）
        "_areaCodes": "",
    }


def raw_path():
    """原始采集产物的实际路径（.ct_raw.json 优先，退回 .gz），都没有则 None。"""
    return RAW if os.path.exists(RAW) else (RAW_GZ if os.path.exists(RAW_GZ) else None)


# 采集器（harvest_hb.js）记的是 `new Date().toISOString()` —— **UTC**，带 Z 后缀。
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|\+00:?00)?$", re.I)


def _local_ts(v):
    """采集时刻 → **北京时间** 'YYYY-MM-DD HH:MM:SS'。

    🔴 为什么要转：定时任务是北京时间 06:00 = UTC 前一天 22:00，直接取字符串前 10 位
    会把「今天采的」记成昨天。后果有两处，都很难查：
      · 页面基线日期比真实采集日晚一天（数据看着永远慢一拍）；
      · `tariff_monitor` 的「本轮采到了吗」闸门（比对日期）会**拒绝**当天采到的数据，
        悄悄退回快照渲染，页面上却只显示「沿用快照」——像是采集失败，其实成功了。

    🔴 **只在明确带 UTC 标记（`Z` / `+00:00`）时才 +8**。不带标记的一律当成
    已经是本地时间原样返回 —— 否则「别的网那种已经是北京时间的 fetchedAt」
    会被二次偏移（实测：`'2026-09-22 18:52:49'` 会变成次日 02:52）。
    显式判断，不依赖 runner 的 TZ。
    """
    s = str(v or "").strip()
    m = _ISO.match(s)
    if not m:
        return s
    y, mo, d, h, mi, sec, tz = m.groups()
    try:
        dt = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec))
    except ValueError:
        return s
    if tz:                      # 带 Z / +00:00 ⇒ UTC，加 8 小时
        dt += datetime.timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def raw_day():
    """原始采集产物里记的**采集日期**（YYYYMMDD）；文件缺失 / 损坏则返回空串。

    ★ 这是「本轮到底采没采到电信」的唯一判据，也是采集与渲染解耦的关键：
      CI 每轮都会重采一次（见 .github/workflows/tariff-daily.yml），采到 → 就是当天；
      采不到 → `.ct_raw.json` 根本不存在（该文件不入库）⇒ 调用方退回**快照渲染**。
      ⚠️ 用文件 mtime 判断是不行的：CI 每次都是新 checkout，mtime 恒为「现在」，
         一份上周提交进来的 .ct_raw.json 也会被当成刚采的。
      ⚠️ 日期必须走 `_local_ts()` 转成北京时间：定时任务落在 UTC 的**前一天**，
         直接用 UTC 日期会把当天采集判成「非当天」。
    """
    p = raw_path()
    if not p:
        return ""
    try:
        if p.endswith(".gz"):
            import gzip
            with gzip.open(p, "rt", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
    except Exception:
        return ""
    return _local_ts(raw.get("fetchedAt"))[:10].replace("-", "")


def fetch_all():
    """读 .ct_raw.json（真实浏览器采集产物，见模块头）→ 中间格式。

    🔴 这里**不发任何网络请求** —— 电信的采集必须借真实浏览器上下文，
    本函数只做「原始数据 → 与其他网同构的中间格式」这一步纯转换。
    """
    path = raw_path()
    if not path:
        raise SystemExit(
            "!! 缺少电信原始采集数据 %s（或 .gz）\n"
            "   采集方法（必须真实 Chrome，脚本直连会被瑞数 WAF 拦）：\n"
            "   · Linux（含 GitHub Runner）：bash probes/tools/ct_browser/ci_grab.sh\n"
            "   · Windows 本机：\n"
            "       1) chrome.exe --remote-debugging-port=9223 --user-data-dir=<临时目录>\n"
            "       2) CDP 导航 %s\n"
            "       3) 页面内 eval probes/tools/ct_browser/harvest_hb.js\n"
            "       4) 结果存为本文件" % (RAW, PROV_URL))
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    if not str(raw.get("provCode")) == PROV:
        raise SystemExit("!! .ct_raw.json 的 provCode=%s ≠ %s（换省了？先改 PROV）"
                         % (raw.get("provCode"), PROV))

    groups, entries, seen = [], [], set()
    l1map = {x.get("name"): x.get("id") for x in (raw.get("lableOneList") or [])}
    for name, sec in (raw.get("sections") or {}).items():
        arr = sec.get("zoneTitleList") or []
        lable1_id = l1map.get(name) or ""
        # 🔴 服务端 count 是分类内真实条数 —— len 对不上说明被截断，宁可报错
        if sec.get("count") is not None and int(sec["count"]) != len(arr):
            raise SystemExit("!! 分类「%s」count=%s ≠ 实收 %d（疑似截断，拒绝采空）"
                             % (name, sec["count"], len(arr)))
        g = {"tariffAttr": "2", "type2": lable1_id, "type2Name": name, "entries": []}
        for e in arr:
            n = _normalize(e, name, lable1_id)
            k = n["reportNo"] or ("name:" + n["name"])
            if k in seen:      # 分类间理论不重叠，这是保险
                continue
            seen.add(k)
            g["entries"].append(n)
            entries.append(n)
        groups.append(g)
    # ══ 集团资费公示（provCode=1000000037，harvest_hb.js 的 jt 块）══════════
    # 2026-10-03 补采：此前只采省级，面板缺「集团资费公示」整块（524 条）。
    # tariffAttr=3 → ATTR_CN["3"]="集团资费" → 电信由此凑齐两档、页面自动出
    # 「资费范围」维度。jt 块缺失 / 采集失败（err）都不算硬错：省级数据照常出。
    jt = raw.get("jt") or {}
    if isinstance(jt, dict) and jt.get("tree") and not jt.get("err"):
        l2map = jt.get("l2map") or {}
        for x in jt["tree"]:
            name, lid = str(x.get("lable1Name") or ""), str(x.get("lable1Id") or "")
            sec = (jt.get("sections") or {}).get(name) or {}
            arr = sec.get("zoneTitleList") or []
            g = {"tariffAttr": "3", "type2": lid, "type2Name": name, "entries": []}
            for e in arr:
                n = _normalize_jt(e, name, lid, l2map.get(e.get("report_no") or e.get("id")))
                k = n["reportNo"] or ("name:" + n["name"])
                if k in seen:   # 与省级共用一份去重（同 reportNo 以省级为准）
                    continue
                seen.add(k)
                g["entries"].append(n)
                entries.append(n)
            groups.append(g)
    # 集团组排在本省之后（同组内仍按 套餐→加装包→营销活动 的既有次序）
    groups.sort(key=lambda g: (0 if str(g.get("tariffAttr")) == "2" else 1,
                               {"套餐": 0, "加装包": 1, "营销活动": 2}.get(g["type2Name"], 9),
                               g["type2Name"]))
    return {"province": PROV, "provinceName": PROV_NAME + "省",
            "endpoint": EP, "fetchedAt": _local_ts(raw.get("fetchedAt")),
            "sourceUrl": PROV_URL,
            "groups": groups, "entries": entries}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    d = fetch_all()
    stat = {"%s/%s" % (g["tariffAttr"], g["type2Name"]): len(g["entries"]) for g in d["groups"]}
    log("电信 %s：%d 条 %s（fetchedAt=%s）"
        % (PROV_NAME, len(d["entries"]), stat, d["fetchedAt"]))
    no_fee = sum(1 for e in d["entries"] if not e["fees"])
    log("  非月费口径（f 留空，费用原文在 otherContent）：%d 条" % no_fee)
    bad = [e for e in d["entries"] if not e["onlineDay"] or not e["offineDay"]]
    log("  上下线日期缺失：%d 条" % len(bad))
