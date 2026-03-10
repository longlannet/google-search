#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)


def print_knowledge_graph(kg):
    if not kg:
        return
    safe_print('\n🧠 知识卡片:')
    safe_print(f"   标题: {kg.get('title')}")
    safe_print(f"   类型: {kg.get('type')}")
    if kg.get('description'):
        safe_print(f"   描述: {kg.get('description')}")
    attrs = kg.get('attributes', {})
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            safe_print(f'   {k}: {v}')
    elif isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict):
                k = attr.get('k') or attr.get('key') or '属性'
                v = attr.get('v') or attr.get('value') or ''
                safe_print(f'   {k}: {v}')
    safe_print('-' * 20)


def print_answer_box(answer_box):
    if not answer_box:
        return
    safe_print('\n📦 直达答案:')
    for field in ['title', 'answer', 'snippet']:
        value = answer_box.get(field)
        if value:
            label = {'title': '标题', 'answer': '答案', 'snippet': '摘要'}[field]
            safe_print(f'   {label}: {value}')
    link = answer_box.get('link')
    if link:
        safe_print(f'   🔗 {link}')
    safe_print('-' * 20)


def print_organic_results(organic, title='自然搜索结果', limit=None):
    if not organic:
        safe_print('❌ 未找到结果。')
        return
    rows = organic[:limit] if limit else organic
    safe_print(f'\n✅ {title}: {len(rows)} 条')
    safe_print('-' * 30)
    for i, res in enumerate(rows, start=1):
        title_text = res.get('title', 'No Title')
        link = res.get('link', '#')
        snippet = res.get('snippet', '')
        date = res.get('date', '')
        prefix = f'[{date}] ' if date else ''
        safe_print(f'{i}. {prefix}{title_text}')
        safe_print(f'   🔗 {link}')
        if snippet:
            safe_print(f'   📝 {snippet}')
        safe_print()


def print_people_also_ask(items, limit=3):
    if not items:
        return
    safe_print('🤔 People Also Ask:')
    for i, item in enumerate(items[:limit], start=1):
        question = item.get('question') or 'No question'
        snippet = item.get('snippet') or ''
        link = item.get('link') or ''
        safe_print(f'{i}. {question}')
        if snippet:
            safe_print(f'   📝 {snippet}')
        if link:
            safe_print(f'   🔗 {link}')
    safe_print('-' * 30)


def print_related_searches(items, limit=5):
    if not items:
        return
    safe_print('🔎 相关搜索:')
    for i, item in enumerate(items[:limit], start=1):
        query = item.get('query') if isinstance(item, dict) else str(item)
        safe_print(f'{i}. {query}')
    safe_print('-' * 30)


def print_news(items, limit=5):
    if not items:
        return
    safe_print('📰 新闻结果:')
    for i, item in enumerate(items[:limit], start=1):
        title = item.get('title', 'No Title')
        link = item.get('link', '#')
        snippet = item.get('snippet') or ''
        source = item.get('source') or ''
        date = item.get('date') or ''
        meta = ' · '.join([x for x in [source, date] if x])
        safe_print(f'{i}. {title}')
        if meta:
            safe_print(f'   🏷️ {meta}')
        safe_print(f'   🔗 {link}')
        if snippet:
            safe_print(f'   📝 {snippet}')
    safe_print('-' * 30)


def print_images(items, limit=5):
    if not items:
        return
    safe_print('🖼️ 图片结果:')
    for i, item in enumerate(items[:limit], start=1):
        title = item.get('title', 'No Title')
        link = item.get('link') or item.get('imageUrl') or '#'
        source = item.get('source') or item.get('domain') or ''
        safe_print(f'{i}. {title}')
        if source:
            safe_print(f'   🏷️ {source}')
        safe_print(f'   🔗 {link}')
    safe_print('-' * 30)


def print_videos(items, limit=5):
    if not items:
        return
    safe_print('🎬 视频结果:')
    for i, item in enumerate(items[:limit], start=1):
        title = item.get('title', 'No Title')
        link = item.get('link', '#')
        snippet = item.get('snippet') or ''
        source = item.get('source') or ''
        date = item.get('date') or ''
        meta = ' · '.join([x for x in [source, date] if x])
        safe_print(f'{i}. {title}')
        if meta:
            safe_print(f'   🏷️ {meta}')
        safe_print(f'   🔗 {link}')
        if snippet:
            safe_print(f'   📝 {snippet}')
    safe_print('-' * 30)


