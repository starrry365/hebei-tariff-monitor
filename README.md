# 河北四网资费每日巡检

🟢 查询页：<https://starrry365.github.io/hebei-tariff-monitor/>

每天 06:00 抓取河北移动/联通/电信/广电的公开资费（在售+已下架，一万四千余条，仅河北+全国口径），比对上下线变更后重新部署查询页。页面支持地域、资费类型、渠道、个人/政企筛选，并保留每日变更历史。

代码与运维说明见 [`cloud/tariff/`](cloud/tariff/)。

本机改完推送并跑一轮：`tools/push-and-run.sh`（`--no-watch` 不等结果，`PROXY=...` 换代理）。退出码 `0` 通过 / `1` 推送或触发失败 / `2` 巡检失败。

> 工作流只由 `schedule` 与 `workflow_dispatch` 触发——巡检会把快照提交回本仓库，加 `push` 会自触发循环。
