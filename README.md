# 河北四网资费每日巡检

🟢 查询页：<https://starrry365.github.io/hebei-tariff-monitor/>

每天 06:00 抓取河北移动/联通/电信/广电的公开资费（在售+已下架，一万四千余条，仅河北+全国口径），比对上下线变更后重新部署查询页。页面支持地域、资费类型、渠道、个人/政企筛选，并保留每日变更历史。

代码与运维说明见 [`cloud/tariff/`](cloud/tariff/)。

本机改完推送并跑一轮：`tools/push-and-run.sh`（`--no-watch` 不等结果，`PROXY=...` 换代理）。退出码 `0` 通过 / `1` 推送或触发失败 / `2` 巡检失败。

> 工作流只由 `schedule` 与 `workflow_dispatch` 触发——巡检会把快照提交回本仓库，加 `push` 会自触发循环。

## 运维工具链

| 脚本 | 做什么 |
|---|---|
| `cloud/tariff/check_fresh.py` | 数据新鲜度检查：产物超过 `FRESH_MAX_HOURS` 没更新就告警（专抓「巡检**根本没跑**」这类静默失效） |
| `cloud/tariff/summary.py` | 把 `history.json` 汇总成一条 Markdown（正文格式复用 `notify.build_body`，不另起一套） |
| `cloud/tariff/push_once.py` | 补推一次：拉数据 → 渲染 → 走五通道发送（`--dry` / `--days 7` / `--force`） |
| `cloud/tariff/noise_guard.py` | 采样噪声护栏 + history 瘦身（`--selfcheck` 自测 / `--check` 只读体检） |
| `cloud/tariff/make_source_zip.py` | 打源码包供站点下载（凭据与数据不入包，确定性打包） |
| `cloud/tariff/notify.py` | 五个推送通道：钉钉 / 飞书 / 企业微信 / PushPlus / 邮件 |
| `tools/make_icons.py` | 生成 PWA 图标（纯 stdlib，尺寸与配色可复现） |
| `tests/run_all.py` | 统一测试入口（`--quick` 只跑不依赖快照的那些） |

**文档**：[推送配置（五通道）](docs/NOTIFY_SETUP.md) ·
[钉钉专题](docs/DINGTALK_SETUP.md) ·
[变更护栏与推送配置](docs/变更护栏与推送配置-20260926.md)

**工作流**（`.github/workflows/`）：
`tariff-daily.yml` 每日巡检 + 部署 ·
`ci.yml` push/PR 跑语法检查与测试 ·
`fresh-check.yml` 每日两次新鲜度巡检 ·
`notify-test.yml` 推送通道自检

**页面**：查询页支持 ★ 收藏关注（本机 localStorage）与「只看收藏」，
并可「添加到主屏幕」当 PWA 用；「关于」页里有源码包下载入口。