def print_shopping(items, limit=5):
    if not items:
        return
    safe_print('🛒 购物结果:')
    for i, item in enumerate(items[:limit], start=1):
        title = item.get('title', 'No Title')
        link = item.get('link', '#')
        price = item.get('price') or ''
        source = item.get('source') or item.get('seller') or ''
        delivery = item.get('delivery') or ''
        safe_print(f'{i}. {title}')
        meta = ' · '.join([x for x in [price, source, delivery] if x])
        if meta:
            safe_print(f'   🏷️ {meta}')
        safe_print(f'   🔗 {link}')
    safe_print('-' * 30)


def print_places(items, limit=5, title='地点结果', show_ids=True):
    if not items:
        return
    safe_print(f'📍 {title}:')
    for i, item in enumerate(items[:limit], start=1):
        title_text = item.get('title') or item.get('name') or 'No Title'
        address = item.get('address') or ''
        phone = item.get('phoneNumber') or item.get('phone') or ''
        rating = item.get('rating') or ''
        rating_count = item.get('ratingCount') or ''
        place_id = item.get('placeId') or ''
        cid = item.get('cid') or ''
        fid = item.get('fid') or ''
        link = item.get('link') or item.get('website') or '#'
        extras = []
        if address:
            extras.append(address)
        if phone:
            extras.append(phone)
        if rating:
            rating_text = f'评分 {rating}'
            if rating_count:
                rating_text += f'（{rating_count}）'
            extras.append(rating_text)
        safe_print(f'{i}. {title_text}')
        if extras:
            safe_print(f"   🏷️ {' · '.join(extras)}")
        if show_ids:
            if place_id:
                safe_print(f'   🆔 placeId: {place_id}')
            if cid:
                safe_print(f'   🆔 cid: {cid}')
            if fid:
                safe_print(f'   🆔 fid: {fid}')
        safe_print(f'   🔗 {link}')
    safe_print('-' * 30)


def print_reviews(items, limit=5):
    if not items:
        return
    safe_print('⭐ 评论结果:')
    for i, item in enumerate(items[:limit], start=1):
        author = item.get('author') or item.get('user') or item.get('name') or item.get('title') or '匿名用户'
        if isinstance(author, dict):
            author = author.get('name') or author.get('title') or author.get('user') or str(author)
        rating = item.get('rating') or ''
        date = item.get('date') or item.get('publishedAt') or ''
        text = item.get('text') or item.get('snippet') or item.get('review') or ''
        source = item.get('source') or item.get('sourceName') or ''
        photos = item.get('images') or item.get('photos') or []
        local_guide = item.get('localGuide')
        contribs = item.get('reviews') or item.get('reviewCount') or item.get('contributions') or ''
        safe_print(f'{i}. {author}')
        meta_bits = []
        if rating:
            meta_bits.append(f'评分 {rating}')
        if date:
            meta_bits.append(str(date))
        if source:
            meta_bits.append(str(source))
        if local_guide:
            meta_bits.append('Local Guide')
        if contribs:
            meta_bits.append(f'贡献 {contribs}')
        if meta_bits:
            safe_print(f"   🏷️ {' · '.join(meta_bits)}")
        if text:
            text = text.strip()
            if len(text) > 300:
                text = text[:300].rstrip() + '…'
            safe_print(f'   📝 {text}')
        if photos:
            safe_print(f'   🖼️ 附图: {len(photos)}')
    safe_print('-' * 30)


def print_autocomplete(items, limit=10):
    if not items:
        return
    safe_print('⌨️ 自动补全:')
    for i, item in enumerate(items[:limit], start=1):
        if isinstance(item, dict):
            text = item.get('value') or item.get('query') or item.get('title') or str(item)
        else:
            text = str(item)
        safe_print(f'{i}. {text}')
    safe_print('-' * 30)


def print_webpage(data, summary_chars=800):
    text = (data.get('text') or '').strip()
    title = (data.get('title') or '').strip()
    if not text:
        safe_print('❌ 未提取到网页正文。')
        return

    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not title:
        title = paragraphs[0] if paragraphs else '网页正文'
    body = '\n\n'.join(paragraphs[1:]) if len(paragraphs) > 1 else text
    summary = body[:summary_chars].strip() if body else text[:summary_chars].strip()

    safe_print('📄 网页摘要:')
    safe_print(f'   标题: {title[:160]}')
    safe_print(f'   长度: {len(text)} 字符')
    safe_print('-' * 30)
    safe_print(summary)
    if len(body) > summary_chars:
        safe_print('\n...（已截断）')
    safe_print('-' * 30)


