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
import json
import os
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
        """比过期更严重：读不到任何产物（连基线都没了）。"""
        with tempfile.TemporaryDirectory() as tmp:
            hp = os.path.join(tmp, "nope.json")
            sd = os.path.join(tmp, "nosnap")
            with mock.patch.object(check_fresh, "HIST", hp), \
                 mock.patch.object(check_fresh, "SNAP_DIR", sd):
                code, out = check_fresh.check(30)
        self.assertEqual(code, 2)
        self.assertEqual(len(out["missing"]), 2)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
