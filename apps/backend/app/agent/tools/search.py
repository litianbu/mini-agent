"""grep 工具：按内容搜代码。

为什么用纯 Python 而不是 `bash: grep -rn`：
    模型给的 pattern 要拼进命令行，就必须转义。转义一旦漏了，
    一个 `;` 或 `$(...)` 就能执行任意命令 —— 这是一条注入通道。
    纯 Python 用 re.compile，pattern 只能当正则用，跑不出命令。

代价是自己实现遍历，但这点代码换来"不可能注入"，很值。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core.config import REPO_ROOT
from .base import Tool
from .fs import _safe_path

# 这些目录永远不搜：依赖和缓存里有几万个文件，搜了也没意义
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache"}
MAX_RESULTS = 50
MAX_LINE = 200


def _iter_files(base: Path, include: str) -> list[Path]:
    """要搜哪些文件。base 是文件就直接搜它，是目录就递归。"""
    if base.is_file():
        return [base]

    pattern = include or "*"
    files = [
        p
        for p in base.rglob(pattern)
        if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
    ]
    return sorted(files)


def _rel(path: Path) -> str:
    """绝对路径太吵，输出相对仓库根的路径。"""
    try:
        return str(path.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _is_binary(path: Path) -> bool:
    """靠"前 1KB 有没有 NUL 字节"判断二进制。

    这是 git 的做法，比看扩展名可靠 —— 有人把 .py 写成 UTF-16 就会被识别出来。
    """
    try:
        return b"\0" in path.read_bytes()[:1024]
    except OSError:
        return True


async def run_grep(args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return "错误：pattern 不能为空"

    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"错误：正则不合法（{exc}）"

    base = _safe_path(str(args.get("path", ".")))
    if not base.exists():
        return f"错误：路径不存在 {args.get('path')}"

    include = str(args.get("include", ""))
    limit = min(int(args.get("max_results", MAX_RESULTS)), 200)

    hits: list[str] = []
    scanned = 0
    for path in _iter_files(base, include):
        if _is_binary(path):
            continue
        scanned += 1

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, 1):
            if rx.search(line):
                # 行太长就截断：一行 5000 字符的压缩 js 会撑爆上下文
                shown = line.strip()[:MAX_LINE]
                rel = _rel(path)
                hits.append(f"{rel}:{lineno}: {shown}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            hits.append(f"…… 已达上限 {limit} 条，缩小范围或加 include 再搜")
            break

    if not hits:
        return f"没有匹配 {pattern!r}（扫描了 {scanned} 个文件）"
    return f"{len(hits)} 条匹配（扫描 {scanned} 个文件）：\n" + "\n".join(hits)


GREP = Tool(
    name="grep",
    description=(
        "按内容搜索代码，返回 文件:行号: 内容。"
        "想知道某个函数/变量在哪定义、被谁引用，用它而不是 glob 之后反复 read_file。"
        "pattern 是正则；include 可限定文件类型如 *.py。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，如 ChatSession 或 def\\s+chat"},
            "path": {"type": "string", "description": "搜索起点，相对仓库根，默认整个仓库"},
            "include": {"type": "string", "description": "只搜这些文件，如 *.py 或 *.ts"},
            "max_results": {"type": "integer", "description": "最多返回多少条，默认 50"},
        },
        "required": ["pattern"],
    },
    run=run_grep,
)
