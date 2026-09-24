# cloud/tariff —— 巡检与页面生成

四网资费抓取、变更比对、页面生成与归档的代码都在此目录，每天 06:00 由 CI 自动运行。

## 主链路

- `tariff_monitor.py` 主流程（抓取 → 对比 → `build_html` → 归档）
- `template.html` 查询页模板；`docs/index.html` 是渲染产物（已 gitignore，CI 直接上传部署）
- 各网适配器：`unicom_monitor.py` / `cbn_monitor.py` / `ct_monitor.py`
- 产物：`page/index.html.gz`（页面归档）、`snapshots/`、`changes/`、`history.json`、`state.json`

## 本机重建（不碰快照 / 变更报告 / state.json）

```bash
python rebuild_offline.py                # 用快照 + 本地缓存重建（秒级）
python rebuild_offline.py --fresh cbn    # 只重采广电
```

⚠️ 别在本地跑完整 `tariff_monitor.py` —— 它会覆盖云端当天的 `changes/` 与 `state.json`。

## 自检（改了判据 / 模板 / 适配器后必跑）

| 脚本 | 查什么 | 需要浏览器 |
|---|---|---|
| `audit_data.py [--net X]` | 数据判据：日期格式、宽带判定、搜索字段缺口、地市归属取值域、注入清单↔构建脚本一致性 | 否 |
| `probes/tools/run_conformance.py [--net X]` | **筛选口径对账**：独立 oracle 重写一套筛选语义，与页面逐条比对条数 | 是 |
| `probes/tools/page_walk_check.js` | 交互全路径遍历（页签 / 下拉 / 主题 / 翻页 / 排序），**未捕获异常必须为 0** | 是 |
| `probes/tools/page_e2e_check.js` | UI 行为断言（置灰档、地市显隐、幽灵塞值、类型联动、chips 可摘） | 是 |
| `probes/check_area_scope.py` | 「只保留河北 + 全国」是否成立：上游地域取值分布 + 被丢弃条目 | 否 |
| `probes/probe_city_code_semantics.py` | **地市码名 ↔ 条目文案**是否一致（码名错挂）+ 上游「全省的第二种写法」是否被识别 | 否 |
| `probes/probe_coverage_axes.py --net unicom\|move` | **采集维度是否穷尽**：上游目录 / 地市集合 / 板块取值 / 目录未声明的组合是否真没数据 | 是（打真接口） |
| `probes/probe_sticky_layers.py` | **吸顶层是否不透明**：每条 `position:sticky` 的规则必须自带实色背景（`--card*`），存在唯一吸顶根 `#deck` | 否 |

🔴 **前两条与前面所有检查问的不是同一件事**（2026-09-24 立）：
前者问「**已经采到的那批**对不对」，后者问「**该采的是不是都去采了**」。
失效方式完全不同 —— 前者算错能对出来；后者是整栏/整档**从未被请求过**，
接口全 200、日志无异常，唯一的症状是「少了」，而少的那部分从不出现。
当天两个真错（`3121` 码名错挂成「省直辖（定州/辛集）」实际是雄安新区、
上游用「12 个地市码全列」表达全省却没被识别）**全在这一层**，
而当时其它所有检查步都照不出来。

🔴 **`probe_sticky_layers.py` 与 `page_walk_check.js` 的 `opaqueOk` 是同一件事的两种测法**
（2026-09-24 立，用户报「界面有问题」）：前者静态读 CSS（进 CI），后者在真实浏览器里
逐元素读 `getComputedStyle().backgroundColor` 的 alpha（本机跑）。
失效形态＝**吸顶层半透明 ⇒ 滚动内容从它下面透上来与它叠成乱码**。
★ 这一类 rect / `elementFromPoint` / 点按钮的遍历**全都测不出来** ——
透明元素的几何完全正常、命中测试返回的仍是最上层元素。
**唯一能表达它的量是背景色的 alpha**，所以只能这么判。
事故与修法见 `docs/UI重做-吸顶层透明乱码-20260924.md`。

🔴 **`conformance.py` 只生成用例，自己不会验** —— 必须由 `run_conformance.py` 驱动浏览器跑完才算数
（它曾长期无人消费，等于没有对账）。`--net` 支持**有条目级地市归属**的三网
（move / telecom / unicom）；广电上游只有「全国 / 河北省」两档，没有地市维度可对账。

## 一条命令跑全套

```bash
python rebuild_offline.py                 # 先刷新页面产物（archive=False，不碰入库 gz）
python probes/tools/run_checks.py         # 上表全套：四网体检 + 地域口径 + 遍历 + e2e + 三网对账
python probes/tools/run_checks.py --no-browser   # 只跑不需要浏览器的（秒级）
python probes/tools/run_checks.py --net unicom   # 体检 / 对账只跑联通
```

`run_checks.py` 存在的理由：上表那些检查**大多不在 CI 里**（浏览器类要真 Chrome + 虚拟显示），
于是有一个很现实的失效模式 —— **检查写了但没人跑**。它把「起 CDP Chrome → nav → 遍历两次
比对字节 → e2e → 三网对账」压成一条命令。🔴 它**不会**替你重建页面：拿旧页面跑出一片绿
比不跑更糟，所以第一行必须自己跑。

## 证据生成器（结论需要复核时用）

| 探针 | 回答什么 | 打真接口 |
|---|---|---|
| `probes/probe_city_matrix.py` | 四网**有没有**条目级地市归属（读快照，不猜） | 否 |
| `probes/probe_upstream_limits.py` | 联通 / 广电**为什么**（不）能按地市取数 | 是 |
| `probes/probe_unicom_city_scope.py` | 联通地市差异的**四层判据**（同城重复 / 跨省区分 / 差异语义 / 全组合覆盖） | 是 |
| `probes/probe_city_only.py` | 某个地市**独有**的三级目录有哪些、落在哪个分类（回答「为什么按这个市筛只出来 N 条」） | 是 |
| `probes/probe_unicom_harvest_health.py` | 联通「逐城取并集」这一步有没有**把请求失败当成空目录**（会静默污染「不限地市」判定） | 是 |
| `probes/probe_city_code_semantics.py` | 4 位地市**码的语义**（码名对不对）+ 「全省」的两种写法（**阴性对照**：把 `3121` 改回旧名应当报错退出） | 否 |
| `probes/probe_sticky_layers.py` | 吸顶层有没有**实色背景**（**阴性对照**：把 `.deck` 的背景换成 `var(--glass)` 应当报错退出 2） | 否 |

🔴 这三支探针的结论**曾经错过一次**：`probe_upstream_limits.py` 当年只比 `indexData` 的
**两级骨架**，而真正随城市变的是**三级目录 id** ⇒ 得出「联通地市无影响」，主链路据此
只采邢台一城、静默漏 130 条。现在 B 段并列保留旧判据（留证）与现行判据 `city_scope()`。
复盘见 `docs/联通河北资费-地市维度纠错-20260924.md`。

页面：<https://starrry365.github.io/hebei-tariff-monitor/>
