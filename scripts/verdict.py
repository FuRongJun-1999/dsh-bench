#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-bench 插件测评站 · 五状态判定算法（MVP 核心可验证件）

真源（本仓内）：
  - docs/spec/工程协议手册-v1.1.md  §2（结果判定：五种状态，判定算法固定）
  - docs/spec/端到端验证计划-v0.md   §7（判定算法边界回归）

本模块是纯函数：不读网络、不读时钟（时间由调用方传入）、不写状态。
所有结论都从区间 `[L,U]` 与显式证据标志算出；生成器/校验器不得手填分数。

硬约束（方案 §3/§5/§9/§16；手册 §2.4/§2.5/§2.6/§13.7）：
  - 不给总分；能力 / 好不好用 / 花费与安全 三张单分开。
  - 「还说不准」「没法测」照常展示，但不进数字排名。
  - 安全提醒单独显示；能力结果不被安全事件改成零分或抹掉。
  - 成绩默认 90 天有效，到期退出推荐与排名；改日期不算续期。
  - 安装失败 ⇒ 能力「没法测」+ 安装环节「失败」两条分别记。
  - 「实验无效」≠「证据不足」：前者作废重跑，后者信息不够。

手册 §2.4 精确映射（a=b=5、半宽 ≤5 为启动候选，本 MVP 采用）：
  1. 数据或算法检查失败            → 运行状态「实验无效/待重跑」，不出能力五状态
  2. 安装失败 / 任务不适用 / 环境不支持 → 「没法测」并附原因
  3. 算法未校验 / 证据缺失 / 结果无法计算 → 拒绝分析，标「待验证/待修复」
     若实验数据有效、计算已校验，但有效样本或精度不足 → 「还说不准」
  4. 精度达标前提下 L>a   → 真有用
  5. 精度达标前提下 U<-b  → 帮倒忙
  6. 精度达标前提下 L>-b 且 U<a → 没啥差别（本次范围内实际等效，不是绝对一样）
  7. 其余（含区间刚好等于阈值）→ 还说不准
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

# ---------------------------------------------------------------- 常量

STATE_USEFUL = "真有用"
STATE_HARMFUL = "帮倒忙"
STATE_EQUIVALENT = "没啥差别"
STATE_UNCERTAIN = "还说不准"
STATE_UNTESTABLE = "没法测"

# 非五状态：过程层状态，照常展示，绝不当成能力标签
STATUS_INVALID_RUN = "实验无效/待重跑"
STATUS_PENDING = "待验证/待修复"

# 体验快测专用结论：快测不测能力 Δ，不能出五状态能力结论
# （方案 §8：需要对照的短测只能叫「能力试跑」，标「观察结果，不进榜」；
#   手册 §6：快测只用「快测记录：查到哪些、结果怎样」，禁用「通过」「合格」。）
STATE_CAPABILITY_TRIAL = "能力试跑（观察结果，不进榜）"

FIVE_STATES = (
    STATE_USEFUL,
    STATE_HARMFUL,
    STATE_EQUIVALENT,
    STATE_UNCERTAIN,
    STATE_UNTESTABLE,
)
# 只有这三种进数字排名（手册 §13.7）
NUMERIC_RANK_STATES = (STATE_USEFUL, STATE_HARMFUL, STATE_EQUIVALENT)
# 只有这些状态算「能力 Δ 结论」——快测一律不得输出它们（方案 §8）
CAPABILITY_DELTA_STATES = (STATE_USEFUL, STATE_HARMFUL, STATE_EQUIVALENT, STATE_UNCERTAIN)

A_DEFAULT = 5.0            # 正向门槛（最小有意义提升）
B_DEFAULT = 5.0            # 可接受退化幅度
MAX_HALF_WIDTH_DEFAULT = 5.0   # 精度门槛：区间半宽上限
VALIDITY_DAYS_DEFAULT = 90     # 成绩默认有效期

DEPTH_FAST = "体验快测"
DEPTH_STANDARD = "标准测评"
DEPTH_AUDIT = "深度审计"
RANKING_DEPTHS = (DEPTH_STANDARD, DEPTH_AUDIT)

# label_kind 取值
LK_FIVE_STATE = "five_state"            # 标准测评／深度审计：五状态能力结论
LK_QUICK_CHECK = "quick_check"          # 体验快测：快测记录／能力试跑，非能力 Δ 结论
LK_REFUSED = "refused"                  # 拒绝分析
LK_INVALID_RUN = "invalid_run"          # 实验无效

