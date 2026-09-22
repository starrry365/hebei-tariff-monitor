# hebei-tariff-monitor —— 河北四网资费每日巡检 + 在线查询页

> 🟢 **在线查询页：<https://starrry365.github.io/hebei-tariff-monitor/>**
>
> 每天北京时间 06:00 由 GitHub Actions 自动抓取移动 / 联通 / 电信 / 广电四网资费，
> 比对上下线变更，并把最新查询页重新部署到上面这个地址 —— **打开就是最新数据，不用手动更新**。

拆自 [starrry365/hebei-mobile-h5-reverse](https://github.com/starrry365/hebei-mobile-h5-reverse)
的 §8「资费专区」模块。独立成库的原因：一是要**公开访问页面**（原仓库私有），
二是资费巡检有自己稳定的每日提交历史，和活动逆向的噪声混在一起不好查。

**四网合计约 13,000 条**：移动 3887（在售）· 联通 7899（在售 4807 + 已下架 3092）·
电信 884（在售）· 广电 324（在售 208 + 已下架 116）。

**只看「河北邢台 + 河北 + 全国」**：每行都带地域标签（邢台 / 河北 / 全国），
与河北无关的其他省份条目已剔除；**已下架资费单独一个页签**，不与在售混列。
两份口径的判据逐网不同、且都只用上游给的信号，详见
[§1 的「地域口径与『已下架』」](#地域口径与已下架)。

---

## 1. 四网一览

| 项 | 移动 | 联通 | 电信 | 广电 |
|---|---|---|---|---|
| 入口 | APP「资费专区」 | APP「资费专区」 | APP「资费专区」 | 🟢 **公开 H5 公示页**（App 反而抓不到） |
| 域名 | `nrapigate` 网关（`nrtariff` 分组） | `mxx.client.10010.com` | `www.189.cn/wapportalweb` | `m.10099.com.cn/contact-web` |
| 鉴权 | **零凭证**：无 token、无签名 | **零凭证**：只需 `Content-Type` + `Referer` + `Origin` + `x-requested-with` | **零凭证**（`ticket` 传空串即可），但要过 JS 挑战 | **零凭证**；`access` 头带不带都行 |
| 请求体 | AES-256-CBC 密文（网关密钥） | 明文 JSON | 🔴 **AES-128-ECB 密文**（base64 整段当包体） | 明文 JSON |
| 响应 | `{"body":"<密文>"}`，本地解密 | 明文 JSON（`code=0000`） | 明文 JSON（`code=W_0000`） | 明文 JSON（`status=000000`） |
| 价费单位 | 元 | 元 | 元 | 🔴 **分**（`1000` = 10 元），归一化必须 `/100` |
| 性质 | 纯只读 | 纯只读 | 纯只读 | 纯只读 |

### 三家都别把「参数能传」当「数据按它分」

三个反例、三种方向：

- **移动** —— `isPublic=1` 只用它筛在售口径；
- **联通** —— `cityId` **传了但数据不分城市**：12 城 `indexData` 的 (一级,二级) 骨架
  签名去重后**仅 1 种**，明细 24 个字段里**没有任何地域字段**；JS 里另一族
  `/queryTariff/*` 在河北侧 5 种调用方式全部 9s 超时（同刻老接口 `queryTariffNew/threeLevelName`
  0.1s 正常返回）⇒ 该族**不被路由**。⇒ 页面**不出地市这一层**（隐藏，不是置灰）；
- **广电** —— `type1` **必填但值被忽略**（传 `GZ`/`ZQ`/`1`/`2` 返回条数完全相同），
  真正分数据的是 `applicableArea`：`ZZZZ`（全国）与 `HB00`（河北）的条目
  **交集为 0** ⇒ **两份都要采**，少采一份就少一半。区域码表只有「省/直辖市」粒度
  （传地市码回 `BASE102 区域编码无效`）⇒ 同样**不出地市这一层**。
- **移动（政企）** —— `isPublic='0'` 会多列出 7~9 个分类组合，看着像「能拿到非公开/下架口径」，
  但明细接口 `getTariffListInfo` 在 `linkScn=1/2/3` 下**恒返回 `total=0, beans=0`**。
  参数能传 ≠ 拿得到数据。

### 地域口径（河北省 / 全国 + 地市）与「已下架」

**地域**：每行两个标 —— `sc`（省级：`hb` 河北 / `cn` 全国）与 `cty`（**条目级地市码**，
空 = 全省通用）。页面据此筛选，与河北无关的（其他省份专属）条目直接剔除。
四网判据**完全不同**（`tariff_monitor.WHERE_OF`），别想找一套通用规则：

| 网 | 河北 | 全国 | 地市 | 判据 |
|---|---|---|---|---|
| 移动 | 2438 | 1449 | **481 条（12 市全有）** | `applicableArea`/`city`/`province` **三个**字段联合判：`HE`=河北、`000`=全国、4 位地市码（`3190`=邢台…）、多省 CSV=全国、`!XX`=排除式（等效全国） |
| 电信 | 884 | — | **31 条（12 市全有）** | `applicableArea`（`HE` / 地市码 CSV，归一化时特意保留为 `_areaCodes`） |
| 联通 | 7899 | — | **无** | 条目里**没有任何**地域字段 ⇒ 一律河北 |
| 广电 | 131 | 193 | **无** | `_areaNames` 只有「全国 / 河北省」两档 |

**地市**取条目自带的 4 位地市码（481 / 31 是**条目数**，一条可覆盖多市 CSV，
码命中次数分别是 1974 / 265）。**联通与广电上游没有地市级数据**（证据见上），
所以这两网的页面**不出地市这一层** —— 结构与移动/电信不同，是刻意如此。
「—」= 该网公开渠道**没有这一档**，页面上对应的筛选项会**置灰**，而不是让你选完
得到「0 条」—— 后者是拿一个不存在的维度在问，答案没有意义。

🔴 **别用名称文本猜地名**（初版做法）：名称没写地市、地市只写在「目标客户」里的条目
会整批漏判。现在**上游地市码优先**，只有条目没有码时才退回文本兜底
（用来捞「雄安新区 / 华北油田」这类没有码的特殊区域）。成按原始字段判之后，
移动 3887 条**无一落入「无关」桶**。

### 政企资费：四网公开渠道都取不到

需求里有「政企资费」这一层，但**四网公开接口全部取不到**（2026-09-22 实测）：

| 网 | 政企入口 | 结果 |
|---|---|---|
| 移动 | `isPublic="0"` | 能列 25 组分类，**每组明细 `total` 恒为 0**；旁路 path 回 `40401 service not exist` |
| 联通 | 一级分类只有 1..5 + 99 | **无政企类**；旁路接口族不被路由 |
| 电信 | 分类树 5 个一级 | **无政企**；functionCode 只有 `tariffSectionHome`/`tariffSectionQuery` |
| 广电 | `ZQ 政企` 节点存在 | **`childTariffTypes` 长度 0（空壳）**，且 `type1` 被服务端忽略 |

政企**门户**也不公开：`qy.10010.com` = nginx 空壳（真实 Chrome 打开即「Welcome to nginx!」）、
`qy.189.cn` = 403、`qy.10086.cn` = 内部运营后台；`qy.10099.com.cn` DNS 不存在。
这与行业事实一致 —— **政企资费按客户协议报备，不进公开资费公示**。

唯一可用的政企信号是条目「目标客户」(`applicablePeople`) 文本：纯政企
移动 2 / 联通 18 / 电信 0 / 广电 0，「均可办理」电信 44 / 联通 4。
**95%+ 条目根本没写客户类型** ⇒ 用它撑不起「个人/政企」两大层。
**当前决定：不做政企层**（结构从「河北省/全国 → 地市 → 分类」走）。

> 地市与政企这两条的**完整推导**（可达性矩阵、联通 4 条独立证据、广电 BASE102、
> 政企门户实测、文本分档表）见 `docs/政企与地市-深挖结论-20260922.md`；
> 结论都能用 `probes/probe_city_matrix.py` / `probe_upstream_limits.py` / `probe_zq_text.py` 复跑。

**已下架**：单独一个页签。判据同样逐网不同，且只用上游给的信号
（`tariff_monitor.state_of`）：

| 网 | 判据 | 条数 |
|---|---|---|
| 联通 | 归入一级分类 `99`「停售套餐」 | 3092 |
| 广电 | 服务端 `stateFlag != 1` | 116 |
| 电信 | 下线日早于数据基线日 | 0（当前） |
| 移动 | 🔴 **拿不到**（见上一条 `isPublic=0`） | 0 |

🔴 联通那批**不能靠下线日判**：老说明称「3879 条里 3874 条 `endDate` 已过期」，
2026-09-22 实测过期条数为 **0**。它被归到 `99` 类才是唯一下架证据。

`probes/probe_scope_state.py` 是上面两套判据的**离线回归探针** —— 判错了不会报错，
只会静默多/少一批条目，所以改完判据务必跑一遍看分布有没有突变。

### 移动那台的 TLS 老坑（另三网都没有）

这台服务器不支持 RFC5746 安全重协商，Python 的 OpenSSL 3.x 必须显式开
`ssl.OP_LEGACY_SERVER_CONNECT`，否则 `SSLV3_ALERT_HANDSHAKE_FAILURE`。

🔴 **本机 `curl` 走 Schannel 会掩盖它** —— `curl` 能通不代表 Python 能通，
别拿「本地能跑」当证据。另三网都不需要这个开关。

---

## 2. 电信那网：加密破解

电信走 APP 接口（不是公开公示页）：

```
POST https://www.189.cn/wapportalweb/wapportalweb/tariffSection.do
Content-Type: application/x-www-form-urlencoded   ← 但整段包体就是 base64 密文，没有 k=v
x-requested-with: com.ct.client
x-qd-reqtime:     <毫秒时间戳>
Origin/Referer:   https://www.189.cn/wapportalweb/rateZone/index.html
User-Agent:       CtClient;13.4.0;Android;16;23113RKC6C

包体 = Base64(AES-128-ECB-PKCS7(UTF-8(JSON)))
响应 = {"headerInfo":{"code":"W_0000","message":"成功"},"responseContent":{…}}   ← 明文
```

> ⚠️ URL 路径里 `wapportalweb` **重复两遍**，不是笔误。

🔴 **密钥是明文硬编码在一份公开 JS 里的**，两类资产各自的作用：

| 资产 | 作用 |
|---|---|
| `/client/wap/common/js/aes.js` | CryptoJS UMD，**同时打包了 `mode-ecb` 与 `pad-pkcs7`** |
| `/client/wapportalweb/vue_common/js/conscript.js` | obfuscator 混淆；解开 `\xNN` 即见 `Utf8.parse(key \|\| 'telecom_wap_2018')` |

⇒ **算法 AES-128-ECB + PKCS7，密钥 `telecom_wap_2018`（16 字节），无 IV。**
⇒ 这两份 JS **直连就能取**（412 只拦 `rateZone/index.html` 本体，**不拦同域静态资源**）
—— 不必依赖浏览器，`curl` 带 `CtClient;13.4.0;Android;16;…` 即可。

已做的自证（`probes/he_ct_tariff.py` 的 `selftest()`，每次跑都验一遍）：

- 三条抓包密文解密后**重新加密**，**逐字节相同**（216 / 216 / 280 字符）；
- 故意用错密钥 ⇒ **PKCS7 校验失败**（不是解出乱码）；
- 从金标准密文里反解出 `sessionid`，再用 `build_*()` 重造 ⇒ 亦**逐字节相同**。

### 🔴 但这网采不到自动：WAF 是「瑞数类」JS 挑战

2026-09-22 端到端实测：

- `GET …/rateZone/index.html` → **412**
- **`POST …/tariffSection.do`（API 本身）→ 也是 412**（`build_tree` / `build_query` 都试了）
- 412 的响应体是一个 **JS 挑战页**：`<meta id="3etE7dr7M7O6" content="…">` + `$_ts=…`
- **`curl`（Schannel TLS）同样 412** ⇒ 拦的不是 TLS 指纹，是**挑战脚本没跑**
- ⇒ 那两条「风控 cookie」`EYg4xOZq0mLeS`（近似恒定）/ `EYg4xOZq0mLeT`（每请求变）
  **就是挑战脚本算出来的**（瑞数类方案的典型特征）

⇒ **纯 requests / curl 不可能通过**，必须走真实浏览器。而自动化浏览器也不行：

> ⚠️ **`navigator.webdriver=true` 会被 WAF 400 硬拒** —— chrome-devtools MCP / Playwright /
> Selenium 的受管浏览器全是这个值，换来的是**400 空响应**，连 412 挑战页都不给。
> 别走那条弯路。

**本仓库的采集路线**（已落地，河北 609906 共 884 条）：

真实 Chrome + 远程调试口（启动只给 `--remote-debugging-port`，
**不给** `--enable-automation`）+ CDP 页面内执行采集 JS —— 页面自带 CryptoJS 造包体。
工具在 `probes/tools/ct_browser/`，云端入口是 `ci_grab.sh`。

🟢 **它在云端也采得到**（2026-09-22 实测，ubuntu-latest）：
Xvfb 起**有头** Chrome，挑战自动通过、`navigator.webdriver=false`、884 条逐项采全。
之前「电信进不了云端 CI」是**推断错误** —— 缺的不是「云端」，是一块屏幕。
详见 `docs/电信云端采集可行性-20260922.md`。

即便如此，电信仍是**机会性采集**（不硬依赖），这是本仓库处理这一网的方式：

- 每日巡检先跑 `ci_grab.sh`（Xvfb + 有头 Chrome）拿原始产物，再直线抓另外三网；
- 采到 ⇒ 电信与移动/联通/广电一样走完整轮次（快照 + 变更报告 + 页面徽章）；
- 采不到 ⇒ 自动退回仓库里最新那份归一化快照
  （`cloud/tariff/snapshots/ct_tariff_YYYYMMDD.json.gz`）渲染。
  **页面不会少一网**，只是电信停在上一版，且会显式标注「快照兜底（基线早 N 天）」。
- ★ 这样做的关键前提：**基线日期取自数据自身**（`data_day()` 读它的 `fetchedAt`），
  而不是巡检当天 —— 所以陈旧是**看得见**的，绝不会拿今天的日期冒充上周的数据。
- 🔴 **`load_prev` 与 `load_latest` 是两个语义**：前者是「上一版」（严格早于今天，
  用于 diff），后者是「最新版」（不晚于今天，用于兜底渲染）。渲染用错成前者的症状是
  「今天明明采到了、页面还显示昨天」，看着像没更新，实则是取错了函数。
- 🔴 **`ci_grab.sh` 永不非零退出**：它是可失败的一步，不该把另外三网的页面部署一起拖死；
  它只负责把失败原因打成 `::warning::` 并留下截图/日志 artifact。

本机想手动补采也可以：跑同一条链路（`--remote-debugging-port` + `harvest_hb.js`），
产物 `cloud/tariff/.ct_raw.json` 交给 `rebuild_ct.py` 一次性重建页面。

其他实测要点：

- **`type=1` 才是「按 `lable1Id` 过滤」**；`type≠1`（0/2/3）忽略 `lable1Id` 返回全量 884 条
  —— 别拿它做分类轮询，会采到 5 份全同副本；
- 省份码硬编码在 `Index-1f2bc0ae.js`（河北=609906、北京=609001、集团=1000000037）；
- `tariffAttr`(1/2/3) 与是否过期**零相关**，页面根本不使用，统一置 `"2"` 与其他网对齐；
- 归一化：`feesUnit` 非「元/月」类（元/次、积分/次…共 382 条）**月费 `f` 留空**、
  费用原文并进详情（否则月费筛选会把「充100元」当月费 100）；流量 MB/M 统一折 GB；
  键名沿用移动那套的拼写 `offineDay`（**少个 f，是契约**，别"顺手改对"）。

细节见 `probes/he_ct_tariff.py` 头部注释与 `docs/电信资费接口-抓包定位-20260922.md`。

---

## 3. 页面在「某网没数据」时怎么办

- 抓不到 / 数据骤降 ⇒ 该网沿用**上一版快照**继续渲染，其余网照常 —— 一网坏不带崩整页。
- 真的一网都没有（首跑）⇒ 该网显示占位说明，工具栏 / 统计行 / 表格一起藏起来；
  给一个点不出东西的工具栏，用户只会怀疑页面坏了。
- **基线日期每网各一份**（抓取时点不会同一天），相对天数与时间筛选按当前网重算。
- 🔴 **新接入一网时不要用 `--render-only` 重建**：它按现有容器重建，页面里还没有那网，
  重建反而会把刚接上的那网弄丢。用 `python rebuild_offline.py --fresh <net>`。

页面上有**两个不同含义的日期，别混**：

| 显示 | 含义 | 会不会自己前进 |
|---|---|---|
| **数据基线**（页面顶部） | 这份资费数据本身的版本日期 | 只有资费真变了才前进 |
| **最近巡检**（脚本输出） | 每日任务最近一次成功运行的时间 | 每天都在前进 |

页面归档为了控制仓库体积，**只在数据变化时更新**，所以
**页面上的日期停住 ≠ 监控挂了**。「监控还活着吗」看脚本打印的「最近巡检」
（取自 `cloud/tariff/state.json`，每次跑都覆盖写入）。

---

## 4. 怎么用

### 看最新页面

🟢 直接打开 <https://starrry365.github.io/hebei-tariff-monitor/>（CI 每日自动重新部署）。

离线/本机查看（从仓库取最近一次归档）：

```bash
python cloud/tariff/view_page.py      # 解压 page/index.html.gz 并用默认浏览器打开
```

或直接双击 `cloud/tariff/view_page.bat`。

### 本地跑一次巡检

```bash
cd cloud/tariff
pip install pycryptodome
printf 'KEY=...\nIV=...\n' > .nrapigate_key     # 只需一次（该文件已 gitignore）
python tariff_monitor.py                        # 抓取 ~15s，写快照 + 报告 + 页面
```

常用开关：`--no-html`（只要数据）/ `--render-only`（不联网，套当前模板重渲染）
/ `--fresh <net>`（新接一网时重建容器）。

> 🔴 **密钥本体不入本仓库**（本仓库是公开仓库）。`cloud/tariff/mz_crypto.py` 的取值顺序：
> 环境变量 `NRAPIGATE_KEY` / `NRAPIGATE_IV`（CI 走 Actions Secret）> 同目录 `.nrapigate_key`
> （已被 `.gitignore` 忽略）> **直接抛错**（绝不回退到硬编码值）。

---

## 5. 自动化：抓取 + 部署一条链

`.github/workflows/tariff-daily.yml`，每天 UTC 22:00（北京时间次日 06:00）触发，
也可在 Actions 页面手动 `workflow_dispatch`：

```
检出 → 装 pycryptodome + websocket-client + xvfb
     → 环境自检（密钥装载 + 出口连通预检，**只告警不阻断**）
     → 采集电信（Xvfb + 有头 Chrome + CDP；失败只发 ::warning::，走快照兜底）
     → 抓取另三网 + 比对变更 + 生成页面（四网）
     → 逐网校验页面（占位符 / 条数区间 / 行字段形态 / 电信是直采还是兜底）
     → 上传 artifact → 部署 GitHub Pages   ← 线上页面到这一步更新
     → 提交快照 / 变更报告 / gz 归档回仓库
```

- **页面**：`cloud/tariff/docs/index.html` 由 CI 现场生成，直接上传部署，**不入 git**；
  同时 gzip 归档进 `cloud/tariff/page/index.html.gz` 做离线备份。
- **数据**：每日快照与变更报告提交回本仓库（这就是自动更新的数据历史）。
- ⚠️ 出口连通预检**不能用 `curl`**：Linux 的 curl 走 OpenSSL 3.x，会因
  `UNSAFE_LEGACY_RENEGOTIATION_DISABLED` 误报 `HTTP=000`，而脚本实际是通的 —— 用 python 预检。
- 🔴 **预检只告警，不做闸门**。2026-09-22 它报 `Errno 101 Network is unreachable`
  把整轮掐死过（抓取 / 校验 / 部署全部 skipped），而同一分支上一次运行是通的 ——
  单发连通探针天生会假阴性，而它的 URL、握手参数都和真实抓取不是同一条路径。
  真正的闸门是抓取本身（抓到 0 条会明确失败）。

---

## 6. 产物与体积控制

| 产物 | 是否入库 | 说明 |
|---|---|---|
| `snapshots/hebei_tariff_*.json.gz` | ✅ | 移动每日快照，gzip（6.5MB → 545KB），只留最近 60 份 |
| `snapshots/unicom_tariff_*.json.gz` | ✅ | 联通每日快照 |
| `snapshots/cbn_tariff_*.json.gz` | ✅ | 广电每日快照 |
| `changes/YYYY-MM-DD.md` | ✅ | 每日变更报告（新增 / 下线 / 字段变更） |
| `page/index.html.gz` | ✅ | 查询页离线归档，**仅在资费数据变化时更新** |
| `docs/index.html` | ❌ | 未压缩页面，Pages 部署用，gitignore |

🔴 **快照文件名必须逐网带前缀** —— 混放会让一网的旧快照变成另一网的基准，
每日比对随即报出满屏假变更。

---

## 7. 健壮性

- **数据量守卫**：条目数跌破上次 60% ⇒ 判为抓取异常，**不写快照、不出下线报告**
  （退出码 2），避免接口抽风报出「资费全部下架」的假警报。
- **发布前校验**：页面占位符未替换 / JSON 解析不出 / **逐网**条数越界 ⇒ 构建失败。
  ⚠️ 条数区间**逐网给**：广电只有两百来条，照抄移动的 500 会让 CI 天天红；
  而一旦为此把全局下限压到 100，移动 / 联通那两网的真骤降就再也拦不住了。
  行的字段形态也要查（少一个 `o`/`e`/`f`，页面排序与时间筛选会**静默**失效 ——
  `NaN` 参与比较恒为 false ⇒ 筛选返回全部）。
- **密钥外部化**：env > `.nrapigate_key` > **抛错**。
- **退出码**：0 正常 / 2 数据量异常 / 3 网络全失败。

---

## 8. 口径声明

数据为运营商**报备公示**口径，与在售资费不完全等同。
「目标客户」字段是接口原文条件，能否实际办理请以营业厅 / 客服热线为准。

`reporterName` / `reporterStaff` 在**源头已打码**（形如 `139****0322`），
本仓库全部数据**不含任何个人信息**（已逐文件复核：未打码 11 位手机号 0 处）。

本仓库为**公开**仓库，因此：网关密钥不入库（走 Secret / 本地文件）、
抓包证据已抹去 Cookie 值、设备指纹与内网 IP。电信页面 JS 里的密钥
`telecom_wap_2018` 属**运营商自己发布在公网静态资源中**的值，作为逆向结论保留。

仅供技术研究与个人查询使用，请勿用于商业用途或高频请求。

---

## 9. 仓库结构

```
├── README.md                     ← 本文件（接口特性 / 坑 / 用法）
├── .github/workflows/
│   └── tariff-daily.yml          ← 每日巡检 + Pages 部署
├── cloud/tariff/                 ← 巡检主体
│   ├── tariff_monitor.py         ← 移动抓取 + 四网编排 + 页面渲染
│   ├── unicom_monitor.py         ← 联通适配器
│   ├── cbn_monitor.py            ← 广电适配器
│   ├── ct_monitor.py             ← 电信适配器（读 .ct_raw.json 纯转换）
│   ├── mz_crypto.py              ← nrapigate 网关加解密（密钥外部化）
│   ├── template.html             ← 查询页模板（四网容器）
│   ├── rebuild_offline.py        ← 不联网重建页面（--fresh <net> 接新网）
│   ├── rebuild_ct.py             ← 电信数据本地重建
│   ├── conformance.py            ← 与页面对账（改筛选/排序后跑一次）
│   ├── audit_data.py             ← 数据体检
│   ├── view_page.py / .bat       ← 本机取归档页面
│   ├── README.md                 ← 模块级细节（怎么跑 / 怎么改模板 / 接网踩坑）
│   ├── snapshots/ · changes/ · page/ · docs/ · state.json
├── probes/                       ← 逆向探针（可独立运行）
│   ├── he_unicom_tariff.py · he_cbn_tariff.py · he_ct_tariff.py
│   ├── probe_scope_state.py      ← 地域判据（河北/全国 + 地市）离线回归
│   ├── probe_city_matrix.py      ← 四网地市粒度可达性矩阵（结论可复现）
│   ├── probe_zq_text.py          ← 四网「个人/政企」文本分档
│   ├── probe_upstream_limits.py  ← 实时探针：联通旁路接口未路由 / 广电区域表只到省级
│   └── tools/
│       ├── ct_browser/           ← 电信真实 Chrome + CDP 采集工具链（ci_grab.sh 为云端入口）
│       ├── cdp_launch.py         ← 本机 CDP Chrome 启动器（9223）
│       └── page_e2e_check.js     ← 页面真实浏览器端到端断言（四网）
├── evidence/                     ← 抓包证据（已脱敏）
└── docs/                         ← 逆向过程报告 + 云端采集可行性实测
```

> `probes/` 与 `cloud/tariff/` 的分工：探针是**怎么把接口问出来的**（可独立跑、
> 带自证），适配器是**日常怎么采**（稳定、少依赖）。适配器通过
> `sys.path` 引用探针模块，所以两者的相对位置是有意保持的。
