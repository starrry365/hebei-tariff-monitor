# 推送配置（五个通道）

> 巡检跑完把「本次变化」推到手机 / 群里。**配几个推几个**，一个都没配也不会报错
> （脚本会静默跳过）—— 本地 fork、别人的仓库跑都不该因为没配 SMTP 就变红。

## 一分钟上手

```bash
cp .env.example .env          # .env 已被 .gitignore 忽略，不会提交
vim .env                       # 按需填凭据，配几个填几个
python cloud/tariff/notify.py --check        # 看有哪些通道配齐了（不发请求）
python cloud/tariff/notify.py --test         # 真发一条自检消息
python cloud/tariff/push_once.py --dry       # 用真实数据渲染一遍，但不发送
```

`--check` 没列出任何通道 ⇒ 巡检会跳过推送，这是**预期行为**，不是故障。

## 五个通道

| 通道 | 需要的变量 | 必填？ | 说明 |
|---|---|---|---|
| 钉钉群机器人 | `DINGTALK_WEBHOOK` | ✅ | 可选 `DINGTALK_SECRET`（机器人开了「加签」才要） |
| 飞书群机器人 | `FEISHU_WEBHOOK` | ✅ | 可选 `FEISHU_SECRET`（同上），以 interactive 卡片发送 |
| 企业微信群机器人 | `WECOM_WEBHOOK` | ✅ | **没有**加签；markdown 正文上限 4096 字节，超了自动降级截断 |
| PushPlus（微信） | `PUSHPLUS_TOKEN` | ✅ | 可选 `PUSHPLUS_TOPIC`（群组编码，一对多） |
| 邮件（SMTP） | `SMTP_HOST` + `MAIL_TO` | ✅ | 另需 `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` 等 |

**通道互不拖累**：一个通道失败（token 过期 / SMTP 被拒）不影响其他通道；
推送整体失败也**不会**让巡检失败 —— 数据采到、页面更新才是主要产物。

## 🔴 钉钉与飞书的加签算法不一样

这是最容易踩的坑：两家的签名公式长得像，但**关键参数相反**。

| | 待签消息 | HMAC 的 key | 时间戳单位 |
|---|---|---|---|
| 钉钉 | `timestamp + "\n" + secret` | **secret** | 毫秒 |
| 飞书 | `timestamp + "\n" + secret` | **空串** | 秒 |

结果都是 base64；钉钉还要再过一次 `urlencode` 拼进 webhook 的 query。

> 直觉上「密钥当然是 HMAC 的 key」—— 这条直觉对钉钉成立，对飞书**不成立**。
> 抄错的表现是：钉钉回 `310000`「sign not match」，飞书回 `code=19021`。
> 两个算法都写进了 `cloud/tariff/notify.py --selfcheck` 的断言里，
> 跑一次就能自证实现没抄反。

## 行为契约（三条，都是踩过的）

### ① 一次变化只推一条

一轮巡检要跑四网，还常常重跑（定时 + 手动 dispatch + 重试）。推送若写在各网内部，
同一次变化会被推 4 次、重跑再推一次 —— 结果就是用户把通知静音。
所以正文在 `main()` 末尾**统一汇总成一条**，并且用 `.notify_state.json` 落盘记录
**内容指纹**；同一天同一份内容重复推送直接跳过。

> 指纹必须**落盘并入库**：CI 每轮都是全新 checkout，只在内存里去重等于没去重。

### ② 零变化不推

没有这道闸门时有个真事故形态：正文首行是「# … {日期}」，
日期每天不同 ⇒ 内容指纹每天都不同 ⇒ **零变化的日子也会照发一条**（去重对它无效）。

要每天报平安：设 `NOTIFY_ALWAYS=1`。

### ③ 异常态必须推

「无变化」**不含异常态**：任一网降级 / 结构变更 / 采集失败，一律照推 ——
那正是**需要人看一眼**的时刻。正文里这两种长得像（都可能是「本轮未记变更」），
靠标题区分：正常是「无变化」，异常是「数据异常已冻结（河北联通）」。

## GitHub Actions 里配

仓库 → Settings → Secrets and variables → Actions：

| 类型 | 名称 |
|---|---|
| Secrets | `DINGTALK_WEBHOOK` `DINGTALK_SECRET` `FEISHU_WEBHOOK` `FEISHU_SECRET` `WECOM_WEBHOOK` |
| Secrets | `PUSHPLUS_TOKEN` `PUSHPLUS_TOPIC` |
| Secrets | `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `MAIL_TO` `MAIL_FROM` |
| Variables | `SMTP_SSL`（`1`=SSL/465，`0`=STARTTLS/587）、`MAIL_FROM_NAME`、`NOTIFY` |

配完跑一次 **Actions → 推送通道自检 → Run workflow**（`mode=test`），
确认手机/群里收到消息。收不到时先跑它，排除「通道坏」再查别的。

## 手动补推

通道刚配好、想把最近一轮补发一次，或当周报用：

```bash
python cloud/tariff/push_once.py                # 最近 1 天，真发（带指纹去重）
python cloud/tariff/push_once.py --days 7       # 最近 7 天（周报）
python cloud/tariff/push_once.py --date 2026-09-30
python cloud/tariff/push_once.py --dry          # 只看内容，不发送
python cloud/tariff/push_once.py --force        # 无视「无变化不推」与指纹去重
```

它与 `notify.py --test` 的分工：`--test` 发**写死的自检文案**，验「通道通不通」；
`push_once.py` 发**真实数据**，验「history.json → 渲染 → 通道」整条链路。
通道通但渲染炸掉（字段改名、空数据、编码）时，只有它能验出来。

## 排障

| 现象 | 先查这里 |
|---|---|
| 只有部分通道成功 | 看日志里各通道的说明；一个失败不影响其他，逐个修 |
| 一次都没收到，日志说「跳过」 | `--check` 看凭据是否配齐；`NOTIFY=0` / `SELF_NOTIFY=0` 是否被设过 |
| 变化了但没推送 | 内容指纹命中（同一份内容当天推过）；或 `has_change()` 认为无值得推的变化 |
| 钉钉 310000 / 飞书 sign not match | 加签算法抄反了（见上）；或时间戳单位错了（钉钉毫秒、飞书秒） |
| 企微报错但内容不长 | 企微 markdown 上限按**字节**算（4096），中文一个字 3 字节 |
| 天天推「无变化」 | 检查 `NOTIFY_ALWAYS` 是否为 1 |
