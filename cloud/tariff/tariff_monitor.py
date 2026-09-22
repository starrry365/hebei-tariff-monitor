# -*- coding: utf-8 -*-
"""河北移动「资费专区」(nrapigate/nrtariff) 每日抓取 + 差异对比

适配云端 CI（GitHub Actions）与本地两种环境：
  - 路径全部相对脚本自身，不写死盘符
  - `--ssl-no-revoke` 仅 Windows Schannel 支持，Linux curl 会直接报 unknown option -> 按平台注入
  - 快照 gzip 压缩 + 只保留最近 KEEP 份，避免仓库体积失控
  - 查询页重建到 docs/index.html（不入 git），并 gzip 归档到 page/index.html.gz（入库）
    · 页面显示「数据基线日期」而非抓取时刻，数据没变则内容逐字节一致 -> 跳过归档
  - 数据量守卫：本次条目数 < 上次 60% 视为抓取异常 -> 不写快照、不出下线报告
  - 有变更时把摘要写进 GITHUB_STEP_SUMMARY（云端页面直接可读）

接口特性（2026-09-20 实测）：无需 APP / token / 签名，明文请求体 + curl 直取，
响应体 `{"body":"<密文>"}` 用网关密钥 AES-256-CBC 本地解密（见 mz_crypto.py）。
密钥不写在代码里：取环境变量 NRAPIGATE_KEY / NRAPIGATE_IV，或同目录 .nrapigate_key
文件（已 gitignore）；两边都没有直接抛错，绝不回退到硬编码值。

用法:
    python tariff_monitor.py              # 抓取 + 对比 + 报告 + HTML
    python tariff_monitor.py --no-html    # 只抓取与对比，不重建页面
    python tariff_monitor.py --render-only  # 不联网：用现有页面数据套当前模板重渲染
退出码: 0=正常  2=数据量骤降  3=网络全失败
"""
import datetime
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
try:
    import mz_crypto  # noqa: E402
    _MZ_ERR = None
except Exception as _e:  # noqa: E402
    # 加解密依赖（pycryptodome）只有真正抓取时才用得到。
    # 本地裸环境（如系统 Python 没装 pycryptodome）也必须能跑 --render-only，
    # 否则「改了模板想看一眼」就被一个跟本次任务无关的依赖卡住。
    mz_crypto = None
    _MZ_ERR = _e

SNAP = os.path.join(BASE, "snapshots")
CHG = os.path.join(BASE, "changes")
DOCS = os.path.join(BASE, "docs")
HTML_DST = os.path.join(DOCS, "index.html")
STATE_FILE = os.path.join(BASE, "state.json")
# 页面归档：查询页没有公网入口，本机靠这个文件「不跑抓取就能看」
PAGE_DIR = os.path.join(BASE, "page")
PAGE_GZ = os.path.join(PAGE_DIR, "index.html.gz")
KEEP_SNAPSHOTS = 60          # 只保留最近 60 份快照（gzip 后约 0.53MB/份，工作区稳定在 ~32MB）
DEGRADE_RATIO = 0.6          # 数据量跌破上次 60% 判为异常

ROOT = "https://h.app.coc.10086.cn/website/nrapigate/"
REF = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
PROV = "311"   # 河北
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": UA,
    "Origin": "https://h.app.coc.10086.cn",
    "Referer": REF,
    "x-requested-with": "com.greenpoint.android.mc10086.activity",
    "x-qen": "1",
    "x-app-version": "1.0.2",
    "channelid": "CHINA_APP",
}


def _ssl_ctx():
    """移动 nrapigate 服务器**不支持 RFC5746 安全重协商**。

    OpenSSL 3.x 默认拒绝这类旧式服务器，报
      `[SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED] unsafe legacy renegotiation disabled`
    （GitHub Actions 上 curl+OpenSSL 3.0.13 会同样报错：`OpenSSL error:0A000152`）。
    必须显式打开 SSL_OP_LEGACY_SERVER_CONNECT（值 0x4）才能握手成功。

    注意：本机 Windows 用 curl 能连通，是因为它走 **Schannel**，不检查这一项 ——
    所以这个坑只在 Linux / Python 侧暴露，别被"本地能跑"误导。
    """
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


# 显式传空 ProxyHandler：否则 urllib 会自动补一个默认的，其代理来自环境变量
# 与 Windows 注册表 Internet 设置（残留代理会静默劫持请求）。空 dict = 真直连。
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_ssl_ctx()),
    urllib.request.ProxyHandler({}),
)

for d in (SNAP, CHG, DOCS):
    os.makedirs(d, exist_ok=True)

KEY_FIELDS = ["fees", "data", "dataUnit", "call", "applicablePeople", "channel",
              "onlineDay", "offineDay", "otherContent", "extraFees", "validPeriod",
              "brandwidth"]
