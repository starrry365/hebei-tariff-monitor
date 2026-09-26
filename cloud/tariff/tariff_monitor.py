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
import io
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

# ── 护栏与推送：都用 try 单独包，缺一个不影响另一个，也不影响抓取 ────────────
# ★ 为什么允许「导入失败还继续跑」：这两个模块都不是采集链路的必需件 ——
#   护栏是给数字上保险、推送是通知。为了它们让整轮巡检起不来，是本末倒置。
#   但**必须把它吼出来**：静默降级成「没有护栏」比没有护栏更糟（见变更报告里
#   那句「本题不允许静默降级」的同源原则）。
try:
    import change_guard as G  # noqa: E402
    _G_ERR = None
except Exception as _e:  # noqa: E402
    G, _G_ERR = None, _e
try:
    import notify as NOTIFY  # noqa: E402
    _N_ERR = None
except Exception as _e:  # noqa: E402
    NOTIFY, _N_ERR = None, _e

SNAP = os.path.join(BASE, "snapshots")
CHG = os.path.join(BASE, "changes")
DOCS = os.path.join(BASE, "docs")
HTML_DST = os.path.join(DOCS, "index.html")
STATE_FILE = os.path.join(BASE, "state.json")
# 页面归档：查询页没有公网入口，本机靠这个文件「不跑抓取就能看」
PAGE_DIR = os.path.join(BASE, "page")
PAGE_GZ = os.path.join(PAGE_DIR, "index.html.gz")
KEEP_SNAPSHOTS = 60          # 只保留最近 60 份快照（gzip 后约 0.53MB/份，工作区稳定在 ~32MB）
# 数据量骤降阈值与「连续多少轮才认账」都由 change_guard 统一定义 ——
# 这里只做一次转发，避免两个模块各写一份阈值（改一处漏一处的下场见 net_round 注释）。
DEGRADE_RATIO = getattr(G, "DEGRADE_RATIO", 0.6) if G else 0.6
DEGRADE_ACCEPT_ROUNDS = getattr(G, "DEGRADE_ACCEPT_ROUNDS", 3) if G else 3
# 降级计数必须**跨轮次持久化**（否则"连续 3 轮"退化成"每轮都算第一轮"）。
# 它入库，CI 每轮提交 —— 与 history.json 同理。
DEGRADE_STATE = os.path.join(BASE, "degrade_state.json")

ROOT = "https://h.app.coc.10086.cn/website/nrapigate/"
REF = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
PROV = "311"   # 河北
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}

# ── 地域归属（三级口径：邢台 → 河北 → 全国）────────────────────────────
# 需求：只要「邢台 + 河北 + 全国」的资费；与河北无关的（其他省份专属）丢弃。
# 四网的地域字段差异极大，**逐网判** —— 不存在一套通用规则：
#   移动：applicableArea / city / province **三个**字段都可能带地域。取值形态有
#         2 字母省码（HE）、全国标记（000）、4 位地市码、多省 CSV、`!XX` 排除式
#   联通：条目里没有地域字段，但**采集侧**有 —— 12 城并集采集时逐条记了城市归属
#         （`_cityNames` / `_allCity`，见 probes/he_unicom_tariff.collect）
#   电信：applicableArea（HE / 地市码 CSV）+ applicableAreaLabel
#   广电：`_areaNames` 只有「全国 / 河北省」两档（上游无地市粒度）
#
# ★ 判定规则已用**已归档快照离线验证**（scope_proto 探针）：移动 3887 条无一落入
#   「无关」桶 → xz 104 / hb 2334 / cn 1449，合计 3887。电信 884 条同样零漏判。
#   验证这一步不能省：分类器写错了不会报错，只会**静默少一批条目**，
#   而页面上「少了些什么」这件事没有任何提示。
XINGTAI_CODE = "3190"        # 已实锤：条目名「邢台爱家光网服务预存活动-冀享专属」
# 河北 12 个地市码 → 中文名（**移动/电信**的 applicableArea 用这套 4 位码；
# 联通的地市码是另一套 3 位码，在 probes/he_unicom_tariff.CITY_CODES 里，
# 已在采集侧换成同一批中文名 —— 页面与对账脚本只看得到名字）。
# ★ 地市码是**条目级**信息：一条资费可覆盖多个市（CSV），也可能一个都不限（全省通用）。
# ★ 三条网有条目级地市信息（2026-09-24 更正）：
#     移动（4 位码，实测命中 1974 次）+ 文案兜底、电信（4 位码 CSV，265 次）、
#     联通（12 城并集采集，139/8042 条没有覆盖全部城市）。
#   🔴 广电是唯一真的没有的（区域码表只有省级）。
#   🔴🔴 这里曾把联通也归到「没有」那一档，依据是「采集侧只采了邢台一城 + 条目零地域
#     字段」—— 那是**抽样假阴性**：单城采集当然看不到城市差异（详见
#     docs/联通河北资费-地市维度纠错-20260924.md）。
HB_CITY = {"3100": "邯郸", "3110": "石家庄", "3120": "保定",
           "3121": "雄安新区", "3130": "张家口", "3140": "承德",
           "3150": "唐山", "3160": "廊坊", "3170": "沧州", "3180": "衡水",
           "3190": "邢台", "3350": "秦皇岛"}
HB_CITY_CODES = frozenset(HB_CITY)
# 🔴🔴 `3121` **不是**「省直辖（定州/辛集）」（2026-09-24 更正）。
#   判据 = 把 `applicableArea == "3121"`（该码**单独**出现）的条目全列出来：
#     雄安新区咪咕融合权益5次包 / 10次包 / 1次包 · 雄安9元返费优惠（12个月）
#     雄安公交出行权益包 · 雄安新区咪咕咖啡体验包（12Y） · 雄安金牌服务包A（12个月）
#     · 40G通用流量包（24个月，雄安专属）
#   8 条里 7 条名字直接写着「雄安」；而其余 11 个码单独出现时，名字各自写着自己的城市名
#   （3100 全部是「邯郸…」、3150 全部是「唐山…」）—— 只有 3121 对不上。
#   代号规律也自洽：雄安原属保定，3120 保定 ⇒ 3121 雄安。
#   后果（移动 + 电信**同时**受影响，两网共用这张表）：
#     · 「雄安新区」档拿不到那 8 条专属资费 —— 页面里它与「仅全省通用」数字完全一样，
#       看起来像「雄安根本没有资费」；
#     · 「省直辖（定州/辛集）」档装的是雄安的资费 + 一批全省资费，名不副实。
#   ⚠️ 上游**没有**给「定州 / 辛集」独立的地市码（逐码单独出现的条目里一个都没有），
#      它们的数据并入保定 / 石家庄 —— 所以 `CITY_ORDER` 里不再保留这个虚构档位。
#   判据脚本：`probes/probe_city_code_semantics.py`（人眼判据：码名必须能在条目名里看到）。

# ★★ 地市的**统一命名空间** —— 页面下拉、行数据（`cty`）、对账脚本都用它。
#   刻意用**中文名**而不是任何一套码：
#     移动/电信是 4 位码（3190=邢台）、联通是 3 位码（185=邢台）。两套码并存时，
#     页面拿到一个 `cty` 值得先问「这是哪张表的」—— 而值本身长得也像（纯数字），
#     判错的结果是「某个市的筛选恒为空」，不报错、看着像「这个市本来就没资费」。
#   统一成名字后只有一套，且与下拉里显示的文本逐字相同。
#   ★ 页面由 __CITY_ORDER__ / __CITY_EXTRA__ 注入，不再自带副本 ——
#     自带副本的下场见 audit_data.check_sync 的历史注释（改了一边忘了另一边）。
CITY_ORDER = ("石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定",
              "张家口", "承德", "沧州", "廊坊", "衡水")
# 🔴 「省直辖（定州/辛集）」**已从本表移除**（2026-09-24）：上游三网都没有给它俩独立的
#   地市码 —— 移动/电信的 4 位码表里 3121 实测是「雄安新区」（见 HB_CITY 注释），
#   联通 cityList 的 12 个里也没有。留着一个**永远取不到数据**的档位，用户点进去
#   拿到的是「不限地市」那一批，会读成「这个市有 4811 条资费」——比不给这个档更糟。
#   （与 `#sc` 维度「取不到的档就置灰」是同一条原则：宁可让入口不存在，也不给假答案。）
#   它的数据并入保定（定州原属保定）/ 石家庄（辛集原属石家庄）。
# 「专属区域」：没有独立地市码的行政/功能区，只能从文案里认出来，页面下拉里单列一组。
#   雄安新区在**移动**那网就是这样（移动的 12 个码里没有雄安）；联通侧它有码
#   （CITY_CODES 里的 782），两条路最后都产出同一个名字 —— 这是刻意的：
#   名字一致，页面/筛选/分布图才不会把同一个地方当成两个。
CITY_EXTRA = ("雄安新区", "华北油田")
CITY_ALL = frozenset(CITY_ORDER) | frozenset(CITY_EXTRA)
# 允许「从文案里认地市」的网。
#   ★ 只有移动 / 电信：这两网的 applicableArea 覆盖不全（雄安新区 / 华北油田这类区域
#     根本没有码），而它们的文案写地市是可靠信号（实测移动文本兜底命中 373 条）。
#   ★ 联通**不在此列**：它每条资费都带回采集侧的城市归属，没标到的就是全省通用；
#     再从文案里认一遍反而会把实为全省的资费错标成某个市（下拉里就会少掉「全省通用」）。
#     广电上游没有地市粒度，更不该猜。
CITY_TEXT = frozenset(("move", "telecom"))
# ★★ 有「条目级地市维度」的网 —— 只有它们的条目才需要区分
#   「限定地市」（写 `cty`）与「不限地市」（写 `pw=1`，页面「仅全省通用」档读它）。
#   ★ 广电**不在此列**：上游区域码表只有「全国 / 河北省」两档，条目里没有地市，
#     页面上这一层整层隐藏（判据是「本网有没有 cty」，与这里同源）。
#   🔴 这个集合与**数据**必须一致，两个方向都有判据兜着（audit_data.check_city）：
#      在集合里却产不出任何 cty ⇒ 采集侧断了，页面会静默藏掉整个维度；
#      不在集合里却出现了 cty / pw ⇒ 有人漏把它加进来。
CITY_NETS = frozenset(("move", "telecom", "unicom"))
# 文案兜底的判据（与 CITY_ORDER / CITY_EXTRA 配对）；只在 code in CITY_TEXT 时用。
TEXT_CITY_LS = ("石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定",
                "张家口", "承德", "沧州", "廊坊", "衡水")
TEXT_CITY_ALIAS = (("雄安新区", "雄安"), ("华北油田", "华北油田|华油"))
HB_PROV_TOK = "HE"           # 2 字母省码 = 河北
HB_PROV_NUM = "311"          # 数字省码 = 河北
CN_TOK = "000"               # 全国标记
SCOPE_CN = {"hb": "河北", "cn": "全国"}

# ── 类型归一：四网「原始分类」→ 统一大类 ─────────────────────────────
# 需求：「分为河北省资费和全国资费…个人资费下面是什么套餐啊、加装包啊…四网都要这样」。
# 但四网的原始分类**根本不同构**（2026-09-22 实测）：
#   移动：加装包 / 营销活动 / 套餐 / 标准资费 / 港澳台-国际资费
#   联通：停售套餐 / 标准资费 / 加装包 / 套餐 / 港澳台-国际资费 / 营销活动
#   电信：加装包 / 营销活动 / 套餐
#   广电：5G套餐 / 促销 / 流量包 / 语音包 / 宽带 / 5G / 4G套餐 / 合约   ← 完全另一套
# 直接拿 `ty` 当筛选项，等于让用户按「上游怎么分类」去理解 —— 四个网四个说法，
# 「套餐」这个词在广电那网甚至不存在。所以在这里显式归一到大类。
# ★ 映射表**列全**而不是靠关键字猜：「5G」这种名字里没写「套餐」的也得进套餐；
#   靠 in 判断「包」字会把「套餐」也误吸进「加装包」。漏映射的会落到「其他」，
#   并由 CI 硬断言把它揪出来（见 .github/workflows/tariff-daily.yml）——
#   宁可停下让人看一眼，也不要页面里悄悄多出一个含义不明的分类。
CAT_ORDER = ("套餐", "加装包", "营销活动", "标准资费", "宽带", "港澳台/国际", "其他")
TYPE_CAT = {
    # 套餐族：广电的「5G / 4G套餐 / 合约」本质都是主套餐
    "套餐": "套餐", "5G套餐": "套餐", "4G套餐": "套餐", "5G": "套餐", "合约": "套餐",
    # 联通的「停售套餐」就是停了售的套餐；是否停售已由「已下架」页签表达，
    # 不该再占一个大类（否则「已停售」会成为联通独有的大类，四网又不同构了）
    "停售套餐": "套餐",
    # 加装包族：广电的「流量包 / 语音包」就是加在主套餐上的包（移动把它们并称「加装包」）
    "加装包": "加装包", "流量包": "加装包", "语音包": "加装包",
    "营销活动": "营销活动", "促销": "营销活动",
    "标准资费": "标准资费",
    "宽带": "宽带",
    "港澳台/国际资费": "港澳台/国际",
}


