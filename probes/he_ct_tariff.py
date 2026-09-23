# -*- coding: utf-8 -*-
"""中国电信 APP「资费专区」探针（河北）—— **加解密层已完整打通并逐字节复现**。

来源页面：https://www.189.cn/wapportalweb/rateZone/index.html
接口：    POST https://www.189.cn/wapportalweb/wapportalweb/tariffSection.do
          ⚠️ 路径里 `wapportalweb` **重复两遍**，不是笔误。

================================================================================
一、加解密（2026-09-22 实测，三条抓包样本全部逐字节复现）
================================================================================
页面加载两份 JS（都在 www.189.cn 上，直连可取，不过 WAF）：

  · /client/wap/common/js/aes.js                        —— CryptoJS
      UMD 打包，**同时含 mode-ecb 与 pad-pkcs7**（这就是它用 ECB 的正面证据）
      md5 2080986a059e6266949418c68c34414f（2026-09-22）
  · /client/wapportalweb/vue_common/js/conscript.js     —— obfuscator 混淆
      把 `\\xNN` 转义解开即可见 encrypt / decrypt 源码。

反混淆后的等价源码：

    function encrypt(word, key) {
      return CryptoJS.AES.encrypt(
        CryptoJS.enc.Utf8.parse(word),
        CryptoJS.enc.Utf8.parse(key || 'telecom_wap_2018'),
        { mode: CryptoJS.mode.ECB, padding: CryptoJS.pad.Pkcs7 }
      ).toString();                       // 默认 OpenSSL formatter ⇒ base64
    }

★ 结论：**算法 = AES-128-ECB + PKCS7；密钥 = b'telecom_wap_2018'**（16 字节，就硬编码
  在这份**公开** JS 里）；**没有 IV**（ECB 不需要）。
★ 整段包体 = `Base64(AES_ECB_PKCS7(UTF-8(JSON)))`。
  Content-Type 虽写 `application/x-www-form-urlencoded`，但**里面没有 k=v**，
  整段就是那串 base64 —— 别按表单去解析它。

确定性自证（本模块 `selftest()` 每次都跑）：
  拿抓包解密出的 JSON，用同一密钥**重新加密**，得到与原包体**逐字节相同**的 base64；
  三条样本（216 / 216 / 280 字符）全中。⇒ 契约成立，不是巧合。

================================================================================
二、ECB 的两个可观察后果（抓包时不用解密就能用）
================================================================================
1. **相同明文前缀 ⇒ 相同密文前缀**。
   两条分类树请求（#998 / #1053）的密文**共同前缀 128 个 base64 字符 = 96 字节 = 6 个整块**；
   而这 96 字节正好是明文里的
   `{"headerInfo": { "functionCode": "tariffSectionHome"},"requestContent":{"ticket":"","sessionid":"`
   —— 第 7 块才因 sessionid 取值不同而分叉。
   （分组链模式 CBC/CFB/OFB/GCM 从第 2 块起就会散开，绝不可能出现「6 个整块逐字节相同」。）
2. **看密文前缀就能判请求动作**：分类树 ↔ 列表的明文共同前缀是 47 字节
   （`…"tariffSection`，下一个字符正是 **H**ome / **Q**uery）⇒ 密文共同 2 个整块
   = 42 个 base64 字符（实测 42 ✓）。

⚠️ 附带推论：ECB 下**同一明文永远同一密文**，报文可直接被指纹化比对
   —— 这个接口等于**没有加密**（密钥在公开 JS 里，且无随机化）。

================================================================================
三、响应：明文 JSON，成功码 W_0000
================================================================================
六套体系的成功码各不相同，别串用：移动 `1`/`0000`、联通 `0000`、广电 `000000`、**电信 `W_0000`**。

- 形态 A **分类树**（约 2077 B）：`responseContent.lableOneList[]`
  5 个一级，其中 3 个带 `lableTow[]` 二级（3 + 3 + 14 = 20 个）。
  一级 id 与抓包实测：
    `69800a2b83bc522f970bd729` 套餐
    `69800a2b83bc522f970bd72a` 加装包         → 流量包 / 增值业务 / 其他
    `69800a2b83bc522f970bd72b` 营销活动       → 促销 / 合约 / 其他
    `69800a2b83bc522f970bd72c` 国际/港澳台资费 → 中欧 / 东南亚 / 东欧 / 北美洲及加勒比海 /
                                                非洲 / 南欧 / 大洋洲 / 西亚 / 西欧 /
                                                中国港澳台地区 / 东亚 / 北欧 / 南亚 / 中亚
    `69ca2e1e49279117fdf2c570` 国内（不含港澳台）标准资费
- 形态 B **列表**（约 256 B）：`zoneTitleList[]` + `zoneTitleListCount` + `priceInterval`。
  抓包那两次 `zoneTitleList` 都是空数组 ⇒ **真正的资费列表响应没落到盘上**（见 §五），
  列表项的字段名**尚未确认**。

`responseContent.sessionid` 在多个响应间复用；而请求明文里也带 `sessionid`
⇒ 它是**客户端持有、来回传**的值，不是每次服务端新发。

================================================================================
四、请求头与风控
================================================================================
    x-requested-with: com.ct.client
    x-qd-reqtime: <毫秒时间戳>                     ← 抓包实测 1790064138220
    origin / referer: https://www.189.cn/wapportalweb/rateZone/index.html
    user-agent: CtClient;13.4.0;Android;16;23113RKC6C
    Content-Type: application/x-www-form-urlencoded （但包体是纯 base64，见上）
    cookie: 两条风控 cookie —— `EYg4xOZq0mLeS`（跨会话恒定，像设备指纹）
                                   `EYg4xOZq0mLeT`（每请求都变，像 nonce）
            另有 `zhizhendata2015jssdkcross`（神策埋点 SDK）与 `sessionid`。

★ `ticket` 是**空字符串** ⇒ 这个接口**不需要登录**。（这是电信三网里最"客气"的一个。）
🔴🔴 **WAF 是「瑞数类」JS 挑战，requests/curl 过不去（2026-09-22 17:3x 端到端实测）**：
  - `GET …/rateZone/index.html` → 412（早已知）
  - **`POST …/tariffSection.do`（API 本身）→ 也是 412**（本次实测，build_tree/build_query 都试了）
  - 412 的响应体是一个 **JS 挑战页**：`<meta id="3etE7dr7M7O6" content="…">` + `$_ts=…`，
    与 HTML 页面同构（同一套挑战脚本）
  - **curl（Schannel TLS）同样 412** ⇒ 拦的不是 TLS 指纹，是**挑战脚本没跑**
  ⇒ `EYg4xOZq0mLeS` / `EYg4xOZq0mLeT` 这两条"风控 cookie"**就是挑战脚本算出来的**
    （前者近似恒定、后者每请求变 —— 瑞数类方案的典型特征）
  ⇒ **纯 requests/curl 不可能通过**；要采数据必须走真实 WebView/浏览器
    （本仓库已有 `probes/tools/cdp.py` / `chrome_local.py` / `phone_js.py` 可用），
    或改走公开公示页 `www.189.cn/jtzfzq`（同一个 WAF，同样要浏览器）。

================================================================================
五、抓包里丢掉的与不许信的
================================================================================
1. 🔴 **Fiddler 的 `apply_filters` 会假阴性**：在已产生 10099 条会话后，用
   `Host Contains "10099"` 过滤仍返回 0，而**不加过滤**的 `get_sessions` 能查到。
   ⇒ 判「某会话在不在」**只信不过滤的 `get_sessions`**。
2. 🔴 Fiddler `netcore.log` 里有 **189 条** `Chunked body did not terminate properly
   with 0-sized chunk.` ⇒ 一批 chunked 响应体没落盘，**真正的资费列表很可能已经丢了**。
3. 🔴 LiveTraffic 是**滚动窗口**：id 区间几分钟内就从 895~1064 滚到 1544~3544，
   电信那批随即不可再取 ⇒ **证据必须当场落盘**（本模块 §六 的金标准就是这么抢救下来的）。

================================================================================
六、当前进度
================================================================================
✅ 加解密层：完全打通，逐字节可复现（本模块就是证据）。
✅ 分类树：一级/二级全量 id + 名称已拿到。
✅ 网络层定性：**API 端点同样被 JS 挑战 WAF 拦截（412），requests/curl 均过不去**
   （2026-09-22 17:3x 实测，见 §四）。
✅ 数据采集：**已落地**（2026-09-22 18:0x，河北 884 条入库页面）。
   路线 = 真实 Chrome(--remote-debugging-port=9223，**不带** --enable-automation)
   + CDP 页面内执行采集 JS（页面自带 CryptoJS 造包体，响应是明文 JSON）。
   ⚠️ 自动化浏览器（navigator.webdriver=true）会被 WAF 直接 400 空响应——
      chrome-devtools MCP 的受管 Chromium 就这么被拒，别再走那条弯路。
   工具：probes/tools/ct_browser/{cdp_desktop_step.py,harvest_hb.js}；
   适配器：cloud/tariff/ct_monitor.py（fetch_all 读 .ct_raw.json 纯转换）。
   新事实：`type=1` 才是「按 lable1Id 过滤」，type≠1 忽略 lable1Id 回全量；
   tariffAttr(1/2/3) 与过期零相关、页面不使用；sessionid 给空串也能过。
   省份码硬编码在 Index-1f2bc0ae.js（河北=609906、北京=609001、集团=1000000037）。
✅ 云端自动化：**已接入**每日巡检（2026-09-22 实测 ubuntu-latest + Xvfb + 有头 Chrome
   即可通过瑞数挑战，884 条逐项采全）。电信已注册 NET_LIVE / NET_RUN；
   采集脚本 probes/tools/ct_browser/ci_grab.sh，采不到时自动退回仓库快照渲染。
   （旧注记写的「暂不注册 NET_RUN、rebuild_ct.py 单独重建」已作废 ——
     那个脚本已删除，本机重建改走 rebuild_offline.py。）

用法：
    python he_ct_tariff.py                 # 默认：跑 selftest（不联网）
    python he_ct_tariff.py dec <base64>    # 解一段包体
    python he_ct_tariff.py enc '<json>'    # 把 JSON 编成包体
    python he_ct_tariff.py tree            # 打印「分类树」请求体
    python he_ct_tariff.py query <lable1Id> <sessionid>
    python he_ct_tariff.py call tree       # 真打接口（需环境变量 CT_COOKIE）

退出码：0 正常 ｜ 3 请求失败 ｜ 4 会话失效。
"""

