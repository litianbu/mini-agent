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
    thinking: bool = False
    mock: bool = False
    port: int = 8787

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
    def db_enabled(self) -> bool:
        """没配密码也没填 URL 就跳过数据库。

        和 is_mock 一个思路：默认能跑，想要完整能力再配。
        """
        return bool(self.db_url)


settings = Settings()