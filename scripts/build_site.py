#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-bench 插件测评站 · 静态站生成器

真源：
  - docs/spec/插件测评站-方案v3.0-完整修订版.md §16（网站有哪些页面）、§17（怎么把网站做出来）
  - docs/spec/工程协议手册-v1.1.md §13.3（数据模型）、§13.7（排名、时效）

做法（方案 §17 原话）：Markdown 内容 + 静态网站 + 版本化 JSON 数据。

硬约束（生成器层面强制）：
  - 成绩、区间、标签从有效证据自动生成，**不能手填**：
    报告里的 capability_state / half_width / 成本单价一律用 verdict.py 复算，
    与声明不一致直接中止构建；出现总分/星级一类字段同样中止。
  - 「还说不准」「没法测」照常展示，但不进数字排名。
  - 三张单分开：能力 / 好不好用 / 花费与安全；第三张里钱与安全也分栏。
  - 未测就写「未测」，不留空。
  - 不在网站机器上运行不可信插件：本站只读 JSON，不执行任何插件代码。
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import shutil
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "site" / "templates"
STATIC_DIR = ROOT / "site" / "static"
CONTENT_DIR = ROOT / "content" / "pages"
DATA_DIR = ROOT / "data"

FORBIDDEN_FIELDS = {"total_score", "overall_score", "stars", "star_rating", "rating", "总分", "星级", "综合分"}

NAV_ITEMS = [
    ("index.html", "首页"),
    ("domains.html", "各域榜"),
    ("method.html", "怎么测"),
    ("schedule.html", "排期"),
    ("corrections.html", "更正"),
    ("ledger.html", "账本"),
]