# 工具型四件事（方案 §4.1）与三张单（方案 §9）
FOUR_THINGS = ("干对没有", "有没有乱来", "花得多不多", "出错能不能接着干")
CARD_CAPABILITY = "能力"
CARD_USABILITY = "好不好用"
CARD_COST_AND_SAFETY = "花费与安全"

RISK_UNASSESSED = "未评估"

STATE_DISPLAY = {
    STATE_USEFUL: "✅ 真有用",
    STATE_HARMFUL: "❌ 帮倒忙",
    STATE_EQUIVALENT: "➖ 没啥差别",
    STATE_UNCERTAIN: "❓ 还说不准",
    STATE_UNTESTABLE: "🚧 没法测",
    STATE_CAPABILITY_TRIAL: "🔎 能力试跑（观察结果，不进榜）",
    STATUS_INVALID_RUN: "⛔ 实验无效/待重跑",
    STATUS_PENDING: "🕓 待验证/待修复",
}

STATE_PLAIN = {
    STATE_USEFUL: "多跑几遍后，结果还是明显更好。",
    STATE_HARMFUL: "装了以后，结果明显变差。",
    STATE_EQUIVALENT: "测得够多，也测得够稳，没看到值得在意的差别（不是绝对一样）。",
    STATE_UNCERTAIN: "现在的结果不够，不能说有用，也不能说没用。",
    STATE_UNTESTABLE: "装不上、环境不支持，或任务不适合；具体原因写清。",
    STATE_CAPABILITY_TRIAL: "这只是快测里的对照短测，只报观察到的结果，不下能力结论、不进榜。",
    STATUS_INVALID_RUN: "这次实验本身坏了，作废重跑并留痕，不算插件输。",
    STATUS_PENDING: "证据不全或算法没校验，先不下结论。",
}

INSTALL_OK = "安装成功"
INSTALL_FAILED = "安装失败"
INSTALL_UNTESTED = "未测"

# 安全六类威胁（手册 §4）
SECURITY_SIX_THREATS = (
    "提示注入",
    "权限越界",
    "数据外传",
    "破坏性操作",
    "凭据保护",
    "供应链与更新行为漂移",
)
# 快测禁用词（手册 §6：快测禁用「通过」「合格」这类词，只描述检查结果）
# 分两档：
#   - 常态档：任何快测文本都不许出现「合格」，以及结论位的裸「通过」
#     （「安装通过」「快测通过」这种句末结论）。
#   - 严格档：连普通行文里的「通过」也算（用于结论位与快测记录位）。
# 「通过检查发现了问题」里的「通过」是介词，不是结论，常态档不误伤。
QUICK_CHECK_BANNED = ("合格", "快测通过", "认证通过", "通过认证", "测试通过")
QUICK_CHECK_BANNED_STRICT = QUICK_CHECK_BANNED + ("通过",)
_QUICK_CHECK_CONCLUSION_PASS = re.compile(r"通过(?=$|[，。；、！？：\s）)])")
QUICK_CHECK_REPLACE = {
    "合格": "已按预设要求核对",
    "快测通过": "快测记录已出",
    "认证通过": "已做指定检查",
    "通过认证": "已做指定检查",
    "测试通过": "检查结果已记录",
    "通过": "已检查",
}


# ---------------------------------------------------------------- 结果对象


@dataclass
class Decision:
    """一次判定结果。字段全部由输入算出，无手填空间。"""

    capability_state: str                 # 五状态之一，或 STATUS_* / 快测试跑标签
    label_kind: str                       # five_state | quick_check | refused | invalid_run
    interval: Optional[tuple] = None      # 能力差值区间 [L, U]；快测档恒为 None（不测能力 Δ）
    observed_interval: Optional[tuple] = None   # 快测档的**观察**区间（不是能力结论）
    half_width: Optional[float] = None
    precision_ok: bool = False
    capability_delta_concluded: bool = False   # 是否产出了「能力 Δ 结论」（快测恒为假）
    install_status: str = INSTALL_UNTESTED
    rank_eligible: bool = False           # 是否进数字排名
    recommendation: str = "待复核"
    worth_trying: bool = False            # 是否进「值得试」入口
    badges: list = field(default_factory=list)
    expired: bool = False
    age_days: Optional[int] = None
    evidence_verification_limited: bool = False
    # 方案 §9：三张单分开；第三张里钱与安全也分栏（绝不算一个总分）
    cards: dict = field(default_factory=dict)
    # 方案 §4.1：工具型四件事分开看
    tool_four_things: dict = field(default_factory=dict)
    # 方案 §8：深度审计 = 标准测评 + 六类安全专项；安全结论按指定范围给
    security_scope: dict = field(default_factory=dict)
    risk_level: str = RISK_UNASSESSED
    notes: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "capability_state": self.capability_state,
            "state_display": STATE_DISPLAY.get(self.capability_state, self.capability_state),
            "state_plain": STATE_PLAIN.get(self.capability_state, ""),
            "label_kind": self.label_kind,
            "capability_delta_concluded": self.capability_delta_concluded,
            "interval": list(self.interval) if self.interval else None,
            "observed_interval": list(self.observed_interval) if self.observed_interval else None,
            "half_width": self.half_width,
            "precision_ok": self.precision_ok,
            "install_status": self.install_status,
            "rank_eligible": self.rank_eligible,
            "recommendation": self.recommendation,
            "worth_trying": self.worth_trying,
            "badges": list(self.badges),
            "expired": self.expired,
            "age_days": self.age_days,
            "evidence_verification_limited": self.evidence_verification_limited,
            "cards": dict(self.cards),
            "tool_four_things": dict(self.tool_four_things),
            "security_scope": dict(self.security_scope),
            "risk_level": self.risk_level,
            "notes": list(self.notes),
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------- 基础工具


