#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布前密钥泄漏扫描器（只读、输出打码、不联网）。

用法:
    py -3.12 secret_scan.py <项目目录>

退出码:
    0 = 未发现疑似密钥
    1 = 有疑似发现（需人工逐条判定真伪后再发布）
    2 = 用法/参数错误
"""
import os
import re
import sys

PATTERNS = [
    ("sk风格密钥", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("32位以上hex(疑似密钥/哈希)", re.compile(r"\b[a-f0-9]{32,}\b", re.I)),
    ("api_key赋值", re.compile(r"(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-.]{12,}", re.I)),
    ("token/secret/password赋值", re.compile(r"[A-Za-z0-9_-]*(token|secret|passwd|password|pwd)[\"']?\s*[:=]\s*[\"']?[^\s\"']{8,}", re.I)),
    ("Bearer令牌", re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{16,}")),
]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".zcode",
             ".agents", "dist", "build", "gh-cli", "pdfs"}
EXTS = {".py", ".js", ".ts", ".cjs", ".mjs", ".json", ".ps1", ".bat", ".cmd",
        ".md", ".txt", ".yml", ".yaml", ".html", ".env", ".cfg", ".ini",
        ".toml", ".sh", ".vbs"}
MAXSIZE = 2_000_000


def main(argv):
    root = os.path.abspath(argv[1] if len(argv) > 1 else os.getcwd())
    if not os.path.isdir(root):
        print("目录不存在: %s" % root)
        return 2

    files_scanned = 0
    findings = {}  # (file, pattern名) -> [行号]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if not ext and fn.startswith("."):  # .env 这类点文件
                ext = fn.lower()
            if ext not in EXTS:
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > MAXSIZE:
                    continue
                with open(p, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            files_scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                for name, pat in PATTERNS:
                    if pat.search(line):
                        findings.setdefault((p, name), []).append(i)

    print("扫描目录: %s" % root)
    print("扫描文件数: %d" % files_scanned)
    if not findings:
        print("结果: 未发现疑似密钥/令牌。")
        return 0
    print("结果: 发现疑似敏感内容（值已打码，仅显示位置和类型）：")
    for (p, name), lines in sorted(findings.items()):
        print("  [%s] %s  第 %s 行" % (name, os.path.relpath(p, root),
                                       ",".join(map(str, lines[:8]))))
    print("注意：环境变量读取(如 os.environ.get)、sha256 等内容哈希、文档占位符"
          "属于常见误报，需人工逐条判定。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
