# 第四步：grep 和 edit —— 让 agent 能改代码

S3 之后 agent 能读了，但**改**还很粗暴：只能用 `write_file` 整文件覆盖。
改 1 行要重发 198 行，又慢又容易出错。这一步补上两个工具。

## grep：按内容找

### 之前有多笨

问"`ChatSession` 在哪定义的"，模型只能 glob 出所有 py 文件，
再一个一个 `read_file` 试。运气差要读十来个文件，每个几百行进上下文。

### 之后

```
grep("class ChatSession")
→ apps/backend/app/models/__init__.py:49: class ChatSession(Base):
```

**一次调用代替十次。** 实测模型只用了 2 轮就答对了。

### 为什么用纯 Python 而不是 bash grep

模型给的 `pattern` 要拼进命令行就得转义。转义漏一次，一个 `;` 或 `$(...)`
就能执行任意命令——这是一条注入通道。

```python
rx = re.compile(pattern)      # 只能当正则用，跑不出命令
```

代价是自己写遍历（约 40 行），换来"不可能注入"。

### 两个细节

- **跳过二进制文件**：看前 1KB 有没有 NUL 字节（这是 git 的做法，比看扩展名可靠）
- **输出相对路径**：`apps/backend/app/...` 而不是 `/Users/runzhili/Desktop/...`，
  前者短 60 个字符，模型一次能看更多行

## edit：精确替换

### 接口

```
edit(path, old_text, new_text)
```

只发改动那几行。`old_text` 必须与文件内容**完全一致**（含缩进、空行）且**只出现一次**。

### 必须拒绝的三种情况

这是 `edit` 比 `write_file` 危险的地方——**它可能改错地方**。

| 情况 | 处理 | 理由 |
|---|---|---|
| 找不到 `old_text` | 报错，提示先 read_file | 文件可能已被改过，模型记的是旧内容 |
| `old_text` 出现多次 | **拒绝执行** | 随便挑一个改，等于随机破坏文件 |
| `old_text` 为空 | 拒绝 | `str.replace("")` 会在每个字符间插入，直接毁掉文件 |

第二条最关键。实测：

```
edit(old_text="beta")  → 错误：old_text 出现了 2 次，无法确定改哪一处
```

模型看到这个错误，会自己把 `old_text` 扩大几行带上上下文，然后成功。

**让工具返回有用的错误，比让它抛出异常更有效**——模型会读错误信息自我纠正。

## 现在的六个工具

| 工具 | 干什么 | 什么时候用 |
|---|---|---|
| `glob` | 找文件 | 知道文件名，不知道在哪 |
| `grep` | 搜内容 | 知道代码片段，不知道在哪个文件 |
| `read_file` | 读文件 | 看清现状 |
| `edit` | 改几行 | **改已有文件的首选** |
| `write_file` | 整篇写 | 新建文件，或整篇重写 |
| `bash` | 跑命令 | 测试、装依赖、看 git |

## 验证

```bash
cd apps/backend
uv run mini-agent "哪个文件定义了 ChatSession？用 grep 查"
uv run mini-agent "把 xxx.txt 里的 1.0.0 改成 2.0.0，用 edit"
```

界面上工具卡片收起后显示 `工具调用 1 个 · 全部成功`，点开看参数和结果。

## 测试的重点不是"能改对"，而是"该拒绝时拒绝"

`tests/test_s4_tools.py` 里有一半用例在验证**拒绝**分支：

```
test_edit_refuses_missing_text      找不到 → 拒绝，且文件一个字节都没变
test_edit_refuses_ambiguous_match   多处匹配 → 拒绝，且文件没变
test_edit_refuses_empty_old_text    空串 → 拒绝
test_edit_blocks_path_escape        越界 → 拒绝
```

**写工具时最该测的是失败路径。** 成功路径跑一次就知道对不对，
失败路径只有在真实事故里才会暴露——那时候代价就大了。
