#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守卫：发布前检查器 + 静态站生成器（fail-closed 与「不能手填」）。

真源：
  - docs/spec/插件测评站-方案v3.0-完整修订版.md §17（发布前检查必要字段、公式、
    证据链接、隐私和复核；成绩/区间/标签从有效证据自动生成，不能手填）
  - docs/spec/工程协议手册-v1.1.md §5（成本分母）、§13.3（数据模型）、§13.5（隐私）、
    §13.7（排名与时效）

变异清单见文件末尾 MUTATIONS，由 tests/mutate_selfcheck.py 执行自证。

跑法：python -X utf8 tests/test_validate_data.py [--list|--only ID|--json]
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VD = _load("validate_data_under_test", "scripts/validate_data.py")
BS = _load("build_site_under_test", "scripts/build_site.py")

TESTS = []


def run_case(tid):
    def deco(fn):
        TESTS.append((tid, fn))
        return fn

    return deco


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def base_report() -> dict:
    return copy.deepcopy(
        json.loads((ROOT / "data" / "examples" / "report-example-memory.json").read_text(encoding="utf-8"))
    )


def write_and_validate(report: dict, release: bool = False, evidence_id: str = None):
    """隔离数据目录：reports/report-case.json + evidence/（#18 类型按目录、#7 证据解析）。

    evidence_id 缺省时按报告自带的 evidence.evidence_id 取对应示例证据文件。
    """
    tmp = Path(tempfile.mkdtemp(prefix="lpb_val_"))
    d = tmp / "reports"
    d.mkdir()
    p = d / "report-case.json"
    p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    ev = tmp / "evidence"
    ev.mkdir()
    eid = evidence_id or (report.get("evidence") or {}).get("evidence_id") or ""
    if eid:
        src_ev = ROOT / "data" / "evidence" / ("evidence-%s.json" % eid.replace("ev-", "", 1))
        if src_ev.exists():
            (ev / src_ev.name).write_text(src_ev.read_text(encoding="utf-8"), encoding="utf-8")
    return VD.validate_file(p, VD.load_verdict(), release=release)


def errors_text(errors) -> str:
    return " | ".join(errors)


# --------------------------------------------------------------- 正例


@run_case("t50_validator_accepts_repo_data")
def t50():
    verdict = VD.load_verdict()
    files = sorted((ROOT / "data" / "examples").glob("report-*.json")) + sorted(
        (ROOT / "data" / "plugins").glob("*.json")
    ) + sorted((ROOT / "data" / "ledger").glob("*.json")) + sorted(
        (ROOT / "data" / "evidence").glob("*.json")
    )
    check(len(files) >= 8, "仓内应有 ≥8 件示例数据，实得 %d" % len(files))
    for p in files:
        ok, errors = VD.validate_file(p, verdict)
        check(ok, "%s 应通过，实得：%s" % (p.name, errors_text(errors)))
    check(VD.main(["--data-dir", str(ROOT / "data")]) == 0, "整仓数据应通过检查器")


# --------------------------------------------------------------- 各类缺项各自被拒


@run_case("t51_missing_required_field_rejected")
def t51():
    r = base_report()
    del r["domain"]
    ok, errors = write_and_validate(r)
    check(not ok, "缺必填字段必须被拒")
    check("domain" in errors_text(errors), "须点名缺哪个字段：%s" % errors_text(errors))


@run_case("t52_formula_mismatch_rejected")
def t52():
    r = base_report()
    r["half_width"] = 99
    ok, errors = write_and_validate(r)
    check(not ok, "半宽公式不符必须被拒")
    check("half_width" in errors_text(errors), "须点名公式：%s" % errors_text(errors))


@run_case("t53_handfilled_state_rejected")
def t53():
    r = base_report()
    r["interval"] = {"L": -2, "U": 2}   # 复算应为「没啥差别」
    r["half_width"] = 2
    ok, errors = write_and_validate(r)
    check(not ok, "手填能力状态必须被拒")
    check("手填" in errors_text(errors), "须点名成绩/区间/标签禁止手填：%s" % errors_text(errors))