import base64
import json
import os
import sys
import time

# ----------------------------------------------------------------- 常量
KEY = b"telecom_wap_2018"          # 16 字节 ⇒ AES-128；硬编码在 conscript.js 里
EP = "https://www.189.cn/wapportalweb/wapportalweb/tariffSection.do"
PAGE = "https://www.189.cn/wapportalweb/rateZone/index.html"
UA = "CtClient;13.4.0;Android;16;23113RKC6C"
PROV_CODE = "1000000037"           # ⚠️ 页面每个请求都带它；但与 URL 里的 `provCode=609001`
                                   #    不是同一个值 ⇒ 哪个才是「省份」尚未确认，别当成省码用
OK_CODE = "W_0000"

# ----------------------------------------------------------------- 金标准
# 2026-09-22 16:01~16:02 抓包原文（Fiddler LiveTraffic #998 / #1053 / #1055）。
# 只留**密文**，明文用「长度 + 结构正则」核对 ⇒ 不必把服务端 sessionid 写进仓库。
# 断言 encrypt(decrypt(body)) == body，密钥错一位这一条就会失败。
GOLD = [
    ("#998  分类树 16:01:42",
     "pJcn/pbYWEMS+Kg2i/hQEqsUL53e0f1innArhK0Ve8/SEdEH6L2dTBS54CyEAqOTzqep2za687EO9t0wv5a"
     "lDiSQGTGyx+VyFXDd1SleGfVvX2gkkH16fBUWwK2S8T9aYKTcP8O53lbsJwDeneiNFVH24m3TboevmhRxu/"
     "b+/Xcpys3gJnYtReRqx7MsEei8G4+tJsH4xeHsRLFI9tIVHw==",
     156, "tariffSectionHome"),
    ("#1053 分类树 16:02:14",
     "pJcn/pbYWEMS+Kg2i/hQEqsUL53e0f1innArhK0Ve8/SEdEH6L2dTBS54CyEAqOTzqep2za687EO9t0wv5a"
     "lDiSQGTGyx+VyFXDd1SleGfVvX2gkkH16fBUWwK2S8T9ai+tnKlLNXkLOS7bP6gF0PNMYMg4TvxnQ27/"
     "9qTNeHzq2svKcXh+GdBImAyi/pCPKG4+tJsH4xeHsRLFI9tIVHw==",
     156, "tariffSectionHome"),
    ("#1055 列表   16:02:16",
     "pJcn/pbYWEMS+Kg2i/hQEqsUL53e0f1innArhK0Ve8+QkXLSm57twFn5oN0u+R8wgGDzg7t26ZuMrXH6y9"
     "2hJ06y1I2aYWUyeen1yMCAQZCZkKt/86bIfzUawtcSknWWOBPWJ3qcUt9bauX1Fc46O8YOD8TIJl0hup"
     "beHxoaLWlHfDrJ97FAQkOTVWYzV+V20YmxTdHrSDrq2YLntLDZOJ96NVlGsVopV2P2EZKNk/S2RAVt7r"
     "WBu0gzfU06h5OMzcyk2+VTazcna39hZtjp9g==",
     192, "tariffSectionQuery"),
]
GOLD_RE = (r'^\{"headerInfo": \{ "functionCode": "tariffSection(Home|Query)"\},'
           r'"requestContent":\{[^}]*\}\}$')


