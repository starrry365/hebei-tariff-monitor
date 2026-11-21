# -*- coding: utf-8 -*-
"""河北通信资费 Model Context Protocol (MCP) Server & API Gateway

为 AI Agent (Antigravity, Claude, Cursor, Dify, Coze 等) 提供标准化 MCP 协议接入：
- 通信协议: JSON-RPC 2.0 (stdio / http)
- 暴露核心 Tools:
    1. search_tariffs (多维资费筛选与全文搜索)
    2. get_tariff_detail (单品深度档案与条款溯源)
    3. compare_tariffs (跨网横向矩阵比价)
    4. optimize_existing_subscriptions (结合已定业务的存量重构与换代精算)
    5. audit_fine_print (隐形条款排雷与限速/违约金审查)
    6. simulate_tco (全生命周期总持有成本测算)
    7. get_market_overview (四网大盘宏观统计)
- 暴露 Resources:
    tariff://market/overview (四网大盘概览)
    tariff://carriers/summary (运营商体量与基线)
- 暴露 Prompts:
    bill-optimizer (账单降费精算师)
    broadband-picker (全家宽带选型助手)
"""

import sys
import os
import json
import logging
import argparse
from typing import Dict, Any, List

# Ensure module path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from mcp.engine import TariffEngine

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s [%(levelname)s] %(message)s")

SERVER_NAME = "hebei-tariff-mcp"
SERVER_VERSION = "1.0.0"

