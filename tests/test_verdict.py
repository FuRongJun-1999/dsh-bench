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
    """档位能说什么（方案 §8 / 手册 §6）。

    更迭理由（本条曾锁住要改的行为）：旧断言是
    `check(d.capability_state == V.STATE_USEFUL, "快测不得改写能力状态本身")`，
    它断言「快测档下 capability_state 仍为『真有用』」——而那个『真有用』正是
    方案 §8 明文禁止的「快测给出能力 Δ 结论」（原话：「体验快测不测能力 Δ，
    不用『认证通过』『合格』」）。因此旧断言是为缺陷写的守卫，必须改向。

    原始意图（「快测不得凭空造出一个能力结论」）保留，换成正向钉法：
    快测档 ⇒ label_kind 不取 five_state、状态不落在五状态里、无徽章、不进榜；
    并留一条对照：同区间在标准测评档仍是五状态（防「把所有档都退化成快测」）。
    """
    fast = V.decide((12, 16), depth=V.DEPTH_FAST)
    check(fast.label_kind != V.LK_FIVE_STATE, "快测档 label_kind 不得为 five_state")
    check(fast.label_kind == V.LK_QUICK_CHECK, "快测档应为快测记录类：%r" % fast.label_kind)
    check(
        fast.capability_state not in V.FIVE_STATES,
        "快测档不得凭空造出能力结论，实得 %r" % fast.capability_state,
    )
    check(
        fast.capability_state == V.STATE_CAPABILITY_TRIAL,
        "快测档应为「能力试跑（观察结果，不进榜）」，实得 %r" % fast.capability_state,
    )
    check(not fast.capability_delta_concluded, "快测档不得产出能力 Δ 结论")
    check(fast.badges == [], "快测档不得签发徽章")
    check(not fast.rank_eligible, "体验快测不进能力榜")
    check(not fast.worth_trying, "快测档不进「值得试」入口")
    check(fast.interval is None, "快测档不得产出能力区间（interval 须为 None）")

    # 对照短测的观察值另放 observed_interval，照实展示但不冒充能力结论
    obs = V.decide((6, 10), depth=V.DEPTH_FAST)
    check(obs.observed_interval == (6.0, 10.0), "观察值须照实登记：%r" % (obs.observed_interval,))
    check(obs.interval is None, "观察值不得塞进 interval 冒充能力区间")
    check(any("观察结果" in n for n in obs.notes), "观察值须注明是观察结果：%r" % obs.notes)

    # 对照：同区间在标准测评档仍是五状态（防「把所有档都退化成快测」）
    s = V.decide((12, 16), depth=V.DEPTH_STANDARD)
    check(s.label_kind == V.LK_FIVE_STATE, "标准测评须出五状态：%r" % s.label_kind)
    check(s.capability_state == V.STATE_USEFUL, "标准测评档同区间应判真有用")
    check(s.rank_eligible, "标准测评达门槛后进榜")
    check(s.badges, "标准测评达门槛后应签发徽章")

    # 非排名档（如作者自测）：即使算出五状态，也不进能力榜
    other = V.decide((12, 16), depth="作者自测")
    check(not other.rank_eligible, "非排名档不进能力榜")


# --------------------------------------------------------------- 方案 §8 / §4.1 / §9

@test("t37_quick_check_record_and_banned_wording")
def t37():
    """手册 §6：快测只描述检查结果，禁用「通过」「合格」这类词。"""
    d = V.decide(
        (6, 10),
        depth=V.DEPTH_FAST,
        quick_check={"安装": "成功", "功能点": "5 个功能点 4 个可用"},
    )
    joined = "；".join(d.notes)
    check("快测记录" in joined, "须给出快测记录：%r" % d.notes)
    check("5 个功能点 4 个可用" in joined, "须逐项列出查到什么：%r" % d.notes)
    check(
        V.banned_quick_check_wording("安装通过") == ["通过"],
        "结论位的裸「通过」须被判为禁用词",
    )
    check("合格" in V.banned_quick_check_wording("快测合格"), "「合格」须被判为禁用词")
    check(
        V.banned_quick_check_wording("通过检查发现了问题") == [],
        "普通行文里的「通过检查」是描述过程，不该误伤",
    )
    check("已检查" in V.sanitize_quick_check_text("安装通过"), "禁用词须被换成描述性表述")


@test("t38_quick_check_untested_not_blank")
def t38():
    """未测就写「未测」，不留空（方案 §16）。"""
    d = V.decide((6, 10), depth=V.DEPTH_FAST)
    check(any("未测" in n for n in d.notes), "无快测项时须写「未测」：%r" % d.notes)
    check(d.cards[V.CARD_CAPABILITY]["display"].startswith("未测"), "能力栏须写未测")