def print_lens_results(data, limit=10):
    candidates = (
        data.get('organic', [])
        or data.get('visualMatches', [])
        or data.get('similarImages', [])
        or data.get('images', [])
    )
    if not candidates:
        safe_print('🔎 Lens 结果: 未找到匹配项。')
        return

    safe_print('🔎 Lens 匹配结果:')
    for i, item in enumerate(candidates[:limit], start=1):
        title = item.get('title', 'No Title')
        link = item.get('link') or item.get('imageUrl') or '#'
        snippet = item.get('snippet') or ''
        source = item.get('source') or item.get('domain') or ''
        safe_print(f'{i}. {title}')
        meta = ' · '.join([x for x in [source] if x])
        if meta:
            safe_print(f'   🏷️ {meta}')
        safe_print(f'   🔗 {link}')
        if snippet:
            safe_print(f'   📝 {snippet}')
    safe_print('-' * 30)


def print_pagination(pagination):
    if not pagination or not isinstance(pagination, dict):
        return
    current = pagination.get('current')
    next_page = pagination.get('next')
    if current or next_page:
        safe_print('📄 分页信息:')
        if current:
            safe_print(f'   当前页: {current}')
        if next_page:
            safe_print(f'   下一页: {next_page}')
        safe_print('-' * 30)


def print_credits(data):
    credits = data.get('credits')
    if credits is None:
        return
    safe_print(f'💳 Credits: {credits}')
    safe_print('-' * 30)


def print_search_parameters(data):
    params = data.get('searchParameters')
    if not params or not isinstance(params, dict):
        return
    bits = []
    for key in ['q', 'url', 'type', 'engine', 'gl', 'hl', 'page', 'num']:
        value = params.get(key)
        if value not in [None, '']:
            bits.append(f'{key}={value}')
    if bits:
        safe_print('⚙️ 搜索参数: ' + ' | '.join(bits))
        safe_print('-' * 30)


def print_overview():
    safe_print('🔎 Google Search Skill · 功能总览 / 速查')
    safe_print('=' * 44)
    safe_print('')
    safe_print('基础搜索:')
    safe_print('  web/search      普通 Google 搜索')
    safe_print('  images          图片搜索')
    safe_print('  videos          视频搜索')
    safe_print('  news            新闻搜索')
    safe_print('  shopping        购物搜索')
    safe_print('  scholar         学术搜索')
    safe_print('  patents         专利搜索')
    safe_print('')
    safe_print('本地/地图:')
    safe_print('  places          地点搜索')
    safe_print('  maps            地图/本地结果，带 placeId/cid/fid')
    safe_print('  reviews         评论查询（必须带 --place-id / --cid / --fid）')
    safe_print('  maps-reviews    先 maps 再 reviews，可配 --pick N / --all')
    safe_print('')
    safe_print('提取/识别:')
    safe_print('  autocomplete    搜索建议/自动补全')
    safe_print('  webpage         网页正文提取（参数是 URL）')
    safe_print('  lens            以图片 URL 做 Lens 查询（参数是 URL）')
    safe_print('')
    safe_print('常用开关:')
    safe_print('  --json          包装后的 JSON 输出')
    safe_print('  --raw           原始 API JSON 输出')
    safe_print('  --compact       单行 JSON，方便管道')
    safe_print('  --save <file>   保存 JSON 到文件')
    safe_print('  --pick <N>      maps-reviews 选第 N 个地点')
    safe_print('  --all           maps-reviews 对全部地点抓评论')
    safe_print('  --limit <N>     限制 pretty 输出条数')
    safe_print('')
    safe_print('常用例子:')
    safe_print('  search.py web "OpenAI"')
    safe_print('  search.py maps "coffee shanghai"')
    safe_print('  search.py maps-reviews "coffee shanghai" --pick 2')
    safe_print('  search.py maps-reviews "coffee shanghai" --all --limit 3')
    safe_print('  search.py reviews --place-id ChIJ...')
    safe_print('  search.py webpage "https://openclaw.ai"')
    safe_print('  search.py lens "https://example.com/image.jpg" --json')
    safe_print('')
    safe_print('更多示例:')
    safe_print('  search.py examples')
    safe_print('')
    safe_print('自检:')
    safe_print('  python3 scripts/selfcheck.py')
    safe_print('  python3 scripts/selfcheck.py --full')
    safe_print('')
    safe_print('别名: overview / cheatsheet / quickref / help')