# Tool Schemas for MCP
TOOLS_DEFINITIONS = [
    {
        "name": "search_tariffs",
        "description": "多维条件筛选与全文检索河北四家运营商（移动、联通、电信、广电）在售与停售资费业务。支持月费区间、流量、通话、宽带速率、客群和地市过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "carrier": {
                    "type": "string",
                    "enum": ["all", "move", "unicom", "telecom", "cbn", "移动", "联通", "电信", "广电"],
                    "description": "指定运营商代码或名称，默认 all (全网四家)",
                    "default": "all"
                },
                "keyword": {
                    "type": "string",
                    "description": "关键词搜索（匹配套餐名、权益、报备号、目标人群，如'芒果卡'、'FTTR'、'校园'）"
                },
                "fee_min": {"type": "number", "description": "最低月费（元）"},
                "fee_max": {"type": "number", "description": "最高月费（元）"},
                "traffic_gb_min": {"type": "number", "description": "最低通用流量（GB）"},
                "traffic_gb_max": {"type": "number", "description": "最高通用流量（GB）"},
                "call_min": {"type": "integer", "description": "最低国内通话时长（分钟）"},
                "has_broadband": {"type": "boolean", "description": "是否要求含宽带（含线路或提速组网）"},
                "broadband_speed_min": {"type": "integer", "description": "最低下行速率（Mbps，如 100, 300, 500, 1000, 2000）"},
                "target_segment": {
                    "type": "string",
                    "enum": ["校园", "敬老", "骑手", "爱心", "政企"],
                    "description": "特定细分客群"
                },
                "city": {"type": "string", "description": "地市归属，如'石家庄'、'保定'、'唐山'、'全国'"},
                "status": {
                    "type": "string",
                    "enum": ["在售", "已停售", "全部"],
                    "default": "在售",
                    "description": "业务销售状态"
                },
                "limit": {"type": "integer", "default": 10, "description": "返回条数上限 (1~50)"},
                "sort_by": {
                    "type": "string",
                    "enum": ["fee_asc", "fee_desc", "traffic_desc", "per_val_desc", "on_date_desc"],
                    "default": "fee_asc",
                    "description": "排序方式：fee_asc(月费升序), traffic_desc(流量降序), per_val_desc(每元流量性价比降序), on_date_desc(上架时间最新)"
                }
            }
        }
    },
    {
        "name": "get_tariff_detail",
        "description": "查询指定业务的官方完整备案档案。包括详细资费组成、违约责任扣费公式、有效期续展规则及退订途径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "业务标识符：官方报备号（如 26HE201458）、套餐名称或内部 ID"
                }
            },
            "required": ["identifier"]
        }
    },
    {
        "name": "compare_tariffs",
        "description": "跨运营商或同运营商多款资费横向横截面对比。生成包含月费、流量、通话、宽带、副卡、合约期及每元性价比的横向对比矩阵。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "待对比的业务标识列表（2~5 款套餐的报备号或名称）"
                }
            },
            "required": ["identifiers"]
        }
    },
    {
        "name": "optimize_existing_subscriptions",
        "description": "结合用户当前已订购的存量业务（主套餐、独立宽带、副卡、叠加包），自动诊断现有资费病灶（碎片化孤岛支付、历史老套餐溢价、副卡冗余月租），并量身定制三大演变方案（方案A: 同网无痛降费、方案B: 预算守恒体验跃升、方案C: 跨网携转降维打击），计算具体节省金额与掌厅办理指引。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "carrier": {
                    "type": "string",
                    "enum": ["move", "unicom", "telecom", "cbn", "移动", "联通", "电信", "广电"],
                    "description": "用户当前所用的运营商"
                },
                "current_subscriptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["main_plan", "broadband", "sub_card", "addon_pack"], "description": "业务类型"},
                            "name": {"type": "string", "description": "业务名称或简述，如'5G畅享128元'、'200M单宽带'"},
                            "monthly_fee": {"type": "number", "description": "该项当前每月实际花费金额（元）"},
                            "count": {"type": "integer", "default": 1, "description": "数量（如副卡张数）"}
                        },
                        "required": ["name", "monthly_fee"]
                    },
                    "description": "当前已开通的各项业务列表"
                },
                "monthly_actual_usage": {
                    "type": "object",
                    "properties": {
                        "avg_used_gb": {"type": "number", "description": "每月实际平均消耗流量（GB）"},
                        "avg_used_call_min": {"type": "integer", "description": "每月实际通话分钟数"},
                        "needs_broadband": {"type": "boolean", "description": "是否需要家用宽带，默认根据存量推断"}
                    },
                    "description": "用户每月真实用量特征（用于判断是否存在性能过剩或不足）"
                },
                "city": {
                    "type": "string",
                    "default": "石家庄",
                    "description": "用户所在地市，如'石家庄'、'保定'、'唐山'等"
                },
                "willing_to_switch": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否愿意在性价比极大提升的前提下考虑携号转网"
                }
            },
            "required": ["carrier", "current_subscriptions"]
        }
    },
    {
        "name": "audit_fine_print",
        "description": "对特定资费条款进行专项排雷与隐蔽条款合规审查。扫描达量降速阈值、免流死角（是否免广告/直播/热点）、提前退订违约金公式与到期回弹原价机制。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "资费报备号、套餐名称或 ID"
                }
            },
            "required": ["identifier"]
        }
    },
    {
        "name": "simulate_tco",
        "description": "全生命周期总持有成本测算（Total Cost of Ownership）。计算在 12、24 或 36 个月周期内的全部真实支出（计入一次性宽带调测费、折扣期满原价回弹、副卡叠加月租）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "资费报备号或套餐名"},
                "duration_months": {"type": "integer", "default": 24, "enum": [12, 24, 36], "description": "测算周期（月）"},
                "sub_card_count": {"type": "integer", "default": 0, "description": "家庭需要办理的副卡总张数"}
            },
            "required": ["identifier"]
        }
    },
    {
        "name": "get_market_overview",
        "description": "获取河北四家运营商在售大盘的宏观统计分布，包括各价格带资费数量、宽带速率分档覆盖率及运营商份额。",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

RESOURCES_DEFINITIONS = [
    {
        "uri": "tariff://market/overview",
        "name": "全网资费大盘概览",
        "description": "四家运营商在售资费总数、价格带分档及宽带覆盖率的实时统计",
        "mimeType": "application/json"
    },
    {
        "uri": "tariff://carriers/summary",
        "name": "四网基本盘与数据源",
        "description": "中国移动、中国联通、中国电信、中国广电四网公示资费体量与数据基线",
        "mimeType": "application/json"
    }
]

PROMPTS_DEFINITIONS = [
    {
        "name": "bill-optimizer",
        "description": "话费账单降费专家：引导用户给出当前资费与消费情况，输出专业的现状病灶诊断与三套降费升级方案",
        "arguments": [
            {"name": "user_bill_text", "description": "用户自己描述的话费账单或现有套餐情况", "required": False}
        ]
    },
    {
        "name": "broadband-picker",
        "description": "家庭千兆宽带选型向导：根据户型大小、常住人口及用网需求推荐最具性价比的全屋光网或融合资费",
        "arguments": [
            {"name": "city", "description": "所在地市", "required": True},
            {"name": "budget", "description": "每月预算上限", "required": False}
        ]
    }
]


class MCPServer:
    """标准 MCP JSON-RPC 2.0 服务器"""

    def __init__(self):
        self.engine = TariffEngine.get_instance()

    def handle_request(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {}
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION
                    }
                }
            }

        elif method == "notifications/initialized":
            return None

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS_DEFINITIONS
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            try:
                res_data = self.execute_tool(tool_name, args)
                text_content = json.dumps(res_data, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": text_content}
                        ],
                        "structuredContent": res_data
                    }
                }
            except Exception as e:
                logging.exception(f"Error calling tool {tool_name}")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32603,
                        "message": f"Tool execution failed: {str(e)}"
                    }
                }

        elif method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": RESOURCES_DEFINITIONS
                }
            }

        elif method == "resources/read":
            uri = params.get("uri")
            if uri == "tariff://market/overview":
                content = json.dumps(self.engine.get_market_overview(), ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [{"uri": uri, "mimeType": "application/json", "text": content}]
                    }
                }
            elif uri == "tariff://carriers/summary":
                summary = {
                    "move": {"name": "中国移动", "source": "河北移动掌厅及资费公示专区"},
                    "unicom": {"name": "中国联通", "source": "中国联通河北公示专区"},
                    "telecom": {"name": "中国电信", "source": "中国电信河北资费专区"},
                    "cbn": {"name": "中国广电", "source": "中国广电河北公示"}
                }
                content = json.dumps(summary, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [{"uri": uri, "mimeType": "application/json", "text": content}]
                    }
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Resource not found: {uri}"}
                }

        elif method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": PROMPTS_DEFINITIONS
                }
            }

        elif method == "prompts/get":
            prompt_name = params.get("name")
            p_args = params.get("arguments", {})
            if prompt_name == "bill-optimizer":
                user_msg = p_args.get("user_bill_text") or "我想优化我的手机话费和宽带支出，请帮我诊断并给出方案。"
                prompt_text = (
                    "你是一名拥有工信部通信资费备案库知识的电信降费专家。\n"
                    "请使用 optimize_existing_subscriptions 工具对用户的存量业务进行断层扫描，\n"
                    "找出病灶（如碎片化支付、老套餐高价低配溢价、副卡多余月租），\n"
                    "并给出 3 种清晰的演进路径：方案A(同网降费平移)、方案B(预算守恒体验跃升)、方案C(跨网携转降维打击)。\n"
                    f"用户输入情况：{user_msg}"
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "description": "账单降费专家系统提示词",
                        "messages": [
                            {"role": "user", "content": {"type": "text", "text": prompt_text}}
                        ]
                    }
                }
            elif prompt_name == "broadband-picker":
                city = p_args.get("city", "石家庄")
                budget = p_args.get("budget", "100")
                prompt_text = (
                    f"请帮助在【{city}】寻找每月预算约【{budget}元】以内的最优千兆宽带/FTTR全屋光网或融合资费。\n"
                    "请优先调用 search_tariffs 工具查找含 500M~1000M 宽带的在售套餐，\n"
                    "并调用 audit_fine_print 排查是否有隐性宽带调测费或苛刻合约期限制。"
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "description": "千兆宽带选型助手",
                        "messages": [
                            {"role": "user", "content": {"type": "text", "text": prompt_text}}
                        ]
                    }
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Prompt not found: {prompt_name}"}
                }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }

    def execute_tool(self, name: str, args: dict) -> Any:
        if name == "search_tariffs":
            return self.engine.search_tariffs(
                carrier=args.get("carrier", "all"),
                keyword=args.get("keyword", ""),
                fee_min=args.get("fee_min"),
                fee_max=args.get("fee_max"),
                traffic_gb_min=args.get("traffic_gb_min"),
                traffic_gb_max=args.get("traffic_gb_max"),
                call_min=args.get("call_min"),
                has_broadband=args.get("has_broadband"),
                broadband_speed_min=args.get("broadband_speed_min"),
                target_segment=args.get("target_segment"),
                city=args.get("city"),
                status=args.get("status", "在售"),
                limit=args.get("limit", 10),
                sort_by=args.get("sort_by", "fee_asc")
            )
        elif name == "get_tariff_detail":
            return self.engine.get_tariff_detail(args["identifier"])
        elif name == "compare_tariffs":
            return self.engine.compare_tariffs(args["identifiers"])
        elif name == "optimize_existing_subscriptions":
            return self.engine.optimize_existing_subscriptions(
                carrier=args["carrier"],
                current_subscriptions=args["current_subscriptions"],
                monthly_actual_usage=args.get("monthly_actual_usage"),
                city=args.get("city", "石家庄"),
                willing_to_switch=args.get("willing_to_switch", True)
            )
        elif name == "audit_fine_print":
            return self.engine.audit_fine_print(args["identifier"])
        elif name == "simulate_tco":
            return self.engine.simulate_tco(
                identifier=args["identifier"],
                duration_months=args.get("duration_months", 24),
                sub_card_count=args.get("sub_card_count", 0)
            )
        elif name == "get_market_overview":
            return self.engine.get_market_overview()
        else:
            raise ValueError(f"Unknown tool name: {name}")

    def run_stdio(self):
        """标准 stdio 交互循环"""
        logging.info("Starting Hebei Tariff MCP Server on stdio...")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                logging.error(f"Malformed JSON: {line}")
            except Exception as e:
                logging.exception("Fatal loop exception")

    def run_self_test(self):
        """内置自检测试模式"""
        print("=" * 70)
        print("🧪 河北通信资费 MCP Server 全功能自检")
        print("=" * 70)

        # 1. initialize
        init_res = self.handle_request({"id": 1, "method": "initialize", "params": {}})
        print(f"[1] MCP Initialize: OK -> Protocol {init_res['result']['protocolVersion']}")

        # 2. tools/list
        tools_res = self.handle_request({"id": 2, "method": "tools/list", "params": {}})
        tools = tools_res["result"]["tools"]
        print(f"[2] MCP Tools List: OK -> {len(tools)} 个标准工具可用")
        for t in tools:
            print(f"    • {t['name']:30s} : {t['description'][:38]}...")

        # 3. tools/call: search_tariffs
        call_search = self.handle_request({
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_tariffs",
                "arguments": {"fee_max": 99, "has_broadband": True, "broadband_speed_min": 1000, "city": "石家庄", "limit": 2}
            }
        })
        search_ret = call_search["result"]["structuredContent"]
        print(f"[3] Tool Call search_tariffs: OK -> 匹配到 {search_ret['total_matches']} 条在售千兆宽带资费")

        # 4. tools/call: optimize_existing_subscriptions
        call_opt = self.handle_request({
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "optimize_existing_subscriptions",
                "arguments": {
                    "carrier": "move",
                    "current_subscriptions": [
                        {"type": "main_plan", "name": "5G畅享128元套餐", "monthly_fee": 128},
                        {"type": "broadband", "name": "200M单宽带", "monthly_fee": 30},
                        {"type": "sub_card", "name": "副卡", "monthly_fee": 10}
                    ],
                    "city": "石家庄"
                }
            }
        })
        opt_ret = call_opt["result"]["structuredContent"]
        print(f"[4] Tool Call optimize_existing_subscriptions: OK")
        print(f"    - 现状总支出: {opt_ret['current_status']['total_monthly_spend']}")
        print(f"    - 方案A (同网平移): {opt_ret['recommendation_paths']['path_a_same_carrier_downgrade']['target_package']} ({opt_ret['recommendation_paths']['path_a_same_carrier_downgrade']['annual_saving']})")
        print(f"    - 方案B (体验翻倍): {opt_ret['recommendation_paths']['path_b_equal_budget_max_value']['target_package']}")
        print(f"    - 方案C (跨网携转): {opt_ret['recommendation_paths']['path_c_cross_carrier_mnp']['target_package']} ({opt_ret['recommendation_paths']['path_c_cross_carrier_mnp']['annual_saving']})")

        # 5. tools/call: audit_fine_print
        call_audit = self.handle_request({
            "id": 5,
            "method": "tools/call",
            "params": {"name": "audit_fine_print", "arguments": {"identifier": "26HE201458"}}
        })
        audit_ret = call_audit["result"]["structuredContent"]
        print(f"[5] Tool Call audit_fine_print: OK -> {audit_ret['audit_verdict']}")

        # 6. resources/list & read
        res_read = self.handle_request({"id": 6, "method": "resources/read", "params": {"uri": "tariff://market/overview"}})
        overview_data = json.loads(res_read["result"]["contents"][0]["text"])
        print(f"[6] Resource Read: OK -> 全网监控 {overview_data['total_tariffs_monitored']} 条, 在售 {overview_data['total_on_sale']} 条")

        # 7. prompts/list & get
        prompt_res = self.handle_request({"id": 7, "method": "prompts/get", "params": {"name": "bill-optimizer", "arguments": {}}})
        print(f"[7] Prompt Get: OK -> {prompt_res['result']['description']}")

        print("=" * 70)
        print("✅ 自检全部通过！MCP Server 已达到工业级可用状态。")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Hebei Tariff Monitor MCP Server")
    parser.add_argument("--test", action="store_true", help="运行内部全功能自检测试")
    parser.add_argument("--http", type=int, nargs="?", const=8765, help="作为轻量 HTTP JSON API 服务器运行 (指定端口，默认 8765)")
    args = parser.parse_args()

    server = MCPServer()

    if args.test:
        server.run_self_test()
    elif args.http:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        port = args.http

        class APIHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/" or self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "healthy",
                        "server": SERVER_NAME,
                        "version": SERVER_VERSION,
                        "endpoints": ["/api/search", "/api/detail", "/api/compare", "/api/optimize", "/api/overview"]
                    }, ensure_ascii=False).encode("utf-8"))
                elif self.path == "/api/overview":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps(server.engine.get_market_overview(), ensure_ascii=False).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                post_body = self.rfile.read(content_len).decode("utf-8")
                try:
                    payload = json.loads(post_body) if post_body else {}
                except Exception:
                    payload = {}

                resp_data = None
                if self.path == "/api/search":
                    resp_data = server.execute_tool("search_tariffs", payload)
                elif self.path == "/api/detail":
                    resp_data = server.execute_tool("get_tariff_detail", payload)
                elif self.path == "/api/compare":
                    resp_data = server.execute_tool("compare_tariffs", payload)
                elif self.path == "/api/optimize":
                    resp_data = server.execute_tool("optimize_existing_subscriptions", payload)
                elif self.path == "/api/audit":
                    resp_data = server.execute_tool("audit_fine_print", payload)
                elif self.path == "/api/tco":
                    resp_data = server.execute_tool("simulate_tco", payload)
                elif self.path == "/mcp":
                    resp_data = server.handle_request(payload)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(resp_data, ensure_ascii=False).encode("utf-8"))

        httpd = HTTPServer(("0.0.0.0", port), APIHandler)
        print(f"📡 Hebei Tariff API Server running on http://127.0.0.1:{port}")
        print(f"   MCP Endpoint: POST http://127.0.0.1:{port}/mcp")
        print(f"   REST Search:  POST http://127.0.0.1:{port}/api/search")
        print(f"   REST Optimize: POST http://127.0.0.1:{port}/api/optimize")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")

    else:
        server.run_stdio()


if __name__ == "__main__":
    main()
