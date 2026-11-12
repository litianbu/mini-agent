"""S4 的测试：grep 和 edit。

这两个工具都有"改错比不改更糟"的性质，所以测试重点是**拒绝执行**的分支：
找不到、多处匹配、空串、路径越界 —— 拦住了才算合格。
"""

from __future__ import annotations

import json

import pytest

from app.agent.tools import execute, schemas
from app.core.config import REPO_ROOT

DEMO = "tmp_s4_demo.txt"


@pytest.fixture
def demo_file():
    """在仓库里建一个临时文件（走正常路径校验），用完删掉。"""
    f = REPO_ROOT / DEMO
    f.write_text("alpha\nbeta\ngamma\nbeta\n", encoding="utf-8")
    yield DEMO
    f.unlink(missing_ok=True)


def test_six_tools_registered() -> None:
    names = {s["function"]["name"] for s in schemas()}
    assert names == {"read_file", "write_file", "edit", "glob", "grep", "bash"}


# ── grep ────────────────────────────────────────────────


async def test_grep_finds_content_with_line_numbers() -> None:
    """结果要带 文件:行号，模型才能接着去读那一行。"""
    out = await execute("grep", json.dumps({"pattern": "class ChatSession", "include": "*.py"}))
    assert "models/__init__.py:" in out
    assert "class ChatSession" in out


async def test_grep_returns_relative_paths() -> None:
    """不能返回绝对路径 —— 又长又没用，还会让模型误以为是别的机器。"""
    out = await execute("grep", json.dumps({"pattern": "MAX_ROUNDS", "include": "*.py"}))
    assert str(REPO_ROOT) not in out
    assert "apps/backend/app/agent/loop.py:" in out


async def test_grep_reports_no_match() -> None:
    # 注意：不能把要搜的字面量写在测试文件里 —— grep 会把测试文件自己搜出来。
    # 所以拼一个运行时才存在的字符串。
    needle = "zz" + "q_never_used_9f3a"
    out = await execute("grep", json.dumps({"pattern": needle}))
    assert "没有匹配" in out


async def test_grep_rejects_bad_regex() -> None:
    """模型可能给出非法正则（比如只写了半个括号），要报错而不是崩。"""
    out = await execute("grep", json.dumps({"pattern": "([unclosed"}))
    assert "正则不合法" in out


# ── edit：正常路径 ───────────────────────────────────────


async def test_edit_replaces_unique_text(demo_file: str) -> None:
    out = await execute(
        "edit", json.dumps({"path": demo_file, "old_text": "alpha", "new_text": "ALPHA"})
    )
    assert "已修改" in out
    assert "第 1 行" in out
    assert (REPO_ROOT / demo_file).read_text().startswith("ALPHA")


# ── edit：必须拒绝的四种情况 ─────────────────────────────


async def test_edit_refuses_missing_text(demo_file: str) -> None:
    """找不到原文时不能静默成功 —— 模型会以为改完了。"""
    out = await execute(
        "edit", json.dumps({"path": demo_file, "old_text": "delta", "new_text": "x"})
    )
    assert "没找到" in out
    # 文件必须一个字都没变
    assert (REPO_ROOT / demo_file).read_text() == "alpha\nbeta\ngamma\nbeta\n"


async def test_edit_refuses_ambiguous_match(demo_file: str) -> None:
    """old_text 出现两次时必须拒绝。

    这是 edit 最重要的防线：随便挑一个改，等于随机破坏文件。
    """
    out = await execute(
        "edit", json.dumps({"path": demo_file, "old_text": "beta", "new_text": "BETA"})
    )
    assert "出现了 2 次" in out
    assert (REPO_ROOT / demo_file).read_text() == "alpha\nbeta\ngamma\nbeta\n"


async def test_edit_refuses_empty_old_text(demo_file: str) -> None:
    """空串会在每个字符间插入，等于毁掉文件。"""
    out = await execute(
        "edit", json.dumps({"path": demo_file, "old_text": "", "new_text": "x"})
    )
    assert "不能为空" in out


async def test_edit_blocks_path_escape() -> None:
    out = await execute(
        "edit", json.dumps({"path": "../../etc/hosts", "old_text": "a", "new_text": "b"})
    )
    assert "路径超出工作区" in out
