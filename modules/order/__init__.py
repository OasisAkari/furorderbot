import datetime
import re
import traceback
from typing import Dict
from copy import deepcopy

import ujson as json

from core.component import on_command, on_regex, on_schedule
from core.elements import MessageSession, IntervalTrigger, FetchTarget
from .dbutils import OrderDBUtil, QueriedInfoStack
from .orm import OrderInfo
from config import Config


def convert_cqat(s):
    match = re.match(r'\[CQ:at,qq=(.*?)]', s)
    if match:
        return match.group(1)
    return s


async def check_admin(msg: MessageSession, repoId):
    repo_info = OrderDBUtil.Repo(repoId).query()
    if repo_info is None:
        return False
    else:
        if repo_info.masterId == msg.target.senderId:
            return True
        else:
            if OrderDBUtil.Sender(msg.target.senderId).check_TargetAdmin(repoId):
                return True
    return False


async def sendMessage(msg: MessageSession, msgchain, quote=True, auto_delete=False):
    ginfo = OrderDBUtil.Group(targetId=msg.target.targetId)
    query = ginfo.query()
    m = await msg.sendMessage(msgchain, quote=quote)
    if auto_delete:
        await msg.sleep(60)
        await m.delete()


undo_actions: Dict[str, list] = {}


def add_undo_action(id, action):
    if id not in undo_actions:
        undo_actions[id] = []
    undo_actions[id].append(action)
    length = len(undo_actions[id])
    if length > 3:
        undo_actions[id].pop(0)


def get_base_repo(msg):
    repos = OrderDBUtil.Group(targetId=msg.target.targetId).get_bind_repos()
    bind_repo = repos[0]
    if len(repos) == 0:
        return False
    elif len(repos) > 1:
        for r in repos:
            q = OrderDBUtil.Repo(r).query()
            if q.createdBy == msg.target.targetId:
                bind_repo = r
    return bind_repo


def infos(func):
    def wrapper(msg):
        group_query = OrderDBUtil.Group(targetId=msg.target.targetId)
        if group_query is None or not group_query.query().isEnabled:
            async def empty(*args):
                pass

            return empty(msg)
        base_repo_query = OrderDBUtil.Repo(get_base_repo(msg))
        return func(msg, group_query, base_repo_query)

    return wrapper


async def sendSlicedMessages(msg: MessageSession, msgs: list):
    if len(msgs) > 1:
        if msg.Feature.forward and msg.target.targetFrom == 'QQ|Group':
            try:
                nodelist = []
                qq_account = Config('qq_account')
                for x in msgs:
                    nodelist.append({
                        "type": "node",
                        "data": {
                            "uin": qq_account,
                            "name": f"下单信息",
                            "content": [
                                {"type": "text", "data": {"text": x}}]
                        }
                    })
                await msg.fake_forward_msg(nodelist)
                legacy = False
            except Exception:
                traceback.print_exc()
                await msg.sendMessage('无法发送转发消息，尝试直接发送消息中...')
                legacy = True
        else:
            legacy = True
        if legacy:
            for x in msgs:
                await sendMessage(msg, x)
    else:
        await sendMessage(msg, msgs[0])


ordr = on_regex('ordr', desc='下单 [<QQ>] <分类> <备注>\n'
                             '查单 [<QQ/分类/备注>] [正序/倒序]\n'
                             '完稿 <#单号/QQ>\n'
                             '编辑 <#单号> <备注>\n'
                             '删除 <#单号>\n'
                             '撤回')


@ordr.handle(r'^下单 (.*)')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query = brinfo.query()
    bind_repo = query.id
    base_category = query.defaultCategoryId
    categories = OrderDBUtil.Category(bind_repo).get_all_category_by_name()
    categories_ = OrderDBUtil.Category(bind_repo).get_all_category_by_id()
    if not query.isAllowMemberOrder:
        if not await check_admin(msg, bind_repo):
            return await sendMessage(msg, '你没有使用该命令的权限，请联系排单管理员执行。')
    sp = msg.matched_msg.group(1).split(" ")
    senderId = None
    remark = []
    nickname = '???'
    for x in sp:
        id = convert_cqat(x)
        if id.isdigit():
            try:
                verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
                nickname = verify['nickname']
                senderId = msg.target.senderFrom + '|' + id
            except Exception:
                traceback.print_exc()
                remark.append(x)
        else:
            remark.append(x)
    if len(remark) > 1:
        category = remark[0]
        remark = ' '.join(remark[1:])
    else:
        category = remark[0]
        remark = ''
    category_id = base_category
    if category in categories:
        category_id = categories[category]
    else:
        confirm = await msg.waitConfirm(f'没有找到分类{category}，是否作为备注写入？')
        if confirm:
            remark = category
        else:
            return await sendMessage(msg, '下单操作已取消。')
    if senderId is not None:
        if not await check_admin(msg, bind_repo):
            if senderId != msg.session.sender:
                return await sendMessage(msg, '你只可以为自己下单，请联系排单管理员执行。')
        displayId = OrderDBUtil.Order.add(
            OrderInfo(orderId=senderId, repoId=bind_repo,
                      remark=remark, nickname=nickname, categoryId=category_id))

        async def undo():
            OrderDBUtil.Order.remove(id=id, orderId=senderId)
            await sendMessage(msg, f'已撤回 #{displayId} 的下单状态。')

        add_undo_action(msg.target.senderId, undo)
        await sendMessage(msg, f'已添加 {nickname} 的 {remark}，分类为{categories_[category_id]}（#{displayId}）。')
    else:
        displayId = OrderDBUtil.Order.add(
            OrderInfo(orderId=msg.target.senderId, repoId=bind_repo,
                      remark=remark, nickname=msg.target.senderName, categoryId=category_id))

        async def undo():
            OrderDBUtil.Order.remove(id=id, repoId=bind_repo,
                                     orderId=msg.target.senderId)
            await sendMessage(msg, f'已撤回 #{displayId} 的下单状态。')

        add_undo_action(msg.target.senderId, undo)
        await sendMessage(msg, f'已添加 {msg.target.senderName} 的 {remark}，分类为{categories_[category_id]}（#{displayId}）')