@test("t39_security_scope_by_depth")
def t39():
    """方案 §8：深度审计 = 标准测评 + 六类安全专项；标准测评标不适用，不留空。"""
    std = V.decide((6, 10))
    check(not std.security_scope["applicable"], "标准测评不含六类安全专项")
    check("不适用" in std.security_scope["display"], "标准测评须标不适用：%r" % std.security_scope)
    aud = V.decide((6, 10), depth=V.DEPTH_AUDIT, security_six_threats={"提示注入": "未发现问题"})
    check(aud.security_scope["applicable"], "深度审计须出安全专项")
    check(
        set(aud.security_scope["threats"].keys()) == set(V.SECURITY_SIX_THREATS),
        "安全专项须覆盖六类威胁：%r" % aud.security_scope["threats"],
    )
    check("提示注入" not in aud.security_scope["untested"], "已给的项不算未测")
    check(len(aud.security_scope["untested"]) == 5, "其余五项须标未测：%r" % aud.security_scope["untested"])
    # 能力结论同规（方案 §8：深度审计＝标准＋安全专项，能力结论可以相同）
    check(aud.capability_state == std.capability_state, "深度审计的能力结论与标准测评同规")


@test("t40_cards_separate_and_tool_four_things")
def t40():
    """方案 §9 三张单分开、第三张里钱与安全分栏；方案 §4.1 工具型四件事分开看。"""
    d = V.decide(
        (12, 16),
        tool_results={"干对没有": "做成 3 件中的 3 件"},
        risk_level="中",
    )
    for name in (V.CARD_CAPABILITY, V.CARD_USABILITY, V.CARD_COST_AND_SAFETY):
        check(name in d.cards, "缺第 %r 张单" % name)
    third = d.cards[V.CARD_COST_AND_SAFETY]
    check("cost" in third and "safety" in third, "第三张单里钱与安全须分栏：%r" % third)
    check(set(d.tool_four_things.keys()) == set(V.FOUR_THINGS), "工具型四件事须齐全")
    check(d.tool_four_things["干对没有"] == "做成 3 件中的 3 件", "已给的项须照实记")
    check(d.tool_four_things["有没有乱来"] == "未测", "缺项写未测，不留空")
    # 完全没给工具结果时，四件事也须齐全且全为「未测」（不留空）
    bare = V.decide((12, 16)).tool_four_things
    check(set(bare.keys()) == set(V.FOUR_THINGS), "未给工具结果时四件事仍须齐全")
    check(all(v == "未测" for v in bare.values()), "未给工具结果时四件事全为未测：%r" % bare)
    check(d.risk_level == "中", "已查清的风险等级须照实记")
    check(V.decide((12, 16)).risk_level == "未评估", "未查清就写「未评估」，不硬给等级")


# --------------------------------------------------------------- 变异清单


# --------------------------------------------------------------- 第一批修复（dsh-bench#4/#7/#13）


@test("t41_harmful_not_recommended_no_badge")
def t41():
    d = V.decide((-10, -6))
    check(d.capability_state == V.STATE_HARMFUL, "期望帮倒忙")
    check(d.recommendation == "不推荐", "帮倒忙必须「不推荐」，实得 %s" % d.recommendation)
    check(not d.worth_trying, "帮倒忙不得进「值得试」")
    check(d.badges == [], "帮倒忙不得签发推荐徽章")
    check(d.rank_eligible, "帮倒忙照常进数字排名（明确负贡献不隐藏）")


@test("t42_report_status_nonofficial_not_ranked")
def t42():
    d = V.decide((6, 10), report_status="方法试验")
    check(not d.rank_eligible, "非「正式」报告不得进数字排名")
    check(any("report_status" in n for n in d.notes), "notes 须点名 report_status：%s" % d.notes)


@test("t43_invalidation_retires_report")
def t43():
    d = V.decide((6, 10), invalidation={"status": "已失效", "reason": "证据撤回"})
    check(not d.rank_eligible, "已失效报告须退榜")
    check(d.recommendation == "待复核", "已失效报告推荐须为待复核，实得 %s" % d.recommendation)


@test("t44_evidence_ok_false_reaches_pending")
def t44():
    d = V.decide((6, 10), evidence_ok=False)
    check(d.capability_state == V.STATUS_PENDING, "缺必要证据 → 待验证/待修复，实得 %s" % d.capability_state)
    check(d.label_kind == V.LK_REFUSED, "拒绝分析档位")
    check(not d.rank_eligible, "缺证据不得进排名")


# --------------------------------------------------------------- 第二批 2a（#16G1/#12①/#19J3）


