"""bash 工具：让模型能跑命令（装依赖、跑测试、看 git 状态）。

这是四个工具里最危险的一个 —— 等于给了模型一个 shell。
三条限制让它可控：
    1. 工作目录固定在仓库根，不能随便乱跑
    2. 有超时，卡死的命令不会把进程拖死
    3. 输出截断，避免一棵目录树把上下文撑爆
"""

from __future__ import annotations

import asyncio
import shlex

from ...core.config import REPO_ROOT
from .base import Tool

TIMEOUT = 30
MAX_OUTPUT = 4000

# 明确禁止的命令：模型不需要，但一旦执行就很难挽回
FORBIDDEN = ("rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", ":(){", "chmod -R 777 /")


async def run_bash(args: dict[str, object]) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "错误：command 为空"

    for bad in FORBIDDEN:
        if bad in command:
            return f"拒绝执行：包含危险片段 {bad!r}"

    timeout = min(int(args.get("timeout", TIMEOUT)), 120)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # 合并，模型不用看两遍
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"超时（{timeout}s）已终止：{command}"

    except Exception as exc:
        return f"执行失败：{exc}"

    text = out.decode("utf-8", errors="replace").strip()
    if not text:
        return f"（无输出，退出码 {proc.returncode}）"

    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n…… 输出过长已截断（共 {len(text)} 字符）"

    return f"退出码 {proc.returncode}\n{text}"


RUN_BASH = Tool(
    name="bash",
    description="在仓库根目录执行 shell 命令，用于跑测试、装依赖、看目录结构。不要用来读文件（用 read_file）。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令，如 pnpm test"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
        },
        "required": ["command"],
    },
    run=run_bash,
)