@ordr.handle(r'^查单$')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repos = ginfo.get_bind_repos()
    msgs = []
    for x in query_repos:
        query_repo = OrderDBUtil.Repo(x).query()
        defaultOrderNum = query_repo.defaultOrderNum
        categories = OrderDBUtil.Category(x).get_all_category_by_id()
        categories_ = OrderDBUtil.Category(x).get_all_category_by_name()
        if msg.target.targetId != query_repo.createdBy:
            createdBy = re.sub(r'' + msg.target.targetFrom + r'\|', '', query_repo.createdBy)
            m = f'仓库{query_repo.id}（创建自{createdBy}）的下单信息：\n'
        else:
            m = f'仓库{query_repo.id}（创建自本群）的下单信息：\n'
        if not await check_admin(msg, query_repo.id):
            if not query_repo.isAllowMemberQuery:
                msgs.append(m + '你没有使用该命令的权限。')
                continue
            query = OrderDBUtil.Order.query_all(orderId=msg.target.senderId, repoId=[x])
            if query_repo.isNeedClassify:
                i = 0
                categories_dict = {}
                for q in query.queried_infos:
                    if q.categoryId in categories_dict:
                        categories_dict[q.categoryId].append(q)
                    else:
                        categories_dict[q.categoryId] = [q]
                for c in categories_dict:
                    cname = categories[c]
                    m += f'{cname} 分类：\n'
                    for q in categories_dict[c]:
                        i += 1
                        m += f'#{q.id} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单\n'
                msgs.append(f'您共有{i}个活跃单：' + m)
            else:
                msg_lst = []
                for q in query.queried_infos:
                    msg_lst.append(f'#{q.id} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单')
                if len(msg_lst) != 0:
                    m += f'您共有{len(msg_lst)}个活跃单：\n  ' + '\n  '.join(msg_lst)
                    msgs.append(m)
                else:
                    msgs.append(m + '您没有任何的活跃单。')
        else:
            query = OrderDBUtil.Order.query_all(mode=1, repoId=[x])
            msg_lst = []
            for q in query.queried_infos:
                orderId = q.orderId
                ma = re.match(r'' + msg.target.senderFrom + r'\|(.*)', orderId)
                if ma:
                    orderId = ma.group(1)
                msg_lst.append(
                    f'#{q.id} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
            if len(msg_lst) != 0:
                cm = []
                for c in categories:
                    i = 0
                    for cc in query.queried_infos:
                        if cc.categoryId == c:
                            i += 1
                    if i != 0:
                        cm.append(f'{i}{categories[c]}')
                cm = '，'.join(cm)
                if len(msg_lst) > defaultOrderNum:
                    msg_lst = msg_lst[:defaultOrderNum]
                    m += f'最近下单的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单：{cm}）：\n  ' + '\n  '.join(msg_lst)
                else:
                    m += f'最近下单的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单：{cm}）：\n  ' + '\n  '.join(
                        msg_lst)
                msgs.append(m)
            else:
                msgs.append(m + '没有任何的活跃单。')
    await sendSlicedMessages(msg, msgs)


@ordr.handle(r'^查单 (.*)$')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repos = ginfo.get_bind_repos()
    msgs = []
    for repo in query_repos:
        query_repo = OrderDBUtil.Repo(repo).query()
        categories = OrderDBUtil.Category(repo).get_all_category_by_id()
        categories_ = OrderDBUtil.Category(repo).get_all_category_by_name()
        if msg.target.targetId != query_repo.createdBy:
            createdBy = re.sub(r'' + msg.target.targetFrom + r'\|', '', query_repo.createdBy)
            m = f'仓库{query_repo.id}（创建自{createdBy}）的下单信息：\n'
        else:
            m = f'仓库{query_repo.id}（创建自本群）的下单信息：\n'
        if not await check_admin(msg, repo):
            if not query_repo.isAllowMemberQuery:
                msgs.append(m + '你没有使用该命令的权限。')
                continue
        defaultOrderNum = query_repo.defaultOrderNum
        split = msg.matched_msg.group(1).split(' ')
        mode = 0
        query_string = []
        orderId = None
        nickname = '???'
        for x in split:
            x = convert_cqat(x)
            if x.isdigit():
                try:
                    verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=x)
                    nickname = verify['nickname']
                    orderId = msg.target.senderFrom + '|' + x
                except Exception:
                    traceback.print_exc()
                    query_string = x
            else:
                if x == '倒序':
                    mode = 1
                elif x == '正序':
                    mode = 0
                else:
                    query_string.append(x)
        query_string = ' '.join(query_string)
        if orderId is None:
            if query_string == '':
                query = OrderDBUtil.Order.query_all(mode=mode, repoId=[repo])
                msg_lst = []
                for q in query.queried_infos:
                    orderId = q.orderId
                    ma = re.match(r'QQ\|(.*)', orderId)
                    if ma:
                        orderId = ma.group(1)
                    msg_lst.append(
                        f'#{q.id} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单')
                if query_repo.isNeedClassify:
                    cm = []
                    for c in categories:
                        i = 0
                        for cc in query.queried_infos:
                            if cc.categoryId == c:
                                i += 1
                        if i != 0:
                            cm.append(f'{i}{categories[c]}')
                    cm = '：' + '，'.join(cm)
                else:
                    cm = ''
                if len(msg_lst) != 0:
                    if len(msg_lst) > defaultOrderNum:
                        msg_lst = msg_lst[:defaultOrderNum]
                        if mode == 0:
                            m += f'接下来的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单{cm}）：\n  ' + '\n  '.join(msg_lst)
                        else:
                            m += f'最近下单的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单{cm}）：\n  ' + '\n  '.join(
                                msg_lst)
                    else:
                        if mode == 0:
                            m += f'接下来的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单{cm}）：\n  ' + '\n  '.join(
                                msg_lst)
                        else:
                            m += f'最近下单的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单{cm}）：\n  ' + '\n  '.join(
                                msg_lst)
                    msgs.append(m)
                else:
                    msgs.append(m + f'没有查询到关于 {query_repo.masterId} 主人的任何单。')
            else:
                msg_lst_category = []
                msg_lst_remark = []
                query_category = QueriedInfoStack()
                if query_string in categories_:
                    query_category = OrderDBUtil.Order.query_all(mode=mode, repoId=[repo],
                                                                 categoryId=categories_[query_string])

                    for q in query_category.queried_infos:
                        orderId = q.orderId
                        ma = re.match(r'QQ\|(.*)', orderId)
                        if ma:
                            orderId = ma.group(1)
                        msg_lst_category.append(
                            f'#{q.id} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')

                query = OrderDBUtil.Order.query_all(mode=mode, remark=query_string, repoId=[repo])
                if len(query.queried_infos) != 0:
                    for q in query.queried_infos:
                        orderId = q.orderId
                        ma = re.match(r'QQ\|(.*)', orderId)
                        if ma:
                            orderId = ma.group(1)
                        msg_lst_remark.append(
                            f'#{q.id} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')

                if len(msg_lst_category) != 0:
                    showtickets = len(query_category.queried_infos)
                    if len(msg_lst_category) > defaultOrderNum:
                        msg_lst_category = msg_lst_category[:defaultOrderNum]
                        showtickets = defaultOrderNum
                    if mode == 0:
                        ms = m + f'{query_string}中最近的{showtickets}个单子（共{len(query_category.queried_infos)}单，正序）：\n  ' + '\n  '.join(
                            msg_lst_category)
                    else:
                        ms = m + f'{query_string}中最近的{showtickets}个单子（{len(query_category.queried_infos)}单，倒序）：\n  ' + '\n  '.join(
                            msg_lst_category)
                    msgs.append(ms)
                if len(msg_lst_remark) != 0:
                    if mode == 0:
                        ms = m + f'{query_string}搜索到如下{len(query.queried_infos)}个结果（正序）：\n  ' + '\n  '.join(msg_lst_remark)
                    else:
                        ms = m + f'{query_string}搜索到如下{len(query.queried_infos)}个结果（倒序）：\n  ' + '\n  '.join(msg_lst_remark)
                    msgs.append(ms)

                if len(msg_lst_category) == 0 and len(msg_lst_remark) == 0:
                    msgs.append(m + f'没有查询到关于 {query_string} 分类/备注的任何单。')

        else:
            if query_string == '':
                query = OrderDBUtil.Order.query_all(orderId=orderId, mode=mode, repoId=[repo])
                msg_lst = []
                for q in query.queried_infos:
                    msg_lst.append(f'#{q.id} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单')
                if len(msg_lst) != 0:
                    m += f'{nickname}有如下{len(msg_lst)}个单子：\n  ' + '\n  '.join(msg_lst)
                    msgs.append(m)
                else:
                    msgs.append(m + f'{nickname}没有任何的活跃单。')
            else:
                query = OrderDBUtil.Order.query_all(orderId=orderId, mode=mode,
                                                    remark=query_string, repoId=[repo])
                msg_lst = []
                for q in query.queried_infos:
                    msg_lst.append(f'#{q.id} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单')
                if len(msg_lst) != 0:
                    m = f'{nickname}有如下{len(msg_lst)}个有关 {query_string} 单子：\n  ' + '\n  '.join(msg_lst)
                    msgs.append(m)
                else:
                    msgs.append(m + f'{nickname}没有任何活跃的有关 {query_string} 的单子。')
    await sendSlicedMessages(msg, msgs)


