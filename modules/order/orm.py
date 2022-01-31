from sqlalchemy import Column, String, Text, TIMESTAMP, text, Integer, Boolean, PrimaryKeyConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.declarative import declarative_base

from database.orm import DBSession

Base = declarative_base()
table_prefix = 'module_order_'
db = DBSession()
session = db.session
engine = db.engine


class OrderInfo(Base):
    __tablename__ = table_prefix + 'OrderInfo'
    id = Column(Integer, primary_key=True)
    masterId = Column(String(512))
    displayId = Column(Integer, default='0')
    orderId = Column(String(512))
    targetId = Column(String(512))
    nickname = Column(String(512))
    remark = Column(LONGTEXT if session.bind.dialect.name == 'mysql' else Text)
    finished = Column(Boolean, default=False)
    timestamp = Column(TIMESTAMP, default=text('CURRENT_TIMESTAMP'))


class GroupInfo(Base):
    __tablename__ = table_prefix + 'GroupInfo'
    targetId = Column(String(512), primary_key=True)
    masterId = Column(String(512))
    isEnabled = Column(Boolean, default=True)
    isAllowMemberQuery = Column(Boolean, default=False)
    isAllowMemberOrder = Column(Boolean, default=False)
    isAutoDelete = Column(Boolean, default=False)
    defaultOrderNum = Column(Integer, default=5)


class TargetAdmin(Base):
    """所属赋予的管理员"""
    __tablename__ = table_prefix + "GroupAdmin"
    id = Column(Integer, primary_key=True)
    senderId = Column(String(512))
    targetId = Column(String(512))


class MasterInfo(Base):
    __tablename__ = table_prefix + 'MasterInfo'
    masterId = Column(String(512), primary_key=True)
    nickname = Column(String(512))


class DeletedRecord(Base):
    __tablename__ = table_prefix + 'DeletedRecord'
    targetId = Column(String(512), primary_key=True)
    timestamp = Column(TIMESTAMP, default=text('CURRENT_TIMESTAMP'))


Base.metadata.create_all(bind=engine, checkfirst=True)
