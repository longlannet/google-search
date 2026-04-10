#!/usr/bin/env python3
from io_common import safe_print


def _first_present(item, *keys, default=None):
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return default


def _join_meta(*values):
    cleaned = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        cleaned.append(str(value))
    return ' · '.join(cleaned)


def print_knowledge_graph(kg):
    if not kg:
        return
    safe_print('\n🧠 知识卡片:')
    title = kg.get('title')
    kind = kg.get('type')
    description = kg.get('description')
    if title:
        safe_print(f"   标题: {title}")
    if kind:
        safe_print(f"   类型: {kind}")
    if description:
        safe_print(f"   描述: {description}")
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
        title_text = res.get('title', '无标题')
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
    safe_print('🤔 相关追问:')
    for i, item in enumerate(items[:limit], start=1):
        question = item.get('question') or '无问题文本'
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
        title = item.get('title', '无标题')
        link = item.get('link', '#')
        snippet = item.get('snippet') or ''
        source = item.get('source')
        date = item.get('date')
        meta = _join_meta(source, date)
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
        title = item.get('title', '无标题')
        link = item.get('link') or item.get('imageUrl') or '#'
        source = _first_present(item, 'source', 'domain')
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
        title = item.get('title', '无标题')
        link = item.get('link', '#')
        snippet = item.get('snippet') or ''
        source = item.get('source')
        date = item.get('date')
        meta = _join_meta(source, date)
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
        title = item.get('title', '无标题')
        link = item.get('link', '#')
        price = item.get('price')
        source = _first_present(item, 'source', 'seller')
        delivery = item.get('delivery')
        safe_print(f'{i}. {title}')
        meta = _join_meta(price, source, delivery)
        if meta:
            safe_print(f'   🏷️ {meta}')
        safe_print(f'   🔗 {link}')
    safe_print('-' * 30)


def print_places(items, limit=5, title='地点结果', show_ids=True):
    if not items:
        return
    safe_print(f'📍 {title}:')
    for i, item in enumerate(items[:limit], start=1):
        title_text = item.get('title') or item.get('name') or '无标题'
        address = _first_present(item, 'address', default='')
        phone = _first_present(item, 'phoneNumber', 'phone', default='')
        rating = item.get('rating')
        rating_count = item.get('ratingCount')
        place_id = _first_present(item, 'placeId', default='')
        cid = _first_present(item, 'cid', default='')
        fid = _first_present(item, 'fid', default='')
        link = _first_present(item, 'link', 'website', default='#')
        extras = []
        if address:
            extras.append(address)
        if phone:
            extras.append(phone)
        if rating is not None:
            rating_text = f'评分 {rating}'
            if rating_count is not None and rating_count != '':
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
        author = _first_present(item, 'author', 'user', 'name', 'title', default='匿名用户')
        if isinstance(author, dict):
            author = author.get('name') or author.get('title') or author.get('user') or str(author)
        rating = item.get('rating')
        date = _first_present(item, 'date', 'publishedAt', default='')
        text = _first_present(item, 'text', 'snippet', 'review', default='')
        source = _first_present(item, 'source', 'sourceName', default='')
        photos = _first_present(item, 'images', 'photos', default=[]) or []
        local_guide = item.get('localGuide')
        contribs = _first_present(item, 'reviews', 'reviewCount', 'contributions')
        safe_print(f'{i}. {author}')
        meta_bits = []
        if rating is not None:
            meta_bits.append(f'评分 {rating}')
        if date:
            meta_bits.append(str(date))
        if source:
            meta_bits.append(str(source))
        if local_guide:
            meta_bits.append('本地向导')
        if contribs is not None and contribs != '':
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
        safe_print('🔎 图片识别结果: 未找到匹配项。')
        return

    safe_print('🔎 图片识别匹配结果:')
    for i, item in enumerate(candidates[:limit], start=1):
        title = item.get('title', '无标题')
        link = item.get('link') or item.get('imageUrl') or '#'
        snippet = item.get('snippet') or ''
        source = _first_present(item, 'source', 'domain')
        safe_print(f'{i}. {title}')
        meta = _join_meta(source)
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
    safe_print(f'💳 配额消耗: {credits}')
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
        'lens': '图片识别结果',
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