# ----------------------------------------------------------------- 加解密
def _pad(b):
    n = 16 - len(b) % 16
    return b + bytes([n]) * n


def _unpad(b):
    if not b:
        raise ValueError("空明文")
    n = b[-1]
    if not 1 <= n <= 16 or b[-n:] != bytes([n]) * n:
        raise ValueError("PKCS7 填充不合法（密钥不对？）")
    return b[:-n]


def encrypt(text, key=KEY):
    """明文 → 包体（base64）。text 可以是 str 或 JSON 可序列化对象。"""
    from Crypto.Cipher import AES
    if not isinstance(text, (str, bytes)):
        text = json.dumps(text, ensure_ascii=False, separators=(",", ":"))
    raw = text.encode("utf-8") if isinstance(text, str) else text
    ct = AES.new(key, AES.MODE_ECB).encrypt(_pad(raw))
    return base64.b64encode(ct).decode("ascii")


def decrypt(body, key=KEY):
    """包体（base64 或 bytes）→ 明文 str。解不开就抛错，绝不返回半截。"""
    from Crypto.Cipher import AES
    if isinstance(body, str):
        body = base64.b64decode(_squash(body))
    return _unpad(AES.new(key, AES.MODE_ECB).decrypt(body)).decode("utf-8")


def _squash(s):
    """抓包文本里常夹换行/空格，base64 解码前先去掉。"""
    return "".join(s.split())


