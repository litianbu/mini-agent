"""存储层：把向量放进 Milvus。

这一步只做三件事：**建集合、写数据、查数据**。
不负责切分（chunk.py），也不负责算向量（embed.py）。

为什么需要它：
  向量算出来在内存里，进程一退就没了。存进库之后，
  "找和这个问题最像的 5 段"才能随时、快速地做。

为什么是 Milvus 而不是 MySQL：
  MySQL 能存数组，但回答不了"给我最像的 5 条"——
  它没有向量索引，只能一行行算余弦。228 条还行，20 万条就废了。
  Milvus 的索引能把这件事从"几百毫秒"压到"几毫秒"。

和官方 demo 的关系：
  demo 用 `create_collection(name, dimension=384)` 一行建表，
  那只建了 id + vector 两个字段（其余字段靠动态字段塞进 JSON）。
  我们这里用**显式 schema**，因为 source / heading / content
  要参与过滤和展示 —— 显式字段才能建索引、才能高效过滤。
  这也是真实项目的做法（你们工作项目就是单集合 + is_deleted 软删除）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pymilvus import DataType, MilvusClient
from pymilvus.orm.schema import CollectionSchema
from pymilvus.milvus_client.index import IndexParams

from ..core.config import settings
from .chunk import Chunk

__all__ = [
    "Hit",
    "VectorStoreError",
    "build_index_params",
    "build_schema",
    "count",
    "ensure_collection",
    "ensure_loaded",
    "flush",
    "get_client",
    "save_source",
    "search",
]

# 字段长度上限。为什么留这么大余量：
#   Milvus 的 VARCHAR 超长会让**整批 insert 失败**，
#   而不是只丢那一条。所以宁可建宽一点（建完改不了），
#   也不要刚好卡在 600。
MAX_SOURCE_CHARS = 512
MAX_HEADING_CHARS = 1024
MAX_CONTENT_CHARS = 4096

VECTOR_FIELD = "vector"
METRIC_TYPE = "COSINE"


class VectorStoreError(RuntimeError):
    """存储层失败。和 EmbeddingError 分开，便于调用方区分是谁的问题。"""


@dataclass(frozen=True)
class Hit:
    """一条检索结果。"""

    score: float
    source: str
    heading: str
    content: str
    chunk_index: int

    def __str__(self) -> str:  # 方便命令行里一眼看清
        return f"{self.score:.4f}  {self.source} #{self.chunk_index}  {self.heading[:40]}"


def _collection_name() -> str:
    """集合名必须显式配置。

    不提供默认值是真的刻意：集合一旦建出来，改名等于重建 + 重新灌数据。
    与其替你猜一个名字，不如在这里喊停。
    """
    name = settings.milvus_collection
    if not name:
        raise VectorStoreError(
            "还没有配置集合名。\n"
            "在仓库根目录的 .env 里加一行：\n"
            "  MILVUS_COLLECTION=你想用的名字\n"
            "（建议带前缀，避免和共享环境里别人的集合撞名）"
        )
    return name


def build_schema(dimension: int) -> CollectionSchema:
    """建集合的结构。纯本地构造，不连服务器 —— 所以可以直接拿来测。

    字段分成两类，用途完全不同：

      向量字段 vector   —— 只有它能参与"相似"查询
      标量字段 source / heading / content —— 用来过滤和展示

    为什么 is_deleted 而不是直接删：
      软删除可以撤销、可以审计，也不会让"第 3 篇文档的第 5 段"
      这个位置信息凭空消失。真实项目几乎都这么做。
    """
    if dimension <= 0:
        raise VectorStoreError(f"维度不合法：{dimension}")

    schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field(VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=dimension)
    schema.add_field("source", DataType.VARCHAR, max_length=MAX_SOURCE_CHARS)
    schema.add_field("heading", DataType.VARCHAR, max_length=MAX_HEADING_CHARS)
    schema.add_field("content", DataType.VARCHAR, max_length=MAX_CONTENT_CHARS)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("is_deleted", DataType.BOOL, default_value=False)
    return schema


def build_index_params() -> IndexParams:
    """向量的索引参数。

    AUTOINDEX 让 Milvus 自己选索引类型 —— 不用先搞懂 HNSW / IVF_FLAT
    的区别，也不会因为参数选错而白灌一遍数据。

    COSINE 而不是 L2 的原因：
      bge-m3 出来的向量已经是单位长度（模长 1），
      此时余弦和点积等价，而 L2 距离会随维度放大、数值不好解释。
      **重要**：度量方式在建索引时就定死了，灌完数据再改必须重建索引。
    """
    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(
        field_name=VECTOR_FIELD,
        index_type="AUTOINDEX",
        metric_type=METRIC_TYPE,
    )
    return index_params


def get_client() -> MilvusClient:
    """连库。

    连服务端只需要 pymilvus —— 不需要 milvus-lite，也不需要 faiss。
    （本地文件模式才需要那两个，而它们对系统版本有要求。）
    """
    if not settings.milvus_uri:
        raise VectorStoreError("没有配置 MILVUS_URI")
    if not settings.milvus_token:
        raise VectorStoreError("没有配置 MILVUS_TOKEN（形如 用户名:密码）")
    return MilvusClient(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        db_name=settings.milvus_db or "default",
        timeout=settings.milvus_timeout,
    )


def ensure_collection(client: MilvusClient) -> bool:
    """集合不存在才建。返回"这次是否真的建了"。

    为什么不无脑 create：
      重复建会报错；而"每次都先 drop 再建"更危险 —— 手滑一次数据全没。
      所以这里只做"没有才建"，重复调用是安全的。
    """
    name = _collection_name()
    if client.has_collection(name):
        return False
    client.create_collection(
        collection_name=name,
        schema=build_schema(settings.embedding_dimension),
        index_params=build_index_params(),
    )
    return True


def ensure_loaded(client: MilvusClient) -> None:
    """确保集合已加载进内存。

    Milvus 的检索必须先 load。共享环境里集合可能因为内存压力被释放，
    这时 search 会报 "collection not loaded" —— 那个错误看起来像
    "数据没了"，实际上数据还在磁盘上，只是没加载。

    load 是幂等的：已经加载过再调一次不会报错。
    """
    client.load_collection(_collection_name())


def _quote(value: str) -> str:
    """把字符串安全地放进过滤表达式。

    为什么不能直接拼引号：
      文件名里带一个 `"` 就会让整个表达式语法错误；
      更糟的是可以构造出额外的条件（表达式注入）。
      json.dumps 会正确转义引号和反斜杠。
    """
    return json.dumps(value, ensure_ascii=False)


def _check_lengths(source: str, chunks: list[Chunk]) -> None:
    """超长会拖垮整批 insert，所以在发之前查。

    Milvus 的行为是**整批失败**，不是跳过坏的那一条 ——
    228 条里有一条超长，前面 227 条也一起白写。
    """
    if len(source) > MAX_SOURCE_CHARS:
        raise VectorStoreError(f"source 太长（{len(source)} > {MAX_SOURCE_CHARS}）：{source[:40]}")
    for c in chunks:
        if len(c.heading) > MAX_HEADING_CHARS:
            raise VectorStoreError(f"{source} 的 heading 太长：{len(c.heading)} 字符")
        if len(c.content) > MAX_CONTENT_CHARS:
            raise VectorStoreError(
                f"{source} 的第 {c.index} 段太长：{len(c.content)} > {MAX_CONTENT_CHARS} 字符"
            )


def save_source(
    client: MilvusClient,
    source: str,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> int:
    """写入一篇文档的全部片段。返回写入条数。

    **先删后插**，这是幂等的关键：

      主键是 auto_id，每次插入都会生成新的 id，
      所以"再灌一次"不会覆盖，只会变成两份。
      先按 source 删掉旧的，再插新的，才是"这篇文档现在长这样"的正确语义。

    为什么不用 upsert：
      auto_id=True 的集合没有稳定主键可指定，upsert 用不了。
      真实项目里如果有稳定的业务主键（比如 文档id+片段序号），
      用 upsert 会更省事 —— 这也是个值得知道的取舍。
    """
    name = _collection_name()
    if len(chunks) != len(vectors):
        raise VectorStoreError(
            f"片段数和向量数不一致：{len(chunks)} vs {len(vectors)}"
        )
    if not chunks:
        return 0

    _check_lengths(source, chunks)

    # 先删旧版本（同一 source）
    client.delete(collection_name=name, filter=f"source == {_quote(source)}")

    rows: list[dict[str, Any]] = [
        {
            VECTOR_FIELD: vector,
            "source": source,
            "heading": c.heading,
            "content": c.content,
            "chunk_index": c.index,
            "is_deleted": False,
        }
        for c, vector in zip(chunks, vectors)
    ]
    client.insert(collection_name=name, data=rows)
    return len(rows)


def flush(client: MilvusClient) -> None:
    """强制把刚写入的数据变成"可见"。

    为什么需要：Milvus 是**最终一致性**的 —— insert 返回成功，
    并不代表马上能被 query/search 查到。刚灌完就 count，会少几条
    （少的正好是最后一批），看起来像"写丢了"，其实只是还没落地。

    flush 把内存里的数据落盘并封段，之后立刻可见。
    代价是有 IO，所以只在"写完之后要立刻读"的地方调
    （建索引脚本），不要放进 save_source 里每次都刷。
    """
    client.flush(_collection_name())


def count(client: MilvusClient, *, include_deleted: bool = False) -> int:
    """有多少条。默认不算软删除的。

    注意：不要用 `filter="id >= 0"` 再 count ——
    auto_id 生成的 id 不能这样过滤，会静默返回 0，
    让人以为数据没写进去。
    """
    name = _collection_name()
    expr = "" if include_deleted else "is_deleted == false"
    rows = client.query(
        collection_name=name,
        filter=expr,
        output_fields=["count(*)"],
    )
    if not rows:
        return 0
    return int(rows[0].get("count(*)", 0))


def search(
    client: MilvusClient,
    vector: list[float],
    *,
    top_k: int = 10,
    source: str | None = None,
) -> list[Hit]:
    """按相似度找 top_k 条。

    source 可选：限定只在某篇文档里找（调试时很有用）。
    """
    name = _collection_name()
    if top_k <= 0:
        raise VectorStoreError(f"top_k 不合法：{top_k}")

    ensure_loaded(client)

    # 软删除的不返回
    expr = "is_deleted == false"
    if source:
        expr = f"{expr} and source == {_quote(source)}"

    results = client.search(
        collection_name=name,
        data=[vector],
        filter=expr,
        limit=top_k,
        output_fields=["source", "heading", "content", "chunk_index"],
        search_params={"metric_type": METRIC_TYPE},
    )

    hits: list[Hit] = []
    # 空集合时 Milvus 返回 [[]]，不是 []，所以先判断
    for row in (results[0] if results else []):
        entity = row.get("entity", {})
        hits.append(
            Hit(
                score=float(row.get("distance", 0.0)),
                source=entity.get("source", ""),
                heading=entity.get("heading", ""),
                content=entity.get("content", ""),
                chunk_index=int(entity.get("chunk_index", -1)),
            )
        )
    return hits