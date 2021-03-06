from core.dirty_check import check
from modules._wiki.utils.UTC8 import UTC8
from modules._wiki.wikilib_v2 import WikiLib


async def ab(wiki_url):
    wiki = WikiLib(wiki_url)
    query = await wiki.get_json(action='query', list='abuselog', aflprop='user|title|action|result|filter|timestamp')
    pageurl = wiki.wiki_info.articlepath.replace('$1', 'Special:AbuseLog')
    d = []
    for x in query['query']['abuselog'][:5]:
        d.append('•' + x['title'] + ' - ' + x['user'] + '于' + UTC8(x['timestamp'], 'onlytimenoutc') + '\n过滤器名：' + x[
            'filter'] + '\n处理结果：' + x['result'])
    y = await check(*d)
    y = '\n'.join(z['content'] for z in y)
    if y.find('<吃掉了>') != -1 or y.find('<全部吃掉了>') != -1:
        return f'{pageurl}\n{y}\n...仅显示前5条内容\n检测到外来信息介入，请前往滥用日志查看所有消息。'
    else:
        return f'{pageurl}\n{y}\n...仅显示前5条内容'