def _is_finite(x: Any) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(float(x))


def _to_date(x: Any) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return datetime.strptime(x[:10], "%Y-%m-%d").date()
    raise TypeError("无法解析日期: %r" % (x,))


def half_width(interval: Sequence[float]) -> float:
    """区间半宽 (U-L)/2。"""
    L, U = interval
    return (float(U) - float(L)) / 2.0


def interval_ok(interval: Any) -> bool:
    """区间能否用于计算：两项、有限、L<=U。NaN / L>U / 缺项一律 False。"""
    if interval is None:
        return False
    if not isinstance(interval, (tuple, list)) or len(interval) != 2:
        return False
    L, U = interval
    if not (_is_finite(L) and _is_finite(U)):
        return False
    return float(L) <= float(U)


def expires_at(tested_at: Any, validity_days: int = VALIDITY_DAYS_DEFAULT) -> date:
    d = _to_date(tested_at)
    if d is None:
        raise ValueError("tested_at 不能为空")
    return d + timedelta(days=validity_days)


def expiry_consistent(
    tested_at: Any,
    declared_expires_at: Any,
    validity_days: int = VALIDITY_DAYS_DEFAULT,
) -> bool:
    """校验页面登记的到期日是否等于「测完 + 90 天」。

    改日期不算续期：登记值与推导值不一致即为 False（手册 §13.7）。
    """
    try:
        return _to_date(declared_expires_at) == expires_at(tested_at, validity_days)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 快测记录（方案 §8 / 手册 §6）


def banned_quick_check_wording(text: str, strict: bool = False) -> list:
    """快测记录里出现的禁用词（手册 §6 禁用「通过」「合格」这类词）。

    strict=True 时连普通行文里的裸「通过」也算（用于结论位与记录位）；
    常态档只抓「合格」与结论位的「通过」（句末／后接标点）。
    """
    if not text:
        return []
    words = QUICK_CHECK_BANNED_STRICT if strict else QUICK_CHECK_BANNED
    hits = [w for w in words if w in text]
    if strict:
        if "通过" in text and "通过" not in hits:
            hits.append("通过")
    elif _QUICK_CHECK_CONCLUSION_PASS.search(text) and "通过" not in hits:
        hits.append("通过")
    return hits


def sanitize_quick_check_text(text: str) -> str:
    """把快测记录里的禁用词换成描述性表述（只描述检查结果）。"""
    out = text or ""
    for word in QUICK_CHECK_BANNED_STRICT:
        out = out.replace(word, QUICK_CHECK_REPLACE[word])
    return out


def quick_check_record(items: Optional[dict]) -> tuple:
    """把快测项整理成「快测记录：查到哪些、结果怎样」＋ 违规词清单。

    返回 (record_lines, banned_hits)；未提供任何项时按「未测」处理，不留空。
    首行带「快测记录：」前缀（手册 §6 的快测标签口径）。
    """
    if not items:
        return ["快测记录：未测（没有可展示的检查项）"], []
    lines, banned = [], []
    for name, value in items.items():
        text = str(value)
        banned += banned_quick_check_wording(text)
        lines.append("%s：%s" % (name, sanitize_quick_check_text(text)))
    if lines:
        lines[0] = "快测记录：" + lines[0]
    return lines, banned