# ----------------------------------------------------------------- 构造请求
# 🔴 模板里的空格（`{ "functionCode"`）是**页面源码原样**，不是手滑。
#    ECB 无随机化 ⇒ 明文逐字节决定密文；虽然服务端解析 JSON 会忽略空格，
#    但既然要复刻就复刻到底，免得日后拿「密文不一致」当疑点浪费时间。
_TMPL = '{"headerInfo": { "functionCode": "%s"},"requestContent":%s}'


def body_of(function_code, req):
    """req 可以是 dict，也可以是**原文 JSON 文本**（推荐，可控制键顺序/间距）。"""
    txt = req if isinstance(req, str) else json.dumps(
        req, ensure_ascii=False, separators=(",", ":"))
    return encrypt(_TMPL % (function_code, txt))


def build_tree(sessionid=""):
    """分类树请求：requestContent = {ticket, sessionid, provCode}"""
    return body_of("tariffSectionHome",
                   '{"ticket":"","sessionid":"%s","provCode":"%s"}' % (sessionid, PROV_CODE))


def build_query(lable1_id, sessionid="", type_=1):
    """列表请求：requestContent = {sessionid, type, provCode, lable1Id}"""
    return body_of("tariffSectionQuery",
                   '{"sessionid":"%s","type":%d,"provCode":"%s","lable1Id":"%s"}'
                   % (sessionid, type_, PROV_CODE, lable1_id))


