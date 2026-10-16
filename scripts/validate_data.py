#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-bench 插件测评站 · 发布前检查器（fail-closed）

真源：
  - docs/spec/插件测评站-方案v3.0-完整修订版.md §17「发布前检查必要字段、公式、
    证据链接、隐私和复核」
  - docs/spec/工程协议手册-v1.1.md §13.3（最小证据和数据模型）、§13.4（失效传播）、
    §13.5（隐私治理）、§13.7（时效与排名）

规则：**不通过即拒绝发布**。逐条报缺哪项，退出码非 0。

除 JSON Schema 必填/类型检查外，另做「不能手填」的语义复核：
  - 区间半宽必须等于 (U-L)/2
  - 声明的能力状态必须等于由区间重算出的状态（拿 verdict.py 复算）
  - 到期日必须等于「测完 + 90 天」（改日期不算续期）
  - 成本分母为 0 时必须显示「不适用」，不得出现数字
  - 禁止出现总分/星级一类手填字段
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "data" / "schema"

FORBIDDEN_FIELDS = {
    "total_score",
    "overall_score",
    "overall",
    "stars",
    "star_rating",
    "rating",
    "总分",
    "星级",
    "综合分",
    "rank_score",
}

KIND_SCHEMA = {
    "report": "report.schema.json",
    "plugin": "plugin.schema.json",
    "evidence": "evidence.schema.json",
    "ledger": "ledger.schema.json",
}


def load_verdict():
    path = ROOT / "scripts" / "verdict.py"
    spec = importlib.util.spec_from_file_location("verdict_for_validate", path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass 需要模块已在 sys.modules 中才能解析类型注解
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------- schema 子集校验


def _type_ok(value, t) -> bool:
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    if t == "null":
        return value is None
    return True


def validate_schema(instance, schema, path="$") -> list:
    errors = []
    if not isinstance(schema, dict):
        return errors
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(instance, x) for x in types):
            errors.append("%s: 类型应为 %s，实为 %s" % (path, types, type(instance).__name__))
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: 取值 %r 不在枚举 %s 内" % (path, instance, schema["enum"]))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: 长度不足 %d" % (path, schema["minLength"]))
        if "pattern" in schema:
            import re

            if not re.search(schema["pattern"], instance):
                errors.append("%s: 不匹配 pattern %s" % (path, schema["pattern"]))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: %r 小于 minimum %r" % (path, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: %r 大于 maximum %r" % (path, instance, schema["maximum"]))
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append("%s: %r 未大于 %r" % (path, instance, schema["exclusiveMinimum"]))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: 缺必填字段 %s" % (path, key))
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                errors += validate_schema(instance[key], sub, "%s.%s" % (path, key))
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in schema.get("properties", {}):
                    errors.append("%s: 多余字段 %s" % (path, key))
    if isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(instance):
            errors += validate_schema(item, schema["items"], "%s[%d]" % (path, i))
    return errors


def find_forbidden(obj, path="$") -> list:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_FIELDS:
                hits.append("%s.%s（禁止总分/星级一类手填字段）" % (path, k))
            hits += find_forbidden(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += find_forbidden(v, "%s[%d]" % (path, i))
    return hits


# ------------------------------------------------------------- 语义复核


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)) else None


def _reject_constant(x):
    """dsh-bench#17 H1：JSON 的 NaN / Infinity 非标准字面量，一律拒绝。"""
    raise ValueError("非标准数值字面量 %r（NaN/Infinity 不得进入数据）" % x)


def _load_json_strict(text: str):
    return json.loads(text, parse_constant=_reject_constant)