def security_scope(depth: str, threats: Optional[dict]) -> dict:
    """安全专项结论（方案 §8：深度审计 = 标准测评 + 六类安全专项）。

    标准测评不做六威胁专项 → 标「不适用」，不留空、不冒充已查。
    """
    if depth != DEPTH_AUDIT:
        return {
            "applicable": False,
            "display": "不适用：%s 不含六类安全专项（要做专项请走深度审计）" % depth,
            "threats": {},
        }
    given = threats or {}
    detail = {}
    for name in SECURITY_SIX_THREATS:
        # 三档分开（手册 §4）：未测（没考）≠ 未发现问题（考了没抓到）≠ 已验证通过（考了有通过证据）
        detail[name] = given.get(name) or "未测"
    return {
        "applicable": True,
        "display": "深度审计安全专项（仅对指定范围成立，不能说到处安全）",
        "threats": detail,
        "untested": [k for k, v in detail.items() if v == "未测"],
    }


# ---------------------------------------------------------------- 核心判定


def decide(
    interval: Optional[Sequence[float]] = None,
    *,
    a: float = A_DEFAULT,
    b: float = B_DEFAULT,
    max_half_width: float = MAX_HALF_WIDTH_DEFAULT,
    # 运行层
    run_valid: bool = True,
    # 证据/算法层
    algorithm_validated: bool = True,
    evidence_ok: bool = True,
    # 不可测原因（手册 §2.6：必须分别记录原因）
    install_failed: bool = False,
    task_not_applicable: bool = False,
    env_unsupported: bool = False,
    install_status: str = INSTALL_UNTESTED,
    # 安全（独立通道，不覆盖能力结论）
    confirmed_severe_security_event: bool = False,
    security_investigation: bool = False,
    # 时效与证据核验
    tested_at: Any = None,
    now: Any = None,
    validity_days: int = VALIDITY_DAYS_DEFAULT,
    original_deleted: bool = False,
    redacted_evidence_sufficient: bool = True,
    # 测评深度
    depth: str = DEPTH_STANDARD,
    # 方案 §8：体验快测的检查项记录（只描述检查结果，禁用「通过」「合格」）
    quick_check: Optional[dict] = None,
    # 方案 §4.1：工具型四件事（缺项写「未测」，不留空）
    tool_results: Optional[dict] = None,
    # 方案 §8：深度审计的六类安全专项证据（键=六威胁名）
    security_six_threats: Optional[dict] = None,
    # 方案 §9：风险等级（未查清就写「未评估」，不硬给等级）
    risk_level: Optional[str] = None,
) -> Decision:
    """按手册 §2.4 计算五状态与发布资格；按方案 §8 区分三档能说什么。

    档位差异（方案 §8 / 手册 §6）：
      - 体验快测：**不测能力 Δ**，只出「快测记录／能力试跑（观察结果，不进榜）」，
        绝不产出五状态能力结论（真有用／帮倒忙／没啥差别／还说不准）。
      - 标准测评：完整对照实验 + 统计判定 → 五状态能力结论。
      - 深度审计：能力结论与标准测评相同（= 标准 + 六类安全专项），
        区别在**安全专项面**（security_scope），不在能力状态。
    """
    notes: list = []
    reasons: list = []

    # 三张单分开（方案 §9）：能力 / 好不好用 / 花费与安全；第三张里钱与安全分栏
    cards = {
        CARD_CAPABILITY: {"value": None, "display": "未测"},
        CARD_USABILITY: {"value": None, "display": "未测"},
        CARD_COST_AND_SAFETY: {
            "cost": {"value": None, "display": "未测"},
            "safety": {"value": None, "display": "未测"},
        },
    }
    # 工具型四件事（方案 §4.1）：分开看，不互相抵分
    four_things = {k: "未测" for k in FOUR_THINGS}
    if tool_results:
        for k in FOUR_THINGS:
            four_things[k] = tool_results.get(k, "未测")
    risk = risk_level or RISK_UNASSESSED
    scope = security_scope(depth, security_six_threats)

    # --- 1. 实验无效（运行层）：作废重跑，不出能力五状态 ---------------
    # 与档位无关：任何档位的实验本身坏了，都不给插件记失败。
    if not run_valid:
        return Decision(
            capability_state=STATUS_INVALID_RUN,
            label_kind=LK_INVALID_RUN,
            capability_delta_concluded=False,
            install_status=install_status,
            rank_eligible=False,
            recommendation="待复核",
            worth_trying=False,
            cards=cards,
            tool_four_things=four_things,
            security_scope=scope,
            risk_level=risk,
            notes=["实验无效是运行层状态（判分器故障/环境崩）：作废 + 留痕 + 重跑，不给插件记失败。"],
            reasons=["运行层判为实验无效，不输出能力五状态。"],
        )

    # --- 2. 没法测：安装失败 / 任务不适用 / 环境不支持 -----------------
    # 与档位无关：这不是「能力 Δ 结论」，而是「拿不到结论」，
    # 且手册 §2.6 边界 1 要求安装环节单独记失败。
    untestable_reasons = []
    if install_failed:
        untestable_reasons.append("安装失败")
        install_status = INSTALL_FAILED
    if task_not_applicable:
        untestable_reasons.append("任务不适用")
    if env_unsupported:
        untestable_reasons.append("环境不支持")
    if untestable_reasons:
        notes.append("能力结论：🚧 没法测（原因：" + "；".join(untestable_reasons) + "）")
        if install_failed:
            # 手册 §2.6 边界 1：安装环节单独记「失败」，不被中性标签掩盖
            notes.append("安装环节：❌ 失败（安装初始化本身是交付能力的一部分）")
        return Decision(
            capability_state=STATE_UNTESTABLE,
            label_kind=LK_FIVE_STATE,
            capability_delta_concluded=False,
            install_status=install_status,
            rank_eligible=False,
            recommendation="待复核",
            worth_trying=False,
            cards=cards,
            tool_four_things=four_things,
            security_scope=scope,
            risk_level=risk,
            notes=notes,
            reasons=["原因：" + "；".join(untestable_reasons) + "，无法取得可判定的能力区间。"],
        )

    # --- 2.5 体验快测：不测能力 Δ，只出快测记录／能力试跑 ----------------
    # 方案 §8：「体验快测不测能力 Δ，不用「认证通过」「合格」。
    #           需要对照的短测只能叫「能力试跑」，标「观察结果，不进榜」。」
    if depth == DEPTH_FAST:
        record_lines, banned = quick_check_record(quick_check)
        notes.append("档位「%s」不测能力 Δ：只报观察结果，不出五状态能力结论。" % DEPTH_FAST)
        notes.append("；".join(record_lines))
        if banned:
            notes.append(
                "快测记录含禁用词 %s（手册 §6）：已改成描述性表述，快测不用「通过」「合格」。"
                % "、".join(banned)
            )
        observed = None
        if interval is not None and interval_ok(interval):
            observed = (float(interval[0]), float(interval[1]))
            notes.append(
                "附：这次对照短测观察到的区间是 [%.3f, %.3f]（观察结果，不是能力结论，不进榜）。"
                % observed
            )
        elif interval is not None:
            notes.append("附：对照短测的区间无法计算（%s），不影响快测记录照常发布。" % interval)
        cards[CARD_CAPABILITY] = {"value": None, "display": "未测（快测不测能力 Δ）"}
        cards[CARD_USABILITY] = {
            "value": None,
            "display": "；".join(record_lines),
        }
        return Decision(
            capability_state=STATE_CAPABILITY_TRIAL,
            label_kind=LK_QUICK_CHECK,
            capability_delta_concluded=False,
            # 快测不产出能力区间：interval 恒为 None；观察值另放 observed_interval，
            # 免得下游把它当成「能力结论」用。
            interval=None,
            observed_interval=observed,
            install_status=install_status,
            rank_eligible=False,
            recommendation="待复核",
            worth_trying=False,
            badges=[],
            cards=cards,
            tool_four_things=four_things,
            security_scope=scope,
            risk_level=risk,
            notes=notes,
            reasons=["档位「%s」按方案 §8 只出「能力试跑（观察结果，不进榜）」，不产出五状态能力结论。" % DEPTH_FAST],
        )

    # --- 3. 拒绝分析：算法未校验 / 证据缺失 / 区间无法计算 -------------
    if not algorithm_validated or not evidence_ok or not interval_ok(interval):
        refusal = []
        if not algorithm_validated:
            refusal.append("算法尚未完成校验")
        if not evidence_ok:
            refusal.append("必要证据缺失")
        if not interval_ok(interval):
            if interval is None or not isinstance(interval, (tuple, list)) or len(interval) != 2:
                refusal.append("区间缺失")
            else:
                L, U = interval
                if not (_is_finite(L) and _is_finite(U)):
                    refusal.append("区间含 NaN/非数值")
                elif float(L) > float(U):
                    refusal.append("区间 L>U")
        return Decision(
            capability_state=STATUS_PENDING,
            label_kind=LK_REFUSED,
            capability_delta_concluded=False,
            install_status=install_status,
            rank_eligible=False,
            recommendation="待复核",
            worth_trying=False,
            cards=cards,
            tool_four_things=four_things,
            security_scope=scope,
            risk_level=risk,
            notes=["拒绝分析：不生成数值区间和正式能力标签（手册 §2.4 第 3 条）。"],
            reasons=refusal,
        )

    # --- 4. 精度门槛 + 区间映射 ---------------------------------------
    L = float(interval[0])
    U = float(interval[1])
    half = (U - L) / 2.0
    precision_ok = half <= max_half_width

    if half > max_half_width:
        state = STATE_UNCERTAIN
        reasons.append(
            "精度不足：半宽 %.3f > 上限 %.3f，即使方向明确也判「还说不准」" % (half, max_half_width)
        )
    elif L > a:
        state = STATE_USEFUL
        reasons.append("区间下界 %.3f > 正向门槛 %.3f，且精度达标" % (L, a))
    elif U < -b:
        state = STATE_HARMFUL
        reasons.append("区间上界 %.3f < −可接受退化 %.3f，且精度达标" % (U, b))
    elif L > -b and U < a:
        state = STATE_EQUIVALENT
        reasons.append(
            "区间完全落入等效范围 (%.3f, %.3f)，且精度达标（本次范围内实际等效，不是绝对一样）"
            % (-b, a)
        )
    else:
        state = STATE_UNCERTAIN
        reasons.append(
            "区间跨门槛或刚好等于门槛（L=%.3f, U=%.3f），判「还说不准」" % (L, U)
        )

    # --- 5. 时效：默认 90 天，到期退出推荐与排名 -----------------------
    age_days = None
    if tested_at is not None and now is not None:
        age_days = (_to_date(now) - _to_date(tested_at)).days
    expired = age_days is not None and age_days > validity_days
    if expired:
        notes.append(
            "已超过 %d 天有效期（%d 天）：退出当前有效排名和推荐徽章，历史结论保留；仅改页面日期不算续期。"
            % (validity_days, age_days)
        )

    # --- 6. 证据核验受限：原件删除且脱敏替代证据不足 -------------------
    evidence_verification_limited = bool(original_deleted and not redacted_evidence_sufficient)
    if evidence_verification_limited:
        notes.append(
            "原件已删除且脱敏替代证据不足：标「证据核验受限/待复核」，退出有效数字排名和推荐徽章；"
            "不以原件哈希冒充可验证证据。"
        )
    elif original_deleted:
        notes.append("原件已删除，但脱敏替代证据仍够重算：成绩继续有效。")

    # --- 7. 排名资格（手册 §13.7 / §2.5）------------------------------
    rank_eligible = (
        state in NUMERIC_RANK_STATES
        and precision_ok
        and not expired
        and not evidence_verification_limited
        and depth in RANKING_DEPTHS
    )
    if state in (STATE_UNCERTAIN, STATE_UNTESTABLE):
        notes.append("「%s」正常展示，但不参加数字排名。" % state)
    if depth not in RANKING_DEPTHS:
        notes.append("档位「%s」不进能力榜。" % depth)

    # --- 8. 推荐资格（安全独立通道，不覆盖能力结论）-------------------
    if confirmed_severe_security_event:
        recommendation = "暂停推荐"
        notes.append("已确认严重安全事件：暂停当前版本推荐；能力结果保留，不改零分、不抹掉。")
        notes.append("不进入「值得试」入口。")
    elif security_investigation:
        recommendation = "调查中"
        notes.append("可疑但证据不足：标「调查中」，先提醒风险，不当成已证实事实。")
    elif evidence_verification_limited:
        recommendation = "待复核"
    elif expired:
        recommendation = "待复核"
    elif state in (STATE_UNTESTABLE, STATUS_PENDING, STATUS_INVALID_RUN):
        recommendation = "待复核"
    else:
        recommendation = "可推荐"

    worth_trying = bool(
        rank_eligible and not confirmed_severe_security_event and not security_investigation
    )

    badges = []
    if rank_eligible and recommendation == "可推荐":
        badges = ["%s·%s" % (depth, state)]
    elif state in FIVE_STATES:
        notes.append("徽章已失效或不签发（未满足排名/推荐条件）。")

    # --- 9. 三张单与安全面（方案 §9 / §8）-----------------------------
    cards[CARD_CAPABILITY] = {
        "value": state,
        "display": STATE_DISPLAY.get(state, state),
        "precision_ok": precision_ok,
        "rank_eligible": rank_eligible,
    }
    cards[CARD_COST_AND_SAFETY]["safety"] = {
        "value": "已确认事件" if confirmed_severe_security_event else ("调查中" if security_investigation else "未测"),
        "display": "能力与安全分栏，不算总分",
    }
    if depth == DEPTH_AUDIT:
        notes.append("档位「%s」：能力结论与标准测评同规，另出六类安全专项结论（仅对指定范围成立）。" % DEPTH_AUDIT)
        if scope.get("untested"):
            notes.append("安全专项未测项：%s（未测就写未测，不留空）。" % "、".join(scope["untested"]))
    if risk == RISK_UNASSESSED:
        notes.append("风险等级：未评估（无可执行映射或关键权限未查明，不硬给等级）。")

    return Decision(
        capability_state=state,
        label_kind=LK_FIVE_STATE,
        capability_delta_concluded=True,
        interval=(L, U),
        half_width=half,
        precision_ok=precision_ok,
        install_status=install_status,
        rank_eligible=rank_eligible,
        recommendation=recommendation,
        worth_trying=worth_trying,
        badges=badges,
        expired=expired,
        age_days=age_days,
        evidence_verification_limited=evidence_verification_limited,
        cards=cards,
        tool_four_things=four_things,
        security_scope=scope,
        risk_level=risk,
        notes=notes,
        reasons=reasons,
    )


