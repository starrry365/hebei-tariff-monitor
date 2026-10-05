# 服务全面健康终检报告（2026-10-05 13:55）

> 范围：本地代码 / CI 运行状态 / GitHub Pages 线上 / 数据文件完整性 / 调度配置。
> 结论：**全层健康，无异常项**。

## 1. 本地代码层 ✅

| 检查 | 结果 |
|---|---|
| ruff（F821/822/823 未定义名） | All checks passed |
| 集成缝自测 selftest_pipeline | 通过（other_nets 四分支 / 返回契约 / note 覆盖） |
| run_checks 全量回归 | **19/19 全绿** |

19 项亮点：conformance 四网与 oracle 逐条一致（抓「页面自洽但算错」的唯一防线）、
e2e 四网语义断言 `__allOk=true`、walk 异常 0 + 连跑一致 + 几何命中、
首屏 **DCL 574ms · parse 64ms · blob 6.2MB**（远低于 1500ms 软阈值）。

## 2. CI 运行状态 ✅（GitHub API 实查）

| Workflow | 最近运行 | 状态 |
|---|---|---|
| 资费每日巡检 tariff-daily | #51 · 今晨 schedule 触发（排定 06:00，实跑 08:25） | ✅ success |
| CI ci.yml | #58 · 双采调度推送（`fb5f497`）触发 | ✅ success |
| 数据新鲜度巡检 fresh-check | #9 · schedule | ✅ success |

文案修复推送（#56）与 IPTV 相关提交（#57）亦全绿；近 5 次运行无失败。

## 3. GitHub Pages 线上 ✅

- 查询页：HTTP 200 · Content-Length 13.28MB · **Last-Modified = 今晨 08:31（北京）**
  —— 即今晨巡检 #51 的部署版
- RSS feed.xml：HTTP 200 · 30 条目 · 最新条目「2026-10-05 河北电信：变更 2」
- PWA 产物（manifest.json / sw.js / icons）在位

> 注：线上页仍是今晨构建版，今天下午的文案修复（在售/尚未接入等 12 项）将随
> **明早 09:30 巡检轮**重建上线 —— 页面由巡检重建部署是本服务的既定架构，非异常。

## 4. 数据文件完整性 ✅

- **快照**：58 份（KEEP=60 上限内），今日四网快照齐全
  （move 5280 / unicom 8096 / cbn 335 / telecom 1303 条）
- **history.json**：schema 1 · 58 条 · 今日四网记录齐全
- **官方新鲜度检查器** check_fresh.py：`stale=[] missing=[]` —— 全部新鲜
- **页面归档** page/index.html.gz：可解压 12.7MB，字节去重机制正常

## 5. 调度配置 ✅（含一处注释修正）

- 双采 cron 已入库：北京 **09:30 / 15:30**（`30 1 * * *` / `30 7 * * *`），
  结构断言通过（2 条 cron、避整点、手动触发保留）
- fresh-check 阈值语义复核：30 小时阈值在双采下依然正确——正常最大数据年龄
  18 小时，留 12 小时排队容错；**连着两轮没跑必然报警**，单轮失败会被下一轮救回
- 本次修正：fresh-check.yml 与 check_fresh.py 中 3 处按老「06:00 一轮」写的注释
  已更新为双采口径（纯注释，无逻辑改动）

## 遗留关注（非异常）

1. **明早 09:30 首轮双采**：GitHub 对新 schedule 的登记有延迟，明早到
   Actions 页确认触发；未触发就手动 workflow_dispatch 补一次验证全链路。
2. GitHub 排队延迟可观（实测 2.5~6 小时）：fresh-check 的 30 小时阈值已覆盖，
   无需调整；但人工看数据时别把「排定时间」当「实跑时间」。