def type_cat(ty):
    """原始分类 → 统一大类。未映射的归「其他」（CI 会断言它为 0）。"""
    return TYPE_CAT.get(str(ty or "").strip(), "其他")


# ── 渠道归一：原文 → 线上 / 线下 / 两者都有 ───────────────────────────
# 渠道字段四网都是 100% 填充，但写法极其碎（移动光「线上」就有「线上渠道」
# 「线上及线下渠道」「线上、线下渠道，具体以各省实际为准，详询10086」三种写法；
# 还有「自有渠道（中国联通APP等）、互联网渠道（飞猪、携程等）」这种
# —— 看着像线下，其实是纯线上）。按原文筛毫无意义，归一到三档才有用。
# ★ 判定顺序要紧：先判「两者都有」，再判单一 —— 「线上线下」两个词都命中时
#   若先判单边就会把它误归成其中一边。
# ★ 归属（个人 / 政企）—— 四网里**只有移动**的上游接口带这个字段：
#   条目级 ``type1``（1=个人、2=政企）。联通/电信/广电的条目**根本没有 type1 键**。
#   ★ 2026-09-23 修正：此前「政企四网全不可得 ⇒ 不做政企层」的结论是**错的**。
#     当时拿 applicablePeople（目标客户）文本去猜，四网只捞到 68 条；
#     而真正的判据 type1 一直躺在快照里 —— 它被当成「分类层级」只用于分组，
#     从未落到行数据上，于是「字段在手却判成没有」。参考同类开源项目时才发现。
OW_CN = {"1": "个人", "2": "政企"}
OW_ORDER = ("个人", "政企")

# ── 板块（tariffAttr）：1 = 全国 / 跨省目录，2 = 本省目录 ──────────────
# 四网同义（移动 / 联通 / 广电的采集侧都这么用；见 probes/he_unicom_tariff.py
# 的注释「tariffAttributes：1 = 全国/跨省目录，2 = 本省目录」）。
# 🔴 **电信是例外**：那个适配器把该字段**写死成 "2" 当占位**（ct_monitor.py：
#   「页面不用 a1；与其他网行对齐」，真值另存 _tariffAttrRaw、语义至今未定）。
#   照它落盘，就等于把「这是本省资费」这么一个**我们根本没资格断言的说法**
#   印到页面上 —— 与「地市维度曾因信了数据源自报的 allProvince 而整层消失」
#   是同一类错误的镜像：那次是信了错的声明、这次会是信了一个占位值。
#   ⇒ 判据取**数据**：只有本网该字段**真的出现两个取值**时才落盘这一维。
#     单档维度本身也没有筛选价值（选与不选一个样），宁可不给。
ATTR_CN = {"1": "全国资费", "2": "本省资费"}

# 联通「停售套餐(99)」—— 它**不是分类，是状态桶**。见 rows_of 里的还原规则。
STOPPED_L1 = "停售套餐"

# 变更历史 —— 逐次追加、不覆盖，是页面「变化历史」时间线的**唯一**数据源。
# changes/*.md 是给人读的长文（含逐条明细），不适合页面解析：正则一改版就全废，
# 而它本身是产物、不是接口。另存一份结构化摘要，两边各干各的。
HIST_FILE = os.path.join(BASE, "history.json")
HIST_KEEP = 400          # 只留最近 400 条「网×批次」（约 100 次巡检，够回溯半年）

CH_ORDER = ("线上", "线下", "线上+线下", "其他")
# 「全渠道 / 全部」这类没点名线上线下的写法，语义就是「两边都能办」——
# 漏进「其他」会让「线上」这一档少掉 120 条（实测移动 15 + 联通 105）。
CH_ALL = ("全渠道", "全部渠道", "全部", "不限渠道", "各渠道", "任何渠道")
CH_ON = ("线上", "在线", "网厅", "掌厅", "官网", "网上", "互联网", "APP", "app", "App",
         "微信", "公众号", "小程序", "外呼", "热线", "10086", "10010", "10000", "10099",
         # 电话/短信/商城/客户端都属自助渠道。联通的「宽视界大屏端」是电视端自办，
         # 也算线上 —— 它不是实体营业厅。
         "电话", "短信", "商城", "客户端", "大屏", "宽视界", "兑换",
         "抖音", "电视端", "IPTV", "会员中心")
CH_OFF = ("线下", "营业厅", "门店", "实体", "社会渠道", "代理", "自有渠道",
          # 校园/合作/指定/代办/装维都是要去实体点或由人上门办的
          "校园", "合作", "指定", "装维", "自办厅", "代办", "加盟",
          "网点", "厅店", "客户经理")


def ch_norm(s):
    """渠道原文 → 归一档。认不出的归「其他」并原样保留在页面（可搜可看）。"""
    t = str(s or "").strip()
    if not t:
        return "其他"
    if any(k in t for k in CH_ALL):
        return "线上+线下"
    on = any(k in t for k in CH_ON)
    off = any(k in t for k in CH_OFF)
    if on and off:
        return "线上+线下"
    if on:
        return "线上"
    if off:
        return "线下"
    return "其他"


def _uniq(seq):
    """保序去重（地市码可能同时出现在 applicableArea 与 city 两个字段里）。"""
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _toks(v):
    return [t.strip() for t in str(v or "").split(",") if t.strip()]


def _mk_cities(codes):
    """4 位地市码列表 → 统一中文名列表（去重、丢掉码表里没有的）。"""
    return _uniq([HB_CITY[t] for t in _uniq(codes) if t in HB_CITY])


def text_cities(e):
    """从**文案**里认地市 —— 只给 ``CITY_TEXT`` 里那两网用（见常量注释）。

    返回中文名列表（可为空）。四个字段取**并集**，缺一不可，这是实测的：
      · 只看名称 → 漏 46 条：地市只写在「目标客户」里的「移动云盘2026特惠季包」（秦皇岛）
      · 只看「目标客户」→ 漏 103 条：市级包名带地市、但该字段只写通用话术的
        「邯郸179元低消回馈」「秦皇岛10元语音低消」…
    返回数组而非单值：一条可属多市（IPTV 类在目标客户里列 4 个市；
    「沧州华油尊享礼包」同属沧州与华北油田）。
    """
    s = " ".join(str(e.get(k) or "") for k in ("name", "tariffName",
                                               "applicablePeople", "otherContent"))
    out = [c for c in TEXT_CITY_LS if c in s]
    for name, pat in TEXT_CITY_ALIAS:
        if re.search(pat, s):
            out.append(name)
    return _uniq(out)


def _mv_where(e):
    """移动条目地域 → ``(sc, cities)``（cities 是**中文名**）。

    ``sc``      ``"hb"`` 河北 / ``"cn"`` 全国 / ``""`` 与河北无关（整条丢弃）
    ``cities``  地市名列表（空列表 = 全省通用）。一条资费可覆盖多个市（CSV），
                也可能同时出现在 `applicableArea` 与 `city` 两个字段里 ⇒ 去重。

    ★ 判序要点：**地市码优先于全国码**。实测有条目 `applicableArea` 同时含
      地市码与 `000`，它的语义是「这个市里按全国资费执行」⇒ 归地市更具体。
    """
    aa, ct, pv = _toks(e.get("applicableArea")), _toks(e.get("city")), _toks(e.get("province"))
    # ★★ 「全省通用」有**两种**写法，第二种以前没被识别（2026-09-24 修）：
    #     ① applicableArea / city 留空                     → 无地域限制
    #     ② 把**全部 12 个地市码**都列出来（实测常整串重复两遍）→ 覆盖面就是全部地市
    #   ② 若直接走下面的 _mk_cities，会被映射成 12 个市名 ⇒ 判成「12 城专属」：
    #     这批（实测移动 59 条，多是「0预存5G金币购机合约活动」这类全省活动）
    #     既不在「仅全省通用（不限地市）」档里，又被计进「地市专属」总数。
    #   判据用「码集合 ⊇ 全部地市码」而不是「token 数 ≥ 12」—— 后者会被重复 token
    #   或非地市码骗到；只有真的覆盖全 12 个码才算全省。
    #   ⚠️ 电信那批「11 个 / 10 个码」**不适用**本判据：实测它们确实只限那几个市
    #     （农业/扶贫/宽带政策类，少的正是廊坊、唐山等），不能算全省。
    if HB_CITY_CODES <= set(_uniq(aa + ct)):
        return "hb", []
    cities = _mk_cities(aa + ct)
    if cities:
        return "hb", cities
    if CN_TOK in aa:
        return "cn", []
    # `!AH` / `!AH,!HI` = 「除这些省以外」，语义上等效全国（实测 12 条）
    if any(t.startswith("!") for t in aa):
        return "cn", []
    letters = [t for t in aa if len(t) == 2 and t.isalpha()]
    if len(letters) >= 2:                      # 多省 CSV
        return ("cn", []) if HB_PROV_TOK in letters else ("", [])
    if len(letters) == 1:
        return ("hb", []) if letters[0] == HB_PROV_TOK else ("", [])
    if len(pv) >= 2:                           # province 侧的多省数字码列表
        return ("cn", []) if HB_PROV_NUM in pv else ("", [])
    if len(pv) == 1:
        return ("hb", []) if pv[0] == HB_PROV_NUM else ("", [])
    if ct and all(t in HB_CITY for t in ct):
        return "hb", _mk_cities(ct)
    if not aa and not ct and not pv:           # 三字段全空 = 无地域限制 ⇒ 全省通用
        return "hb", []
    return "", []


def _ct_where(e):
    """电信条目地域 → ``(sc, cities)``（cities 是**中文名**）。

    ★ 整套电信数据的 provCode 就是 609906（河北），所以**默认 hb 是保守且正确的**；
      只有条目自己声明了地市码时才细分（`applicableArea` 是 CSV，含 3190 即邢台）。
      这里读的是 ct_monitor 归一化时特意保留的 `_areaCodes`（原来是丢掉的）。

    ★ 「全部 12 个地市码都列出来」= 全省通用，与 `_mv_where` 同一判据（见那边的注释）。
      实测电信这批（10~11 个码）**不是**全省 —— 它们确实只限那几个市，所以本判据
      只在**恰好覆盖全 12 码**时生效，不会误伤。
    """
    toks = _toks(e.get("_areaCodes"))
    if HB_CITY_CODES <= set(_uniq(toks)):
        return "hb", []
    return "hb", _mk_cities(toks)


def _uc_where(e):
    """联通条目地域 → ``(sc, cities)``（cities 是**中文名**）。

    联通**没有地域字段**，但采集侧有 —— 12 城并集采集时，每条资费都带回了
    「它的三级目录出现在哪些城市」（见 probes/he_unicom_tariff.collect）：

      · ``_cityNames``：城市名列表，**没有**覆盖全部城市 ⇒ 只在这些城市的目录里；
      · ``_allCity=1``：覆盖了全部城市 ⇒ 全省通用（采集侧刻意不落列表，见那边注释）。

    🔴 这里读的键必须在**采集侧**就被换成中文名（同 CITY_ORDER / CITY_EXTRA 那套）。
      不要在这边另配一张「联通 3 位码 → 名字」的表 —— 两张表要同步，而它们迟早会漂。

    ⚠️ 老快照（2026-09-24 之前采的）两个键都没有 ⇒ 一律按「全省通用」渲染，
      页面因此**不出地市这一层**（它按「有没有 cty」判）。这是刻意的 fail-safe：
      数据里没有地市信息时，宁可不给这个维度，也不要拿一个不存在的维度去筛
      （用户选「邢台」会得到 0 条，而那看起来像「邢台没有资费」）。
    """
    return "hb", _uniq([n for n in (e.get("_cityNames") or []) if n in CITY_ALL])


WHERE_OF = {
    "move": _mv_where,
    "telecom": _ct_where,
    "unicom": _uc_where,
    # 广电在上游只有「全国 / 河北省」两档地区，条目里没有地市字段 ⇒ cities 恒空。
    "cbn": lambda e: (("cn" if "全国" in str(e.get("_areaNames") or "") else "hb"), []),
}


def where_of(code, e):
    """取条目的地城归属 ``(sc, cities)``；未知网返回 ``("hb", [])``（宁可多留，不可静默丢）。"""
    f = WHERE_OF.get(code)
    return f(e) if f else ("hb", [])


def scope_of(code, e):
    """兼容包装：只要 ``sc``（``hb`` / ``cn`` / ``""``）。"""
    return where_of(code, e)[0]

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


def _declared(b):
    """系列声明的明细条数 = nonModuleListTotal + moduleListTotal；取不到返回 None。"""
    try:
        n, m = b.get("nonModuleListTotal"), b.get("moduleListTotal")
        if n is None and m is None:
            return None
        return int(n or 0) + int(m or 0)
    except (TypeError, ValueError):
        return None


