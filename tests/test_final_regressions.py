#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本轮新增能力的回归测试

覆盖：
  · 推送通道 —— 五通道识别、钉钉/飞书**加签算法差异**、字节截断、零变化闸门
  · 新鲜度 —— 时间戳解析、过期判定、读不到数据
  · 变化汇总 —— 同 (日期, 网) 多轮去重、rounds 字段映射
  · 采样噪声 —— 双条件边界、瘦身是纯函数
  · 源码包 —— 凭据/数据黑名单

跑法：
    python tests/test_final_regressions.py
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARIFF = os.path.join(ROOT, "cloud", "tariff")
sys.path.insert(0, TARIFF)

import check_fresh      # noqa: E402
import make_source_zip  # noqa: E402
import noise_guard      # noqa: E402
import notify           # noqa: E402
import prune_data       # noqa: E402
import summary          # noqa: E402

PY = sys.executable


# ════════════════════════════════════════════════════════════════════
#  推送通道
# ════════════════════════════════════════════════════════════════════
class TestNotifyChannels(unittest.TestCase):

    def test_five_channels_registered(self):
        names = [n for n, _ in notify.CHANNELS]
        for want in ("钉钉", "飞书", "企业微信", "PushPlus", "邮件"):
            self.assertIn(want, names)
        self.assertEqual(len(names), 5)

    def test_channel_env_table_covers_all(self):
        """每个通道都得有「算它配齐」的判据，否则 available() 永远列不出它。"""
        for name, _ in notify.CHANNELS:
            self.assertIn(name, notify._CHANNEL_ENV)
            self.assertTrue(notify._CHANNEL_ENV[name])

    def test_is_on_and_available(self):
        with mock.patch.dict(os.environ, {
            "DINGTALK_WEBHOOK": "https://x", "FEISHU_WEBHOOK": "",
            "WECOM_WEBHOOK": "", "PUSHPLUS_TOKEN": "", "SMTP_HOST": "", "MAIL_TO": "",
        }, clear=False):
            self.assertTrue(notify._is_on("钉钉"))
            self.assertFalse(notify._is_on("飞书"))
            self.assertEqual(notify.available(), ["钉钉"])

    def test_mail_needs_both_host_and_to(self):
        """只有 SMTP_HOST 没有 MAIL_TO —— 不算配齐（发不出去）。"""
        with mock.patch.dict(os.environ, {
            "SMTP_HOST": "smtp.qq.com", "MAIL_TO": "", "PUSHPLUS_TOKEN": "",
            "DINGTALK_WEBHOOK": "", "FEISHU_WEBHOOK": "", "WECOM_WEBHOOK": "",
        }, clear=False):
            self.assertNotIn("邮件", notify.available())

    def test_dingtalk_sign_algorithm(self):
        """钉钉：HMAC-SHA256(key=secret, msg=ts+"\\n"+secret)，毫秒 + urlencode。"""
        import base64
        import hashlib
        import hmac
        import urllib.parse
        ts, sign = notify._dingtalk_sign("SECabc", ts=1700000000000)
        self.assertEqual(ts, "1700000000000")
        want = base64.b64encode(hmac.new(
            b"SECabc", b"1700000000000\nSECabc", hashlib.sha256).digest()).decode()
        self.assertEqual(urllib.parse.unquote_plus(sign), want)

    def test_feishu_sign_algorithm(self):
        """飞书：HMAC-SHA256(key=**空串**, msg=ts+"\\n"+secret)，秒。"""
        import base64
        import hashlib
        import hmac
        ts, sign = notify._feishu_sign("SECabc", ts=1700000000)
        self.assertEqual(ts, "1700000000")
        want = base64.b64encode(hmac.new(
            b"1700000000\nSECabc", b"", hashlib.sha256).digest()).decode()
        self.assertEqual(sign, want)

    def test_two_signs_differ(self):
        """两家算法不同 —— 混用的表现就是永远 sign not match。"""
        import urllib.parse
        _, d = notify._dingtalk_sign("SECabc", ts=1700000000)
        _, f = notify._feishu_sign("SECabc", ts=1700000000)
        self.assertNotEqual(urllib.parse.unquote_plus(d), f)

    def test_trunc_bytes_no_broken_utf8(self):
        cut = notify._trunc_bytes("汉" * 3000, 3800)
        self.assertLessEqual(len(cut.encode("utf-8")), 3800)
        self.assertNotIn("\ufffd", cut)
        self.assertEqual(notify._trunc_bytes("短", 3800), "短")

    def test_zero_change_gate(self):
        R = lambda **kw: dict({"net": "河北移动", "n": 100}, **kw)   # noqa: E731
        self.assertFalse(notify.has_change([R(added=0, removed=0, changed=0)]))
        self.assertFalse(notify.has_change([]))
        self.assertTrue(notify.has_change([R(added=1)]))
        self.assertTrue(notify.has_change([R(note="degraded")]))
        self.assertFalse(notify.has_change([R(note="baseline")]))

    def test_html_escape_upstream_html(self):
        """上游资费文案带真 HTML —— 必须转义，不能原样塞进邮件正文。"""
        self.assertIn("&lt;b&gt;", notify._md_to_html("<b>粗</b>"))


