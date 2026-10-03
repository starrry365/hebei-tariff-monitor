# 全面代码审查 — 修复总览

对 `hebei-tariff-monitor` 全仓 19341 行做了系统性审查（3 个并行子代理分片 + 亲审核心构建脚本），所有可疑点均经真实数据/实机验证，区分「探针过时」与「真 bug」。全部修复已验证，18 项检查全绿。

## 真 bug 修复（12 处）

### 构建层 `cloud/tariff/`
| 严重度 | 文件 | 问题 |
|---|---|---|
| 🔴 P0 | tariff_monitor.py | **move_quiet NameError**：仅常规 diff 分支赋值，frozen/首版/accept 路径引用即崩（与 2026-09-26 other_nets 事故同类）。修复：分支前初始化 |
| P2 | tariff_monitor.py | 集团公示（attr=3）误写 pw=1，524 条混入「仅全省通用」。修复：attr=3 不写 pw；常量提成 `ATTR_GROUP_PUB` 唯一权威 |
| P1 | template.html | readHash 参数缺失不清控件：深链删参数后残留值污染后续筛选（实测污染 move 大类 2/537）。修复：`else{sel.value=""}` |
| P1 | template.html | readHash STA 缺省退到「全部」：外部链接不带 st 时丢「在售」默认。修复：不赋值即保留初始 "0" |
| P2 | template.html | optText 正则兼容「（N 条）」写法 |

### 对账层（oracle 判据漂移，页面是对的）
| 文件 | 问题 |
|---|---|
| conformance.py | off 判据 `lf<0 or lf>N` → `abs(lf)>N`（页面在售/下架双向匹配 ±N） |
| audit_data.py | bw_info 两处漂移：name 分支缺 `BW_ASSERT` 词表、field 分支缺 `BW_NOTLINE` 过滤（多判 3 条，128≠125）；BW_ASSERT 接入 check_sync 常量比对 |
| audit_data.py | check_city 三态语义：集团公示（a1=3）在售无 cty/pw 是合法形态（524 条误报「上下游没说地域范围」） |

### 采集层 `probes/`
| 文件 | 问题 |
|---|---|
| ct_monitor.py | `_normalize/_normalize_jt` 费用拼接条件错，9 条 fees="0" 无单位条目整条丢失 → 现进注记；`_jt_table` 多值行取首行防静默覆盖 |
| he_unicom_tariff.py | 去重键对齐广电（同号不同名不再误合并） |
| he_cbn_tariff.py | stateFlag None=在售语义与构建侧对齐 |

### 工具/CI
| 文件 | 问题 |
|---|---|
| run_conformance.py | 页面就绪竞态：13MB 页面 nav sleep 2s 不够，83/83 全灭 → 30 秒轮询 |
| page_e2e_check.js | 断言写死「地市下线」时段 → 按 ctUsable 分叉正向/幽灵断言；**resetAll 漏清 hash 与视图**（见下） |
| run_checks.py | `--net` 无效值从静默回退全量 → 报错退出 |
| export_desktop.py | 电信快照前缀 ct_ 拼错永不命中 → 加映射 + 找不到警告 |
| tariff-daily.yml | 重试循环加快照存在检查，防第二轮拿当天快照当基准覆盖真实变更报告为 0/0/0；ci.yml glob 兼容 *.yaml；fresh-check.yml 删 RC 死代码 |

## 审查中发现并修复的「检查工具自身」问题

1. **probe_filter_dims.py**（判据过时）：电信集团公示块真有 type3Name（东亚/促销…16 个合法 ty 被误判越界）；联通 `UC_TY_NORM` 映射目标（其他加装/移网套餐）未计入 allow。判据跟上构建侧后四网全绿。
2. **page_e2e_check.js resetAll 状态恢复不全**（本轮最深的一个）：
   - 症状：run_checks 第二/三轮 e2e 稳定挂（walk 之后），报「套餐筛出 0 条」「深链 0≠81」等，但单独跑 e2e 全绿；
   - 根因：walk 遍历结束时遗留 hash（kw/时间筛选）+ 停在 `setView("ov")` 总览视图；e2e 的 resetAll 清了控件但**不清 hash、不恢复视图** → cityProbe 在遗留 hash 上追加 ct 触发 hashchange → readHash 恢复遗留条件/切网/切到总览视图 → `view.length` 全 0；
   - 修复：resetAll 里 replaceState 抹 hash + `setView("list")` 恢复视图 + 死调用 syncTyOptions 改为页面真实函数 syncDims。

## 验证结果

- 全部 py 编译 OK；selftest_pipeline.py 通过（集成缝四分支自测）
- **conformance 四网全绿**：move 78/78 · telecom 119/119 · unicom 102/102 · cbn 83/83
- **e2e allOk=true**（真重载 + walk 遗留状态的最坏序列下验证）
- **audit_data 四网全过**（三态判据更新后）
- **run_checks.py 18 项全绿**

## 未接入项（有意识的决定）

- noise_guard.py 不接入写路径：「总数没变整批换人」四网未实测发生，贸然接入有误冻结真变化风险；CI --check 事后审计保留。