@run_case("t54_expiry_mismatch_rejected")
def t54():
    r = base_report()
    r["expires_at"] = "2027-12-30"
    ok, errors = write_and_validate(r)
    check(not ok, "到期日与测完+90天不符必须被拒")
    check("续期" in errors_text(errors), "须点名改日期不算续期：%s" % errors_text(errors))


@run_case("t55_cost_zero_as_number_rejected")
def t55():
    r = base_report()
    r["cost"]["success_count"] = 0
    r["cost"]["unit_success_cost"] = 2.0
    r["cost"]["incremental_unit_success_cost"] = 3.0
    ok, errors = write_and_validate(r)
    check(not ok, "成本分母为 0 时出现数字必须被拒")
    check("成本分母" in errors_text(errors), "须点名成本分母：%s" % errors_text(errors))


@run_case("t56_forbidden_total_score_rejected")
def t56():
    r = base_report()
    r["total_score"] = 88
    ok, errors = write_and_validate(r)
    check(not ok, "出现总分字段必须被拒")
    check("禁止" in errors_text(errors), "须点名禁止总分/星级：%s" % errors_text(errors))


@run_case("t57_review_not_independent_rejected")
def t57():
    r = base_report()
    r["review"]["independent"] = False
    ok, errors = write_and_validate(r)
    check(not ok, "非独立复核必须被拒")
    check("独立复核" in errors_text(errors), "须点名复核：%s" % errors_text(errors))
    r2 = base_report()
    r2["review"]["independent_reviewer"] = r2["review"]["executor"]
    ok2, errors2 = write_and_validate(r2)
    check(not ok2, "执行与复核同一人必须被拒")


@run_case("t58_release_mode_rejects_trial")
def t58():
    r = base_report()
    ok, errors = write_and_validate(r, release=True)
    check(not ok, "正式发布模式须拒绝「方法试验」")
    check("report_status" in errors_text(errors), "须点名发布检查（report_status）：%s" % errors_text(errors))


@run_case("t60_schema_version_mismatch_rejected")
def t60():
    r = base_report()
    r["schema_version"] = "0.9.0"
    ok, errors = write_and_validate(r)
    check(not ok, "schema_version 不符必须被拒")
    check("schema_version" in errors_text(errors), "须点名 schema_version：%s" % errors_text(errors))


@run_case("t61_plugin_identity_unobservable_needs_note")
def t61():
    p = ROOT / "data" / "plugins" / "plugin-example-tool.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["identity"]["note"] = ""
    d = Path(tempfile.mkdtemp(prefix="lpb_pl_")) / "plugins"
    d.mkdir()
    p2 = d / "plugin-case.json"
    p2.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ok, errors = VD.validate_file(p2, VD.load_verdict())
    check(not ok, "无 commit 可观测却无说明必须被拒")
    check("无 commit 可观测" in errors_text(errors), "须点名该约束：%s" % errors_text(errors))


@run_case("t62_evidence_minimal_proof_missing")
def t62():
    p = ROOT / "data" / "evidence" / "evidence-example-001.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["minimal_proof"]["state_restore"] = []
    d = Path(tempfile.mkdtemp(prefix="lpb_ev_")) / "evidence"
    d.mkdir()
    p2 = d / "evidence-case.json"
    p2.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ok, errors = VD.validate_file(p2, VD.load_verdict())
    check(not ok, "最小证明集缺项必须被拒")
    check("state_restore" in errors_text(errors), "须点名缺哪项：%s" % errors_text(errors))


@run_case("t63_privacy_redaction_required")
def t63():
    r = base_report()
    r["evidence"]["public_redacted"] = False
    r["evidence"]["original_deleted"] = False
    ok, errors = write_and_validate(r)
    check(not ok, "公开件未脱敏必须被拒")
    check("脱敏" in errors_text(errors), "须点名隐私：%s" % errors_text(errors))


