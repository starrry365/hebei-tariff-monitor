# 复查修复执行总结（2026-10-04 · 第三轮）

用户指令：复查报告 12 项中「除了 9，全部修好」。实际落地 11 项，提交 `fb2abe2` 已推送。

## 修复清单

| # | 问题 | 修复方式 |
|---|------|----------|
| 2 | noise_guard 未接线 | diff_round 在护栏核验后调 `is_sampling_noise`，命中走回弹同路径（不计变化/不推送/报告+history 留痕 note=noise）；`_NOTE_CN`、页面 `NOTE_CN`、selftest need 三处词条同步 |
| 3 | 日期兜底用今天 | build_html 两处改 `prev_day()`，与 render_only 对齐 |
| 4 | CSV 公式注入 | csvCell 对 `^[=+\-@]` 加 `'` 前缀 |
| 5 | esc 缺单引号 | 补 `'` → `&#39;` |
| 6 | ROWS 残留 | render 空结果分支清空 |
| 7 | 收藏键同名冲突 | 键升级 v2「网\|细分\|名称」+ `migrateFav()` 一次性迁移存量（导入旧导出文件同样自动迁移） |
| 8 | 集成缝测试缺口 | 提示条组装提纯为 `build_notice()` 纯函数；selftest 新增 6 项断言（安静隐藏/带数字/护栏露面/别网警告不隐藏/特殊路径警告保留） |
| 10 | 缺机器友好出口 | 构建期生成 `docs/feed.xml`（RSS 2.0），只收真变化（a/r/c>0），pubDate 取抓取时刻，数据源只走 history.json |
| 11 | rows_of 体量 | 联通分类段提为 `classify_entry()` 具名函数；页面拆分维持观察 |
| 12 | 待上架无筛选 | on 维度新增 `future` 档（ag<0 与徽标同口径），深链自动支持 |
| 9 | 采集全失败无推送 | ⏸ 用户豁免，未修 |

## 验证

- py_compile / ruff F821-823 / selftest_pipeline（含新 6 项）/ noise_guard 自检：全过
- 离线重建：15014 条四网全量，feed.xml 29 条真变化生成正确
- 全量回归 run_checks **18/18 全绿**（audit ×4、conformance ×4 与 oracle 逐条一致、e2e 四网语义、walk 连跑一致）

## 关键文件

- `cloud/tariff/tariff_monitor.py`：noise 接线、write_feed、classify_entry、build_notice、prev_day
- `cloud/tariff/template.html`：esc/csvCell/ROWS/收藏 v2+迁移/待上架档/NOTE_CN
- `cloud/tariff/selftest_pipeline.py`：build_notice 单测 + noise 词条
- `cloud/tariff/docs/feed.xml`：新增产物（订阅地址 `https://starrry365.github.io/hebei-tariff-monitor/feed.xml`）
- `recheck-20261004.md`：处置清单已全部更新