# ---------------------------------------------------------------- 成本口径（手册 §5）


def unit_success_cost(total_cost: Optional[float], successes: Optional[int]) -> dict:
    """总单位成功成本 = 全部成本 ÷ 成功任务数。禁止除零、禁止填 0。"""
    if total_cost is None or successes is None:
        return {
            "value": None,
            "display": "没测到",
            "reason": "计量缺失；观测不到不能填 0，须写估算值和误差来源。",
        }
    if successes == 0:
        return {
            "value": None,
            "display": "不适用：没有成功任务",
            "total_cost": total_cost,
            "reason": "成功数为 0，禁止除零；仍展示实际总花费。",
        }
    if successes < 0:
        return {"value": None, "display": "不适用：成功数为负，数据无效", "reason": "输入无效。"}
    return {"value": total_cost / successes, "display": "%.4f" % (total_cost / successes)}


def incremental_unit_success_cost(
    plugin_cost: Optional[float],
    base_cost: Optional[float],
    plugin_successes: Optional[int],
    base_successes: Optional[int],
) -> dict:
    """增量单位成功成本 = (插件成本 − 基线成本) ÷ 插件多带来的成功数。"""
    if None in (plugin_cost, base_cost, plugin_successes, base_successes):
        return {"value": None, "display": "没测到", "reason": "计量缺失，不得填 0。"}
    delta_cost = plugin_cost - base_cost
    delta_successes = plugin_successes - base_successes
    if delta_successes <= 0:
        return {
            "value": None,
            "display": "不适用：没有多干成任务",
            "delta_cost": delta_cost,
            "delta_successes": delta_successes,
            "reason": "单列花费差与结果差，不能用负比值暗示划算。",
        }
    return {
        "value": delta_cost / delta_successes,
        "display": "%.4f" % (delta_cost / delta_successes),
        "delta_cost": delta_cost,
        "delta_successes": delta_successes,
    }


