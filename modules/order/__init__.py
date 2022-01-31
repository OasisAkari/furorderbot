import datetime
import re
import traceback

from core.component import on_command, on_regex, on_schedule
from core.elements import MessageSession, IntervalTrigger, FetchTarget
from .dbutils import OrderDBUtil
from .orm import OrderInfo


def convert_cqat(s):
    match = re.match(r'\[CQ:at,qq=(.*?)]', s)
    if match:
        return match.group(1)
    return s


async def check_admin(msg: MessageSession):
    if await msg.checkPermission():
        return True
    if OrderDBUtil.SenderInfo(msg.target.senderId).check_TargetAdmin(msg.target.targetId):
        return True
    return False


async def sendMessage(msg: MessageSession, msgchain, quote=True):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    m = await msg.sendMessage(msgchain, quote=quote)
    if group_info is not None and group_info.isAutoDelete:
        await msg.sleep(60)
        await m.delete()
    

ordr = on_regex('ordr')


@ordr.handle(r'^下单 (.*?) (.*)')
async def _(msg: MessageSession):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    if group_info is None or not group_info.isEnabled:
        return
    if not group_info.isAllowMemberOrder:
        if not await check_admin(msg):
            return await sendMessage(msg, '你没有使用该命令的权限，请联系排单管理员执行。')
    id = convert_cqat(msg.matched_msg.group(1))
    nickname = '???'
    try:
        verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
        if verify:
            nickname = verify['nickname']
        senderId = msg.target.senderFrom + '|' + id
    except Exception:
        traceback.print_exc()
        return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')
    remark = msg.matched_msg.group(2)
    OrderDBUtil.Order.add(OrderInfo(masterId=group_info.masterId, orderId=senderId, targetId=msg.target.targetId,
                                    remark=remark, nickname=nickname))
    await sendMessage(msg, f'已添加 {nickname} 的 {remark}')