def fetch_group(c):
    a, t1, t2v = str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2"))
    entries, beans_all, page, total, fails = [], [], 1, None, 0
    short, miss_eg = 0, []
    while True:
        # ★★ fistLimit = **每个系列最多返回多少条明细**（不是外层分页大小！）
        #   默认传 100 时，条目数 >100 的系列会被静默截断（2026-09-23 实锤：
        #   attr=1/t2=4 某个系列声明 178 条只回 100；全网加装包某系列声明 244 只回 100）。
        #   ⇒ 传 5000（实测 0 / 1000 / 5000 拿到的条数一致且等于上游声明数）。
        #   limit=100 才是外层「每页几个系列」，page 翻的是系列页。
        lst = call("nrtariff/new/Tariff/getTariffListInfo",
                   {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                    "tariffAttr": a, "type1": t1, "type2": t2v,
                    "page": page, "limit": 100, "fistLimit": 5000})
        d = lst.get("data") if isinstance(lst, dict) else None
        if not isinstance(d, dict):
            fails += 1
            log(f"  !! attr={a} t1={t1} t2={t2v} page={page} 异常: {str(lst)[:150]}")
            break
        for b in d.get("beans") or []:
            # ★★ 一个系列下的明细有**两种容器**，只取 nonModuleList 会整栏漏采：
            #   nonModuleList          —— 大多数栏目走这里
            #   moduleList[].tariffList —— 「模组化」资费走这里（上游另有 moduleListTotal
            #                              / nonModuleListTotal 两个计数，本来就是并列的两种）
            #   2026-09-23 实锤：attr=1/type2=4（全网资费·港澳台国际专区）34 个系列的
            #   **1240 条全在 moduleList**，nonModuleList 恒空 ⇒ 页面这一栏一直是 0 条，
            #   而且因为「枚举组合是对的、接口也 200」，日志里完全看不出异常。
            #   全量核对：18 个组合里 4 个有 moduleList，合计 1257 条（占当时总量 32.6%）。
            # ★ 两类容器**互斥**（按 (name, reportNo) 求交集 = 0），可直接拼接、无需去重。
            en = list(b.get("nonModuleList") or [])
            n_mod = 0
            for m in b.get("moduleList") or []:
                tl = m.get("tariffList") or []
                n_mod += len(tl)
                en.extend(tl)
            entries.extend(en)
            beans_all.append({"tariffSeqno": b.get("tariffSeqno"),
                              "tariffName": b.get("tariffName"), "count": len(en),
                              "nonModule": len(en) - n_mod, "module": n_mod,
                              "declared": _declared(b)})
            # ★★ 对账守卫：上游**每个系列**都带 nonModuleListTotal / moduleListTotal。
            #   声明数 ≠ 实取数 ⇒ 要么还有第三种容器没消费，要么 fistLimit 又截断了。
            #   这个判据比外层的 data.page.total 精确得多：
            #   上面 moduleList 整栏漏采（1240 条）和 fistLimit 截断（单系列 244→100）
            #   在旧逻辑下**接口都返回 rc=0、外层 total 也正常**，只有这里能照出来。
            dec = _declared(b)
            if dec is not None and dec != len(en):
                short += max(0, dec - len(en))
                if len(miss_eg) < 5:
                    miss_eg.append(f"{b.get('tariffSeqno')}:{len(en)}/{dec}")
        pg = d.get("page") or {}
        total = pg.get("total")
        pages = pg.get("pages") or 1
        if page >= pages:
            break
        page += 1
    n_mod_all = sum(b.get("module") or 0 for b in beans_all)
    log(f"  attr={a} t1={t1} t2={t2v} total={total} series={len(beans_all)} "
        f"entries={len(entries)} (其中 moduleList={n_mod_all}) fails={fails}"
        + (f"  ⚠️ 缺 {short} 条 例 {miss_eg}" if short else ""))
    return {"tariffAttr": a, "type1": t1, "type2": t2v, "total": total,
            "series": beans_all, "entries": entries, "short": short}


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
    # 空栏目自检：上游说有数据（系列数 > 0）而我们一条都没取到 ⇒ 一定是容器/参数没对上，
    # 不能当「这个栏目本来就没内容」放过去（2026-09-23：全网·港澳台国际专区就是这么丢的）。
    empty = [f"attr={g['tariffAttr']}/t1={g['type1']}/t2={g['type2']}"
             for g in groups if (g.get("series") or []) and not g.get("entries")]
    if empty:
        log(f"  ⚠️ 有系列却零条目的栏目：{empty}")
    total_short = sum(g.get("short") or 0 for g in groups)
    log(f"抓取完成：{n} 条 / {time.time() - t0:.0f}s"
        + (f"；⚠️ 声明对账缺 {total_short} 条（看上面 ⚠️ 行）" if total_short else
           "；声明对账 0 缺口"))
    return {"province": PROV, "provinceName": "河北",
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "endpoint": ROOT + "nrtariff/new/Tariff/getTariffListInfo",
            "short": total_short, "groups": groups}


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


def load_history():
    """读 history.json。缺失/损坏一律回空表 —— 它是展示用的旁路数据，
    **不该**因为一份坏文件把整轮巡检或页面构建打断。"""
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"schema": 1, "items": []}


def _code_of_net(net):
    """报告里的中文网名 → 网 code（NETS_META 的显示名是后缀匹配：
    「河北移动」「河北联通」「中国广电」「河北电信」都对得上）。"""
    for c, cn, _ in NETS_META:
        if net == cn or net.endswith(cn):
            return c
    return ""


def hist_append(rec):
    """往 history.json 写一条「某网某轮巡检」的变更记录 —— 同一天同一网只留最新一条。

    ★ 去重键是 **(日期, 网)**，不是 (抓取时刻, 网)。一天里跑两轮是常态
      （定时巡检 + 手动 workflow_dispatch / CI 重试），而两轮的比对基线都是
      「上一个不同日期的快照」，所以后一轮的数字**覆盖**前一轮，不该各记一条。
      按时刻去重会让时间线上同一天冒出两组互相矛盾的数字
      （2026-09-23 实测：上午移动 3861 · 新增 0，下午 3862 · 新增 1 ——
       页面顶部「本次巡检」显示的是后者，历史页里两条并存，看着像数据错乱）。
    ★ 覆盖时**原位替换**，不删掉再追加：items 按时间顺序读，当天那条本就该待在
      自己日期的位置上。
    ★ 只保留最近 HIST_KEEP 条：这个文件在仓库里、每轮巡检都提交，
      不设上限就会一年年涨上去。
    """
    h = load_history()
    items = list(h["items"])
    key = (rec.get("d"), rec.get("code"))
    for i, x in enumerate(items):
        if (x.get("d"), x.get("code")) == key:
            items[i] = rec
            break
    else:
        items.append(rec)
    h["schema"] = 1
    h["items"] = items[-HIST_KEEP:]
    # indent=1 而不是紧凑：这个文件每天被 CI 提交一次，缩进让 diff 只显示
    # 「新增了哪条记录」；紧凑格式整个文件是一行，diff 里什么都看不出来。
    # 🔴 newline="\n" 必须显式给：Windows 下默认会把 \n 翻译成 \r\n，
    #    于是每次 CI（Linux，写 LF）与本地（Windows，写 CRLF）交替提交时，
    #    整个文件的每一行都显示为「已修改」，真正的变更淹没在行尾噪音里。
    # 🔴 走原子写：这个文件一旦写坏，页面时间线与 CI 的 history 断言会一起红，
    #    而它是**产物不是接口** —— 宁可保持上一版完整内容。
    if G:
        G.write_json(HIST_FILE, h)
    else:
        with open(HIST_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(h, f, ensure_ascii=False, indent=1)
    return h


def write_report(old_o, new_o, added, removed, changed,
                 net="河北移动", fname=None, guard=None):
    """写变更报告。

    ``net`` / ``fname`` 是接第二网时加的：联通那网要写自己的标题，
    报告也不能和移动挤同一个 ``changes/<日期>.md``（会互相覆盖）。

    ``guard`` ＝ ``change_guard.audit_diff()`` 的结果（可为 None）。
    ★ 传进来的 ``added`` / ``removed`` / ``changed`` 已经**是护栏核验后**的集合；
      ``guard`` 只用来补一段「被摘掉了什么、为什么」—— 那段不能省：
      一个数字变小了而报告里不说明为什么变小，下一个看报告的人只会怀疑数据错了。
    """
    d = new_o.get("fetchedAt", "")[:10]
    idx, oidx = index_rows(new_o), index_rows(old_o)
    L = [f"# {net}资费变更报告 · {d}", "",
         f"- 本次抓取：{new_o.get('fetchedAt')}",
         f"- 上次抓取：{old_o.get('fetchedAt', '（无）')}",
         f"- 条目数：{len(oidx)} → **{len(idx)}**",
         f"- 新增 **{len(added)}** · 下线 **{len(removed)}** · 字段变更 **{len(changed)}**", ""]

    # ── 护栏段落：把「被抑制的假变化」摊开 ─────────────────────────────
    # 这一段的定位是**审计凭证**：报告头部的三个数字是「可信变化」，
    # 而这里回答「那剩下的去哪了」。没有它，护栏本身就成了一个黑箱 ——
    # 而黑箱化的护栏比没有护栏更危险（真变化被吃掉时无人能察觉）。
    if guard:
        gsec = []
        rel = guard.get("relocated") or []
        if rel:
            gsec += [f"### 身份漂移 {len(rel)} 条（不计入新增/下线）", "",
                     "同一条资费换了栏目/板块 —— 身份键里含栏目名，不加这一步会被记成"
                     "「下线一条 + 新增一条」。", ""]
            for a, b in rel[:20]:
                gsec.append(f"- **{oidx.get(a, {}).get('_name') or idx.get(b, {}).get('_name') or a}**"
                            f"　`{a}` → `{b}`")
            if len(rel) > 20:
                gsec.append(f"- …（其余 {len(rel) - 20} 条见快照）")
            gsec.append("")
        sm = guard.get("state_moved") or []
        if sm:
            downs = [(a, b) for a, b, d in sm if d == "to_stopped"]
            ups = [(a, b) for a, b, d in sm if d == "from_stopped"]
            gsec += [f"### 状态桶迁移 {len(sm)} 条（**已计入**上面的下线/新增，不重复记）", ""]
            if downs:
                gsec.append(f"- 转入停售目录 **{len(downs)}** 条 → 计为下架")
            if ups:
                gsec.append(f"- 从停售目录转回在售 **{len(ups)}** 条 → 计为新增")
            gsec += ["", "> 上游把资费在「在售目录」与「停售目录（一级分类 99）」之间挪动。"
                         "同一条被挪动时旧键下线、新键上线，**只能算一次** —— "
                         "否则就是那个「新增 120 / 下线 123」的老毛病。", ""]
            gsec.append("> ⚠️ 这一类**不按下线日期复核**：联通停售桶的 endDate 实测几乎都未到期"
                        "（本轮 110 条里有 4 条写着 2029-12-31），桶归属才是它的下架证据。")
            gsec.append("")
        fr = guard.get("fake_removed") or []
        if fr:
            gsec += [f"### 假下架 {len(fr)} 条（下线日期尚未到期，判为漏采）", ""]
            for k in fr[:20]:
                r = oidx.get(k, {})
                gsec.append(f"- **{r.get('_name') or r.get('_tname')}**"
                            f"　下线日 `{r.get('offineDay') or '—'}`")
            gsec += ["", "> 上游按随机子集返回时，「本轮没采到」与「业务下架」长得一模一样；"
                         "下线日期是唯一能区分二者的字段。", ""]
        rs = guard.get("restored") or []
        if rs:
            gsec += [f"### 补录 {len(rs)} 条（上线日早于上一轮基线，不是新上架）", ""]
            for k in rs[:20]:
                r = idx.get(k, {})
                gsec.append(f"- **{r.get('_name') or r.get('_tname')}**"
                            f"　上线日 `{r.get('onlineDay') or '—'}`")
            gsec += ["", "> 上一轮限流/降级漏采 ⇒ 本轮补回。**不计新增、不进推送**。", ""]
        for n in (guard.get("notes") or []):
            L.append(f"> 🛡 {n}")
        if guard.get("notes"):
            L.append("")
        if gsec:
            L += ["## 🛡 护栏核验（以下**不计入**上面的三个数字）", ""] + gsec

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
    if G:
        G.atomic_write_text(p, txt, newline="\n")
    else:
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    # ★ 顺手把这一网的变更摘要追加进 history.json（页面时间线用）。
    #   放在 write_report 里、而不是各调用点：移动走 main()、其余三网走 net_round()，
    #   两处**都**经过这里；写在调用点就得分两遍，漏一处 = 时间线里少一网且不报错。
    _smp = []
    for k in added[:6]:
        r = idx[k]
        _smp.append({"n": (r.get("_name") or r.get("_tname") or "")[:60],
                     "ty": r.get("_ty") or "", "k": "a"})
    for k in removed[:4]:
        r = oidx[k]
        _smp.append({"n": (r.get("_name") or r.get("_tname") or "")[:60],
                     "ty": r.get("_ty") or "", "k": "r"})
    for k, dd in changed[:4]:
        r = idx[k]
        _smp.append({"n": (r.get("_name") or r.get("_tname") or "")[:60],
                     "ty": r.get("_ty") or "", "k": "c", "f": list(dd.keys())[:3]})
    rec = {"ts": str(new_o.get("fetchedAt") or "")[:19], "d": d,
           "code": _code_of_net(net), "net": net, "n": len(idx),
           "a": len(added), "r": len(removed), "c": len(changed), "smp": _smp}
    if guard:
        # 护栏数字也记进时间线：只有「本轮 a=0/r=0」而没有任何解释时，
        # 页面上的「本次无变化」会被读成「上游确实没变」——
        # 而它有可能其实是「数据异常已冻结」或「判定为基线回弹」。
        for k, n in (("restored", "gs"), ("fake_removed", "gf"),
                     ("relocated", "gl"), ("state_moved", "gm")):
            if guard.get(k):
                rec[n] = len(guard[k])
        if guard.get("rebound"):
            rec["note"] = "rebound"
    hist_append(rec)
    return p, txt


def write_page(html_body, tag=""):
    """把页面**先写成 ``.new``，校验通过再原子替换** ``docs/index.html``。

    🔴 为什么不能直接盖：这个文件是部署产物，坏掉的表现是**全站白屏**
       （`const NETS=` 那一段语法错了，整页 JS 全废，而标签看着都在）。
       而「写到一半被 kill」「模板改坏」这两件事都不报错、只是留下一个坏文件 ——
       上一步刚刚算好、下一轮才能重来的数据就这么没了。
       先写 ``.new`` 再验，等于给这一步加了一道闸门：
       闸门不过就**保留上一版可用的页面**，明明白白报错，而不是把线上打黑。

    返回 (是否成功, 说明)。
    """
    tmp = HTML_DST + ".new"
    if G:
        G.atomic_write_text(tmp, html_body, newline="\n")
    else:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(html_body)
    s = open(tmp, encoding="utf-8").read()
    # 占位符不许残留：漏替换会让 `const HIST=__HIST__` **直接变成语法错误**，
    # 整页功能全废而标签都在 —— CI 里那条同类断言也是照这个写的。
    left = [p for p in PLACEHOLDERS if p in s]
    if left:
        log(f"!! 页面校验不通过（占位符未替换 {left}），保留上一版 {os.path.basename(HTML_DST)}")
        os.remove(tmp)
        return False, f"占位符未替换: {left}"
    if "const NETS=" not in s:
        log("!! 页面校验不通过（找不到 const NETS= 数据容器），保留上一版页面")
        os.remove(tmp)
        return False, "缺少 const NETS="
    if len(s) < 100000:
        # 四网页面实测 2.5 MB 量级；小到十万字节说明构建中途出了事。
        log(f"!! 页面校验不通过（只有 {len(s)} 字节，明显不完整），保留上一版页面")
        os.remove(tmp)
        return False, f"体积过小 {len(s)}"
    os.replace(tmp, HTML_DST)
    log(f"页面已写入 {os.path.relpath(HTML_DST, BASE)}（{len(s)/1024:.0f} KB{tag}）")
    return True, "ok"


def _load_prev2(prefix="hebei_tariff_"):
    """取**上上一轮**的快照（严格早于「上一轮」那份），仅用于基线回弹判定。

    没有就返回 ``(None, None)`` —— 回弹判据会自动退到「计数级」兜底，
    而不是拿一份空快照去比对（空快照会让「本轮整批下线」看起来像回弹，
    把一批真实的删除吃掉）。
    """
    fs = snap_paths(prefix)
    if len(fs) < 2:
        return None, None
    p = fs[-2]
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f), p
    except Exception as e:
        log(f"-- 上上轮快照不可读（{os.path.basename(p)}）：{type(e).__name__}: {e}")
        return None, None