# --------------------------------------------------------------- 生成器守卫


def _legal_ev_index():
    return {"ev-example-001": json.loads(
        (ROOT / "data" / "evidence" / "evidence-example-001.json").read_text(encoding="utf-8")
    )}


@run_case("t59_build_rejects_handfilled_state")
def t59():
    r = base_report()
    r["capability_state"] = "帮倒忙"   # 与区间复算不符
    try:
        BS.check_report(dict(r, _source="case"), _legal_ev_index())
        raise AssertionError("生成器必须拒绝手填状态")
    except BS.BuildError as exc:
        check("禁止手填" in str(exc), "中止理由须点名禁止手填：%s" % exc)


@run_case("t64_build_rejects_total_score")
def t64():
    r = base_report()
    r["total_score"] = 88
    try:
        BS.check_report(dict(r, _source="case"), _legal_ev_index())
        raise AssertionError("生成器必须拒绝总分字段")
    except BS.BuildError as exc:
        check("total_score" in str(exc), "中止理由须点名字段：%s" % exc)


@run_case("t65_site_has_no_total_score_and_keeps_boards_separate")
def t65():
    import re

    data = BS.load_data(ROOT / "data", include_examples=True)
    out = Path(tempfile.mkdtemp(prefix="lpb_site_"))
    BS.build(out, data)
    index = (out / "index.html").read_text(encoding="utf-8")
    check("不给总分" in index, "首页须明示不给总分")
    check(not re.search(r"总分\s*[:：=]", index), "首页不得出现总分取值")
    check("三张单分开" in index, "须明示三张单分开")
    report = next(p for p in out.glob("report-*.html") if "memory" in p.name)
    html = report.read_text(encoding="utf-8")
    check("花了多少钱" in html and "安全提醒" in html or "安全" in html, "报告页须把花费与安全分开摆")
    check("不能手填" in html, "报告页须注明数字从证据自动算出")


@run_case("t66_domains_page_excludes_unranked")
def t66():
    data = BS.load_data(ROOT / "data", include_examples=True)
    out = Path(tempfile.mkdtemp(prefix="lpb_site2_"))
    BS.build(out, data)
    html = (out / "domains.html").read_text(encoding="utf-8")
    check("不参加数字排名（照常展示）" in html, "各域榜须单列不参加数字排名的条目")
    rows = html.count("<tr><td>")
    all_reports = data["reports"] + data["examples"]
    ranked = sum(1 for r in all_reports if r["_decision"].rank_eligible)
    check(rows == ranked, "排名表行数应等于进排名的报告数：表内 %d，应为 %d" % (rows, ranked))
    # 不参加排名的报告只能出现在单列区（<li>，带来源路径），不得进排名表
    for r in all_reports:
        if not r["_decision"].rank_eligible:
            check(
                "<li>%s：%s（报告 %s）</li>" % (r["plugin_id"], r["_decision"].capability_state, r.get("report_id", "")) in html,
                "不参加排名的 %s 未出现在单列区" % r["_source"],
            )


@run_case("t67_public_face_is_clean")
def t67():
    """公开面检查必须干净：不得含本机绝对路径、凭据、内部代号、内部私有路径。"""
    PC = _load("public_check_under_test", "scripts/public_check.py")
    hits = {}
    total = 0
    for p in PC.iter_files(ROOT):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = PC.scan_text(text)
        if found:
            hits[str(p.relative_to(ROOT)).replace("\\", "/")] = found
            total += len(found)
    check(total == 0, "公开面检查有 %d 处命中：%s" % (total, hits))


@run_case("t68_spec_hash_registry_in_sync")
def t68():
    """真源哈希登记必须与实际文件一致（手册：正式实验必须绑定实际文件哈希）。

    哈希按**行尾归一化后的内容**算（CRLF→LF）：Git 在不同平台签出时行尾可能不同，
    按原始字节算哈希会让同一份内容在不同机器上判出不同结果。
    """
    import hashlib

    registry = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for p in sorted((ROOT / "docs" / "spec").glob("*.md")):
        normalized = p.read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(normalized).hexdigest()
        check(digest in registry, "%s 的哈希未登记在 docs/README.md" % p.name)


