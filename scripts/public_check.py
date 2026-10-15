#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开面检查：提交前扫描本仓全部文本，确保可以安全公开。

本站是 GitHub PUBLIC 仓。提交正文不得含：
  1. 本机绝对路径（盘符、家目录、临时目录变量）
  2. 凭据形态字面量（令牌前缀、密钥赋值）
  3. 内部代号（只在本组织内部有意义的词）
  4. 内部文件路径（私有清单、临时目录）

被禁词面本身不能写进本文件（本文件也在公开面内），因此规则里的敏感字面量
一律用**码点拼装**（见 `_w`），源码里只留数字。

用法：
    python -X utf8 scripts/public_check.py            # 扫描工作区
    python -X utf8 scripts/public_check.py --json
退出码 0 表示干净，1 表示有命中。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".git", "dist", "__pycache__", ".venv", "node_modules"}


def _w(*codepoints) -> str:
    """按码点拼词：运行时得到正确词面，公开源码里只有数字。"""
    return "".join(chr(c) for c in codepoints)


# 内部代号（ASCII），逐字符码点
_CODE_NAMES = [
    _w(0x7A, 0x63, 0x6F, 0x64, 0x65),                    # 某客户端代号
    _w(0x61, 0x65, 0x69, 0x73),                          # 某私域库代号
    _w(0x6D, 0x64, 0x5F, 0x63, 0x67),                    # 某模块名
    _w(0x68, 0x69, 0x76, 0x65),                          # 某调度组件名
    _w(0x63, 0x6F, 0x67, 0x67, 0x72, 0x61, 0x70, 0x68),  # 某图谱组件名
    _w(0x6C, 0x69, 0x6E, 0x67, 0x73, 0x68, 0x75),        # 某项目名
    _w(0x64, 0x73, 0x68, 0x2D, 0x6D, 0x65, 0x6D, 0x6F, 0x72, 0x79),  # 某仓库名
    _w(0x64, 0x73, 0x68, 0x5F, 0x6D, 0x65, 0x6D, 0x6F, 0x72, 0x79),
]

# 内部代号（中文），同样逐字码点
_WORD_NAMES = [
    _w(0x8702, 0x5DE2),
    _w(0x8702, 0x7FA4),
    _w(0x8BA4, 0x77E5, 0x56FE),
    _w(0x7075, 0x67A2),
    _w(0x5927, 0x8113, 0x4ED3, 0x5E93),
    _w(0x8EAB, 0x4F53, 0x4ED3, 0x5E93),
]

# 敏感路径字面量（同样用码点拼装）
_HOME_A = re.escape(_w(0x43, 0x3A, 0x5C, 0x5C, 0x55, 0x73, 0x65, 0x72, 0x73, 0x5C, 0x5C))
_HOME_B = re.escape(_w(0x43, 0x3A, 0x2F, 0x55, 0x73, 0x65, 0x72, 0x73, 0x2F))
_TEMP_VARS = "|".join(re.escape(_w(*cp)) for cp in (
    (0x25, 0x54, 0x45, 0x4D, 0x50, 0x25),   # 环境变量式临时目录
    (0x25, 0x54, 0x4D, 0x50, 0x25),
))
_TMP_DIR = re.escape(_w(0x2E, 0x74, 0x6D, 0x70)) + r"[/\\]"
_PLANS_DIR = re.escape(_w(0x64, 0x6F, 0x63, 0x73, 0x2F, 0x70, 0x6C, 0x61, 0x6E, 0x73))

# 每类：名称 -> (说明, 正则)
# 注意：abs_path 排除转义序列写法（源码里的 `a:\n`、`b:\t` 是换行/制表符，不是路径）。
PATTERNS = [
    ("abs_path", "本机绝对路径（盘符）", re.compile(r"\b[A-Za-z]:[\\/](?![nrt\"'\\/ ])")),
    ("home_dir", "家目录写法", re.compile(r"(?i)(%s|/home/[a-z0-9_.-]+/|%s)" % (_HOME_A, _HOME_B))),
    ("temp_var", "临时目录变量", re.compile(_TEMP_VARS)),
    ("credential", "凭据形态字面量", re.compile(r"(?i)(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|"
                                                r"api[_-]?key\s*[:=]\s*[\"'][^\"']|password\s*[:=]\s*[\"'][^\"']|"
                                                r"secret\s*[:=]\s*[\"'][^\"'])")),
    ("internal_tmp", "内部私有路径", re.compile("%s|%s" % (_TMP_DIR, _PLANS_DIR))),
    ("internal_code", "内部代号（ASCII）",
     re.compile(r"(?i)(?<![0-9A-Za-z_])(%s)(?![0-9A-Za-z_])" % "|".join(_CODE_NAMES))),
    ("internal_word", "内部代号（中文）", re.compile("|".join(_WORD_NAMES))),
    ("session_id", "会话标识形态", re.compile(r"sess_[0-9a-f]{8,}")),
]


