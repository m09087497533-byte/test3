"""Context-aware Commons image discovery; no API key or arbitrary URL downloads."""
import html
import io
import json
import re
import shutil
import threading
import time
import uuid
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict

from PIL import Image, ImageOps
from .media import ASSETS
from .network import https_handler,open_with_retry
from .models import Project, frame_count
from .narration import has_stem

USER_AGENT = 'KadrStudio/0.4 (local documentary video editor; Wikimedia Commons image search)'
HOSTS = {'commons.wikimedia.org','upload.wikimedia.org'}
API = 'https://commons.wikimedia.org/w/api.php'
# Visual concepts, not literal translation of entire narrative sentences.
CONCEPTS = [
    (('эфиоп','ethiopi'), 'Ethiopian illuminated manuscript', 'Эфиопская рукопись'),
    (('енох','enoch'), 'Book of Enoch manuscript', 'Книга Еноха'),
    (('греческ','greek'), 'Greek biblical manuscript', 'Греческая рукопись'),
    (('рукопис','писание','манускрипт','стих за стих'), 'illuminated manuscript parchment', 'Рукопись'),
    (('монастыр','monaster'), 'monastery mountains', 'Монастырь'),
    (('ангел','angel'), 'angel illuminated manuscript', 'Ангел в миниатюре'),
    (('церк','храм','church'), 'historic church architecture', 'Храм'),
    (('испытан','испытыв','худши','одиноч','трудност'), 'solitary person mountain path', 'Путь и испытание'),
    (('наказан','суд','воздаян'), 'Last Judgment painting', 'Суд и воздаяние'),
    (('свеч','candle'), 'candle old book', 'Свеча и книга'),
    (('психолог','исследован','эксперимент'), 'psychology research experiment', 'Исследование'),
    (('статист','график','диаграм'), 'statistical chart', 'Статистика'),
    (('космос','вселенн','space'), 'galaxy nebula', 'Космос'),
    (('планет','земля','earth'), 'Earth from space', 'Земля'),
    (('вулкан','volcano'), 'volcano eruption', 'Вулкан'),
    (('океан','море','ocean'), 'ocean coastline', 'Океан'),
    (('лес','forest'), 'forest landscape', 'Лес'),
    (('город','city'), 'city skyline', 'Город'),
    (('мозг','нейрон','brain'), 'human brain neurons', 'Мозг'),
    (('сердц','heart'), 'human heart anatomy', 'Сердце'),
    (('рим','rome'), 'ancient Rome ruins', 'Древний Рим'),
    (('егип','egypt'), 'ancient Egypt papyrus', 'Древний Египет'),
    (('войн','war'), 'historical war photograph', 'История войны'),
    (('музык','music'), 'musical instrument orchestra', 'Музыка'),
    (('животн','animal'), 'wildlife animals', 'Животные'),
]
STOP = set('это как что чтобы уже так того этой этих она они он мы вы вам вас его её для про при над под был была были будет может можно нужно своей свою сами сам один семь тип жизни люди людей результат ближайшие увидите каждый которым сможете ходу примерить намеренно говорит называет перечисляет'.split())
_cache = OrderedDict()
_lock = threading.Lock()


def scene_query(project: Project, index: int) -> dict:
    scene = project.scenes[index]
    start = sum(frame_count(s) for s in project.scenes[:index])/30
    end = start+frame_count(scene)/30
    cues = ' '.join(c.text.replace('\n',' ') for c in project.subtitles if c.start < end and c.end > start)
    spoken=cues.lower()
    if re.search(r'\b(?:карт\w*|map)\b',spoken):
        places=[(('эфиоп','ethiop'),'Ethiopia map','Карта Эфиопии'),
                (('грец','греч','greek','greece'),'Greece map','Карта Греции'),
                (('рим','rome','roman'),'Rome map','Карта Рима'),
                (('егип','egypt'),'Egypt map','Карта Египта')]
        for stems,query,concept in places:
            if has_stem(stems,spoken):return {'query':query,'concept':concept,'basis':cues}
    # Rank the concepts actually spoken during this shot. A mention in a tiny
    # overlap must not override the phrase occupying most of the shot.
    weighted=[]
    scopes={};carry=''
    for cue in project.subtitles:
        combined=(carry+' '+cue.text.replace('\n',' ')).strip()
        scopes[id(cue)]=combined
        carry=re.split(r'[.!?]',combined)[-1]
    for cue in project.subtitles:
        overlap=max(0,min(end,cue.end)-max(start,cue.start))
        if overlap:
            weighted.append((cue.text.lower(),overlap,scopes[id(cue)].lower()))
    if not weighted:weighted=[((scene.title+' '+scene.caption).lower(),1,(scene.title+' '+scene.caption).lower())]
    def matches(stems,text,scope):
        if any(s in ('наказан','суд','воздаян') for s in stems) and re.search(r'не\s+(?:\w+\s+){0,3}наказан',scope or text):
            return False
        return has_stem(stems,text)
    negative=any(re.search(r'не\s+(?:\w+\s+){0,3}наказан',scope) for _,_,scope in weighted)
    scored=[]
    for order,(stems,query,concept) in enumerate(CONCEPTS):
        score=sum(weight for text,weight,scope in weighted if matches(stems,text,scope))
        if score:scored.append((score,-order,query,concept))
    if scored:
        _,_,query,concept=max(scored)
        # In this book narrative, "God tested people" and "scripture recorded"
        # describe the book's contents, not present-day hiking or office writing.
        if query in ('solitary person mountain path','illuminated manuscript parchment'):
            earlier=' '.join(c.text for c in project.subtitles if c.start<=start)
            if has_stem(('енох','enoch'),earlier):
                query,concept='Book of Enoch manuscript','Книга Еноха'
        return {'query':query,'concept':concept,'basis':cues or scene.title or scene.caption}
    # A multilingual model can distinguish a new topic from an anaphoric
    # continuation, even when that topic is absent from the legacy concept list.
    # The query always consists of actual spoken words, never invented entities.
    from .semantic import similarity
    preceding_text=' '.join(c.text for c in project.subtitles if c.end<=start)[-1500:]
    relation=similarity(cues,preceding_text) if project.semantic_media else None
    if relation is not None and relation < .35:
        words=[w for w in re.findall(r'[a-zа-яё][a-zа-яё-]{3,}',cues.lower()) if w not in STOP]
        keywords=[word for word,_ in Counter(words).most_common(5)]
        if keywords:return {'query':' '.join(keywords),'concept':'Новая тема сцены','basis':cues}
    # Abstract narration retains the nearest preceding concrete subject. Do not
    # borrow an entity first mentioned later in the story, or search filler words.
    preceding=[c for c in project.subtitles if c.start<=start]
    for cue in reversed(preceding):
        for stems,query,concept in CONCEPTS:
            if negative and any(s in ('наказан','суд','воздаян') for s in stems):continue
            if matches(stems,cue.text.lower(),scopes[id(cue)].lower()):
                earlier=' '.join(c.text for c in project.subtitles if c.start<=start)
                if query in ('solitary person mountain path','illuminated manuscript parchment') and has_stem(('енох','enoch'),earlier):
                    query,concept='Book of Enoch manuscript','Книга Еноха'
                return {'query':query,'concept':concept,'basis':cues or scene.title}
    if negative:return {'query':'','concept':'Текстовая композиция','basis':cues or scene.title,'text_only':True}
    text=(cues+' '+scene.title+' '+scene.caption).lower()
    words=[w for w in re.findall(r'[a-zа-яё][a-zа-яё-]{3,}',text) if w not in STOP]
    keywords=[word for word,_ in Counter(words).most_common(5)]
    if keywords:return {'query':' '.join(keywords),'concept':'Ключевые слова сцены','basis':cues or scene.title}

    raise ValueError('Добавьте текст сцены, субтитры или поисковую фразу.')