# ════════════════════════════════════════════════════════════════════
#  新鲜度
# ════════════════════════════════════════════════════════════════════
class TestFresh(unittest.TestCase):

    def test_parse_ts_formats(self):
        self.assertIsNotNone(check_fresh.parse_ts("2026-09-30 09:06:44"))
        self.assertIsNotNone(check_fresh.parse_ts("2026-09-30T09:06:44"))
        self.assertIsNotNone(check_fresh.parse_ts("2026-09-30"))
        self.assertIsNone(check_fresh.parse_ts(""))
        self.assertIsNone(check_fresh.parse_ts("不是时间"))

    def _tmp_env(self, tmp, hist_items=None, snaps=()):
        hp = os.path.join(tmp, "history.json")
        with open(hp, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "items": hist_items or []}, f, ensure_ascii=False)
        sd = os.path.join(tmp, "snapshots")
        os.makedirs(sd, exist_ok=True)
        for s in snaps:
            open(os.path.join(sd, s), "wb").close()
        return hp, sd

    def test_newest_history_picks_max_ts_not_last(self):
        """回填记录可能后插，必须按 ts 取最大而不是盲信最后一条。"""
        items = [
            {"ts": "2026-09-30 12:00:00", "d": "2026-09-30", "code": "move", "net": "河北移动"},
            {"ts": "2026-09-20 08:00:00", "d": "2026-09-20", "code": "move", "net": "河北移动"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            hp, sd = self._tmp_env(tmp, items)
            with mock.patch.object(check_fresh, "HIST", hp), \
                 mock.patch.object(check_fresh, "SNAP_DIR", sd):
                ts, why = check_fresh.newest_history()
        self.assertEqual(ts.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-30 12:00:00")
        self.assertIn("河北移动", why)

    def test_newest_snapshot_from_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            hp, sd = self._tmp_env(tmp, [], ("unicom_tariff_20260929.json.gz",
                                             "cbn_tariff_20260930.json.gz"))
            with mock.patch.object(check_fresh, "HIST", hp), \
                 mock.patch.object(check_fresh, "SNAP_DIR", sd):
                ts, why = check_fresh.newest_snapshot()
        self.assertEqual(ts.strftime("%Y%m%d"), "20260930")
        self.assertIn("cbn_tariff_20260930", why)

    def test_stale_detection_and_exit_code(self):
        """阈值调成 0 小时 ⇒ 必然过期 ⇒ 退出码 1。"""
        items = [{"ts": "2020-01-01 00:00:00", "d": "2020-01-01",
                  "code": "move", "net": "河北移动"}]
        with tempfile.TemporaryDirectory() as tmp:
            hp, sd = self._tmp_env(tmp, items, ("cbn_tariff_20200101.json.gz",))
            with mock.patch.object(check_fresh, "HIST", hp), \
                 mock.patch.object(check_fresh, "SNAP_DIR", sd):
                code, out = check_fresh.check(0)
        self.assertEqual(code, 1)
        self.assertTrue(out["stale"])

    def test_no_data_at_all_is_2(self):
        """比过期更严重：读不到任何产物（连基线都没了）。
        分源判定后：四源各自的「快照+history」都读不到 ⇒ missing 按源计 4 条。"""
        with tempfile.TemporaryDirectory() as tmp:
            hp = os.path.join(tmp, "nope.json")
            sd = os.path.join(tmp, "nosnap")
            with mock.patch.object(check_fresh, "HIST", hp), \
                 mock.patch.object(check_fresh, "SNAP_DIR", sd):
                code, out = check_fresh.check(30)
        self.assertEqual(code, 2)
        self.assertEqual(len(out["missing"]), len(check_fresh.SOURCES))


# ════════════════════════════════════════════════════════════════════
#  变化汇总
# ════════════════════════════════════════════════════════════════════
class TestSummary(unittest.TestCase):

    ITEMS = [
        {"ts": "2026-09-30 09:00:00", "d": "2026-09-30", "code": "move",
         "net": "河北移动", "n": 5000, "a": 1, "r": 0, "c": 0, "smp": []},
        {"ts": "2026-09-30 12:00:00", "d": "2026-09-30", "code": "move",
         "net": "河北移动", "n": 5000, "a": 9, "r": 2, "c": 52,
         "smp": [{"n": "甲", "ty": "套餐", "k": "a"}]},
        {"ts": "2026-09-30 09:10:00", "d": "2026-09-30", "code": "unicom",
         "net": "河北联通", "n": 8000, "a": 1, "r": 0, "c": 11, "smp": []},
    ]

    def test_same_day_multiple_rounds_keeps_last(self):
        """同一天重跑多轮 ⇒ 只保留最后一条，否则会出现重复播报。"""
        picked = summary.pick(self.ITEMS, date="2026-09-30")
        by_code = {p["code"]: p for p in picked}
        self.assertEqual(len(picked), 2)
        self.assertEqual(by_code["move"]["a"], 9)      # 取 12:00 那条
        self.assertEqual(by_code["move"]["ts"], "2026-09-30 12:00:00")

    def test_order_is_stable(self):
        picked = summary.pick(self.ITEMS, date="2026-09-30")
        self.assertEqual([p["code"] for p in picked], ["move", "unicom"])

    def test_to_round_maps_fields(self):
        r = summary._to_round(self.ITEMS[1])
        self.assertEqual((r["added"], r["removed"], r["changed"]), (9, 2, 52))
        self.assertEqual(r["net"], "河北移动")
        self.assertEqual(len(r["samples"]), 1)

    def test_build_summary_uses_build_body(self):
        t, md = summary.build_summary(date="2026-09-30", hist=self.ITEMS)
        self.assertIn("河北移动", md)
        self.assertIn("河北联通", md)
        self.assertIn("合计", md)
        self.assertIn("新增", t)

    def test_empty_range(self):
        t, md = summary.build_summary(date="1999-01-01", hist=self.ITEMS)
        self.assertIn("没有任何记录", md)

    def test_days_window_includes_today(self):
        """--days 1 必须包含今天（而不是「24 小时前」这种口径）。"""
        import datetime
        today = datetime.datetime.now(check_fresh.CST).date().isoformat()
        items = [{"ts": "%s 09:00:00" % today, "d": today, "code": "move",
                  "net": "河北移动", "n": 1, "a": 1, "r": 0, "c": 0, "smp": []}]
        self.assertEqual(len(summary.pick(items, days=1)), 1)


# ════════════════════════════════════════════════════════════════════
#  采样噪声护栏
# ════════════════════════════════════════════════════════════════════
class TestNoiseGuard(unittest.TestCase):

    def test_ratio_condition(self):
        self.assertTrue(noise_guard.is_sampling_noise(1500, 1500, 8000))   # 37.5%
        self.assertFalse(noise_guard.is_sampling_noise(200, 200, 8000))    # 5%

    def test_abs_condition_protects_small_nets(self):
        """小网高比例但是真变化 —— 绝对数下限就是为它存在的。"""
        self.assertFalse(noise_guard.is_sampling_noise(1, 1, 3))
        self.assertFalse(noise_guard.is_sampling_noise(25, 24, 100))
        self.assertTrue(noise_guard.is_sampling_noise(25, 25, 100))

    def test_ratio_is_strictly_greater(self):
        self.assertFalse(noise_guard.is_sampling_noise(150, 150, 1000))    # 正好 30%
        self.assertTrue(noise_guard.is_sampling_noise(151, 150, 1000))

    def test_bad_baseline(self):
        self.assertFalse(noise_guard.is_sampling_noise(100, 100, 0))
        self.assertFalse(noise_guard.is_sampling_noise(100, 100, None))
        self.assertFalse(noise_guard.is_sampling_noise(100, 100, "x"))

    def test_slim_is_pure(self):
        items = [{"d": "2026-09-30", "smp": [{"n": "汉" * 300}] * 99}]
        out = noise_guard.slim_history(items)
        self.assertEqual(len(out[0]["smp"]), noise_guard.HIST_SAMPLE_LIMIT)
        self.assertEqual(len(items[0]["smp"]), 99)                  # 原对象没被动
        self.assertLessEqual(len(out[0]["smp"][0]["n"]),
                             noise_guard.DETAIL_TEXT_LIMIT + 1)

    def test_scan_finds_noise(self):
        rep = noise_guard.scan([
            {"d": "2026-09-30", "net": "河北联通", "n": 8000, "a": 1500, "r": 1500},
            {"d": "2026-09-30", "net": "河北移动", "n": 5000, "a": 3, "r": 1},
        ])
        self.assertEqual(len(rep["noisy"]), 1)
        self.assertEqual(rep["noisy"][0]["net"], "河北联通")
        self.assertEqual(rep["noisy"][0]["pct"], 37.5)


# ════════════════════════════════════════════════════════════════════
#  源码包黑名单
# ════════════════════════════════════════════════════════════════════
class TestSourceZip(unittest.TestCase):

    def test_keep_blocks_credentials_and_data(self):
        for rel in ("cloud/tariff/.nrapigate_key", "probes/.fiddler_mcp_key",
                    "cloud/tariff/.ct_raw.json", "cloud/tariff/.unicom_cache.json",
                    "cloud/tariff/snapshots/cbn_tariff_20260930.json.gz",
                    "cloud/tariff/docs/index.html", ".env"):
            self.assertFalse(make_source_zip.keep(rel), rel)

    def test_keep_allows_real_source(self):
        for rel in ("cloud/tariff/notify.py", "README.md",
                    "cloud/tariff/template.html", ".env.example"):
            self.assertTrue(make_source_zip.keep(rel), rel)

    def test_evidence_images_excluded(self):
        self.assertFalse(make_source_zip.keep("evidence/某截图-20260924.png"))
        # 但同目录的 json 证据要留下（体积小、是结论的原始凭证）
        self.assertTrue(make_source_zip.keep("evidence/unicom-coverage.json"))

    def test_audit_catches_leaks(self):
        bad = make_source_zip.audit([
            "hebei-tariff-monitor/x/cloud/tariff/.nrapigate_key",
            "hebei-tariff-monitor/x/cloud/tariff/snapshots/a.json.gz",
        ])
        self.assertEqual(len(bad), 2)


# ════════════════════════════════════════════════════════════════════
#  脚本端到端冒烟（子进程，验真实退出码）
# ════════════════════════════════════════════════════════════════════
class TestScriptsSmoke(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run([PY] + list(args), cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120)

    def test_check_fresh_runs(self):
        # 冒烟口径（2026-10-08 修）：验「脚本正确执行并给出合法结论」，
        # 不是「数据此刻必须新鲜」。check_fresh 的退出码语义（见其文件头）：
        #   0=新鲜 / 1=过期告警 / 2=连基线都读不到。
        # 旧断言 ==0 会把每天 06:30–09:35（晨班车落地前）窗口内跑的 CI
        # 全部误红（快照 30~33 小时龄属日变窗内的正常陈旧，巡检本身健康），
        # 2026-10-08 08:52 的 push CI 实际踩中。故 0/1 都算通过——
        # 1 还必须是「规范告警」而非崩溃：输出须含检查时间行。
        r = self._run("cloud/tariff/check_fresh.py")
        self.assertIn(r.returncode, (0, 1), "退出码 2=基线缺失/其它=异常崩溃：" + r.stdout + r.stderr)
        self.assertIn("检查时间：", r.stdout, "输出缺格式标记，脚本可能没真正跑完逻辑")
        self.assertIn("阈值", r.stdout)

    def test_noise_guard_selfcheck(self):
        r = self._run("cloud/tariff/noise_guard.py", "--selfcheck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_summary_runs(self):
        r = self._run("cloud/tariff/summary.py", "--days", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("河北", r.stdout)

    def test_push_once_dry(self):
        r = self._run("cloud/tariff/push_once.py", "--dry")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_notify_selfcheck(self):
        r = self._run("cloud/tariff/notify.py", "--selfcheck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ════════════════════════════════════════════════════════════════════
#  check_fresh.newest_snapshot —— fetchedAt 优先于文件名日期
# ════════════════════════════════════════════════════════════════════
class TestFreshSnapshotFetchedAt(unittest.TestCase):
    """2026-10-08 真实教训：newest_snapshot 曾按文件名日期（午夜零点）算年龄，
    20:17 采集的 cbn_tariff_20261007.json.gz 被当成 00:00，虚增 ~20 小时，
    晨间窗口（当天班车落地前）的 push CI 全部误红 —— 数据实际只有 12.6h 龄。
    锁死：必须优先读 gzip 内 JSON 的 fetchedAt，文件名只作兜底。"""

    def _mk_snap(self, d, name, payload):
        p = os.path.join(d, name)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return p

    def test_prefers_fetched_at_over_filename(self):
        import datetime as dt
        recent = dt.datetime.now(check_fresh.CST) - dt.timedelta(hours=2)
        d = tempfile.mkdtemp()
        old = check_fresh.SNAP_DIR
        check_fresh.SNAP_DIR = d
        try:
            self._mk_snap(d, "cbn_tariff_20200101.json.gz",
                          {"fetchedAt": recent.strftime("%Y-%m-%d %H:%M:%S"),
                           "entries": [{"n": "x"}]})
            ts, why = check_fresh.newest_snapshot()
            self.assertIsNotNone(ts)
            age_h = (dt.datetime.now(check_fresh.CST) - ts).total_seconds() / 3600
            # 年龄应 ≈2h；若用了文件名零点会是 ~24 万小时
            self.assertLess(age_h, 4, f"疑似用了文件名零点而非 fetchedAt：age={age_h}h why={why}")
            self.assertIn("cbn_tariff_20200101", why)
        finally:
            check_fresh.SNAP_DIR = old
            shutil.rmtree(d, ignore_errors=True)

    def test_falls_back_to_filename_when_unreadable(self):
        d = tempfile.mkdtemp()
        old = check_fresh.SNAP_DIR
        check_fresh.SNAP_DIR = d
        try:
            # 无 fetchedAt 字段 → 退回文件名日期（午夜零点），不能崩
            self._mk_snap(d, "cbn_tariff_20200101.json.gz", {"entries": []})
            ts, why = check_fresh.newest_snapshot()
            self.assertIsNotNone(ts)
            self.assertEqual(ts.strftime("%Y-%m-%d"), "2020-01-01")
        finally:
            check_fresh.SNAP_DIR = old
            shutil.rmtree(d, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
#  check_fresh 分源判定 —— 单源静默失效必须能响
# ════════════════════════════════════════════════════════════════════
class TestFreshPerSource(unittest.TestCase):
    """旧逻辑只看「全局最新」：广电单独静默失效而其它源正常时，
    全局最新永远新鲜，报警永远不响 —— 而这正是脚本要防的场景②。
    锁死：任一源两个证据（快照 fetchedAt + history 该源最新条）都过期
    ⇒ exit 1；全部源都没有任何数据 ⇒ exit 2。"""

    @staticmethod
    def _mk_snap(d, name, payload):
        p = os.path.join(d, name)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return p

    def _setup(self, ages):
        """ages: {code: 小时前}。为每源造快照 + history 条目；缺键 = 完全没有。"""
        import datetime as dt
        d = tempfile.mkdtemp()
        old_snap, old_hist = check_fresh.SNAP_DIR, check_fresh.HIST
        items = []
        for code, prefix in [("move", "hebei_tariff"), ("unicom", "unicom_tariff"),
                             ("telecom", "ct_tariff"), ("cbn", "cbn_tariff")]:
            if code not in ages:
                continue
            h = ages[code]
            ts = (dt.datetime.now(check_fresh.CST) - dt.timedelta(hours=h)
                  ).strftime("%Y-%m-%d %H:%M:%S")
            self._mk_snap(d, f"{prefix}_20200101.json.gz",
                          {"fetchedAt": ts, "entries": [{"n": "x"}]})
            items.append({"ts": ts, "d": ts[:10], "code": code})
        check_fresh.SNAP_DIR = d
        hp = os.path.join(d, "history.json")
        with open(hp, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "items": items}, f, ensure_ascii=False)
        check_fresh.HIST = hp
        return d, old_snap, old_hist

    def test_single_source_silent_failure_alerts(self):
        # 广电 40h 没跑、其它源 2h 前：全局「最新」是新鲜的，但必须响
        d, s, h = self._setup({"move": 2, "unicom": 2, "telecom": 2, "cbn": 40})
        try:
            code, out = check_fresh.check(30)
            self.assertEqual(code, 1, "单源静默失效必须告警")
            self.assertEqual([r["code"] for r in out["stale"]], ["cbn"])
        finally:
            check_fresh.SNAP_DIR, check_fresh.HIST = s, h
            shutil.rmtree(d, ignore_errors=True)

    def test_all_fresh_passes(self):
        d, s, h = self._setup({"move": 2, "unicom": 3, "telecom": 4, "cbn": 5})
        try:
            code, out = check_fresh.check(30)
            self.assertEqual(code, 0)
            self.assertEqual(len(out["items"]), 4)
        finally:
            check_fresh.SNAP_DIR, check_fresh.HIST = s, h
            shutil.rmtree(d, ignore_errors=True)

    def test_no_data_at_all_is_level2(self):
        d, s, h = self._setup({})
        try:
            code, out = check_fresh.check(30)
            self.assertEqual(code, 2, "四源全部无数据 = 连基线都没了")
        finally:
            check_fresh.SNAP_DIR, check_fresh.HIST = s, h
            shutil.rmtree(d, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
#  prune_data —— 快照裁剪的三道保险
# ════════════════════════════════════════════════════════════════════
class TestPruneData(unittest.TestCase):
    """裁剪脚本动的是数据归档，三道保险必须锁死：
    ① 每源最新一份永不删（停更源的 diff 基线不能断）；
    ② 剩余 < 4 份触发保险丝，放弃删除；
    ③ 不认识命名的一律不碰。"""

    def _setup(self, files):
        d = tempfile.mkdtemp()
        old = prune_data.SNAP_DIR
        prune_data.SNAP_DIR = d
        for name, content in files.items():
            with gzip.open(os.path.join(d, name), "wt", encoding="utf-8") as f:
                json.dump(content, f)
        return d, old

    def test_deletes_old_keeps_new_and_protects_newest_per_prefix(self):
        d, old = self._setup({
            "cbn_tariff_20200101.json.gz": {"x": 1},   # 老
            "cbn_tariff_20990101.json.gz": {"x": 2},   # 新（未来日期 = 最新）
            "cbn_tariff_20200201.json.gz": {"x": 3},   # 老但比上面那份新一点
        })
        try:
            to_del, keep = prune_data.scan(90)
            names = {os.path.basename(p) for p in to_del}
            self.assertEqual(names, {"cbn_tariff_20200101.json.gz", "cbn_tariff_20200201.json.gz"})
            # 最新那份即使超龄也在 keep
            self.assertIn(os.path.join(d, "cbn_tariff_20990101.json.gz"), keep)
        finally:
            prune_data.SNAP_DIR = old
            shutil.rmtree(d, ignore_errors=True)

    def test_fuse_aborts_when_below_min_keep(self):
        d, old = self._setup({
            "cbn_tariff_20200101.json.gz": {"x": 1},
            "cbn_tariff_20200201.json.gz": {"x": 2},
            "unicom_tariff_20200101.json.gz": {"x": 3},
            "unicom_tariff_20200201.json.gz": {"x": 4},
            "ct_tariff_20200101.json.gz": {"x": 5},
            "ct_tariff_20200201.json.gz": {"x": 6},
            "hebei_tariff_20200101.json.gz": {"x": 7},
            "hebei_tariff_20200201.json.gz": {"x": 8},
        })
        try:
            # --days 0 → 全部超龄，每源只保最新 ⇒ 剩 4 份本来够底线；
            # 把 MIN_KEEP 提到 10 模拟「参数灾难」，验证保险丝放弃删除
            with mock.patch.object(prune_data, "MIN_KEEP", 10):
                sys.argv = ["prune_data.py", "--days", "0", "--delete"]
                rc = prune_data.main()
            self.assertEqual(rc, 1, "低于保险丝必须放弃删除")
            self.assertEqual(len(os.listdir(d)), 8, "保险丝触发时不得删除任何文件")
        finally:
            prune_data.SNAP_DIR = old
            shutil.rmtree(d, ignore_errors=True)

    def test_unrecognized_names_untouched(self):
        d, old = self._setup({
            "shct_latest.json": {"x": 1},                       # 不是 .json.gz
            "weird.json.gz": {"x": 2},                          # 没有日期尾
            "cbn_tariff_20200101.json.gz": {"x": 3},
            "cbn_tariff_20990101.json.gz": {"x": 4},
        })
        try:
            to_del, keep = prune_data.scan(90)
            names = {os.path.basename(p) for p in to_del}
            self.assertNotIn("weird.json.gz", names)
            self.assertNotIn("shct_latest.json", names)
        finally:
            prune_data.SNAP_DIR = old
            shutil.rmtree(d, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
#  双份 COND_DEFS 漂移护栏 —— template.html（主界面）vs build_sh.TPL（上海页）
# ════════════════════════════════════════════════════════════════════
class TestCondDefsSync(unittest.TestCase):
    """两处手维护同一份判据（7 类条款的 lab/cls/正则/find），历史上靠人工逐
    def 对比；2026-10-08 深查确认当时一致，但没有任何机制拦住将来某次只改
    一边的漂移 —— 此测试把语义一致性锁进 CI。"""

    @classmethod
    def setUpClass(cls):
        import build_sh  # 有 __main__ 守卫，import 无副作用
        tpl_src = open(os.path.join(TARIFF, "template.html"), encoding="utf-8").read()
        m1 = re.search(r"const COND_DEFS=\[([\s\S]*?)\n\];", tpl_src)
        m2 = re.search(r"var COND_DEFS=\[([\s\S]*?)\n\];", build_sh.TPL)
        cls.assertIsNotNone(cls, m1, "template.html 里找不到 COND_DEFS")
        cls.assertIsNotNone(cls, m2, "build_sh.TPL 里找不到 COND_DEFS")
        cls.main_src = cls._norm(m1.group(1))
        cls.sh_src = cls._norm(m2.group(1))

    @staticmethod
    def _norm(s):
        # 规范化到语义等价：剥注释 / let→var / 压空白 / 逗号后空格
        # （代码区的这些差异不改变语义；字符串字面量里只有中文标点，不受影响）
        s = re.sub(r"/\*[\s\S]*?\*/", "", s)
        s = re.sub(r"//[^\n]*", "", s)
        s = re.sub(r"\blet\b", "var", s)
        s = re.sub(r",\s+", ",", s)
        return re.sub(r"\s+", " ", s).strip()

    def test_defs_identical(self):
        self.assertEqual(self.main_src, self.sh_src,
                         "主界面与上海页的 COND_DEFS 漂移了 —— 两边要同步改，"
                         "否则两页对同一套餐给出不同的办理必读")

    def test_seven_categories_in_order(self):
        labs = re.findall(r'lab:"([^"]+)"', self.main_src)
        self.assertEqual(labs, ["合约期", "违约金", "最低消费", "预存",
                                "首月优惠", "一次性费用", "限办次数"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