@test("t45_g1_security_survives_early_return")
def t45():
    d = V.decide(install_failed=True, confirmed_severe_security_event=True)
    check(d.capability_state == V.STATE_UNTESTABLE, "能力结论保留没法测")
    check(d.recommendation == "暂停推荐", "无法测路径遇已确认安全事件须暂停推荐，实得 %s" % d.recommendation)
    check(any("能力结果保留" in n for n in d.notes), "notes 须保留能力结果说明")
    d2 = V.decide((6, 10), depth=V.DEPTH_FAST, security_investigation=True)
    check(d2.recommendation == "调查中", "快测路径遇调查中须标调查中，实得 %s" % d2.recommendation)
    d3 = V.decide((6, 10), evidence_ok=False, confirmed_severe_security_event=True)
    check(d3.recommendation == "暂停推荐", "拒绝分析路径遇已确认事件须暂停推荐，实得 %s" % d3.recommendation)


@test("t46_scenario_count_gates_ranking")
def t46():
    d = V.decide((6, 10), scenario_count=1)
    check(not d.rank_eligible, "单场景不得生成跨场景正式结论")
    check(any("场景数" in n for n in d.notes), "notes 须点名场景数")
    d2 = V.decide((6, 10), scenario_count=5)
    check(d2.rank_eligible, "多场景照常进排名")


@test("t47_audit_badge_derailed_when_threats_untested")
def t47():
    threats = {k: "未测" for k in V.SECURITY_SIX_THREATS}
    d = V.decide((6, 10), depth=V.DEPTH_AUDIT, security_six_threats=threats)
    check(d.badges == ["标准测评·真有用"], "六类全未测的深度审计徽章须降标准档，实得 %s" % d.badges)
    check(any("深度审计（指定 0/6 项）" in n for n in d.notes), "notes 须注明覆盖范围")
    threats2 = {k: "已验证通过" for k in threats}
    d2 = V.decide((6, 10), depth=V.DEPTH_AUDIT, security_six_threats=threats2)
    check(d2.badges == ["深度审计·真有用"], "六类全测的深度审计照常签发深度档徽章，实得 %s" % d2.badges)

