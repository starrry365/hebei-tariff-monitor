# 河北通信资费 AI 智库 MCP Server & API Gateway

为大语言模型 (AI Agent) 提供对河北四家运营商（中国移动、中国联通、中国电信、中国广电）全量 **15,081 款资费业务**的标准化 Model Context Protocol (MCP) 与 RESTful API 接入。

AI Agent 通过本服务可直接具备：
1. **全维精准筛选**：按月费、通用流量、免流、通话、千兆宽带、垂直人群（校园/敬老/骑手/爱心/政企）、地市归属进行毫秒级组合检索。
2. **官方条款溯源**：查阅任意资费的工信部报备号、官方长文细则、合约在网期、违约金扣费公式与退订途径。
3. **跨网矩阵横向比价**：多款套餐并排比对，自动计算每元流量性价比（GB/元）与宽带梯次。
4. **结合已定业务重构推荐（杀手级功能）**：诊断用户现有存量账单病灶（碎片化支付、老套餐高价低配溢价、副卡多余月租），量身输出**同网无痛降费（方案A）**、**预算守恒体验跃升（方案B）**、**跨网携转降维打击（方案C）**，量化每年省钱金额并附带营业厅办理话术。
5. **隐蔽条款合规排雷**：扫描达量降速阈值、免流死角、违约责任与优惠期满原价回弹机制。
6. **全生命周期总持有成本测算（TCO）**：推演 12/24/36 个月总支出（计入宽带一次性调测费与原价回弹）。
7. **全网大盘宏观透视**：实时获取四网资费分布、价格带占比与千兆宽带覆盖率。
8. **家庭全屋千兆选型方案求解（全家桶精算）**：多卡共享+免月费副卡+千兆FTTR对比各自独立单卡开办，量化家庭年度节省金额。
9. **跨运营商竞品对标雷达（1对3矩阵）**：以任意在售/存量套餐为锚点，精准对标友商正面竞品，生成多维雷达能力分。

---

## 一、快速开始

### 1. 运行自检测试
```bash
python cloud/tariff/mcp/server.py --test
```
输出各工具自检结果，验证全量 15,081 款资费内存加载（约 1.6 秒）与检索性能（< 1 毫秒）。

### 2. 作为标准 MCP 服务运行 (stdio 模式)
```bash
python cloud/tariff/mcp/server.py
```
通过标准 JSON-RPC 2.0 在 `stdin`/`stdout` 交互，原生支持 Antigravity、Claude Desktop、Cursor 等客户端。

### 3. 作为 HTTP REST API 服务器运行
```bash
python cloud/tariff/mcp/server.py --http 8765
```
启动极轻量 HTTP 接口服务，零外部依赖（纯 Python 标准库），原生内置完整跨域预检（CORS OPTIONS 204），可直接被网页前端与 Dify 等平台无阻跨域调用，提供 RESTful 兼容端点：
- `GET  /health`：健康检查
- `GET  /api/overview`：全网资费大盘概览
- `GET / POST /api/search`：多维资费检索（支持 URL query 参数快速测试）
- `GET / POST /api/detail`：单业务详细档案
- `GET / POST /api/family`：家庭多卡选型求解
- `GET / POST /api/radar`：跨网竞品对标雷达
- `POST /api/compare`：多业务横向比价
- `POST /api/optimize`：结合已定业务的存量重构与换代精算
- `POST /api/audit`：隐形条款排雷与限速/违约金审查
- `POST /api/tco`：全生命周期综合成本模拟
- `POST /mcp`：标准 JSON-RPC 2.0 MCP 协议端点

---

## 二、在 AI 客户端中配置

### 1. Antigravity / Claude Desktop 配置
在客户端的 `claude_desktop_config.json` 或 Antigravity 插件配置中加入：

```json
{
  "mcpServers": {
    "hebei-tariff": {
      "command": "python",
      "args": [
        "d:/Work/Antigravity/hebei-tariff-monitor/cloud/tariff/mcp/server.py"
      ]
    }
  }
}
```

