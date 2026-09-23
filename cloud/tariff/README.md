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
| `audit_data.py [--net X]` | 数据判据：日期格式、宽带判定、搜索字段缺口、城市表、脚本↔模板规则同步 | 否 |
| `probes/tools/run_conformance.py [--net X]` | **筛选口径对账**：独立 oracle 重写一套筛选语义，与页面逐条比对条数 | 是 |
| `probes/tools/page_walk_check.js` | 交互全路径遍历（页签 / 下拉 / 主题 / 翻页 / 排序），**未捕获异常必须为 0** | 是 |
| `probes/tools/page_e2e_check.js` | UI 行为断言（置灰档、地市显隐、幽灵塞值、类型联动、chips 可摘） | 是 |
| `probes/check_area_scope.py` | 「只保留河北 + 全国」是否成立：上游地域取值分布 + 被丢弃条目 | 否 |

🔴 **`conformance.py` 只生成用例，自己不会验** —— 必须由 `run_conformance.py` 驱动浏览器跑完才算数
（它曾长期无人消费，等于没有对账）。`--net` 只支持**有条目级地市码**的网（move / telecom）；
联通、广电的数据源声明 `allProvince`，没有地市维度可对账。

页面：<https://starrry365.github.io/hebei-tariff-monitor/>