# ----------------------------------------------------------------- 网络层
def fetch(body, cookie, timeout=20):
    """真打接口。**尚未端到端验证过**（只验证了加解密）。

    cookie：自备，至少要有 EYg4xOZq0mLeS / EYg4xOZq0mLeT 两条风控 cookie。
    🔴 `trust_env = False`：否则 requests 会偷读系统/环境里的残留代理（Fiddler 8866 /
       V2Ray），症状是连接被积极拒绝 —— 这条在移动/联通/广电那边都踩过。
    fail-closed：只要不是 W_0000 就抛错，绝不静默返回空列表。
    """
    import requests
    s = requests.Session()
    s.trust_env = False                      # ← 别删，见 docstring
    ts = int(time.time() * 1000)
    r = s.post(EP, data=body.encode("ascii"), timeout=timeout, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "x-requested-with": "com.ct.client",
        "x-qd-reqtime": str(ts),
        "Origin": "https://www.189.cn",
        "Referer": PAGE,
        "User-Agent": UA,
        "Cookie": cookie,
    })
    if r.status_code == 412:
        raise RuntimeError("412 —— WAF 挑战，需要在真实浏览器/WebView 里跑，curl 过不去")
    r.raise_for_status()
    js = r.json()
    code = (js.get("headerInfo") or {}).get("code")
    if code != OK_CODE:
        raise RuntimeError("接口未成功：code=%r message=%r" % (
            code, (js.get("headerInfo") or {}).get("message")))
    return js