# ---------------------------------------------------------------- 样本层校验


def select_valid_runs(runs: Iterable[dict]) -> tuple:
    """剔除无效运行（判分器故障/环境崩），返回 (有效运行, 被剔除的运行)。"""
    valid, dropped = [], []
    for r in runs:
        (valid if r.get("valid", True) else dropped).append(r)
    return valid, dropped


def incomplete_pairs(pairs: Iterable[dict]) -> list:
    """找出缺 B0 或 P 的配对：缺失配对不得计入样本，须留痕。"""
    missing = []
    for p in pairs:
        if not p.get("b0") or not p.get("p"):
            missing.append(p.get("pair_id"))
    return missing


def weights_ok(weights: dict, expected_total: float = 1.0, tol: float = 1e-6) -> bool:
    """权重和须等于预设总量（默认 1.0）。"""
    if not weights:
        return False
    try:
        total = sum(float(v) for v in weights.values())
    except (TypeError, ValueError):
        return False
    return abs(total - expected_total) <= tol


def cross_scenario_claim_allowed(scenario_count: int) -> bool:
    """场景不能合理分组（只有一个场景）时，不得生成跨场景正式结论。"""
    return scenario_count >= 2


def check_analysis_manifest(manifest: Optional[dict]) -> list:
    """analysis manifest 必填项（手册 §2.4：RNG/库版本/百分位等须登记）。"""
    required = [
        "analysis_id",
        "method",
        "seed",
        "rng",
        "library_versions",
        "bootstrap_resamples",
        "percentile_method",
        "scenario_aggregation",
    ]
    if not manifest:
        return list(required)
    return [k for k in required if manifest.get(k) in (None, "", [])]