def safe_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Неподдерживаемый адрес источника изображения')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url,limit=4*1024*1024):
    safe_url(url)
    req=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'application/json,image/*'})
    with open_with_retry(urllib.request.build_opener(SafeRedirect(),https_handler()),req,20) as response:
        safe_url(response.geturl())
        content=response.read(limit+1)
        if len(content)>limit:
            raise ValueError('Файл источника слишком большой')
        return content


def plain(value):
    return html.unescape(re.sub(r'<[^>]*>',' ',value or '')).strip()[:800]


def search_images(query):
    params = dict(action='query',generator='search',gsrsearch=query+' filetype:bitmap',gsrnamespace=6,
                  gsrlimit=12,prop='imageinfo',iiprop='url|mime|size|extmetadata',iiurlwidth=1600,
                  iiextmetadatafilter='Artist|LicenseShortName|LicenseUrl|AttributionRequired|ImageDescription',format='json')
    data=json.loads(fetch(API+'?'+urllib.parse.urlencode(params)))
    if 'error' in data:
        raise ValueError('Wikimedia не выполнила поиск. Попробуйте изменить фразу.')
    results=[]
    pages=data.get('query',{}).get('pages',{}).values()
    for page in sorted(pages,key=lambda p:p.get('index',0)):
        info=next(iter(page.get('imageinfo',[])),{})
        if info.get('mime') not in ('image/jpeg','image/png','image/webp'):
            continue
        ext=info.get('extmetadata',{})
        field=lambda name:plain(ext.get(name,{}).get('value',''))
        license_name=field('LicenseShortName')
        if not license_name or info.get('width',0)<400 or info.get('height',0)<240:
            continue
        url=info.get('thumburl',info.get('url',''))
        try: safe_url(url); safe_url(info.get('descriptionurl',''))
        except ValueError: continue
        result=dict(token=uuid.uuid4().hex,title=plain(page.get('title','').removeprefix('File:')),
                    url=url,source_url=info['descriptionurl'],author=field('Artist'),
                    license=license_name,license_url=field('LicenseUrl'),description=field('ImageDescription'),
                    width=info.get('thumbwidth',info['width']),height=info.get('thumbheight',info['height']),query=query)
        with _lock:
            _cache[result['token']]=(time.monotonic(),result)
            while len(_cache)>256: _cache.popitem(last=False)
        results.append(result)
    return results


def import_image(token):
    with _lock:
        entry=_cache.get(token)
    if not entry or time.monotonic()-entry[0]>3600:
        raise ValueError('Результат поиска устарел. Выполните поиск ещё раз.')
    item=entry[1]
    content=fetch(item['url'],16*1024*1024)
    with Image.open(io.BytesIO(content)) as image:
        if image.width*image.height>24_000_000:
            raise ValueError('Слишком большое изображение')
        image.load()
        image=ImageOps.exif_transpose(image).convert('RGB')
        identifier=uuid.uuid4().hex
        folder=ASSETS/identifier
        folder.mkdir()
        try:
            image.save(folder/'image.jpg',quality=95)
            image.thumbnail((640,360)); image.save(folder/'thumb.jpg')
            meta=dict(id=identifier,name=item['title'][:160],kind='image',file='image.jpg',duration=0,created_at=time.time(),
                      has_audio=False,size=(folder/'image.jpg').stat().st_size,
                      provenance={key:item.get(key,'') for key in ('source_url','author','license','license_url','query','description')})
            (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False))
            return meta
        except Exception:
            shutil.rmtree(folder,ignore_errors=True)
            raise
