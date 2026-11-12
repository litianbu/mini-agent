"""模型包。这里只做导出，不放定义。

为什么这么分：
    一个文件放所有表，表多了以后翻起来很累，改 A 表要和 B 表的几百行
    挤在一起看。按表拆文件，找 ChatSession 就直接打开 session.py。

用法不变：
    from app.models import Base, ChatSession, Message

注意下面这两个 import 不能删：
    SQLAlchemy 靠"类被导入过"才知道有这张表。只 import Base 的话，
    Base.metadata.create_all() 会一张表都不建。
"""

from .base import Base, LongText
from .message import Message
from .session import ChatSession

__all__ = ["Base", "LongText", "ChatSession", "Message"]
