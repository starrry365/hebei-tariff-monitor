# -*- coding: utf-8 -*-
"""河北通信资费 AI 智库数据与计算引擎 (Tariff Knowledge & Computation Engine)

组织全量四网（移动、联通、电信、广电）15,081 款资费：
- 内存级 SQLite 数据库构建与 FTS 全文索引
- 综合有效月费、宽带速率、免流定向、免月费副卡、垂直客群特征提取
- 核心算法：全维筛选、单品档案、跨网比价、存量业务重构推荐、TCO全生命周期测算、家庭组网规划、条款排雷
"""

import os
import sys
import re
import json
import time
import sqlite3
from typing import Dict, List, Any, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from audit_data import load_rows

NET_NAMES = {
    "move": "中国移动",
    "unicom": "中国联通",
    "telecom": "中国电信",
    "cbn": "中国广电"
}

BAD_VAL_RE = re.compile(r"^(0|[-—－/\\]|无.*|没有|否|不涉及|不限|暂无|无需)$")
NOTLINE_RE = re.compile(r"提速|电视|IPTV|检修|装机|调测|加速|绿色上网|调优|扩容|移机|权益|营销活动|预约办理|电子券|标识|游戏宽带|无宽带|神眼")
ASSERT_RE = re.compile(r"含宽带|宽带版|宽带包|宽带套餐|宽带融合|融合宽带|宽带流量|宽带[+＋]|单宽带|FTTR|宽带.{0,8}\d{3,4}\s*M|\d{3,4}\s*M\D{0,4}宽带|千兆宽带|光宽带|宽带.{0,4}套餐")
DESC_LINE_RE = re.compile(r"套内含宽带|包含.{0,8}\d{2,4}\s*M(?:光纤)?宽带|赠送.{0,8}\d{2,4}\s*M(?:光纤)?宽带|免费提供.{0,8}\d{2,4}\s*M(?:光纤)?宽带|(?:^|[^0-9元])\d{2,4}\s*M(?:光纤)?宽带|\b宽带\s*\d{2,4}\s*M|\b\d{2,4}\s*M宽带与直播电视|\b\d{2,4}\s*M宽带\+电视|与宽带一条|与广电高速宽带|单宽带|FTTR全屋光宽带|移动上网速率下行\s*\d{2,4}\s*M|移动上网速率下行\s*1\s*G")
SPEED_DEP_RE = re.compile(r"依赖(?:中国)?(?:移动|联通|电信|广电)?宽带|仅面向.*宽带|宽带提速至|提速一档|调测费|装机服务|检修服务|预约办理|得会员|送4个季度|送4个月|打车会员|航旅纵横|宽带终端标识|神眼|提速回馈|提速包|专属AI豆")
SPEED_RE = re.compile(r"提速|光网|组网|FTTR")
SPEED_PACK_RE = re.compile(r"提速包|提速卡|提速业务|提速至|千兆提速|加速包|提速回馈|提速优惠|提速体验")
REAL_LINE_RE = re.compile(r"含宽带|融合宽带|单宽带|宽带套餐|全家福|双百兆套餐|畅享融合")
DIR_TRAFFIC_RE = re.compile(r"(?:定向流量|专属(?:网络)?流量|定向包|免流)")
SUB_CARD_RE = re.compile(r"(?:免月费副卡|副卡0元/月)[^\d]*(\d+)\s*张?|送(?:免月费)?副卡\s*(\d+)\s*张?|可享(?:一|1)张副卡功能费优惠至0元")
CONTRACT_RE = re.compile(r"(?:承诺在网|合约期|协议期|在网期限为|协议期限|承诺使用|协议期为)\s*(\d+)\s*(个?月|年)")


def parse_promo_fee(d: dict) -> Tuple[Optional[float], Optional[float], bool]:
    f = d.get("f")
    if f is not None and str(f).strip() not in ("", "0", "0.0"):
        return None, None, False
    nm = str(d.get("n") or d.get("t") or "").strip()
    x = str(d.get("x") or "").strip()
    comb = nm + " " + x
    m = re.search(r"(?:套餐)?(?:月基本费|月费|主套餐)?(?:优惠至|降至|调整为|优惠后)\s*(\d+(?:\.\d+)?)\s*元(?:\s*/\s*(月|年))?", comb)
    if m:
        val = float(m.group(1))
        if val > 0:
            is_year = m.group(2) == "年"
            fee = round(val / 12, 1) if is_year else val
            m_orig = re.search(r"(\d+(?:\.\d+)?)\s*元(?:档位?|套餐)", nm)
            orig = float(m_orig.group(1)) if m_orig else None
            return fee, orig, is_year
    m_rate = re.search(r"(\d+(?:\.\d+)?)\s*元\s*/\s*(月|年)", nm)
    if m_rate:
        val = float(m_rate.group(1))
        if val > 0:
            is_year = m_rate.group(2) == "年"
            fee = round(val / 12, 1) if is_year else val
            return fee, None, is_year
    return None, None, False


def parse_bw_features(d: dict) -> Tuple[bool, bool, Optional[int], Optional[int]]:
    v = str(d.get("bw") or "").strip()
    n = str(d.get("n") or d.get("t") or "").strip()
    x = str(d.get("x") or "").strip()
    comb = v + " " + n + " " + x

    is_speed_pack = bool(SPEED_PACK_RE.search(n)) and not bool(REAL_LINE_RE.search(n))

    is_line = False
    if v and not BAD_VAL_RE.search(v) and not NOTLINE_RE.search(v) and not is_speed_pack:
        is_line = True
    elif "宽带" in n and not NOTLINE_RE.search(n) and ASSERT_RE.search(n) and not is_speed_pack:
        is_line = True
    elif x and not is_speed_pack:
        dep = bool(SPEED_DEP_RE.search(x) or SPEED_DEP_RE.search(n))
        if dep:
            if re.search(r"套内含宽带|与宽带一条|与广电高速宽带|5G畅享融合", x + " " + n):
                is_line = True
        elif DESC_LINE_RE.search(x):
            is_line = True

    is_speedup = bool(SPEED_RE.search(n) or SPEED_RE.search(x)) or is_speed_pack

    mb = None
    if re.search(r"2000\s*M|2\s*G|两千兆", comb, re.I):
        mb = 2000
    elif re.search(r"1500\s*M", comb, re.I):
        mb = 1500
    elif re.search(r"1000\s*M|1\s*G|千兆", comb, re.I):
        mb = 1000
    elif re.search(r"500\s*M|五百兆", comb, re.I):
        mb = 500
    elif re.search(r"300\s*M|三百兆", comb, re.I):
        mb = 300
    elif re.search(r"200\s*M|两百兆", comb, re.I):
        mb = 200
    elif re.search(r"100\s*M|百兆", comb, re.I):
        mb = 100
    elif v:
        ms = re.findall(r"\d{3,4}", v)
        if ms:
            mb = max(int(m) for m in ms)
    if mb is None:
        nm = re.findall(r"(\d{3,4})\s*M", n, re.I)
        if nm:
            mb = max(int(m) for m in nm)
    if mb is None:
        xm = re.findall(r"(?:下行|宽带|速率|提速至|包含|赠送)\D{0,6}(\d{3,4})\s*M", x, re.I)
        if xm:
            mb = max(int(m) for m in xm)

    up = None
    m_up = re.search(r"(?:上行(?:速率)?|↑)\s*(?:为|达到)?\s*(\d+)\s*(?:M|Mbps)", comb, re.I)
    if m_up:
        up = int(m_up.group(1))

    return is_line, is_speedup, mb, up


