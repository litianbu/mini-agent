"""切分（S12-1）的测试。

这里测的都是"会让检索变差"的具体行为，而不是实现细节：
切得超限、丢了标题、切口没有重叠、切出空片、死循环。
"""

from app.rag.chunk import (
    Chunk,
    fences_balanced,
    split_by_separators,
    split_markdown,
    split_sections,
)


def test_short_doc_is_one_chunk() -> None:
    """短文档不该被切碎：切成几片反而降低检索精度。"""
    chunks = split_markdown("# 标题\n\n只有一句话。", source="a.md")
    assert len(chunks) == 1
    assert chunks[0].content == "只有一句话。"
    assert chunks[0].source == "a.md"


def test_heading_path_includes_parent() -> None:
    """标题要带父级 —— 单独检索到 "为什么" 时，得知道在说哪件事。"""
    text = "# 上下文管理\n\n## 为什么不做摘要\n\n因为要额外调模型。\n"
    sections = split_sections(text)
    headings = [h for h, _ in sections]
    assert "上下文管理 > 为什么不做摘要" in headings


def test_never_exceeds_max_chars() -> None:
    """每片都不能超限 —— 超了就喂不进 embedding 接口。"""
    text = "# T\n\n" + ("这是一句话。" * 300)
    chunks = split_markdown(text, max_chars=200, overlap=50)
    assert all(len(c) <= 200 for c in chunks)


def test_overlap_does_not_overflow() -> None:
    """回归测试：把尾巴带到下一片时，不许因此超限。

    这个 bug 我第一版就写了：cur = tail + piece，
    当 piece 本身就接近 max_chars 时，加尾巴必然超限。
    上面的 test_never_exceeds_max_chars 抓不到它，
    因为那里每片都远小于上限，加尾巴也不会超。
    """
    # 关键：每段必须**接近上限但不超过**（这里是 176 字符 vs 上限 200）。
    # 这样"加 80 字符尾巴"必然超限 —— 只有这种构造才能抓到 bug。
    # 如果每段只有 70 字符，70 + 80 = 150 仍在上限内，测了等于没测。
    para = "这是一个用来测试重叠是否会撑破上限的长句子。" * 8   # 约 176 字符
    assert 150 < len(para) <= 200, f"构造不合要求: {len(para)}"
    text = "# T\n\n" + "\n\n".join([para] * 12)
    chunks = split_markdown(text, max_chars=200, overlap=80)
    for c in chunks:
        assert len(c) <= 200, f"超限：{len(c)} 字符"


def test_overlap_shares_tail() -> None:
    """相邻片段要有重叠，否则答案落在切口上会两边都不完整。"""
    text = "# T\n\n" + "".join(f"第{i}句内容略长一些。" for i in range(60))
    chunks = split_markdown(text, max_chars=150, overlap=30)
    assert len(chunks) >= 2
    # 直接验证语义：相邻两片必须有一段公共文字（那就是重叠）
    # 不写死具体字符数 —— 重叠长度会被"剩余空间"限制，不是固定值
    for prev, nxt in zip(chunks, chunks[1:]):
        shared = any(
            prev.content[-k:] and prev.content[-k:] in nxt.content[:80]
            for k in (10, 20, 30)
        )
        assert shared, f"相邻片段没有重叠：{prev.content[-20:]!r} / {nxt.content[:20]!r}"


def test_no_empty_chunks() -> None:
    """不能切出空片段 —— 空片段会污染向量库。"""
    text = "# A\n\n\n\n## B\n\n\n\n有内容。\n\n### C\n\n\n"
    chunks = split_markdown(text, source="x.md")
    assert all(c.content.strip() for c in chunks)


def test_text_ending_with_separator_terminates() -> None:
    """回归测试：句号在末尾时曾导致无限递归（切完和原文一样长）。"""
    text = "很长的句子" * 20 + "。"
    out = split_by_separators(text, max_chars=20)
    assert len(out) > 1
    assert all(len(p) <= 20 for p in out)


def test_tiny_chunks_are_merged() -> None:
    """太短的片段是噪声，要并进相邻片（但并完不能超限）。"""
    text = "# T\n\n短。\n\n" + "正常长度的一段话。" * 10
    chunks = split_markdown(text, max_chars=200, min_chars=100)
    assert all(len(c) >= 1 for c in chunks)
    # 单独一个"短。"不该独立成片
    assert not any(c.content.strip() == "短。" for c in chunks)