### 2. Dify / FastGPT 等平台通过 HTTP 接入
先启动 HTTP 模式：`python cloud/tariff/mcp/server.py --http 8765`。
在外部平台的工具/知识库配置中填入目标端点，如 `http://127.0.0.1:8765/api/optimize`。

---

## 三、核心 MCP Tools 详细接口

### 1. `search_tariffs`（全维资费筛选与检索）
```json
{
  "carrier": "all | move | unicom | telecom | cbn",
  "keyword": "FTTR / 芒果卡 / 校园",
  "fee_min": 0,
  "fee_max": 99,
  "traffic_gb_min": 30,
  "call_min": 200,
  "has_broadband": true,
  "broadband_speed_min": 1000,
  "target_segment": "校园 | 敬老 | 骑手 | 爱心 | 政企",
  "city": "石家庄",
  "status": "在售",
  "limit": 10,
  "sort_by": "fee_asc | traffic_desc | per_val_desc | on_date_desc"
}
```

### 2. `optimize_existing_subscriptions`（存量资费重构推荐）
```json
{
  "carrier": "move",
  "current_subscriptions": [
    {"type": "main_plan", "name": "5G畅享128元套餐", "monthly_fee": 128},
    {"type": "broadband", "name": "200M单宽带", "monthly_fee": 30},
    {"type": "sub_card", "name": "家庭副卡", "monthly_fee": 10}
  ],
  "monthly_actual_usage": {
    "avg_used_gb": 35,
    "avg_used_call_min": 100,
    "needs_broadband": true
  },
  "city": "石家庄",
  "willing_to_switch": true
}
```
**返回示例**：
- **现状诊断**：月花 168 元，年花 2016 元；诊断出碎片化支付、老套餐溢价与副卡冗余月租。
- **方案 A（同网平移）**：动感地带芒果卡 59元/月（含宽带与20G），每年省 ¥1308 元。
- **方案 B（体验跃升）**：中国移动直播神器卡 167.3元/月（200GB+宽带），月费不变，配置拉满。
- **方案 C（跨网携转）**：联通 5G 云智套餐 79元/月（60GB+500M宽带），每年立省 ¥1068 元。
- **办理攻略**：提供手机营业厅搜索路径与防止线下客服拦截的话术。

### 3. `get_tariff_detail`（单品深度档案）
```json
{"identifier": "26HE201458 或 套餐名称"}
```

### 4. `compare_tariffs`（横向对比矩阵）
```json
{"identifiers": ["26HE201458", "慧家118元套餐"]}
```

### 5. `audit_fine_print`（条款排雷）
```json
{"identifier": "26HE201458"}
```

### 6. `simulate_tco`（全生命周期成本测算）
```json
{
  "identifier": "慧家118元套餐",
  "duration_months": 24,
  "sub_card_count": 2
}
```

### 7. `get_market_overview`（全网宏观大盘）
无需入参，实时返回四网在售/停售总数、价格带分档及宽带速率覆盖统计。

### 8. `plan_family_bundle`（家庭多卡共享与全屋千兆光网选型精算）
```json
{
  "members_count": 3,
  "total_data_gb": 60,
  "need_broadband": true,
  "broadband_speed_min": 1000,
  "carrier_preference": "all",
  "city": "石家庄"
}
```
**返回**：最优大流量融合主卡 + 免月费副卡配置、全家总月费、人均月支出、对比独立办卡办宽带的年度节省金额。

### 9. `competitor_radar`（跨运营商正面竞品对标雷达）
```json
{
  "target_identifier": "26HE201458",
  "city": "石家庄"
}
```
**返回**：锚定套餐在其余三家运营商中最接近的价格带对标资费、五维能力雷达评分（价格力、流量力、宽带档位、合约自由度）与决策建议。
