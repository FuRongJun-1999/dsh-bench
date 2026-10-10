#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守卫：五状态判定算法（逐行落成 §7 边界表 + 组合规则）。

真源：
  - docs/spec/端到端验证计划-v0.md §7（判定算法边界回归）
  - docs/spec/工程协议手册-v1.1.md §2（五状态与判定算法）、§5（成本口径）、§13.7（排名/时效）

变异风格（本仓惯例）：
  锚点自检 → 源码级短路 → **恰好**命中期望红项，多红少红都算错。
  变异清单见本文件末尾 MUTATIONS，由 tests/mutate_selfcheck.py 执行自证。

跑法：python -X utf8 tests/test_verdict.py [--list|--only ID|--json]
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


V = _load("verdict_under_test", "scripts/verdict.py")

TESTS = []


def test(tid):
    def deco(fn):
        TESTS.append((tid, fn))
        return fn

    return deco


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# --------------------------------------------------------------- §7 边界表逐行

@test("t01_boundary_6_10_useful")
def t01():
    d = V.decide((6, 10))
    check(d.capability_state == V.STATE_USEFUL, "期望真有用，实得 %s" % d.capability_state)
    check(d.precision_ok, "半宽 2 应满足精度门槛")
    check(d.rank_eligible, "真有用且精度达标应进数字排名")


@test("t02_boundary_neg10_neg6_harmful")
def t02():
    d = V.decide((-10, -6))
    check(d.capability_state == V.STATE_HARMFUL, "期望帮倒忙，实得 %s" % d.capability_state)
    check(d.rank_eligible, "帮倒忙也应正常收录进排名（明确负贡献不隐藏）")


@test("t03_boundary_neg2_2_equivalent")
def t03():
    d = V.decide((-2, 2))
    check(d.capability_state == V.STATE_EQUIVALENT, "期望没啥差别，实得 %s" % d.capability_state)
    check(d.rank_eligible, "没啥差别在精度达标时进排名")


@test("t04_boundary_4_8_uncertain_cross_positive")
def t04():
    d = V.decide((4, 8))
    check(d.capability_state == V.STATE_UNCERTAIN, "跨过正向门槛应还说不准，实得 %s" % d.capability_state)
    check(not d.rank_eligible, "还说不准不进数字排名")


@test("t05_boundary_neg8_neg4_uncertain_cross_negative")
def t05():
    d = V.decide((-8, -4))
    check(d.capability_state == V.STATE_UNCERTAIN, "跨过负向门槛应还说不准，实得 %s" % d.capability_state)


@test("t06_boundary_5_8_uncertain_exact_positive")
def t06():
    d = V.decide((5, 8))
    check(d.capability_state == V.STATE_UNCERTAIN, "正好卡门槛（L=a）应还说不准，实得 %s" % d.capability_state)


@test("t07_boundary_neg8_neg5_uncertain_exact_negative")
def t07():
    d = V.decide((-8, -5))
    check(d.capability_state == V.STATE_UNCERTAIN, "正好卡门槛（U=-b）应还说不准，实得 %s" % d.capability_state)


@test("t08_boundary_neg9_9_uncertain_low_precision")
def t08():
    d = V.decide((-9, 9))
    check(d.capability_state == V.STATE_UNCERTAIN, "精度不足应还说不准，实得 %s" % d.capability_state)
    check(d.half_width == 9.0, "半宽应为 9，实得 %r" % d.half_width)
    check(not d.precision_ok, "半宽 9 > 5，precision_ok 必须为假")


@test("t09_boundary_6_30_uncertain_direction_clear_low_precision")
def t09():
    d = V.decide((6, 30))
    check(d.capability_state == V.STATE_UNCERTAIN, "方向明确但精度不足应还说不准，实得 %s" % d.capability_state)
    check(not d.precision_ok, "半宽 12 > 5，precision_ok 必须为假")
    check(any("精度不足" in r for r in d.reasons), "原因须写明精度不足：%r" % d.reasons)


# --------------------------------------------------------------- 拒绝分析

@test("t10_refuse_missing_evidence")
def t10():
    d = V.decide((6, 10), evidence_ok=False)
    check(d.capability_state == V.STATUS_PENDING, "缺必要证据应拒绝分析，实得 %s" % d.capability_state)
    check(d.label_kind == "refused", "label_kind 应为 refused")
    check(d.interval is None, "拒绝分析不得生成数值区间")


