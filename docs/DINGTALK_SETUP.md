# 钉钉推送专题

> 五通道总览见 [NOTIFY_SETUP.md](NOTIFY_SETUP.md)。这里只讲钉钉这一条路，
> 以及它**独有**的坑。

## 1. 建一个群机器人

1. 打开要接收通知的钉钉群 → 右上角 `...` → **群设置** → **智能群助手**
2. **添加机器人** → 选 **自定义（通过 Webhook 接入自定义服务）**
3. 安全设置三选一（见下一节），勾选 **我已阅读并同意**
4. 复制 **Webhook 地址**，形如：

   ```
   https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
   ```

5. 填到 `.env` 的 `DINGTALK_WEBHOOK=`，或 GitHub Secrets 的 `DINGTALK_WEBHOOK`

## 2. 安全设置怎么选

| 方式 | 要填的变量 | 评价 |
|---|---|---|
| **加签** | `DINGTALK_SECRET` | ✅ **推荐**。webhook 万一泄露，别人没有 secret 也发不进来 |
| 自定义关键词 | 不用填 | 消息里必须含该关键词。本项目推送标题含「资费巡检」，可把它设为关键词 |
| IP 白名单 | 不用填 | ❌ **别选**：GitHub Actions 的出口 IP 不固定，也不公布完整列表 |

## 3. 加签怎么算（本项目的实现）

```
timestamp = 当前毫秒时间戳（字符串）
待签串    = timestamp + "\n" + secret
签名      = base64( HMAC-SHA256(key = secret, msg = 待签串) )
请求      = webhook + "&timestamp=" + timestamp + "&sign=" + urlencode(签名)
```

对照实现（`cloud/tariff/notify.py`）：

```python
def _dingtalk_sign(secret, ts=None):
    ts = str(ts if ts is not None else round(time.time() * 1000))   # 毫秒
    sign = urllib.parse.quote_plus(base64.b64encode(
        hmac.new(secret.encode("utf-8"),
                 ("%s\n%s" % (ts, secret)).encode("utf-8"),
                 hashlib.sha256).digest()))
    return ts, sign
```

### 🔴 和飞书别搞混

| | 待签串 | HMAC 的 **key** | 时间戳单位 |
|---|---|---|---|
| 钉钉 | `ts + "\n" + secret` | **secret** | 毫秒 |
| 飞书 | `ts + "\n" + secret` | **空串** | 秒 |

「密钥当然是 HMAC 的 key」这条直觉对钉钉成立，对飞书**不成立**。
抄反的典型症状：钉钉回 `310000 sign not match`，飞书回 `code=19021`。

自证实现没抄反（用固定时间戳做确定性断言）：

```bash
python cloud/tariff/notify.py --selfcheck
# → 推送自测通过（… / 加签 / 字节截断）
```

## 4. 常见 errcode

| errcode | 含义 | 处置 |
|---|---|---|
| `0` | 成功 | — |
| `310000` | `sign not match` / 关键词不匹配 | 加签算法抄反了，或时间戳单位用错；用了关键词模式则消息里得含该词 |
| `300001` | 无效的 access_token | webhook 复制错 / 机器人被移除 |
| `300002` | 请求过于频繁 | 单机器人 20 条/分钟；本项目「一次变化只推一条」正常不会触发 |
| `300003` | 消息过长 | 本项目推送正文按 18000 字节截断，正常不会触发 |
| `400013` | 机器人已停用 | 群设置里把机器人移除重加 |

## 5. 正文长什么样

以 **markdown** 类型发送（`msgtype=markdown`），标题最多 64 字符，
正文超过上限时按 **UTF-8 字节边界**截断（不会切出半个汉字）：

```markdown
# 资费巡检 2026-09-30：新增 10 · 下线 2

- **河北移动** 5318 条 · 新增 9 · 下线 2 · 字段变更 52
- **河北联通** 8038 条 · 新增 1 · 下线 0 · 字段变更 11
...
```

## 6. 验证

```bash
# 只列通道（不发请求）
python cloud/tariff/notify.py --check

# 发一条自检消息
DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=xxx' \
DINGTALK_SECRET='SECxxxx' \
python cloud/tariff/notify.py --test

# 用真实数据渲染 + 发送（先 --dry 看内容）
python cloud/tariff/push_once.py --dry
python cloud/tariff/push_once.py
```
