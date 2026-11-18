"""文档切分（chunking）—— RAG 的第一步。

## 为什么要切分

检索的最小单位是"一个片段（chunk）"。模型回答问题时，
塞进 prompt 的不是整篇文档，而是"最像问题的那几片"。

所以切分质量直接决定检索质量 —— 这也是为什么说
**RAG 的效果八成在切分和检索，不在模型**。

## 三个关键决策

1. **在哪切**：按语义边界（标题、段落、句号）切，
   而不是按固定字数切 —— 后者会把一句话、一张表劈成两半。

2. **切多大**：太小则语义不完整，太大则检索不精确、还浪费 token。
   实战经验：中文 300~800 字比较稳。bge-m3 单次输入上限 7500 字节
   （约 2500 中文字符），所以片段大小必须远小于它。

3. **要不要重叠**：要。相邻片段共享一小段尾巴，
   避免"答案正好落在切口上"导致两边都不完整。

## 与工作项目的对应

这个模块对应工作项目里的 `app/rag/` 目录（他们叫 pipeline/embedder/retriever）。
我们按同样的拆分方式组织，方便以后对照。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 分隔符从"最该切的地方"排到"最不该切的地方"。
# 递归切分的思路：先用最粗的边界试，一段还是太长就退一级用更细的边界。
#
# 注意中文标点也在列表里 —— 英文项目常常只写 "\n\n"/"\n"/" "，
# 中文文档里句号才是天然的语义边界。
SEPARATORS: tuple[str, ...] = (
    "\n\n",   # 段落
    "\n",     # 换行
    "。",      # 中文句号
    "！",
    "？",
    "；",
    "，",      # 逗号：切到这里已经很不理想了
    " ",       # 英文空格
)

# markdown 标题行，例如 "## 上下文预算" / "# 步骤 7"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """一个待向量化的片段。

    为什么要把 heading 和 source 一起存：
    检索到片段之后，用户需要知道"这是哪篇文档的哪一节"。
    没有这两个字段，回答就只能说"根据资料"，无法给出出处。
    """

    source: str          # 来源文件名，例如 "step-07.md"
    heading: str         # 标题路径，例如 "上下文管理 > 为什么不做摘要"
    content: str         # 正文（可能带一点上一片的尾巴，见 overlap）
    index: int = 0       # 在本篇文档内的序号，从 0 开始

    def __len__(self) -> int:
        return len(self.content)


def split_sections(text: str) -> list[tuple[str, str]]:
    """按 markdown 标题把文档切成 (标题路径, 正文) 列表。

    标题路径会带上父级，例如：
        "# 上下文管理" 下面的 "## 为什么不做摘要"
        → "上下文管理 > 为什么不做摘要"

    为什么要带父级：单看 "为什么不做摘要" 不知道在说哪件事，
    带上父级之后，即便这一段被单独检索出来，也能看懂上下文。
    """
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []   # (层级, 标题)
    buf: list[str] = []
    current = ""                        # 当前标题路径

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        if body:
            sections.append((current, body))
        buf = []

    in_code = False

    for line in text.splitlines():
        # 代码块里的 # 不是标题，是注释或 shell 提示符。
        #
        # 这个 bug 很隐蔽：下面这种 bash 代码块
        #     ```bash
        #     # 启动后端
        #     uv run uvicorn ...
        #     ```
        # 里的 "# 启动后端" 完全符合 markdown 标题的语法，
        # 于是被当成了新章节 —— 一个代码块从中间被劈成两段，
        # 两段都没有配对的围栏。必须先判围栏，再判标题。
        if line.lstrip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            continue

        m = None if in_code else HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            # 弹出同级或更深的标题，维护"当前路径"
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current = " > ".join(t for _, t in stack)
        else:
            buf.append(line)
    flush()
    return sections


def split_blocks(text: str) -> list[tuple[str, bool]]:
    """把正文切成块，返回 [(块内容, 是不是代码块)]。

    代码块是**原子**的：整块进来、整块出去，不参与按句子切分。

    为什么必须这样：
    ``` 围栏一旦被切开，片段里就只剩半段代码。检索命中它也读不懂，
    塞进 prompt 更糟 —— 模型看到的是语法不完整的内容。

    真实数据里这很常见：文档中的目录树、配置示例往往比正文段落还长。
    """
    blocks: list[tuple[str, bool]] = []
    buf: list[str] = []
    in_code = False

    def flush(is_code: bool) -> None:
        nonlocal buf
        if buf:
            joined = "\n".join(buf)
            if joined.strip():
                # 统一补一个换行，拼回片段时不会把两段粘成一句
                blocks.append((joined + "\n", is_code))
            buf = []

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if not in_code:
                flush(False)          # 先把前面的正文冲出去
                in_code = True
                buf.append(line)
            else:
                buf.append(line)
                flush(True)           # 围栏闭合，整块入列
                in_code = False
            continue
        buf.append(line)

    # 没闭合的围栏：当普通文本处理。
    # 输入本身是坏的，此时"保持原样"比"假装它是代码块"更安全。
    flush(False)
    return blocks


def _split_code_block(block: str, max_chars: int) -> list[str]:
    """超过上限的代码块：按行切，并给每一片补上围栏。

    不这样做的话，切出来的每片都是残缺代码：

        第一片: ```python
                a = 1
        第二片:  b = 2
                ```
        两边都没闭合，谁也读不懂。

    正确做法是每片自带完整的围栏：

        第一片: ```python  a = 1  ```
        第二片: ```python  b = 2  ```

    这不是我发明的 —— 所有正经的代码切分器都这么做，
    因为"每片必须能独立被理解"是切分的第一原则。
    """
    lines = block.rstrip("\n").splitlines()
    if len(lines) < 2:
        return _hard_cut(block, max_chars)

    opening, closing = lines[0], lines[-1]
    # 最后一行不是围栏说明这其实不是完整代码块，退化处理
    if not closing.lstrip().startswith("```"):
        return _hard_cut(block, max_chars)

    body = lines[1:-1]
    overhead = len(opening) + len(closing) + 2   # 两处换行
    budget = max_chars - overhead
    if budget <= 0:
        return _hard_cut(block, max_chars)

    out: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def emit() -> None:
        nonlocal cur, cur_len
        if cur:
            # 结尾必须带换行：merge_pieces 是直接字符串拼接，
            # 不带换行会和下一片粘成 ``````（六个反引号，围栏就废了）
            out.append(opening + "\n" + "\n".join(cur) + "\n" + closing + "\n")
        cur, cur_len = [], 0

    for line in body:
        # 单行就超预算（比如压缩过的一行 JS）：先把当前片冲出去，再硬切这行
        if len(line) + 1 > budget:
            emit()
            out.extend(_hard_cut(line, budget))
            continue
        if cur and cur_len + len(line) + 1 > budget:
            emit()
        cur.append(line)
        cur_len += len(line) + 1
    emit()

    return out or [opening + "\n" + closing + "\n"]


