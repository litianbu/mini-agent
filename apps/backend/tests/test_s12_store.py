"""存储层（S12-3）的测试。

全部用一个假的 client —— **一个请求都不打**。
理由：这些测试跑在共享的测试库上，真建集合会污染环境；
而且"我的代码对不对"和"Milvus 通不通"是两件事。
后者由一次性手工验证负责（见 docs/step-12）。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.rag.chunk import Chunk
from app.rag.store import (
    MAX_CONTENT_CHARS,
    Hit,
    VectorStoreError,
    build_index_params,
    build_schema,
    count,
    ensure_collection,
    flush,
    save_source,
    search,
)

DIM = 8


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "milvus_uri", "http://fake:19530")
    monkeypatch.setattr(settings, "milvus_token", "test-token")
    monkeypatch.setattr(settings, "milvus_db", "default")
    monkeypatch.setattr(settings, "milvus_collection", "test_docs")
    monkeypatch.setattr(settings, "embedding_dimension", DIM)


class FakeClient:
    """记录所有调用的假 MilvusClient。

    `calls` 是 (方法名, 参数) 的序列 —— 顺序也要断言，
    因为"先删后插"和"先插后删"结果完全不同。
    """

    def __init__(self, *, exists: bool = False, count_rows: int = 0, hits: list | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._exists = exists
        self._count_rows = count_rows
        self._hits = hits or []

    # —— 被 store.py 用到的方法 ——
    def has_collection(self, name: str) -> bool:
        self.calls.append(("has_collection", {"name": name}))
        return self._exists

    def create_collection(self, **kwargs) -> None:
        self.calls.append(("create_collection", kwargs))

    def load_collection(self, name: str) -> None:
        self.calls.append(("load_collection", {"name": name}))

    def delete(self, **kwargs) -> None:
        self.calls.append(("delete", kwargs))

    def insert(self, **kwargs) -> None:
        self.calls.append(("insert", kwargs))

    def flush(self, name: str) -> None:
        self.calls.append(("flush", {"name": name}))

    def query(self, **kwargs) -> list:
        self.calls.append(("query", kwargs))
        return [{"count(*)": self._count_rows}]

    def search(self, **kwargs) -> list:
        self.calls.append(("search", kwargs))
        return [self._hits]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def make_chunks(n: int = 2) -> list[Chunk]:
    return [
        Chunk(source="a.md", heading=f"标题{i}", content=f"内容{i}", index=i)
        for i in range(n)
    ]


def make_vectors(n: int = 2) -> list[list[float]]:
    return [[float(i)] * DIM for i in range(n)]


# ── schema / index ────────────────────────────────────


def test_schema_has_all_fields() -> None:
    """字段齐不齐，决定了能不能过滤和展示。"""
    schema = build_schema(DIM)
    names = [f.name for f in schema.fields]
    assert names == ["id", "vector", "source", "heading", "content", "chunk_index", "is_deleted"]


def test_schema_vector_dimension() -> None:
    """维度必须来自配置 —— 写死会让换模型变成改代码。"""
    schema = build_schema(1024)
    vector_field = next(f for f in schema.fields if f.name == "vector")
    assert vector_field.params["dim"] == 1024


def test_schema_rejects_bad_dimension() -> None:
    with pytest.raises(VectorStoreError, match="维度不合法"):
        build_schema(0)


def test_index_uses_cosine() -> None:
    """度量方式必须和 embedding 的输出匹配（已归一化 → 余弦）。

    这条错了不会报错，只会让排序慢慢变得不准 —— 所以值得钉住。
    """
    params = build_index_params()
    entry = list(params)[0].to_dict()
    assert entry["field_name"] == "vector"
    assert entry["index_type"] == "AUTOINDEX"
    assert entry["metric_type"] == "COSINE"


# ── 建集合 ────────────────────────────────────────────


def test_ensure_collection_skips_when_exists() -> None:
    """已经存在就不许再建 —— 重复建会报错，drop 重建会丢数据。"""
    client = FakeClient(exists=True)
    created = ensure_collection(client)
    assert created is False
    assert "create_collection" not in client.names()


def test_ensure_collection_creates_when_missing() -> None:
    client = FakeClient(exists=False)
    created = ensure_collection(client)
    assert created is True
    create_call = dict(client.calls)["create_collection"]
    assert create_call["collection_name"] == "test_docs"
    # 必须带上显式 schema 和索引参数
    assert create_call["schema"] is not None
    assert create_call["index_params"] is not None


def test_missing_collection_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """没定集合名时要喊停，而不是连上去之后报 collection not found。"""
    monkeypatch.setattr(settings, "milvus_collection", "")
    with pytest.raises(VectorStoreError, match="集合名"):
        ensure_collection(FakeClient())


# ── 写入 ──────────────────────────────────────────────


def test_save_deletes_before_insert() -> None:
    """先删后插 —— 顺序错了就会变成两份数据。"""
    client = FakeClient()
    n = save_source(client, "a.md", make_chunks(2), make_vectors(2))
    assert n == 2
    assert client.names() == ["delete", "insert"], "必须先 delete 再 insert"


def test_save_inserts_all_fields() -> None:
    client = FakeClient()
    save_source(client, "a.md", make_chunks(2), make_vectors(2))
    rows = dict(client.calls)["insert"]["data"]
    assert len(rows) == 2
    assert rows[0]["source"] == "a.md"
    assert rows[0]["chunk_index"] == 0
    assert rows[0]["is_deleted"] is False
    assert len(rows[0]["vector"]) == DIM


def test_save_escapes_quotes_in_source() -> None:
    """文件名里的引号必须转义，否则过滤表达式语法错误（甚至可被注入）。"""
    client = FakeClient()
    save_source(client, 'weird"name.md', make_chunks(1), make_vectors(1))
    expr = dict(client.calls)["delete"]["filter"]
    assert expr == 'source == "weird\\"name.md"'


def test_save_rejects_oversized_content() -> None:
    """超长会让整批 insert 失败，所以要在发之前拦住。"""
    client = FakeClient()
    big = Chunk(source="a.md", heading="h", content="字" * (MAX_CONTENT_CHARS + 1), index=0)
    with pytest.raises(VectorStoreError, match="太长"):
        save_source(client, "a.md", [big], make_vectors(1))
    assert client.calls == [], "长度不合要求时不该发出任何请求"


def test_save_rejects_mismatched_counts() -> None:
    with pytest.raises(VectorStoreError, match="不一致"):
        save_source(FakeClient(), "a.md", make_chunks(3), make_vectors(2))


def test_save_empty_does_nothing() -> None:
    client = FakeClient()
    assert save_source(client, "a.md", [], []) == 0
    assert client.calls == []


# ── 读 ────────────────────────────────────────────────


def test_flush_targets_our_collection() -> None:
    """写入之后要能立刻读到，所以 flush 是必要的。"""
    client = FakeClient()
    flush(client)
    assert dict(client.calls)["flush"]["name"] == "test_docs"


def test_count_excludes_deleted_by_default() -> None:
    client = FakeClient(count_rows=7)
    assert count(client) == 7
    assert dict(client.calls)["query"]["filter"] == "is_deleted == false"


def test_count_includes_deleted_when_asked() -> None:
    client = FakeClient(count_rows=9)
    count(client, include_deleted=True)
    assert dict(client.calls)["query"]["filter"] == ""


def test_count_handles_empty_result() -> None:
    class Empty(FakeClient):
        def query(self, **kwargs):  # type: ignore[override]
            self.calls.append(("query", kwargs))
            return []

    assert count(Empty()) == 0


def test_search_filters_deleted() -> None:
    client = FakeClient()
    search(client, [0.0] * DIM, top_k=3)
    call = dict(client.calls)["search"]
    assert call["filter"] == "is_deleted == false"
    assert call["limit"] == 3
    assert call["search_params"]["metric_type"] == "COSINE"


def test_search_loads_before_searching() -> None:
    """检索前必须先 load，否则会报 collection not loaded。"""
    client = FakeClient()
    search(client, [0.0] * DIM)
    names = client.names()
    assert "load_collection" in names
    assert names.index("load_collection") < names.index("search")


def test_search_with_source_filter() -> None:
    client = FakeClient()
    search(client, [0.0] * DIM, source="a.md")
    expr = dict(client.calls)["search"]["filter"]
    assert expr == 'is_deleted == false and source == "a.md"'


def test_search_maps_hits() -> None:
    raw = [
        {
            "id": 1,
            "distance": 0.87,
            "entity": {
                "source": "step-07.md",
                "heading": "上下文裁剪",
                "content": "正文",
                "chunk_index": 3,
            },
        }
    ]
    hits = search(FakeClient(hits=raw), [0.0] * DIM)
    assert len(hits) == 1
    assert isinstance(hits[0], Hit)
    assert hits[0].score == pytest.approx(0.87)
    assert hits[0].source == "step-07.md"
    assert hits[0].chunk_index == 3


def test_search_on_empty_collection_returns_empty_list() -> None:
    """空集合时 Milvus 返回 [[]]，不能炸。"""
    assert search(FakeClient(hits=[]), [0.0] * DIM) == []


def test_search_rejects_bad_top_k() -> None:
    with pytest.raises(VectorStoreError, match="top_k"):
        search(FakeClient(), [0.0] * DIM, top_k=0)