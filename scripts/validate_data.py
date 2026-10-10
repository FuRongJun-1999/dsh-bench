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
import sys
from datetime import datetime, timedelta
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
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def semantic_report(report: dict, verdict) -> list:
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

    # 2) 状态复算
    install_failed = report.get("install_status") == "安装失败"
    sec = report.get("security") or {}
    ev = report.get("evidence") or {}
    d = verdict.decide(
        (L, U) if (L is not None and U is not None) else None,
        a=_num(report.get("a")) or verdict.A_DEFAULT,
        b=_num(report.get("b")) or verdict.B_DEFAULT,
        max_half_width=_num(report.get("max_half_width")) or verdict.MAX_HALF_WIDTH_DEFAULT,
        install_failed=install_failed,
        confirmed_severe_security_event=sec.get("status") == "已确认事件",
        security_investigation=sec.get("status") == "调查中",
        tested_at=report.get("tested_at"),
        now=report.get("tested_at"),  # 只做状态复算，不引入外部时钟
        original_deleted=bool(ev.get("original_deleted")),
        redacted_evidence_sufficient=bool(ev.get("redacted_evidence_sufficient", True)),
        depth=report.get("depth", verdict.DEPTH_STANDARD),
    )
    declared = report.get("capability_state")
    if declared != d.capability_state:
        errors.append(
            "状态与证据不符：声明 %r，按区间复算为 %r（成绩、区间、标签禁止手填）"
            % (declared, d.capability_state)
        )
    if report.get("precision_ok") is not None and d.precision_ok != report.get("precision_ok"):
        errors.append("precision_ok 与复算不符：声明 %r，实为 %r" % (report.get("precision_ok"), d.precision_ok))

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


def semantic_ledger(led: dict, verdict) -> list:
    errors = []
    if led.get("tested_count", 0) < led.get("published_reports", 0):
        errors.append("账本自相矛盾：发布报告数多于已测数")
    return errors


# ------------------------------------------------------------- 主流程


def infer_kind(path: Path) -> str:
    name = path.name
    for kind in KIND_SCHEMA:
        if name.startswith(kind + "-") or name.startswith(kind + "_") or name == kind + ".json":
            return kind
    return ""


def validate_file(path: Path, verdict, release: bool = False) -> tuple:
    kind = infer_kind(path)
    if not kind:
        return False, ["无法从文件名推断类型（须 report-/plugin-/evidence-/ledger- 前缀）"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, ["读取/解析失败：%s" % exc]
    schema = json.loads((SCHEMA_DIR / KIND_SCHEMA[kind]).read_text(encoding="utf-8"))
    errors = validate_schema(data, schema)
    errors += find_forbidden(data)
    if data.get("schema_version") != schema.get("schema_version"):
        errors.append(
            "schema_version 不符：文件 %r，schema %r" % (data.get("schema_version"), schema.get("schema_version"))
        )
    if kind == "report":
        errors += semantic_report(data, verdict)
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
        errors += semantic_ledger(data, verdict)
    return (not errors), errors


def iter_data_files(data_dir: Path):
    for sub in ("plugins", "reports", "ledger", "examples", "evidence"):
        d = data_dir / sub
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dsh-bench 插件测评站 · 发布前检查器")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--release", action="store_true", help="正式发布模式：只收「正式」状态、拒示例")
    args = ap.parse_args(argv)

    verdict = load_verdict()
    data_dir = Path(args.data_dir)
    files = list(iter_data_files(data_dir))
    if not files:
        print("没有可校验的数据文件（%s）" % data_dir)
        return 0

    failed = 0
    for p in files:
        ok, errors = validate_file(p, verdict, release=args.release)
        rel = p.relative_to(data_dir) if data_dir in p.parents else p
        if ok:
            print("PASS  %s" % rel)
        else:
            failed += 1
            print("FAIL  %s" % rel)
            for e in errors:
                print("        - %s" % e)
    print("-" * 60)
    print("共 %d 件，通过 %d，拒绝 %d（不通过即拒绝发布）" % (len(files), len(files) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
