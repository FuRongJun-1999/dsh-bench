#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定点变异自证执行器（守卫的守卫）

风格（本仓惯例）：**锚点自检 → 源码级短路 → 恰好命中期望红项，多红少红都算错。**

做法：
  1. 把仓内除 `.git` / `dist` 外的一切复制到临时目录；
  2. 对每个变异：先在目标文件里**自检锚点存在且出现次数相符**（不符即该变异判「锚点失配」）；
  3. 做源码级精确替换（等价于把规则短路）；
  4. 在临时副本里跑对应守卫（`--json` 取失败清单）；
  5. 比对失败集合与 `expect`：**完全相等**才算通过；多红、少红、零红都算错；
  6. 删除临时副本。

只读工作区：本脚本永不修改仓内源文件（所有改动只发生在临时副本里）。

跑法：python -X utf8 tests/mutate_selfcheck.py [--only V01|--json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "dist", "__pycache__"}


def _load_mutations(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.MUTATIONS


def copy_repo(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(ROOT.iterdir()):
        if p.name in SKIP:
            continue
        target = dst / p.name
        if p.is_dir():
            shutil.copytree(p, target, ignore=shutil.ignore_patterns(*SKIP))
        else:
            shutil.copy2(p, target)


def purge_pycache(root: Path) -> None:
    """清掉字节码缓存：同尺寸同 mtime 的旧 pyc 会让变异后的源码不被重新加载，
    造成「上一轮变异的读数」被误当成这一轮的读数（本仓踩过的坑）。"""
    for p in root.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def run_guard(repo: Path, test_file: str) -> list:
    """返回守卫失败项 id 列表。"""
    purge_pycache(repo)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(repo / test_file), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(repo),
        env=env,
    )
    out = (proc.stdout or "").strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.startswith("{"):
            data = json.loads(line)
            return [f["id"] for f in data.get("failures", [])]
    return ["<守卫无法解析输出: rc=%s stderr=%s>" % (proc.returncode, (proc.stderr or "")[:200])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="定点变异自证")
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    mutations = _load_mutations("mut_verdict", "tests/test_verdict.py") + _load_mutations(
        "mut_validate", "tests/test_validate_data.py"
    )
    if args.only:
        mutations = [m for m in mutations if m["id"] == args.only]
        if not mutations:
            print("没有编号为 %s 的变异" % args.only)
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="dshbench_mut_"))
    repo = tmp / "repo"
    results = []
    try:
        copy_repo(repo)
        for m in mutations:
            target = repo / m["target"]
            text = target.read_text(encoding="utf-8")
            occurrences = text.count(m["anchor"])
            if occurrences != m["count"]:
                results.append(
                    {
                        "id": m["id"],
                        "status": "ANCHOR_MISMATCH",
                        "detail": "锚点自检失败：出现 %d 次，期望 %d 次" % (occurrences, m["count"]),
                        "expect": m["expect"],
                        "rule": m["rule"],
                    }
                )
                continue
            target.write_text(text.replace(m["anchor"], m["replace"]), encoding="utf-8")
            failed = run_guard(repo, m["test_file"])
            target.write_text(text, encoding="utf-8")   # 还原副本

            if sorted(failed) == sorted(m["expect"]):
                status = "OK"
                detail = "恰好命中 %d 项" % len(failed)
            elif not failed:
                status = "NO_RED"
                detail = "注入后守卫全绿（规则没被守住）"
            else:
                status = "MULTI_RED"
                extra = sorted(set(failed) - set(m["expect"]))
                missing = sorted(set(m["expect"]) - set(failed))
                detail = "多红 %s / 少红 %s" % (extra, missing)
            results.append(
                {"id": m["id"], "status": status, "detail": detail,
                 "expect": m["expect"], "failed": failed, "rule": m["rule"]}
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(r["status"] == "OK" for r in results) else 1

    ok = 0
    for r in results:
        mark = {"OK": "OK    ", "NO_RED": "NO_RED", "MULTI_RED": "MULTI ", "ANCHOR_MISMATCH": "ANCHOR"}[r["status"]]
        print("%s %-6s %-34s %s" % (mark, r["id"], r["rule"], r["detail"]))
        if r["status"] != "OK":
            print("        - 期望红项：%s" % ", ".join(r["expect"]))
            print("        - 实得红项：%s" % ", ".join(r.get("failed", [])))
        else:
            ok += 1
    print("-" * 72)
    print("变异自证：共 %d 条，恰好命中 %d 条，异常 %d 条" % (len(results), ok, len(results) - ok))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