@run_case("t69_quickcheck_depth_cannot_claim_five_state")
def t69():
    """方案 §8：体验快测不得产出能力 Δ 结论（校验器层也必须挡住）。"""
    r = json.loads(
        (ROOT / "data" / "examples" / "report-example-quickcheck.json").read_text(encoding="utf-8")
    )
    ok, errors = write_and_validate(r)
    check(ok, "快测示例应通过：%s" % errors_text(errors))

    # 注入：快测档改口宣称「真有用」⇒ 必须被拒并点名档位越界
    bad = copy.deepcopy(r)
    bad["capability_state"] = "真有用"
    bad["label_kind"] = "five_state"
    bad["capability_delta_concluded"] = True
    bad["rank_eligible"] = True
    ok2, errors2 = write_and_validate(bad)
    check(not ok2, "快测档宣称五状态必须被拒")
    text = errors_text(errors2)
    check("档位越界" in text, "须点名档位越界：%s" % text)
    check("能力 Δ" in text, "须点名「快测不测能力 Δ」：%s" % text)

    # 注入：快测结论位出现禁用词 ⇒ 必须被拒
    bad2 = copy.deepcopy(r)
    bad2["human_text"]["结论"] = "快测通过"
    ok3, errors3 = write_and_validate(bad2)
    check(not ok3, "快测结论位出现禁用词必须被拒")
    check("快测禁用词" in errors_text(errors3), "须点名快测禁用词：%s" % errors_text(errors3))

    # 对照：同内容在标准测评档是允许的五状态报告（防「把所有档都退化成快测」）
    std = json.loads(
        (ROOT / "data" / "examples" / "report-example-memory.json").read_text(encoding="utf-8")
    )
    ok4, errors4 = write_and_validate(std)
    check(ok4, "标准测评档五状态应通过：%s" % errors_text(errors4))


# --------------------------------------------------------------- 变异清单


# --------------------------------------------------------------- 第一批修复（dsh-bench#2/#7/#9/#15/#18）


@run_case("t70_thresholds_frozen_reject_report_values")
def t70():
    r = base_report()
    r["a"] = 0.1
    r["max_half_width"] = 50
    ok, errors = write_and_validate(r)
    check(not ok, "报告自带门槛必须被拒")
    txt = errors_text(errors)
    check("平台冻结" in txt and "a=0.1" in txt.replace("0.1'", "0.1'"), "中止理由须点名门槛冻结：%s" % txt)


@run_case("t71_scores_must_cross_check")
def t71():
    verdict = VD.load_verdict()
    r = base_report()
    r["delta"] = -50
    errors = VD.semantic_report(r, verdict)
    txt = errors_text(errors)
    check("分数不符" in txt, "delta ≠ p−b0 须报「分数不符」：%s" % txt)
    check("分数与区间不符" in txt, "delta 区间外须报「分数与区间不符」：%s" % txt)
    r2 = base_report()
    r2["interval"] = {"L": 6, "U": 10}
    r2["half_width"] = 2.0
    r2.pop("delta")
    errors2 = VD.semantic_report(r2, verdict)
    check(any("同有同无" in e for e in errors2), "interval 与 delta 同有同无须成立：%s" % errors_text(errors2))


@run_case("t72_future_tested_at_rejected")
def t72():
    verdict = VD.load_verdict()
    r = base_report()
    r["tested_at"] = "2099-01-01"
    r["expires_at"] = "2099-04-01"
    errors = VD.semantic_report(r, verdict)
    check(any("测完日期在未来" in e for e in errors), "未来测完日期须被拒：%s" % errors_text(errors))