def last_hist(code, day=""):
    """取 history.json 里该网最近的**一条更早的**记录（用于回弹的计数级判据）。

    🔴 必须显式排除 ``d == day``：当天重跑（手动 dispatch / CI 重试）时，
       当天那条记录用的基线和本轮完全相同，拿它当「上一轮」会得出
       a=0/r=0 之类的假证据。同一天里第一次跑时，它本来也不存在。
    """
    items = load_history().get("items") or []
    for x in reversed(items):
        if x.get("code") == code and str(x.get("d") or "") != str(day or ""):
            return x
    return None


def diff_round(code, cn, tag, data, old_o, prev_p, today, fname=None):
    """一轮数据的完整处理：**结构体检 → diff → 护栏核验 → 写报告 → 记 history**。

    移动走 ``main()``、其余三网走 ``net_round()`` —— 但这两条路必须共用本函数。
    各写一份的代价：改一处漏一处时，症状是「某网静默地不再核验假变化」，
    而日志里一切正常、页面照常更新，没人会发现。

    返回 summary dict（供推送、页面提示、CI 汇总使用）：
      ``n`` ``added`` ``removed`` ``changed`` 计数
      ``restored`` ``fake_removed`` ``relocated`` 被护栏摘掉的条数
      ``samples`` 供推送用的样例 · ``report`` 报告路径 · ``note`` 抑制原因
      ``guard`` 原始护栏结果（供报告用）
    """
    prefix = SNAP_PREFIX[code]
    base_day = (old_o or {}).get("fetchedAt", "")[:10]
    idx_old, idx_new = index_rows(old_o), index_rows(data)
    out = {"code": code, "net": cn, "n": len(idx_new), "added": 0, "removed": 0,
           "changed": 0, "restored": 0, "fake_removed": 0, "relocated": 0,
           "state_moved": 0, "samples": [], "report": "", "note": "", "guard": None,
           "_added_keys": [], "_removed_keys": [], "_changed_keys": []}

    if not G:
        # 护栏不可用时**照旧出报告**，但把这件事吼出来 ——
        # 静默降级成「没有核验」比没有核验更糟。
        log(f"!! 护栏模块不可用（{_G_ERR}），本轮不做假变化核验")
    else:
        sig_old = G.schema_sig(old_o, KEY_FIELDS)
        sig_new = G.schema_sig(data, KEY_FIELDS)
        if G.REBUILD_ON_SCHEMA and sig_old != sig_new:
            # ④ 结构变更 ⇒ 只重建基线，不通知。
            #   为什么必须这样：我们改采集字段（或上游开始普遍填一个新字段）时，
            #   每条资费都会「多出一个字段有值」⇒ 一次改动炸出上千条「字段变更」，
            #   而它们**没有一条**是业务变化。重建基线是唯一诚实的选择。
            log(f"!! {cn} 字段结构变更：{len(sig_old)} 个字段 → {len(sig_new)} 个字段"
                f"（新增 {sorted(set(sig_new) - set(sig_old))}，"
                f"消失 {sorted(set(sig_old) - set(sig_new))}）—— 只重建基线，不通知")
            out["note"] = "schema"
            if G:
                G.atomic_write_text(
                    os.path.join(CHG, fname or
                                 f"{tag}-{today[:4]}-{today[4:6]}-{today[6:]}.md"),
                    f"# {cn}资费基线重建 · {data.get('fetchedAt', '')[:10]}\n\n"
                    f"- 本轮检测到**字段结构变更**，只重建基线、不出变更报告、不推送\n"
                    f"- 旧字段：{'、'.join(sig_old) or '（无）'}\n"
                    f"- 新字段：{'、'.join(sig_new) or '（无）'}\n"
                    f"- 条目数：{len(idx_old)} → **{len(idx_new)}**\n"
                    f"- 原因：结构一改，每条资费都会「多/少一个字段有值」，"
                    f"逐条报「字段变更」等于一次刷屏上千条，且无一是业务变化。\n",
                    newline="\n")
            hist_append({"ts": str(data.get("fetchedAt") or "")[:19],
                         "d": str(data.get("fetchedAt") or "")[:10],
                         "code": code, "net": cn, "n": len(idx_new),
                         "a": 0, "r": 0, "c": 0, "note": "schema", "smp": []})
            return out

    a, r, c = diff_rows(idx_old, idx_new)
    guard = None
    if G:
        old2, _ = _load_prev2(prefix)
        guard = G.audit_diff(
            a, r, c, idx_old, idx_new,
            today=today,
            base_day=base_day,
            old2_rows=(index_rows(old2) if old2 else None),
            prev_rec=last_hist(code, str(data.get("fetchedAt") or "")[:10]),
            log=log)
        a, r, c = guard["added"], guard["removed"], guard["changed"]
        out.update({"restored": len(guard["restored"]),
                    "fake_removed": len(guard["fake_removed"]),
                    "relocated": len(guard["relocated"]),
                    "state_moved": len(guard["state_moved"])})
        if guard["rebound"]:
            # ③ 基线回弹：本轮不计变化、不推送，**但报告照写**（留审计痕迹）。
            log(f"!! {cn} 判定为基线回弹（{'；'.join(guard['suspected'])}），"
                f"本轮不计变化、不推送")
            out["note"] = "rebound"
            a, r, c = [], [], []

    rp, _ = write_report(old_o, data, a, r, c, net=cn,
                         fname=fname or f"{tag}-{today[:4]}-{today[4:6]}-{today[6:]}.md",
                         guard=guard)
    out.update({"added": len(a), "removed": len(r), "changed": len(c),
                "report": rp, "guard": guard,
                # 键集合要给调用方（页面标注 ca/ck 用），但不下放给推送 ——
                # 推送只该拿到「几条 + 几个样例」，不是全量键。
                "_added_keys": list(a), "_removed_keys": list(r),
                "_changed_keys": [k for k, _ in c],
                "samples": _samples(a, r, c, idx_new, idx_old)})
    return out


def _samples(added, removed, changed, idx_new, idx_old, max_n=8):
    """推送用的样例条目（只挑几条，通知不是报告）。"""
    out = []
    for k in list(added)[:max_n]:
        r = idx_new.get(k) or {}
        out.append({"n": (r.get("_name") or r.get("_tname") or "")[:40],
                    "ty": r.get("_ty") or "", "k": "a"})
    for k in list(removed)[:4]:
        r = idx_old.get(k) or {}
        out.append({"n": (r.get("_name") or r.get("_tname") or "")[:40],
                    "ty": r.get("_ty") or "", "k": "r"})
    for k, _ in list(changed)[:4]:
        r = idx_new.get(k) or {}
        out.append({"n": (r.get("_name") or r.get("_tname") or "")[:40],
                    "ty": r.get("_ty") or "", "k": "c"})
    return out


def _degrade(code, cn, n, n_old):
    """⑤ 降级状态机的一层薄封装：读状态 → 判定 → 落盘。返回 (动作, 轮次说明)。"""
    if not G:
        # 护栏不可用：退回「单轮阈值」的老行为，但把这件事说清楚
        return ("hold" if (n_old > 0 and n < n_old * DEGRADE_RATIO) else "ok"), ""
    st = G.read_json(DEGRADE_STATE, {}) or {}
    act, st = G.degrade_step(st, code, n, n_old,
                             stamp=time.strftime("%Y-%m-%d %H:%M:%S"))
    G.write_json(DEGRADE_STATE, st)
    rec = st.get(code) or {}
    if act == "hold":
        return act, (f"第 {rec.get('rounds')}/{DEGRADE_ACCEPT_ROUNDS} 轮"
                     f"（连续 {DEGRADE_ACCEPT_ROUNDS} 轮才接受新数据）")
    if act == "accept":
        return act, f"连续 {DEGRADE_ACCEPT_ROUNDS} 轮偏低，认定源站现状如此，接受并重建基线"
    return act, ""