def semantic_report(report: dict, verdict, evidence_ok: bool = True, now: str = None) -> list:
    """复算：状态/半宽/到期日/成本分母，全部由数据算出，拒绝手填。"""
    errors = []
    iv = report.get("interval") or {}
    L, U = _num(iv.get("L")), _num(iv.get("U"))

    # 1) 半宽公式
    if L is not None and U is not None:
        want = (U - L) / 2.0
        got = _num(report.get("half_width"))
        if got is None or abs(got - want) > 1e-9:
            errors.append("公式不符：half_width 应为 (U-L)/2=%.6f，实为 %r" % (want, report.get("half_width")))

    # 1a) 门槛由平台冻结（dsh-bench#2：报告自带门槛即放宽判定）
    for key, frozen in (("a", verdict.A_DEFAULT), ("b", verdict.B_DEFAULT), ("max_half_width", verdict.MAX_HALF_WIDTH_DEFAULT)):
        got = _num(report.get(key))
        if got is None:
            errors.append("门槛 %s 缺失（平台冻结 a=%s / b=%s / max_half_width=%s）" % (key, verdict.A_DEFAULT, verdict.B_DEFAULT, verdict.MAX_HALF_WIDTH_DEFAULT))
        elif abs(got - frozen) > 1e-9:
            errors.append("门槛 %s=%r 由平台冻结为 %s（报告自带门槛即放宽判定）" % (key, got, frozen))

    # 1b) 分数口径（手册 §13.1：Δ = P − B0；区间与差值同有同无）（dsh-bench#9）
    b0, p, dl = _num(report.get("b0_score")), _num(report.get("p_score")), _num(report.get("delta"))
    if None not in (b0, p, dl) and abs((p - b0) - dl) > 1e-6:
        errors.append("分数不符：delta 应为 p_score - b0_score = %.6f，实为 %r" % ((p - b0), report.get("delta")))
    if dl is not None and L is not None and U is not None and not (L - 1e-9 <= dl <= U + 1e-9):
        errors.append("分数与区间不符：delta=%r 不在区间 [%s, %s] 内" % (report.get("delta"), L, U))
    if (L is not None) != (dl is not None):
        errors.append("分数与区间须同有同无：interval 与 delta 一个为空、一个有值")

    # 1c) 测完日期不得在未来（dsh-bench#15：tested_at 写未来即永不过期）
    ta = report.get("tested_at")
    if isinstance(ta, str):
        try:
            if datetime.strptime(ta, "%Y-%m-%d").date() > date.today():
                errors.append("测完日期在未来：%s（今天 %s）——未来日期不可作为成绩起点" % (ta, date.today().isoformat()))
        except ValueError:
            pass  # 格式非法由 schema pattern 兜底

    # 2) 状态复算（门槛用平台冻结值；时钟用真实今天——与生成器口径一致）（dsh-bench#2/#8①/#15）
    install_failed = report.get("install_status") == "安装失败"
    sec = report.get("security") or {}
    ev = report.get("evidence") or {}
    # dsh-bench#8②：run_status / untestable_reason 如实传给 decide（没法测须分别记录原因）
    run_valid = report.get("run_status", "有效") != "实验无效"
    untestable_reason = report.get("untestable_reason") or ""
    task_not_applicable = untestable_reason == "任务不适用"
    env_unsupported = untestable_reason == "环境不支持"
    d = verdict.decide(
        (L, U) if (L is not None and U is not None) else None,
        a=verdict.A_DEFAULT,
        b=verdict.B_DEFAULT,
        max_half_width=verdict.MAX_HALF_WIDTH_DEFAULT,
        install_failed=install_failed,
        confirmed_severe_security_event=sec.get("status") == "已确认事件",
        security_investigation=sec.get("status") == "调查中",
        tested_at=report.get("tested_at"),
        now=now or date.today().isoformat(),
        original_deleted=bool(ev.get("original_deleted")),
        redacted_evidence_sufficient=bool(ev.get("redacted_evidence_sufficient", True)),
        depth=report.get("depth", verdict.DEPTH_STANDARD),
        report_status=report.get("report_status", "正式"),
        invalidation=report.get("invalidation"),
        evidence_ok=evidence_ok,
        run_valid=run_valid,
        task_not_applicable=task_not_applicable,
        env_unsupported=env_unsupported,
        scenario_count=report.get("scenario_count"),
        security_six_threats=(report.get("security_scope") or {}).get("threats") or None,
        cost=report.get("cost"),
        # dsh-bench#27 S2：证据层输入两条管线一致传入
        tool_results=report.get("tool_results"),
        quick_check=report.get("quick_check"),
        risk_level=report.get("risk_level"),
    )
    declared = report.get("capability_state")
    if declared != d.capability_state:
        errors.append(
            "状态与证据不符：声明 %r，按区间复算为 %r（成绩、区间、标签禁止手填）"
            % (declared, d.capability_state)
        )
    if report.get("precision_ok") is not None and d.precision_ok != report.get("precision_ok"):
        errors.append("precision_ok 与复算不符：声明 %r，实为 %r" % (report.get("precision_ok"), d.precision_ok))

    # 2b2) 失效须有原因（dsh-bench#28 T4）：非「有效」状态时 reason 必填
    inv4 = report.get("invalidation") or {}
    if verdict.is_invalidated(inv4) and not (inv4.get("reason") or "").strip():
        errors.append("invalidation.status=%r 须填写 reason（失效必须可追溯）" % inv4.get("status"))

    # 2c) 安全通道对账（dsh-bench#16 G2/G3/G4）--------------------------
    events = sec.get("events") or []
    if events and sec.get("status") == "未发现问题":
        errors.append("安全对账不符：events 记录了 %d 条事件，status 却为「未发现问题」" % len(events))
    if sec.get("status") == "调查中":
        started = sec.get("investigation_started_at")
        if not started:
            errors.append("调查中须填 investigation_started_at（起始日期），否则无法判断 14 天期限")
        else:
            try:
                sd = datetime.strptime(str(started), "%Y-%m-%d").date()
                age = (date.today() - sd).days
                if age > 14:
                    errors.append("调查已 %d 天（起于 %s）：超过 14 天期限，须升级处置或关闭调查" % (age, started))
            except ValueError:
                errors.append("investigation_started_at 格式非法：%r（须 YYYY-MM-DD）" % started)
    rv = (report.get("review") or {}).get("reviewed_at")
    if rv:
        try:
            rd_ = datetime.strptime(str(rv), "%Y-%m-%d").date()
            if ta and isinstance(ta, str):
                td = datetime.strptime(ta, "%Y-%m-%d").date()
                if rd_ < td:
                    errors.append("reviewed_at %s 早于 tested_at %s（复核不得早于测完）" % (rv, ta))
        except ValueError:
            errors.append("reviewed_at 格式非法：%r（须 YYYY-MM-DD）" % rv)

    # 2d) 更正记录（dsh-bench#22 N2）-----------------------------------
    corr = report.get("corrections") or []
    ver = report.get("report_version")
    for c_ in corr:
        if not isinstance(c_, dict) or not c_.get("reason") or c_.get("to_version") is None:
            errors.append("更正记录须为对象且含 to_version 与 reason：%r" % (c_,))
            break
    if isinstance(ver, int) and ver > 1:
        if not any(isinstance(c_, dict) and c_.get("to_version") == ver for c_ in corr):
            errors.append("report_version=%s 但没有 to_version=%s 的更正记录（版本递增须留痕）" % (ver, ver))

    # 2e) 利益关系（dsh-bench#22 N3）-----------------------------------
    if report.get("report_status", "正式") == "正式" and not (report.get("disclosure") or "").strip():
        errors.append("正式报告 disclosure 为空：利益关系必须披露（没有就写「无」）")

    # 2f) 人话结论与复算一致（dsh-bench#23 Q2）--------------------------
    concl = (report.get("human_text") or {}).get("结论") or ""
    if report.get("depth", verdict.DEPTH_STANDARD) in verdict.RANKING_DEPTHS and concl:
        words = ("真有用", "帮倒忙", "没啥差别", "还说不准", "没法测")
        hits = [w for w in words if w in concl]
        if hits and hits[0] != d.capability_state and d.capability_state in words:
            errors.append("human_text.结论 写「%s」，与复算状态「%s」不符（人话可以补充，不能反着说）" % (hits[0], d.capability_state))

    # 3) 到期日 = 测完 + 90 天
    if not verdict.expiry_consistent(report.get("tested_at"), report.get("expires_at")):
        errors.append(
            "到期日不符：应为测完 + 90 天 = %s（改日期不算续期）"
            % verdict.expires_at(report.get("tested_at")).isoformat()
        )

    # 4) 成本分母
    cost = report.get("cost") or {}
    tc, sc = _num(cost.get("total_cost")), cost.get("success_count")
    base_c, base_s = _num(cost.get("baseline_cost")), cost.get("baseline_success_count")
    if tc is not None and isinstance(sc, int):
        want = verdict.unit_success_cost(tc, sc)
        got = cost.get("unit_success_cost")
        if want["value"] is None:
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                errors.append("成本分母为 0/无效时不得出现数字：unit_success_cost=%r" % (got,))
            elif not isinstance(got, str) or "不适用" not in got:
                errors.append("成本分母为 0/无效时应显示「不适用：没有成功任务」，实为 %r" % (got,))
        else:
            if _num(got) is None or abs(_num(got) - want["value"]) > 1e-6:
                errors.append("unit_success_cost 复算不符：应为 %.6f，实为 %r" % (want["value"], got))
    if None not in (tc, base_c, sc, base_s) and isinstance(sc, int) and isinstance(base_s, int):
        want = verdict.incremental_unit_success_cost(tc, base_c, sc, base_s)
        got = cost.get("incremental_unit_success_cost")
        if want["value"] is None:
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                errors.append("增量成功数 ≤0 时不得用负比值：incremental_unit_success_cost=%r" % (got,))
            elif not isinstance(got, str) or "不适用" not in got:
                errors.append("增量成功数 ≤0 时应显示「不适用：没有多干成任务」，实为 %r" % (got,))
        else:
            if _num(got) is None or abs(_num(got) - want["value"]) > 1e-6:
                errors.append("incremental_unit_success_cost 复算不符：应为 %.6f，实为 %r" % (want["value"], got))

    # 5) 复核独立
    review = report.get("review") or {}
    if not review.get("independent"):
        errors.append("正式报告须有独立复核：review.independent 为假")
    if review.get("independent_reviewer") and review.get("executor") == review.get("independent_reviewer"):
        errors.append("执行与复核分离：executor 与 independent_reviewer 不得同一人")

    # 6) 证据链接
    if not (report.get("evidence") or {}).get("evidence_id"):
        errors.append("证据链接缺失：evidence.evidence_id 为空")

    # 7) 隐私：原件未删除时必须登记脱敏状态
    if not ev.get("public_redacted") and not ev.get("original_deleted"):
        errors.append("隐私：公开报告须脱敏（public_redacted 为假）")

    # 8) 档位能说什么（方案 §8 / 手册 §6）：快测不得产出五状态能力结论
    depth = report.get("depth")
    declared_state = report.get("capability_state")
    declared_kind = report.get("label_kind")
    cards = report.get("cards") or {}
    if depth == verdict.DEPTH_FAST:
        if declared_state in verdict.CAPABILITY_DELTA_STATES:
            errors.append(
                "档位越界：体验快测不得产出能力 Δ 结论（方案 §8「体验快测不测能力 Δ」），"
                "实为 %r；应为 %r" % (declared_state, verdict.STATE_CAPABILITY_TRIAL)
            )
        if declared_kind == verdict.LK_FIVE_STATE:
            errors.append(
                "档位越界：体验快测的 label_kind 不得为 five_state（应为 quick_check 或过程态）"
            )
        if report.get("capability_delta_concluded"):
            errors.append("档位越界：体验快测 capability_delta_concluded 必须为假")
        if report.get("rank_eligible"):
            errors.append("档位越界：体验快测不进能力榜（方案 §8）")
        for field, line in (report.get("human_text") or {}).items():
            banned = verdict.banned_quick_check_wording(str(line), strict=(field == "结论"))
            if banned:
                errors.append(
                    "快测禁用词：%s 含 %s（手册 §6 禁用「通过」「合格」这类词）"
                    % (field, "、".join(banned))
                )
        for field, line in (cards.get(verdict.CARD_USABILITY) or {}).items():
            banned = verdict.banned_quick_check_wording(str(line), strict=True)
            if banned:
                errors.append("快测禁用词：好不好用.%s 含 %s（手册 §6）" % (field, "、".join(banned)))
    elif depth in verdict.RANKING_DEPTHS:
        if declared_state in verdict.CAPABILITY_DELTA_STATES and declared_kind != verdict.LK_FIVE_STATE:
            errors.append("label_kind 与结论不符：%r 应为 five_state" % declared_state)
        if declared_state in verdict.CAPABILITY_DELTA_STATES and not report.get("capability_delta_concluded"):
            errors.append("label_kind 与结论不符：标准／深度档产出五状态时 capability_delta_concluded 应为真")

    # 9) 三张单必须分开且第三张内钱与安全分栏（方案 §9）
    cards = report.get("cards") or {}
    for name in (verdict.CARD_CAPABILITY, verdict.CARD_USABILITY, verdict.CARD_COST_AND_SAFETY):
        if name not in cards:
            errors.append("三张单缺失：cards 缺 %r" % name)
    third = cards.get(verdict.CARD_COST_AND_SAFETY) or {}
    for sub in ("cost", "safety"):
        if sub not in third:
            errors.append("第三张单未分栏：花费与安全里缺 %r" % sub)

    # 10) 风险等级：未查清不得硬给等级（方案 §9/§16）
    if report.get("risk_level") not in ("低", "中", "高", "关键", "未评估"):
        errors.append("风险等级取值非法：%r" % report.get("risk_level"))

    # 11) 安全专项：深度审计须有六威胁面；标准测评须标不适用（方案 §8）
    scope = report.get("security_scope") or {}
    if depth == verdict.DEPTH_AUDIT and not scope.get("applicable"):
        errors.append("安全专项缺失：深度审计须出六类安全专项（security_scope.applicable 为假）")
    if depth == verdict.DEPTH_STANDARD and scope.get("applicable"):
        errors.append("安全专项越界：标准测评不含六类安全专项，须标不适用")
    return errors