MUTATIONS = [
    # --- §7 边界表逐行 ---
    {"id": "V41", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif state == STATE_HARMFUL:\n        recommendation = \"不推荐\"",
     "replace": "    elif state == STATE_HARMFUL:\n        recommendation = \"可推荐\"",
     "count": 1, "expect": ["t41_harmful_not_recommended_no_badge"],
     "rule": "dsh-bench#13：帮倒忙必须不推荐"},
    {"id": "V42", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if report_status != \"正式\":\n        rank_eligible = False",
     "replace": "    if False:\n        rank_eligible = False",
     "count": 1, "expect": ["t42_report_status_nonofficial_not_ranked"],
     "rule": "dsh-bench#4：非正式报告退榜"},
    {"id": "V43", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif inv_status and inv_status != \"有效\":\n        recommendation = \"待复核\"",
     "replace": "    elif inv_status and inv_status != \"已失效\":\n        recommendation = \"待复核\"",
     "count": 1, "expect": ["t43_invalidation_retires_report"],
     "rule": "dsh-bench#4：已失效退榜待复核"},
    {"id": "V45", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if confirmed_severe_security_event:\n        early_rec = \"暂停推荐\"",
     "replace": "    if False:\n        early_rec = \"暂停推荐\"",
     "count": 1, "expect": ["t45_g1_security_survives_early_return"],
     "rule": "dsh-bench#16 G1：安全通道覆盖提前返回路径"},
    {"id": "V46", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if scenario_count is not None and not cross_scenario_claim_allowed(int(scenario_count)):\n        rank_eligible = False",
     "replace": "    if False:\n        rank_eligible = False",
     "count": 1, "expect": ["t46_scenario_count_gates_ranking"],
     "rule": "dsh-bench#12①：单场景退出数字排名"},
    {"id": "V47", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "                badge_depth = DEPTH_STANDARD",
     "replace": "                badge_depth = depth",
     "count": 1, "expect": ["t47_audit_badge_derailed_when_threats_untested"],
     "rule": "dsh-bench#19 J3：有未测专项的深度审计徽章降标准档"},
    {"id": "V01", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif L > a:\n        state = STATE_USEFUL",
     "replace": "    elif L > a + 1:\n        state = STATE_USEFUL",
     "count": 1, "expect": ["t01_boundary_6_10_useful", "t46_scenario_count_gates_ranking", "t47_audit_badge_derailed_when_threats_untested"],
     "rule": "§7 [6,10]→真有用（正向门槛）"},
    {"id": "V02", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    elif U < -b:\n        state = STATE_HARMFUL",
     "replace": "    elif U < -b - 1:\n        state = STATE_HARMFUL",
     "count": 1, "expect": ["t02_boundary_neg10_neg6_harmful", "t41_harmful_not_recommended_no_badge"],
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
     "count": 1, "expect": ["t10_refuse_missing_evidence", "t44_evidence_ok_false_reaches_pending"], "rule": "§7/7 缺必要证据→拒绝分析（分支可达性同点）"},
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
     "count": 1, "expect": ["t13_install_failed_untestable_and_install_recorded", "t45_g1_security_survives_early_return"],
     "rule": "§7 安装失败⇒没法测＋安装失败"},
    {"id": "V14", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if confirmed_severe_security_event:\n        recommendation = \"暂停推荐\"",
     "replace": "    if confirmed_severe_security_event:\n        recommendation = \"可推荐\"",
     "count": 1, "expect": ["t15_security_confirmed_suspend_recommendation"],
     "rule": "§7 已确认严重安全事件⇒暂停推荐"},
    {"id": "V15", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    worth_trying = bool(\n        rank_eligible and not confirmed_severe_security_event and not security_investigation\n        and recommendation == \"可推荐\"\n    )",
     "replace": "    worth_trying = bool(rank_eligible)",
     "count": 1, "expect": ["t16_security_confirmed_not_worth_trying", "t41_harmful_not_recommended_no_badge"],
     "rule": "§7/13 暂停推荐与帮倒忙不进「值得试」入口"},
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
     "anchor": "            label_kind=LK_INVALID_RUN,",
     "replace": "            label_kind=LK_REFUSED,",
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
     "count": 1, "expect": ["t33_cross_scenario_claim_guard", "t46_scenario_count_gates_ranking"], "rule": "§7 场景只有一个"},
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
     "count": 1, "expect": ["t36_depth_not_ranked_fast_test"], "rule": "方案 §8 非排名档不进能力榜"},
    {"id": "V37", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "            label_kind=LK_QUICK_CHECK,",
     "replace": "            label_kind=LK_FIVE_STATE,",
     "count": 1, "expect": ["t36_depth_not_ranked_fast_test"],
     "rule": "方案 §8 快测档 label_kind 不得为 five_state（快测不出能力 Δ 结论）"},
    {"id": "V38", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if depth == DEPTH_FAST:\n        record_lines, banned = quick_check_record(quick_check)",
     "replace": "    if False:\n        record_lines, banned = quick_check_record(quick_check)",
     "count": 1,
     "expect": ["t36_depth_not_ranked_fast_test",
                "t37_quick_check_record_and_banned_wording",
                "t38_quick_check_untested_not_blank"],
     "rule": "方案 §8 快测档须走快测分支（不得落到五状态）"},
    {"id": "V39", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    hits = [w for w in words if w in text]",
     "replace": "    hits = []",
     "count": 1, "expect": ["t37_quick_check_record_and_banned_wording"],
     "rule": "手册 §6 快测禁用词检查"},
    {"id": "V40", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if not items:\n        return [\"快测记录：未测（没有可展示的检查项）\"], []",
     "replace": "    if not items:\n        return [], []",
     "count": 1, "expect": ["t38_quick_check_untested_not_blank"],
     "rule": "方案 §16 未测就写「未测」，不留空"},
    {"id": "V41", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    if depth != DEPTH_AUDIT:",
     "replace": "    if False:",
     "count": 1, "expect": ["t39_security_scope_by_depth"],
     "rule": "方案 §8 标准测评不含六类安全专项（须标不适用）"},
    {"id": "V42", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "        detail[name] = given.get(name) or \"未测\"",
     "replace": "        detail[name] = given.get(name, \"已通过\")",
     "count": 1, "expect": ["t39_security_scope_by_depth"],
     "rule": "手册 §4 未测（没考）≠ 已验证通过（考了有通过证据）"},
    {"id": "V43", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "    four_things = {k: \"未测\" for k in FOUR_THINGS}",
     "replace": "    four_things = {k: \"\" for k in FOUR_THINGS}",
     "count": 1, "expect": ["t40_cards_separate_and_tool_four_things"],
     "rule": "方案 §4.1 工具型四件事缺项写未测（不留空）"},
    {"id": "V44", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "            four_things[k] = tool_results.get(k, \"未测\")",
     "replace": "            four_things[k] = tool_results.get(k, \"\")",
     "count": 1, "expect": ["t40_cards_separate_and_tool_four_things"],
     "rule": "方案 §4.1 工具型四件事部分缺项也写未测"},
    {"id": "V45", "target": "scripts/verdict.py", "test_file": "tests/test_verdict.py",
     "anchor": "            interval=None,\n            observed_interval=observed,",
     "replace": "            interval=observed,\n            observed_interval=observed,",
     "count": 1, "expect": ["t36_depth_not_ranked_fast_test"],
     "rule": "方案 §8 快测档不得产出能力区间（观察值不得冒充能力区间）"},
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