@ordr.handle(r'^完稿 (.*)$')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    bind_repos = ginfo.get_bind_repos()
    repos = []
    repos_createdBy = {}
    for repo in bind_repos:
        if await check_admin(msg, repo):
            repos.append(repo)
    if len(repos) == 0:
        return await sendMessage(msg, '你没有此群绑定的所有仓库中任何一个仓库的管理员权限，无法执行完稿命令。')
    for r in repos:
        repos_createdBy[r] = OrderDBUtil.Repo(r).query().createdBy
    id = convert_cqat(msg.matched_msg.group(1))
    if id.isdigit():
        try:
            verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
            nickname = verify['nickname']
            orderId = msg.target.senderFrom + '|' + id
        except Exception:
            traceback.print_exc()
            return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')
        query = OrderDBUtil.Order.query_all(orderId=orderId, mode=0, repoId=repos)
        msg_lst = []
        displayIds = []
        for q in query.queried_infos:
            displayIds.append(q.id)
            ms = ''
            if repos_createdBy[int(q.repoId)] != msg.target.targetId:
                ms += '（来自' + re.sub(r'' + msg.target.targetFrom + r'\|', '', repos_createdBy[int(q.repoId)]) + '）'
            msg_lst.append(f'#{q.id} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]' + ms)
        if len(msg_lst) != 0:
            m = f'{nickname}有如下{len(msg_lst)}个单子：\n  ' + '\n  '.join(msg_lst)

            async def confirm(msg: MessageSession):
                w = await msg.waitAnything()
                m = re.match(r'#(.*)', w)
                if m:
                    if int(m.group(1)) in displayIds:
                        fin = OrderDBUtil.Order.finish(orderId=orderId,
                                                       id=m.group(1), repoId=repos)
                        if fin:
                            msg_ = f'成功标记#{m.group(1)}为结单状态，如需撤回，请发送“撤回”'

                            async def undo():
                                if OrderDBUtil.Order.undo_finish(orderId=orderId,
                                                                 id=m.group(1), repoId=repos):
                                    msg_ = f'成功撤回#{m.group(1)}的结单状态。'
                                    await sendMessage(msg, msg_, quote=False)

                            add_undo_action(msg.target.senderId, undo)
                        else:
                            msg_ = f'未找到#{m.group(1)}，请检查输入。'
                        await sendMessage(msg, msg_, quote=False)
                    else:
                        await sendMessage(msg, f'未找到#{m.group(1)}，请检查输入。')
                    await confirm(msg)
                else:
                    if w == '全部':
                        for q in query.queried_infos:
                            OrderDBUtil.Order.finish(orderId=orderId,
                                                     id=q.id, repoId=repos)
                        msg_ = f'成功标记{nickname}的所有单号为结单状态，如需撤回，请发送“撤回”'

                        async def undo():
                            for q in query.queried_infos:
                                OrderDBUtil.Order.undo_finish(orderId=orderId,
                                                              id=q.id, repoId=repos)
                            msg_ = f'成功撤回{nickname}所有单号的结单状态。'
                            await sendMessage(msg, msg_, quote=False)

                        add_undo_action(msg.target.senderId, undo)

                        await sendMessage(msg, msg_, quote=False)
                    if w == '撤回':
                        await confirm(msg)
                    else:
                        await sendMessage(msg, '操作已结束。', quote=False)

            await sendMessage(msg, m + '请回复“全部”或对应编号来标记完稿。')
            await confirm(msg)
        else:
            await sendMessage(msg, f'{nickname}没有任何的活跃单。')
    else:
        m = re.match(r'#(.*)', id)
        if m:
            fin = OrderDBUtil.Order.finish(id=m.group(1), repoId=repos)
            if fin:
                msg_ = f'成功标记#{m.group(1)}为结单状态，如需撤回，请发送“撤回”。'

                async def undo():
                    if OrderDBUtil.Order.undo_finish(id=m.group(1), repoId=repos):
                        msg_ = f'成功撤回#{m.group(1)}的结单状态。'
                        await sendMessage(msg, msg_, quote=False)

                add_undo_action(msg.target.senderId, undo)
            else:
                msg_ = f'未找到#{m.group(1)}，请检查输入。'

            await sendMessage(msg, msg_, quote=False)


