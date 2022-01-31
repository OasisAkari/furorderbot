import urllib.parse

from config import Config
from core.dirty_check import check
from modules._wiki.utils.UTC8 import UTC8
from modules._wiki.utils.action_cn import action
from modules._wiki.wikilib_v2 import WikiLib


async def rc_qq(wiki_url):
    wiki = WikiLib(wiki_url)
    qq_account = Config("qq_account")
    query = await wiki.get_json(action='query', list='recentchanges',
                                rcprop='title|user|timestamp|loginfo|comment|redirect|flags|sizes|ids',
                                rclimit=99,
                                rctype='edit|new|log'
                                )
    pageurl = wiki.wiki_info.articlepath.replace("$1", 'Special:RecentChanges')
    nodelist = [{
        "type": "node",
        "data": {
            "name": f"最近更改地址",
            "uin": qq_account,
            "content": [
                {"type": "text", "data": {"text": pageurl}}]
        }
    }]
    rclist = []
    userlist = []
    titlelist = []
    for x in query["query"]["recentchanges"]:
        userlist.append(x['user'])
        titlelist.append(x['title'])
    checked_userlist = await check(*userlist)
    user_checked_map = {}
    for u in checked_userlist:
        user_checked_map[u['original']] = u['content']
    checked_titlelist = await check(*titlelist)
    title_checked_map = {}
    for t in checked_titlelist:
        title_checked_map[t['original']] = t['content']
    for x in query["query"]["recentchanges"]:
        t = []
        t.append(f"用户：{user_checked_map[x['user']]}")
        t.append(UTC8(x['timestamp'], 'full'))
        if x['type'] == 'edit':
            count = x['newlen'] - x['oldlen']
            if count > 0:
                count = f'+{str(count)}'
            else:
                count = str(count)
            t.append(f"{title_checked_map[x['title']]}（{count}）")
            comment = x['comment']
            if comment == '':
                comment = '（无摘要内容）'
            t.append(comment)
            t.append(
                f"{pageurl}{urllib.parse.quote(title_checked_map[x['title']])}?oldid={x['old_revid']}&diff={x['revid']}")
        if x['type'] == 'new':
            r = ''
            if 'redirect' in x:
                r = '（新重定向）'
            t.append(f"{title_checked_map[x['title']]}{r}")
            comment = x['comment']
            if comment == '':
                comment = '（无摘要内容）'
            t.append(comment)
        if x['type'] == 'log':
            log = x['logaction'] + '了' + title_checked_map[x['title']]
            if x['logtype'] in action:
                a = action[x['logtype']].get(x['logaction'])
                if a is not None:
                    log = a % title_checked_map[x['title']]
            t.append(log)
            params = x['logparams']
            if 'durations' in params:
                t.append('时长：' + params['durations'])
            if 'target_title' in params:
                t.append('对象页面：' + params['target_title'])
            if x['revid'] != 0:
                t.append(f"{pageurl}{urllib.parse.quote(title_checked_map[x['title']])}")
        rclist.append('\n'.join(t))
    for x in rclist:
        nodelist.append(
            {
                "type": "node",
                "data": {
                    "name": f"最近更改",
                    "uin": qq_account,
                    "content": [{"type": "text", "data": {"text": x}}],
                }
            })
    print(nodelist)
    return nodelist