def parse_sub_cards(d: dict) -> int:
    s = str(d.get("x") or "") + " " + str(d.get("n") or d.get("t") or "")
    m = SUB_CARD_RE.search(s)
    if not m:
        return 0
    return int(m.group(1) or m.group(2) or 1)


def parse_contract_months(d: dict) -> int:
    s = str(d.get("x") or "") + " " + str(d.get("oth") or "") + " " + str(d.get("vp") or "")
    m = CONTRACT_RE.search(s)
    if not m:
        return 0
    v = int(m.group(1))
    return v * 12 if "年" in m.group(2) else v


def parse_segments(d: dict) -> str:
    s = str(d.get("n") or "") + " " + str(d.get("t") or "") + " " + str(d.get("ap") or "") + " " + str(d.get("x") or "")
    tags = []
    if re.search(r"校园|学生|青春|高校", s): tags.append("校园")
    if re.search(r"老年|银发|长者|敬老|孝心|重阳|助老", s): tags.append("敬老")
    if re.search(r"骑手|骑士|外卖|快递小哥", s): tags.append("骑手")
    if re.search(r"爱心|助残|残疾|扶贫|特惠关怀", s): tags.append("爱心")
    if re.search(r"商旅|政企|商务", s): tags.append("政企")
    return ",".join(tags)