@ordr.handle(r'^撤回')
async def _(msg: MessageSession):
    print(undo_actions)
    get_undo_action = undo_actions.get(msg.target.senderId)
    if get_undo_action is not None:
        if get_undo_action:
            undo_action = deepcopy(get_undo_action[-1])
            undo_actions[msg.target.senderId].pop()
            await undo_action()
        else:
            await sendMessage(msg, '没有可撤回的操作。')
    else:
        await sendMessage(msg, '没有可撤回的操作。')


@ordr.handle(r'^编辑 (.*)')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    repos = ginfo.get_bind_repos()
    split = msg.matched_msg.group(1).split(' ')
    ticket = '0'
    remarks = []
    for x in split:
        if x[0] == '#':
            ticket = x[1:]
        else:
            remarks.append(x)
    remark = ' '.join(remarks)
    query_order_info = OrderDBUtil.Order.query_all(id=ticket, repoId=repos)
    for x in query_order_info.queried_infos:
        if not await check_admin(msg, x.repoId):
            return await sendMessage(msg, '输入的单号有误，请检查输入。')
        edit = OrderDBUtil.Order.edit(ticket, [x.repoId], 'remark', remark)
        if edit:
            async def undo():
                OrderDBUtil.Order.edit(ticket, [x.repoId], 'remark',
                                       edit)
                await sendMessage(msg, f'成功撤回#{ticket}的备注为 {edit}。')

            add_undo_action(msg.target.senderId, undo)
            await sendMessage(msg, f'成功编辑#{ticket}的备注为 {remark}。')


