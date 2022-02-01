import re
from datetime import datetime
from typing import Union, List

import ujson as json
from tenacity import retry, stop_after_attempt

from core.elements import MessageSession
from database import session, auto_rollback_error
from modules.order.orm import OrderInfo, GroupInfo, TargetAdmin, MasterInfo, DeletedRecord

from sqlalchemy.sql import func


class QueriedInfo:
    def __init__(self, displayId, remark, ts, queue, nickname, orderId):
        self.displayId = displayId
        self.nickname = nickname
        self.orderId = orderId
        self.remark = remark
        self.ts = ts
        self.queue = queue


class QueriedInfoStack:
    def __init__(self, queried_infos: List[QueriedInfo] = None):
        self.queried_infos = queried_infos


class OrderDBUtil:
    class Order:
        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def edit(masterId, displayId, column: str, value):
            query = session.query(OrderInfo) \
                .filter_by(masterId=masterId, displayId=displayId).first()
            if query is not None:
                setattr(query, column, value)
                session.commit()
                session.expire_all()
                return True
            return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add(order_info: Union[OrderInfo]):
            q = session.query(OrderInfo.masterId, func.max(OrderInfo.displayId)) \
                .filter(OrderInfo.masterId == order_info.masterId).first()
            displayId = 0
            if q[1] is not None:
                displayId = q[1] + 1
            order_info.displayId = displayId
            session.add(order_info)
            session.commit()

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def finish(display_id, master_id, order_id=None):
            if order_id is not None:
                q = session.query(OrderInfo).filter(OrderInfo.displayId == display_id,
                                                    OrderInfo.masterId == master_id,
                                                    OrderInfo.orderId == order_id,
                                                    OrderInfo.finished == False).first()
            else:
                q = session.query(OrderInfo).filter(OrderInfo.displayId == display_id,
                                                    OrderInfo.masterId == master_id,
                                                    OrderInfo.finished == False).first()
            if q:
                q.finished = True
                session.commit()
                return True
            else:
                return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def undo_finish(display_id, master_id, order_id=None):
            if order_id is not None:
                q = session.query(OrderInfo).filter(OrderInfo.displayId == display_id,
                                                    OrderInfo.masterId == master_id,
                                                    OrderInfo.orderId == order_id,
                                                    OrderInfo.finished == True).first()
            else:
                q = session.query(OrderInfo).filter(OrderInfo.displayId == display_id,
                                                    OrderInfo.masterId == master_id,
                                                    OrderInfo.finished == True).first()
            if q:
                q.finished = False
                session.commit()
                return True
            else:
                return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query(orderId, masterId, mode=0, remark=None) -> QueriedInfoStack:
            qmax = session.query(OrderInfo.masterId, func.max(OrderInfo.displayId)) \
                .filter(OrderInfo.masterId == masterId).first()
            if qmax[1] is None:
                return QueriedInfoStack()
            if remark is None:
                query = session.query(OrderInfo).filter_by(orderId=orderId, finished=False).all()
            else:
                query = session.query(OrderInfo).filter(OrderInfo.orderId == orderId, OrderInfo.finished == False,
                                                        OrderInfo.remark.like(f'%{remark}%')).all()
            if query is None:
                return QueriedInfoStack()
            queried_infos = []
            for q in query:
                queryAll = session.query(OrderInfo).filter(OrderInfo.masterId == masterId,
                                                           OrderInfo.finished == False,
                                                           OrderInfo.displayId < q.displayId).all()
                queue = 0
                if queryAll is not None:
                    queue = len(queryAll)
                queried_infos.append(QueriedInfo(q.displayId, q.remark, q.timestamp, queue, q.nickname, q.orderId))
            if mode == 1:
                queried_infos.reverse()
            return QueriedInfoStack(queried_infos)

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query_all(masterId, mode, remark=None) -> QueriedInfoStack:
            if mode == 0:
                o = OrderInfo.displayId
            else:
                o = - OrderInfo.displayId
            if remark is None:
                queryAll = session.query(OrderInfo).filter(OrderInfo.masterId == masterId,
                                                           OrderInfo.finished == False).order_by(o).all()
            else:
                queryAll = session.query(OrderInfo).filter(OrderInfo.masterId == masterId,
                                                           OrderInfo.finished == False,
                                                           OrderInfo.remark.like(f'%{remark}%')).order_by(o).all()
            if queryAll is None:
                return QueriedInfoStack()
            else:
                lst = []
                i = 0
                for q in queryAll:
                    i += 1
                    allqueue = len(queryAll)
                    if mode == 0:
                        queue = i
                    else:
                        queue = allqueue - i
                    lst.append(QueriedInfo(q.displayId, q.remark, q.timestamp, queue, q.nickname, q.orderId))
                return QueriedInfoStack(lst)

    class Group:
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def __init__(self, targetId):
            self.targetId = targetId

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query(self) -> Union[GroupInfo, None]:
            return session.query(GroupInfo).filter_by(targetId=self.targetId).first()

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def enable(self, masterId):
            exists = self.query()
            if exists is not None:
                exists.masterId = masterId
                exists.isEnabled = True
            else:
                session.add(GroupInfo(targetId=self.targetId, masterId=masterId, isEnabled=True))
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def disable(self):
            exists = self.query()
            if exists is not None:
                exists.enable = False
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def edit(self, column: str, value):
            query = self.query()
            if query is not None:
                setattr(query, column, value)
                session.commit()
                session.expire_all()
                return True
            return False

    class Master:
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def __init__(self, masterId):
            self.masterId = masterId

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query(self) -> Union[GroupInfo, None]:
            return session.query(MasterInfo).filter_by(masterId=self.masterId).first()

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add(self, nickname):
            exists = self.query()
            if exists is not None:
                exists.nickname = nickname
            else:
                session.add(MasterInfo(masterId=self.masterId, nickname=nickname))
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def edit(self, column: str, value):
            query = self.query()
            if query is not None:
                setattr(query, column, value)
                session.commit()
                session.expire_all()
                return True
            return False

    class SenderInfo:
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def __init__(self, senderId):
            self.senderId = senderId

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def check_TargetAdmin(self, targetId):
            query = session.query(TargetAdmin).filter_by(senderId=self.senderId, targetId=targetId).first()
            if query is not None:
                return query
            return False

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add_TargetAdmin(self, targetId):
            if not self.check_TargetAdmin(targetId):
                session.add_all([TargetAdmin(senderId=self.senderId, targetId=targetId)])
                session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def remove_TargetAdmin(self, targetId):
            query = self.check_TargetAdmin(targetId)
            if query:
                session.delete(query)
                session.commit()
            return True

    class Delete:
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def __init__(self, targetId):
            self.targetId = targetId

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add(self):
            session.add(DeletedRecord(targetId=self.targetId))
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def remove(self):
            query = session.query(DeletedRecord).filter_by(targetId=self.targetId).first()
            if query is not None:
                session.delete(query)
                session.commit()
            return True

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def show():
            query = session.query(DeletedRecord).all()
            return query

    @staticmethod
    @retry(stop=stop_after_attempt(3))
    @auto_rollback_error
    def delete_all_data_by_targetId(targetId):
        o = session.query(OrderInfo).filter_by(targetId=targetId).all()
        for x in o:
            session.delete(x)
            session.commit()
        g = session.query(GroupInfo).filter_by(targetId=targetId).all()
        for x in g:
            session.delete(x)
            session.commit()
        t = session.query(TargetAdmin).filter_by(targetId=targetId).all()
        for x in t:
            session.delete(x)
            session.commit()