FIELD_CN = {"fees": "月费", "data": "流量", "dataUnit": "流量单位", "call": "通话",
            "applicablePeople": "目标客户", "channel": "办理渠道",
            "onlineDay": "上线日", "offineDay": "下线日",
            "otherContent": "权益说明", "extraFees": "超套资费",
            "validPeriod": "有效期", "brandwidth": "宽带"}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def call(path, body, retry=2):
    """调网关：POST 明文 JSON -> 若响应是 {"body": "<密文>"} 则本地解密 -> dict"""
    if mz_crypto is None:
        return {"_err": f"缺少加解密依赖 pycryptodome（{_MZ_ERR}），只能跑 --render-only"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(ROOT + path, data=data,
                                         headers=HEADERS, method="POST")
            with _OPENER.open(req, timeout=25) as r:
                txt = r.read().decode("utf-8", "replace").strip()
            j = json.loads(txt)
            if isinstance(j, dict) and set(j.keys()) == {"body"}:
                j = json.loads(mz_crypto.decrypt(j["body"]))
            return j
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            if i < retry:
                time.sleep(2 * (i + 1))
    return {"_err": last}


def fetch_group(c):
    a, t1, t2v = str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2"))
    entries, beans_all, page, total, fails = [], [], 1, None, 0
    while True:
        lst = call("nrtariff/new/Tariff/getTariffListInfo",
                   {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                    "tariffAttr": a, "type1": t1, "type2": t2v,
                    "page": page, "limit": 100, "fistLimit": 100})
        d = lst.get("data") if isinstance(lst, dict) else None
        if not isinstance(d, dict):
            fails += 1
            log(f"  !! attr={a} t1={t1} t2={t2v} page={page} 异常: {str(lst)[:150]}")
            break
        for b in d.get("beans") or []:
            en = b.get("nonModuleList") or []
            entries.extend(en)
            beans_all.append({"tariffSeqno": b.get("tariffSeqno"),
                              "tariffName": b.get("tariffName"), "count": len(en)})
        pg = d.get("page") or {}
        total = pg.get("total")
        pages = pg.get("pages") or 1
        if page >= pages:
            break
        page += 1
    log(f"  attr={a} t1={t1} t2={t2v} total={total} series={len(beans_all)} "
        f"entries={len(entries)} fails={fails}")
    return {"tariffAttr": a, "type1": t1, "type2": t2v, "total": total,
            "series": beans_all, "entries": entries}


def fetch_all(workers=4):
    t0 = time.time()
    t2 = call("nrtariff/new/Tariff/getType2List", {"province": PROV, "isPublic": "1"})
    combos = (t2.get("data") if isinstance(t2, dict) else None) or []
    if not combos:
        log(f"分类列表获取失败: {str(t2)[:200]}")
        return None
    log(f"分类组合 {len(combos)} 个，并发 {workers}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        groups = list(ex.map(fetch_group, combos))
    n = sum(len(g["entries"]) for g in groups)
    log(f"抓取完成：{n} 条 / {time.time() - t0:.0f}s")
    return {"province": PROV, "provinceName": "河北",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "endpoint": ROOT + "nrtariff/new/Tariff/getTariffListInfo",
            "groups": groups}


def index_rows(o):
    rows, seen = {}, {}
    for g in (o or {}).get("groups") or []:
        # ★ 口径必须与 rows_of() 逐字一致（原来是 ZFLX.get(type2) 硬查，只有移动对得上）：
        #   联通的一级分类号 1..5 与移动 ZFLX 同名同义，但「99 停售套餐」移动没有；
        #   广电的 type2 是 GZ_TC_5G 这种代码，ZFLX 里根本没有 ⇒ 变更报告里种类名会显示 "?"。
        #   以采集侧给的中文名为准，没有再退回 ZFLX。
        ty = g.get("type2Name") or ZFLX.get(str(g.get("type2")), "?")
        for e in g.get("entries") or []:
            nm = str(e.get("name") or e.get("tariffName") or "").strip()
            base = f"{g.get('type2')}|{g.get('tariffAttr')}|{nm}"
            seen[base] = seen.get(base, 0) + 1
            k = base if seen[base] == 1 else f"{base}#{seen[base]}"
            row = {f: str(e.get(f) or "").replace("\n", " ").strip() for f in KEY_FIELDS}
            row.update({"_ty": ty, "_attr": g.get("tariffAttr"), "_name": nm,
                        "_tname": str(e.get("tariffName") or "").strip(),
                        "_reportNo": str(e.get("reportNo") or "").strip()})
            rows[k] = row
    return rows


def diff_rows(old, new):
    ok, nk = set(old), set(new)
    added, removed = sorted(nk - ok), sorted(ok - nk)
    changed = []
    for k in sorted(ok & nk):
        d = {f: (old[k].get(f, ""), new[k].get(f, ""))
             for f in KEY_FIELDS if old[k].get(f, "") != new[k].get(f, "")}
        if d:
            changed.append((k, d))
    return added, removed, changed


def brief(row):
    fee = row.get("fees") or "—"
    gb = ((row.get("data") or "") + (row.get("dataUnit") or "")).strip() or "—"
    ap = (row.get("applicablePeople") or "—")[:70]
    return f"月费 {fee} 元 · 流量 {gb} · 通话 {row.get('call') or '—'} 分 · {ap}"


def write_report(old_o, new_o, added, removed, changed,
                 net="河北移动", fname=None):
    """写变更报告。

    ``net`` / ``fname`` 是接第二网时加的：联通那网要写自己的标题，
    报告也不能和移动挤同一个 ``changes/<日期>.md``（会互相覆盖）。
    """
    d = new_o.get("fetchedAt", "")[:10]
    idx, oidx = index_rows(new_o), index_rows(old_o)
    L = [f"# {net}资费变更报告 · {d}", "",
         f"- 本次抓取：{new_o.get('fetchedAt')}",
         f"- 上次抓取：{old_o.get('fetchedAt', '（无）')}",
         f"- 条目数：{len(oidx)} → **{len(idx)}**",
         f"- 新增 **{len(added)}** · 下线 **{len(removed)}** · 字段变更 **{len(changed)}**", ""]
    if added:
        L += [f"## 新增资费（{len(added)}）", ""]
        for k in added[:120]:
            r = idx[k]
            L.append(f"- **{r['_name'] or r['_tname']}** 〔{r['_ty']}〕 {brief(r)}")
            if r.get("_reportNo"):
                L.append(f"  - 报备编号 `{r['_reportNo']}` · 上线 {r.get('onlineDay') or '—'}"
                         f" ~ 下线 {r.get('offineDay') or '—'}")
        if len(added) > 120:
            L.append(f"- …（其余 {len(added) - 120} 条见当日快照）")
        L.append("")
    if removed:
        L += [f"## 下线/下架资费（{len(removed)}）", ""]
        for k in removed[:120]:
            r = oidx[k]
            L.append(f"- **{r['_name'] or r['_tname']}** 〔{r['_ty']}〕 {brief(r)}")
        if len(removed) > 120:
            L.append(f"- …（其余 {len(removed) - 120} 条见上一版快照）")
        L.append("")
    if changed:
        L += [f"## 关键字段变更（{len(changed)}）", ""]
        for k, dd in changed[:120]:
            r = idx[k]
            L.append(f"- **{r['_name'] or r['_tname']}** 〔{r['_ty']}〕")
            for f, (a, b) in dd.items():
                L.append(f"  - {FIELD_CN.get(f, f)}：`{a[:70] or '—'}` → `{b[:70] or '—'}`")
        if len(changed) > 120:
            L.append(f"- …（其余 {len(changed) - 120} 条见当日快照）")
        L.append("")
    if not (added or removed or changed):
        L += ["本次未检测到任何变化。", ""]
    txt = "\n".join(L)
    p = os.path.join(CHG, fname or f"{d}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(txt)
    return p, txt


def archive_page():
    """把查询页 gzip 归档进仓库（``page/index.html.gz``），供本机 view_page.py 取用。

    页面已无公网入口（GitHub 免费版 Pages 只支持公开仓库），要「本机不跑抓取就能看」，
    就得有地方取 —— 这就是那个地方。归档进 git 也顺带成了页面快照存档。

    ★ 直接按字节比对去重，只有资费数据真变了才写新归档。

      能做到「按字节比」的前提是：**页面里不含任何运行时刻**。
      所以页面顶部显示的是「数据基线日期」（取自快照的日期），不是「抓取时刻」——
      数据没变，页面就一模一样，既省仓库体积又不会显示一个骗人的旧时间。

      ⚠️ 早先的版本是「用正则把时间戳抹掉再比」，那样有个隐患：正则
      ``\\d{4}-\\d\\d-\\d\\d[ T]\\d\\d:\\d\\d:\\d\\d`` 会连带命中业务字段。
      当前 ``onlineDay``/``offineDay`` 的格式是 ``20030517``（无分隔符）侥幸没被误伤，
      但哪天上游把格式换成 ``2003-05-17 00:00:00``，真实的上下线变更就会被**静默忽略**。
      现在页面本身没有运行时刻，正则就没必要了。
    """
    if not os.path.exists(HTML_DST):
        return None
    cur = open(HTML_DST, encoding="utf-8").read()
    if os.path.exists(PAGE_GZ):
        try:
            with gzip.open(PAGE_GZ, "rt", encoding="utf-8") as f:
                old = f.read()
            if old == cur:
                log("页面内容与归档一致（数据未变），跳过归档")
                return PAGE_GZ
        except Exception as e:
            log(f"读取旧归档失败，将重新写入：{type(e).__name__}: {e}")
    os.makedirs(PAGE_DIR, exist_ok=True)
    raw = cur.encode("utf-8")
    # mtime=0 同样内容 => 同样字节（与快照一致）
    with gzip.GzipFile(PAGE_GZ, "wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    log(f"页面已归档 {os.path.relpath(PAGE_GZ, BASE)}"
        f"（{os.path.getsize(PAGE_GZ) / 1024:.0f} KB，原始 {len(raw) / 1048576:.2f} MB）")
    return PAGE_GZ


def data_day(o, fallback=""):
    """取页面顶部那个「数据基线日期」（__DATE__）。

    🔴 为什么值得单独一个函数：页面的**全部相对天数**与**上架/下线时间筛选**都以它为基准。
    它一旦不是合法的 YYYY-MM-DD，页面侧 BASE 解析失败 → ago/left 变 NaN →
    `NaN<0 || NaN>7` 恒为 false → 「7 天内上架」会**静默返回全部条目**。
    不报错、看着还正常，所以必须在源头拦下并回退到一个稳定值。

    fallback 必须是**稳定**的（快照日/页面已有日期），不能用 time.strftime("today")：
    页面里一旦带上会变的日期，归档的逐字节去重就废了。
    """
    s = str((o or {}).get("fetchedAt") or "")[:10]
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            datetime.date(*(int(g) for g in m.groups()))
            return s
        except ValueError:
            pass
    log(f"!! 数据基线日期不可解析：{s!r} —— 回退到 {fallback or '（空）'}。"
        f"页面会把时间筛选置为 fail-closed，请尽快检查抓取逻辑。")
    return fallback


# —— 四网骨架 ——
# code: (简称, 全称)。顺序即页面导航条顺序。
# 骨架阶段只有「移动」接了真实数据；其余三家的 rows / src / base 留空，
# 接入时把对应那家的字段填上即可，**页面侧无需改动**。
NETS_META = (
    ("move",    "移动", "中国移动"),
    ("unicom",  "联通", "中国联通"),
    ("telecom", "电信", "中国电信"),
    ("cbn",     "广电", "中国广电"),
)
SRC_OF = {
    "move":    "中国移动 APP「资费专区」（nrapigate / nrtariff）",
    "unicom":  "中国联通 APP「资费专区」（mxx.client.10010.com / queryTariffNew）",
    "telecom": "中国电信「资费专区」H5（www.189.cn / tariffSection，真实浏览器采集）",
    "cbn":     "中国广电「资费公示」H5（m.10099.com.cn / queryTariffAllByCond）",
}
# 每日巡检里**由脚本直连就能采到**的那几家（有 src / base / rows 的）。
# 电信也在里面 —— 但它和另两家不同：它需要真实浏览器，是**机会性采集**
# （采到就正常入库；采不到自动退回下面的 NET_SNAP 快照渲染，绝不拖垮其它网）。
NET_LIVE = ("move", "unicom", "cbn", "telecom")
# 「采不到时的渲染兜底」名单 —— 目前只有电信。
#
# 2026-09-22 修正：此前这里写的是「电信在云端采不到，只能快照直渲」，
# 那是**推断**（"Runner 上没浏览器"）而不是实测。当天在 ubuntu-latest 上起了
# Xvfb + 有头 Chrome 实测：挑战自动通过、`navigator.webdriver=false`、
# 884 条 5 个分类逐项采全 ⇒ **云端本来就采得到**，缺的只是那一步 xvfb 启动。
#
# 现在的语义是「兜底」而不是「唯一通路」：本轮没拿到采集产物（CI 里那步失败、
# 或本机没有 .ct_raw.json）就用仓库里最新那份快照渲染。这样即使哪天瑞数改规则
# 或 runner 镜像没了 Chrome，页面也**不会少一网**，只是数据停在上一版 ——
# 而且基线日期取自快照自身，陈旧是**看得见**的，不会假装今天更新过。
NET_SNAP = ("telecom",)
# 每网一份独立快照，文件名前缀区分 —— 共用一套快照会让两网互相覆盖
# （load_prev 按文件名排序取「最近一份」，混在一起就会拿联通昨天的当移动今天的基准）。
SNAP_PREFIX = {"move": "hebei_tariff_", "unicom": "unicom_tariff_",
               "cbn": "cbn_tariff_", "telecom": "ct_tariff_"}
# 「移动之外」各网的轮次配置：code → (适配器模块名, 报告/摘要里的中文名, 报告文件名前缀)。
# 三处必须**成对**出现（模块、显示名、报告文件名），散在 main() 里各写一遍迟早漏一处。
NET_RUN = {
    "unicom": ("unicom_monitor", "河北联通", "unicom"),
    "cbn":    ("cbn_monitor",    "中国广电", "cbn"),
    # 电信适配器只做「原始产物 → 中间格式」的纯转换，不发请求；
    # 真正的采集在 tariff-daily.yml 里由 ci_grab.sh（Xvfb + 真实 Chrome）先跑完。
    "telecom": ("ct_monitor",    "河北电信", "ct"),
}
# 页面顶部那行「来源」在各网切换时要跟着变，所以它不能是静态文本（模板里改成由 JS 渲染）
UP_N = 0        # 由 build_html 回填：四网总条数（供 __N__ 占位符）

# 页面里的「查看变更明细」链接指向仓库里的 changes/<日期>.md（网页版可直接看）。
# 与 view_page.py 的 DEFAULT_REPO 同值 —— 两个脚本各有独立入口，不互相 import。
REPO = "starrry365/hebei-tariff-monitor"

# 页面 gz 体积预警线：单网(3878 条) 约 245 KB，四网全接入会到 1 MB 上下。
# 超了就只是**提醒**（不改行为）—— 该考虑按网拆分/按需加载，而不是继续往单文件里塞。
GZ_WARN = 900 * 1024


def net_payload(payloads):
    """把各网的 rows 装进四网容器。

    ``payloads``：``{网code: {"rows": [...], "base": "...", "src": "..."}}``。
    没给的网自动落成空壳 —— 骨架阶段那两家就是这么留白的。

    ★ 结构刻意做成**对称**的（每网都有 sh/nm/src/base/rows），而不是
      「移动特殊、另两家另放一个数组」—— 后者每加一处逻辑都要分叉一次，迟早漏一边。
    ★ 每网自带 base：相对天数与时间筛选都以**本网**基线为准。各网的抓取时点
      不可能总在同一天，共用一个全局基线会让后接入的那几家整体算错天数。
    """
    out = {}
    for code, sh, nm in NETS_META:
        p = payloads.get(code) or {}
        out[code] = {"sh": sh, "nm": nm,
                     "src": p.get("src") or "",
                     "base": p.get("base") or "",
                     # 「本网数据不分城市」由数据源自己声明（联通为真）。页面拿到它就把
                     # 该网全部条目按「全省通用」处理，不再去名称文本里猜地市 ——
                     # 否则选任何地市都返回 0 条，看着像 bug，实际是拿不存在的维度在筛。
                     "allProvince": bool(p.get("allProvince")),
                     "rows": p.get("rows") or []}
    return out


def _mark_changes(rows, diff):
    """给页面行打「本次新增 / 关键字段变更」标，并做条数自检。

    标注**错行比不标更坏** —— 用户会据此认定某条资费变了。所以条数对不上就整批撤掉，
    退回「没有标注」，而不是留一批看着像真的、其实指错行的标注。
    """
    if not diff:
        return rows
    want = (len(diff["added"]), len(diff["changed"]))
    got = (sum(1 for r in rows if r.get("ca")), sum(1 for r in rows if r.get("ck")))
    if got != want:
        log(f"!! 变更标注条数对不上（新增 {got[0]}/{want[0]} · 字段变更 {got[1]}/{want[1]}），"
            f"本次不加标注 —— 宁可没有，也不能标错行")
        for r in rows:
            r.pop("ca", None)
            r.pop("ck", None)
    return rows


def rows_of(o, diff=None):
    """把**某一家**的数据源对象构造成页面行。

    移动与联通在这一层是同构的：联通采集时就把字段名映射成了移动那套
    （feesStandard→fees、startDate→onlineDay…，见 probes/he_unicom_tariff.py
    的 FIELD_MAP），所以两网共用这一个函数，页面无需为任何一家分叉。

    ``diff``（可选）＝ ``{"added": set(键), "changed": set(键)}``，来自 diff_rows。
    给了就给命中的行打 ``ca`` / ``ck`` 标，页面据此显示「新增 / 变更」徽章，
    并支持「只看本次变更」筛选。没给（如 --render-only）时页面只是没有标注。
    """
    def gb(v, unit):
        try:
            f = float(str(v).strip())
        except Exception:
            return None
        u = str(unit or "").upper()
        if u.startswith("MB"):
            return f / 1024.0
        if u.startswith("GB"):
            return f
        if u.startswith("TB"):
            return f * 1024.0
        return None

    rows, seen = [], {}
    for g in o["groups"]:
        # 联通的一级分类号（1..5）与移动 ZFLX 的 1..5 语义一致，直接复用；
        # 采集侧若给了 type2Name 就以它为准（联通的「99 停售套餐」是移动没有的类）。
        ty = g.get("type2Name") or ZFLX.get(str(g.get("type2")), "?")
        attr = g.get("tariffAttr")
        for e in g["entries"]:
            def s(k, n=400):
                v = e.get(k)
                return "" if v in (None, "None") else str(v).replace("\n", " ")[:n]
            rec = {"n": s("name", 120), "t": s("tariffName", 80),
                   "f": s("fees", 20), "d": s("data", 20), "du": s("dataUnit", 10),
                   "c": s("call", 20), "g": gb(e.get("data"), e.get("dataUnit")),
                   "ap": s("applicablePeople", 220), "ch": s("channel", 120),
                   "o": s("onlineDay", 12), "e": s("offineDay", 12),
                   "ty": ty, "a1": attr, "r": s("reportNo", 30),
                   "x": s("otherContent", 500), "ex": s("extraFees", 200),
                   "vp": s("validPeriod", 200), "bw": s("brandwidth", 40)}
            # ★ 行级变更标注：键必须与 index_rows() **逐字一致** ——
            #   取**未截断**的 name（name 缺失时退回 tariffName），重名追加 #2/#3。
            #   若图省事拿页面里那个截断到 120 的 n 去比，超长名字会静默对不上。
            if diff:
                nm = str(e.get("name") or e.get("tariffName") or "").strip()
                kb = "%s|%s|%s" % (g.get("type2"), attr, nm)
                seen[kb] = seen.get(kb, 0) + 1
                kb = kb if seen[kb] == 1 else "%s#%d" % (kb, seen[kb])
                if kb in diff["added"]:
                    rec["ca"] = 1
                if kb in diff["changed"]:
                    rec["ck"] = 1
            rows.append(rec)
    return _mark_changes(rows, diff)


def build_html(sources, notice="", diffs=None):
    """重建查询页（多网）。

    ``sources``：``{网code: 数据源对象}`` —— 目前是 ``{"move": o, "unicom": o}``，
    缺哪家就哪家留空壳。
    ``diffs``：``{网code: {"added": set(键), "changed": set(键)}}``，按网各给一份；
    变更标注必须**按网分别算**（键里带分类号，两网的键空间不通用）。
    """
    global UP_N
    payloads, total = {}, 0
    for code, o in (sources or {}).items():
        if not o:
            continue
        d = (diffs or {}).get(code)
        rows = rows_of(o, d)
        payloads[code] = {"rows": rows, "src": SRC_OF.get(code, ""),
                          "base": data_day(o, time.strftime("%Y-%m-%d")),
                          "allProvince": bool(o.get("allProvince"))}
        total += len(rows)
    if not payloads:
        log("!! 没有任何一网的数据，放弃重建页面")
        return 0
    UP_N = total
    html = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
    # __DATE__ 只取「日期」部分，不取到秒。
    # ★ 这是归档去重能生效的前提：页面里一旦带上运行时刻，内容就天天不同，
    #   按字节比对会永远不等 —— 要么每天白写一份归档撑大仓库，要么退回用正则
    #   抹时间戳（会误伤业务日期字段）。只放日期，数据没变页面就一模一样。
    # ★ __DATE__ 是「移动那网的基线」——页面顶部那行来源/基线/条数已改成
    #   由 JS 按当前网渲染（原来写死成移动的，切到联通会显示错的来源与条数），
    #   这个占位符只留作无 JS 时的后备文本，取移动的 base 最不容易误导。
    date = (payloads.get("move") or {}).get("base") or data_day(
        sources.get("move") or {}, time.strftime("%Y-%m-%d"))
    payload = json.dumps(net_payload(payloads), ensure_ascii=False, separators=(",", ":"))
    out = (html.replace("__NETS__", payload).replace("__N__", str(total))
               .replace("__DATE__", date)
               .replace("__NOTICE__", notice or "本次巡检未检测到变化"))
    with open(HTML_DST, "w", encoding="utf-8") as f:
        f.write(out)
    # gz 才是用户实际要下载的字节数：原始 2.5 MB 的页面 gz 后只有 245 KB，
    # 只看原始大小会高估一个数量级。多网接入后这个数字会翻几倍，所以要盯着。
    gz = len(gzip.compress(out.encode("utf-8"), 6))
    per = " · ".join("%s %d" % (c, len(p["rows"])) for c, p in payloads.items())
    log(f"已重建查询页（{total} 条：{per}，{os.path.getsize(HTML_DST)/1024:.0f} KB / "
        f"gz {gz/1024:.0f} KB）")
    if gz > GZ_WARN:
        log(f"!! 页面 gz 已 {gz/1024:.0f} KB，超过 {GZ_WARN/1024:.0f} KB 预警线。"
            f"再接入一家会继续翻 —— 该考虑按网拆分 / 按需加载，"
            f"而不是继续往单文件里塞")
    archive_page()
    return total


def render_only():
    """不联网：拿现有 ``docs/index.html`` 里的数据，套当前 template.html 重渲染。

    为什么需要它：只改了模板（筛选项 / 样式 / 文案）时，**不该为了重建页面去跑抓取**。
    抓取会顺带写快照、写 ``changes/<今天>.md``、覆盖 ``state.json`` ——
    云端当天已经跑过一轮时，本地补跑会以「本地那份旧基准」生成一份不同的变更报告，
    把云端真实结果覆盖掉。所以重渲染必须走这条不碰数据的旁路。

    数据来源是页面里那段 ``const NETS={...}``（就是 build_html 注入的四网容器），
    用 ``JSONDecoder.raw_decode`` 取，比正则稳妥（字段正文里可能有 ``]`` 或 ``;``）。
    """
    if not os.path.exists(HTML_DST):
        log("!! 本地没有 docs/index.html —— 先跑一次完整巡检，或用 view_page.py 拉归档")
        return 3
    cur = open(HTML_DST, encoding="utf-8").read()
    m = re.search(r"const\s+NETS\s*=", cur)
    if m:
        try:
            nets, _ = json.JSONDecoder().raw_decode(cur[m.end():])
        except Exception as e:
            log(f"!! 解析现有页面数据失败：{type(e).__name__}: {e}")
            return 3
        if not isinstance(nets, dict) or not nets:
            log("!! 页面数据容器不是对象，放弃重建")
            return 3
    else:
        # 兼容四网改造**之前**的页面（容器还是 `const DATA=[...]`）。
        # 没有这段就是死锁：想重建页面得先有 NETS，而 NETS 只有重建才写得出来。
        # 迁移过一次之后这个分支就再也不会进。
        mo = re.search(r"const\s+DATA\s*=", cur)
        if not mo:
            log("!! 现有页面里既没有 `const NETS=` 也没有 `const DATA=`，无法取数")
            return 3
        try:
            legacy, _ = json.JSONDecoder().raw_decode(cur[mo.end():])
        except Exception as e:
            log(f"!! 解析旧版页面数据失败：{type(e).__name__}: {e}")
            return 3
        if not isinstance(legacy, list) or not legacy:
            log("!! 旧版页面数据为空，放弃重建")
            return 3
        log(f"-- 检测到旧版页面（const DATA=，{len(legacy)} 条），本次按四网容器迁移")
        nets = net_payload({"move": {"rows": legacy}})
    mv = nets.get("move") or {}
    rows = mv.get("rows") or []
    if not isinstance(rows, list) or not rows:
        log("!! 页面数据为空，放弃重建（避免生成 0 条页面）")
        return 3
    # 重渲染要把**所有网**的条数都算上，否则顶部「共 N 条」会只报移动一家的。
    all_n = sum(len((nets.get(c) or {}).get("rows") or []) for c in SNAP_PREFIX)

    # 日期与通知必须沿用页面里的原值：基线日期是「数据基线」，
    # 重渲染不该让它漂移（漂移会让归档天天不等）。
    # 基线优先取 NETS["move"].base（唯一真相），退回页面正文的「数据基线 <日期>」。
    dm = re.search(r"数据基线 ([\d-]+)", cur)
    nm = re.search(r'id="notice">(.*?)</div>', cur, re.S)
    # 万一是空的/坏的，回退到**最新快照的日期**（稳定），而不是"今天"。
    date = data_day({"fetchedAt": mv.get("base") or (dm.group(1) if dm else "")}, prev_day())
    notice = nm.group(1).strip() if nm else ""
    mv["base"] = date     # 回填，保证静态 sub 与容器里的基线一致
    mv["rows"] = rows

    payload = json.dumps(nets, ensure_ascii=False, separators=(",", ":"))
    tpl = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
    for ph in ("__NETS__", "__N__", "__DATE__", "__NOTICE__"):
        if ph not in tpl:
            log(f"!! 模板缺少占位符 {ph}，中止（否则会留下未替换的标记）")
            return 3
    out = (tpl.replace("__NETS__", payload)
              .replace("__N__", str(all_n))
              .replace("__DATE__", date)
              .replace("__NOTICE__", notice))
    with open(HTML_DST, "w", encoding="utf-8") as f:
        f.write(out)
    log(f"已按当前模板重渲染：共 {all_n} 条（移动 {len(rows)}）· 基线 {date or '?'} · "
        f"{os.path.getsize(HTML_DST)/1024:.0f} KB")
    archive_page()
    return 0


def prev_day(prefix="hebei_tariff_"):
    """最新快照文件对应的日期（YYYY-MM-DD）—— 用作基线日期的稳定回退值。"""
    fs = [os.path.basename(p) for p in snap_paths(prefix)]
    if not fs:
        return ""
    d = fs[-1][len(prefix):-len(".json.gz")]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if re.fullmatch(r"\d{8}", d) else ""


def snap_paths(prefix="hebei_tariff_"):
    return sorted(os.path.join(SNAP, f) for f in os.listdir(SNAP)
                  if f.startswith(prefix) and f.endswith(".json.gz"))


def save_snapshot(data, day, prefix="hebei_tariff_"):
    p = os.path.join(SNAP, f"{prefix}{day}.json.gz")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0 保证同样内容产生同样字节，避免无意义的二进制 diff
    with gzip.GzipFile(p, "wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    log(f"快照已存 {os.path.basename(p)}（{os.path.getsize(p)/1024:.0f} KB，"
        f"原始 {len(raw)/1024/1024:.1f} MB）")
    return p


def prune_snapshots(prefix="hebei_tariff_"):
    fs = snap_paths(prefix)
    for p in fs[:-KEEP_SNAPSHOTS] if len(fs) > KEEP_SNAPSHOTS else []:
        os.remove(p)
        log(f"清理过期快照 {os.path.basename(p)}")


def load_prev(today, prefix="hebei_tariff_"):
    """选对比基准：优先「今天之前」最近的一份快照。

    ⚠️ 同一自然日重复运行时（手动补跑 / workflow_dispatch 验证），不会有「今天之前」
    的快照了，这时必须**退回用今天已存在的那份**当基准。

       否则第二次跑会被判成「首版基线」，然后 main() 里那条基线分支会把
       ``changes/<今天>.md`` 覆盖成一句「首版基线快照」——当天真实的新增/下线
       差异就这么静默丢了。（2026-09-20 实测踩到：当天补跑一次，3878→3879 的
       +1 变更被覆盖成基线文字。）

    真·首版（仓库里一份快照都没有）才返回 None。
    """
    name = f"{prefix}{today}.json.gz"
    fs = [p for p in snap_paths(prefix) if os.path.basename(p) < name]
    if not fs:
        fs = [p for p in snap_paths(prefix) if os.path.basename(p) == name]
    if not fs:
        return None, None
    p = fs[-1]
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f), p


def load_latest(today, prefix="hebei_tariff_"):
    """渲染专用：取「不晚于今天」的最新一份快照（没有就退到全局最新）。

    🔴 刻意**不复用 load_prev()**：两者是不同语义。
      - load_prev  = 「上一版」，用于 diff —— 严格早于今天，当天有也算不上基准；
      - load_latest = 「最新版」，用于渲染 —— 当天采到的就要显示当天那份。
    用 load_prev 渲染，会出现「今天明明采到了、页面却还显示昨天」这种
    看着像没更新的假故障（NET_SNAP 的网尤其容易踩：它一天只可能被采一次）。
    """
    fs = [p for p in snap_paths(prefix)
          if os.path.basename(p)[len(prefix):-len(".json.gz")] <= today]
    if not fs:
        fs = snap_paths(prefix)
    if not fs:
        return None, None
    p = fs[-1]
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f), p


def emit_summary(new_o, added, removed, changed, report_path, has_prev,
                 net="河北移动"):
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if not sp:
        return
    idx = index_rows(new_o)
    L = [f"## {net}资费巡检 {new_o.get('fetchedAt')}", "",
         f"- 条目总数：**{len(idx)}**"]
    if not has_prev:
        L.append("- 本次为**首版基线**，后续运行才开始检测差异")
    else:
        L.append(f"- 新增 **{len(added)}** · 下线 **{len(removed)}** · 字段变更 **{len(changed)}**")
        if not (added or removed or changed):
            L.append("- ✅ 未检测到变化")
        for k in added[:15]:
            L.append(f"  - 🆕 **{idx[k]['_name'] or idx[k]['_tname']}** 〔{idx[k]['_ty']}〕{brief(idx[k])}")
        if len(added) > 15:
            L.append(f"  - …另有 {len(added) - 15} 条新增")
    L.append(f"\n完整报告：`{os.path.relpath(report_path, BASE)}`")
    with open(sp, "a", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def net_round(code, today, fallback=None):
    """跑一遍「非移动」的某一网：采集 → 骤降自检 → 快照 → 变更检测 → 报告。

    与移动共用同一套机制（同 index_rows / diff_rows / write_report），
    只有快照前缀与报告文件名不同 —— 各网必须各存各的，
    否则 load_prev 按文件名排序取「最近一份」时会把这一网的当成那一网的基准。

    ★ 任何一网失败**不致命**：抓不到就沿用上一版快照继续渲染，绝不因此让移动那网
      （或别的网）也出不了页面。返回 (数据, diff 或 None)。

    ★ 收敛成一个函数而不是「联通一份、广电一份」：三网的流程逐字相同，
      只有模块名/显示名/文件名三处不同（都在 NET_RUN 里）。复制一份就等于
      把「骤降保护」「沿用上次快照」「基线不覆盖」这些约束各写两遍 ——
      改一处漏一处时，症状是某网静默地不再保护数据。

    ``fallback``：采集失败 / 数据异常时用哪个「上一版」顶上，默认 ``load_prev``。
      电信（机会性采集）传 ``load_latest`` —— 本轮没采到时该用**最新那份**快照
      （可能就是今天早些时候采的），而不是「严格早于今天」的那份；
      否则明明仓库里有今天的数据，页面却退回昨天。
    """
    mod_name, cn, tag = NET_RUN[code]
    prefix = SNAP_PREFIX[code]
    load = fallback or load_prev
    try:
        mod = __import__(mod_name)
        data = mod.fetch_all()
    except (Exception, SystemExit) as e:
        # SystemExit 也要接：ct_monitor.fetch_all() 在缺少 .ct_raw.json 时
        # 刻意用 SystemExit 抛出一段给**人看**的采集指引（本机直接跑时体验好）。
        # 只接 Exception 的话，一次「本轮没采到电信」会直接把整个巡检打断，
        # 连移动/联通/广电的页面都出不来 —— 正是这里最不该发生的事。
        log(f"!! {cn}采集异常，本轮沿用上次：{type(e).__name__}: {e}")
        return load(today, prefix)[0], None
    old_o, _ = load_prev(today, prefix)
    if not data:
        log(f"!! {cn}采集失败，本轮沿用上次快照")
        return old_o, None
    n = len(data.get("entries") or [])
    n_old = len((old_o or {}).get("entries") or [])
    if n == 0 or (old_o is not None and n < n_old * DEGRADE_RATIO):
        log(f"!! {cn}数据量异常 {n_old} -> {n}，本轮不写快照、沿用上次")
        return old_o, None
    save_snapshot(data, today, prefix)
    prune_snapshots(prefix)
    day = f"{today[:4]}-{today[4:6]}-{today[6:]}"
    rp = os.path.join(CHG, f"{tag}-{day}.md")
    if old_o is None:
        log(f"{cn}无历史快照，本次为首版基线（{n} 条）")
        if not os.path.exists(rp):
            with open(rp, "w", encoding="utf-8") as f:
                f.write(f"# {cn}资费基线 · {data['fetchedAt']}\n\n"
                        f"- 首版基线快照，共 **{n}** 条\n"
                        f"- 来源：{SRC_OF[code]}\n")
        emit_summary(data, [], [], [], rp, False, net=cn)
        return data, None
    a, r, c = diff_rows(index_rows(old_o), index_rows(data))
    log(f"{cn}对比：新增 {len(a)} 下线 {len(r)} 变更 {len(c)}")
    rp, _ = write_report(old_o, data, a, r, c, net=cn, fname=f"{tag}-{day}.md")
    emit_summary(data, a, r, c, rp, True, net=cn)
    return data, {"added": set(a), "changed": {k for k, _ in c}}


def snap_net_ready(code, today):
    """兜底网（电信）本轮是否真拿到了采集产物 —— 决定走采集还是走快照。

    ★ 判据是**采集产物里记录的日期**，不是「文件在不在」：`.ct_raw.json` 不入库，
      但本机那份可能是上周采的，光看存在就走采集，会拿一份陈旧数据当今日快照入库
      （还会连带生成一份「电信无变化」的假报告）。用 mtime 更糟 ——
      CI 每次都是全新 checkout，mtime 恒为「现在」，等于恒真。
    """
    if code != "telecom":
        return True
    try:
        ct = __import__("ct_monitor")
    except Exception as e:
        log(f"!! 电信适配器不可用（{type(e).__name__}: {e}），本轮改用快照渲染")
        return False
    d = ct.raw_day()
    if d == today:
        return True
    log(f"-- 电信本轮没有新采到的原始数据"
        f"（.ct_raw.json 记录的采集日 {d or '（无文件）'} ≠ 今天 {today}），改用快照渲染")
    return False


def other_nets(today):
    """把「移动之外」的每一网都跑一遍。

    返回 ``(sources, diffs, tails)``：
      ``sources`` = {网code: 数据源对象}（失败的那网是上一版快照，可能没有）
      ``diffs``   = {网code: {"added": set, "changed": set} 或 None}
      ``tails``   = [" · 联通新增 1 / 变更 0", " · 广电无变化"] 供页面顶部提示拼接

    ★ 新增一网时**只需要动 NET_RUN**：main() 里不再逐个写 uni/cbn 变量。
      原来 main() 里两处（首版基线分支、常规分支）各写一遍联通的调用与提示拼接，
      接第三网就得改四处 —— 漏一处就是「某网采了但页面没提示 / 提示错字」。

    ★ 2026-09-22 追加 NET_SNAP（兜底名单，目前只有电信）：
      电信是「机会性采集」—— 云端有真实 Chrome 时采得到，采不到就退回仓库快照。
      两条路都从这里进，**不能只改 main()**：进页面有两条路（首版基线分支 &
      常规分支）都调本函数，只补一处，另一处就会静默少一网。
    """
    sources, diffs, tails = {}, {}, []
    sh_of = {c: sh for c, sh, _ in NETS_META}
    for code in NET_LIVE:
        if code == "move":
            continue
        # 兜底网（电信）先确认「本轮真拿到了采集产物」再采 ——
        # 没有就跳过，交给下面的 NET_SNAP 用快照顶上。
        # 这一步必须在 net_round **之前**：ct_monitor 宁可抛 SystemExit 给一段
        # 人看的采集指引，也不肯静默返回空 —— 那对人是好事，对无人值守是打扰。
        if code in NET_SNAP and not snap_net_ready(code, today):
            continue
        fb = load_latest if code in NET_SNAP else None
        data, dd = net_round(code, today, fallback=fb)
        sources[code] = data
        diffs[code] = dd
        cn = sh_of.get(code, code)
        if dd:
            if dd["added"] or dd["changed"]:
                tails.append(f' · {cn}新增 {len(dd["added"])} / 变更 {len(dd["changed"])}')
            else:
                tails.append(f" · {cn}无变化")

    # ── 快照兜底的网（电信本轮没采到时）────────────────────────────────
    # 提示语里必须带上**快照日期**：这一网在本轮里没有发生任何采集，
    # 只说「电信 884 条」会让人以为它今天也被抓过一次。日期是它唯一诚实的时效声明。
    for code in NET_SNAP:
        if sources.get(code):
            continue          # 本轮真采到了，不需要兜底
        cn = sh_of.get(code, code)
        o, p = load_latest(today, SNAP_PREFIX[code])
        if not o:
            log(f"!! {cn}没有可用快照（snapshots/{SNAP_PREFIX[code]}*.json.gz 缺失），"
                f"页面这一网将显示占位说明")
            continue
        sources[code] = o
        d8 = os.path.basename(p)[len(SNAP_PREFIX[code]):-len(".json.gz")]
        d10 = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}" if re.fullmatch(r"\d{8}", d8) else d8
        tails.append(f" · {cn}沿用 {d10} 快照")
        log(f"{cn}由快照渲染：{os.path.basename(p)}（{len(o.get('entries') or [])} 条）")
    return sources, diffs, tails


def main():
    argv = sys.argv[1:]
    no_html = "--no-html" in argv
    if "--render-only" in argv:
        return render_only()
    today = time.strftime("%Y%m%d")

    data = fetch_all()
    if data is None:
        log("!! 抓取失败：分类列表拿不到")
        return 3
    n = sum(len(g["entries"]) for g in data["groups"])

    old_o, prev_p = load_prev(today)
    n_old = sum(len(g["entries"]) for g in (old_o or {}).get("groups") or [])

    if old_o is not None and n < n_old * DEGRADE_RATIO:
        log(f"!! 数据量骤降 {n_old} -> {n}（<{DEGRADE_RATIO:.0%}），判为抓取异常，"
            f"本次不写快照、不出报告")
        return 2
    if n == 0:
        log("!! 抓到 0 条，判为异常")
        return 2

    save_snapshot(data, today)
    prune_snapshots()

    if old_o is None:
        log(f"无历史快照，本次为首版基线（{n} 条）")
        rp = os.path.join(CHG, f"{today[:4]}-{today[4:6]}-{today[6:]}.md")
        # 双保险：基线文字不覆盖已存在的当天报告。
        # load_prev() 已经保证「当天有快照就不会走到这里」，但万一快照被删/损坏，
        # 也不能把一份真实的变更报告换成一句「首版基线」。
        if os.path.exists(rp):
            log(f"当天报告已存在，保留不覆盖：{os.path.relpath(rp, BASE)}")
        else:
            with open(rp, "w", encoding="utf-8") as f:
                f.write(f"# 河北移动资费基线 · {data['fetchedAt']}\n\n"
                        f"- 首版基线快照，共 **{n}** 条\n"
                        f"- 来源：中国移动 APP「资费专区」（nrapigate / nrtariff）\n")
        emit_summary(data, [], [], [], rp, False)
        if not no_html:
            extra, xdiff, _ = other_nets(today)
            build_html(dict({"move": data}, **extra),
                       f"首版基线建立（{n} 条），自次日起开始检测资费上下线变更",
                       xdiff)
        return 0

    a, r, c = diff_rows(index_rows(old_o), index_rows(data))
    log(f"对比 {os.path.basename(prev_p or '')}：新增 {len(a)} 下线 {len(r)} 变更 {len(c)}")
    rp, txt = write_report(old_o, data, a, r, c)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": data["fetchedAt"], "n": n, "prev_n": n_old,
                   "added": len(a), "removed": len(r), "changed": len(c),
                   "report": os.path.relpath(rp, BASE), "prev": os.path.basename(prev_p or "")},
                  f, ensure_ascii=False, indent=1)
    emit_summary(data, a, r, c, rp, True)
    # 页面每次都重建（本地那份必须是当天最新），但归档只在内容真变了时才写。
    # 「页面显示的时间停住」曾是个坑：所以页面显示的是「数据基线日期」而非抓取时刻，
    # 停住＝数据确实没变，语义正确。想知道巡检有没有在跑，看 state.json（view_page 会读）。
    if not no_html:
        # 「变更了多少条」是一句话能说完的，「哪几条、变了什么」说不完 ——
        # 明细细在 changes/<日期>.md 里，所以摘要后面挂一条直达链接，
        # 别让用户自己翻仓库找当天那份。
        rel = os.path.relpath(rp, BASE).replace(os.sep, "/")
        tail = (f' · <a href="https://github.com/{REPO}/blob/main/{rel}"'
                f' target="_blank" rel="noopener">查看变更明细 →</a>')
        notice = ((f"本次巡检：新增 {len(a)} 条 · 下线 {len(r)} 条 · 字段变更 {len(c)} 条"
                   + tail)
                  if (a or r or c) else ("本次巡检未检测到任何变化" + tail))
        # changed 是 [(键, {字段: (旧, 新)})]，取键时要展开，直接 set(c) 会得到一堆元组。
        extra, xdiff, tails = other_nets(today)
        notice += "".join(tails)
        diffs = {"move": {"added": set(a), "changed": {k for k, _ in c}}}
        diffs.update(xdiff)
        build_html(dict({"move": data}, **extra), notice, diffs)
    log(f"变更报告 {os.path.relpath(rp, BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