def split_by_separators(text: str, max_chars: int) -> list[str]:
    """把一段文本切成若干块，每块长度 ≤ max_chars。

    递归策略：先试最粗的分隔符。如果有哪一块还是太长，就对它再退一级。
    一直退到单字符都没有，就硬切 —— 这是最后的保险，
    保证函数一定返回满足长度约束的结果（宁可切得难看，也不能超限）。
    """
    if len(text) <= max_chars:
        return [text]

    for sep in SEPARATORS:
        if sep not in text:
            continue
        parts = text.split(sep)
        # 把分隔符粘回去，否则句号会丢，拼出来的片段读起来是断的
        pieces = [p + sep for p in parts[:-1]] + [parts[-1]]
        out: list[str] = []
        for piece in pieces:
            if not piece:
                continue
            if len(piece) <= max_chars:
                out.append(piece)
            elif len(piece) < len(text):
                # 确实变短了才继续递归 —— 否则会死循环（见下面的注释）
                out.extend(split_by_separators(piece, max_chars))
            else:
                # 切完和原文一样长，说明这个分隔符没用（比如它就在末尾，
                # 拼回去又还原成原文）。直接硬切，保证一定前进。
                out.extend(_hard_cut(piece, max_chars))
        return out

    # 所有分隔符都不在文本里（比如一长串没有标点的字符）：硬切
    return _hard_cut(text, max_chars)