_CRED_EXT = re.compile(
    "(?i)("
    + _w(0x73, 0x6B, 0x2D, 0x70, 0x72, 0x6F, 0x6A, 0x2D) + "[A-Za-z0-9]{10,}"   # 某云密钥前缀（proj 形态）
    + "|" + _w(0x67, 0x69, 0x74, 0x68, 0x75, 0x62, 0x5F, 0x70, 0x61, 0x74, 0x5F) + "[A-Za-z0-9_]{20,}"  # 某平台细粒度令牌
    + "|" + _w(0x78, 0x6F, 0x78, 0x62, 0x2D) + "[A-Za-z0-9]{10,}"   # 某聊天平台令牌
    + "|" + _w(0x42, 0x65, 0x61, 0x72, 0x65, 0x72) + "\\s+[A-Za-z0-9._-]{15,}"  # Bearer 头
    + "|" + _w(0x2D, 0x2D, 0x2D, 0x2D, 0x2D) + "[A-Z ]{0,20}" + _w(0x50, 0x52, 0x49, 0x56, 0x41, 0x54, 0x45) + "[ A-Z]*" + _w(0x4B, 0x45, 0x59)   # PEM 私钥头
    + "|\"(?:api[_-]?key|password|secret|token)\"\\s*:\\s*\"[^\"]{4,}\""  # JSON 键值形态
    + ")"
)


def scan_text(text: str) -> list:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for key, desc, pat in PATTERNS:
            for m in pat.finditer(line):
                hits.append({"kind": key, "desc": desc, "line": lineno, "match": m.group(0)})
        # dsh-bench#17 H4：凭据形态扩展（JSON 键值 / proj 前缀 / 细粒度令牌 / Bearer / PEM）
        for m in _CRED_EXT.finditer(line):
            hits.append({"kind": "credential_ext", "desc": "凭据形态（扩展集）", "line": lineno, "match": m.group(0)})
    return hits


def iter_files(root: Path):
    # dsh-bench#21 M4：跳过目录按**相对 root 的组件**匹配——仓库放在任何名下都照扫；
    # root 本身叫 dist/ 也不例外（生成好的站点同样要扫）。
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            rel_parts = p.parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="公开面检查")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        # dsh-bench#21 M4：root 不存在不得报「干净」
        print("公开面检查：root 不存在：%s" % root, file=sys.stderr)
        return 2
    report = {}
    total = 0
    for p in iter_files(root):
        text = None
        for enc in ("utf-8", "utf-16"):
            try:
                text = p.read_text(encoding=enc)
                break
            except (UnicodeDecodeError, OSError):
                continue
        if text is None:
            # dsh-bench#19 J4：读不了的文件不得静默跳过
            report.setdefault("<unreadable>" + str(p), [])
            report["<unreadable>" + str(p)] = [{"kind": "unreadable", "desc": "文件无法解码，未扫描", "line": 0, "match": ""}]
            total += 1
            continue
        hits = scan_text(text)
        if hits:
            rel = str(p.relative_to(root)).replace("\\", "/")
            report[rel] = hits
            total += len(hits)

    if args.json:
        print(json.dumps({"total": total, "files": report}, ensure_ascii=False, indent=2))
        return 1 if total else 0

    if not report:
        print("公开面检查：干净（0 命中）")
        return 0
    for rel, hits in report.items():
        print("HIT   %s" % rel)
        for h in hits:
            print("        - [%s] %s 行 %d：%s" % (h["kind"], h["desc"], h["line"], h["match"]))
    print("-" * 60)
    print("公开面检查：共 %d 处命中，须改后重扫" % total)
    return 1


if __name__ == "__main__":
    sys.exit(main())
