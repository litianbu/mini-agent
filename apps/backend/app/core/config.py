"""配置：全部来自环境变量 / 仓库根目录的 .env。

为什么值得单独一个文件？
  因为"读配置"这件事会扩散 —— 一旦有人在自己模块里直接
  os.getenv("DEEPSEEK_API_KEY")，配置就有了两个来源。
  唯一的入口 + 一个 Settings 对象，后面加任何新配置都只改这里。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root(start: Path) -> Path:
    """向上找，直到看见 pnpm-workspace.yaml（仓库根的标记）。"""
    for path in (start, *start.parents):
        if (path / "pnpm-workspace.yaml").exists():
            return path
    return Path.cwd()


REPO_ROOT = _find_repo_root(Path(__file__).resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 默认值刻意留成"公开的默认值 + 空 key"。
    # 真实地址和 key 一律放 .env（已被 .gitignore 忽略）——
    # **密钥永远不要写进源码**：一旦进过版本库或分享出去，
    # 就只能当作已泄露，去后台吊销重发。
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    # 子 agent 用的模型（S9）。留空 = 和主模型一样 ——
    # 只有一套模型的人不用改任何配置；想省钱时填个便宜模型名即可。
    llm_model_sub: str = ""
    thinking: bool = False
    mock: bool = False
    port: int = 8787

    # ── 上下文预算 ─────────────────────────────────────
    # 一次请求最多带多少 token 的历史。超了就从最老的开始丢。
    #
    # 为什么是 16000 而不是"窗口大小乘个比例"？
    #   1. 网关没告诉我们窗口多大（/models 不返回 context_length）
    #   2. 实测一次普通问答约 1400 token 入、带工具约 3100
    #   3. 16k 大约能装 5-10 轮带工具的对话，够用，且便宜
    # 真调大也能改：.env 里设 CONTEXT_BUDGET=32000 即可，不用动代码。
    context_budget: int = 16000

    # ── 超时 ───────────────────────────────────────────
    # 两个超时管的是**不同的事**，不是替代关系：
    #
    #   llm_read_timeout  两帧之间的最大间隔。
    #                     模型正常思考时可能十几秒不出字，所以不能设太小；
    #                     又必须有值 —— 网关半死不活时（TCP 连着、偶尔发心跳、
    #                     就是不输出正文）没这个就会永远挂着。
    #
    #   agent_timeout     整件事的总时长（含所有轮次的工具执行）。
    #                     httpx 的超时只管单次读写，管不了"这个请求已经跑了 20 分钟"。
    #
    # 为什么总时长是 300 秒：典型一轮 = 模型 5-20 秒 + 工具 1-10 秒，
    # 8 轮大约 2-4 分钟。跑长任务（比如全量构建）就调大：
    #   .env 里设 AGENT_TIMEOUT=900
    llm_read_timeout: float = 60.0
    agent_timeout: float = 300.0

    # 三个都认，谁先有值用谁 —— 这样换服务商不用改代码
    deepseek_api_key: str = ""
    llm_api_key: str = ""
    openai_api_key: str = ""

    # ── 数据库 ──────────────────────────────────────────
    # 分成几项写，是为了让"哪里该改什么"一眼看明白：
    #   地址/端口/用户/库名 = 公共配置，写在这里
    #   密码               = 机密，只从 .env 读，源码里永远是空
    #
    # 换成 PostgreSQL 只改 db_driver 一行：
    #   mysql+aiomysql / postgresql+asyncpg / sqlite+aiosqlite
    db_driver: str = "mysql+aiomysql"
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_name: str = "mini_agent"
    db_password: str = ""  # ← 只在 .env 里出现

    # 想用完整 URL（CI、云上、SQLite 文件库）就填这项，优先于上面几项
    database_url: str = ""

    # 调试用：True 会把每条 SQL 打到日志
    db_echo: bool = False

    # ── 向量化（S12-2）─────────────────────────────────
    # RAG 的第二段：文本 → 向量。
    #
    # 字段名刻意和真实项目保持一致（EMBEDDING_API_KEY / EMBEDDING_API_BASE），
    # 这样 .env 可以直接从工作项目拷过来，不用改名对照。
    #
    # 为什么维度必须写进配置、而不是写死在代码里：
    #   维度是**数据库集合的 schema 决定的**。先建了 1024 维的集合，
    #   之后换成 768 维的模型，灌进去会直接报错 —— 而且是灌到一半才发现。
    #   写进配置就能在调用时立刻校验，错误停在最早的地方。
    embedding_api_base: str = "https://mg.biomap-int.com/v1"
    embedding_api_key: str = ""  # ← 只在 .env 里出现
    embedding_model: str = "bge-m3"
    embedding_dimension: int = 1024
    # 一次发多少条。太大：网关拒绝或超时；太小：请求数多、慢。
    embedding_batch_size: int = 64
    embedding_timeout: float = 30.0

    @property
    def api_key(self) -> str:
        return self.deepseek_api_key or self.llm_api_key or self.openai_api_key

    @property
    def is_mock(self) -> bool:
        """没有 key 就自动进假模型模式，所以项目开箱即跑。"""
        return self.mock or not self.api_key

    @property
    def db_url(self) -> str:
        """拼出连接串。填了 database_url 就直接用，不拼。"""
        if self.database_url:
            return self.database_url
        if not self.db_password:
            return ""
        # 密码必须转义：@ / # / : 这些字符会直接把 URL 拆坏。
        # 比如密码是 p@ss，不转义就变成 user:p@ss@host —— 解析必然出错。
        return (
            f"{self.db_driver}://{self.db_user}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def embedding_enabled(self) -> bool:
        """没配 key 就明确报错，而不是悄悄返回零向量。

        零向量灌进库里不会报错，但检索结果是随机的 ——
        这种"看起来在工作"的故障最难查。
        """
        return bool(self.embedding_api_key)

    @property
    def db_enabled(self) -> bool:
        """没配密码也没填 URL 就跳过数据库。

        和 is_mock 一个思路：默认能跑，想要完整能力再配。
        """
        return bool(self.db_url)


settings = Settings()