@run_case("t73_kind_by_directory_not_prefix")
def t73():
    verdict = VD.load_verdict()
    tmp = Path(tempfile.mkdtemp(prefix="lpb_kind_"))
    rep = tmp / "reports"
    rep.mkdir()
    r = base_report()
    # 报告改名 ledger-k.json 放 reports/：仍按报告校验（#18）
    p = rep / "ledger-k.json"
    r["half_width"] = 99
    p.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    ok, errors = VD.validate_file(p, verdict)
    check(not ok and any("half_width" in e for e in errors), "reports/ 下文件须按报告校验（不看文件名）：%s" % errors_text(errors))
    # 放根目录：无法推断类型
    p2 = tmp / "whatever.json"
    p2.write_text(json.dumps(base_report(), ensure_ascii=False), encoding="utf-8")
    ok2, errors2 = VD.validate_file(p2, verdict)
    check(not ok2 and any("所在目录" in e for e in errors2), "根目录文件须报「无法从所在目录推断类型」：%s" % errors_text(errors2))


@run_case("t74_dangling_evidence_rejected")
def t74():
    r = base_report()
    r["evidence"]["evidence_id"] = "does-not-exist"
    ok, errors = write_and_validate(r)
    check(not ok and any("悬空" in e for e in errors), "悬空证据引用必须被拒：%s" % errors_text(errors))


@run_case("t75_evidence_fail_propagates")
def t75():
    verdict = VD.load_verdict()
    tmp = Path(tempfile.mkdtemp(prefix="lpb_ev_"))
    rep = tmp / "reports"; rep.mkdir()
    evd = tmp / "evidence"; evd.mkdir()
    ev = json.loads((ROOT / "data" / "evidence" / "evidence-example-001.json").read_text(encoding="utf-8"))
    ev["minimal_proof"].pop("security", None)   # 证据自身缺必要项
    (evd / "evidence-example-001.json").write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    (rep / "report-case.json").write_text(json.dumps(base_report(), ensure_ascii=False), encoding="utf-8")
    ok, errors = VD.validate_file(rep / "report-case.json", verdict)
    check(not ok and any("自身校验未通过" in e for e in errors), "证据 FAIL 须传播到引用它的报告：%s" % errors_text(errors))



# --------------------------------------------------------------- 第二批 2a（#8②/#16/#17/#22/#23/#12④）


@run_case("t76_run_status_and_untestable_reason_accepted")
def t76():
    verdict = VD.load_verdict()
    r = base_report()
    r["capability_state"] = "没法测"
    r["install_status"] = "安装成功"
    r["untestable_reason"] = "环境不支持"
    r["interval"] = None
    r["half_width"] = None
    r["precision_ok"] = False
    r["b0_score"] = r["p_score"] = r["delta"] = None
    r["human_text"]["结论"] = "没法测（环境不支持）。"
    ok, errors = write_and_validate(r)
    check(ok, "环境不支持如实填写须通过：%s" % errors_text(errors))
    r2 = base_report()
    r2["capability_state"] = "实验无效/待重跑"
    r2["run_status"] = "实验无效"
    r2["precision_ok"] = None if False else r2["precision_ok"]
    r2["precision_ok"] = False
    r2["human_text"]["结论"] = "实验无效/待重跑。"
    ok2, errors2 = write_and_validate(r2)
    check(ok2, "实验无效如实填写须通过：%s" % errors_text(errors2))


@run_case("t77_security_events_status_reconciled")
def t77():
    verdict = VD.load_verdict()
    r = base_report()
    r["security"]["status"] = "未发现问题"
    r["security"]["events"] = [{"severity": "关键", "desc": "x"}]
    ok, errors = write_and_validate(r)
    check(not ok and any("安全对账不符" in e for e in errors), "events 与 status 须对账：%s" % errors_text(errors))