def test_chunk_length_helper() -> None:
    """Chunk 要能直接 len()，方便判断大小。"""
    c = Chunk(source="a.md", heading="h", content="12345")
    assert len(c) == 5


# ── 代码块保护（S12-1b）──────────────────────────────────
# 由来：第一版切分把 13 篇文档切成 232 片，其中 39 片的代码围栏是断的。
# 断掉的片段检索命中也没用 —— 里面只有半段代码。


def test_code_block_stays_whole() -> None:
    """代码块必须整块保留，不能从中间切开。"""
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(20)) + "\n```"
    text = f"# T\n\n前置说明。\n\n{code}\n\n后置说明。\n"
    chunks = split_markdown(text, max_chars=600, overlap=80)
    assert any(code.strip() in c.content for c in chunks)


def test_oversized_code_block_keeps_fences_balanced() -> None:
    """超长代码块切成多片时，每一片都要自带完整围栏。

    切坏的症状：第一片有开头没结尾，第二片只有内容 —— 两片都是非法 markdown。
    """
    lines = [f"value_{i} = compute_something({i})  # 一行解释" for i in range(60)]
    code = "```python\n" + "\n".join(lines) + "\n```"
    text = f"# T\n\n{code}\n"
    chunks = split_markdown(text, max_chars=300, overlap=50)
    assert len(chunks) >= 2, "这个代码块应该被切成多片"
    for c in chunks:
        assert fences_balanced(c.content), f"围栏不配对: {c.content[:40]!r}"
        assert len(c) <= 300


def test_heading_inside_code_block_is_not_a_heading() -> None:
    """代码块里的 # 是注释，不是标题。

    这个 bug 曾把 bash 代码块劈成两半：
        ```bash
        # 启动后端          ← 被当成了新章节
        uv run uvicorn ...
        ```
    """
    code = "```bash\n# 启动后端\nuv run uvicorn app.main:app\n# 启动前端\npnpm dev\n```"
    text = f"# 真标题\n\n{code}\n"
    chunks = split_markdown(text, source="a.md")
    assert {c.heading for c in chunks} == {"真标题"}


def test_no_dangling_fence_across_overlap() -> None:
    """重叠不许把上一片的结束围栏复制到下一片。

    踩过的坑：上一片以 ``` 结尾，尾巴被复制到下一片开头，
    结果后一片凭空多出一个孤立围栏，前一片也变成半段代码。
    """
    body = "\n\n".join(f"第{i}段说明文字，内容不长但足够拼成多片。" for i in range(40))
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(30)) + "\n```"
    text = f"# T\n\n{body}\n\n{code}\n\n{body}\n"
    for c in split_markdown(text, max_chars=300, overlap=80):
        assert fences_balanced(c.content), f"落单围栏: {c.content[-60:]!r}"


def test_real_docs_have_no_broken_fences() -> None:
    """整库回归：真实文档切出来的每一片，围栏都必须配对。

    单测造的小样本容易漏掉真实 markdown 的写法，
    所以这条直接扫 docs/ 下所有文件。
    """
    from pathlib import Path

    docs_dir = Path(__file__).resolve().parents[3] / "docs"
    if not docs_dir.is_dir():
        return  # 没有 docs 就跳过
    for path in sorted(docs_dir.glob("*.md")):
        for c in split_markdown(path.read_text(encoding="utf-8"), source=path.name):
            assert fences_balanced(c.content), (
                f"{path.name} 有落单围栏: {c.content[:60]!r}"
            )

def test_split_markdown_prints_nothing(capsys) -> None:
    """切分是纯函数，不许往 stdout 写东西。

    为什么专门测这个：调试 print 留在库里最难被发现 —— 它不报错，
    只是每次切分都往日志里灌一大坨对象 repr。这次就漏了三处。
    """
    text = "# 甲\n\n第一段。\n\n## 甲二\n\n第二段。\n"
    split_markdown(text, source="a.md")
    out = capsys.readouterr()
    assert out.out == "", f"split_markdown 不该有输出，实际打印：{out.out[:200]!r}"
