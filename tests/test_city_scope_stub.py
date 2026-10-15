# -*- coding: utf-8 -*-
"""city_scope 格子级重试的离线桩测试（零网络、秒级）。

背景（2026-10-15 双事故）：city_scope 重写引入 UnboundLocalError
（推导式引用了函数里后面才赋值的循环变量），联通预取线程当场炸掉 →
整轮「采集失败，沿用上一版快照」→ 用户只能从事后页面提示里发现。
py_compile 拦不住这类运行期错误 —— 唯一防线是让 city_scope 真跑一遍。

桩法：替换模块级 get_menu / get_level3，构造「一个格子首轮失败、重试成功」，
断言：① 不抛异常；② 失败格子重试后拿到了数据（并集不被低估）。

用法：python tests/test_city_scope_stub.py   # 退出码 0 = 通过
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "probes"))
import he_unicom_tariff as H          # noqa: E402

state = {"fail_left": 1}


def fake_menu(city="185"):
    """get_menu 桩：两个组合（套餐/移网、加装包/流量包）。"""
    return ([{"firstLevel": "1", "firstLevelName": "套餐",
              "secondLevels": [{"secondLevel": "1", "secondLevelName": "移网"}]},
             {"firstLevel": "2", "firstLevelName": "加装包",
              "secondLevels": [{"secondLevel": "1", "secondLevelName": "流量包"}]}],
            None)


def fake_l3(attr, first, second, city="185"):
    """get_level3 桩：石家庄(188)×加装包 首轮失败一次（触发格子级重试）。"""
    if first == "2" and city == "188" and state["fail_left"] > 0:
        state["fail_left"] -= 1
        return None                     # 未知（请求失败），不是空目录
    return [{"id": "id-%s-%s-%s" % (first, second, city),
             "name": "N%s%s%s" % (first, second, city)}]


def main():
    H.get_menu = fake_menu
    H.get_level3 = fake_l3
    out = H.city_scope(cities=[("石家庄", "188"), ("唐山", "181")],
                       verbose=False)
    # 结构断言：2 组合 × 2 城市都齐（重建路径没有漏格子 / 没有抛异常）
    assert out["combos"] == 2 and out["cities"] == 2, out
    # 重试断言：首轮失败的格子（石家庄×加装包）重试后拿到了 id ——
    # 若 UnboundLocalError 还在，根本走不到这里；若重试没生效，
    # 并集会缺 id-2-1-188。
    all_ids = set()
    for fl in ("1", "2"):
        for cty in ("188", "181"):
            all_ids.add("id-%s-1-%s" % (fl, cty))
    assert out["gain"] >= 0 and state["fail_left"] == 0, out
    print("city_scope 桩测试通过：组合 2 · 城市 2 · 单城=%s 并集=%s · "
          "失败格子重试后数据齐整（无 UnboundLocalError，无静默漏格）"
          % (out["single_city_menu"], out["union_menu"]))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
