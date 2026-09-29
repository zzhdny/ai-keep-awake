# -*- coding: utf-8 -*-
"""发布前密钥泄漏扫描器：只读扫描，输出打码，不做任何修改。"""
import os
import re

ROOT = r"D:\glm"
PATTERNS = [
    ("sk风格密钥", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("32位以上hex(疑似密钥)", re.compile(r"\b[a-f0-9]{32,}\b", re.I)),
    ("api_key赋值", re.compile(r"(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-.]{12,}", re.I)),
    ("token/secret/password赋值", re.compile(r"\b(token|secret|passwd|password|pwd)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}", re.I)),
    ("Bearer令牌", re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{16,}")),
]
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "gh-cli", "venv", ".venv", "pdfs"}
EXTS = {".py", ".js", ".ts", ".json", ".ps1", ".bat", ".cmd", ".md", ".txt",
        ".yml", ".yaml", ".html", ".env", ".cfg", ".ini", ".toml", ".sh"}
MAXSIZE = 2_000_000

files_scanned = 0
findings = {}  # (file, pattern) -> [lines]
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if os.path.splitext(fn)[1].lower() not in EXTS:
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

print("扫描文件数: %d  (根目录 %s)" % (files_scanned, ROOT))
print("跳过目录: %s" % ", ".join(sorted(SKIP_DIRS)))
print()
if not findings:
    print("未发现疑似密钥/令牌。")
else:
    print("发现疑似敏感内容（值已打码，仅显示位置和类型）：")
    cur = None
    for (p, name), lines in sorted(findings.items()):
        rel = os.path.relpath(p, ROOT)
        print("  [%s] %s  第 %s 行" % (name, rel, ",".join(map(str, lines[:8]))))