@run_case("t78_investigation_requires_started_at")
def t78():
    verdict = VD.load_verdict()
    r = base_report()
    r["security"]["status"] = "调查中"
    r["security"]["investigation_started_at"] = "2026-01-01"
    ok, errors = write_and_validate(r)
    check(not ok and any("超过 14 天" in e for e in errors), "调查超期须被点名：%s" % errors_text(errors))
    r2 = base_report()
    r2["security"]["status"] = "调查中"
    ok2, errors2 = write_and_validate(r2)
    check(not ok2 and any("investigation_started_at" in e for e in errors2), "调查中缺起始日期须被拒：%s" % errors_text(errors2))


@run_case("t79_reviewed_at_order_enforced")
def t79():
    verdict = VD.load_verdict()
    r = base_report()
    r["review"]["reviewed_at"] = "2020-01-01"
    ok, errors = write_and_validate(r)
    check(not ok and any("早于 tested_at" in e for e in errors), "复核早于测完须被拒：%s" % errors_text(errors))


@run_case("t80_negative_cost_rejected")
def t80():
    r = base_report()
    r["cost"]["total_cost"] = -12
    ok, errors = write_and_validate(r)
    check(not ok, "负总成本须被拒：%s" % errors_text(errors))


@run_case("t81_nan_literal_rejected")
def t81():
    tmp = Path(tempfile.mkdtemp(prefix="lpb_nan_"))
    d = tmp / "reports"; d.mkdir()
    ev = tmp / "evidence"; ev.mkdir()
    (ev / "evidence-example-001.json").write_text((ROOT / "data/evidence/evidence-example-001.json").read_text(encoding="utf-8"), encoding="utf-8")
    import re as _re
    raw = _re.sub(r'"half_width":\s*[0-9.]+', '"half_width": NaN', (ROOT / "data/examples/report-example-memory.json").read_text(encoding="utf-8"), count=1)
    (d / "report-case.json").write_text(raw, encoding="utf-8")
    ok, errors = VD.validate_file(d / "report-case.json", VD.load_verdict())
    check(not ok and any("NaN" in e or "非标准数值" in e for e in errors), "NaN 字面量须被拒：%s" % errors_text(errors))


@run_case("t82_broken_file_does_not_silence_batch")
def t82():
    verdict = VD.load_verdict()
    tmp = Path(tempfile.mkdtemp(prefix="lpb_h3_"))
    d = tmp / "reports"; d.mkdir()
    (d / "report-bad.json").write_text("[1, 2]", encoding="utf-8")
    (d / "report-good.json").write_text(json.dumps(base_report(), ensure_ascii=False), encoding="utf-8")
    (tmp / "evidence").mkdir()
    (tmp / "evidence" / "evidence-example-001.json").write_text((ROOT / "data/evidence/evidence-example-001.json").read_text(encoding="utf-8"), encoding="utf-8")
    rc = VD.main(["--data-dir", str(tmp)])
    check(rc == 1, "坏文件存在时退出码须非 0")
    ok, errors = VD.validate_file(d / "report-good.json", verdict)
    check(ok, "合格报告独立校验须通过：%s" % errors_text(errors))


@run_case("t83_corrections_structure_enforced")
def t83():
    verdict = VD.load_verdict()
    r = base_report()
    r["report_version"] = 3
    r["corrections"] = []
    ok, errors = write_and_validate(r)
    check(not ok and any("更正记录" in e for e in errors), "第 3 版无更正记录须被拒：%s" % errors_text(errors))
    r2 = base_report()
    r2["corrections"] = [123]
    ok2, errors2 = write_and_validate(r2)
    check(not ok2 and any("更正记录须为对象" in e for e in errors2), "非对象更正须被拒：%s" % errors_text(errors2))


@run_case("t84_disclosure_required_for_official")
def t84():
    verdict = VD.load_verdict()
    r = base_report()
    r["report_status"] = "正式"
    r["disclosure"] = ""
    ok, errors = write_and_validate(r)
    check(not ok and any("disclosure" in e for e in errors), "正式报告空利益关系须被拒：%s" % errors_text(errors))