def semantic_evidence(ev: dict, verdict) -> list:
    errors = []
    manifest = ev.get("analysis") or {}
    missing = verdict.check_analysis_manifest(manifest)
    if missing:
        errors.append("analysis manifest 缺项（区间无法独立重算）：%s" % ", ".join(missing))
    pub = ev.get("public_redacted") or {}
    if pub.get("original_deleted") and not pub.get("redacted_evidence_sufficient"):
        # 允许，但必须显式登记受限
        if not pub.get("note"):
            errors.append("原件删除且替代证据不足时须登记 note（证据核验受限）")
    for key in ("correctness", "cost", "security", "state_restore"):
        if not (ev.get("minimal_proof") or {}).get(key):
            errors.append("最小证明集缺 %s（缺任一必要证据不能重算相应结论）" % key)
    return errors


def semantic_plugin(pl: dict, verdict) -> list:
    errors = []
    ident = pl.get("identity") or {}
    if ident.get("observable") is False and not ident.get("note"):
        errors.append("无 commit 可观测的插件须注明它限制了什么结论（identity.note 缺失）")
    m = pl.get("maintainer") or {}
    age = m.get("known_issue_age_days")
    if isinstance(age, int) and age > 90 and m.get("response_days") is None:
        errors.append("已通知问题超过 90 天须写明依据（response_days）")
    return errors


