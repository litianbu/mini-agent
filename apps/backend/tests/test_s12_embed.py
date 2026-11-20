"""向量化（S12-2）的测试。

全部用 httpx.MockTransport —— 单测不打网络。
理由：真打接口的测试又慢又不稳定（限流、网关维护），
而且它测的是"网关能不能用"，不是"我的代码对不对"。

真实接口的连通性由一次性的手工验证负责（见 docs/step-11）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.rag.embed import EmbeddingError, embed_query, embed_texts

DIM = 4  # 测试里用小维度，省得造 1024 个浮点数


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试都用假配置，绝不碰真实的 .env。"""
    monkeypatch.setattr(settings, "embedding_api_key", "sk-test-fake")
    monkeypatch.setattr(settings, "embedding_api_base", "https://fake.local/v1")
    monkeypatch.setattr(settings, "embedding_model", "fake-model")
    monkeypatch.setattr(settings, "embedding_dimension", DIM)
    monkeypatch.setattr(settings, "embedding_batch_size", 64)


def make_client(handler, calls: list | None = None) -> httpx.AsyncClient:
    """造一个拦截所有请求的 client。calls 用来断言发了几次。"""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(json.loads(request.content))
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))


def ok_response(texts: list[str], *, dim: int = DIM, shuffle: bool = False) -> httpx.Response:
    """造一个正常的响应。shuffle=True 用来验证顺序错乱时能否纠正。"""
    items = [
        {"index": i, "object": "embedding", "embedding": [float(i)] * dim}
        for i in range(len(texts))
    ]
    if shuffle:
        items = list(reversed(items))
    return httpx.Response(200, json={"model": "fake-model", "data": items})


async def test_one_vector_per_text() -> None:
    """传几条就回几个向量，顺序一一对应。"""
    client = make_client(lambda r: ok_response(["a", "b", "c"]))
    vecs = await embed_texts(["a", "b", "c"], client=client)
    assert len(vecs) == 3
    assert [v[0] for v in vecs] == [0.0, 1.0, 2.0]


async def test_out_of_order_response_is_sorted() -> None:
    """接口乱序返回时，必须按 index 排回去。

    这条测的是一个**不报错的 bug**：并行编码时响应很容易乱序，
    不排序的话片段 A 会拿到片段 B 的向量 —— 检索全错，但什么都不崩。
    """
    client = make_client(lambda r: ok_response(["a", "b", "c"], shuffle=True))
    vecs = await embed_texts(["a", "b", "c"], client=client)
    assert [v[0] for v in vecs] == [0.0, 1.0, 2.0]


async def test_batching_splits_requests() -> None:
    """5 条文本、每批 2 条 → 应该发 3 次请求。"""
    calls: list = []
    client = make_client(lambda r: ok_response(json.loads(r.content)["input"]), calls)
    vecs = await embed_texts([f"t{i}" for i in range(5)], batch_size=2, client=client)
    assert len(vecs) == 5
    assert len(calls) == 3
    assert [len(c["input"]) for c in calls] == [2, 2, 1]


async def test_dimension_mismatch_raises() -> None:
    """维度不符要立刻报错 —— 这是为灌库准备的前置校验。"""
    client = make_client(lambda r: ok_response(["a"], dim=8))  # 期望 4，给 8
    with pytest.raises(EmbeddingError, match="维度不符"):
        await embed_texts(["a"], client=client)


async def test_empty_text_rejected_before_calling() -> None:
    """空文本必须在发请求之前挡住，而且一次请求都不该发出去。"""
    calls: list = []
    client = make_client(lambda r: ok_response([""]), calls)
    with pytest.raises(EmbeddingError, match="空的"):
        await embed_texts(["正常", "   "], client=client)
    assert calls == [], "空文本应该在本地就被拦下，不该发请求"


async def test_missing_key_raises_before_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 key 时给一句人话，而不是让网关回 401。"""
    monkeypatch.setattr(settings, "embedding_api_key", "")
    with pytest.raises(EmbeddingError, match="EMBEDDING_API_KEY"):
        await embed_texts(["a"])


async def test_retries_on_server_error() -> None:
    """5xx 要重试，第二次成功就算成功。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, text="boom")
        return ok_response(json.loads(request.content)["input"])

    client = make_client(handler)
    vecs = await embed_texts(["a"], retries=2, client=client)
    assert len(vecs) == 1
    assert state["n"] == 2


async def test_no_retry_on_client_error() -> None:
    """401/400 这类错误重试没用，只该试一次。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(401, text="token_not_found_in_db")

    client = make_client(handler)
    with pytest.raises(EmbeddingError):
        await embed_texts(["a"], retries=2, client=client)
    assert state["n"] == 1, "4xx 不该重试"


async def test_embed_query_returns_single_vector() -> None:
    """检索用的问题也要走同一条路。"""
    client = make_client(lambda r: ok_response(["q"]))
    vec = await embed_query("怎么报销", client=client)
    assert isinstance(vec, list)
    assert len(vec) == DIM


async def test_empty_input_list_is_fine() -> None:
    """空列表不算错误，返回空列表。"""
    client = make_client(lambda r: ok_response([]))
    assert await embed_texts([], client=client) == []


async def test_verbose_prints_batch_progress(capsys) -> None:
    """verbose 时逐批打印：能看出分了几批、每批多少条、各花多久。

    灌库时"卡在哪一批"和"某批特别慢（限流退避）"都要靠这几行看出来。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(json.loads(request.content)["input"])

    client = make_client(handler)
    await embed_texts(["a", "b", "c"], batch_size=2, client=client, verbose=True)
    out = capsys.readouterr().out
    assert "批次 1/2" in out
    assert "批次 2/2" in out
    assert "共 3 个向量" in out