@run_case("t85_ledger_month_and_reconciliation")
def t85():
    verdict = VD.load_verdict()
    led = json.loads((ROOT / "data/ledger/ledger-2026-10.json").read_text(encoding="utf-8"))
    bad = dict(led, month="2026-13")
    errors = VD.semantic_ledger(bad, verdict, actual_published=0)
    check(any("月份非法" in e for e in errors), "月份 2026-13 须被拒：%s" % errors_text(errors))
    bad2 = dict(led, failed_count=9, tested_count=4)
    errors2 = VD.semantic_ledger(bad2, verdict, actual_published=0)
    check(any("失败数" in e for e in errors2), "failed>tested 须被拒：%s" % errors_text(errors2))
    bad3 = dict(led, published_reports=5)
    errors3 = VD.semantic_ledger(bad3, verdict, actual_published=0)
    check(any("账本不对账" in e for e in errors3), "published 与实际不符须被拒：%s" % errors_text(errors3))


@run_case("t86_human_conclusion_must_match_recomputed")
def t86():
    verdict = VD.load_verdict()
    r = base_report()
    r["human_text"]["结论"] = "❌ 帮倒忙：越用越糟。"
    ok, errors = write_and_validate(r)
    check(not ok and any("与复算状态" in e for e in errors), "人话结论与复算不符须被拒：%s" % errors_text(errors))