def _hold(code, cn, tag, data, old_o, prev_p, today, why, note="degraded", fname=None):
    """降级「冻结旧数据」的收尾：写一份说明报告 + 记一条带 note 的 history。

    🔴 为什么不能**静默**保留旧数据：页面看着正常、数据其实是旧的 ——
       这正是「一直冻结旧数据」最危险的地方。所以：
         · 页面照旧渲染上一版（数据不断供）；
         · changes/<日期>.md 里写明「本轮没采到，沿用上一版」；
         · history 里记 ``note``，页面时间线上那一条会显示成「数据异常已冻结」，
           而不是伪装成「本次无变化」。
    """
    day = f"{today[:4]}-{today[4:6]}-{today[6:]}"
    rp = os.path.join(CHG, fname or f"{tag}-{day}.md")
    n_old = sum(len(g["entries"]) for g in (old_o or {}).get("groups") or [])
    txt = (f"# {cn}资费巡检 · {day}\n\n"
           f"- ⚠️ 本轮**未采用**新数据：{why}\n"
           f"- 本轮采到：{data.get('fetchedAt') if data else '（采集失败）'}\n"
           f"- 页面与报告沿用上一版快照（{n_old} 条）\n"
           f"- 原因：数据量/结构异常时直接换基线，会把「本轮没采到」记成"
           f"「一大批资费下架」—— 实测同类项目一次假下架 1613 条（96% 的下线日期仍在未来）。\n")
    if G:
        G.atomic_write_text(rp, txt, newline="\n")
    else:
        with open(rp, "w", encoding="utf-8") as f:
            f.write(txt)
    hist_append({"ts": str((data or {}).get("fetchedAt") or "")[:19], "d": day,
                 "code": code, "net": cn, "n": n_old, "a": 0, "r": 0, "c": 0,
                 "note": note, "smp": []})
    return rp, txt


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
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    # ★ 原子写：归档是「本机不跑抓取就能看」的唯一来源，写坏等于这一版页面丢了。
    if G:
        G.atomic_write_bytes(PAGE_GZ, buf.getvalue())
    else:
        with open(PAGE_GZ, "wb") as f:
            f.write(buf.getvalue())
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
# 支持「连下架资费一起采」的网 —— 适配器 fetch_all 接受 include_stopped。
# 需求：「各运营商下架的资费也要收集全」。移动**不在**此列：实测它的 isPublic=0
# 虽能列出分类，但明细接口恒返回 0 条，本网拿不到下架数据（见 state_of 注释）。
NET_STOPPED = {"unicom", "cbn"}
# ★「适配器是**纯本地转换**」的网 —— 它的 fetch_all() 不发网络请求，
#   读的是浏览器采集产物（电信读 .ct_raw.json）。这类网**不该进本地缓存**：
#   缓存只带来一种风险 —— 采集产物更新了而缓存还是旧的，
#   本地重建出来的页面看不出这一点，且没有任何提示（2026-09-23 实测踩到：
#   .telecom_cache.json 停在 09-22，重跑采集也不会被用上）。
#   直读适配器是毫秒级往返，缓存它没有任何收益。
NET_NOCACHE = {"telecom"}
# 页面顶部那行「来源」在各网切换时要跟着变，所以它不能是静态文本（模板里改成由 JS 渲染）
UP_N = 0        # 由 build_html 回填：四网总条数（供 __N__ 占位符）

# 页面里的「查看变更明细」链接指向仓库里的 changes/<日期>.md（网页版可直接看）。
# 与 view_page.py 的 DEFAULT_REPO 同值 —— 两个脚本各有独立入口，不互相 import。
REPO = "starrry365/hebei-tariff-monitor"


def repo_rel(path):
    """把路径换算成「相对**仓库根**」的形式 —— GitHub 链接必须以仓库根做基准。

    🔴 别写成 os.path.relpath(path, BASE)：BASE 是**模块目录**（cloud/tariff），
      那样算出来是 "changes/2026-09-23.md"，少了 cloud/tariff/ 这一层前缀，
      生成的链接指向仓库里不存在的路径、点开就是 GitHub 404
      （2026-09-23 用户报障「查看变更明细 → 也不同」）。

    这里从 BASE 逐级往上找 .git 来定位仓库根：模块将来换到别的子目录也不用改。
    万一找不到 .git（比如脚本被单独拷出来跑），退化成相对 BASE —— 至少不会崩。
    """
    d = BASE
    for _ in range(6):
        if os.path.isdir(os.path.join(d, ".git")):
            return os.path.relpath(path, d).replace(os.sep, "/")
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.relpath(path, BASE).replace(os.sep, "/")

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
                     # ★ 这里**曾有**一个 allProvince 声明（「本网数据不分城市」），
                     #   页面拿它决定要不要出地市那一层。2026-09-24 删掉：
                     #   联通那网的声明是**错的**（它按城市分数据），而页面信声明、
                     #   不信数据 ⇒ 一个错的声明就能把整个维度藏掉，且毫无提示。
                     #   现在页面按「本网有没有 d.cty」自己判（数据驱动，见 renderDims）。
                     #   教训：**声明与现实脱节时不会有任何报警**，能不用就别用。
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


# ── 流量「数值 + 单位」与月费的归一（构建期，四网共用）──────────────────────
# ★ 目的：让**行数据**里的 `du` 只可能是 ``GB`` / ``MB`` / ``TB`` / ``''``（空＝无流量），
#   `f` 只可能是数字串或空。这样「流量」筛选、「每元流量」排序、CSV 导出三处口径
#   必然一致 —— 判据只此一份，页面只读不算（与 cat / chx / cty 同一条原则）。
# ★ 为什么必须做：上游写法不统一，不归一会产生两类**静默**后果：
#     ① 真缩写认不出：联通写 ``M`` / ``T``（``100M`` 就是 100MB）⇒ 归一前 gb() 返回
#        None ⇒ 该条在「流量」列显示「—」、在「每元流量」排序里**凭空消失**，
#        看着像「上游没填流量」。实测 4 条（100/200/500M + 1T）。
#     ② 占位符与脏值混进单位列：``0`` / ``0GB`` / ``不涉及`` / ``无`` / ``M/B`` ⇒
#        体检就没法用「单位白名单」兜住取值域，上游新增一个单位也没人发现。
# 🔴 归一只在**单位认不出**或**数值为 0** 时动手，绝不碰正常数据：
#    ``d='0' du='GB'``（四网合计 9556 条）是上游表达「本套餐不含流量」的标准写法，
#    gb() 算得 0.0、页面显示「—」、体检本来就过 —— 归一它属于无事生非。
UNIT_ALIAS = {"M": "MB", "MB": "MB", "M/B": "MB",     # 联通写 M；M/B 是脏值
              "G": "GB", "GB": "GB", "GB起": "GB",     # 「GB起」＝至少 GB 档
              "T": "TB", "TB": "TB"}
FEE_NONE = frozenset(("无", "不涉及", "没有", "否", "-", "－", "/", "—"))


def du_norm(data, unit):
    """``(data, dataUnit)`` → ``(数值, 单位)``；单位只可能是 ``GB`` / ``MB`` / ``TB`` / ``''``。

    返回 ``('', '')`` 表示「本套餐不含流量」—— 此时 ``gb()`` 返回 None，页面显示「—」。
    """
    u = str(unit or "").strip().upper()
    d = str(data if data is not None else "").strip()
    if not d:
        return "", ""
    canon = UNIT_ALIAS.get(u)
    if canon:
        return d, canon
    # 单位认不出（占位符 / 脏值 / 空）：只有**数值确实是 0** 时才当「无流量」收起。
    # 数值非 0 又认不出单位 ⇒ 原样留着，让体检的「单位白名单」把它报出来
    #（上游新增单位必须人工过目，这里不能默默吞掉）。
    try:
        if float(d) == 0:
            return "", ""
    except ValueError:
        pass
    return d, u


def fee_norm(v):
    """月费字段归一：上游把「本套餐不收月费」写成 ``无`` / ``不涉及`` ⇒ 归一成空。

    ★ 页面 ``num()`` 对 ``无`` 与空都返回 null（``parseFloat('无')`` 是 NaN），
      所以这里**不改变任何显示与排序**，只是让行数据的取值域干净、
      体检能用「数字串或空」这一条把住。
    """
    s = str(v if v is not None else "").strip()
    return "" if s in FEE_NONE else s


def state_of(code, e, g, base_day):
    """该条目是否「已下架」。

    ★ 只能用**采集侧已有的信号**，不能自己发明判据 —— 四网的「下架」根本不是同一回事：
      · 联通：一级分类 ``99``「停售套餐」。🔴 实测那批 ``endDate`` **一条都没过期**，
        所以**不能靠日期判**（初版注释写的「99.87% 已过期」是当时的样本，现已不成立）——
        它被归到 99 类这件事本身就是唯一下架证据。
      · 广电：服务端状态位 ``stateFlag``（``1`` 在售、``0`` 下架）
      · 电信：该网**没有**独立状态位，只能看下线日是否早于基线日
      · 移动：``isPublic='0'`` 的分类能列出来，但明细接口恒返回 0 条 ⇒ **本网拿不到**
    """
    if code == "unicom":
        return str(g.get("type2")) == "99" or str(e.get("_firstLevel")) == "99"
    if code == "cbn":
        return str(e.get("stateFlag") or "1") != "1"
    if code == "telecom":
        d = str(e.get("offineDay") or "").strip()
        # 8 位日期按字符串比较即是按时间比较；格式不对一律当在售（宁可漏判不可误判）
        return bool(re.fullmatch(r"\d{8}", d)) and d < str(base_day or "").replace("-", "")
    return False


