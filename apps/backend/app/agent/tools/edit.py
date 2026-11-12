"""edit 工具：精确替换一段文本。

为什么必须有它：
    write_file 要求模型把整个文件内容重新输出一遍。
    改 1 行却要重发 198 行 —— 又慢又贵，而且模型很可能
    "顺手"改了别的地方，或者输出被截断导致文件后半段丢失。

    edit 只发改动的那几行，风险小得多。

但 edit 有一类特殊风险：**改错地方**。
    所以下面用三重检查挡住它（找不到 / 找到多处 / 空串）。
"""

from __future__ import annotations

from typing import Any

from .base import Tool
from .fs import _safe_path


async def run_edit(args: dict[str, Any]) -> str:
    path = _safe_path(str(args.get("path", "")))
    old = str(args.get("old_text", ""))
    new = str(args.get("new_text", ""))

    # ① 空串：str.replace("") 会在每个字符之间插入，等于毁掉整个文件
    if old == "":
        return "错误：old_text 不能为空（空串会匹配到所有位置）"

    if not path.is_file():
        return f"错误：文件不存在 {args.get('path')}"

    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)

    # ② 找不到：多半是模型记的内容已经过时了，让它重新读一遍
    if count == 0:
        return (
            f"错误：在 {args.get('path')} 里没找到这段文本。\n"
            "文件可能已被改过，请先用 read_file 看当前内容，再提供更精确的 old_text。"
        )

    # ③ 找到多处：不知道改哪个，直接拒绝。
    #    这一个检查挡掉了绝大多数"改错地方"的事故。
    if count > 1:
        return (
            f"错误：old_text 在 {args.get('path')} 里出现了 {count} 次，无法确定改哪一处。\n"
            "请把 old_text 扩大几行（带上前后文）使它唯一，或改用 write_file 整文件覆盖。"
        )

    # 记录改动发生在第几行，方便模型和用户确认
    lineno = text[: text.index(old)].count("\n") + 1

    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    old_lines = old.count("\n") + 1
    new_lines = new.count("\n") + 1
    return (
        f"✓ 已修改 {args.get('path')}（第 {lineno} 行，{old_lines} 行 → {new_lines} 行）"
    )


EDIT = Tool(
    name="edit",
    description=(
        "把文件里的某段文本替换成新文本。改已有文件优先用这个，不要用 write_file 整文件覆盖。"
        "old_text 必须与文件内容完全一致（含缩进和空行）且只出现一次，否则会拒绝执行。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对仓库根的路径"},
            "old_text": {"type": "string", "description": "要被替换的原文，需完全一致"},
            "new_text": {"type": "string", "description": "替换成的新内容"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    run=run_edit,
)
