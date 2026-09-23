# cloud/tariff —— 巡检与页面生成

四网资费抓取、变更比对、页面生成与归档的代码都在此目录，每天 06:00 由 CI 自动运行。

- `tariff_monitor.py` 主流程；`rebuild_offline.py` 用本地缓存离线重渲（不联网）
- `template.html` 查询页模板；`conformance.py` 归档一致性校验
- 产物：`docs/index.html`（线上页面）、`page/index.html.gz`（归档）、`snapshots/`、`changes/`

页面：<https://starrry365.github.io/hebei-tariff-monitor/>
