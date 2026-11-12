"""模型基础设施：Base 和各表共用的列类型。

放这里的都是"所有模型都要用的东西"，不含任何具体表。
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有模型的基类。SQLAlchemy 2.x 的写法：继承它就会自动建映射。"""


# MySQL 的 TEXT 上限是 64KB，长对话很容易超。
# 用 with_variant 只对 MySQL 换成 LONGTEXT（4GB），
# SQLite 等其它库仍用普通 Text —— 换数据库不用改模型代码。
LongText = Text().with_variant(LONGTEXT, "mysql")