def load_verdict():
    path = ROOT / "scripts" / "verdict.py"
    spec = importlib.util.spec_from_file_location("verdict_for_site", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 注解解析需要
    spec.loader.exec_module(mod)
    return mod


V = load_verdict()


# ------------------------------------------------------------- 小工具


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def show(x) -> str:
    """未测就写「未测」，不留空。"""
    if x is None or x == "":
        return "未测"
    return str(x)


def slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", str(text)).strip("-")
    return s or "item"


_PH = __import__("re").compile(r"\{\{(\w+)\}\}")


def render(template_name: str, **kw) -> str:
    # dsh-bench#8④：一次性替换——只扫模板原文，填入值里的 {{key}} 字面量不再被二次替换
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    return _PH.sub(lambda m: "" if kw.get(m.group(1)) is None else str(kw[m.group(1)]), text)


def page(title: str, body: str, banner: str = "") -> str:
    nav = "".join('<a href="%s">%s</a>' % (href, esc(label)) for href, label in NAV_ITEMS)
    return render("base.html", title=title, nav=nav, body=body, banner=banner)


def md_to_html(text: str) -> str:
    """极简 Markdown：标题、无序/有序列表、段落、粗体、行内码、代码块。"""
    out, in_code, in_ul, in_ol = [], False, False, False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            close_lists()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(esc(line))
            continue
        if not line.strip():
            close_lists()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            close_lists()
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append("<li>%s</li>" % inline(m.group(1)))
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            out.append("<li>%s</li>" % inline(m.group(1)))
            continue
        close_lists()
        out.append("<p>%s</p>" % inline(line))
    close_lists()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def inline(text: str) -> str:
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def load_content(name: str) -> str:
    p = CONTENT_DIR / name
    if not p.exists():
        return ""
    return md_to_html(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------- 数据装载（含手填守卫）


def find_forbidden(obj, path="$") -> list:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_FIELDS:
                hits.append("%s.%s" % (path, k))
            hits += find_forbidden(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += find_forbidden(v, "%s[%d]" % (path, i))
    return hits


class BuildError(Exception):
    pass


def load_json_dir(d: Path) -> list:
    items = []
    if not d.is_dir():
        return items
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        data["_source"] = _rel(p)
        items.append(data)
    return items


def check_report(report: dict, evidence_index: dict = None) -> dict:
    """复算：状态、半宽、成本单价全部从区间/证据算出；不一致即中止。"""
    hits = find_forbidden(report)
    if hits:
        raise BuildError("%s: 出现禁止的手填分数字段 %s" % (report.get("_source"), hits))

    # dsh-bench#2：门槛由平台冻结，报告自带值不一致即中止
    for key, frozen in (("a", V.A_DEFAULT), ("b", V.B_DEFAULT), ("max_half_width", V.MAX_HALF_WIDTH_DEFAULT)):
        got = report.get(key)
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            raise BuildError("%s: 门槛 %s 缺失或非数值（平台冻结 a=%s / b=%s / max_half_width=%s）" % (report.get("_source"), key, V.A_DEFAULT, V.B_DEFAULT, V.MAX_HALF_WIDTH_DEFAULT))
        if abs(float(got) - frozen) > 1e-9:
            raise BuildError("%s: 门槛 %s=%r 由平台冻结为 %s（报告自带门槛即放宽判定）" % (report.get("_source"), key, got, frozen))

    # dsh-bench#7：证据链必须可解析且证据自身通过，否则中止
    ev_index = evidence_index if evidence_index is not None else {}
    eid = (report.get("evidence") or {}).get("evidence_id")
    ev_doc = ev_index.get(eid)
    if not eid or ev_doc is None:
        raise BuildError("%s: 证据 %r 悬空（evidence_index 无匹配文件；缺必要证据 → 待验证/待修复，不进排名）" % (report.get("_source"), eid))
    ev_errors = _validate_evidence_doc(ev_doc)
    if ev_errors:
        raise BuildError("%s: 证据 %s 自身校验未通过：%s" % (report.get("_source"), eid, "；".join(ev_errors[:3])))

    iv = report.get("interval")
    interval = (float(iv["L"]), float(iv["U"])) if iv else None
    sec = report.get("security") or {}
    ev = report.get("evidence") or {}
    d = V.decide(
        interval,
        a=V.A_DEFAULT,
        b=V.B_DEFAULT,
        max_half_width=V.MAX_HALF_WIDTH_DEFAULT,
        install_failed=report.get("install_status") == "安装失败",
        confirmed_severe_security_event=sec.get("status") == "已确认事件",
        security_investigation=sec.get("status") == "调查中",
        tested_at=report.get("tested_at"),
        now=_NOW or date.today().isoformat(),
        original_deleted=bool(ev.get("original_deleted")),
        redacted_evidence_sufficient=bool(ev.get("redacted_evidence_sufficient", True)),
        depth=report.get("depth"),
        report_status=report.get("report_status", "正式"),
        invalidation=report.get("invalidation"),
        scenario_count=report.get("scenario_count"),
        # dsh-bench#6 F1：证据层输入传入 decide，让 Decision 真正由输入算出
        tool_results=report.get("tool_results"),
        security_six_threats=(report.get("security_scope") or {}).get("threats"),
        quick_check=report.get("quick_check"),
        risk_level=report.get("risk_level"),
    )
    if d.capability_state != report.get("capability_state"):
        raise BuildError(
            "%s: 声明的能力状态 %r 与按区间复算的 %r 不一致（成绩、区间、标签禁止手填）"
            % (report.get("_source"), report.get("capability_state"), d.capability_state)
        )
    report["_decision"] = d
    return report


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


_VD_MODULE = None


def _load_validate_data():
    """按路径加载 validate_data（与 load_verdict 同机制；测试导入本模块时 scripts/ 不在 sys.path）。"""
    global _VD_MODULE
    if _VD_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "validate_data_for_build", Path(__file__).resolve().parent / "validate_data.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _VD_MODULE = mod
    return _VD_MODULE


def _validate_evidence_doc(ev_doc: dict) -> list:
    """跑证据语义校验（dsh-bench#7：证据文件自身 FAIL 须传播到引用它的报告）。"""
    return _load_validate_data().semantic_evidence(ev_doc, V)


def build_evidence_index(data_dir: Path) -> dict:
    idx = {}
    for e in load_json_dir(data_dir / "evidence"):
        if e.get("evidence_id"):
            idx[e["evidence_id"]] = e
    return idx


_NOW = None  # dsh-bench#8①：构建时钟（--as-of，缺省今天），写入 build-manifest.json


def _ver_key(ref: str):
    """从 plugin_identity.ref 提取可比较版本键（@x.y.z → (x,y,z)；不可解析退化为 (0,)）。"""
    tail = str(ref).rsplit("@", 1)[-1] if "@" in str(ref) else ""
    parts = []
    for seg in tail.split("."):
        if seg.isdigit():
            parts.append(int(seg))
        else:
            return (0,)
    return tuple(parts) or (0,)


def load_data(data_dir: Path, include_examples: bool) -> dict:
    ev_index = build_evidence_index(data_dir)
    plugins = load_json_dir(data_dir / "plugins")
    registered = {p.get("plugin_id") for p in plugins}
    reports = [check_report(r, ev_index) for r in load_json_dir(data_dir / "reports")]
    # dsh-bench#11：未登记插件不得进榜（fail-closed）
    for r in reports:
        if r["plugin_id"] not in registered:
            raise BuildError("%s: 插件 %r 未在 data/plugins/ 登记，不得进榜（先登记插件再发报告）" % (r.get("_source"), r["plugin_id"]))
    # dsh-bench#12②：同一插件同一域只取最高版本进榜，旧版本转入不参加排名区
    best = {}
    for r in reports:
        key = (r["plugin_id"], r["domain"])
        ref = (r.get("plugin_identity") or {}).get("ref") or ""
        k = _ver_key(ref)
        if key not in best or k > best[key][0]:
            best[key] = (k, ref, r)
    for r in reports:
        key = (r["plugin_id"], r["domain"])
        _, top_ref, top_r = best[key]
        if r is not top_r:
            r["_superseded_by"] = top_ref
            r.setdefault("_decision").notes.append(
                "plugin_identity.ref=%r 非该插件同域最高版本（最高 %s）：不进数字排名，历史结论保留。" % (ref_of(r), top_ref)
            )
            r["_decision"].rank_eligible = False
    examples = []
    if include_examples:
        for p in sorted((data_dir / "examples").glob("report-*.json")):
            r = json.loads(p.read_text(encoding="utf-8"))
            r["_source"] = _rel(p)
            r["_example"] = True
            examples.append(check_report(r, ev_index))
    return {
        "plugins": plugins,
        "reports": reports,
        "examples": examples,
        "ledger": load_json_dir(data_dir / "ledger"),
        "evidence": load_json_dir(data_dir / "evidence"),
    }


def ref_of(report: dict) -> str:
    return (report.get("plugin_identity") or {}).get("ref") or ""


# ------------------------------------------------------------- 页面片段


def state_chip(decision) -> str:
    return '<span class="chip state">%s</span>' % esc(V.STATE_DISPLAY.get(decision.capability_state, decision.capability_state))


def report_card(report: dict) -> str:
    d = report["_decision"]
    flags = []
    if report.get("_example"):
        flags.append('<span class="chip example">示例（非正式成绩）</span>')
    if not d.rank_eligible:
        flags.append('<span class="chip muted">不参加数字排名</span>')
    if d.expired:
        flags.append('<span class="chip warn">已过 90 天</span>')
    if d.evidence_verification_limited:
        flags.append('<span class="chip warn">证据核验受限</span>')
    return (
        '<li class="card">'
        '<a href="report-%s.html"><strong>%s</strong></a> '
        "%s%s"
        '<div class="meta">%s · %s · 测于 %s</div>'
        "</li>"
    ) % (
        esc(slug(report["report_id"])),
        esc(report["plugin_id"]),
        state_chip(d),
        "".join(flags),
        esc(report["domain"]),
        esc(report["depth"]),
        esc(str(report["tested_at"])[:10]),
    )


def domain_table(reports: list) -> str:
    ranked = [r for r in reports if r["_decision"].rank_eligible]
    unranked = [r for r in reports if not r["_decision"].rank_eligible]
    rows = []
    # 排序键对缺区间（没法测）宽容：不因缺项让整页构建崩掉
    for r in sorted(ranked, key=lambda x: -float((x.get("interval") or {}).get("L") or 0)):
        d = r["_decision"]
        iv = r.get("interval") or {}
        rec_cell = esc(d.recommendation)
        if d.recommendation == "暂停推荐":
            rec_cell = '<span class="chip warn">⛔ %s（安全暂停，不进「值得试」）</span>' % esc(d.recommendation)
        elif d.recommendation == "调查中":
            rec_cell = '<span class="chip warn">⏳ %s</span>' % esc(d.recommendation)
        elif d.recommendation == "不推荐":
            rec_cell = '<span class="chip muted">✕ %s（负贡献）</span>' % esc(d.recommendation)
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>[%s, %s]</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(r["plugin_id"]),
                esc(V.STATE_DISPLAY.get(d.capability_state, d.capability_state)),
                esc(show(iv.get("L"))),
                esc(show(iv.get("U"))),
                esc(show(r.get("b0_score"))),
                esc(show(r.get("p_score"))),
                rec_cell,
            )
        )
    html_ranked = (
        "<table><thead><tr><th>插件</th><th>结论</th><th>区间</th><th>不装</th><th>装了</th><th>推荐</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else "<p>本域暂时没有进入数字排名的结果。</p>"
    )
    html_unranked = ""
    if unranked:
        html_unranked = "<h3>不参加数字排名（照常展示）</h3><ul>" + "".join(
            "<li>%s：%s（报告 %s）</li>"
            % (esc(r["plugin_id"]), esc(r["_decision"].capability_state), esc(r.get("report_id", "")))
            for r in unranked
        ) + "</ul>"
    note = (
        "<p class=\"note\">只有同域、同题集/权重、同模型、同宿主及预算制度的可比组才放在一起比。"
        "两份报告差得多，不等于这两个插件互相之间就一定有明显差别。</p>"
    )
    return html_ranked + html_unranked + note


def comparability_groups(reports: list) -> dict:
    groups = {}
    for r in reports:
        c = r.get("comparability") or {}
        # dsh-bench#11：可比组必须绑定题集/判分器/协议哈希——自由文本相同但哈希不同不可比
        key = (r["domain"], c.get("model"), c.get("host"), c.get("taskset"), c.get("budget_policy"),
               r.get("taskset_hash"), r.get("scorer_hash"), r.get("protocol_hash"))
        groups.setdefault(key, []).append(r)
    return groups


def cost_block(report: dict) -> str:
    cost = report["cost"]
    unit = cost["unit_success_cost"]
    incr = cost["incremental_unit_success_cost"]
    unit_s = unit if isinstance(unit, str) else "%.4f" % float(unit)
    incr_s = incr if isinstance(incr, str) else "%.4f" % float(incr)
    return (
        "<ul class=\"cost\">"
        "<li>一共花了 %s，干成 %s 件。</li>"
        "<li>每干成一件花多少钱：%s</li>"
        "<li>多干成一件多花多少钱：%s</li>"
        "<li>不装那边花了 %s，干成 %s 件。</li>"
        "</ul>"
    ) % (
        esc(cost["total_cost"]),
        esc(cost["success_count"]),
        esc(unit_s),
        esc(incr_s),
        esc(cost["baseline_cost"]),
        esc(cost["baseline_success_count"]),
    )


# ------------------------------------------------------------- 页面生成


def build(out_dir: Path, data: dict) -> list:
    # dsh-bench#21 M1：构建是全量重建——先清空输出目录，撤下的报告页不再残留
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    for f in STATIC_DIR.glob("*"):
        (out_dir / "static" / f.name).write_bytes(f.read_bytes())

    all_reports = data["reports"] + data["examples"]
    banner = ""
    if data["examples"]:
        banner = (
            '<div class="banner">示例数据（非正式成绩）：本仓当前只有方案与验证计划，'
            "还没有经过验证的正式成绩。下列带「示例」标签的条目只用于演示生成链路。</div>"
        )
    written = []

    # 首页
    entries = "".join(
        '<a class="entry" href="%s"><strong>%s</strong><span>%s</span></a>' % (href, title, desc)
        for href, title, desc in [
            ("domains.html", "我想干什么", "按能力域看：记忆、工具、安全边界……"),
            ("worth_trying.html", "哪些值得试", "只列通过推荐资格（worth_trying）的结果；安全暂停与调查中不在此列。"),
            ("reports.html", "哪些有坑", "安全提醒、已确认事件和更正记录。"),
            ("method.html", "怎么测的", "方法、题目、判分程序和局限。"),
        ]
    )
    # dsh-bench#10：覆盖面从报告推导——有报告才算「已测」，深度列报告实际档位
    prs_by_plugin = {}
    for r in all_reports:
        prs_by_plugin.setdefault(r["plugin_id"], []).append(r)
    coverage = "".join(
        "<li>%s：%s</li>"
        % (
            esc(p["name"]),
            esc(
                "已测（%s）" % "／".join(sorted({str(r.get("depth")) for r in prs_by_plugin.get(p["plugin_id"], [])}))
                if prs_by_plugin.get(p["plugin_id"]) else "未测"
            ),
        )
        for p in data["plugins"]
    ) or "<li>还没有登记插件。未测就写「未测」，不留空。</li>"
    # dsh-bench#21 M5：最新报告按 tested_at 倒序，不再按文件名
    latest = "".join(
        report_card(r) for r in sorted(all_reports, key=lambda x: str(x.get("tested_at")), reverse=True)
    ) or "<li>还没有报告。</li>"
    index_body = render(
        "index.html",
        intro=load_content("index.md"),
        entries=entries,
        reports="<ul class=\"cards\">%s</ul>" % latest,
        coverage="<ul>%s</ul>" % coverage,
        schedule=load_content("schedule.md"),
    )
    (out_dir / "index.html").write_text(page("首页 · 插件测评站", index_body, banner), encoding="utf-8")
    written.append("index.html")

    # 报告页
    for r in all_reports:
        d = r["_decision"]
        rbanner = banner if r.get("_example") else ""
        safety = ""
        sec = r.get("security") or {}
        # dsh-bench#19 J2：只有负面状态进「安全提醒」块；「指定检查通过」等正面状态不进警示版式
        if sec.get("status") in ("已确认事件", "调查中"):
            safety = (
                '<div class="safety"><strong>安全提醒（单独显示）</strong><p>状态：%s</p>'
                "<p>能力结果保留，不改零分、不抹掉。</p></div>"
            ) % esc(sec.get("status"))
        human = "<dl>" + "".join(
            "<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(v)) for k, v in (r.get("human_text") or {}).items()
        ) + "</dl>"
        # dsh-bench#23 Q2：版本与日期由结构化字段生成，手填文本只作补充
        human = ("<p class=\"meta\">被测版本：%s ｜ 测完：%s ｜ 有效期至：%s</p>" %
                 (esc(ref_of(r)) or "未登记", esc(show(r.get("tested_at"))), esc(show(r.get("expires_at"))))) + human
        iv = r.get("interval") or {}
        numbers = (
            "<ul class=\"numbers\">"
            "<li>不装：%s 分；装了：%s 分；差 %s 分。</li>"
            "<li>区间 [%s, %s]，半宽 %s（门槛 ≤%s）。</li>"
            "<li>独立场景数 %s，重跑数 %s。</li>"
            "<li>安装环节：%s。</li>"
            "<li>排名资格：%s；推荐：%s。</li>"
            "</ul>"
        ) % (
            esc(show(r.get("b0_score"))), esc(show(r.get("p_score"))), esc(show(r.get("delta"))),
            esc(show(iv.get("L"))), esc(show(iv.get("U"))),
            esc(show(r.get("half_width"))), esc(V.MAX_HALF_WIDTH_DEFAULT),
            esc(show(r.get("scenario_count"))), esc(show(r.get("rerun_count"))),
            esc(r["install_status"]), "是" if d.rank_eligible else "否", esc(d.recommendation),
        )
        notes = "".join("<li>%s</li>" % esc(n) for n in d.notes)
        scope = "<p>%s</p><ul>%s</ul>" % (esc((r.get("human_text") or {}).get("查了什么没查什么", "")), notes)

        # 三张单分开（方案 §9）：能力 / 好不好用 / 花费与安全；第三张里钱与安全也分栏
        # dsh-bench#6 F1：只渲染 decide 复算结果，报告 JSON 的手填卡片不再上页
        cards_data = d.cards
        cap_card = cards_data.get(V.CARD_CAPABILITY) or {}
        use_card = cards_data.get(V.CARD_USABILITY) or {}
        third_card = cards_data.get(V.CARD_COST_AND_SAFETY) or {}
        cards_html = (
            "<div class=\"cards3\">"
            "<div class=\"card\"><h3>能力</h3><p>%s</p></div>"
            "<div class=\"card\"><h3>好不好用</h3><p>%s</p></div>"
            "<div class=\"card\"><h3>花费与安全</h3>"
            "<p><strong>钱：</strong>%s</p><p><strong>安全：</strong>%s</p></div>"
            "</div><p class=\"note\">三张单分开摆，不给总分；第三张里钱和安全也分栏。</p>"
        ) % (
            esc(cap_card.get("display", "未测")),
            esc(use_card.get("display", "未测")),
            esc((third_card.get("cost") or {}).get("display", "未测")),
            esc((third_card.get("safety") or {}).get("display", "未测")),
        )

        # 工具型四件事（方案 §4.1）—— dsh-bench#6 F1：渲染 decide 输出
        four = d.tool_four_things or {}
        four_html = "<ul>" + "".join(
            "<li>%s：%s</li>" % (esc(k), esc(four.get(k, "未测"))) for k in V.FOUR_THINGS
        ) + "</ul>"

        # 安全专项（方案 §8）：深度审计才有；标准测评标不适用 —— dsh-bench#6 F1：渲染 decide 输出
        sc = d.security_scope or {}
        scope_sec = "<p>%s</p>" % esc(sc.get("display", "未测"))
        if sc.get("applicable"):
            scope_sec += "<ul>" + "".join(
                "<li>%s：%s</li>" % (esc(k), esc(v)) for k, v in (sc.get("threats") or {}).items()
            ) + "</ul>"
            if sc.get("untested"):
                scope_sec += "<p class=\"note\">未测项：%s（未测就写未测，不留空）。</p>" % esc("、".join(sc["untested"]))

        detail = (
            "<ul><li>证据：%s</li><li>实验：%s / 分析：%s</li>"
            "<li>协议哈希：<code>%s</code></li><li>判分器哈希：<code>%s</code></li>"
            "<li>环境：%s</li><li>复核：%s（独立：%s）</li><li>利益关系：%s</li>"
            "<li>题集哈希：<code>%s</code></li>"
            "<li>风险等级：%s%s</li></ul>"
        ) % (
            esc((r.get("evidence") or {}).get("evidence_id")),
            esc(r["experiment_id"]), esc(r["analysis_id"]),
            esc(r["protocol_hash"]), esc(r["scorer_hash"]),
            esc(r["environment_id"]),
            esc((r.get("review") or {}).get("independent_reviewer")),
            "是" if (r.get("review") or {}).get("independent") else "否",
            esc(r.get("disclosure")),
            esc(r.get("taskset_hash")),
            esc(V.RISK_UNASSESSED),
            ("（报告自填 %r，未采信；风险等级由判级依据得出，未查清就写未评估）" % r.get("risk_level"))
            if r.get("risk_level") not in (None, V.RISK_UNASSESSED) else "",
        )
        body = render(
            "report.html",
            title=esc("%s · %s" % (r["plugin_id"], r["domain"])),
            state_display=esc(V.STATE_DISPLAY.get(d.capability_state, d.capability_state)),
            state_plain=esc(V.STATE_PLAIN.get(d.capability_state, "")),
            flags="".join(
                '<span class="chip %s">%s</span>' % (cls, txt)
                for cls, txt in [
                    ("example", "示例（非正式成绩）") if r.get("_example") else ("", ""),
                    ("warn", "已过 90 天") if d.expired else ("", ""),
                    ("warn", "证据核验受限") if d.evidence_verification_limited else ("", ""),
                    ("muted", "不参加数字排名") if not d.rank_eligible else ("", ""),
                ]
                if txt
            ),
            safety=safety,
            cards=cards_html,
            human=human,
            numbers=numbers,
            cost=cost_block(r),
            four_things=four_html,
            security_scope=scope_sec,
            scope=scope,
            detail=detail,
        )
        name = "report-%s.html" % slug(r["report_id"])
        if name in written:
            raise BuildError("report_id %r 与先前报告的页面文件撞名（%s）：report_id 须唯一（dsh-bench#12③）" % (r["report_id"], name))
        (out_dir / name).write_text(page("%s · 报告 · 插件测评站" % esc(r["plugin_id"]), body, rbanner), encoding="utf-8")
        written.append(name)

    # 插件页
    for p in data["plugins"]:
        prs = [r for r in all_reports if r["plugin_id"] == p["plugin_id"]]
        # dsh-bench#10：测到哪/风险/现在推不推 从该插件报告的复算结果推导，不取插件 JSON 手填值
        depths = sorted({str(r.get("depth")) for r in prs}) or ["未测"]
        recs = sorted({r["_decision"].recommendation for r in prs}) or ["未测"]
        worst_risk = "未评估"
        for r in prs:
            rl = r.get("risk_level") or "未评估"
            if rl == "关键" or worst_risk == "关键":
                worst_risk = "关键" if rl == "关键" else worst_risk
        body = render(
            "plugin.html",
            name=esc(p["name"]),
            tags="".join(
                '<span class="chip">%s</span>' % esc(x)
                for x in [p["category"], "面向%s" % p["audience"], "测到%s" % "／".join(depths),
                          "风险%s" % worst_risk, "／".join(recs)]
            ),
            capabilities=(
                "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(c) for c in p.get("capabilities", []))
                if p.get("capabilities")
                else "<p>未测。</p>"
            ),
            install=(
                "<p>%s</p>" % esc(p.get("install_notes") or "未测（插件未登记安装说明）")
                + ("<p class=\"note\">安装环节：%s</p>" % esc("；".join(sorted({str(r.get("install_status")) for r in prs}))) if prs else "")
                + ("<p class=\"warn\">⚠ 安装失败记录存在，详见报告。</p>" if any(r.get("install_status") == "安装失败" for r in prs) else "")
            ),
            uninstall="<p>%s</p>" % esc(p.get("uninstall_notes") or "未登记"),
            maintainer=(
                "<ul>" + "".join(
                    "<li>%s：%s</li>" % (esc(k), esc(v))
                    for k, v in (p.get("maintainer") or {}).items() if v not in (None, "")
                ) + "</ul>" or "<p>未登记。</p>"
            ),
            update_log=(
                "<ul>" + "".join("<li>%s</li>" % esc(u) for u in (p.get("update_log") or [])) + "</ul>"
                if p.get("update_log") else "<p>暂无更新记录。</p>"
            ),
            price="<p>%s</p>" % esc(p["price_notes"]),
            caveats="<p>%s</p>" % esc(p["caveats"]),
            identity="<p>身份：%s / %s；可观测：%s</p>" % (
                esc(p["delivery_form"]), esc((p.get("identity") or {}).get("ref")),
                "是" if (p.get("identity") or {}).get("observable") else "否（无 commit 可观测）",
            ),
            reports="<ul class=\"cards\">%s</ul>" % ("".join(report_card(r) for r in prs) or "<li>还没有报告。</li>"),
        )
        name = "plugin-%s.html" % slug(p["plugin_id"])
        (out_dir / name).write_text(page("%s · 插件 · 插件测评站" % esc(p["name"]), body, banner), encoding="utf-8")
        written.append(name)

    # 各域榜
    groups = comparability_groups(all_reports)
    sections = []
    for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        domain, model, host, taskset, budget = key[:5]
        thash, shash, phash = key[5], key[6], key[7]
        sections.append("<h2>%s</h2>" % esc(domain))
        sections.append("<p class=\"meta\">可比组：模型 %s · 宿主 %s · 题集 %s · 预算 %s ｜ 题集哈希 <code>%s</code> · 判分器 <code>%s</code> · 协议 <code>%s</code></p>" % (
            esc(model), esc(host), esc(taskset), esc(budget), esc(str(thash)[:12]), esc(str(shash)[:12]), esc(str(phash)[:12])))
        sections.append(domain_table(rs))
    body = render("list.html", title="各域榜", intro=load_content("domains.md"), body="".join(sections) or "<p>还没有可比组。</p>")
    (out_dir / "domains.html").write_text(page("各域榜 · 插件测评站", body, banner), encoding="utf-8")
    written.append("domains.html")

    # 报告总览
    body = render("list.html", title="全部报告", intro="<p>按域分组，只有可比组内才排名。</p>",
                  body="<ul class=\"cards\">%s</ul>" % ("".join(report_card(r) for r in all_reports) or "<li>还没有报告。</li>"))
    (out_dir / "reports.html").write_text(page("全部报告 · 插件测评站", body, banner), encoding="utf-8")
    written.append("reports.html")

    # 值得试专页（dsh-bench#5：判定层的 worth_trying 必须落到页面）
    wt = [r for r in all_reports if r["_decision"].worth_trying]
    wt_body = render(
        "list.html",
        title="值得试",
        intro="<p>只列通过推荐资格的结果：进数字排名、无安全暂停、不在调查中。「帮倒忙」不推荐、不在此列。已失效（invalidation）与方法试验等非正式报告不进排名，自然不在此列。</p>",
        body='<ul class="cards">%s</ul>' % ("".join(report_card(r) for r in wt) or "<p>暂时没有值得试的结果。</p>"),
    )
    (out_dir / "worth_trying.html").write_text(page("值得试 · 插件测评站", wt_body, banner), encoding="utf-8")
    written.append("worth_trying.html")

    # 方法页
    (out_dir / "method.html").write_text(
        page("怎么测 · 插件测评站", render("list.html", title="怎么测", intro="", body=load_content("method.md")), banner),
        encoding="utf-8",
    )
    written.append("method.html")

    # 排期页
    sched_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            esc(p["name"]),
            esc("／".join(sorted({str(r.get("depth")) for r in prs_by_plugin.get(p["plugin_id"], [])})) or "未测"),
            esc("／".join(sorted({r["_decision"].recommendation for r in prs_by_plugin.get(p["plugin_id"], [])})) or "未测"),
        )
        for p in data["plugins"]
    ) or "<tr><td colspan=3>还没有登记插件。</td></tr>"
    sched = load_content("schedule.md") + "<table><thead><tr><th>插件</th><th>测到哪</th><th>现在推不推</th></tr></thead><tbody>%s</tbody></table>" % sched_rows
    (out_dir / "schedule.html").write_text(
        page("排期 · 插件测评站", render("list.html", title="排期", intro="", body=sched), banner), encoding="utf-8"
    )
    written.append("schedule.html")

    # 更正页
    corr_items = []
    for r in all_reports:
        for c in r.get("corrections", []) or []:
            corr_items.append("<li>%s：%s</li>" % (esc(r["report_id"]), esc(c)))
        inv = r.get("invalidation") or {}
        if inv.get("status") and inv["status"] not in ("有效", "无"):
            corr_items.append("<li>%s：%s（原因：%s）</li>" % (esc(r["report_id"]), esc(inv["status"]), esc(inv.get("reason"))))
    corr = load_content("corrections.md") + ("<ul>%s</ul>" % "".join(corr_items) if corr_items else "<p>还没有更正记录。</p>")
    (out_dir / "corrections.html").write_text(
        page("更正 · 插件测评站", render("list.html", title="找错和更正", intro="", body=corr), banner), encoding="utf-8"
    )
    written.append("corrections.html")

    # 账本页
    led_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (esc(l["month"]), esc(l["tested_count"]), esc(l["published_reports"]), esc(l["failed_count"]),
           esc(l["cost_total"]), esc(l["human_hours"]))
        for l in data["ledger"]
    ) or "<tr><td colspan=6>还没有账目。</td></tr>"
    # dsh-bench#22 N5：阻塞原因必须上页（空列表写「本月无阻塞」）
    blocked_rows = "".join(
        "<li>%s：%s</li>" % (esc(l["month"]), esc("；".join(l.get("blocked") or []) or "本月无阻塞"))
        for l in data["ledger"]
    ) or "<li>本月无阻塞。</li>"
    led = (
        "<table><thead><tr><th>月份</th><th>测了多少</th><th>发布报告</th><th>失败</th><th>花了多少</th><th>人时</th></tr></thead>"
        "<tbody>%s</tbody></table>"
        "<h3>阻塞与原因</h3><ul>%s</ul>" % (led_rows, blocked_rows)
    )
    (out_dir / "ledger.html").write_text(
        page("账本 · 插件测评站", render("list.html", title="月度账本", intro="", body=led), banner), encoding="utf-8"
    )
    written.append("ledger.html")

    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dsh-bench 插件测评站 · 静态站生成器")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--official-only", action="store_true", help="不包含 data/examples 的演示数据")
    ap.add_argument("--as-of", default=None, help="复算时钟（YYYY-MM-DD），缺省今天；写入 build-manifest.json（dsh-bench#8①）")
    args = ap.parse_args(argv)
    global _NOW
    _NOW = args.as_of or date.today().isoformat()

    # 发布闸（dsh-bench#3）：构建前先跑校验器，任一 FAIL 即中止。
    # 口径：语义复核（公式/门槛/证据链/状态/分数/时钟）**任何构建都跑**；
    # release 专属检查（只收「正式」、拒示例、插件测过深度）只在 --official-only 对 reports/ 施加。
    _VD = _load_validate_data()
    data_dir_path = Path(args.data_dir)
    gate_fail = 0
    for sub, release in (("reports", args.official_only), ("plugins", False), ("evidence", False), ("ledger", False)):
        for p in sorted((data_dir_path / sub).glob("*.json")):
            ok, errs = _VD.validate_file(p, V, release=release)
            if not ok:
                gate_fail += 1
                print("[发布闸] FAIL %s/%s" % (sub, p.name), file=sys.stderr)
                for e in errs:
                    print("        - %s" % e, file=sys.stderr)
    if gate_fail:
        print("构建中止（发布闸：%d 件未通过）" % gate_fail, file=sys.stderr)
        return 2

    try:
        data = load_data(data_dir_path, include_examples=not args.official_only)
    except BuildError as exc:
        print("构建中止（fail-closed）：%s" % exc, file=sys.stderr)
        return 2
    written = build(Path(args.out), data)
    # dsh-bench#8①：构建时钟入 manifest——产物里记录用的是哪一天
    (Path(args.out) / "build-manifest.json").write_text(
        json.dumps({"as_of": _NOW, "as_of_default": args.as_of is None}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("生成 %d 个页面到 %s" % (len(written), args.out))
    for w in written:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
