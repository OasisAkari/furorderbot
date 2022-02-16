import re
from datetime import datetime
from typing import Union, List

import ujson as json
from tenacity import retry, stop_after_attempt

from core.elements import MessageSession
from database import session, auto_rollback_error
from modules.order.orm import OrderInfo, GroupInfo, TargetAdmin, MasterInfo, DeletedRecord, RepoInfo

from sqlalchemy.sql import func
from sqlalchemy import or_


class QueriedInfo:
    def __init__(self, id, remark, ts, queue, nickname, orderId, repoId, finished):
        self.id = id
        self.nickname = nickname
        self.orderId = orderId
        self.remark = remark
        self.ts = ts
        self.queue = queue
        self.finished = finished
        self.repoId = repoId


class QueriedInfoStack:
    def __init__(self, queried_infos: List[QueriedInfo] = None):
        self.queried_infos = queried_infos


class OrderDBUtil:
    class Order:
        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def edit(id, repoId: list, column: str, value):
            filters = [OrderInfo.id == id]
            ors = []
            for x in repoId:
                ors.append(OrderInfo.repoId == x)
            filters.append(or_(*ors))
            query = session.query(OrderInfo) \
                .filter(*filters).first()
            if query is not None:
                original = getattr(query, column)
                setattr(query, column, value)
                session.commit()
                session.expire_all()
                return original
            return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add(order_info: Union[OrderInfo]):
            session.add(order_info)
            session.commit()
            q = session.query(OrderInfo).filter(OrderInfo.orderId == order_info.orderId,
                                                OrderInfo.timestamp == order_info.timestamp).first()
            return q.id

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def remove(id, repoId: list, orderId):
            filters = [OrderInfo.id == id,
                       OrderInfo.orderId == orderId]
            q = session.query(OrderInfo).filter().first()
            ors = []
            for x in repoId:
                ors.append(OrderInfo.repoId == x)
            filters.append(or_(*ors))
            if q:
                session.delete(q)
                session.commit()
                return True
            else:
                return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def finish(id, repoId: list, orderId=None):
            filters = [OrderInfo.id == id,
                       OrderInfo.finished == False]
            ors = []
            for x in repoId:
                ors.append(OrderInfo.repoId == x)
            filters.append(or_(*ors))
            if orderId is not None:
                filters.append(OrderInfo.orderId == orderId)
            q = session.query(OrderInfo).filter(*filters).first()
            if q:
                q.finished = True
                session.commit()
                return True
            else:
                return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def undo_finish(id, repoId: list, orderId=None):
            filters = [OrderInfo.id == id,
                       OrderInfo.finished == True]
            ors = []
            for x in repoId:
                ors.append(OrderInfo.repoId == x)
            filters.append(or_(*ors))
            if orderId is not None:
                filters.append(OrderInfo.orderId == orderId)
            q = session.query(OrderInfo).filter(*filters).first()
            if q:
                q.finished = False
                session.commit()
                return True
            else:
                return False

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query(orderId, mode=0, remark=None, showfinished=False, repoId: list = None) -> QueriedInfoStack:
            filters = [OrderInfo.orderId == orderId]
            if not showfinished:
                filters.append(OrderInfo.finished == False)
            if remark is not None:
                filters.append(OrderInfo.remark.like(f'%{remark}%'))
            qm_filter = []
            if repoId is not None:
                ors = []
                for x in repoId:
                    ors.append(OrderInfo.repoId == x)
                filters.append(or_(*ors))
                qm_filter.append(or_(*ors))
            query = session.query(OrderInfo).filter(*filters).all()
            if query is None:
                return QueriedInfoStack()
            queried_infos = []
            for q in query:
                queryAll = session.query(OrderInfo).filter(OrderInfo.finished == False,
                                                           OrderInfo.id < q.id, *qm_filter).all()
                queue = 0
                if queryAll is not None:
                    queue = len(queryAll)
                queried_infos.append(QueriedInfo(id=q.id, remark=q.remark, ts=q.timestamp, queue=queue,
                                                 nickname=q.nickname, orderId=q.orderId, finished=q.finished,
                                                 repoId=q.repoId))
            if mode == 1:
                queried_infos.reverse()
            return QueriedInfoStack(queried_infos)

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query_all(mode, remark=None, showfinished=False, repoId: list = None) -> QueriedInfoStack:
            if mode == 0:
                o = OrderInfo.id
            else:
                o = - OrderInfo.id
            filters = []
            if not showfinished:
                filters.append(OrderInfo.finished == False)
            if remark is not None:
                filters.append(OrderInfo.remark.like(f'%{remark}%'))
            if repoId is not None:
                ors = []
                for x in repoId:
                    ors.append(OrderInfo.repoId == x)
                filters.append(or_(*ors))
            queryAll = session.query(OrderInfo).filter(*filters).order_by(o).all()
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
                    lst.append(QueriedInfo(id=q.id, remark=q.remark, ts=q.timestamp, queue=queue,
                                           nickname=q.nickname, orderId=q.orderId, finished=q.finished,
                                           repoId=q.repoId))
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
                session.add(RepoInfo(createdBy=self.targetId, masterId=masterId))
                session.commit()
                queryRepoId = session.query(RepoInfo).filter_by(createdBy=self.targetId).first()
                session.add(
                    GroupInfo(targetId=self.targetId, isEnabled=True, bindRepos=f'[{queryRepoId.id}]'))
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
        def add_bind_repos(self, repoId):
            exists = self.query()
            if exists is not None:
                load = json.loads(exists.bindRepos)
                load.append(repoId)
                dump = json.dumps(load)
                exists.bindRepos = dump
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def remove_bind_repos(self, repoId):
            exists = self.query()
            if exists is not None:
                load = json.loads(exists.bindRepos)
                load.remove(repoId)
                dump = json.dumps(load)
                exists.bindRepos = dump
            session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def get_bind_repos(self):
            exists = self.query()
            if exists is not None:
                return json.loads(exists.bindRepos)
            else:
                return []

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

    class Repo:
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def __init__(self, repoId):
            self.repoId = repoId

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def query(self) -> Union[RepoInfo, None]:
            return session.query(RepoInfo).filter_by(id=self.repoId).first()

        @staticmethod
        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def get_repo_id_by_createdBy(createdBy):
            return session.query(RepoInfo).filter_by(createdBy=createdBy).first().id

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
        def check_TargetAdmin(self, repoId):
            query = session.query(TargetAdmin).filter_by(senderId=self.senderId, repoId=repoId).first()
            if query is not None:
                return query
            return False

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def add_TargetAdmin(self, repoId):
            if not self.check_TargetAdmin(repoId):
                session.add_all([TargetAdmin(senderId=self.senderId, repoId=repoId)])
                session.commit()
            return True

        @retry(stop=stop_after_attempt(3))
        @auto_rollback_error
        def remove_TargetAdmin(self, repoId):
            query = self.check_TargetAdmin(repoId)
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
        g = session.query(GroupInfo).filter_by(targetId=targetId).all()
        for x in g:
            session.delete(x)
            session.commit()
        t = session.query(RepoInfo).filter_by(createdBy=targetId).all()
        for x in t:
            t = session.query(TargetAdmin).filter_by(targetId=x.createdBy).all()
            for y in t:
                session.delete(y)
                session.commit()
            o = session.query(OrderInfo).filter_by(targetId=x.createdBy).all()
            for y in o:
                session.delete(y)
                session.commit()
            session.delete(x)
            session.commit()