@test("t11_refuse_L_gt_U")
def t11():
    d = V.decide((8, 4))
    check(d.capability_state == V.STATUS_PENDING, "L>U 应拒绝分析，实得 %s" % d.capability_state)
    check(any("L>U" in r for r in d.reasons), "原因须点名 L>U：%r" % d.reasons)


@test("t12_refuse_nan")
def t12():
    d = V.decide((float("nan"), 10))
    check(d.capability_state == V.STATUS_PENDING, "NaN 应拒绝分析，实得 %s" % d.capability_state)
    check(any("NaN" in r for r in d.reasons), "原因须点名 NaN：%r" % d.reasons)


# --------------------------------------------------------------- 组合规则

@test("t13_install_failed_untestable_and_install_recorded")
def t13():
    d = V.decide(install_failed=True)
    check(d.capability_state == V.STATE_UNTESTABLE, "安装失败能力应没法测，实得 %s" % d.capability_state)
    check(d.install_status == V.INSTALL_FAILED, "安装环节须单独记「安装失败」，实得 %r" % d.install_status)
    check(any("安装环节" in n and "失败" in n for n in d.notes), "须同时记录安装环节失败：%r" % d.notes)


@test("t14_security_confirmed_capability_kept")
def t14():
    d = V.decide((12, 16), confirmed_severe_security_event=True)
    check(d.capability_state == V.STATE_USEFUL, "已确认严重安全事件不得抹掉能力结果，实得 %s" % d.capability_state)
    check(d.interval == (12.0, 16.0), "区间不得被安全标签改动")


@test("t15_security_confirmed_suspend_recommendation")
def t15():
    d = V.decide((12, 16), confirmed_severe_security_event=True)
    check(d.recommendation == "暂停推荐", "已确认严重事件应暂停推荐，实得 %r" % d.recommendation)


@test("t16_security_confirmed_not_worth_trying")
def t16():
    d = V.decide((12, 16), confirmed_severe_security_event=True)
    check(not d.worth_trying, "暂停推荐的版本不得进「值得试」入口")


@test("t17_expired_exits_ranking_and_badge")
def t17():
    d = V.decide((12, 16), tested_at="2026-01-01", now="2026-06-01")
    check(d.expired, "超过 90 天须标记过期")
    check(d.age_days == 151, "天数应为 151，实得 %r" % d.age_days)
    check(not d.rank_eligible, "过期须退出有效排名")
    check(not d.badges, "过期徽章须失效")
    check(d.capability_state == V.STATE_USEFUL, "历史结论保留，不因过期改能力状态")


@test("t18_uncertain_untestable_displayed_not_ranked")
def t18():
    u = V.decide((4, 8))
    n = V.decide(task_not_applicable=True)
    check(u.capability_state == V.STATE_UNCERTAIN and not u.rank_eligible, "还说不准不进数字排名")
    check(n.capability_state == V.STATE_UNTESTABLE and not n.rank_eligible, "没法测不进数字排名")
    check(n.install_status in (V.INSTALL_UNTESTED,), "任务不适用时安装环节应保持未测")
    check(bool(n.notes) and bool(n.reasons), "照常展示：须有原因说明")


@test("t19_evidence_limited_exits_ranking")
def t19():
    d = V.decide((12, 16), original_deleted=True, redacted_evidence_sufficient=False)
    check(d.evidence_verification_limited, "须标证据核验受限")
    check(not d.rank_eligible, "证据核验受限须退出有效数字排名")
    check(d.recommendation == "待复核", "须退出推荐徽章，实得 %r" % d.recommendation)
    check(d.capability_state == V.STATE_USEFUL, "能力结果保留")


@test("t20_invalid_run_no_capability_state")
def t20():
    d = V.decide(run_valid=False)
    check(d.capability_state == V.STATUS_INVALID_RUN, "实验无效是运行层状态，实得 %s" % d.capability_state)
    check(d.capability_state not in V.FIVE_STATES, "实验无效不得输出能力五状态")
    check(not d.rank_eligible, "实验无效不进排名")


