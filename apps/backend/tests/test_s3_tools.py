"""S3 的测试：工具循环。

三件必须钉住的事：
    1. 碎片拼接（流式最易错的地方，纯逻辑，不依赖网络）
    2. 工具真的安全（路径逃逸必须被拦）
    3. 事件类型对得上（前端靠这个渲染）
"""

from __future__ import annotations

import json

from app.agent.delta import ToolCallDelta
from app.agent.events import ToolCallEvent, ToolResultEvent
from app.agent.loop import _Accumulator
from app.agent.tools import execute, schemas


def test_fragments_are_concatenated() -> None:
    """参数分三帧到达，必须拼成合法 JSON。

    这是 S3 的头号坑：如果实现成"后一帧覆盖前一帧"，
    拼出来的就是 {"} 这种残废 JSON，而且只在真模型上才暴露。
    """
    acc = _Accumulator()
    acc.feed([ToolCallDelta(index=0, id="call_1", name="read_file", arguments='{"path":')])
    acc.feed([ToolCallDelta(index=0, arguments='"app/main.py"')])
    acc.feed([ToolCallDelta(index=0, arguments="}")])

    calls = acc.result()
    assert len(calls) == 1
    assert calls[0]["name"] == "read_file"
    assert calls[0]["id"] == "call_1"
    assert json.loads(calls[0]["arguments"]) == {"path": "app/main.py"}


def test_multiple_tool_calls_are_separated() -> None:
    """模型同时调两个工具时，按 index 分开，不能串味。"""
    acc = _Accumulator()
    acc.feed(
        [
            ToolCallDelta(index=0, id="c1", name="read_file", arguments='{"path":"a.py"}'),
            ToolCallDelta(index=1, id="c2", name="glob", arguments='{"pattern":"*.py"}'),
        ]
    )

    calls = acc.result()
    assert [c["id"] for c in calls] == ["c1", "c2"]
    assert [c["name"] for c in calls] == ["read_file", "glob"]


def test_empty_id_does_not_overwrite() -> None:
    """后续帧的 id 是空字符串，不能把第一帧的 id 冲掉。"""
    acc = _Accumulator()
    acc.feed([ToolCallDelta(index=0, id="call_1", name="bash", arguments="")])
    acc.feed([ToolCallDelta(index=0, arguments='{"command":"ls"}')])

    assert acc.result()[0]["id"] == "call_1"


async def test_path_escape_is_blocked() -> None:
    """../.. 必须被拦住 —— 模型可能因为幻觉去读系统文件。"""
    out = await execute("read_file", '{"path":"../../etc/passwd"}')
    assert "路径超出工作区" in out


async def test_unknown_tool_and_bad_json_are_reported() -> None:
    """模型记错工具名、拼坏 JSON 都是常态 —— 要返回错误文本而不是抛异常。"""
    assert "没有名为" in await execute("no_such_tool", "{}")
    assert "不是合法 JSON" in await execute("read_file", "{broken")


def test_schemas_include_core_tools() -> None:
    """S3 的四个工具必须在 schema 里（S4 之后工具变多，用包含关系断言）。"""
    names = {s["function"]["name"] for s in schemas()}
    assert {"read_file", "write_file", "glob", "bash"} <= names



def test_tool_events_serialize() -> None:
    """新增的两种事件必须能序列化成 SSE 帧（前端靠 type 字段分发）。"""
    call = ToolCallEvent(id="c1", name="bash", arguments='{"command":"ls"}')
    assert json.loads(call.model_dump_json())["type"] == "tool_call"

    result = ToolResultEvent(id="c1", name="bash", ok=True, output="ok")
    payload = json.loads(result.model_dump_json())
    assert payload["type"] == "tool_result"
    assert payload["ok"] is True


def test_system_prompt_mentions_workspace_root() -> None:
    """提示词必须写清工作区根目录 —— 否则模型会猜错路径。

    实测：没有这句时，模型把 app/agent/tools 当成仓库根下的路径，
    用 find 满仓库找了三轮；有了它，第一轮就命中。
    """
    from app.agent.prompt import system_prompt
    from app.core.config import REPO_ROOT

    text = system_prompt()
    assert str(REPO_ROOT) in text
    # 四个工具的名字都要出现，否则模型不知道有什么可用
    for name in ("glob", "read_file", "write_file", "bash"):
        assert name in text
