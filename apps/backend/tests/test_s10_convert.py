"""S10-2：MCP 工具定义 → 模型认识的形状。

这一步看起来只是"改几个字段名"，其实有两个真问题：

  1. **名字撞车**：MCP 服务器也可能提供叫 `bash` 的工具，
     直接混进本地清单会静默覆盖，非常难查。
  2. **服务器的数据不一定靠谱**：list_tools 可能漏字段。
     一条坏数据不该让整批工具都发不出去。
"""

from __future__ import annotations

from app.agent.mcp.convert import (
    PREFIX,
    parse_name,
    qualified_name,
    to_openai_schema,
)

WEATHER = {
    "name": "get_weather",
    "description": "查询城市天气",
    "inputSchema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


# ── 名字：命名空间 ──────────────────────────────────────


def test_qualified_name_has_namespace() -> None:
    assert qualified_name("weather", "get_weather") == "mcp__weather__get_weather"


def test_name_detects_collision_with_local_tools() -> None:
    """本地有个 bash，MCP 也可能有个 bash —— 加前缀就不会被覆盖。"""
    mcp_bash = qualified_name("hacker", "bash")
    assert mcp_bash != "bash"


def test_parse_roundtrip() -> None:
    name = qualified_name("weather", "get_weather")
    assert parse_name(name) == ("weather", "get_weather")


def test_parse_rejects_local_tool_names() -> None:
    """本地工具名不能被当成 MCP。"""
    assert parse_name("bash") is None
    assert parse_name("read_file") is None


def test_parse_handles_tool_name_with_underscores() -> None:
    """工具名本身可能含下划线，拆的时候不能多切。

    `mcp__weather__get_daily_forecast` 拆成
    ("weather", "get_daily_forecast")，不是三截。
    """
    assert parse_name("mcp__weather__get_daily_forecast") == (
        "weather",
        "get_daily_forecast",
    )


def test_parse_rejects_malformed() -> None:
    assert parse_name("mcp__serveronly") is None    # 只有服务器，没有工具
    assert parse_name("mcp____x") is None           # 服务器名为空


# ── 转换：字段映射 ──────────────────────────────────────


def test_converts_input_schema_to_parameters() -> None:
    """这是两个生态最大的差异：inputSchema vs parameters。"""
    schema = to_openai_schema("weather", WEATHER)
    assert schema is not None

    fn = schema["function"]
    assert schema["type"] == "function"
    assert fn["name"] == "mcp__weather__get_weather"
    assert fn["description"] == "查询城市天气"
    assert "parameters" in fn
    assert "inputSchema" not in fn          # 不能把 MCP 的字段名漏出去
    assert fn["parameters"]["required"] == ["city"]


def test_missing_schema_gets_empty_object() -> None:
    """无参数的工具也必须有个合法的 parameters，否则请求会被上游拒。"""
    schema = to_openai_schema("t", {"name": "ping"})
    assert schema is not None
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}


def test_missing_description_gets_fallback() -> None:
    """描述缺失时给一句兜底，否则模型完全不知道这工具干什么。"""
    schema = to_openai_schema("t", {"name": "x"})
    assert schema is not None
    assert schema["function"]["description"]


def test_bad_tool_is_skipped_not_crashing() -> None:
    """坏数据返回 None，由调用方跳过 —— 不能让一整批工具废掉。"""
    assert to_openai_schema("t", {}) is None                    # 没有名字
    assert to_openai_schema("t", {"name": ""}) is None          # 空名字
    assert to_openai_schema("t", {"name": 123}) is None         # 名字不是字符串
    # 名字有、schema 是垃圾：仍要产出一个可用的定义
    assert to_openai_schema("t", {"name": "ok", "inputSchema": "junk"}) is not None


def test_does_not_mutate_input() -> None:
    """转换不能改原对象 —— 那个 dict 是服务器返回的，还要给别人用。"""
    before = repr(WEATHER)
    to_openai_schema("weather", WEATHER)
    assert repr(WEATHER) == before