def rows_of(o, diff=None, code=""):
    """把**某一家**的数据源对象构造成页面行。

    移动与联通在这一层是同构的：联通采集时就把字段名映射成了移动那套
    （feesStandard→fees、startDate→onlineDay…，见 probes/he_unicom_tariff.py
    的 FIELD_MAP），所以两网共用这一个函数，页面无需为任何一家分叉。

    ``code``：网代码。只用于地域归属（``sc``）与下架判定（``st``）——
    这两件事四网判据完全不同，必须知道是哪一家。留空时退化为「不做地域过滤、
    不标下架」，这样旧的调用方（若有）不会被改坏。

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

    base_day = data_day(o)
    # 板块是否该落盘：本网该字段真有两个取值才算（见 ATTR_CN 的注释）。
    _attrs = {str(g.get("tariffAttr") or "").strip() for g in o["groups"]}
    sect_on = len(_attrs & set(ATTR_CN)) >= 2
    rows, seen, dropped = [], {}, 0
    for g in o["groups"]:
        # 联通的一级分类号（1..5）与移动 ZFLX 的 1..5 语义一致，直接复用；
        # 采集侧若给了 type2Name 就以它为准（联通的「99 停售套餐」是移动没有的类）。
        raw_l1 = str(g.get("type2Name") or ZFLX.get(str(g.get("type2")), "?") or "").strip()
        attr = g.get("tariffAttr")
        for e in g["entries"]:
            # ══ 联通：把「停售套餐(99)」还原成真实分类 ════════════════════
            # 🔴 实测（2026-09-24，全量 8041 条）：联通把**停售**这件事编码成了一级
            #   栏目 99「停售套餐」，而那批条目的**真实分类写在二级栏目里** ——
            #   二级的取值域 {套餐, 加装包, 营销活动, 港澳台/国际资费, 标准资费}
            #   恰好就是一级栏目那 5 个分类（二级码 1/2/3/4/5 与一级码同号，可自证）。
            #   照字面把它当分类的后果是**静默错归类 3177 条**（= 联通全部已下架条目）：
            #   其中 1220 条其实是加装包、581 条标准资费、303 条营销活动、
            #   42 条港澳台/国际，却全被归成「套餐」；用户按「加装包」筛时
            #   这 1220 条一个都不会出现，而页面上没有任何异常。
            #   ★ 「停售」这个语义**已经由「已下架」页签表达**（构建期 st，
            #     联通判据 = type2 == 99，与这里逐字同源），不必也不该再占一个分类。
            #   ⇒ 还原规则：停售桶取二级栏目当分类、细分留空（它的二级被分类占用了，
            #     本就没有更细的信息 —— 留空是诚实的，编一个出来才是错的）。
            raw_l2 = str(e.get("type3Name") or "").strip()
            if raw_l1 == STOPPED_L1:
                cat_src, sub = (raw_l2 or raw_l1), ""
            elif raw_l2:
                # 上游真有二级栏目（联通在售）⇒ 细分就用它，这才是有信息量的两级。
                cat_src, sub = raw_l1, raw_l2
            else:
                # 本网上游**没有**二级栏目（移动 / 电信 / 广电：条目里连 type3Name
                # 这个键都不存在）⇒ 细分退回一级栏目名，与改动前逐字一致。
                # 🔴 别把「没有二级」当成「二级为空」：那会让这三网的「细分」下拉
                #    整层变成只有一个空档 —— 一个看着还在、实则筛不出东西的控件。
                cat_src, sub = raw_l1, raw_l1
            sc, cty = where_of(code, e) if code else ("hb", [])
            # ★ 需求：只要「河北 + 全国」。与河北无关的（其他省份专属）**丢弃**。
            #   这条过滤用归档快照离线验过：四网现有数据一条都不会被它丢掉（只放过未知网）。
            #   真丢了就要吭声 —— 静默少一批条目在页面上完全看不出来。
            if code and not sc:
                dropped += 1
                continue
            # ★ 地市：**上游字段没有时**，允许从文案里认（仅移动 / 电信，见 CITY_TEXT）。
            #   这一段原先写在页面的 cityTags() 里，2026-09-24 搬到构建期 ——
            #   与 cat / chx / sc / st 同一条原则：归一在构建期做完写进行数据，
            #   页面只读不算。搬过来的实打实收益是**判据只剩一份**：
            #   原先页面与 audit_data 各有一份文本规则，靠 check_sync 比对字符串，
            #   而那段注释自己就写着「页面判据一升级，这里不跟着改，对账就从对得上
            #   滑成永远差一截；又因为平时没人跑，看上去只是噪音」。
            # ★★ 码判定与文案是**互补**而不是**互斥**（2026-09-24 修，此前是
            #   `if not cty`，即「有码就完全不看文案」）：
            #     · 地市**码**是权威的 —— 有码时不该再用文案去猜**别的 4 位码地市**，
            #       否则「唐山爱家光网回馈」会因说明里提到别处而被加上多余的地市；
            #     · 但 `CITY_EXTRA`（雄安新区 / 华北油田）**没有独立地市码**，只能靠文案 ——
            #       互斥写法让这两个档**永远是 0 条专属**（「沧州华油尊享礼包」的码是
            #       3170 沧州，只有名字里带「华油」），而 `text_cities()` 的注释自己
            #       早就写着「『沧州华油尊享礼包』同属沧州与华北油田」。
            #   ⇒ 无码：文案兜全量；有码：只用文案补 CITY_EXTRA。
            if code in CITY_TEXT:
                tb = text_cities(e)
                if not cty:
                    cty = tb
                else:
                    extra = [c for c in tb if c in CITY_EXTRA and c not in cty]
                    if extra:
                        cty = _uniq(list(cty) + extra)

            def s(k, n=400):
                v = e.get(k)
                return "" if v in (None, "None") else str(v).replace("\n", " ")[:n]
            chn = s("channel", 120)
            # ★ 流量「数值 + 单位」先归一（见 du_norm）：上游写法四网各不相同，
            #   不归一就会出现「100M 认不出单位 ⇒ 流量筛选把它漏掉」这类静默错。
            #   `g` 由**归一后**的 (d, du) 算，三者口径必然一致。
            d_n, du_n = du_norm(e.get("data"), e.get("dataUnit"))
            rec = {"n": s("name", 120), "t": s("tariffName", 80),
                   "f": fee_norm(s("fees", 20)), "d": d_n, "du": du_n,
                   "c": s("call", 20), "g": gb(d_n, du_n),
                   "ap": s("applicablePeople", 220), "ch": chn,
                   "o": s("onlineDay", 12), "e": s("offineDay", 12),
                   "ty": sub, "a1": attr, "r": s("reportNo", 30),
                   "x": s("otherContent", 500), "ex": s("extraFees", 200),
                   "vp": s("validPeriod", 200), "bw": s("brandwidth", 40),
                   "sc": sc,
                   # ★ 归一化结果在**构建期**算好写进行数据（而不是让页面查表）：
                   #   ① 页面只读不算，筛选/CSV/统计三处不会各算一遍导致口径漂移；
                   #   ② CI 断言能直接校验取值域（cat 必须落在 CAT_ORDER 内）；
                   #   ③ 探针复用同一份函数，结论与页面必然一致。
                   #   ★★ cat_src 而非 raw_l1 —— 联通停售桶要先按二级栏目还原，
                   #      见本函数开头那段「把停售套餐还原成真实分类」。
                   "cat": type_cat(cat_src), "chx": ch_norm(chn)}
            # ★ 板块：本网该字段真有两档才写（电信那个写死的 "2" 因此不会落盘）。
            if sect_on and str(attr or "").strip() in ATTR_CN:
                rec["sect"] = ATTR_CN[str(attr).strip()]
            # ★ 溯源：**只在归一后与上游原文不同**时才写。
            #   一样的话是纯冗余（联通在售条目的 l1==cat_src、l2==sub，一条都不用写）；
            #   不一样的只有 3177 条停售条目 —— 而那正是最需要能解释
            #   「它凭什么被归到加装包」的地方：展开详情里一眼能看到
            #   「上游栏目：停售套餐 / 加装包」，对账不必回翻快照。
            #   🔴 这也让判据能**独立复算**：探针拿 l1/l2 就能验 cat/ty 还原得对不对，
            #      不用去猜构建逻辑（判据与被判对象共用一份实现＝假安全感）。
            if raw_l1 and raw_l1 != cat_src:
                rec["l1"] = raw_l1
            if raw_l2 and raw_l2 != sub:
                rec["l2"] = raw_l2
            # ★ 归属（个人/政企）：只有移动的条目带 type1，其余三网连键都没有。
            #   与 cty 同策略 —— **有值才写**，免得页面拿到一堆空串还要判断。
            #   页面显隐该维度时看「本网有没有任一条目带 ow」，与地市判据一致。
            ow = OW_CN.get(s("type1", 4))
            if ow:
                rec["ow"] = ow
            # ★ 在售 / 已下架必须在写地市**之前**算出来 —— 地市与「不限地市」
            #   两档都只对在售条目成立（见下）。
            st = bool(code and state_of(code, e, g, base_day))
            # ★ 地市归属只有三种合法形态，**互斥且完备**（audit_data.check_city 会断言）：
            #     · `cty` 有值  = 该资费限这几个市（**中文名**，取值域 = CITY_ORDER ∪ CITY_EXTRA）
            #     · `pw=1`      = 不限地市（全省通用）—— 页面「仅全省通用」档读它
            #     · 两者皆无    = 归属不详（当前只出现在**已下架**条目上）
            #   空列表不落盘：免得页面拿到一堆 `cty: []` 误以为「这些条目属于第 0 个地市」。
            #   ★ 页面据此决定**要不要出地市这一层**（本网一条 cty 都没有 ⇒ 不出），
            #     不再依赖任何「本网不分城市」的声明 —— 那个声明错过一次，代价是
            #     整个维度被藏起来而无人察觉（见 WHERE_OF / net_payload 的注释）。
            # 🔴🔴 已下架条目的地市归属是**不可信**的，刻意不写：它的来源是各城
            #   「停售目录」的差异，而停售目录本质是各城各自的遗留清单 —— 同一条停售
            #   资费被哪些城市收录是**任意**的。实测 139 条「城市专属」里 18 条其实是
            #   9~11 城共有（含 6 条明摆着是「…-河北省版」的省版资费）；把它们算成
            #   「邢台专属」，用户按邢台筛时就会混进一批与邢台无关的条目。
            #   ⇒ 宁可让它们**没有任何地市归属**（只在「全部城市」下可见），
            #     也不要给一个看着正常、实则随机的标签。
            if not st:
                if cty:
                    rec["cty"] = cty
                elif code in CITY_NETS:
                    rec["pw"] = 1
            if st:
                rec["st"] = 1
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
    if dropped:
        log(f"!! {code}：{dropped} 条与河北/全国无关（其他省份专属）已按需求丢弃 —— "
            f"这批条目若正好落在本次 diff 里，变更标注会因条数对不上而整批撤销")
    return _mark_changes(rows, diff)


# 模板占位符 —— 只此一份。
# ★ 漏掉一个占位符的表现是：生成的页面里留着 `__XXX__`，JS 直接 ReferenceError，
#   整页白屏；而且**本地不一定复现**（要么走 build_html、要么走 render_only）。
#   原先是 build_html 与 render_only 各写一遍替换列表 —— 2026-09-22 加 __CAT_ORDER__
#   时就只改了 build_html，render_only 那侧静默漏掉。合并到一处，从结构上消除这种漏。
PLACEHOLDERS = ("__NETS__", "__N__", "__DATE__", "__CAT_ORDER__",
                "__OW_ORDER__", "__CITY_ORDER__", "__CITY_EXTRA__",
                "__HIST__", "__NOTICE__")


def js_json(obj):
    """把对象序列化成能**安全嵌进内联 <script> 块**的 JSON 字面量。

    🔴 为什么不能直接用 ``json.dumps``：它（尤其 ``ensure_ascii=False`` 时）
       **不转义** ``<`` ``>`` ``&``，而这些字符放在内联 ``<script>`` 里是
       HTML 解析器的雷 —— 只要数据里出现 ``</script``，HTML 解析器就在那里
       **提前闭合脚本块**，其后所有 JS 变成普通文本 ⇒ 整页白屏、交互全废，
       而页面看起来只是「打开是空的」，没人会想到是某条资费文案的锅。

       这不是假想风险：上游资费文案**实测带富文本 HTML** —— 移动的「权益说明」
       字段含真正的 ``<p>…</p>``（2026-09-23 实测 19 个 ``<``、11 个 ``</p>``）。
       文案由运营商运营人员填写，出现 ``</script`` 只是时间问题。

       ``\\u003c`` 在 JSON 里与 ``<`` **完全等价**，JS 解析后字符串一模一样，
       所以对页面功能零影响；体积只多几个字节（四网实测 +0.02%）。
    """
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def fill_template(tpl, vals):
    """把模板占位符一次填掉。缺任何一个都**报错**，而不是留下 `__X__` 让页面坏掉。"""
    miss = [p for p in PLACEHOLDERS if p not in tpl]
    if miss:
        raise RuntimeError("模板缺少占位符：%s" % "、".join(miss))
    for p in PLACEHOLDERS:
        tpl = tpl.replace(p, str(vals.get(p, "")))
    return tpl


def build_html(sources, notice="", diffs=None, archive=True):
    """重建查询页（多网）。

    ``sources``：``{网code: 数据源对象}`` —— 目前是 ``{"move": o, "unicom": o}``，
    缺哪家就哪家留空壳。
    ``diffs``：``{网code: {"added": set(键), "changed": set(键)}}``，按网各给一份；
    变更标注必须**按网分别算**（键里带分类号，两网的键空间不通用）。
    ``archive``：是否把结果 gzip 归档进 ``page/index.html.gz``（**入库的官方页面快照**）。
    🔴 本机预览路径（``rebuild_offline.py``）必须传 ``archive=False``：
       它的数据来自**本地采集缓存**（不入库），而归档进 git 的应是「与快照同源」的
       那一份。用本地缓存重建的页面盖掉官方归档，结果是归档与任何一份快照都对不上 ——
       既无法自证，也没人会注意到（2026-09-23 实测：跑一次本机重建，
       ``page/index.html.gz`` 就从 867218 字节变成 862474 字节，静默入库级别的改动）。
    """
    global UP_N
    payloads, total = {}, 0
    for code, o in (sources or {}).items():
        if not o:
            continue
        d = (diffs or {}).get(code)
        rows = rows_of(o, d, code)
        sc_stat, cat_stat, st_n, cty_n, pw_n, unmapped = {}, {}, 0, 0, 0, {}
        for r in rows:
            sc_stat[r.get("sc")] = sc_stat.get(r.get("sc"), 0) + 1
            cat_stat[r.get("cat")] = cat_stat.get(r.get("cat"), 0) + 1
            st_n += 1 if r.get("st") else 0
            cty_n += 1 if r.get("cty") else 0
            pw_n += 1 if r.get("pw") else 0
            if r.get("cat") == "其他":
                unmapped[r.get("ty") or "(空)"] = unmapped.get(r.get("ty") or "(空)", 0) + 1
        # 地市那一格要**成对**打印（限定地市 / 不限地市）：
        # 只报「地市专属 N」看不出「剩下的都是不限地市」，也看不出某天采集侧
        # 把归属整批丢了（那一栏会从「专属 N」直接变成「无地市维度」，看着像正常的）。
        log("   %s：%d 条（%s%s%s）" % (
            code, len(rows),
            " · ".join("%s %d" % (SCOPE_CN.get(k, k or "?"), v)
                       for k, v in sorted(sc_stat.items())),
            (" · 地市专属 %d · 不限地市 %d" % (cty_n, pw_n)) if cty_n else " · 无地市维度",
            " · 已下架 %d" % st_n if st_n else ""))
        # 大类分布按 CAT_ORDER 输出（而不是 most_common）—— 顺序固定才便于逐日比对，
        # 也才能一眼看出「某网这次少了一整类」。
        log("     大类：" + " · ".join("%s %d" % (c, cat_stat[c])
                                     for c in CAT_ORDER if cat_stat.get(c)))
        if unmapped:
            log("     !! 有原始分类没归到大类（落进「其他」）：" + " · ".join(
                "%s×%d" % (k, v) for k, v in sorted(unmapped.items()))
                + " —— 请在 TYPE_CAT 里补映射（CI 会因此硬失败）")
        payloads[code] = {"rows": rows, "src": SRC_OF.get(code, ""),
                          "base": data_day(o, time.strftime("%Y-%m-%d"))}
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
    payload = js_json(net_payload(payloads))
    out = fill_template(html, {
        "__NETS__": payload, "__N__": str(total), "__DATE__": date,
        # 大类顺序（不是数据）注入页面：页面按它生成下拉，顺序才和构建日志一致。
        # 在页面里另写一份常量就会漂移 —— 而漂移的表现是「下拉顺序和日志对不上」，
        # 不报错、只是慢慢让人不敢信。
        "__CAT_ORDER__": js_json(list(CAT_ORDER)),
        # 归属档位顺序（同 CAT_ORDER 的理由：顺序只此一份，页面不另写常量）。
        "__OW_ORDER__": js_json(list(OW_ORDER)),
        # 地市清单（下拉的「地市」组 + 「专属区域」组，也是地市分布图的顺序）。
        # 页面**不再自带**这份清单 —— 一份副本的下场见 audit_data.check_sync：
        # 页面那份是 4 位地市码表，而联通的地市码是 3 位，两套编码并存时
        # 「这个 cty 该查哪张表」这个问题根本没人能答对。
        "__CITY_ORDER__": js_json(list(CITY_ORDER)),
        "__CITY_EXTRA__": js_json(list(CITY_EXTRA)),
        # 变更历史（页面时间线）。走紧凑序列化 —— 它一年年涨，白空格也是体积。
        "__HIST__": js_json(load_history().get("items") or []),
        "__NOTICE__": notice or "本次巡检未检测到变化"})
    # ★ 先写 .new 校验、再原子替换：这个文件坏掉＝全站白屏，而「写到一半」
    #   与「模板改坏」都不报错。校验不过就保留上一版可用页面（见 write_page）。
    ok, why = write_page(out)
    if not ok:
        log(f"!! 页面未替换（{why}）—— 上一版页面继续可用，请检查模板/占位符")
        return 0
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
    if archive:
        archive_page()
    else:
        log("（本机预览模式：不覆盖入库的官方归档 page/index.html.gz）")
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

    # 同 build_html：内联进 <script> 的 JSON 必须走 js_json 转义（防 </script 提前闭合）
    payload = js_json(nets)
    tpl = open(os.path.join(BASE, "template.html"), encoding="utf-8").read()
    try:
        out = fill_template(tpl, {
            "__NETS__": payload, "__N__": str(all_n), "__DATE__": date,
            "__CAT_ORDER__": js_json(list(CAT_ORDER)),
            "__OW_ORDER__": js_json(list(OW_ORDER)),
            "__CITY_ORDER__": js_json(list(CITY_ORDER)),
            "__CITY_EXTRA__": js_json(list(CITY_EXTRA)),
            "__HIST__": js_json(load_history().get("items") or []),
            "__NOTICE__": notice})
    except RuntimeError as e:
        log(f"!! {e}，中止（否则会留下未替换的标记把页面搞坏）")
        return 3
    ok, why = write_page(out)
    if not ok:
        log(f"!! 页面未替换（{why}）—— 上一版页面继续可用")
        return 3
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
    """落一份快照。``day`` 必须是 **8 位紧凑日期**（``20260922``）。

    🔴 这里显式拒收带上横线的 ``2026-09-22`` —— 因为它**不会报错**，只会安静地
    写出一个 ``xxx_2026-09-22.json.gz``：文件名与 ``snap_paths()`` 的日期解析
    对不上，于是 ``load_prev`` / ``load_latest`` / ``prune`` 全都看不见它。
    症状是「快照看着存了，实际等于没存」，而次日巡检会拿更旧的基准比，
    一口气报出成百上千条假变更。这个坑本仓库真踩过（2026-09-22）。
    注意 ``data_day()`` 返的正是带横线的格式，两者**不能直接对接**，要 ``.replace("-", "")``。
    """
    if not re.fullmatch(r"\d{8}", str(day or "")):
        raise ValueError("快照日期必须是 8 位紧凑格式（如 20260922），收到 %r" % (day,))
    p = os.path.join(SNAP, f"{prefix}{day}.json.gz")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0 保证同样内容产生同样字节，避免无意义的二进制 diff
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    # ★ 原子写（.tmp → os.replace）：CI 有 timeout-minutes，进程随时可能被 kill。
    #   直接写目标文件被打断会留下**半个 gz**，下一轮解压失败或 diff 出一整批假变化。
    if G:
        G.atomic_write_bytes(p, buf.getvalue())
    else:
        with open(p, "wb") as f:
            f.write(buf.getvalue())
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
                 net="河北移动", summary=None):
    """把摘要写进 GITHUB_STEP_SUMMARY（Actions 页面上直接可读）。

    ``added`` / ``removed`` / ``changed`` 允许直接给**计数**（护栏路径下拿不到
    键列表，只有数字），也可以用列表 —— 列表时按其长度与内容展开样例。
    ``summary``：``diff_round()`` 的结果，给了就顺带把护栏数字与抑制原因写出来。
    """
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if not sp:
        return
    idx = index_rows(new_o)

    def _n(x):
        return x if isinstance(x, int) else len(x or [])

    # 计数优先取 summary（护栏路径下拿到的就是数字）；
    # 样例只从**真键**里挑 —— 🔴 调用方为省事传过 `list(range(n))` 这种占位列表，
    # 若直接拿它当键去索引 idx 会 KeyError（占位元素是 int），而那会**在写摘要这一步
    # 把整轮巡检打断**：数据已经采完、报告也写了，却因为一行展示逻辑崩掉。
    na = int(summary["added"]) if summary and "added" in summary else _n(added)
    nr = int(summary["removed"]) if summary and "removed" in summary else _n(removed)
    nc = int(summary["changed"]) if summary and "changed" in summary else _n(changed)
    keys = [k for k in (added or []) if k in idx] if isinstance(added, (list, tuple)) else []
    L = [f"## {net}资费巡检 {new_o.get('fetchedAt')}", "",
         f"- 条目总数：**{len(idx)}**"]
    if not has_prev:
        L.append("- 本次为**首版基线**，后续运行才开始检测差异")
    elif summary and summary.get("note"):
        L.append(f"- ⚠️ 本轮**未记入任何变化**：{_NOTE_CN.get(summary['note'], summary['note'])}")
    else:
        L.append(f"- 新增 **{na}** · 下线 **{nr}** · 字段变更 **{nc}**")
        if not (na or nr or nc):
            L.append("- ✅ 未检测到变化")
        if summary:
            g = [f"补录 {summary.get('restored', 0)}",
                 f"假下架抑制 {summary.get('fake_removed', 0)}",
                 f"身份漂移合并 {summary.get('relocated', 0)}",
                 f"在售↔停售迁移 {summary.get('state_moved', 0)}"]
            if any(summary.get(k) for k in ("restored", "fake_removed",
                                            "relocated", "state_moved")):
                L.append("- 🛡 护栏：" + " · ".join(g))
        for k in keys[:15]:
            L.append(f"  - 🆕 **{idx[k]['_name'] or idx[k]['_tname']}** 〔{idx[k]['_ty']}〕{brief(idx[k])}")
        if na > len(keys[:15]):
            L.append(f"  - …另有 {na - len(keys[:15])} 条新增")
    L.append(f"\n完整报告：`{os.path.relpath(report_path, BASE)}`")
    with open(sp, "a", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


_NOTE_CN = {"degraded": "数据量异常，已冻结上一版（等下一轮复采）",
            "rebound": "检测到基线回弹，本轮不计入变化",
            "schema": "字段结构变更，仅重建基线、不通知",
            "baseline": "首版基线建立",
            "resync": "连续偏低后重同步基线",
            "collect-error": "本轮采集失败，沿用上一版快照",
            # 🔴 2026-09-26 补：漏了这一项时，下面的 .get(note, note) 兜底会
            #    把英文码 `snapshot-fallback` 原样印到中文界面上 —— 静默的英文泄漏。
            #    凡是系统**能产出**的 note，这里必须都有对照（有自测盯着，见
            #    selftest_pipeline.py 的「_NOTE_CN 覆盖全部 note 取值」）。
            "snapshot-fallback": "本轮未采到，沿用仓库快照渲染"}


def net_round(code, today, fallback=None):
    """跑一遍「非移动」的某一网：采集 → 降级自检 → 快照 → 变更检测 → 报告。

    与移动共用同一套机制（同 diff_round / index_rows / write_report），
    只有快照前缀与报告文件名不同 —— 各网必须各存各的，
    否则 load_prev 按文件名排序取「最近一份」时会把这一网的当成那一网的基准。

    ★ 任何一网失败**不致命**：抓不到就沿用上一版快照继续渲染，绝不因此让移动那网
      （或别的网）也出不了页面。返回 (数据, diff 或 None, summary 或 None)。

    ★ 收敛成一个函数而不是「联通一份、广电一份」：三网的流程逐字相同，
      只有模块名/显示名/文件名三处不同（都在 NET_RUN 里）。复制一份就等于
      把「降级保护」「沿用上次快照」「基线不覆盖」这些约束各写两遍 ——
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
        data = (mod.fetch_all(include_stopped=True) if code in NET_STOPPED
                else mod.fetch_all())
    except (Exception, SystemExit) as e:
        # SystemExit 也要接：ct_monitor.fetch_all() 在缺少 .ct_raw.json 时
        # 刻意用 SystemExit 抛出一段给**人看**的采集指引（本机直接跑时体验好）。
        # 只接 Exception 的话，一次「本轮没采到电信」会直接把整个巡检打断，
        # 连移动/联通/广电的页面都出不来 —— 正是这里最不该发生的事。
        log(f"!! {cn}采集异常，本轮沿用上次：{type(e).__name__}: {e}")
        d, _ = load(today, prefix)
        return d, None, _summary_skip(code, cn, d, "collect-error")
    old_o, prev_p = load_prev(today, prefix)
    if not data:
        log(f"!! {cn}采集失败，本轮沿用上次快照")
        return old_o, None, _summary_skip(code, cn, old_o, "collect-error")

    n = len(data.get("entries") or [])
    n_old = len((old_o or {}).get("entries") or [])
    if n == 0:
        log(f"!! {cn}抓到 0 条，本轮不写快照、沿用上次")
        rp, _ = _hold(code, cn, tag, data, old_o, prev_p, today,
                      "本轮抓到 0 条（采集整体失败）")
        return old_o, None, _summary_skip(code, cn, old_o, "degraded", report=rp)
    # ⑤ 降级状态机：单轮偏低先冻结，连续 N 轮才接受新数据
    act, why = _degrade(code, cn, n, n_old)
    if act == "hold":
        log(f"!! {cn}数据量异常 {n_old} -> {n}（<{DEGRADE_RATIO:.0%}），{why}，"
            f"本轮不写快照、沿用上次")
        rp, _ = _hold(code, cn, tag, data, old_o, prev_p, today,
                      f"本轮 {n} 条 < 上一版 {n_old} 条的 {DEGRADE_RATIO:.0%}，{why}")
        return old_o, None, _summary_skip(code, cn, old_o, "degraded", report=rp)
    if act == "accept":
        log(f"!! {cn}{why}（{n_old} -> {n}）—— 本轮按**重建基线**处理，不报变更")
    elif why:
        log(f"-- {cn}数据量已恢复正常（{n} 条）")

    save_snapshot(data, today, prefix)
    prune_snapshots(prefix)
    day = f"{today[:4]}-{today[4:6]}-{today[6:]}"
    rp = os.path.join(CHG, f"{tag}-{day}.md")

    if old_o is None or act == "accept":
        # 首版基线，或降级认账后的重同步：**只重建基线，不出变更报告**
        log(f"{cn}{'无历史快照' if old_o is None else '重同步'}，本次为首版基线（{n} 条）")
        if G:
            G.atomic_write_text(
                rp, f"# {cn}资费基线 · {data['fetchedAt']}\n\n"
                    f"- {'首版基线快照' if old_o is None else '数据量连续偏低后重同步'}"
                    f"，共 **{n}** 条\n- 来源：{SRC_OF[code]}\n", newline="\n")
        elif not os.path.exists(rp):
            with open(rp, "w", encoding="utf-8") as f:
                f.write(f"# {cn}资费基线 · {data['fetchedAt']}\n\n"
                        f"- 首版基线快照，共 **{n}** 条\n- 来源：{SRC_OF[code]}\n")
        hist_append({"ts": str(data.get("fetchedAt") or "")[:19], "d": day,
                     "code": code, "net": cn, "n": n, "a": 0, "r": 0, "c": 0,
                     "note": "baseline" if old_o is None else "resync", "smp": []})
        _clear_degrade(code)
        emit_summary(data, [], [], [], rp, False, net=cn)
        return data, None, {"code": code, "net": cn, "n": n, "added": 0, "removed": 0,
                            "changed": 0, "restored": 0, "fake_removed": 0,
                            "relocated": 0, "samples": [], "report": rp,
                            "note": "baseline" if old_o is None else "resync", "guard": None}

    sm = diff_round(code, cn, tag, data, old_o, prev_p, today, fname=f"{tag}-{day}.md")
    log(f"{cn}对比：新增 {sm['added']} 下线 {sm['removed']} 变更 {sm['changed']}"
        + (f" · 护栏：补录 {sm['restored']} / 假下架 {sm['fake_removed']} / "
           f"漂移 {sm['relocated']}" if (sm["restored"] or sm["fake_removed"]
                                         or sm["relocated"]) else ""))
    emit_summary(data, sm["_added_keys"], sm["_removed_keys"], sm["_changed_keys"],
                 sm["report"], True, net=cn, summary=sm)
    if sm["note"]:
        return data, None, sm
    return data, {"added": set(sm["_added_keys"]), "changed": set(sm["_changed_keys"])}, sm