def semantic_ledger(led: dict, verdict, actual_published: int = None) -> list:
    errors = []
    # dsh-bench#22 N5：月份合法、内部大小关系
    month = str(led.get("month") or "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        errors.append("账本月份非法：%r（须 YYYY-MM，01–12 月）" % led.get("month"))
    if led.get("failed_count", 0) > led.get("tested_count", 0):
        errors.append("账本自相矛盾：失败数 %s > 已测数 %s" % (led.get("failed_count"), led.get("tested_count")))
    # dsh-bench#28 T1：恢复「已测 ≥ 发布」（#22 N5 重写时丢失的回归）
    if led.get("tested_count", 0) < led.get("published_reports", 0):
        errors.append("账本自相矛盾：已测数 %s < 发布报告数 %s" % (led.get("tested_count"), led.get("published_reports")))
    # dsh-bench#12④：published_reports 与实际已发布报告对账
    if actual_published is not None and led.get("published_reports", 0) != actual_published:
        errors.append(
            "账本不对账：published_reports=%s，当月实际正式报告 %d 份（month=%s）"
            % (led.get("published_reports"), actual_published, month)
        )
    return errors


# ------------------------------------------------------------- 证据链（dsh-bench#7）


def _resolve_evidence(report_path: Path, report: dict, verdict) -> tuple:
    """evidence_id 必须解析到 data/evidence/ 下的真实文件；证据自身校验不通过 → 引用它的报告不得发布。"""
    eid = (report.get("evidence") or {}).get("evidence_id")
    if not eid:
        return False, ["evidence.evidence_id 缺失（缺必要证据 → 待验证/待修复，不进排名）"]
    ev_dir = report_path.parent.parent / "evidence"
    if not ev_dir.is_dir():
        return False, ["证据目录不存在：%s（evidence_id=%r 悬空）" % (ev_dir, eid)]
    for p in sorted(ev_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("evidence_id") != eid:
            continue
        ok, ev_errors = validate_file(p, verdict, release=False)
        if not ok:
            return False, ["证据文件 %s 自身校验未通过，引用它的报告不得发布：%s" % (p.name, "；".join(ev_errors[:3]))]
        return True, []
    return False, ["证据 %r 悬空：data/evidence/ 下无 evidence_id 匹配的文件" % eid]


# ------------------------------------------------------------- 主流程


DIR_KIND = {
    "reports": "report",
    "examples": "report",
    "plugins": "plugin",
    "evidence": "evidence",
    "ledger": "ledger",
}


def infer_kind(path: Path) -> str:
    """按**所在目录**定类型（dsh-bench#18：文件名前缀可被仿冒，目录才是校验器与生成器共同的判定面）。"""
    return DIR_KIND.get(path.parent.name, "")


def validate_file(path: Path, verdict, release: bool = False, now: str = None) -> tuple:
    # dsh-bench#27 S5：隔离收口在本体——两条入口（CLI main / 生成器发布闸）自动一致
    try:
        return _validate_file_impl(path, verdict, release=release, now=now)
    except Exception as exc:
        return False, ["校验器内部异常：%s: %s" % (type(exc).__name__, exc)]


def _validate_file_impl(path: Path, verdict, release: bool = False, now: str = None) -> tuple:
    kind = infer_kind(path)
    if not kind:
        return False, ["无法从所在目录推断类型（须位于 reports/、examples/、plugins/、evidence/、ledger/ 之一）"]
    try:
        data = _load_json_strict(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, ["读取/解析失败：%s" % exc]
    if not isinstance(data, dict):
        return False, ["顶层必须是 JSON 对象，实为 %s" % type(data).__name__]
    schema = json.loads((SCHEMA_DIR / KIND_SCHEMA[kind]).read_text(encoding="utf-8"))
    errors = validate_schema(data, schema)
    errors += find_forbidden(data)
    if data.get("schema_version") != schema.get("schema_version"):
        errors.append(
            "schema_version 不符：文件 %r，schema %r" % (data.get("schema_version"), schema.get("schema_version"))
        )
    if kind == "report":
        ev_ok, ev_errors = _resolve_evidence(path, data, verdict)
        errors += ev_errors
        errors += semantic_report(data, verdict, evidence_ok=ev_ok, now=now)
        if release and data.get("report_status") != "正式":
            errors.append("发布检查：report_status=%r，正式发布只收「正式」" % data.get("report_status"))
        if release and data.get("is_example"):
            errors.append("发布检查：示例数据不得作为正式发布")
    elif kind == "evidence":
        errors += semantic_evidence(data, verdict)
    elif kind == "plugin":
        errors += semantic_plugin(data, verdict)
        if release and data.get("tested_depth") in (None, "未测", "作者自测"):
            errors.append("发布检查：tested_depth=%r 不足以上能力榜" % data.get("tested_depth"))
    elif kind == "ledger":
        # dsh-bench#27 S1：对账按账本月份——只统计 tested_at 落在该月的正式报告
        actual = 0
        rep_dir = path.parent.parent / "reports"
        month = str(data.get("month") or "")
        if rep_dir.is_dir():
            for p2 in sorted(rep_dir.glob("*.json")):
                try:
                    d2 = _load_json_strict(p2.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
                if d2.get("report_status") == "正式" and month and str(d2.get("tested_at") or "")[:7] == month:
                    actual += 1
        errors += semantic_ledger(data, verdict, actual_published=actual)
    return (not errors), errors


def iter_data_files(data_dir: Path):
    for sub in ("plugins", "reports", "ledger", "examples", "evidence"):
        d = data_dir / sub
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                yield p


def validate_tree(data_dir: Path, verdict, release: bool = False, now: str = None) -> tuple:
    """跨文件检查（dsh-bench#28 T3）：同月账本唯一等；校验器 main 与生成器发布闸共用同一口径。"""
    files = list(iter_data_files(data_dir))
    failures = []
    seen_months = {}
    for p in files:
        if infer_kind(p) == "ledger":
            try:
                d3 = _load_json_strict(p.read_text(encoding="utf-8"))
                m3 = d3.get("month")
                if m3 in seen_months:
                    rel3 = p.relative_to(data_dir) if data_dir in p.parents else p
                    failures.append((rel3, ["账本月份重复：%s 同时见于 %s 与 %s" % (m3, seen_months[m3], p.name)]))
                else:
                    seen_months[m3] = p.name
            except (OSError, json.JSONDecodeError, ValueError):
                pass
    for p in files:
        # dsh-bench#28 T3 边界：examples/ 是演示数据，不施 release 专属检查（官方构建本来就不含它们）
        rel_release = release and p.parent.name != "examples"
        ok, errors = validate_file(p, verdict, release=rel_release, now=now)
        rel = p.relative_to(data_dir) if data_dir in p.parents else p
        if not ok:
            failures.append((rel, errors))
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dsh-bench 插件测评站 · 发布前检查器")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--release", action="store_true", help="正式发布模式：只收「正式」状态、拒示例")
    ap.add_argument("--as-of", default=None, help="复算时钟（YYYY-MM-DD）；--release 时必须为今天（dsh-bench#26）")
    args = ap.parse_args(argv)
    if args.as_of is not None:
        try:
            datetime.strptime(args.as_of, "%Y-%m-%d")
        except ValueError:
            print("--as-of 须为 YYYY-MM-DD，实为 %r" % args.as_of, file=sys.stderr)
            return 2
        if args.release and args.as_of != date.today().isoformat():
            print("--release 的复算时钟必须是今天（--as-of 只用于非发布复算）", file=sys.stderr)
            return 2

    verdict = load_verdict()
    data_dir = Path(args.data_dir)
    files = list(iter_data_files(data_dir))
    if not files:
        print("没有可校验的数据文件（%s）——目录为空或路径写错本身即发布异常（fail-closed）" % data_dir)
        return 1

    failures = validate_tree(data_dir, verdict, release=args.release, now=args.as_of)
    failed = 0
    printed = set()
    for rel, errors in failures:
        failed += 1
        printed.add(str(rel))
        print("FAIL  %s" % rel)
        for e in errors:
            print("        - %s" % e)
    for p in files:
        rel = p.relative_to(data_dir) if data_dir in p.parents else p
        if str(rel) not in printed:
            print("PASS  %s" % rel)
    print("-" * 60)
    print("共 %d 件，通过 %d，拒绝 %d（不通过即拒绝发布）" % (len(files), len(files) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
