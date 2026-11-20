"""向量化：把文本变成一串数字。

这一步只有一个动作：**文本 → 1024 个浮点数**。

为什么要做这件事？
  计算机不会比"意思像不像"，它只会比数字。把两句话各变成 1024 个数字之后，
  "像不像"就有了一个可计算的答案：两个向量的夹角余弦。
  意思越近，夹角越小，余弦越接近 1。

     "怎么报销"     → [0.02, -0.13, ...]  ┐
     "差旅费用申报"  → [0.03, -0.11, ...]  ┘ 余弦 0.62  ← 近
     "今天天气不错"  → [-0.4, 0.21, ...]    ← 余弦 0.44  ← 远

  （实测就是上面这三个数，见 docs/step-11）

为什么用 bge-m3：
  中文语义检索里它是主流选择之一，而且和工作项目用的是同一个模型 ——
  本地调通之后换到公司环境，向量空间是一致的，不用重建库。

这个文件只负责"调接口拿到向量"，不负责存、不负责搜。
存是 S12-3（Milvus），搜是 S12-4（检索）。
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ..core.config import settings

__all__ = ["EmbeddingError", "embed_query", "embed_texts"]


class EmbeddingError(RuntimeError):
    """向量化失败。

    单独一个异常类型，是为了让调用方能区分"接口挂了"和"我的代码错了"。
    """


def _check_config() -> None:
    """开调之前先确认配置齐了。

    宁可在这里报一句"没配 key"，也不要发一个空 Authorization 出去 ——
    网关会返回 401，然后排查时你得先猜是 key 错了还是没配。
    """
    if not settings.embedding_enabled:
        raise EmbeddingError(
            "没有配置 EMBEDDING_API_KEY。\n"
            "在仓库根目录的 .env 里加：\n"
            "  EMBEDDING_API_KEY=sk-...\n"
            "  EMBEDDING_API_BASE=https://mg.biomap-int.com/v1"
        )
    if settings.embedding_dimension <= 0:
        raise EmbeddingError(f"EMBEDDING_DIMENSION 不合法：{settings.embedding_dimension}")


def _sort_by_index(items: list[dict]) -> list[dict]:
    """按 index 排回原始顺序。

    为什么不能直接用返回顺序：
      批量请求的响应里每条都带 "index" 字段，**协议并不保证返回顺序和传入一致**
      （并行编码时很容易乱）。不排序的话，片段 A 会拿到片段 B 的向量 ——
      检索结果全是错的，而且不会报任何错。

    这类 bug 的可怕之处：它不崩，只是答案莫名地不准。
    """
    return sorted(items, key=lambda d: d.get("index", 0))


async def _embed_batch(
    client: httpx.AsyncClient,
    texts: list[str],
    *,
    retries: int,
) -> list[list[float]]:
    """发一批，失败重试。

    为什么重试是安全的：
      向量化是**幂等**的 —— 同一段文本算两次，向量一样。
      （对比：写数据库、发消息就不幂等，重试可能写两条。）
      所以 429/5xx/网络抖动都可以放心重试。
    """
    url = f"{settings.embedding_api_base.rstrip('/')}/embeddings"
    payload = {"model": settings.embedding_model, "input": texts}
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        # 网络层失败（超时、连不上）值得重试 —— 是"路上"的问题
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (2**attempt))
            continue

        # 429 限流 / 5xx 服务端故障 —— 也值得重试，等一会儿多半就好了
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = EmbeddingError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (2**attempt))
            continue

        # 其余 4xx 是"我的请求有问题"（key 错、模型名错）——
        # 重试一百次也是同样的结果，立刻失败。
        if resp.status_code >= 400:
            raise EmbeddingError(
                f"向量化接口返回 {resp.status_code}：{resp.text[:300]}\n"
                f"（模型 {settings.embedding_model}，地址 {url}）"
            )

        # 从这里开始是"请求成功但内容不对"（维度不符、数量不对）。
        # 这些错误重试也不会变，所以不放进重试逻辑里，直接抛。
        body = resp.json()
        items = _sort_by_index(body.get("data", []))
        if len(items) != len(texts):
            raise EmbeddingError(
                f"返回了 {len(items)} 个向量，但传了 {len(texts)} 条文本"
            )

        vectors: list[list[float]] = []
        for item in items:
            vec = item.get("embedding") or []
            # 维度校验：这是最值得写的一处防御
            #
            # 换了模型（比如 bge-m3 → 别的 768 维模型）而没改配置，
            # 灌进 1024 维的集合会失败；更难查的是配置改小了 ——
            # 灌库成功，但检索时维度对不上，报错在完全不相干的地方。
            # 在源头校验，错误就停在最早、最清楚的位置。
            if len(vec) != settings.embedding_dimension:
                raise EmbeddingError(
                    f"维度不符：期望 {settings.embedding_dimension}，"
                    f"实际 {len(vec)}。\n"
                    f"检查 .env 里的 EMBEDDING_MODEL 和 EMBEDDING_DIMENSION。"
                )
            vectors.append(vec)
        return vectors

    raise EmbeddingError(f"向量化失败（重试 {retries} 次）：{last_error}")


async def embed_texts(
    texts: list[str],
    *,
    batch_size: int | None = None,
    retries: int = 2,
    client: httpx.AsyncClient | None = None,
    verbose: bool = False,
) -> list[list[float]]:
    """把一批文本变成向量，顺序和传入一一对应。

    `client` 参数是给测试用的：单测传一个 MockTransport 的 client，
    就不会真的打网络。生产代码不传，函数自己建。

    `verbose=True` 时逐批打印进度和耗时 —— 灌库时盯着看，能立刻发现
    "某批特别慢"（通常是限流退避）或者"批次数量不对"（切分出了问题）。
    """
    _check_config()

    if not texts:
        return []

    # 空文本必须挡在前面。
    # 有的网关对空字符串直接 400，有的返回全零向量 ——
    # 全零向量在余弦计算里会让所有相似度变成 NaN，比报错更难查。
    for i, t in enumerate(texts):
        if not t.strip():
            raise EmbeddingError(f"第 {i} 条文本是空的，不能向量化")

    size = batch_size or settings.embedding_batch_size
    if size <= 0:
        raise EmbeddingError(f"batch_size 不合法：{size}")

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(settings.embedding_timeout))

    try:
        out: list[list[float]] = []
        # 分批是顺序发的，不是并发。
        #
        # 并发能快 3-4 倍，但会让限流更容易触发，也会把"哪批失败了"变得难追踪。
        # 228 个片段分批约 4 次请求、2 秒左右，不值得为这点时间换复杂度。
        total_batches = (len(texts) + size - 1) // size
        for i, start in enumerate(range(0, len(texts), size), 1):
            batch = texts[start : start + size]
            t0 = time.perf_counter()
            out.extend(await _embed_batch(client, batch, retries=retries))
            if verbose:
                print(
                    f"        批次 {i}/{total_batches}  "
                    f"{len(batch):3d} 条  {time.perf_counter() - t0:5.2f}s"
                )
        if verbose:
            print(f"        → 共 {len(out)} 个向量（每个 {settings.embedding_dimension} 维）")
        return out
    finally:
        # 只关自己建的 client；外面传进来的由调用方负责
        if own_client and client is not None:
            await client.aclose()


async def embed_query(text: str, *, client: httpx.AsyncClient | None = None) -> list[float]:
    """检索时用：把用户的问题变成向量。

    单独包一层是因为**问题和文档必须用同一个模型编码**。
    用两个不同模型各编一遍，两个向量不在同一个空间里，
    余弦值毫无意义 —— 而且不会有任何报错。
    """
    vectors = await embed_texts([text], client=client)
    return vectors[0]