@test("t21_invalid_run_is_not_evidence_insufficient")
def t21():
    inv = V.decide(run_valid=False)
    ins = V.decide(interval=None)
    check(inv.label_kind == "invalid_run", "实验无效 label_kind 应为 invalid_run")
    check(ins.label_kind == "refused", "证据不足 label_kind 应为 refused")
    check(inv.capability_state != ins.capability_state, "两者不得混为一谈")


@test("t22_equivalence_is_not_absolute_zero")
def t22():
    check("不是绝对一样" in V.STATE_PLAIN[V.STATE_EQUIVALENT], "「没啥差别」的白话须写明不是绝对一样")


# --------------------------------------------------------------- 时效

@test("t25_expiry_is_90_days")
def t25():
    check(V.VALIDITY_DAYS_DEFAULT == 90, "默认有效期须为 90 天")
    check(V.expires_at("2026-10-01").isoformat() == "2026-12-30", "测完 90 天应为 2026-12-30")


@test("t26_expiry_date_change_is_not_renewal")
def t26():
    check(V.expiry_consistent("2026-10-01", "2026-12-30"), "一致时应为真")
    check(not V.expiry_consistent("2026-10-01", "2027-12-30"), "只改日期不算续期，须判不一致")


# --------------------------------------------------------------- 成本口径

@test("t27_cost_zero_success_no_division")
def t27():
    r = V.unit_success_cost(12.0, 0)
    check(r["value"] is None, "成功数为 0 不得给出比值")
    check("不适用" in r["display"], "应显示不适用：%r" % r["display"])
    check(r["total_cost"] == 12.0, "仍须展示实际总花费")


@test("t28_cost_incremental_nonpositive")
def t28():
    r = V.incremental_unit_success_cost(12.0, 10.0, 5, 5)
    check(r["value"] is None, "增量成功数 ≤0 不得给比值")
    check("不适用" in r["display"], "应显示不适用：%r" % r["display"])
    check(r["delta_cost"] == 2.0 and r["delta_successes"] == 0, "须单列花费差与结果差")


@test("t29_cost_unmeasured_not_zero")
def t29():
    r = V.unit_success_cost(None, None)
    check(r["value"] is None, "观测不到不得给数字")
    check("没测到" in r["display"], "应写没测到，不得填 0：%r" % r["display"])
    r2 = V.incremental_unit_success_cost(None, 1.0, 1, 1)
    check("没测到" in r2["display"], "增量侧同样不得填 0：%r" % r2["display"])


# --------------------------------------------------------------- 样本层

@test("t30_sample_layer_valid_runs")
def t30():
    runs = [{"run_id": "a", "valid": True}, {"run_id": "b", "valid": False}]
    valid, dropped = V.select_valid_runs(runs)
    check([r["run_id"] for r in valid] == ["a"], "有效运行筛选错误")
    check([r["run_id"] for r in dropped] == ["b"], "无效运行须作废留痕")


@test("t31_sample_layer_incomplete_pairs")
def t31():
    pairs = [{"pair_id": "p1", "b0": 1, "p": 2}, {"pair_id": "p2", "b0": 1, "p": None}]
    check(V.incomplete_pairs(pairs) == ["p2"], "缺 B0 或 P 的配对须被识别")


@test("t32_sample_layer_weights")
def t32():
    check(V.weights_ok({"a": 0.5, "b": 0.5}), "权重和为 1 应通过")
    check(not V.weights_ok({"a": 0.5, "b": 0.4}), "权重和不为 1 须拒绝")
    check(not V.weights_ok({}), "空权重须拒绝")


@test("t33_cross_scenario_claim_guard")
def t33():
    check(V.cross_scenario_claim_allowed(2), "≥2 个场景可生成跨场景结论")
    check(not V.cross_scenario_claim_allowed(1), "只有一个场景不得生成跨场景正式结论")


@test("t34_analysis_manifest_required")
def t34():
    missing = V.check_analysis_manifest({"analysis_id": "a"})
    for k in ("seed", "rng", "library_versions", "bootstrap_resamples", "percentile_method", "scenario_aggregation"):
        check(k in missing, "manifest 缺项须点名 %s，实得 %r" % (k, missing))
    check(V.check_analysis_manifest({k: "x" for k in (
        "analysis_id", "method", "seed", "rng", "library_versions", "bootstrap_resamples",
        "percentile_method", "scenario_aggregation")}) == [], "齐备时不应报缺项")


