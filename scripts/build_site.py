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


def render(template_name: str, **kw) -> str:
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    for key, value in kw.items():
        text = text.replace("{{%s}}" % key, "" if value is None else str(value))
    return text


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


def check_report(report: dict) -> dict:
    """复算：状态、半宽、成本单价全部从区间/证据算出；不一致即中止。"""
    hits = find_forbidden(report)
    if hits:
        raise BuildError("%s: 出现禁止的手填分数字段 %s" % (report.get("_source"), hits))

    iv = report.get("interval")
    interval = (float(iv["L"]), float(iv["U"])) if iv else None
    sec = report.get("security") or {}
    ev = report.get("evidence") or {}
    d = V.decide(
        interval,
        a=float(report["a"]),
        b=float(report["b"]),
        max_half_width=float(report["max_half_width"]),
        install_failed=report.get("install_status") == "安装失败",
        confirmed_severe_security_event=sec.get("status") == "已确认事件",
        security_investigation=sec.get("status") == "调查中",
        tested_at=report.get("tested_at"),
        now=date.today().isoformat(),
        original_deleted=bool(ev.get("original_deleted")),
        redacted_evidence_sufficient=bool(ev.get("redacted_evidence_sufficient", True)),
        depth=report.get("depth"),
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


def load_data(data_dir: Path, include_examples: bool) -> dict:
    reports = [check_report(r) for r in load_json_dir(data_dir / "reports")]
    examples = []
    if include_examples:
        for p in sorted((data_dir / "examples").glob("report-*.json")):
            r = json.loads(p.read_text(encoding="utf-8"))
            r["_source"] = _rel(p)
            r["_example"] = True
            examples.append(check_report(r))
    return {
        "plugins": load_json_dir(data_dir / "plugins"),
        "reports": reports,
        "examples": examples,
        "ledger": load_json_dir(data_dir / "ledger"),
        "evidence": load_json_dir(data_dir / "evidence"),
    }


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
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>[%s, %s]</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(r["plugin_id"]),
                esc(V.STATE_DISPLAY.get(d.capability_state, d.capability_state)),
                esc(show(iv.get("L"))),
                esc(show(iv.get("U"))),
                esc(show(r.get("b0_score"))),
                esc(show(r.get("p_score"))),
            )
        )
    html_ranked = (
        "<table><thead><tr><th>插件</th><th>结论</th><th>区间</th><th>不装</th><th>装了</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else "<p>本域暂时没有进入数字排名的结果。</p>"
    )
    html_unranked = ""
    if unranked:
        html_unranked = "<h3>不参加数字排名（照常展示）</h3><ul>" + "".join(
            "<li>%s：%s（%s）</li>"
            % (esc(r["plugin_id"]), esc(r["_decision"].capability_state), esc(r["_source"]))
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
        key = (r["domain"], c.get("model"), c.get("host"), c.get("taskset"), c.get("budget_policy"))
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
            ("domains.html", "哪些值得试", "只列进了排名和推荐徽章的结果。"),
            ("reports.html", "哪些有坑", "安全提醒、已确认事件和更正记录。"),
            ("method.html", "怎么测的", "方法、题目、判分程序和局限。"),
        ]
    )
    coverage = "".join(
        "<li>%s：%s</li>"
        % (
            esc(p["name"]),
            esc("已测（%s）" % p["tested_depth"] if p.get("tested_depth") not in (None, "未测") else "未测"),
        )
        for p in data["plugins"]
    ) or "<li>还没有登记插件。未测就写「未测」，不留空。</li>"
    latest = "".join(report_card(r) for r in all_reports) or "<li>还没有报告。</li>"
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
        if sec.get("status") not in (None, "未测", "未发现问题"):
            safety = (
                '<div class="safety"><strong>安全提醒（单独显示）</strong><p>状态：%s</p>'
                "<p>能力结果保留，不改零分、不抹掉。</p></div>"
            ) % esc(sec.get("status"))
        human = "<dl>" + "".join(
            "<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(v)) for k, v in (r.get("human_text") or {}).items()
        ) + "</dl>"
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
            esc(show(r.get("half_width"))), esc(r["max_half_width"]),
            esc(show(r.get("scenario_count"))), esc(show(r.get("rerun_count"))),
            esc(r["install_status"]), "是" if d.rank_eligible else "否", esc(d.recommendation),
        )
        notes = "".join("<li>%s</li>" % esc(n) for n in d.notes)
        scope = "<p>%s</p><ul>%s</ul>" % (esc((r.get("human_text") or {}).get("查了什么没查什么", "")), notes)
        detail = (
            "<ul><li>证据：%s</li><li>实验：%s / 分析：%s</li>"
            "<li>协议哈希：<code>%s</code></li><li>判分器哈希：<code>%s</code></li>"
            "<li>环境：%s</li><li>复核：%s（独立：%s）</li><li>利益关系：%s</li></ul>"
        ) % (
            esc((r.get("evidence") or {}).get("evidence_id")),
            esc(r["experiment_id"]), esc(r["analysis_id"]),
            esc(r["protocol_hash"]), esc(r["scorer_hash"]),
            esc(r["environment_id"]),
            esc((r.get("review") or {}).get("independent_reviewer")),
            "是" if (r.get("review") or {}).get("independent") else "否",
            esc(r.get("disclosure")),
        )
        body = render(
            "report.html",
            title="%s · %s" % (r["plugin_id"], r["domain"]),
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
            human=human,
            numbers=numbers,
            cost=cost_block(r),
            scope=scope,
            detail=detail,
        )
        name = "report-%s.html" % slug(r["report_id"])
        (out_dir / name).write_text(page("%s · 报告 · 插件测评站" % r["plugin_id"], body, rbanner), encoding="utf-8")
        written.append(name)

    # 插件页
    for p in data["plugins"]:
        prs = [r for r in all_reports if r["plugin_id"] == p["plugin_id"]]
        body = render(
            "plugin.html",
            name=esc(p["name"]),
            tags="".join(
                '<span class="chip">%s</span>' % esc(x)
                for x in [p["category"], "面向%s" % p["audience"], "测到%s" % p["tested_depth"],
                          "风险%s" % p["risk_level"], p["recommendation"]]
            ),
            capabilities=(
                "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(c) for c in p.get("capabilities", []))
                if p.get("capabilities")
                else "<p>未测。</p>"
            ),
            install="<p>%s</p>" % esc(p["uninstall_notes"]),
            price="<p>%s</p>" % esc(p["price_notes"]),
            caveats="<p>%s</p>" % esc(p["caveats"]),
            identity="<p>身份：%s / %s；可观测：%s</p>" % (
                esc(p["delivery_form"]), esc((p.get("identity") or {}).get("ref")),
                "是" if (p.get("identity") or {}).get("observable") else "否（无 commit 可观测）",
            ),
            reports="<ul class=\"cards\">%s</ul>" % ("".join(report_card(r) for r in prs) or "<li>还没有报告。</li>"),
        )
        name = "plugin-%s.html" % slug(p["plugin_id"])
        (out_dir / name).write_text(page("%s · 插件 · 插件测评站" % p["name"], body, banner), encoding="utf-8")
        written.append(name)

    # 各域榜
    groups = comparability_groups(all_reports)
    sections = []
    for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        domain, model, host, taskset, budget = key
        sections.append("<h2>%s</h2>" % esc(domain))
        sections.append("<p class=\"meta\">可比组：模型 %s · 宿主 %s · 题集 %s · 预算 %s</p>" % (
            esc(model), esc(host), esc(taskset), esc(budget)))
        sections.append(domain_table(rs))
    body = render("list.html", title="各域榜", intro=load_content("domains.md"), body="".join(sections) or "<p>还没有可比组。</p>")
    (out_dir / "domains.html").write_text(page("各域榜 · 插件测评站", body, banner), encoding="utf-8")
    written.append("domains.html")

    # 报告总览
    body = render("list.html", title="全部报告", intro="<p>按域分组，只有可比组内才排名。</p>",
                  body="<ul class=\"cards\">%s</ul>" % ("".join(report_card(r) for r in all_reports) or "<li>还没有报告。</li>"))
    (out_dir / "reports.html").write_text(page("全部报告 · 插件测评站", body, banner), encoding="utf-8")
    written.append("reports.html")

    # 方法页
    (out_dir / "method.html").write_text(
        page("怎么测 · 插件测评站", render("list.html", title="怎么测", intro="", body=load_content("method.md")), banner),
        encoding="utf-8",
    )
    written.append("method.html")

    # 排期页
    sched_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (esc(p["name"]), esc(p.get("tested_depth", "未测")), esc(p.get("recommendation", "待复核")))
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
    led = (
        "<table><thead><tr><th>月份</th><th>测了多少</th><th>发布报告</th><th>失败</th><th>花了多少</th><th>人时</th></tr></thead>"
        "<tbody>%s</tbody></table>" % led_rows
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
    args = ap.parse_args(argv)

    try:
        data = load_data(Path(args.data_dir), include_examples=not args.official_only)
    except BuildError as exc:
        print("构建中止（fail-closed）：%s" % exc, file=sys.stderr)
        return 2
    written = build(Path(args.out), data)
    print("生成 %d 个页面到 %s" % (len(written), args.out))
    for w in written:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