# ---------------------------------------------------------------- CLI


_BOUNDARY_TABLE = [
    ((6, 10), STATE_USEFUL, "真有用"),
    ((-10, -6), STATE_HARMFUL, "帮倒忙"),
    ((-2, 2), STATE_EQUIVALENT, "没啥差别"),
    ((4, 8), STATE_UNCERTAIN, "还说不准：跨过正向门槛"),
    ((-8, -4), STATE_UNCERTAIN, "还说不准：跨过负向门槛"),
    ((5, 8), STATE_UNCERTAIN, "还说不准：正好卡门槛"),
    ((-8, -5), STATE_UNCERTAIN, "还说不准：正好卡门槛"),
    ((-9, 9), STATE_UNCERTAIN, "还说不准：精度不足"),
    ((6, 30), STATE_UNCERTAIN, "还说不准：方向明确但未满足本协议精度门槛"),
]


def _demo() -> int:
    print("判定算法边界回归（真源：端到端验证计划-v0 §7；门槛 a=b=5、半宽 ≤5）")
    print("-" * 72)
    bad = 0
    for interval, expected, note in _BOUNDARY_TABLE:
        d = decide(interval)
        ok = d.capability_state == expected
        bad += 0 if ok else 1
        print(
            "%-10s → %-6s  期望 %-6s  %s  %s"
            % (str(list(interval)), d.capability_state, expected, "OK" if ok else "MISMATCH", note)
        )
    print("-" * 72)
    combos = [
        ("安装失败", decide(install_failed=True)),
        ("缺必要证据", decide(interval=(6, 10), evidence_ok=False)),
        ("L>U", decide(interval=(8, 4))),
        ("NaN", decide(interval=(float("nan"), 10))),
        ("正增益 + 已确认严重安全事件", decide((6, 10), confirmed_severe_security_event=True)),
        ("超 90 天", decide((6, 10), tested_at="2026-01-01", now="2026-06-01")),
        ("原件删除且脱敏证据不足", decide((6, 10), original_deleted=True, redacted_evidence_sufficient=False)),
        ("实验无效", decide(run_valid=False)),
    ]
    for name, d in combos:
        print("%-28s → %-14s 排名=%s 推荐=%s" % (name, d.capability_state, d.rank_eligible, d.recommendation))
    print("-" * 72)
    print("档位能说什么（方案 §8 / 手册 §6）")
    print("-" * 72)
    fast = decide((6, 10), depth=DEPTH_FAST, quick_check={"安装": "成功", "功能点": "5 个功能点 4 个可用"})
    std = decide((6, 10))
    aud = decide((6, 10), depth=DEPTH_AUDIT, security_six_threats={"提示注入": "未发现问题"})
    for name, d in (("体验快测", fast), ("标准测评", std), ("深度审计", aud)):
        print(
            "%-6s → 结论 %-22s label_kind=%-11s 能力Δ=%s 排名=%s 徽章=%d"
            % (name, d.capability_state, d.label_kind, d.capability_delta_concluded,
               d.rank_eligible, len(d.badges))
        )
    print("快测记录：%s" % "；".join(
        n.replace("快测记录：", "") for n in fast.notes if n.startswith("快测记录：") or "功能点：" in n))
    print("深度审计安全专项：适用=%s，未测项=%s" % (
        aud.security_scope.get("applicable"), aud.security_scope.get("untested")))
    print("标准测评安全专项：%s" % std.security_scope.get("display"))
    print("-" * 72)
    print("不一致行数：%d" % bad)
    return 1 if bad else 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="dsh-bench 插件测评站 · 五状态判定算法")
    p.add_argument("--demo", action="store_true", help="打印 §7 边界回归表读数")
    p.add_argument("--interval", nargs=2, type=float, metavar=("L", "U"), help="直接判定一个区间")
    p.add_argument("--json", action="store_true", help="以 JSON 输出判定结果")
    args = p.parse_args(argv)

    if args.interval:
        d = decide(tuple(args.interval))
        if args.json:
            print(json.dumps(d.as_dict(), ensure_ascii=False, indent=2))
        else:
            print("%s：%s" % (d.capability_state, "；".join(d.reasons)))
        return 0
    if args.demo:
        return _demo()
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