# --------------------------------------------------------------- 门槛与档位

@test("t35_first_domain_gate_a_b_5")
def t35():
    check(V.A_DEFAULT == 5.0 and V.B_DEFAULT == 5.0, "启动候选门槛须为 a=b=5")
    check(V.MAX_HALF_WIDTH_DEFAULT == 5.0, "精度门槛半宽须 ≤5")
    check(V.decide((6, 10)).interval == (6.0, 10.0), "区间原样保留")


@test("t36_depth_not_ranked_fast_test")
def t36():
    d = V.decide((12, 16), depth=V.DEPTH_FAST)
    check(not d.rank_eligible, "体验快测不进能力榜")
    check(d.capability_state == V.STATE_USEFUL, "快测不得改写能力状态本身")
    s = V.decide((12, 16), depth=V.DEPTH_STANDARD)
    check(s.rank_eligible, "标准测评达门槛后进榜")


# --------------------------------------------------------------- 变异清单

MUTATIONS = [
    # --- §7 边界表逐行 ---
    {"id": "V01", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif L > a:\n        state = STATE_USEFUL",
     "replace": "    elif L > a + 1:\n        state = STATE_USEFUL",
     "count": 1, "expect": ["t01_boundary_6_10_useful"],
     "rule": "§7 [6,10]→真有用（正向门槛）"},
    {"id": "V02", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif U < -b:\n        state = STATE_HARMFUL",
     "replace": "    elif U < -b - 1:\n        state = STATE_HARMFUL",
     "count": 1, "expect": ["t02_boundary_neg10_neg6_harmful"],
     "rule": "§7 [-10,-6]→帮倒忙（负向门槛）"},
    {"id": "V03", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif L > -b and U < a:",
     "replace": "    elif L > 0 and U < a:",
     "count": 1, "expect": ["t03_boundary_neg2_2_equivalent"],
     "rule": "§7 [-2,2]→没啥差别（等效范围）"},
    {"id": "V04", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif L > a:\n        state = STATE_USEFUL",
     "replace": "    elif L > a - 3:\n        state = STATE_USEFUL",
     "count": 1,
     "expect": ["t04_boundary_4_8_uncertain_cross_positive", "t06_boundary_5_8_uncertain_exact_positive",
                "t18_uncertain_untestable_displayed_not_ranked"],
     "rule": "§7 [4,8] 跨正向门槛 / [5,8] 正好卡门槛→还说不准"},
    {"id": "V05", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif U < -b:\n        state = STATE_HARMFUL",
     "replace": "    elif U < -b + 2:\n        state = STATE_HARMFUL",
     "count": 1,
     "expect": ["t05_boundary_neg8_neg4_uncertain_cross_negative",
                "t07_boundary_neg8_neg5_uncertain_exact_negative"],
     "rule": "§7 [-8,-4] / [-8,-5]→还说不准"},
    {"id": "V06", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif L > a:\n        state = STATE_USEFUL",
     "replace": "    elif L >= a:\n        state = STATE_USEFUL",
     "count": 1, "expect": ["t06_boundary_5_8_uncertain_exact_positive"],
     "rule": "§7 [5,8] 正好卡门槛（L=a 不算真有用）"},
    {"id": "V07", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif U < -b:\n        state = STATE_HARMFUL",
     "replace": "    elif U <= -b:\n        state = STATE_HARMFUL",
     "count": 1, "expect": ["t07_boundary_neg8_neg5_uncertain_exact_negative"],
     "rule": "§7 [-8,-5] 正好卡门槛（U=-b 不算帮倒忙）"},
    {"id": "V08", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    precision_ok = half <= max_half_width",
     "replace": "    precision_ok = True",
     "count": 1,
     "expect": ["t08_boundary_neg9_9_uncertain_low_precision",
                "t09_boundary_6_30_uncertain_direction_clear_low_precision"],
     "rule": "§7 精度门槛半宽 ≤5"},
    {"id": "V09", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "            \"精度不足：半宽 %.3f > 上限 %.3f，即使方向明确也判「还说不准」\" % (half, max_half_width)",
     "replace": "            \"区间太宽：半宽 %.3f > 上限 %.3f\" % (half, max_half_width)",
     "count": 1, "expect": ["t09_boundary_6_30_uncertain_direction_clear_low_precision"],
     "rule": "§7 [6,30] 方向明确但精度不足的说明"},
    # --- 拒绝分析 ---
    {"id": "V10", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if not algorithm_validated or not evidence_ok or not interval_ok(interval):",
     "replace": "    if not algorithm_validated or not interval_ok(interval):",
     "count": 1, "expect": ["t10_refuse_missing_evidence"], "rule": "§7 缺必要证据→拒绝分析"},
    {"id": "V11", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    return float(L) <= float(U)",
     "replace": "    return True",
     "count": 1, "expect": ["t11_refuse_L_gt_U"], "rule": "§7 L>U→拒绝分析"},
    {"id": "V12", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    L, U = interval\n    if not (_is_finite(L) and _is_finite(U)):\n        return False\n    return float(L) <= float(U)",
     "replace": "    L, U = interval\n    return True",
     "count": 1, "expect": ["t12_refuse_nan", "t11_refuse_L_gt_U"], "rule": "§7 NaN→拒绝分析"},
    # --- 组合规则 ---
    {"id": "V13", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        untestable_reasons.append(\"安装失败\")",
     "replace": "        pass",
     "count": 1, "expect": ["t13_install_failed_untestable_and_install_recorded"],
     "rule": "§7 安装失败⇒没法测＋安装失败"},
    {"id": "V14", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if confirmed_severe_security_event:\n        recommendation = \"暂停推荐\"",
     "replace": "    if confirmed_severe_security_event:\n        recommendation = \"可推荐\"",
     "count": 1, "expect": ["t15_security_confirmed_suspend_recommendation"],
     "rule": "§7 已确认严重安全事件⇒暂停推荐"},
    {"id": "V15", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    worth_trying = bool(\n        rank_eligible and not confirmed_severe_security_event and not security_investigation\n    )",
     "replace": "    worth_trying = bool(rank_eligible)",
     "count": 1, "expect": ["t16_security_confirmed_not_worth_trying"],
     "rule": "§7 暂停推荐不进「值得试」入口"},
    {"id": "V16", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if confirmed_severe_security_event:\n        recommendation = \"暂停推荐\"",
     "replace": "    if confirmed_severe_security_event:\n        state = STATE_HARMFUL\n        recommendation = \"暂停推荐\"",
     "count": 1, "expect": ["t14_security_confirmed_capability_kept"],
     "rule": "§7 能力结果不被安全标签改成零分或抹掉"},
    {"id": "V17", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    expired = age_days is not None and age_days > validity_days",
     "replace": "    expired = False",
     "count": 1, "expect": ["t17_expired_exits_ranking_and_badge"],
     "rule": "§7 超 90 天⇒退出排名与徽章"},
    {"id": "V18", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        state in NUMERIC_RANK_STATES\n",
     "replace": "        True\n",
     "count": 1,
     "expect": ["t18_uncertain_untestable_displayed_not_ranked",
                "t04_boundary_4_8_uncertain_cross_positive"],
     "rule": "§7 还说不准/没法测不进数字排名"},
    {"id": "V19", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    evidence_verification_limited = bool(original_deleted and not redacted_evidence_sufficient)",
     "replace": "    evidence_verification_limited = False",
     "count": 1, "expect": ["t19_evidence_limited_exits_ranking"],
     "rule": "§7 原件删除且脱敏证据不足⇒证据核验受限退出排名"},
    {"id": "V20", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if not run_valid:",
     "replace": "    if False:",
     "count": 1,
     "expect": ["t20_invalid_run_no_capability_state", "t21_invalid_run_is_not_evidence_insufficient"],
     "rule": "§7 实验无效≠证据不足"},
    {"id": "V21", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "            label_kind=\"invalid_run\",",
     "replace": "            label_kind=\"refused\",",
     "count": 1, "expect": ["t21_invalid_run_is_not_evidence_insufficient"],
     "rule": "§7 实验无效与证据不足的分类标识"},
    {"id": "V22", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    STATE_EQUIVALENT: \"测得够多，也测得够稳，没看到值得在意的差别（不是绝对一样）。\",",
     "replace": "    STATE_EQUIVALENT: \"测得够多，也测得够稳，没看到差别（绝对一样）。\",",
     "count": 1, "expect": ["t22_equivalence_is_not_absolute_zero"],
     "rule": "§7/方案 §5「没啥差别」不是绝对一样"},
    # --- 时效 ---
    {"id": "V25", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    return d + timedelta(days=validity_days)",
     "replace": "    return d + timedelta(days=validity_days + 1)",
     "count": 1,
     "expect": ["t25_expiry_is_90_days", "t26_expiry_date_change_is_not_renewal"],
     "rule": "§7 成绩默认只管 90 天"},
    {"id": "V26", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        return _to_date(declared_expires_at) == expires_at(tested_at, validity_days)",
     "replace": "        return True",
     "count": 1, "expect": ["t26_expiry_date_change_is_not_renewal"],
     "rule": "§7 改日期不算续期"},
    # --- 成本口径 ---
    {"id": "V27", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if successes == 0:",
     "replace": "    if False:",
     "count": 1, "expect": ["t27_cost_zero_success_no_division"], "rule": "§5 成本分母为 0 禁止除零"},
    {"id": "V28", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if delta_successes <= 0:",
     "replace": "    if delta_successes < 0:",
     "count": 1, "expect": ["t28_cost_incremental_nonpositive"],
     "rule": "§5 增量成功数 ≤0 单列不适用"},
    {"id": "V29", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "\"display\": \"没测到\",",
     "replace": "\"display\": \"0\",",
     "count": 2, "expect": ["t29_cost_unmeasured_not_zero"], "rule": "§5 测不到的不能填 0"},
    # --- 样本层 ---
    {"id": "V30", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        (valid if r.get(\"valid\", True) else dropped).append(r)",
     "replace": "        valid.append(r)",
     "count": 1, "expect": ["t30_sample_layer_valid_runs"], "rule": "§7 无效运行混入须作废留痕"},
    {"id": "V31", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        if not p.get(\"b0\") or not p.get(\"p\"):",
     "replace": "        if False:",
     "count": 1, "expect": ["t31_sample_layer_incomplete_pairs"], "rule": "§7 缺失配对"},
    {"id": "V32", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    return abs(total - expected_total) <= tol",
     "replace": "    return True",
     "count": 1, "expect": ["t32_sample_layer_weights"], "rule": "§7 错误权重"},
    {"id": "V33", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    return scenario_count >= 2",
     "replace": "    return True",
     "count": 1, "expect": ["t33_cross_scenario_claim_guard"], "rule": "§7 场景只有一个"},
    {"id": "V34", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    return [k for k in required if manifest.get(k) in (None, \"\", [])]",
     "replace": "    return []",
     "count": 1, "expect": ["t34_analysis_manifest_required"],
     "rule": "§7 bootstrap RNG/版本不同须登记（manifest 缺项）"},
    # --- 门槛与档位 ---
    {"id": "V35", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "A_DEFAULT = 5.0",
     "replace": "A_DEFAULT = 4.0",
     "count": 1,
     "expect": ["t35_first_domain_gate_a_b_5", "t06_boundary_5_8_uncertain_exact_positive"],
     "rule": "手册 §2.4 首域 a=b=5"},
    {"id": "V36", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        and depth in RANKING_DEPTHS\n    )",
     "replace": "    )",
     "count": 1, "expect": ["t36_depth_not_ranked_fast_test"], "rule": "方案 §8 体验快测不进能力榜"},
]


# --------------------------------------------------------------- 运行器


def run_all(only=None) -> dict:
    passed, failures = [], []
    for tid, fn in TESTS:
        if only and tid != only:
            continue
        try:
            fn()
            passed.append(tid)
        except Exception as exc:  # noqa: BLE001
            failures.append({"id": tid, "message": "%s: %s" % (type(exc).__name__, exc)})
    return {"total": len(passed) + len(failures), "passed": passed, "failures": failures}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        for tid, _ in TESTS:
            print(tid)
        return 0
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    result = run_all(only)
    if "--json" in argv:
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["failures"] else 0
    for tid in result["passed"]:
        print("PASS  %s" % tid)
    for f in result["failures"]:
        print("FAIL  %s" % f["id"])
        print("        - %s" % f["message"])
    print("-" * 60)
    print("共 %d 项，通过 %d，失败 %d" % (result["total"], len(result["passed"]), len(result["failures"])))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