# ----------------------------------------------------------------- 自测
def selftest(verbose=True):
    import re
    ok = True

    def say(*a):
        if verbose:
            print(*a)

    say("== 1. 金标准：密文 → 解密 → 重新加密，要求逐字节相同 ==")
    for tag, body, nlen, fn in GOLD:
        try:
            pt = decrypt(body)
        except Exception as e:
            ok = False
            say("   ✗ %s 解密失败：%s" % (tag, e))
            continue
        again = encrypt(pt)
        hit_rt = (again == body)
        hit_len = (len(pt) == nlen)
        hit_re = bool(re.match(GOLD_RE, pt))
        hit_fn = ('"%s"' % fn) in pt
        ok &= hit_rt and hit_len and hit_re and hit_fn
        say("   %s %-22s 密文%3d字符 明文%3d字节 往返%s 长度%s 结构%s functionCode%s"
            % ("✓" if (hit_rt and hit_len and hit_re and hit_fn) else "✗",
               tag, len(body), len(pt),
               "同" if hit_rt else "**不同**",
               "对" if hit_len else "**错**",
               "对" if hit_re else "**错**",
               "对" if hit_fn else "**错**"))
        say("       明文 = %s" % pt)

    say("== 2. 结构自洽：build_* 造出来的包体要能被解回同一份 JSON ==")
    b1 = build_tree("0123456789abcdef0123456789abcdef")
    d1 = json.loads(decrypt(b1))
    hit = (d1["headerInfo"]["functionCode"] == "tariffSectionHome"
           and d1["requestContent"]["provCode"] == PROV_CODE
           and d1["requestContent"]["ticket"] == "")
    ok &= hit
    say("   %s build_tree  → %s" % ("✓" if hit else "✗", decrypt(b1)))

    b2 = build_query("69800a2b83bc522f970bd729", "f" * 32)
    d2 = json.loads(decrypt(b2))
    hit = (d2["headerInfo"]["functionCode"] == "tariffSectionQuery"
           and d2["requestContent"]["lable1Id"] == "69800a2b83bc522f970bd729"
           and d2["requestContent"]["type"] == 1)
    ok &= hit
    say("   %s build_query → %s" % ("✓" if hit else "✗", decrypt(b2)))

    say("== 2b. 最硬的一条：从金标准密文反解出参数，再 build_* 重造，"
        "要求与原密文**逐字节相同** ==")
    # 注意这里**没有**把 sessionid 写进仓库：它是从已经入库的密文里现解出来的。
    # 这样既能把「构造口径」钉死（含空格、键序），又不多留一份会话值。
    for tag, src, builder in (("分类树", GOLD[0][1], "tree"),
                              ("列表",   GOLD[2][1], "query")):
        req = json.loads(decrypt(src))["requestContent"]
        if builder == "tree":
            again = build_tree(req["sessionid"])
        else:
            again = build_query(req["lable1Id"], req["sessionid"], req["type"])
        hit = (again == src)
        ok &= hit
        say("   %s %s：用 sessionid=%s… 重造，%s"
            % ("✓" if hit else "✗", tag, req["sessionid"][:8],
               "逐字节相同" if hit else "**与原密文不同**"))

    say("== 3. ECB 特征：同明文必得同密文；改一个字节则第 7 块起全变 ==")
    a = build_tree("A" * 32)
    b = build_tree("A" * 32)
    hit = (a == b)
    ok &= hit
    say("   %s 两次 build_tree 完全相同（ECB 无随机化）" % ("✓" if hit else "✗"))
    c = build_tree("B" * 32)
    # 比**字节**而不是比 base64 字符：明文共同前缀 97 字节 ⇒ 密文共同 6 个整块 = 96 字节。
    # （比字符数会在「第 129 个字符恰好相同」时以 1/64 的概率抖动，字节数不会。）
    ab, cb = base64.b64decode(a), base64.b64decode(c)
    common = 0
    for x, y in zip(ab, cb):
        if x != y:
            break
        common += 1
    hit = (common == 96)
    ok &= hit
    say("   %s sessionid 首字节不同 ⇒ 密文共同 %d 字节（期望 96 = 6 个整块）；"
        "base64 共同前缀 %d 个字符"
        % ("✓" if hit else "✗", common, common // 3 * 4))

    say("== 4. 反向：用错密钥必须失败（否则说明上面全是假通过）==")
    try:
        bad = decrypt(GOLD[0][1], key=b"telecom_wap_2019")
        ok = False
        say("   ✗ 错密钥居然解出了内容：%r" % bad[:40])
    except Exception as e:
        say("   ✓ 错密钥按预期失败：%s" % e)

    print("\n%s" % ("★ selftest 全部通过" if ok else "!! selftest 有失败项"))
    return ok


# ----------------------------------------------------------------- CLI
def main(argv):
    if not argv or argv[0] == "selftest":
        return 0 if selftest() else 1

    cmd = argv[0]
    if cmd == "dec":
        print(decrypt(argv[1]))
        return 0
    if cmd == "enc":
        print(encrypt(argv[1]))
        return 0
    if cmd == "tree":
        print(build_tree())
        return 0
    if cmd == "query":
        print(build_query(argv[1], argv[2] if len(argv) > 2 else ""))
        return 0
    if cmd == "call":
        cookie = os.environ.get("CT_COOKIE")
        if not cookie:
            print("需要环境变量 CT_COOKIE（含 EYg4xOZq0mLeS / EYg4xOZq0mLeT）", file=sys.stderr)
            return 4
        body = build_tree() if len(argv) < 2 or argv[1] == "tree" else argv[1]
        try:
            js = fetch(body, cookie)
        except Exception as e:
            print("请求失败：%s" % e, file=sys.stderr)
            return 3
        print(json.dumps(js, ensure_ascii=False, indent=1)[:4000])
        return 0

    print(__doc__.split("用法：")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