@ordr.handle(r'^查单$')
async def _(msg: MessageSession):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    if group_info is None or not group_info.isEnabled:
        return
    defaultOrderNum = group_info.defaultOrderNum
    if not await check_admin(msg):
        if not group_info.isAllowMemberQuery:
            if not await check_admin(msg):
                return await sendMessage(msg, '你没有使用该命令的权限。')
        query = OrderDBUtil.Order.query(orderId=msg.target.senderId, masterId=group_info.masterId)
        if query.queried_infos is not None:
            msg_lst = []
            for q in query.queried_infos:
                msg_lst.append(f'#{q.displayId} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}] - 前面还有{q.queue}单')
            m = f'您共有{len(msg_lst)}个活跃单：\n  ' + '\n  '.join(msg_lst)
            await sendMessage(msg, m)
        else:
            await sendMessage(msg, '您没有任何的活跃单。')
    else:
        query = OrderDBUtil.Order.query_all(masterId=group_info.masterId, mode=1)
        if query.queried_infos is not None:
            msg_lst = []
            for q in query.queried_infos:
                orderId = q.orderId
                m = re.match(r'QQ\|(.*)', orderId)
                if m:
                    orderId = m.group(1)
                msg_lst.append(f'#{q.displayId} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
            if len(msg_lst) > defaultOrderNum:
                msg_lst = msg_lst[:defaultOrderNum]
                msg_ = f'最近下单的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
            else:
                msg_ = f'最近下单的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
            await sendMessage(msg, msg_)
        else:
            await sendMessage(msg, '没有任何的活跃单。')


@ordr.handle(r'^查单 (.*)$')
async def _(msg: MessageSession):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    if group_info is None or not group_info.isEnabled:
        return
    if not await check_admin(msg):
        return await sendMessage(msg, '你没有使用该命令的权限。')
    defaultOrderNum = group_info.defaultOrderNum
    split = msg.matched_msg.group(1).split(' ')
    mode = 0
    query_string = None
    orderId = None
    for x in split:
        x = convert_cqat(x)
        if x.isdigit():
            try:
                verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=x)
                if verify:
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
                query_string = x
    if orderId is None:
        if query_string is None:
            query = OrderDBUtil.Order.query_all(masterId=group_info.masterId, mode=mode)
            if query.queried_infos is None:
                return await sendMessage(msg, f'没有查询到{group_info.masterId}的任何单。')
            else:
                msg_lst = []
                for q in query.queried_infos:
                    orderId = q.orderId
                    m = re.match(r'QQ\|(.*)', orderId)
                    if m:
                        orderId = m.group(1)
                    msg_lst.append(
                        f'#{q.displayId} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
                if len(msg_lst) > defaultOrderNum:
                    msg_lst = msg_lst[:defaultOrderNum]
                    if mode == 0:
                        msg_ = f'接下来的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
                    else:
                        msg_ = f'最近下单的{defaultOrderNum}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
                else:
                    if mode == 0:
                        msg_ = f'接下来的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
                    else:
                        msg_ = f'最近下单的{len(query.queried_infos)}个单子（共{len(query.queried_infos)}活跃单）：\n  ' + '\n  '.join(msg_lst)
                await sendMessage(msg, msg_)
        else:
            query = OrderDBUtil.Order.query_all(masterId=group_info.masterId, mode=mode, remark=query_string)
            if query.queried_infos is None:
                return await sendMessage(msg, f'没有查询到有关 {query_string} 的任何单。')
            else:
                msg_lst = []
                for q in query.queried_infos:
                    orderId = q.orderId
                    m = re.match(r'QQ\|(.*)', orderId)
                    if m:
                        orderId = m.group(1)
                    msg_lst.append(
                        f'#{q.displayId} {q.nickname}({orderId}) - {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
                if mode == 0:
                    msg_ = f'{query_string}搜索到如下{len(query.queried_infos)}个结果（正序）：\n  ' + '\n  '.join(msg_lst)
                else:
                    msg_ = f'{query_string}搜索到如下{len(query.queried_infos)}个结果（倒序）：\n  ' + '\n  '.join(msg_lst)
                await sendMessage(msg, msg_)
    else:
        if query_string is None:
            query = OrderDBUtil.Order.query(masterId=group_info.masterId, orderId=orderId, mode=mode)
            if query.queried_infos is not None:
                msg_lst = []
                for q in query.queried_infos:
                    msg_lst.append(f'#{q.displayId} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
                m = f'{nickname}有如下{len(msg_lst)}个单子：\n  ' + '\n  '.join(msg_lst)
                await sendMessage(msg, m)
            else:
                await sendMessage(msg, f'{nickname}没有任何的活跃单。')
        else:
            query = OrderDBUtil.Order.query(masterId=group_info.masterId, orderId=orderId, mode=mode,
                                            remark=query_string)
            if query.queried_infos is not None:
                msg_lst = []
                for q in query.queried_infos:
                    msg_lst.append(f'#{q.displayId} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
                m = f'{nickname}有如下{len(msg_lst)}个有关 {query_string} 单子：\n  ' + '\n  '.join(msg_lst)
                await sendMessage(msg, m)
            else:
                await sendMessage(msg, f'{nickname}没有任何活跃的有关 {query_string} 的单子。')


@ordr.handle(r'^完稿 (.*)$')
async def _(msg: MessageSession):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    if group_info is None or not group_info.isEnabled:
        return
    if not await check_admin(msg):
        return await sendMessage(msg, '你没有使用该命令的权限。')
    id = convert_cqat(msg.matched_msg.group(1))
    if id.isdigit():
        try:
            verify = await msg.call_api('get_group_member_info', group_id=msg.session.target, user_id=id)
            if verify:
                nickname = verify['nickname']
                orderId = msg.target.senderFrom + '|' + id
        except Exception:
            traceback.print_exc()
            return await sendMessage(msg, '无法获取群员信息，可能输入的ID有误。')
        query = OrderDBUtil.Order.query(masterId=group_info.masterId, orderId=orderId, mode=0)
        if query.queried_infos is not None:
            msg_lst = []
            for q in query.queried_infos:
                msg_lst.append(f'#{q.displayId} {q.remark} [{q.ts.strftime("%Y/%m/%d %H:%M")}]')
            m = f'{nickname}有如下{len(msg_lst)}个单子：\n  ' + '\n  '.join(msg_lst)
            undo_list = []

            async def confirm(msg: MessageSession):
                w = await msg.waitAnything()
                m = re.match(r'#(.*)', w)
                if m:
                    fin = OrderDBUtil.Order.finish(master_id=group_info.masterId, order_id=orderId,
                                                   display_id=m.group(1))
                    if fin:
                        msg_ = f'成功标记#{m.group(1)}为结单状态，如需撤销，请发送“撤销”'
                        undo_list.append(m.group(1))
                    else:
                        msg_ = f'未找到#{m.group(1)}，请检查输入，如已标记完成请发送“完成”。'
                    await sendMessage(msg, msg_, quote=False)
                    await confirm(msg)
                else:
                    if w == '撤销':
                        if OrderDBUtil.Order.undo_finish(master_id=group_info.masterId, order_id=orderId,
                                                         display_id=undo_list[-1]):
                            msg_ = f'成功撤销#{undo_list[-1]}的结单状态。'
                            await sendMessage(msg, msg_, quote=False)
                    if w == '完成':
                        await sendMessage(msg, '操作已结束。', quote=False)
                        return

            await sendMessage(msg, m + '请回复“全部”或对应编号来标记完稿。')
            await confirm(msg)
        else:
            await sendMessage(msg, f'{nickname}没有任何的活跃单。')
    else:
        m = re.match(r'#(.*)', id)
        if m:
            fin = OrderDBUtil.Order.finish(master_id=group_info.masterId,
                                           display_id=m.group(1))
            if fin:
                msg_ = f'成功标记#{m.group(1)}为结单状态，如需撤销，请发送“撤销 #单号”'
            else:
                msg_ = f'未找到#{m.group(1)}，请检查输入。'
            await sendMessage(msg, msg_, quote=False)


@ordr.handle(r'^撤销 (.*)$')
async def _(msg: MessageSession):
    group_info = OrderDBUtil.Group(targetId=msg.target.targetId).query()
    if group_info is None or not group_info.isEnabled:
        return
    if not await check_admin(msg):
        return await sendMessage(msg, '你没有使用该命令的权限，请联系排单管理员执行。')
    m = re.match(r'#(.*)', msg.matched_msg.group(1))
    if m:
        fin = OrderDBUtil.Order.undo_finish(master_id=group_info.masterId,
                                            display_id=m.group(1))
        if fin:
            msg_ = f'成功撤销#{m.group(1)}的结单状态。”'
        else:
            msg_ = f'未找到#{m.group(1)}，请检查输入。'
        await sendMessage(msg, msg_, quote=False)


ord = on_command('furorder')


@ord.handle('enable [<id>] {启用查单功能并将本群和使用者账号绑定。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
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
        id = msg.session.sender
        masterId = msg.target.senderId
        nickname = msg.target.senderName
    if OrderDBUtil.Group(msg.target.targetId).enable(masterId):
        await sendMessage(msg, f'已启用查单功能，查单对象为：{nickname}（{id}）')


@ord.handle('disable {停用机器人相关的一切指令，机器人不再响应enable以外所有的数据。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    if OrderDBUtil.Group(msg.target.targetId).disable():
        await sendMessage(msg, f'已禁用查单功能。')


@ord.handle('memberuse (true|false) {设置是否允许群成员查询排队进度。}',
            'onlyplacebyop (true|false) {设置是否只有排单管理员能够下单。}',
            'autoretract (true|false) {设置是否在消息发送1分钟后自动撤回消息。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    column = ''
    if msg.parsed_msg['memberuse']:
        column = 'isAllowMemberQuery'
    if msg.parsed_msg['onlyplacebyop']:
        column = 'isAllowMemberOrder'
    if msg.parsed_msg['autoretract']:
        column = 'isAutoDelete'
    value = msg.parsed_msg['true'] if msg.parsed_msg['true'] else False
    q = OrderDBUtil.Group(msg.target.targetId).edit(column, value)
    if q:
        await sendMessage(msg, f'操作成功。')
    else:
        await sendMessage(msg, f'操作失败，此群没有开启过查单功能。')


@ord.handle('defaultordernum <Int> {设置管理员使用查单顺序/倒序功能时默认显示的数量。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    value = msg.parsed_msg['<Int>']
    if value.isdigit():
        value = int(value)
        if value < 30:
            return await sendMessage(msg, '默认查单数量不能大于30。')
        if OrderDBUtil.Group(msg.target.targetId).edit('defaultOrderNum', value):
            await sendMessage(msg, f'已设置默认查单数量为：{value}')
        else:
            await sendMessage(msg, f'操作失败，此群没有开启过查单功能。')
    else:
        await sendMessage(msg, f'操作失败，请输入一个整数。')


@ord.handle('op <id> {将某人设置为排单管理员。}',
            'deop <id> {将某人的排单管理员资格取消。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
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
    if msg.parsed_msg['op']:
        q = OrderDBUtil.SenderInfo(senderId).add_TargetAdmin(msg.target.targetId)
        if q:
            await sendMessage(msg, f'成功添加{nickname}（{id}）为排单管理员。')
        else:
            await sendMessage(msg, f'操作失败。')  # 理应不会发生，所以不知道怎么写理由
    else:
        q = OrderDBUtil.SenderInfo(senderId).remove_TargetAdmin(msg.target.targetId)
        if q:
            await sendMessage(msg, f'成功移除{nickname}（{id}）的排单管理员权限。')
        else:
            await sendMessage(msg, f'操作失败。')  # 理应不会发生，所以不知道怎么写理由


@ord.handle('leave {机器人自动退群。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    confirm = await msg.waitConfirm('您真的确定要这么做吗？如确认，本机器人将退出本群。')
    if confirm:
        await msg.call_api('set_group_leave', group_id=msg.session.target)


delete_confirm_list = []


@ord.handle('delete {机器人自动退群，30分钟后删除本群产生的一切数据。}')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
    delete_confirm_list.append(msg.target.targetId)
    await msg.sendMessage('您真的确定要这么做吗？如确认，本机器人将退出本群，且本群制造的数据将于30分钟后完全移除。'
                          '请使用~furorder confirm/cancel 来确认操作。')


@ord.handle('confirm', 'cancel')
async def _(msg: MessageSession):
    if not await check_admin(msg):
        await sendMessage(msg, '你没有使用此命令的权限。')
        return
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