def _hard_cut(text: str, max_chars: int) -> list[str]:
    """硬切 —— 最后手段。

    注意这里会和 merge_pieces 的重叠加起来：
    硬切出来的相邻块没有重叠，所以如果一段超长无标点文本被硬切，
    切口处就没有 overlap 保护。这是可接受的权衡：
    无标点长文本本来就少见，而"一定返回不超限的结果"更重要。
    """
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def merge_pieces(pieces: list[str], max_chars: int, overlap: int) -> list[str]:
    """把碎块拼回接近 max_chars 大小的片段，并加上重叠。

    为什么要有这一步：
    切分是先切碎再拼回，如果不拼，每个段落都会变成一个独立片段，
    片段太小 → 语义不完整 → 检索命中率低。

    重叠（overlap）怎么起作用：
        片段1: [....... 后半句ABCD]
        片段2:            [ABCD 后半句.......]
                                   ↑ 上一片的尾巴复制过来
    这样即使答案正好落在切口上，两片里各有一半，检索时至少有一片能命中。
    """
    chunks: list[str] = []
    cur = ""

    for piece in pieces:
        if not cur:
            cur = piece
        elif len(cur) + len(piece) <= max_chars:
            cur += piece
        else:
            chunks.append(cur)
            cur = _carry_tail(cur, piece, max_chars, overlap)

    if cur:
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


def _carry_tail(prev: str, piece: str, max_chars: int, overlap: int) -> str:
    """给下一片带上上一片的尾巴，但不许因此超限。

    这个边界很容易漏：加上尾巴之后 cur 可能超过 max_chars，
    于是"每片不超过 600 字"这条保证就被破坏了。
    修法是把尾巴长度限制在剩余空间里 —— 空间不足就不带。
    """
    if overlap <= 0:
        return piece
    room = max_chars - len(piece)
    if room <= 0:
        return piece          # 这一块本身就快满了，没地方放尾巴

    tail = prev[-min(overlap, room):]

    # 尾巴里有围栏就放弃重叠。
    #
    # 踩过的坑：上一片以 ``` 结尾时，把这半截围栏复制到下一片开头，
    # 结果是两片都变成非法 markdown —— 后一片凭空多出一个孤立围栏，
    # 前一片的开头又是半段代码。而重叠本来只想接上**文字**上下文，
    # 一段代码的结尾没有这个价值，放弃它没有任何损失。
    if "```" in tail:
        return piece

    return tail + piece


def merge_tiny(chunks: list[Chunk], min_chars: int, max_chars: int) -> list[Chunk]:
    """把过短的片段并到相邻片段里。

    为什么：只有几十个字的片段是噪声。检索时它很容易因为
    "碰巧包含关键词" 被召回，但它其实说明不了任何事。
    实战里通常直接丢掉 <100 字的片段，或者像这里一样并入下一片。
    """
    if not chunks:
        return []

    out: list[Chunk] = []
    for c in chunks:
        # 上一片太短，且属于同一节 → 并进来（并完可能还是短，循环会继续处理）
        if out and len(out[-1]) < min_chars and out[-1].heading == c.heading:
            merged = out[-1].content + "\n" + c.content
            # 但不能因此超限 —— 超了就宁可留一个短片段
            if len(merged) <= max_chars:
                out[-1].content = merged
                continue
        out.append(c)
    return out


def split_markdown(
    text: str,
    *,
    source: str = "",
    max_chars: int = 600,
    overlap: int = 80,
    min_chars: int = 100,
) -> list[Chunk]:
    """把一个 markdown 文档切成片段列表。

    参数：
        max_chars: 单片最大字符数。600 字对应约 1800 字节，
                  远低于 bge-m3 的 7500 字节上限，留足余量。
        overlap:   相邻片段的重叠字符数。
        min_chars: 小于这个长度的片段会被并入下一片（避免噪声）。
    """
    # 给重叠预留空间：正文按 (max_chars - overlap) 切，
    # 这样"尾巴 + 本片"一定不超过 max_chars，重叠也始终是完整的。
    #
    # 不这样做的教训：如果正文按 max_chars 切，一片已经快满，
    # 留给尾巴的空间可能只剩几个字符 —— 重叠就名存实亡了。
    inner = max_chars - overlap
    if inner <= 0:
        inner = max(1, max_chars // 2)

    chunks: list[Chunk] = []

    for heading, body in split_sections(text):
        # 先按"代码块 vs 文本"分块：代码块整体保留，文本按句子切
        pieces: list[str] = []
        for block, is_code in split_blocks(body):
            if len(block) <= inner:
                pieces.append(block)
            elif is_code:
                # 超长代码块：按行切并补围栏（见 _split_code_block）
                pieces.extend(_split_code_block(block, inner))
            else:
                pieces.extend(split_by_separators(block, inner))

        for piece in merge_pieces(pieces, max_chars, overlap):
            chunks.append(Chunk(source=source, heading=heading, content=piece))

    chunks = merge_tiny(chunks, min_chars, max_chars)

    for i, c in enumerate(chunks):
        c.index = i
    return chunks