def _clear_degrade(code):
    """某网重建基线后把它的降级计数清零（否则「上一轮的偏低」会一直挂账）。"""
    if not G:
        return
    st = G.read_json(DEGRADE_STATE, {}) or {}
    if code in st:
        st[code] = {"rounds": 0, "cleared": time.strftime("%Y-%m-%d %H:%M:%S")}
        G.write_json(DEGRADE_STATE, st)


def _summary_skip(code, cn, data, note, report=""):
    """本轮没有变更可报（采集失败 / 降级冻结）时的 summary 壳。"""
    return {"code": code, "net": cn,
            "n": len((data or {}).get("entries") or []),
            "added": 0, "removed": 0, "changed": 0, "restored": 0,
            "fake_removed": 0, "relocated": 0, "state_moved": 0, "samples": [],
            "report": report, "note": note, "guard": None}


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

    返回 ``(sources, diffs, tails, summaries)``：
      ``sources`` = {网code: 数据源对象}（失败的那网是上一版快照，可能没有）
      ``diffs``   = {网code: {"added": set, "changed": set} 或 None}
      ``tails``   = [" · 联通新增 1 / 变更 0", " · 广电无变化"] 供页面顶部提示拼接
      ``summaries`` = [diff_round 的 summary] —— 推送只在 main() 末尾汇总发一次，
                      所以各网的核验结果必须**带出来**（在网内部发就等于一网一条，
                      用户会在第一天把这个通道静音）。

    ★ 新增一网时**只需要动 NET_RUN**：main() 里不再逐个写 uni/cbn 变量。
      原来 main() 里两处（首版基线分支、常规分支）各写一遍联通的调用与提示拼接，
      接第三网就得改四处 —— 漏一处就是「某网采了但页面没提示 / 提示错字」。

    ★ 2026-09-22 追加 NET_SNAP（兜底名单，目前只有电信）：
      电信是「机会性采集」—— 云端有真实 Chrome 时采得到，采不到就退回仓库快照。
      两条路都从这里进，**不能只改 main()**：进页面有两条路（首版基线分支 &
      常规分支）都调本函数，只补一处，另一处就会静默少一网。
    """
    # 🔴 2026-09-26 修：这一行原本漏了 summaries，而函数体里三处 append 它 ——
    #    Python 按**全局名**去查，直接 NameError。它在 CI 上炸了第 8 步（整轮巡检中断、
    #    后面 12 步全 skip），而**本机所有自测都是绿的**：因为那些自测单独调 net_round /
    #    diff_round，**没有一个走到 other_nets 这个集成缝**。
    #    教训：函数级测试全过 ≠ 集成路径通。已加静态检查闸门（见 CI 的 ruff F821/F823）。
    sources, diffs, tails, summaries = {}, {}, [], []
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
        data, dd, sm = net_round(code, today, fallback=fb)
        sources[code] = data
        diffs[code] = dd
        if sm:
            summaries.append(sm)
        cn = sh_of.get(code, code)
        if dd:
            if dd["added"] or dd["changed"]:
                tails.append(f' · {cn}新增 {len(dd["added"])} / 变更 {len(dd["changed"])}')
            else:
                tails.append(f" · {cn}无变化")
        elif sm and sm.get("note"):
            # 采集失败 / 降级冻结 / 回弹 / 结构变更 —— 这几种都**没有变更可报**，
            # 但绝不能显示成「无变化」：那是「看到了，没变」，
            # 而这些是「没看到」或「看到了但不敢信」，两者对读者的含义完全不同。
            tails.append(f" · {cn}{_NOTE_CN.get(sm['note'], sm['note'])}")
            if sm.get("note") == "degraded":
                print(f"::warning title={cn}本轮数据量异常::本轮 {sm['n']} 条，"
                      f"已冻结上一版数据、未记入变更", flush=True)

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
        summaries.append({"code": code, "net": cn, "n": len(o.get("entries") or []),
                          "added": 0, "removed": 0, "changed": 0, "restored": 0,
                          "fake_removed": 0, "relocated": 0, "state_moved": 0,
                          "samples": [], "report": "", "note": "snapshot-fallback",
                          "guard": None})
        log(f"{cn}由快照渲染：{os.path.basename(p)}（{len(o.get('entries') or [])} 条）")
    return sources, diffs, tails, summaries


def main():
    argv = sys.argv[1:]
    no_html = "--no-html" in argv
    no_notify = "--no-notify" in argv
    if "--render-only" in argv:
        return render_only()
    today = time.strftime("%Y%m%d")
    day10 = f"{today[:4]}-{today[4:6]}-{today[6:]}"

    data = fetch_all()
    if data is None:
        log("!! 抓取失败：分类列表拿不到")
        return 3
    n = sum(len(g["entries"]) for g in data["groups"])

    old_o, prev_p = load_prev(today)
    n_old = sum(len(g["entries"]) for g in (old_o or {}).get("groups") or [])

    if n == 0:
        log("!! 抓到 0 条，判为异常")
        return 2
    # ⑤ 降级状态机（与其余三网共用同一条判据与同一份计数）
    act, why = _degrade("move", "河北移动", n, n_old)
    if act == "hold":
        log(f"!! 数据量骤降 {n_old} -> {n}（<{DEGRADE_RATIO:.0%}），{why}，"
            f"本轮不写快照、不出报告，页面沿用上一版")
        _hold("move", "河北移动", "", data, old_o, prev_p, today,
              f"本轮 {n} 条 < 上一版 {n_old} 条的 {DEGRADE_RATIO:.0%}，{why}",
              fname=f"{day10}.md")
        # ⚠️ 这里**不再** return 2（老行为）。
        #   老代码在这一步直接失败退出 ⇒ 后面所有步骤（其余三网采集 / 页面重建 /
        #   部署）全部 skipped，页面**停在上一版且不留任何说明**。
        #   现在的行为是：页面照旧出（数据来自上一版快照），提示语里写明
        #   「本轮数据量异常，已冻结上一版」，并把 ::warning 打到 Actions 上。
        #   想恢复「硬失败」的老行为：环境变量 DEGRADE_STRICT=1。
        if os.getenv("DEGRADE_STRICT", "").strip() == "1":
            return 2
    elif act == "accept":
        log(f"!! {why}（{n_old} -> {n}）—— 本轮按**重建基线**处理，不报变更")
    elif why:
        log(f"-- 数据量已恢复正常（{n} 条）")

    frozen = (act == "hold")
    if not frozen:
        save_snapshot(data, today)
        prune_snapshots()

    if old_o is None or act == "accept" or frozen:
        if frozen:
            move_sm = _summary_skip("move", "河北移动", old_o, "degraded")
            move_sm["report"] = os.path.join(CHG, f"{day10}.md")
            rp = move_sm["report"]
            notice = (f"⚠️ 本轮数据量异常（{n} 条 < 上一版 {n_old} 条），"
                      f"已冻结上一版数据、未记入变更")
            has_prev = True
        else:
            log(f"无历史快照，本次为首版基线（{n} 条）"
                if old_o is None else "数据量连续偏低后重同步，重建基线")
            rp = os.path.join(CHG, f"{day10}.md")
            move_sm = {"code": "move", "net": "河北移动",
                       "n": n if old_o is None else n_old, "added": 0, "removed": 0,
                       "changed": 0, "restored": 0, "fake_removed": 0, "relocated": 0,
                       "state_moved": 0, "samples": [], "report": rp,
                       "note": "baseline" if old_o is None else "resync", "guard": None}
            # 双保险：基线文字不覆盖已存在的当天报告。
            # load_prev() 已经保证「当天有快照就不会走到这里」，但万一快照被删/损坏，
            # 也不能把一份真实的变更报告换成一句「首版基线」。
            if os.path.exists(rp) and old_o is None:
                log(f"当天报告已存在，保留不覆盖：{os.path.relpath(rp, BASE)}")
            else:
                txt = (f"# 河北移动资费基线 · {data['fetchedAt']}\n\n"
                       f"- {'首版基线快照' if old_o is None else '数据量连续偏低后重同步'}"
                       f"，共 **{n}** 条\n"
                       f"- 来源：中国移动 APP「资费专区」（nrapigate / nrtariff）\n")
                if G:
                    G.atomic_write_text(rp, txt, newline="\n")
                else:
                    with open(rp, "w", encoding="utf-8") as f:
                        f.write(txt)
            if old_o is None:
                hist_append({"ts": str(data.get("fetchedAt") or "")[:19], "d": day10,
                             "code": "move", "net": "河北移动",
                             "n": n, "a": 0, "r": 0, "c": 0, "note": "baseline",
                             "smp": []})
            notice = (f"首版基线建立（{n} 条），自次日起开始检测资费上下线变更"
                      if old_o is None else f"数据量连续偏低后重同步基线（{n} 条）")
            has_prev = False
        _clear_degrade("move")
        emit_summary(data, [], [], [], rp, has_prev, summary=move_sm)
        move_diffs = None
    else:
        move_sm = diff_round("move", "河北移动", "", data, old_o, prev_p, today,
                             fname=f"{day10}.md")
        rp = move_sm["report"]
        log(f"对比 {os.path.basename(prev_p or '')}：新增 {move_sm['added']} "
            f"下线 {move_sm['removed']} 变更 {move_sm['changed']}"
            + (f" · 护栏：补录 {move_sm['restored']} / 假下架 {move_sm['fake_removed']} "
               f"/ 漂移 {move_sm['relocated']}"
               if (move_sm["restored"] or move_sm["fake_removed"]
                   or move_sm["relocated"]) else ""))
        _clear_degrade("move")
        if G:
            G.write_json(STATE_FILE, {"ts": data["fetchedAt"], "n": n, "prev_n": n_old,
                                      "added": move_sm["added"],
                                      "removed": move_sm["removed"],
                                      "changed": move_sm["changed"],
                                      "restored": move_sm["restored"],
                                      "fake_removed": move_sm["fake_removed"],
                                      "relocated": move_sm["relocated"],
                                      "note": move_sm["note"],
                                      "report": os.path.relpath(rp, BASE),
                                      "prev": os.path.basename(prev_p or "")})
        else:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": data["fetchedAt"], "n": n, "prev_n": n_old,
                           "added": move_sm["added"], "removed": move_sm["removed"],
                           "changed": move_sm["changed"],
                           "report": os.path.relpath(rp, BASE),
                           "prev": os.path.basename(prev_p or "")},
                          f, ensure_ascii=False, indent=1)
        emit_summary(data, move_sm.get("_added_keys") or [],
                     move_sm.get("_removed_keys") or [],
                     move_sm.get("_changed_keys") or [],
                     rp, True, summary=move_sm)
        # move_sm 里的键集合是给页面标注用的（ca/ck）；护栏若判回弹则为空
        move_diffs = None if move_sm["note"] else {
            "added": set(move_sm["_added_keys"]),
            "changed": set(move_sm["_changed_keys"])}
        # 页面顶部提示：三个数字 + 「查看变更明细」直达链接。
        # 「变更了多少条」是一句话能说完的，「哪几条、变了什么」说不完 ——
        # 明细细在 changes/<日期>.md 里，别让用户自己翻仓库找当天那份。
        rel = repo_rel(rp)
        tail = (f' · <a href="https://github.com/{REPO}/blob/main/{rel}"'
                f' target="_blank" rel="noopener">查看变更明细 →</a>')
        notice = ((f"本次巡检：新增 {move_sm['added']} 条 · 下线 {move_sm['removed']} 条"
                   f" · 字段变更 {move_sm['changed']} 条" + tail)
                  if (move_sm["added"] or move_sm["removed"] or move_sm["changed"])
                  else ("本次巡检未检测到任何变化" + tail))
        if move_sm["relocated"] or move_sm["fake_removed"] or move_sm["restored"]:
            notice += (f" · 🛡 护栏另摘除 {move_sm['relocated'] + move_sm['fake_removed']}"
                       f" 条假变化、识别 {move_sm['restored']} 条补录")

    # ── 其余三网 + 页面 ────────────────────────────────────────────────
    # 页面每次都重建（本地那份必须是当天最新），但归档只在内容真变了时才写。
    # 「页面显示的时间停住」曾是个坑：所以页面显示的是「数据基线日期」而非抓取时刻，
    # 停住＝数据确实没变，语义正确。想知道巡检有没有在跑，看 state.json（view_page 会读）。
    summaries = [move_sm]
    if not no_html:
        extra, xdiff, tails, xsum = other_nets(today)
        summaries += xsum
        notice += "".join(tails)
        diffs = {"move": move_diffs} if move_diffs else {}
        diffs.update(xdiff)
        # 「结构变更 ⇒ 重建基线不通知」的收尾：结构变了但页面照常重建
        # （页面读的是数据，不是 diff），只是不把它当成一次「变化」推出去。
        build_html(dict({"move": data}, **extra), notice, diffs)
    else:
        # --no-html 也要跑其余三网：不然「另三网的快照与报告」会缺失，
        # 而 --no-html 的语义只是「不重建页面」，不是「只跑移动」。
        _, _, _, xsum = other_nets(today)
        summaries += xsum

    log(f"变更报告 {os.path.relpath(rp, BASE)}")

    # ── 推送：一次巡检只发一条 ────────────────────────────────────────
    # 放在最末尾：前面所有步骤都不该因为推送而改变结果（推送失败也不影响巡检）。
    if not no_notify and NOTIFY:
        try:
            NOTIFY.notify_change(day10, summaries,
                                 extra_notes=[n for n in _verify_notes(summaries)],
                                 repo=REPO)
        except Exception as e:
            log(f"!! 推送异常（不影响巡检结果）：{type(e).__name__}: {e}")
    elif _N_ERR:
        log(f"-- 推送模块不可用：{_N_ERR}")
    return 0


def _verify_notes(summaries):
    """给推送正文补几句「为什么数字是这样」的说明。"""
    out = []
    for s in summaries or []:
        if s.get("note") and s["note"] not in ("baseline", "snapshot-fallback"):
            out.append(f"{s['net']}：{_NOTE_CN.get(s['note'], s['note'])}")
    return out


if __name__ == "__main__":
    sys.exit(main())