MUTATIONS = [
    {"id": "W51", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "        for key in schema.get(\"required\", []):",
     "replace": "        for key in []:",
     "count": 1, "expect": ["t51_missing_required_field_rejected"], "rule": "必填字段检查"},
    {"id": "W52", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "        if got is None or abs(got - want) > 1e-9:",
     "replace": "        if False:",
     "count": 1, "expect": ["t52_formula_mismatch_rejected", "t73_kind_by_directory_not_prefix"], "rule": "半宽公式复算"},
    {"id": "W53", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if declared != d.capability_state:",
     "replace": "    if False:",
     "count": 1, "expect": ["t53_handfilled_state_rejected"], "rule": "状态不得手填（复算比对）"},
    {"id": "W54", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if not verdict.expiry_consistent(report.get(\"tested_at\"), report.get(\"expires_at\")):",
     "replace": "    if False:",
     "count": 1, "expect": ["t54_expiry_mismatch_rejected"], "rule": "到期日 = 测完 + 90 天"},
    {"id": "W55", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "            if isinstance(got, (int, float)) and not isinstance(got, bool):\n"
               "                errors.append(\"成本分母为 0/无效时不得出现数字：unit_success_cost=%r\" % (got,))\n"
               "            elif not isinstance(got, str) or \"不适用\" not in got:\n"
               "                errors.append(\"成本分母为 0/无效时应显示「不适用：没有成功任务」，实为 %r\" % (got,))",
     "replace": "            pass",
     "count": 1, "expect": ["t55_cost_zero_as_number_rejected"], "rule": "成本分母为 0 禁除零"},
    {"id": "W56", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    \"total_score\",\n",
     "replace": "",
     "count": 1, "expect": ["t56_forbidden_total_score_rejected"], "rule": "禁总分/星级字段"},
    {"id": "W57", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if not review.get(\"independent\"):",
     "replace": "    if False:",
     "count": 1, "expect": ["t57_review_not_independent_rejected"], "rule": "执行与复核分离"},
    {"id": "W58", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "        if release and data.get(\"report_status\") != \"正式\":",
     "replace": "        if False:",
     "count": 1, "expect": ["t58_release_mode_rejects_trial"], "rule": "正式发布只收「正式」"},
    {"id": "W60", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if data.get(\"schema_version\") != schema.get(\"schema_version\"):",
     "replace": "    if False:",
     "count": 1, "expect": ["t60_schema_version_mismatch_rejected"], "rule": "版本化 schema_version"},
    {"id": "W61", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if ident.get(\"observable\") is False and not ident.get(\"note\"):",
     "replace": "    if False:",
     "count": 1, "expect": ["t61_plugin_identity_unobservable_needs_note"], "rule": "禁止虚构版本号"},
    {"id": "W62", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "        if not (ev.get(\"minimal_proof\") or {}).get(key):",
     "replace": "        if False:",
     "count": 1, "expect": ["t62_evidence_minimal_proof_missing"], "rule": "最小证明集"},
    {"id": "W63", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if not ev.get(\"public_redacted\") and not ev.get(\"original_deleted\"):",
     "replace": "    if False:",
     "count": 1, "expect": ["t63_privacy_redaction_required"], "rule": "隐私：公开件须脱敏"},
    {"id": "W59", "target": "scripts/build_site.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    if d.capability_state != report.get(\"capability_state\"):",
     "replace": "    if False:",
     "count": 1, "expect": ["t59_build_rejects_handfilled_state"], "rule": "生成器拒绝手填状态"},
    {"id": "W64", "target": "scripts/build_site.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    hits = find_forbidden(report)\n    if hits:",
     "replace": "    hits = find_forbidden(report)\n    if False:",
     "count": 1, "expect": ["t64_build_rejects_total_score"], "rule": "生成器拒绝总分字段"},
    {"id": "W65", "target": "site/templates/base.html", "test_file": "tests/test_validate_data.py",
     "anchor": "三张单分开",
     "replace": "合成一个总分",
     "count": 1, "expect": ["t65_site_has_no_total_score_and_keeps_boards_separate"],
     "rule": "三张单分开、不给总分"},
    {"id": "W66", "target": "scripts/build_site.py", "test_file": "tests/test_validate_data.py",
     "anchor": "    ranked = [r for r in reports if r[\"_decision\"].rank_eligible]",
     "replace": "    ranked = list(reports)",
     "count": 1, "expect": ["t66_domains_page_excludes_unranked"], "rule": "还说不准/没法测不进排名"},
    # 注入一个内部代号，公开面检查必须当场点名（本文件正文用转义写，避免自身被扫到）
    {"id": "W67", "target": "scripts/verdict.py", "test_file": "tests/test_validate_data.py",
     "anchor": "description=\"dsh-bench 插件测评站 · 五状态判定算法\"",
     "replace": "description=\"\u7075\u67a2插件测评站 · 五状态判定算法\"",
     "count": 1, "expect": ["t67_public_face_is_clean"], "rule": "公开面检查的检出能力（注入内部代号）"},
    {"id": "W68", "target": "docs/spec/端到端验证计划-v0.md", "test_file": "tests/test_validate_data.py",
     "anchor": "# 端到端验证计划 v0（修订版）",
     "replace": "# 端到端验证计划 v0（修订版·改一字）",
     "count": 1, "expect": ["t68_spec_hash_registry_in_sync"], "rule": "真源哈希登记与实际文件同步"},
    {"id": "W69", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "        if declared_state in verdict.CAPABILITY_DELTA_STATES:",
     "replace": "        if False:",
     "count": 1, "expect": ["t69_quickcheck_depth_cannot_claim_five_state"],
     "rule": "方案 §8 快测不得产出能力 Δ 结论（校验器层）"},
    {"id": "W70", "target": "scripts/validate_data.py", "test_file": "tests/test_validate_data.py",
     "anchor": "            banned = verdict.banned_quick_check_wording(str(line), strict=(field == \"结论\"))",
     "replace": "            banned = []",
     "count": 1, "expect": ["t69_quickcheck_depth_cannot_claim_five_state"],
     "rule": "手册 §6 快测禁用词（校验器层）"},
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


def run_all():
    """执行全部判据，返回失败 id 列表（dsh-bench#1：pytest 聚合入口）。"""
    fails = []
    for tid, fn in TESTS:
        try:
            fn()
        except Exception as exc:
            fails.append("%s: %s" % (tid, exc))
    return fails


def test_run_all():
    fails = run_all()
    assert not fails, "守卫失败项：%s" % fails