class TariffEngine:
    """全量通信资费查询与智能计算引擎"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._init_db()
        self._load_all_tariffs()

    def _init_db(self):
        self.con.execute("""
        CREATE TABLE tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            net TEXT,
            carrier_name TEXT,
            title TEXT,
            official_name TEXT,
            report_num TEXT,
            cat TEXT,
            ty TEXT,
            fee REAL,
            eff_fee REAL,
            is_promo INTEGER,
            promo_orig REAL,
            traffic_gb REAL,
            dir_traffic INTEGER,
            call_min INTEGER,
            bw_line INTEGER,
            bw_speedup INTEGER,
            bw_down_mbps INTEGER,
            bw_up_mbps INTEGER,
            free_sub_cards INTEGER,
            contract_months INTEGER,
            segment TEXT,
            scope TEXT,
            cities TEXT,
            province_wide INTEGER,
            status TEXT,
            on_date TEXT,
            off_date TEXT,
            ownership TEXT,
            channel TEXT,
            benefits_text TEXT,
            validity_text TEXT,
            penalty_text TEXT,
            cancel_text TEXT,
            target_customer TEXT,
            raw_json TEXT
        );
        """)
        self.con.execute("CREATE INDEX idx_tariffs_net ON tariffs(net);")
        self.con.execute("CREATE INDEX idx_tariffs_status ON tariffs(status);")
        self.con.execute("CREATE INDEX idx_tariffs_eff_fee ON tariffs(eff_fee);")
        self.con.execute("CREATE INDEX idx_tariffs_traffic ON tariffs(traffic_gb);")
        self.con.execute("CREATE INDEX idx_tariffs_bw ON tariffs(bw_line, bw_down_mbps);")
        self.con.execute("CREATE INDEX idx_tariffs_report_num ON tariffs(report_num);")

    def _load_all_tariffs(self):
        t0 = time.time()
        rows_to_insert = []
        for net in ["move", "unicom", "telecom", "cbn"]:
            _, rows = load_rows(net)
            for r in rows:
                title = str(r.get("t") or r.get("n") or "").strip()
                official_name = str(r.get("n") or title).strip()
                report_num = str(r.get("r") or "").strip()
                cat = str(r.get("cat") or "").strip()
                ty = str(r.get("ty") or "").strip()

                f_val = r.get("f")
                fee = float(f_val) if f_val is not None and f_val != "" else None

                promo_fee, promo_orig, is_promo = parse_promo_fee(r)
                eff_fee = fee if fee is not None else promo_fee

                traffic_gb = r.get("g")
                dir_traffic = 1 if bool(DIR_TRAFFIC_RE.search(str(r.get("x") or "") + " " + title)) else 0

                c_val = r.get("c")
                call_min = int(c_val) if c_val and c_val != "" and c_val != "0" else 0

                bw_line, bw_speedup, bw_down, bw_up = parse_bw_features(r)
                sub_cards = parse_sub_cards(r)
                contract = parse_contract_months(r)
                segment = parse_segments(r)

                scope = str(r.get("sc") or "hb").strip()
                cty_list = r.get("cty") or []
                cities = ",".join(cty_list) if cty_list else ""
                province_wide = 1 if r.get("pw") else 0
                status = "已停售" if r.get("st") else "在售"
                on_date = str(r.get("o") or "").strip()
                off_date = str(r.get("e") or "").strip()
                ow = str(r.get("ow") or "个人").strip()
                ch = str(r.get("ch") or "").strip()

                x_text = str(r.get("x") or "").strip()
                vp_text = str(r.get("vp") or "").strip()
                rsp_text = str(r.get("rsp") or "").strip()
                ub_text = str(r.get("ub") or "").strip()
                ap_text = str(r.get("ap") or "").strip()

                raw_json_str = json.dumps(r, ensure_ascii=False)

                rows_to_insert.append((
                    net, NET_NAMES[net], title, official_name, report_num, cat, ty,
                    fee, eff_fee, 1 if is_promo else 0, promo_orig, traffic_gb, dir_traffic, call_min,
                    1 if bw_line else 0, 1 if bw_speedup else 0, bw_down, bw_up,
                    sub_cards, contract, segment, scope, cities, province_wide, status,
                    on_date, off_date, ow, ch, x_text, vp_text, rsp_text, ub_text, ap_text,
                    raw_json_str
                ))

        self.con.executemany("""
        INSERT INTO tariffs (
            net, carrier_name, title, official_name, report_num, cat, ty,
            fee, eff_fee, is_promo, promo_orig, traffic_gb, dir_traffic, call_min,
            bw_line, bw_speedup, bw_down_mbps, bw_up_mbps,
            free_sub_cards, contract_months, segment, scope, cities, province_wide, status,
            on_date, off_date, ownership, channel, benefits_text, validity_text, penalty_text, cancel_text, target_customer,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, rows_to_insert)
        self.con.commit()
        print(f"TariffEngine loaded {len(rows_to_insert)} items in {time.time() - t0:.2f}s")

    # =========================================================================
    # 核心工具 1: 全维检索与筛选 (search_tariffs)
    # =========================================================================
    def search_tariffs(self, carrier: str = "all", keyword: str = "",
                       fee_min: Optional[float] = None, fee_max: Optional[float] = None,
                       traffic_gb_min: Optional[float] = None, traffic_gb_max: Optional[float] = None,
                       call_min: Optional[int] = None,
                       has_broadband: Optional[bool] = None,
                       broadband_speed_min: Optional[int] = None,
                       free_sub_cards_min: Optional[int] = None,
                       contract_months_max: Optional[int] = None,
                       target_segment: Optional[str] = None,
                       city: Optional[str] = None,
                       status: str = "在售",
                       limit: int = 10,
                       sort_by: str = "fee_asc") -> Dict[str, Any]:
        """多维条件过滤与全文检索"""
        clauses = []
        params = []

        if carrier and carrier != "all":
            carrier_map = {"move": "move", "unicom": "unicom", "telecom": "telecom", "cbn": "cbn",
                           "移动": "move", "联通": "unicom", "电信": "telecom", "广电": "cbn"}
            net_code = carrier_map.get(carrier.lower(), carrier)
            clauses.append("net = ?")
            params.append(net_code)

        if status and status != "全部":
            clauses.append("status = ?")
            params.append(status)

        if fee_min is not None:
            clauses.append("eff_fee >= ?")
            params.append(fee_min)
        if fee_max is not None:
            clauses.append("eff_fee <= ?")
            params.append(fee_max)

        if traffic_gb_min is not None:
            clauses.append("traffic_gb >= ?")
            params.append(traffic_gb_min)
        if traffic_gb_max is not None:
            clauses.append("traffic_gb <= ?")
            params.append(traffic_gb_max)

        if call_min is not None:
            clauses.append("call_min >= ?")
            params.append(call_min)

        if has_broadband is True:
            clauses.append("(bw_line = 1 OR bw_speedup = 1)")
        elif has_broadband is False:
            clauses.append("bw_line = 0 AND bw_speedup = 0")

        if broadband_speed_min is not None:
            clauses.append("bw_down_mbps >= ?")
            params.append(broadband_speed_min)

        if free_sub_cards_min is not None:
            clauses.append("free_sub_cards >= ?")
            params.append(free_sub_cards_min)

        if contract_months_max is not None:
            clauses.append("contract_months <= ?")
            params.append(contract_months_max)

        if target_segment:
            clauses.append("segment LIKE ?")
            params.append(f"%{target_segment}%")

        if city and city not in ("全省", "全国", "不限"):
            clauses.append("(province_wide = 1 OR cities LIKE ? OR scope = 'cn')")
            params.append(f"%{city}%")

        if keyword:
            kw = f"%{keyword.strip()}%"
            clauses.append("(title LIKE ? OR benefits_text LIKE ? OR report_num LIKE ? OR target_customer LIKE ?)")
            params.extend([kw, kw, kw, kw])

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        sort_map = {
            "fee_asc": "eff_fee IS NULL, eff_fee ASC",
            "fee_desc": "eff_fee DESC",
            "traffic_desc": "traffic_gb DESC",
            "per_val_desc": "(CASE WHEN eff_fee > 0 AND traffic_gb > 0 THEN traffic_gb/eff_fee ELSE 0 END) DESC",
            "on_date_desc": "on_date DESC"
        }
        order_sql = " ORDER BY " + sort_map.get(sort_by, "eff_fee ASC")

        count_sql = f"SELECT count(*) FROM tariffs{where_sql}"
        total_count = self.con.execute(count_sql, params).fetchone()[0]

        query_sql = f"""
        SELECT id, net, carrier_name, title, official_name, report_num, cat, ty,
               fee, eff_fee, is_promo, promo_orig, traffic_gb, dir_traffic, call_min,
               bw_line, bw_speedup, bw_down_mbps, bw_up_mbps, free_sub_cards, contract_months,
               segment, status, on_date, cities, benefits_text, target_customer
        FROM tariffs{where_sql}{order_sql} LIMIT ?
        """
        params.append(limit)
        rows = self.con.execute(query_sql, params).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            fee_str = f"{d['eff_fee']}元/月" if d['eff_fee'] is not None else "未标月费"
            if d['is_promo']:
                fee_str += f" (优惠折后价, 原{d['promo_orig']}元)"
            bw_str = "不含"
            if d['bw_line']:
                bw_str = f"含宽带线路 ({d['bw_down_mbps']}M)" if d['bw_down_mbps'] else "含宽带线路"
            elif d['bw_speedup']:
                bw_str = f"宽带提速/FTTR ({d['bw_down_mbps']}M)" if d['bw_down_mbps'] else "宽带提速/组网"

            per_val = round(d['traffic_gb'] / d['eff_fee'], 2) if d['eff_fee'] and d['eff_fee'] > 0 and d['traffic_gb'] else None

            items.append({
                "id": d["id"],
                "carrier": d["carrier_name"],
                "title": d["title"],
                "report_num": d["report_num"],
                "category": d["cat"],
                "monthly_fee": fee_str,
                "raw_fee": d["eff_fee"],
                "traffic": f"{d['traffic_gb']}GB" + (" (含定向/免流)" if d['dir_traffic'] else ""),
                "call": f"{d['call_min']}分钟" if d['call_min'] else "—",
                "broadband": bw_str,
                "free_sub_cards": f"送{d['free_sub_cards']}张副卡" if d['free_sub_cards'] else "无",
                "contract": f"{d['contract_months']}个月合约" if d['contract_months'] else "无协议期",
                "segment": d["segment"] or "通用",
                "status": d["status"],
                "on_date": d["on_date"],
                "cost_per_gb": f"{per_val} GB/元" if per_val else "—",
                "brief_benefits": (d["benefits_text"][:90] + "...") if len(d["benefits_text"]) > 90 else d["benefits_text"]
            })

        return {
            "total_matches": total_count,
            "returned_count": len(items),
            "filter_summary": {
                "carrier": carrier, "keyword": keyword, "fee_range": [fee_min, fee_max],
                "traffic_min": traffic_gb_min, "has_broadband": has_broadband, "status": status
            },
            "results": items
        }

    # =========================================================================
    # 核心工具 2: 单品档案与官方条款溯源 (get_tariff_detail)
    # =========================================================================
    def get_tariff_detail(self, identifier: str) -> Dict[str, Any]:
        """获取特定业务的最全档案（含违约金计算公式与退订规则）"""
        ident = identifier.strip()
        row = self.con.execute("""
        SELECT * FROM tariffs
        WHERE report_num = ? OR title = ? OR official_name = ? OR id = ?
        LIMIT 1
        """, (ident, ident, ident, ident if ident.isdigit() else -1)).fetchone()

        if not row:
            row = self.con.execute("""
            SELECT * FROM tariffs WHERE title LIKE ? LIMIT 1
            """, (f"%{ident}%",)).fetchone()

        if not row:
            return {"found": False, "message": f"未找到与「{identifier}」匹配的资费档案"}

        d = dict(row)
        raw_obj = json.loads(d["raw_json"])

        per_val = round(d['traffic_gb'] / d['eff_fee'], 2) if d['eff_fee'] and d['eff_fee'] > 0 and d['traffic_gb'] else None

        return {
            "found": True,
            "basic_info": {
                "id": d["id"],
                "carrier": d["carrier_name"],
                "title": d["title"],
                "official_name": d["official_name"],
                "report_num": d["report_num"],
                "category": d["cat"],
                "type": d["ty"],
                "ownership": d["ownership"],
                "status": d["status"],
                "on_shelf_date": d["on_date"],
                "off_shelf_date": d["off_date"],
                "channel": d["channel"]
            },
            "pricing_and_allowances": {
                "standard_monthly_fee": d["fee"],
                "effective_monthly_fee": d["eff_fee"],
                "is_promo": bool(d["is_promo"]),
                "promo_orig_fee": d["promo_orig"],
                "general_traffic_gb": d["traffic_gb"],
                "has_directional_traffic": bool(d["dir_traffic"]),
                "call_minutes": d["call_min"],
                "cost_effectiveness_gb_per_cny": per_val
            },
            "broadband_and_devices": {
                "has_broadband_line": bool(d["bw_line"]),
                "is_speedup_or_fttr": bool(d["bw_speedup"]),
                "downlink_mbps": d["bw_down_mbps"],
                "uplink_mbps": d["bw_up_mbps"],
                "free_sub_cards": d["free_sub_cards"]
            },
            "restrictions_and_rules": {
                "contract_months": d["contract_months"],
                "target_audience": d["target_customer"] or "面向个人/大众客户",
                "segment_tags": d["segment"].split(",") if d["segment"] else [],
                "geographical_scope": "全省通用" if d["province_wide"] else (d["cities"] or d["scope"]),
                "benefits_clause": d["benefits_text"],
                "validity_and_renewal_clause": d["validity_text"] or "按月自动续订",
                "penalty_and_breach_clause": d["penalty_text"] or "未明确约定特定违约金，按一般入网协议执行",
                "cancellation_procedure": d["cancel_text"] or "可通过营业厅或客服热线办理退订"
            },
            "raw_item": raw_obj
        }

    # =========================================================================
    # 核心工具 3: 跨运营商横向比价 (compare_tariffs)
    # =========================================================================
    def compare_tariffs(self, identifiers: List[str]) -> Dict[str, Any]:
        """多款资费横向横截面对比矩阵"""
        details = []
        for ident in identifiers[:5]:
            dt = self.get_tariff_detail(ident)
            if dt.get("found"):
                details.append(dt)

        if not details:
            return {"error": "未找到任何可对比的有效资费"}

        matrix = []
        for dt in details:
            b = dt["basic_info"]
            p = dt["pricing_and_allowances"]
            bd = dt["broadband_and_devices"]
            r = dt["restrictions_and_rules"]

            matrix.append({
                "title": b["title"],
                "carrier": b["carrier"],
                "effective_fee": f"{p['effective_monthly_fee']}元/月" if p['effective_monthly_fee'] is not None else "—",
                "traffic": f"{p['general_traffic_gb']}GB" + (" (含定向)" if p['has_directional_traffic'] else ""),
                "calls": f"{p['call_minutes']}分钟" if p['call_minutes'] else "—",
                "broadband": f"{bd['downlink_mbps']}M" if bd['downlink_mbps'] else ("含宽带" if bd['has_broadband_line'] else "无"),
                "sub_cards": f"送{bd['free_sub_cards']}张" if bd['free_sub_cards'] else "无",
                "contract": f"{r['contract_months']}个月" if r['contract_months'] else "无协议",
                "cost_per_gb": f"{p['cost_effectiveness_gb_per_cny']} GB/元" if p['cost_effectiveness_gb_per_cny'] else "—"
            })

        valid_fees = [(m["title"], dt["pricing_and_allowances"]["effective_monthly_fee"]) for m, dt in zip(matrix, details) if dt["pricing_and_allowances"]["effective_monthly_fee"] is not None]
        lowest_fee = min(valid_fees, key=lambda x: x[1]) if valid_fees else None

        valid_traffic = [(m["title"], dt["pricing_and_allowances"]["general_traffic_gb"] or 0) for m, dt in zip(matrix, details)]
        max_traffic = max(valid_traffic, key=lambda x: x[1]) if valid_traffic else None

        return {
            "compared_count": len(matrix),
            "matrix": matrix,
            "verdict": {
                "lowest_price": lowest_fee[0] if lowest_fee else "—",
                "max_data": max_traffic[0] if max_traffic else "—"
            }
        }

    # =========================================================================
    # 核心工具 4: 结合已定业务的优化与换代推荐 (optimize_existing_subscriptions)
    # =========================================================================
    def optimize_existing_subscriptions(self,
                                        carrier: str,
                                        current_subscriptions: List[Dict[str, Any]],
                                        monthly_actual_usage: Optional[Dict[str, Any]] = None,
                                        city: str = "石家庄",
                                        willing_to_switch: bool = True) -> Dict[str, Any]:
        """存量资费重构精算师：诊断现状病灶、输出同网平移/预算守恒/跨网携转三大演变方案"""
        total_monthly_spend = sum(float(item.get("monthly_fee", 0)) for item in current_subscriptions)
        has_current_broadband = any("宽带" in item.get("name", "") or item.get("type") == "broadband" for item in current_subscriptions)
        sub_card_count = sum(int(item.get("count", 1)) for item in current_subscriptions if "副卡" in item.get("name", "") or item.get("type") == "sub_card")

        actual_gb = (monthly_actual_usage or {}).get("avg_used_gb", 30)
        actual_calls = (monthly_actual_usage or {}).get("avg_used_call_min", 100)
        needs_bb = (monthly_actual_usage or {}).get("needs_broadband", has_current_broadband)

        pathologies = []
        if has_current_broadband and len(current_subscriptions) > 1:
            pathologies.append("【碎片化支付病灶】主卡移动套餐与家用宽带独立扣费，未享受融合资费补贴与合包优惠。")
        if total_monthly_spend >= 120 and actual_gb <= 30:
            pathologies.append("【代际老套餐溢价病灶】现有主套餐可能为早期 4G/旧 5G 资费，单价高、通用流量少，存在严重溢价。")
        if any(item.get("monthly_fee", 0) > 0 and ("副卡" in item.get("name", "") or item.get("type") == "sub_card") for item in current_subscriptions):
            pathologies.append("【副卡冗余支出病灶】副卡仍按 10元/月缴纳功能费，而当前新融合资费已标配赠送 1~2 张免月费副卡。")

        carrier_map = {"move": "move", "unicom": "unicom", "telecom": "telecom", "cbn": "cbn",
                       "移动": "move", "联通": "unicom", "电信": "telecom", "广电": "cbn"}
        cur_net = carrier_map.get(carrier.lower(), "move")

        def query_real_package(target_net, fee_min, fee_max, need_broadband, sort_mode="fee_asc"):
            sql = """
            SELECT * FROM tariffs
            WHERE status='在售'
              AND net = ?
              AND (cat='套餐' OR ty='套餐' OR title LIKE '%套餐%' OR title LIKE '%融合%')
              AND eff_fee IS NOT NULL AND eff_fee >= ? AND eff_fee <= ?
            """
            params = [target_net, fee_min, fee_max]
            if actual_gb and actual_gb > 5:
                sql += " AND traffic_gb >= ?"
                params.append(min(15.0, actual_gb * 0.5))
            if need_broadband:
                sql += " AND (bw_line = 1 OR bw_speedup = 1)"
            if city and city not in ("全省", "全国", "不限"):
                sql += " AND (province_wide = 1 OR cities LIKE ? OR scope = 'cn')"
                params.append(f"%{city}%")
            if sort_mode == "fee_asc":
                sql += " ORDER BY eff_fee ASC, traffic_gb DESC LIMIT 3"
            else:
                sql += " ORDER BY traffic_gb DESC, eff_fee ASC LIMIT 3"
            return [dict(r) for r in self.con.execute(sql, params).fetchall()]

        # 方案 A: 同网降费 (找费用更低但在售的主套餐/融合)
        target_a_fee_max = max(39, total_monthly_spend - 10 if total_monthly_spend > 50 else total_monthly_spend)
        plan_a_candidates = query_real_package(cur_net, fee_min=39, fee_max=target_a_fee_max, need_broadband=needs_bb, sort_mode="fee_asc")
        plan_a = plan_a_candidates[0] if plan_a_candidates else None

        # 方案 B: 预算守恒，体验跃升 (花费基本一致，流量更高，千兆宽带)
        plan_b_candidates = query_real_package(cur_net, fee_min=total_monthly_spend * 0.85, fee_max=total_monthly_spend * 1.15, need_broadband=True, sort_mode="traffic_desc")
        plan_b = plan_b_candidates[0] if plan_b_candidates else None

        # 方案 C: 跨网携转 (其余三网最低成本在售融合)
        other_nets = [n for n in ["unicom", "telecom", "cbn", "move"] if n != cur_net]
        plan_c_candidates = []
        for o_net in other_nets:
            c_rows = query_real_package(o_net, fee_min=39, fee_max=max(49, total_monthly_spend * 0.8), need_broadband=needs_bb, sort_mode="fee_asc")
            if c_rows:
                plan_c_candidates.append(c_rows[0])

        plan_c = min(plan_c_candidates, key=lambda x: x["eff_fee"]) if plan_c_candidates else None

        def fmt_row(r):
            if not r:
                return None
            bw_str = f"含宽带 ({r['bw_down_mbps']}M)" if r['bw_down_mbps'] else ("含宽带线路" if r['bw_line'] else "不含")
            cards_str = f"送{r['free_sub_cards']}张副卡" if r['free_sub_cards'] else "无副卡赠送"
            return {
                "carrier": r["carrier_name"],
                "title": r["title"],
                "report_num": r["report_num"],
                "monthly_fee": f"{r['eff_fee']}元/月",
                "raw_fee": r["eff_fee"],
                "traffic": f"{r['traffic_gb']}GB",
                "broadband": bw_str,
                "sub_cards": cards_str
            }

        fa = fmt_row(plan_a)
        fb = fmt_row(plan_b)
        fc = fmt_row(plan_c)

        savings_a = (total_monthly_spend - fa["raw_fee"]) * 12 if fa else 0
        savings_c = (total_monthly_spend - fc["raw_fee"]) * 12 if fc else 0

        return {
            "current_status": {
                "carrier": NET_NAMES.get(cur_net, carrier),
                "total_monthly_spend": f"{total_monthly_spend}元/月",
                "annual_spend": f"{total_monthly_spend * 12}元/年",
                "has_broadband": has_current_broadband,
                "sub_card_count": sub_card_count,
                "diagnosed_pathologies": pathologies
            },
            "recommendation_paths": {
                "path_a_same_carrier_downgrade": {
                    "name": "方案 A：同运营商无痛降费（零摩擦平移）",
                    "target_package": fa["title"] if fa else "暂无合适同网降费套餐",
                    "monthly_fee": fa["monthly_fee"] if fa else "—",
                    "entitlements": f"流量 {fa['traffic']} · 宽带 {fa['broadband']} · 副卡 {fa['sub_cards']}" if fa else "—",
                    "annual_saving": f"每年省下 ¥{round(savings_a, 1)} 元" if savings_a > 0 else "支出持平",
                    "handling_note": "无需换卡、无需携号转网、宽带原机平移，通过掌上营业厅APP或拨打客服热线直接转为在售新套餐。"
                },
                "path_b_equal_budget_max_value": {
                    "name": "方案 B：预算守恒，体验跃升（加量不加价）",
                    "target_package": fb["title"] if fb else "暂无合适体验跃升套餐",
                    "monthly_fee": fb["monthly_fee"] if fb else "—",
                    "entitlements": f"流量 {fb['traffic']} · 宽带 {fb['broadband']} · 副卡 {fb['sub_cards']}" if fb else "—",
                    "upgrade_highlights": "保持当前月支出基本不变的前提下，宽带升级为 1000M FTTR 全屋千兆光网，流量配置翻倍。"
                },
                "path_c_cross_carrier_mnp": {
                    "name": "方案 C：跨网携号转网降维打击（极致省钱）",
                    "target_package": f"[{fc['carrier']}] {fc['title']}" if fc else "暂无更低跨网资费",
                    "monthly_fee": fc["monthly_fee"] if fc else "—",
                    "entitlements": f"流量 {fc['traffic']} · 宽带 {fc['broadband']} · 副卡 {fc['sub_cards']}" if fc else "—",
                    "annual_saving": f"每年立省 ¥{round(savings_c, 1)} 元" if savings_c > 0 else "—",
                    "migration_notice": "需确认现有主号无在网协议/未履约终端合约，前往转入方营业厅携入办理新千兆融合。"
                }
            },
            "action_guide": {
                "in_carrier_handling": f"登录【{NET_NAMES.get(cur_net, '运营商')}手机营业厅APP】-> 搜索资费名称或报备号办理。如遇在线客服拦截，出示官方公示专区在售证明，申述工信部老用户自由选套餐权益。",
                "mnp_check": "发送短信查询携转资格：发送「CXXZ#姓名#身份证号」至当前运营商客服号（移动10086/联通10010/电信10001/广电10099）。"
            }
        }

    # =========================================================================
    # 核心工具 5: 条款排雷与合规审查 (audit_fine_print)
    # =========================================================================
    def audit_fine_print(self, identifier: str) -> Dict[str, Any]:
        """对资费条款进行专项排雷：限速规则、免流死角、违约金扣款公式、回弹原价"""
        dt = self.get_tariff_detail(identifier)
        if not dt.get("found"):
            return dt

        d = dt["basic_info"]
        r = dt["restrictions_and_rules"]
        x = r["benefits_clause"]
        rsp = r["penalty_and_breach_clause"]
        vp = r["validity_and_renewal_clause"]
        comb = x + " " + rsp + " " + vp

        traps = []

        m_speed = re.search(r"(?:达量|超出后|降至|限速为?)\s*(\d+(?:\.\d+)?)\s*(?:Mbps|M|Kbps|K)", comb)
        if m_speed:
            traps.append({
                "type": "达量限速陷阱",
                "risk_level": "中",
                "finding": f"套餐含达量降速机制：{m_speed.group(0)}。降速后网页/高清视频将出现明显卡顿。",
                "quote": m_speed.group(0)
            })

        if dt["pricing_and_allowances"]["has_directional_traffic"]:
            traps.append({
                "type": "免流死角风险",
                "risk_level": "低",
                "finding": "官方定向流量通常不包含 APP 内弹幕、第三方开屏广告、直播外链及手机热点分享，使用时需谨防消耗通用流量。",
                "quote": "包含定向流量"
            })

        m_rsp = re.search(r"(?:违约责任|违约金|未履约|补交|按月收取|退订需缴纳)[^。；\n]*", rsp)
        if m_rsp and len(m_rsp.group(0).strip()) > 5:
            traps.append({
                "type": "提前解约违约金条款",
                "risk_level": "高",
                "finding": f"合约期内提前退订/携转需承担解约金责任：{m_rsp.group(0)}",
                "quote": m_rsp.group(0)
            })

        if dt["pricing_and_allowances"]["is_promo"] or re.search(r"优惠期|活动期|到期后|恢复为", comb):
            traps.append({
                "type": "优惠期满原价回弹",
                "risk_level": "中",
                "finding": f"当前展示费用为优惠折扣价，优惠到期后将恢复为原价（原档位：{dt['pricing_and_allowances']['promo_orig_fee'] or '标准月费'}元），请注意优惠到期时间。",
                "quote": "优惠活动资费"
            })

        return {
            "title": d["title"],
            "carrier": d["carrier"],
            "report_num": d["report_num"],
            "audit_verdict": f"共扫描发现 {len(traps)} 处需要特别注意的条款细则" if traps else "条款较为规整透明，未发现苛刻违约或隐蔽限速条款",
            "risk_items": traps,
            "raw_clauses": {
                "penalty": rsp,
                "validity": vp
            }
        }

    # =========================================================================
    # 核心工具 6: 全生命周期总持有成本测算 (simulate_tco)
    # =========================================================================
    def simulate_tco(self, identifier: str, duration_months: int = 24, sub_card_count: int = 0) -> Dict[str, Any]:
        """测算 12/24/36 个月全生命周期的综合实际支出（含调测费与到期回弹）"""
        dt = self.get_tariff_detail(identifier)
        if not dt.get("found"):
            return dt

        p = dt["pricing_and_allowances"]
        bd = dt["broadband_and_devices"]
        r = dt["restrictions_and_rules"]

        eff_fee = p["effective_monthly_fee"] or 0
        orig_fee = p["promo_orig_fee"] or eff_fee

        install_fee = 0
        if bd["has_broadband_line"]:
            m_fee = re.search(r"(?:调测费|安装费)\s*(\d+)\s*元", r["benefits_clause"])
            install_fee = int(m_fee.group(1)) if m_fee else 100

        free_cards = bd["free_sub_cards"] or 0
        chargeable_sub_cards = max(0, sub_card_count - free_cards)
        sub_card_monthly = chargeable_sub_cards * 10

        promo_months = min(duration_months, 12 if p["is_promo"] else duration_months)
        normal_months = duration_months - promo_months

        spend_promo = promo_months * eff_fee
        spend_normal = normal_months * orig_fee
        sub_card_total = sub_card_monthly * duration_months
        total_tco = spend_promo + spend_normal + install_fee + sub_card_total

        return {
            "title": dt["basic_info"]["title"],
            "carrier": dt["basic_info"]["carrier"],
            "simulation_period_months": duration_months,
            "sub_cards_simulated": sub_card_count,
            "cost_breakdown": {
                "monthly_base_spend": f"{eff_fee}元/月 (前{promo_months}个月)" + (f", 后续恢复{orig_fee}元/月" if normal_months > 0 else ""),
                "one_off_install_fee": f"{install_fee}元 (一次性宽带调测费)",
                "sub_card_fee_monthly": f"{sub_card_monthly}元/月 ({free_cards}张免月费, {chargeable_sub_cards}张按10元计)",
                "total_tco_spend": f"¥{round(total_tco, 1)} 元",
                "amortized_monthly_spend": f"¥{round(total_tco / duration_months, 1)} 元/月"
            }
        }

    # =========================================================================
    # 核心工具 7: 全网大盘透视与宏观统计 (get_market_overview)
    # =========================================================================
    def get_market_overview(self) -> Dict[str, Any]:
        """获取四网资费分布大盘统计"""
        carrier_counts = {}
        for net, c_name in NET_NAMES.items():
            cnt = self.con.execute("SELECT count(*) FROM tariffs WHERE net=?", (net,)).fetchone()[0]
            on_sale = self.con.execute("SELECT count(*) FROM tariffs WHERE net=? AND status='在售'", (net,)).fetchone()[0]
            carrier_counts[c_name] = {"total": cnt, "on_sale": on_sale}

        fee_distribution = {
            "0元/免费包": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee=0").fetchone()[0],
            "10元以下": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee>0 AND eff_fee<=10").fetchone()[0],
            "10~30元": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee>10 AND eff_fee<=30").fetchone()[0],
            "30~60元": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee>30 AND eff_fee<=60").fetchone()[0],
            "60~100元": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee>60 AND eff_fee<=100").fetchone()[0],
            "100元以上": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee>100").fetchone()[0],
            "未标月费": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND eff_fee IS NULL").fetchone()[0]
        }

        bw_distribution = {
            "千兆及以上(1000M+)": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND bw_down_mbps>=1000").fetchone()[0],
            "500M~999M": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND bw_down_mbps>=500 AND bw_down_mbps<1000").fetchone()[0],
            "300M~499M": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND bw_down_mbps>=300 AND bw_down_mbps<500").fetchone()[0],
            "100M~299M": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND bw_down_mbps>=100 AND bw_down_mbps<300").fetchone()[0],
            "提速/组网/FTTR": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售' AND bw_speedup=1").fetchone()[0]
        }

        return {
            "total_tariffs_monitored": self.con.execute("SELECT count(*) FROM tariffs").fetchone()[0],
            "total_on_sale": self.con.execute("SELECT count(*) FROM tariffs WHERE status='在售'").fetchone()[0],
            "carrier_breakdown": carrier_counts,
            "fee_distribution_on_sale": fee_distribution,
            "broadband_distribution_on_sale": bw_distribution
        }

    # =========================================================================
    # 核心工具 8: 家庭多卡共享与千兆选型方案求解 (plan_family_bundle)
    # =========================================================================
    def plan_family_bundle(self,
                           members_count: int = 3,
                           total_data_gb: float = 60.0,
                           need_broadband: bool = True,
                           broadband_speed_min: int = 1000,
                           carrier_preference: str = "all",
                           city: str = "石家庄") -> Dict[str, Any]:
        """多成员家庭融合选型求解：主卡+免月费副卡+千兆FTTR对比各自独立单卡"""
        sql = """
        SELECT * FROM tariffs
        WHERE status='在售'
          AND (cat='套餐' OR ty='套餐' OR title LIKE '%套餐%' OR title LIKE '%融合%')
          AND eff_fee IS NOT NULL
          AND traffic_gb >= ?
        """
        params = [total_data_gb * 0.5]

        if need_broadband:
            sql += " AND (bw_line = 1 OR bw_speedup = 1)"
        if broadband_speed_min:
            sql += " AND bw_down_mbps >= ?"
            params.append(broadband_speed_min)
        if city and city not in ("全省", "全国", "不限"):
            sql += " AND (province_wide = 1 OR cities LIKE ? OR scope = 'cn')"
            params.append(f"%{city}%")
        if carrier_preference and carrier_preference != "all":
            carrier_map = {"move": "move", "unicom": "unicom", "telecom": "telecom", "cbn": "cbn",
                           "移动": "move", "联通": "unicom", "电信": "telecom", "广电": "cbn"}
            sql += " AND net = ?"
            params.append(carrier_map.get(carrier_preference.lower(), carrier_preference))

        sql += " ORDER BY eff_fee ASC, free_sub_cards DESC, traffic_gb DESC LIMIT 5"
        rows = [dict(r) for r in self.con.execute(sql, params).fetchall()]

        if not rows:
            return {
                "success": False,
                "message": f"未在{city}找到同时满足≥{total_data_gb}GB与{broadband_speed_min}M宽带的在售家庭套餐"
            }

        best = rows[0]
        free_cards = best["free_sub_cards"] or 0
        needed_sub_cards = max(0, members_count - 1)
        chargeable_sub_cards = max(0, needed_sub_cards - free_cards)
        sub_card_monthly = chargeable_sub_cards * 10
        total_monthly = best["eff_fee"] + sub_card_monthly
        per_person = round(total_monthly / members_count, 1)

        # 独立开办对比基准: 每个人单独办39元单卡 + 单独办50元宽带
        standalone_total = (members_count * 39) + (50 if need_broadband else 0)
        annual_saving = (standalone_total - total_monthly) * 12

        alternatives = []
        for r in rows[1:]:
            f_cards = r["free_sub_cards"] or 0
            c_cards = max(0, needed_sub_cards - f_cards)
            tot = r["eff_fee"] + (c_cards * 10)
            alternatives.append({
                "carrier": r["carrier_name"],
                "title": r["title"],
                "report_num": r["report_num"],
                "monthly_fee": f"{r['eff_fee']}元/月",
                "free_sub_cards": f"送{f_cards}张",
                "family_total_monthly": f"{tot}元/月",
                "per_person_monthly": f"{round(tot / members_count, 1)}元/月",
                "broadband": f"{r['bw_down_mbps']}M" if r['bw_down_mbps'] else "含宽带"
            })

        return {
            "success": True,
            "family_requirements": {
                "members_count": members_count,
                "total_data_gb": total_data_gb,
                "broadband_speed_min": f"{broadband_speed_min}M",
                "city": city
            },
            "recommended_bundle": {
                "carrier": best["carrier_name"],
                "title": best["title"],
                "report_num": best["report_num"],
                "main_monthly_fee": f"{best['eff_fee']}元/月",
                "traffic_pool": f"{best['traffic_gb']}GB (家庭共享)",
                "broadband": f"{best['bw_down_mbps']}M 全屋光纤" if best['bw_down_mbps'] else "包含高速家用宽带",
                "sub_card_config": f"主卡 1 张 + 副卡 {needed_sub_cards} 张（其中 {min(needed_sub_cards, free_cards)} 张免月费，{chargeable_sub_cards} 张按10元/月计费）",
                "family_total_monthly": f"¥{total_monthly} 元/月",
                "per_person_monthly": f"¥{per_person} 元/月"
            },
            "financial_advantage": {
                "standalone_comparison_monthly": f"¥{standalone_total} 元/月 (按每人独立开办¥39套餐 + 单独宽带¥50测算)",
                "annual_savings": f"全家每年立省 ¥{round(annual_saving, 1)} 元" if annual_saving > 0 else "与独立开办成本持平",
                "benefit_summary": f"花更少的钱，全家共享{best['traffic_gb']}GB大流量 + {broadband_speed_min}M千兆FTTR宽带"
            },
            "alternative_options": alternatives,
            "setup_guide": "办理主套餐入网后，主卡持有人携带身份证前往营业厅一次性办理副卡并关联共享，副卡共享主卡套内全部通用流量与语音分钟数。"
        }

    # =========================================================================
    # 核心工具 9: 跨运营商竞品对标雷达 (competitor_radar)
    # =========================================================================
    def competitor_radar(self, target_identifier: str, city: str = "石家庄") -> Dict[str, Any]:
        """以指定业务为基准，横向穿透四网大盘寻找正面竞品并生成多维雷达对比"""
        dt = self.get_tariff_detail(target_identifier)
        if not dt.get("found"):
            return dt

        t_basic = dt["basic_info"]
        t_price = dt["pricing_and_allowances"]
        t_bw = dt["broadband_and_devices"]
        t_rules = dt["restrictions_and_rules"]

        carrier_map = {"move": "move", "unicom": "unicom", "telecom": "telecom", "cbn": "cbn",
                       "中国移动": "move", "中国联通": "unicom", "中国电信": "telecom", "中国广电": "cbn"}
        target_net = carrier_map.get(t_basic["carrier"], "move")
        fee = t_price["effective_monthly_fee"] or 59.0

        rivals = []
        for other_net, c_name in NET_NAMES.items():
            if other_net == target_net:
                continue
            sql = """
            SELECT * FROM tariffs
            WHERE net = ? AND status = '在售'
              AND eff_fee IS NOT NULL
              AND eff_fee >= ? AND eff_fee <= ?
            """
            params = [other_net, max(19.0, fee * 0.7), fee * 1.35]
            if city and city not in ("全省", "全国", "不限"):
                sql += " AND (province_wide = 1 OR cities LIKE ? OR scope = 'cn')"
                params.append(f"%{city}%")

            sql += " ORDER BY ABS(eff_fee - ?) ASC, traffic_gb DESC, bw_down_mbps DESC LIMIT 1"
            params.append(fee)
            row = self.con.execute(sql, params).fetchone()
            if row:
                r = dict(row)
                fee_diff = round(r["eff_fee"] - fee, 1)
                traffic_diff = round((r["traffic_gb"] or 0) - (t_price["general_traffic_gb"] or 0), 1)

                eval_comments = []
                if fee_diff < 0: eval_comments.append(f"月费便宜 ¥{abs(fee_diff)} 元")
                elif fee_diff > 0: eval_comments.append(f"月费略高 ¥{fee_diff} 元")
                else: eval_comments.append("月费持平")

                if traffic_diff > 0: eval_comments.append(f"流量多 {traffic_diff}GB")
                elif traffic_diff < 0: eval_comments.append(f"流量少 {abs(traffic_diff)}GB")

                if (r["bw_down_mbps"] or 0) > (t_bw["downlink_mbps"] or 0):
                    eval_comments.append("宽带速率更高")

                rivals.append({
                    "carrier": c_name,
                    "title": r["title"],
                    "report_num": r["report_num"],
                    "monthly_fee": f"{r['eff_fee']}元/月",
                    "traffic": f"{r['traffic_gb']}GB",
                    "broadband": f"{r['bw_down_mbps']}M" if r['bw_down_mbps'] else ("含宽带" if r['bw_line'] else "无宽带"),
                    "sub_cards": f"送{r['free_sub_cards']}副卡" if r['free_sub_cards'] else "无",
                    "contract": f"{r['contract_months']}月" if r['contract_months'] else "无协议",
                    "competitive_highlight": " · ".join(eval_comments)
                })

        # 计算雷达分 (满分10分)
        price_score = min(10.0, max(2.0, round(10.0 - (fee / 30.0), 1)))
        data_score = min(10.0, max(2.0, round(((t_price["general_traffic_gb"] or 0) / 15.0) + 3.0, 1)))
        bw_score = 9.0 if (t_bw["downlink_mbps"] or 0) >= 1000 else (7.0 if t_bw["has_broadband_line"] else 3.0)
        freedom_score = 9.5 if not t_rules["contract_months"] else max(3.0, 10.0 - (t_rules["contract_months"] / 4.0))

        return {
            "target_package": {
                "carrier": t_basic["carrier"],
                "title": t_basic["title"],
                "report_num": t_basic["report_num"],
                "monthly_fee": f"{fee}元/月",
                "traffic": f"{t_price['general_traffic_gb']}GB",
                "broadband": f"{t_bw['downlink_mbps']}M" if t_bw['downlink_mbps'] else ("含宽带" if t_bw['has_broadband_line'] else "无宽带")
            },
            "radar_scores_out_of_10": {
                "price_competitiveness": price_score,
                "data_abundance": data_score,
                "broadband_tier": bw_score,
                "contract_freedom": freedom_score
            },
            "rival_benchmarks": rivals,
            "strategic_verdict": f"在 {fee} 元价格带，{t_basic['carrier']} 该套餐整体评分良好。如需更高流量或千兆宽带，可重点参考友商对标资费。"
        }
