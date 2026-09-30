# 🧪 hebei-tariff-monitor 测试套件

把仓库已有的 4 个自测脚本统一串成一个入口 —— 本地和 CI 都能一键跑。

## 快速开始

```bash
# 列出所有测试
python tests/run_all.py --list

# 跑全部（包含依赖快照的集成测试）
python tests/run_all.py

# 只跑快速测试（不依赖快照，本地预提交用）
python tests/run_all.py --quick

# 只跑某一个
python tests/run_all.py --only notify-selfcheck

# 多个用逗号分隔
python tests/run_all.py --only notify-config,change-guard
```

退出码：`0` 全通过 / `1` 至少一个失败 / `2` 没有匹配的测试名

## 已纳入的测试

| 名称 | 来源脚本 | 覆盖 | quick |
|---|---|---|---|
| `notify-config` | `cloud/tariff/notify.py --check` | 列已配置通道（不发请求） | ✅ |
| `notify-selfcheck` | `cloud/tariff/notify.py --selfcheck` | 零变化闸门 / 异常态必推 / 转义 | ✅ |
| `change-guard` | `cloud/tariff/change_guard.py` | 假下架 / 假新增 / 身份漂移 / 回弹 / 降级状态机 | ✅ |
| `pipeline-selftest` | `cloud/tariff/selftest_pipeline.py` | other_nets 四分支 / 返回契约 / note 覆盖 | ❌（读快照） |

## 设计原则

1. **零新增依赖** —— 直接调现有脚本的入口，不重新实现逻辑
2. **失败隔离** —— 一个测试挂掉不会拖死其他测试；汇总按个报
3. **本地 / CI 同源** —— 本地跑的和 GitHub Actions 跑的是同一份代码

## 没纳入的脚本及原因

| 脚本 | 没纳入的原因 |
|---|---|
| `cloud/tariff/audit_data.py` | 检查**采回来的数据**（非算法本身）；产物级别的检查，由 CI 在巡检流程里跑 |
| `cloud/tariff/backfill_history.py` | 一次性补录脚本，不是测试 |
| `cloud/tariff/rebuild_offline.py` | 离线重建脚本，不是测试 |

## 在 CI 里集成

仓库已有的 `.github/workflows/tariff-daily.yml` 已经在巡检流程里跑过这些自测
（分散在「体检数据判据」那一步）。如果想加一个 push 时跑的快速闸门：

```yaml
# .github/workflows/ci.yml（示例）
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python tests/run_all.py --quick
```
