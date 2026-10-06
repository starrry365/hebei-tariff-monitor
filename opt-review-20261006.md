# 资费监控第 4 轮全面体检报告（2026-10-06）

> 前三轮分别覆盖信息呈现、工程维度、文案规范。本轮扫**尚未覆盖的盲区**：
> 仓库与产物增长（双采新变量）、CI 配置安全与效率、采集层健壮性、前端残余维度。
> 全部结论基于 git/文件系统/API 实测。

## 总体结论

系统在**功能与健壮性层面已经相当成熟**：CI 三件套（最小权限 / 并发组 / 超时）齐全，
采集层有重试退避+抖动、25s 超时、原子写、degrade 数据冻结机制；前端 PWA / 深色模式 /
reduced-motion / aria 无障碍均已覆盖。本轮真正的新发现集中在**仓库增长**这一此前没人盯的维度。

## P2 —— 仓库增长不可持续（唯一需要规划的事）

**实测**：`.git` 101MB（pack 87.85MiB），工作区快照仅 29MB。历史 blob 按路径聚合：

| 路径 | 历史累计 | 性质 |
|---|---|---|
| page/index.html.gz | 33.0MB | 构建产物，每日 ~1.1MB 全量新增（gz 不可 delta） |
| snapshots/*.json.gz | 40.9MB | 审计数据，unicom 22MB + hebei 13.6MB |
| docs/hebei-tariff-monitor-source.zip | 16.6MB | 构建产物，zip 不可 delta |
| history.json | 8.6MB | 追加型 JSON，delta 压缩良好，不急 |

**推演**：合计约 **2.5~3.5MB/天** 不可压缩增长 → 约 **10~14 个月触及 GitHub 1GB 软警告**。
不影响正确性，但会在明年某个时点被迫仓促治理。

**建议（按代价排序）**：
1. **先立仪表（10 分钟）**：在 fresh-check 或 run_checks 里记录 `git count-objects` 体积，
   超 600MB 警示——让增长可见。
2. **中期治理（触发线时做）**：把 `page/index.html.gz` 与 `source.zip` 两个可再生产物迁到
   GitHub Releases 或独立数据分支（工作区保留最新一份，conformance 消费不受影响）；
   旧快照按季归档到 Release。**代价**：仓库历史审计能力部分降级（Release 仍是完整存档）。
3. **不建议**：Git LFS（免费配额 1GB 存储不够这个增速）、history 重写（破坏审计且高频重写不可持续）。

## P3 —— 两项小改进（各 ~15 分钟）

1. **巡检失败无即时推送**：tariff-daily 无 `if: failure()` 通知步骤；目前靠 fresh-check
   兜底，但 fresh-check 设计上容忍一轮失败，最坏 **~1.5 天**才告警。建议在巡检 job 尾部加
   失败分支调 `notify.send_all`（dedup 已内置，不会重复轰炸）。
2. **社交分享 meta 缺失**：`og:title/og:image/description-card` 为 0——分享到微信/QQ 无卡片。
   资费页有天然的分享场景，5 分钟补齐。

## P3 —— 观察项（暂不动）

- **@media print 缺失**：打印/存 PDF 场景样式未适配。资费对比打印是低频但真实的需求，
  可等有人真用时再做。
- **SW 缓存与双采节奏**：页面一天两更后，service worker 缓存策略决定用户看到新版的最长延迟，
  属既有设计，先观察 PWA 更新反馈再定。

## 明确不动（本轮复核确认）

- **CI 配置**：permissions 最小化（ci/fresh-check 均 `contents: read`）、tariff-daily
  恰当使用 `contents+pages+id-token: write`、concurrency 组与 cancel-in-progress 取向正确
  （数据管线不取消在跑的）——已是最佳实践，无需动。
- **采集层**：重试+指数退避+随机抖动、UA 伪装、25s 超时、原子写（.tmp→os.replace）、
  degrade 数据量异常冻结——链路完整。
- **快照入库本身**：有 KEEP=60 滚动保留，审计价值真实存在（conformance oracle 依赖），
  治理只应针对增量速度而非取消入库。

## 优先级总表

| 级别 | 事项 | 动作 | 紧迫度 |
|---|---|---|---|
| P2 | 仓库增长 ~3MB/天 | 先加体积仪表；600MB 触发产物迁移方案 | 本月内加仪表即可 |
| P3 | 巡检失败无即时通知 | tariff-daily 加 `if: failure()` 推送 | 顺手修 |
| P3 | og: 分享 meta 缺失 | 补 3 行 meta | 顺手修 |
| P3 | 打印样式 / SW 缓存节奏 | 观察，暂不动 | 无 |
