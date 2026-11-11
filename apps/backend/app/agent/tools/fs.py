"""文件类工具：读、写、找。

安全边界：所有路径都必须落在仓库内。
    模型是"外部输入"，它可能因为幻觉写出 ../../etc/passwd。
    这不是恶意，但一样会把你的机器搞坏 —— 所以路径必须校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.config import REPO_ROOT
from .base import Tool

# 单次读取上限。模型上下文有限，塞进去 10000 行只会把真正的信息挤掉。
MAX_READ_LINES = 400
MAX_OUTPUT_CHARS = 8000


def _safe_path(raw: str) -> Path:
    """把模型给的字符串变成一个"确定在仓库内"的绝对路径。

    resolve() 会展开 .. 和软链接，所以 ../.. 这种逃逸在这里就被拦下。
    相对路径按仓库根算 —— 模型说 "app/main.py" 指的是仓库里的那个。
    """
    candidate = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    root = REPO_ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"路径超出工作区: {raw}")
    return candidate


async def read_file(args: dict[str, Any]) -> str:
    path = _safe_path(args["path"])
    if not path.is_file():
        return f"错误：文件不存在 {args['path']}"

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)

    # 支持分页：读大文件时模型可以分批拉
    offset = max(1, int(args.get("offset", 1)))
    limit = min(int(args.get("limit", MAX_READ_LINES)), MAX_READ_LINES)
    chunk = lines[offset - 1 : offset - 1 + limit]

    # 带行号，模型才能准确说"改第几行"
    body = "\n".join(f"{offset + i:5d}  {line}" for i, line in enumerate(chunk))
    header = f"{args['path']}（共 {total} 行，第 {offset}-{offset + len(chunk) - 1} 行）\n"
    return header + body


async def write_file(args: dict[str, Any]) -> str:
    path = _safe_path(args["path"])
    content = str(args.get("content", ""))

    # 目录不存在就建 —— 模型经常直接写 src/new/thing.py
    path.parent.mkdir(parents=True, exist_ok=True)

    existed = path.is_file()
    path.write_text(content, encoding="utf-8")
    action = "覆盖" if existed else "创建"
    return f"已{action} {args['path']}（{len(content.splitlines())} 行，{len(content)} 字符）"


async def glob_search(args: dict[str, Any]) -> str:
    """按 glob 模式找文件，比如 **/*.py。

    为什么不用 bash 的 find —— 因为 glob 更快、更安全，
    而且结果是排序稳定的，模型读起来一致。
    """
    pattern = args["pattern"]
    root = REPO_ROOT.resolve()
    hits = [
        str(p.relative_to(root))
        for p in root.glob(pattern)
        # 排除依赖和缓存目录，否则 *.py 会返回几千个第三方文件
        if p.is_file() and not any(part in {".venv", "node_modules", "__pycache__", "dist"} for part in p.parts)
    ]
    hits.sort()

    if not hits:
        return f"没有匹配 {pattern} 的文件"
    shown = hits[:100]
    more = f"\n…… 还有 {len(hits) - len(shown)} 个" if len(hits) > len(shown) else ""
    return f"{len(hits)} 个匹配：\n" + "\n".join(shown) + more


READ_FILE = Tool(
    name="read_file",
    description="读取仓库内的文件，返回带行号的内容。大文件可用 offset/limit 分批读。改动文件前应先读它。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对仓库根的路径，如 app/main.py"},
            "offset": {"type": "integer", "description": "起始行号，默认 1"},
            "limit": {"type": "integer", "description": "最多读多少行，默认 400"},
        },
        "required": ["path"],
    },
    run=read_file,
)

WRITE_FILE = Tool(
    name="write_file",
    description="把内容整体写入仓库内的文件（会覆盖原内容，目录会自动创建）。已有文件必须先 read_file 看清原内容。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对仓库根的路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    },
    run=write_file,
)

GLOB_SEARCH = Tool(
    name="glob",
    description="按 glob 模式查找仓库内的文件，如 **/*.py、app/**/*.ts。想找某个文件在哪用它。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，支持 ** 递归"},
        },
        "required": ["pattern"],
    },
    run=glob_search,
)