def print_examples():
    safe_print('📚 Google Search Skill · 示例命令')
    safe_print('=' * 36)
    safe_print('')
    safe_print('# 普通搜索')
    safe_print('search.py web "OpenAI"')
    safe_print('search.py "OpenAI" 3 1 us en')
    safe_print('')
    safe_print('# 新闻 / 图片 / 专利')
    safe_print('search.py news "OpenAI" --limit 5')
    safe_print('search.py images "cute cat" --json')
    safe_print('search.py patents "OpenAI" --raw')
    safe_print('')
    safe_print('# 地图 / 评论')
    safe_print('search.py maps "coffee shanghai"')
    safe_print('search.py maps-reviews "coffee shanghai" --pick 2 --limit 3')
    safe_print('search.py maps-reviews "coffee shanghai" --all --limit 2')
    safe_print('search.py reviews --place-id ChIJ...')
    safe_print('')
    safe_print('# 网页 / Lens')
    safe_print('search.py webpage "https://openclaw.ai"')
    safe_print('search.py lens "https://example.com/image.jpg" --json --compact')
    safe_print('')
    safe_print('# 机器可读输出')
    safe_print('search.py web "OpenAI" --json --save /tmp/serper.json')
    safe_print('search.py maps-reviews "coffee shanghai" --all --raw --compact')


def render_results(endpoint, data, limit=10):
    endpoint_titles = {
        'search': '自然搜索结果',
        'images': '图片结果',
        'news': '新闻结果',
        'videos': '视频结果',
        'places': '地点结果',
        'maps': '地图结果',
        'reviews': '评论结果',
        'autocomplete': '自动补全',
        'shopping': '购物结果',
        'scholar': '学术结果',
        'patents': '专利结果',
        'lens': 'Lens 结果',
    }

    print_answer_box(data.get('answerBox', {}))
    print_knowledge_graph(data.get('knowledgeGraph', {}))

    if endpoint == 'search':
        print_organic_results(data.get('organic', []), title=endpoint_titles[endpoint], limit=limit)
        print_news(data.get('news', []), limit=limit)
        print_images(data.get('images', []), limit=limit)
        print_videos(data.get('videos', []), limit=limit)
        print_shopping(data.get('shopping', []), limit=limit)
        print_places(data.get('places', []), limit=limit)
        print_people_also_ask(data.get('peopleAlsoAsk', []), limit=min(limit, 10))
        print_related_searches(data.get('relatedSearches', []), limit=min(limit, 10))
    elif endpoint == 'images':
        print_images(data.get('images', []), limit=limit)
    elif endpoint == 'news':
        print_news(data.get('news', []), limit=limit)
    elif endpoint == 'videos':
        print_videos(data.get('videos', []), limit=limit)
    elif endpoint == 'places':
        print_places(data.get('places', []), limit=limit, title=endpoint_titles[endpoint])
    elif endpoint == 'maps':
        print_places(data.get('places', []) or data.get('maps', []), limit=limit, title=endpoint_titles[endpoint])
    elif endpoint == 'reviews':
        print_reviews(data.get('reviews', []) or data.get('organic', []), limit=limit)
    elif endpoint == 'autocomplete':
        print_autocomplete(data.get('suggestions', []) or data.get('autocomplete', []) or data.get('organic', []), limit=limit)
    elif endpoint == 'shopping':
        print_shopping(data.get('shopping', []), limit=limit)
    elif endpoint == 'scholar':
        print_organic_results(data.get('organic', []), title=endpoint_titles[endpoint], limit=limit)
    elif endpoint == 'patents':
        print_organic_results(data.get('organic', []), title=endpoint_titles[endpoint], limit=limit)
    elif endpoint == 'webpage':
        print_webpage(data)
    elif endpoint == 'lens':
        print_lens_results(data, limit=limit)
    else:
        print_organic_results(data.get('organic', []), title=endpoint_titles.get(endpoint, '结果'), limit=limit)

    print_pagination(data.get('pagination', {}))
    print_credits(data)
    print_search_parameters(data)


def serialize_json(payload, compact=False):
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def save_output(text, save_path):
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def emit_json_wrapper(endpoint, data, used_key, gl, hl, query, num, page, compact=False, save_path=None):
    payload = {
        'ok': True,
        'endpoint': endpoint,
        'query': query,
        'num': num,
        'page': page,
        'gl': gl,
        'hl': hl,
        'usedKeySuffix': used_key[-4:],
        'response': data,
    }
    text = serialize_json(payload, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def emit_raw_json(data, compact=False, save_path=None):
    text = serialize_json(data, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def summarize_response_shape(data):
    if not isinstance(data, dict):
        return {'topLevelType': type(data).__name__}

    list_lengths = {}
    for key, value in data.items():
        if isinstance(value, list):
            list_lengths[key] = len(value)

    return {
        'topLevelKeys': sorted(list(data.keys())),
        'listLengths': list_lengths,
        'hasOrganic': bool(data.get('organic')),
        'hasAnswerBox': bool(data.get('answerBox')),
        'hasKnowledgeGraph': bool(data.get('knowledgeGraph')),
        'hasCredits': 'credits' in data,
        'hasSearchParameters': isinstance(data.get('searchParameters'), dict),
    }