@ordr.handle('^删除 #(.*)$')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    repos = ginfo.get_bind_repos()
    query_order_info = OrderDBUtil.Order.query_all(id=msg.matched_msg.group(1), repoId=repos)
    for x in query_order_info.queried_infos:
        if not await check_admin(msg, x.repoId):
            return await sendMessage(msg, '输入的单号有误，请检查输入。')
        edit = OrderDBUtil.Order.edit(msg.matched_msg.group(1), [x.repoId], 'remark', x.remark + '（删除操作）')
        OrderDBUtil.Order.finish(id=msg.matched_msg.group(1), repoId=[repos])
        if edit:
            async def undo():
                OrderDBUtil.Order.edit(msg.matched_msg.group(1), [x.repoId], 'remark',
                                       x.remark)
                OrderDBUtil.Order.undo_finish(id=msg.matched_msg.group(1), repoId=[repos])
                await sendMessage(msg, f'成功撤回#{msg.matched_msg.group(1)}的删除状态。')

            add_undo_action(msg.target.senderId, undo)
            await sendMessage(msg, f'成功删除#{msg.matched_msg.group(1)}。')


ord = on_command('furorder')


@ord.handle('enable [<id>] {启用查单功能并将本群和使用者账号绑定。}')
async def _(msg: MessageSession):
    if not await msg.checkPermission():
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    id = msg.parsed_msg['<id>']
    nickname = '???'
    if id:
        id = convert_cqat(id)
        try:
            verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
            if verify:
                nickname = verify['nickname']
            masterId = msg.target.senderFrom + '|' + id
        except Exception:
            traceback.print_exc()
            return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')
    else:
        masterId = msg.target.senderId
        nickname = msg.target.senderName
    query = OrderDBUtil.Group(msg.target.targetId)
    if query.enable(masterId):
        add_master_info = OrderDBUtil.Master(masterId=masterId).add(nickname)
        if add_master_info:
            queryinfo = query.get_bind_repos()
            rm = '、'.join(str(x) for x in queryinfo)
            await sendMessage(msg, f'已启用查单功能并新建仓库{rm}，主人为{nickname}。')


@ord.handle('disable {停用机器人相关的一切指令，机器人不再响应enable以外所有的数据。}', required_admin=True)
async def _(msg: MessageSession):
    if OrderDBUtil.Group(msg.target.targetId).disable():
        await sendMessage(msg, f'已禁用查单功能。')


