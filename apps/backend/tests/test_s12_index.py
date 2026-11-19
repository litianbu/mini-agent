"""索引入口（S12-3 的胶水层）的测试。

为什么这层也要测：
  它没有新逻辑，但它**串错了顺序**会静默出错 ——
  比如"灌完没 flush 就 count"，条数会少掉最后一批，
  看起来像数据丢了。这种错只有真的跑一遍才会暴露，
  所以值得用假 client 钉住"调用顺序"。

全部离线：embed_texts 被替换掉，client 是假的。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.rag import index as index_mod

DIM = 8


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "milvus_uri", "http://fake:19530")
    monkeypatch.setattr(settings, "milvus_token", "test-token")
    monkeypatch.setattr(settings, "milvus_db", "default")
    monkeypatch.setattr(settings, "milvus_collection", "test_docs")
    monkeypatch.setattr(settings, "embedding_dimension", DIM)


class FakeClient:
    def __init__(self, *, exists: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self._exists = exists

    def has_collection(self, name: str) -> bool:
        self.calls.append(("has_collection", {"name": name}))
        return self._exists

    def create_collection(self, **kwargs) -> None:
        self.calls.append(("create_collection", kwargs))

    def delete(self, **kwargs) -> None:
        self.calls.append(("delete", kwargs))

    def insert(self, **kwargs) -> None:
        self.calls.append(("insert", kwargs))

    def flush(self, name: str) -> None:
        self.calls.append(("flush", {"name": name}))

    def load_collection(self, name: str) -> None:
        self.calls.append(("load_collection", {"name": name}))

    def query(self, **kwargs) -> list:
        self.calls.append(("query", kwargs))
        return [{"count(*)": 0}]

    def search(self, **kwargs) -> list:
        self.calls.append(("search", kwargs))
        return [[]]

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture
def docs_dir(tmp_path):
    """两篇小文档，够切出多片。"""
    (tmp_path / "a.md").write_text("# 甲\n\n第一段内容。\n\n## 甲二\n\n第二段内容。\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# 乙\n\n只有一段。\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch):
    """替换掉真实 embedding 调用，并记录每次批量的大小。"""
    batches: list[int] = []

    async def _embed(texts: list[str]) -> list[list[float]]:
        batches.append(len(texts))
        return [[0.1] * DIM for _ in texts]

    monkeypatch.setattr(index_mod, "embed_texts", _embed)
    return batches


# ── 灌库流程 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_writes_every_doc(docs_dir, fake_embed) -> None:
    """每篇文档都要经过 切分 → 向量化 → 写入。"""
    client = FakeClient()
    docs, chunks = await index_mod.rebuild(client, docs_dir)

    assert docs == 2
    assert chunks > 0
    # 每篇一次 delete + 一次 insert
    assert client.names().count("delete") == 2
    assert client.names().count("insert") == 2


@pytest.mark.asyncio
async def test_rebuild_vetors_match_chunks(docs_dir, fake_embed) -> None:
    """向量条数必须等于片段条数 —— 不对齐会让片段配错向量。"""
    client = FakeClient()
    await index_mod.rebuild(client, docs_dir)
    for name, kwargs in client.calls:
        if name == "insert":
            rows = kwargs["data"]
            assert all(len(r["vector"]) == DIM for r in rows)
            assert all("content" in r for r in rows)


@pytest.mark.asyncio
async def test_rebuild_skips_empty_dir(tmp_path, fake_embed) -> None:
    """目录里没有 md 要明确报错，而不是静默"成功灌 0 条"。"""
    with pytest.raises(SystemExit):
        await index_mod.rebuild(FakeClient(), tmp_path)


@pytest.mark.asyncio
async def test_rebuild_creates_collection_when_missing(docs_dir, fake_embed) -> None:
    client = FakeClient(exists=False)
    index_mod.ensure_collection(client)
    assert "create_collection" in client.names()


def test_rebuild_flushes_before_counting(docs_dir, fake_embed, monkeypatch) -> None:
    """灌完必须先 flush 再 count。

    这条钉的是一个真实踩过的坑：不 flush 就数，会少掉最后一批
    （实测 228 条数成 212），然后打印一条"条数不一致"的假警报 ——
    看起来像数据丢了，其实只是还没可见。
    """
    client = FakeClient()
    monkeypatch.setattr(index_mod, "get_client", lambda: client)

    code = index_mod.main(["--rebuild", "--docs-dir", str(docs_dir)])

    assert code == 0
    names = client.names()
    assert "flush" in names, "灌完必须 flush"
    assert names.index("flush") < names.index("query"), "必须 flush 在前、count 在后"


# ── 命令行参数 ────────────────────────────────────────


def test_no_args_returns_help_code() -> None:
    """不带参数不能装作没事发生。"""
    assert index_mod.main([]) == 1


def test_count_path_flushes_nothing() -> None:
    """--count 是只读，不该触发写操作。"""
    client = FakeClient()
    index_mod.count(client)
    assert "insert" not in client.names()


def test_docs_dir_defaults_to_repo_docs() -> None:
    """默认目录必须指向仓库的 docs/，否则 --rebuild 会灌错地方。"""
    assert index_mod.DEFAULT_DOCS_DIR.name == "docs"
    assert (index_mod.DEFAULT_DOCS_DIR / "00-plan.md").exists()