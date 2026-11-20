"""索引入口：把 docs/ 里的文档灌进 Milvus。

它本身不含新逻辑，只是把三段已经写好的东西串起来：

    docs/*.md  --split_markdown-->  片段
    片段       --embed_texts----->  向量
    (片段, 向量) --save_source---->  Milvus

为什么单独做一个"入口"而不是每次手敲 Python：
  灌库是**会重复做**的事（改文档、改切分参数、换模型都要重灌）。
  重复做的事就该有一条命令，而不是一段需要修改的临时代码。

用法：
    uv run python -m app.rag.index --init        只建集合
    uv run python -m app.rag.index --rebuild     全量重灌（幂等）
    uv run python -m app.rag.index --rebuild --file s1-code-map.md   只灌这一篇
    uv run python -m app.rag.index --count       有多少条
    uv run python -m app.rag.index --search "问题"   检索一次

注意 --rebuild 不是"清空重建集合"：
  它逐篇覆盖（同一篇先删后插），所以：
    · 重复跑不会产生重复数据
    · 删除某篇文档后，它的旧片段**不会**自动消失（要手动删）
  这是有意的取舍：宁可残留，也不要一次手滑把 228 条全清掉。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from ..core.config import settings
from .chunk import split_markdown
from .embed import embed_query, embed_texts
from .store import count, ensure_collection, flush, get_client, save_source, search

# 仓库根目录：app/rag/index.py → [0]=app/rag [1]=app [2]=backend [3]=apps [4]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"


def _client():
    """连库并打印连到哪儿了 —— 连错环境是这类脚本最常见的错。"""
    client = get_client()
    print(f"Milvus : {settings.milvus_uri}  db={settings.milvus_db}")
    print(f"集合   : {settings.milvus_collection}")
    print(f"维度   : {settings.embedding_dimension}")
    return client


async def rebuild(
    client,
    docs_dir: Path,
    *,
    verbose: bool = True,
    only: str | None = None,
) -> tuple[int, int]:
    """切分 → 向量化 → 写入，逐篇处理。返回 (文档数, 片段数)。

    verbose=True 把每一步都打出来：读了多少字符、切成几片、分几批发给
    向量接口、删了几条、插了几条、各自花了多久。
    默认打开 —— 这个入口就是给人看着跑的，出问题时省一次排查。

    only 非空时只处理这一篇（文件名或绝对路径），其余文档一概不动。
    用途：一次只灌一篇，看清楚它切成几片、花了多久，再决定要不要全量。
    """
    if only:
        target = Path(only)
        if not target.is_absolute():
            target = docs_dir / target
        if not target.exists():
            raise SystemExit(f"找不到文件：{target}")
        paths = [target]
        print(f"\n只处理这一篇：{target.name}（其余文档不动）")
    else:
        paths = sorted(docs_dir.glob("*.md"))
        if not paths:
            raise SystemExit(f"{docs_dir} 下没有 .md 文件")

    print(f"\n开始灌库：{len(paths)} 篇文档 → {docs_dir}")
    print(f"模型        {settings.embedding_model}（{settings.embedding_dimension} 维）")
    print(f"批大小      {settings.embedding_batch_size} 条/次\n")

    total_chunks = 0
    t_all = time.perf_counter()
    for i, path in enumerate(paths, 1):
        t0 = time.perf_counter()
        text = path.read_text(encoding="utf-8")
        if verbose:
            print(f"[{i}/{len(paths)}] {path.name}")
            print(f"    读文件      {len(text):,} 字符")

        chunks = split_markdown(text, source=path.name)
        if not chunks:
            print("    切分        0 片 → 跳过这篇")
            continue
        if verbose:
            longest = max(len(c.content) for c in chunks)
            shortest = min(len(c.content) for c in chunks)
            print(f"    切分        {len(chunks)} 片（最长 {longest} / 最短 {shortest} 字符）")
            print(f"    向量化      {len(chunks)} 条文本 …")

        vectors = await embed_texts([c.content for c in chunks], verbose=verbose)
        written = save_source(client, path.name, chunks, vectors, verbose=verbose)
        total_chunks += written
        if verbose:
            print(f"    本篇合计    {written} 条   累计 {total_chunks} 条")
            print(f"    耗时        {time.perf_counter() - t0:.2f}s\n")
        else:
            print(f"  {path.name:20s} {written:3d} 片  （{len(text):5d} 字符）")

    if verbose:
        print(f"逐篇写入完毕：{total_chunks} 条，用时 {time.perf_counter() - t_all:.2f}s")
    return len(paths), total_chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 docs/ 文档灌进 Milvus")
    parser.add_argument("--init", action="store_true", help="只建集合，不灌数据")
    parser.add_argument("--rebuild", action="store_true", help="全量重灌（幂等）")
    parser.add_argument(
        "--file",
        metavar="文件名",
        help="只处理这一篇（如 s1-code-map.md），不传则处理目录下全部",
    )
    parser.add_argument("--count", action="store_true", help="查看条数")
    parser.add_argument("--search", metavar="问题", help="检索一次")
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR, help="文档目录")
    parser.add_argument("--top-k", type=int, default=5, help="检索返回条数")
    args = parser.parse_args(argv)

    if not any([args.init, args.rebuild, args.count, args.search, args.file]):
        parser.print_help()
        return 1

    client = _client()

    if args.init:
        created = ensure_collection(client, verbose=True)
        print("\n集合" + ("已创建 ✓" if created else "已存在，跳过"))
        return 0

    if args.rebuild or args.file:
        t0 = time.perf_counter()
        created = ensure_collection(client, verbose=True)
        if created:
            print("\n（集合不存在，已新建）")
        docs, chunks = asyncio.run(
            rebuild(client, args.docs_dir, verbose=True, only=args.file)
        )

        # 必须 flush 之后再数：Milvus 最终一致，刚 insert 完立刻 count
        # 会少掉最后一批，白白看着像"写丢了"。
        flush(client, verbose=True)
        total = count(client, verbose=True)
        print(f"\n完成：{docs} 篇 → 写入 {chunks} 片段")
        print(f"库里当前共有 {total} 条")
        print(f"总耗时 {time.perf_counter() - t0:.2f}s")

        # 只灌一篇时，库里条数当然不等于这篇的片段数，不做"条数不一致"的比较。
        if args.file is None and total != chunks:
            print(
                f"⚠️  条数不一致（{total} vs {chunks}）：\n"
                "   通常是某篇文档被删了但旧片段还在 —— 见模块说明里的取舍。",
                file=sys.stderr,
            )
        return 0

    if args.count:
        total = count(client, verbose=True)
        print(f"\n条数（不含软删除）: {total}")
        return 0

    if args.search:
        print(f"\n问：{args.search}")
        t0 = time.perf_counter()
        vector = asyncio.run(embed_query(args.search))
        print(f"  问题向量化   {len(vector)} 维  {time.perf_counter() - t0:.2f}s")
        hits = search(client, vector, top_k=args.top_k, verbose=True)
        print()
        if not hits:
            print("  没有命中 —— 库里可能还是空的，先跑 --rebuild")
            return 1
        for i, hit in enumerate(hits, 1):
            print(f"  {i}. {hit}")
            print(f"     {hit.content[:100].replace(chr(10), ' ')}…")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())