@ord.handle('classify add <name> {新增一个分类。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repo = brinfo.query()
    repoId = query_repo.id
    if not await check_admin(msg, repoId):
        return await sendMessage(msg, '失败：你没有此仓库的权限。')
    query = OrderDBUtil.Category(query_repo.id)
    name = msg.parsed_msg['<name>']
    if query.add_category(name):
        await sendMessage(msg, f'成功添加分类：{name}')
    else:
        await sendMessage(msg, f'失败：已存在名为{name}的分类。')


@ord.handle('classify remove <name> {移除一个分类。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repo = brinfo.query()
    repoId = query_repo.id
    if not await check_admin(msg, repoId):
        return await sendMessage(msg, '失败：你没有此仓库的权限。')
    query = OrderDBUtil.Category(query_repo.id)
    check_base_category = query.get_all_category_by_id()[query_repo.defaultCategoryId]
    name = msg.parsed_msg['<name>']
    if check_base_category == name:
        return await sendMessage(msg, '失败：你无法移除默认分类。')
    if query.remove_category(name):
        await sendMessage(msg, f'成功移除分类：{name}，所有在此分类下的单子已归类到默认分类。')
    else:
        await sendMessage(msg, f'失败：已存在名为{name}的分类。')


@ord.handle('classify set <#n/name1> <name2> {将单号/分类移动至新的分类。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repo = brinfo.query()
    repoId = query_repo.id
    arg1 = msg.parsed_msg['<#n/name1>']
    arg2 = msg.parsed_msg['<name2>']
    categories = OrderDBUtil.Category(repoId).get_all_category_by_name()
    if arg1[0] == '#':
        id = arg1[1:]
        if id.isdigit():
            cid = categories.get(arg2)
            if cid is not None:
                add = OrderDBUtil.Order.add_category(int(id), repoId, cid)
                if add:
                    return await sendMessage(msg, f'成功将 {arg1} 的分类设置为 {arg2}')
            else:
                return await sendMessage(msg, f'错误：仓库中找不到 {arg2} 分类。')
        else:
            return await sendMessage(msg, f'错误：你输入的单号必须是纯数字的。')
    else:
        if arg1 in categories and arg2 in categories:
            get_queries = OrderDBUtil.Order.query_all(categoryId=categories[arg1], repoId=[repoId])
            for x in get_queries.queried_infos:
                OrderDBUtil.Order.edit(x.id, [repoId], 'categoryId', categories[arg2])
            return await sendMessage(msg, f'成功将 {arg1} 分类下的所有单号移动到 {arg2} 下。')
        else:
            return await sendMessage(msg, f'错误：你输入的分类有误或不存在，请重新输入。')


@ord.handle('classify rename <name> <newname> {重命名一个分类。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repo = brinfo.query()
    repoId = query_repo.id
    arg1 = msg.parsed_msg['<name>']
    arg2 = msg.parsed_msg['<newname>']
    category = OrderDBUtil.Category(repoId)
    categories = category.get_all_category_by_name()
    if arg2 in categories:
        return await sendMessage(msg, f'错误：{arg2}分类已存在，请重新输入或使用移动命令。')
    edit = category.edit_category(arg1, arg2)
    if edit:
        await sendMessage(msg, f'成功将 {arg1} 分类重命名为 {arg2}。')
    else:
        await sendMessage(msg, f'错误：{arg1} 分类不存在。')


@ord.handle('classify list {查看当前的分类列表。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    query_repo = brinfo.query()
    repoId = query_repo.id
    category = OrderDBUtil.Category(repoId)
    categories = category.get_all_category_by_name()
    msgs = [f'当前 {repoId} 仓库共有以下分类：']
    for x in categories:
        query_orders = OrderDBUtil.Order.query_all(categoryId=categories[x], repoId=[repoId])
        value = len(query_orders.queried_infos)
        msgs.append(x + f'（{value}单）')
    await sendMessage(msg, '\n'.join(msgs))


@ord.handle('info {查看当前群聊的绑定信息。}')
async def _(msg: MessageSession):
    query = OrderDBUtil.Group(targetId=msg.target.targetId)
    if query.query() is None:
        return await msg.sendMessage('此群未启用过查单功能。')
    msgs = [f'当前群（{msg.session.target}）绑定了以下信息：']
    msgs_second = []
    repos = query.get_bind_repos()
    msgs_second.append('当前群还绑定了以下仓库ID：')
    for x in repos:
        q = OrderDBUtil.Repo(x).query()
        qr = q.createdBy
        if qr != msg.target.targetId:
            createdBy = re.sub("" + msg.target.targetFrom + "\|", "", qr)
            msgs_second.append(str(x) + f'（创建自{createdBy}）')
        else:
            msgs.append(f'仓库ID：' + str(x))
            msgs.append(f'仓库主人：' + re.sub("" + msg.target.senderFrom + "\|", "", q.masterId))
    await sendMessage(msg, '\n'.join(msgs + (msgs_second if len(msgs_second) > 1 else [])))


@ord.handle('bind <RepoID> {关联一个仓库。}')
async def _(msg: MessageSession):
    repoId = int(msg.parsed_msg['<RepoID>'])
    query = OrderDBUtil.Group(targetId=msg.target.targetId)
    bind_repos = query.get_bind_repos()
    if repoId not in bind_repos:
        qr = OrderDBUtil.Repo(repoId=repoId).query()
        if qr is not None:
            if qr.masterId == msg.target.senderId:
                add = query.add_bind_repos(repoId)
                if add:
                    createdBy = re.sub("" + msg.target.targetFrom + "\|", "", qr.createdBy)
                    await sendMessage(msg, f'成功为此群绑定了 {repoId} 仓库（创建自{createdBy}）')
            else:
                await sendMessage(msg, '发生错误：你必须要成为此仓库的主人才可绑定。')
        else:
            await sendMessage(msg, '发生错误：此仓库不存在。')
    else:
        await sendMessage(msg, '发生错误：此仓库已绑定本群。')


@ord.handle('unbind <RepoID> {取消关联一个仓库。}')
async def _(msg: MessageSession):
    repoId = int(msg.parsed_msg['<RepoID>'])
    query = OrderDBUtil.Group(targetId=msg.target.targetId)
    bind_repos = query.get_bind_repos()
    if repoId in bind_repos:
        qr = OrderDBUtil.Repo(repoId=repoId).query()
        if qr is not None:
            if qr.masterId == msg.target.senderId:
                if qr.createdBy != msg.target.targetId:
                    add = query.remove_bind_repos(repoId)
                    if add:
                        createdBy = re.sub("" + msg.target.targetFrom + "\|", "", qr.createdBy)
                        await sendMessage(msg, f'成功为此群解绑了 {repoId} 仓库（创建自{createdBy}）')
                else:
                    await sendMessage(msg, '发生错误：你不可以解绑当前群的基础仓库。')
            else:
                await sendMessage(msg, '发生错误：你必须要成为此仓库的主人才可解绑。')
        else:
            await sendMessage(msg, '发生错误：此仓库不存在。')
    else:
        await sendMessage(msg, '发生错误：此仓库未绑定本群。')


@ord.handle('transfer <id> {转让本群基础仓库的主人。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    brinfo = brinfo.query()
    if msg.target.senderId != brinfo.masterId:
        return sendMessage(msg, '你不是本群基础仓库的主人，无法转让仓库。')
    id = msg.parsed_msg['<id>']
    nickname = '???'
    if id:
        id = convert_cqat(id)
        try:
            verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
            if verify:
                nickname = verify['nickname']
            masterId = msg.target.senderFrom + '|' + id
        except Exception:
            traceback.print_exc()
            return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')
    else:
        masterId = msg.target.senderId
        nickname = msg.target.senderName
    query = OrderDBUtil.Repo(brinfo.id).edit('masterId', masterId)
    if query:
        OrderDBUtil.Sender(msg.target.senderId).add_TargetAdmin(brinfo.id)
        await sendMessage(msg, f'成功将此群的基础仓库主人转让给{nickname}。')


@ord.handle('list [<page>] [-f] {显示当前所有的单号列表。}', options_desc={'[-f]': '显示已完成的单号'})
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    bind_repos = ginfo.get_bind_repos()
    repos = []
    for repo in bind_repos:
        if await check_admin(msg, repo):
            repos.append(repo)
    if len(repos) == 0:
        return await sendMessage(msg, '你没有此群绑定的所有仓库中任何一个仓库的管理员权限，无法执行命令。')
    query = OrderDBUtil.Group(targetId=msg.target.targetId)
    group_info = query.query()
    if group_info is None or not group_info.isEnabled:
        return
    query = OrderDBUtil.Order.query_all(showfinished=msg.parsed_msg['-f'], mode=0,
                                        repoId=[repos])
    msg_lst = []
    for q in query.queried_infos:
        orderId = q.orderId
        m = re.match(r'QQ\|(.*)', orderId)
        if m:
            orderId = m.group(1)
        ms = f'#{q.id} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]'
        if msg.parsed_msg['-f']:
            ms += '（已结单）' if q.finished else '（未结单）'
        msg_lst.append(ms)
    if msg_lst:
        split = [msg_lst[i:i + 10] for i in range(0, len(msg_lst), 10)]
        all_pages = len(split)
        page = msg.parsed_msg['<page>']
        if page is None:
            page = 1
        page = int(page)
        if page < 1 or page > all_pages:
            page = 1
        await sendMessage(msg, '单号列表：\n  ' + '\n  '.join(split[page - 1]) + f'\n第 {page} 页 - 共 {all_pages} 页')


@ord.handle('memberuse (true|false) [<RepoID>] {设置是否允许群成员查询排队进度。}',
            'onlyplacebyop (true|false) [<RepoID>] {设置是否只有排单管理员能够下单。}',
            'autoretract (true|false) [<RepoID>] {设置是否在消息发送1分钟后自动撤回消息。}',
            'classified (true|false) [<RepoID>] {设置是否在群员查单时按分类显示/管理员查单时显示分类下的单号数量。}', required_admin=True)
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    column = ''
    repoId = msg.parsed_msg['<RepoID>']
    if not repoId:
        edit_repo = brinfo
    else:
        if repoId in ginfo.get_bind_repos():
            edit_repo = OrderDBUtil.Repo(int(repoId))
        else:
            return await sendMessage(msg, '操作失败：此群未绑定本仓库。')
    if msg.parsed_msg['memberuse']:
        column = 'isAllowMemberQuery'
    if msg.parsed_msg['onlyplacebyop']:
        column = 'isAllowMemberOrder'
    if msg.parsed_msg['autoretract']:
        column = 'isAutoDelete'
    if msg.parsed_msg['classified']:
        column = 'isNeedClassify'
    value = msg.parsed_msg['true'] if msg.parsed_msg['true'] else False
    if column == 'isAllowMemberOrder':
        value = not value
    q = edit_repo.edit(column, value)
    if q:
        await sendMessage(msg, f'操作成功。')


@ord.handle('defaultordernum <Int> [<RepoID>] {设置管理员使用查单顺序/倒序功能时默认显示的数量。}', required_admin=True)
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    repoId = msg.parsed_msg['<RepoID>']
    if not repoId:
        edit_repo = brinfo
    else:
        if repoId in ginfo.get_bind_repos():
            edit_repo = OrderDBUtil.Repo(int(repoId))
        else:
            return await sendMessage(msg, '操作失败：此群未绑定本仓库。')
    value = msg.parsed_msg['<Int>']
    if value.isdigit():
        value = int(value)
        if value > 30:
            return await sendMessage(msg, '默认查单数量不能大于30。')
        if edit_repo.edit('defaultOrderNum', value):
            await sendMessage(msg, f'已设置默认查单数量为：{value}')
    else:
        await sendMessage(msg, f'操作失败，请输入一个整数。')


@ord.handle('op <id> [<RepoID>] {将某人设置为排单管理员。}',
            'deop <id> [<RepoID>] {将某人的排单管理员资格取消。}')
@infos
async def _(msg: MessageSession, ginfo, brinfo):
    group_info = ginfo.query()
    if group_info is None or not group_info.isEnabled:
        return await msg.sendMessage('此群未开启查单功能。')
    repoIds = ginfo.get_bind_repos()
    requestRepoId = msg.parsed_msg['<RepoID>']
    if requestRepoId and int(requestRepoId) not in repoIds:
        return await msg.sendMessage(f'此群未绑定ID为{requestRepoId}的仓库。')
    nickname = '???'
    id = convert_cqat(msg.parsed_msg['<id>'])
    try:
        verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
        if verify:
            nickname = verify['nickname']
        senderId = msg.target.senderFrom + '|' + id
    except Exception:
        traceback.print_exc()
        return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')

    if not requestRepoId:
        requestRepo = brinfo.query()
    else:
        requestRepo = OrderDBUtil.Repo(requestRepoId).query()
    if requestRepo.masterId != msg.target.senderId:
        return await sendMessage(msg, '你不是本仓库的主人，无法执行该命令。')
    if msg.parsed_msg['op']:
        q = OrderDBUtil.Sender(senderId).add_TargetAdmin(requestRepo.id)
        if q:
            await sendMessage(msg, f'成功添加{nickname}（{id}）为{requestRepo.id}仓库的排单管理员。')
        else:
            await sendMessage(msg, f'操作失败。')  # 理应不会发生，所以不知道怎么写理由
    else:
        q = OrderDBUtil.Sender(senderId).remove_TargetAdmin(requestRepo.id)
        if q:
            await sendMessage(msg, f'成功移除{nickname}（{id}）的{requestRepo.id}仓库的排单管理员权限。')
        else:
            await sendMessage(msg, f'操作失败。')  # 理应不会发生，所以不知道怎么写理由


@ord.handle('leave {机器人自动退群。}', required_admin=True)
async def _(msg: MessageSession):
    confirm = await msg.waitConfirm('您真的确定要这么做吗？如确认，本机器人将退出本群。')
    if confirm:
        await msg.call_api('set_group_leave', group_id=msg.session.target)


delete_confirm_list = []


@ord.handle('delete {机器人自动退群，30分钟后删除本群产生的一切数据。}', required_admin=True)
async def _(msg: MessageSession):
    delete_confirm_list.append(msg.target.targetId)
    await msg.sendMessage('您真的确定要这么做吗？如确认，本机器人将退出本群，且本群制造的数据将于30分钟后完全移除。'
                          '请使用~furorder confirm/cancel 来确认操作。')


@ord.handle('confirm', 'cancel', required_admin=True)
async def _(msg: MessageSession):
    if msg.parsed_msg['confirm']:
        if msg.target.targetId in delete_confirm_list:
            await msg.sendMessage('已执行。')
            delete_confirm_list.remove(msg.target.targetId)
            await msg.call_api('set_group_leave', group_id=msg.session.target)
            OrderDBUtil.Delete(targetId=msg.target.targetId).add()
    if msg.parsed_msg['cancel']:
        if msg.target.targetId in delete_confirm_list:
            delete_confirm_list.remove(msg.target.targetId)
            await msg.sendMessage('已取消。')


@on_schedule('autodelete_scheduler', trigger=IntervalTrigger(minutes=10), required_superuser=True)
async def _(bot: FetchTarget):
    records = OrderDBUtil.Delete.show()
    getlist = await bot.call_api('get_group_list')
    for x in records:
        m = re.match(r'QQ\|Group\|(.*)', x.targetId)
        if m:
            if datetime.datetime.now().timestamp() - x.timestamp.timestamp() > 1800:
                found = False
                for y in getlist:
                    if y['group_id'] == int(m.group(1)):
                        found = True
                if not found:
                    OrderDBUtil.delete_all_data_by_targetId(x.targetId)
                OrderDBUtil.Delete(x.targetId).remove()
