"""Choose and assign scene media from local assets and available search sources."""
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import image_search, stock_video
from .media import ASSETS, DATA
from .visual_fallback import text_scene
from .narration import has_stem
from .models import Project
from .network import connection_error

ROOT = DATA / 'auto-media'
ROOT.mkdir(exist_ok=True)


def tokens(text):
    return {word[:6] for word in re.findall(r'[a-zа-яё]{3,}', text.lower())
            if word not in image_search.STOP and word not in {'jpg','jpeg','png','mp4','file','image','video','old','ancient','historical','illuminated','person','human','photo','picture','landscape','footage'}}


def topic_words(text):
    expanded=text
    for stems,query,_ in image_search.CONCEPTS:
        if has_stem(stems,text):expanded+=' '+query
    for stems,query in [(('горы','горах','горная','горный','горам'),'mountain path'),
                        (('путник','поход','тропа','идущий'),'hiking solitary person path'),
                        (('книга','книги','книгой'),'book manuscript'),
                        (('океан','море','морской'),'ocean coastline')]:
        if has_stem(stems,text):expanded+=' '+query
    return tokens(expanded)


def scene_context(project, index):
    context = image_search.scene_query(project, index)
    footage = stock_video.footage_query(project, index)
    scene = project.scenes[index]
    text = (context['basis']+' '+scene.title+' '+scene.caption).lower()
    earlier=' '.join(c.text for c in project.subtitles if c.start<sum(round(x.duration*30) for x in project.scenes[:index+1])/30)
    historical=has_stem(('енох','enoch','древн','старин'),earlier) and has_stem(('книг','рукопис','book','manuscript'),earlier) and not has_stem(('исследован','эксперимент','сегодня','современн','research'),text)
    archive = any(s in text for s in ('рукопис','манускрип','енох','эфиоп','греческ','писание','папирус','летопис','документ','архив'))
    return {**context, 'historical':historical, 'semantic': project.semantic_media, 'video_query':footage['query'], 'preferred':'image' if archive else 'video',
            'words':topic_words(context['query']+' '+footage['query']+' '+text)}


def graphic_indices(project):
    """Reserve at most two real spoken emphasis points for full-screen typography."""
    if len(project.scenes)<5:return set()
    candidates=[]
    cursor=0
    for index,scene in enumerate(project.scenes):
        start=cursor/30;cursor+=round(scene.duration*30);end=cursor/30
        spoken=' '.join(c.text for c in project.subtitles if c.start<end and c.end>start)
        events=[e for e in scene.text_events if e.tier=='major']
        score=max((5 if e.layout in ('grid','number','counter') else 3 if e.treatment in ('neon','chromatic','italic') else 1 for e in events),default=0)
        if re.search(r'(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)|[«“][^»”]{8,120}[»”]',spoken):score=max(score,4)
        if re.search(r'\b(?:семь|7)\s+признак\w*|\b(?:список|перечень)\b',spoken,re.I):score=max(score,3)
        if score:candidates.append((score,index))
    if not candidates:return set()
    budget=1 if len(project.scenes)<10 else 2
    chosen=[]
    for _,index in sorted(candidates,key=lambda pair:(-pair[0],pair[1])):
        if any(abs(index-old)<2 for old in chosen):continue
        chosen.append(index)
        if len(chosen)>=budget:break
    return set(chosen)


def search_contexts(context, kind):
    """Exact subject first; related period visuals keep variety without changing facts."""
    query=context['query'] if kind=='image' else context['video_query']
    options=[(query,context['concept'])]
    if has_stem(('enoch',),context['query']):
        options += ([('Ethiopian illuminated manuscript','Эфиопская рукопись'),
                     ('ancient manuscript parchment','Старинная рукопись')]
                    if kind=='image' else
                    [('old book pages','Листание старинной книги'),
                     ('ancient manuscript','Старинная рукопись')])
    elif has_stem(('ethiop',),query):
        options += [('Ethiopic manuscript','Эфиопская рукопись'),('Ethiopia manuscript','Эфиопская рукопись')]
    elif has_stem(('greek',),query):
        options += [('Greek manuscript codex','Греческая рукопись')]
    result=[]
    for phrase,concept in dict.fromkeys(options):
        variant={**context,'query':phrase,'story_query':context['query'],'concept':concept,'words':topic_words(phrase+' '+context['basis'])}
        result.append(variant)
    return result


def preferred_kinds(context, mode, index):
    if mode == 'photos': return ['image']
    if mode == 'videos': return ['video']
    # In mixed mode, seek real motion on occasional beats even in archival stories.
    # The relevance check still rejects unrelated stock and then tries photos.
    if index % 3 == 2:return ['video','image']
    return [context['preferred'], 'video' if context['preferred']=='image' else 'image']


def asset_words(asset):
    source = asset.get('provenance',{})
    return topic_words(' '.join(str(x) for x in (asset.get('name',''), source.get('query',''), source.get('description',''))))


def asset_kind(item):
    return item.get('kind') or ('video' if item.get('provider') else 'image')


def rank_candidate(item, context, duration, order=0, local=False):
    words = asset_words(item) if local else topic_words(str(item.get('title',''))+' '+str(item.get('description','')))
    overlap = len(words & context['words'])
    # Local files need an explicit text match; remote search order supplies a relevance signal.
    score = overlap*2 + (0 if local else 2/(order+1))
    from .semantic import similarity
    source = item.get('provenance', {})
    meaning = similarity(context.get('basis') or context['query'], ' '.join(str(v) for v in
                         (item.get('name',''), item.get('title',''), item.get('description',''), source.get('description','')))) if context.get('semantic', True) else None
    if meaning is not None and context.get('semantic', True):
        score += 8 * meaning
    width,height = item.get('width',0),item.get('height',0)
    if width and height:
        score += max(-1,1-abs(width/height-16/9))*.5
    length = item.get('duration',0)
    if asset_kind(item)=='video':
        score += .4 if length>=duration else -.4
    return score



def relevant_candidate(item, context, *, local=False):
    """Require described subject evidence; provider rank alone is insufficient.

    Provider queries are metadata, not proof of what an image depicts. Avoid
    choosing the wrong country/manuscript merely because both mention 'book'.
    """
    source=item.get('provenance',{}) if local else {}
    description=' '.join(str(v) for v in (item.get('name',''),item.get('title',''),item.get('description',''),source.get('description','')))
    words=topic_words(description)
    query=context['query'].lower()
    story_query=context.get('story_query','').lower()
    if has_stem(('enoch',),story_query) and not has_stem(('greek','greece'),query) and has_stem(('greek','greece','гречес','греч'),description):return False
    named_book=has_stem(('enoch','енох'),query) and has_stem(('enoch','енох'),description) and has_stem(('book','книг'),description)
    primary=[(('manuscript','parchment'),('manuscript','рукопис','манускрип','papyrus','папирус','parchment','пергамент','codex','кодекс','illuminated')),
             (('mountain','hiking'),('mountain','горы','горах','горн','hiking','поход','тропа')),
             (('map',),('map','карта','картограф')),
             (('candle',),('candle','свеч'))]
    for triggers,required in primary:
        if has_stem(triggers,query) and not has_stem(required,description) and not (triggers[0]=='manuscript' and named_book):return False
    historical=context.get('historical',False) or has_stem(('manuscript','parchment','ancient','enoch','ethiop','greek','old book'),query)
    if historical and has_stem(('tourist','hiking','camping','turtle','coffee','lilac','bouquet','турист','поход','черепах','кофе','сирен','букет'),description) and not has_stem(('турист','поход','черепах','кофе','сирен','букет'),context.get('basis','')):return False
    if historical and has_stem(('laptop','notebook','ballpoint','computer','office','homework','ноутбук','тетрад','шариков','компьютер','офис'),description):return False
    if has_stem(('old book',),query) and not has_stem(('old','antique','ancient','vintage','aged','historical','manuscript','parchment','старин','древн','рукопис','пергамент'),description):return False
    entities={'ethiop':('эфиоп','ethiop'),'greek':('гречес','greek'),
              'greece':('грец','греч','greek','greece'),'enoch':('енох','enoch'),
              'egypt':('егип','egypt'),'rome':('рим','rome','roman')}
    for entity,aliases in entities.items():
        if has_stem((entity,),query) and not has_stem(aliases,description):return False
    # The country alone does not turn a church or a skyline into a manuscript.
    subjects=[(('manuscript',),('manuscript','рукопис','манускрип','papyrus','папирус','parchment','пергамент','codex','кодекс','book','книг')),
              (('painting','fresco'),('painting','fresco','миниатюр','икон','живопис','роспис','картина'))]
    for triggers,required in subjects:
        if has_stem(triggers,query) and not has_stem(required,description):return False
    from .semantic import similarity
    meaning = similarity(context.get('basis') or context['query'], description) if context.get('semantic', True) else None
    # High semantic confidence can admit a cross-language synonym, but can
    # never bypass any of the subject/entity checks above.
    return bool(words & context['words']) or (context.get('semantic', True) and meaning is not None and meaning >= .58)


def query_variants(query):
    alternatives={'Book of Enoch manuscript':'Enoch manuscript',
                  'Ethiopian illuminated manuscript':'Ethiopic manuscript',
                  'Greek biblical manuscript':'Greek manuscript',
                  'illuminated manuscript parchment':'illuminated manuscript',
                  'solitary person mountain path':'person hiking mountains',
                  'ancient book':'old book pages'}
    return list(dict.fromkeys([query,alternatives.get(query,query)]))

def asset_identity(asset):
    return asset.get('provenance',{}).get('source_url') or asset.get('source_url') or asset.get('id')


def local_assets():
    items=[]
    for path in sorted(ASSETS.glob('*/meta.json')):
        try:
            item=json.loads(path.read_text())
            if item.get('kind') in ('image','video'):items.append(item)
        except (OSError,ValueError):continue
    return items


def assignment(index,asset,context,origin):
    is_video=asset['kind']=='video'
    # The editor can change the offset after automatic assignment.
    return {'scene_index':index,'asset':asset,'offset':0,
            'reason':f"{'Видео' if is_video else 'Фото'} · {context['concept']} · {origin}",
            'query':context['query']}


def arrange(project, mode, replace, update, cancelled, scene_indices=None, excluded_asset_ids=None):
    if project.semantic_media:
        from . import semantic
        state = semantic.status()
        if state['available'] and not state['ready'] and not cancelled():
            update({'progress':0,'message':'Готовим локальную смысловую модель. При первом запуске требуется интернет…'})
            state = semantic.prepare()
        update({'semantic':state})
    library=[a for a in local_assets() if a.get('created_at',0)>=project.library_since]
    excluded=set(excluded_asset_ids or [])
    excluded.update(asset_identity(a) for a in library if a['id'] in excluded)
    targets=set(scene_indices) if scene_indices is not None else set(range(len(project.scenes)))
    used={asset_identity(a) for a in library if any(s.asset_id==a['id'] and (i not in targets or not replace)
                                                    for i,s in enumerate(project.scenes))}
    available=[p for p in ('pexels','pixabay') if stock_video.get_key(p)]
    disabled=set(); cache={}; imported=[]; outcomes=[]; errors=[]; fallbacks=[]; usage={}
    minutes={};minute_usage={};cursor=0
    for i,scene in enumerate(project.scenes):
        minutes[i]=int((cursor/30)//60)
        for event in scene.text_events:
            if event.elapsed>0 or event.tier=='caption':continue
            bucket=int((cursor/30+event.start)//60)
            minute_usage[bucket]=minute_usage.get(bucket,0)+1
        if scene.automatic_text and not scene.text_events and i not in targets:
            minute_usage[minutes[i]]=minute_usage.get(minutes[i],0)+1
        cursor+=round(scene.duration*30)
    total=len(targets) or 1

    def publish(index,message):
        update({'progress':round(index/total*100),'message':message,'assignments':list(imported),
                'outcomes':list(outcomes),'errors':list(dict.fromkeys(errors)), 'fallbacks':list(fallbacks),'sources':{'connected':available,'limited':sorted({p for p,k in disabled})}})

    for index,scene in enumerate(project.scenes):
        if index not in targets:continue
        if cancelled():break
        if scene.template in ('quote','thesis') and not scene.automatic_text:
            outcomes.append({'scene_index':index,'status':'skipped','reason':'Текстовая сцена использует собственный фон.'})
            publish(len(outcomes),f'Сцена {index+1}: текстовый фон');continue
        if scene.asset_id and not replace:
            outcomes.append({'scene_index':index,'status':'kept','reason':'Сохранён назначенный материал.'})
            publish(len(outcomes),f'Сцена {index+1}: материал сохранён');continue
        try:context=scene_context(project,index)
        except ValueError as exc:
            if project.director_version>=9:
                fallback=text_scene(project,index)
                fallbacks.append({'scene_index':index,'scene':fallback.model_dump(),'reason':fallback.media_reason})
                outcomes.append({'scene_index':index,'status':'text','reason':fallback.media_reason})
            else:outcomes.append({'scene_index':index,'status':'missing','reason':str(exc)})
            publish(len(outcomes),f'Сцена {index+1}: нужен текст для поиска');continue
        publish(len(outcomes),f'Сцена {index+1}: {context["concept"]}')
        if context.get('text_only'):
            fallback=text_scene(project,index)
            fallbacks.append({'scene_index':index,'scene':fallback.model_dump(),'reason':fallback.media_reason})
            outcomes.append({'scene_index':index,'status':'text','reason':fallback.media_reason})
            publish(len(outcomes),f'Сцена {index+1}: текстовая композиция');continue
        chosen=None
        attempts=[]
        kinds=preferred_kinds(context,mode,index)
        seen_options=set();attempted_queries=set()
        searches={}
        for kind in kinds:
            # Compare providers/kinds at each specificity level; only broaden
            # again if every actual download from that round failed.
            seen_queries=set();searches[kind]=[]
            for variant in search_contexts(context,kind):
                for phrase in query_variants(variant['query']):
                    if phrase not in seen_queries:
                        searches[kind].append((variant,phrase));seen_queries.add(phrase)

        def collect(round_index):
            options=[]
            for kind in kinds:
                if round_index>=len(searches[kind]) or cancelled():continue
                variant,phrase=searches[kind][round_index]
                for item in library:
                    identity=asset_identity(item)
                    if (item['kind']==kind and item['id'] not in excluded and identity not in excluded and
                            identity not in used and identity not in seen_options and relevant_candidate(item,variant,local=True)):
                        score=rank_candidate(item,variant,scene.duration,local=True)+.7*(kind==kinds[0])
                        options.append((score,'local',item,variant));seen_options.add(identity)
                sources=(['commons']+available) if kind=='image' else available
                for provider in sources:
                    if cancelled():break
                    label='Wikimedia Commons' if provider=='commons' else provider.title()
                    if (provider,kind) in disabled:continue
                    key=(provider,kind,phrase)
                    if key in attempted_queries:continue
                    attempted_queries.add(key)
                    publish(len(outcomes),f'Сцена {index+1}: ищем {"фото" if kind=="image" else "видео"} · {label} · {phrase}')
                    attempt={'provider':label,'kind':kind,'query':phrase,'status':'empty'}
                    attempts.append(attempt)
                    if key not in cache:
                        try:
                            if provider=='commons':cache[key]=image_search.search_images(phrase)
                            elif kind=='image':cache[key]=stock_video.search_photos(provider,phrase)
                            else:cache[key]=stock_video.search_videos(provider,phrase)
                        except (ValueError,OSError) as exc:
                            message=connection_error(exc,label) if isinstance(exc,OSError) else str(exc)
                            errors.append(message);cache[key]=[]
                            attempt.update(status='error',message=message)
                            # Other providers stay independent of this failure.
                            disabled.add((provider,kind))
                    for order,item in enumerate(cache[key]):
                        identity=asset_identity(item)
                        if identity not in used and identity not in excluded and identity not in seen_options and relevant_candidate(item,variant):
                            score=rank_candidate(item,variant,scene.duration,order)+.7*(kind==kinds[0])
                            options.append((score,provider,item,variant));seen_options.add(identity)
                            attempt['status']='candidates'
            return options

        def download(options):
            for _,provider,item,variant in sorted(options,key=lambda row:row[0],reverse=True):
                if cancelled():break
                if (provider,asset_kind(item)) in disabled:continue
                try:
                    if provider=='local':asset=item
                    elif provider=='commons':asset=image_search.import_image(item['token'])
                    elif asset_kind(item)=='image':asset=stock_video.import_photo(item['token'])
                    else:asset=stock_video.import_video(item['token'])
                    if cancelled():break
                    if provider!='local':library.append(asset)
                    return assignment(index,asset,variant,'из библиотеки' if provider=='local' else 'Wikimedia' if provider=='commons' else provider.title())
                except (ValueError,OSError) as exc:
                    message=connection_error(exc,'Загрузка материала') if isinstance(exc,OSError) else str(exc)
                    errors.append(message)
                    if 'HTTP 429' in message:disabled.add((provider,asset_kind(item)))
                except Exception:
                    message='Не удалось прочитать один из найденных материалов. Пробуем следующий.'
                    errors.append(message)
            return None

        # A search hit is not a downloaded frame. If every primary candidate
        # fails, run the alternative queries on the remaining providers too.
        for round_index in range(max(len(queries) for queries in searches.values())):
            if round_index:publish(len(outcomes),f'Сцена {index+1}: пробуем другие запросы и доступные стоки…')
            chosen=download(collect(round_index))
            if chosen or cancelled():break
        if cancelled():break
        if not chosen and project.director_version>=9:
            # A relevant asset may return later, but never in adjacent scenes.
            previous=next((asset_identity(a['asset']) for a in reversed(imported) if a['scene_index']==index-1),None)
            if previous is None and index:
                previous=next((asset_identity(a) for a in library if a['id']==project.scenes[index-1].asset_id),None)
            for kind in kinds:
                for variant in search_contexts(context,kind):
                    reusable=[a for a in library if a['kind']==kind and a['id'] not in excluded and
                              asset_identity(a) not in excluded and asset_identity(a)!=previous and
                              usage.get(asset_identity(a),0)<2 and
                              relevant_candidate(a,variant,local=True)]
                    if reusable:
                        asset=max(reusable,key=lambda a:rank_candidate(a,variant,scene.duration,local=True)-usage.get(asset_identity(a),0)*2)
                        chosen=assignment(index,asset,variant,'повтор подходящего материала')
                        break
                if chosen:break
        if not chosen and project.director_version>=9 and minute_usage.get(minutes[index],0)<2 and not any('HTTP 429' in e for e in errors):
            fallback=text_scene(project,index)
            fallbacks.append({'scene_index':index,'scene':fallback.model_dump(),'reason':fallback.media_reason})
            outcomes.append({'scene_index':index,'status':'text','reason':fallback.media_reason})
            minute_usage[minutes[index]]=minute_usage.get(minutes[index],0)+1
        elif chosen:
            chosen['restore_visual']=scene.automatic_text
            imported.append(chosen);used.add(asset_identity(chosen['asset']))
            identity=asset_identity(chosen['asset']);usage[identity]=usage.get(identity,0)+1
            outcomes.append({'scene_index':index,'status':'assigned','reason':chosen['reason'],'attempts':attempts})
        else:
            tried=list(dict.fromkeys(a['provider'] for a in attempts))
            limited=[('Wikimedia' if provider=='commons' else provider.title()) for provider,kind in sorted(disabled) if kind in kinds]
            reason='Подходящий кадр пока не найден.'
            if tried:reason+=' Проверены: '+', '.join(tried)+'.'
            if limited:reason+=' Недоступны: '+', '.join(dict.fromkeys(limited))+'.'
            not_connected=[p.title() for p in ('pexels','pixabay') if p not in available]
            if not_connected:reason+=' Для дополнительных стоков подключите '+ ' / '.join(not_connected)+'.'
            reason+=' Можно посмотреть найденные сцены и заменить кадры.'
            outcomes.append({'scene_index':index,'status':'missing','reason':reason,'attempts':attempts})
        publish(len(outcomes),f'Обработано {len(outcomes)} из {total} сцен')
    return {'assignments':imported,'outcomes':outcomes,'fallbacks':fallbacks,'errors':list(dict.fromkeys(errors)),
            'sources':{'connected':available,'limited':sorted({p for p,k in disabled})}}


class ArrangementJobs:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.root.mkdir(exist_ok=True)
        self.lock=threading.Lock();self.pool=ThreadPoolExecutor(max_workers=1)
        self.states={};self.cancellations={}
        for file in self.root.glob('*/status.json'):
            try:
                state=json.loads(file.read_text())
                if state['status'] in ('queued','running','cancelling'):
                    state.update(status='interrupted',message='Подбор прерван перезапуском. Уже найденные материалы доступны; запустите подбор для оставшихся сцен.')
                    file.write_text(json.dumps(state,ensure_ascii=False))
                self.states[file.parent.name]=state
            except (OSError,ValueError,KeyError):continue

    def _write(self,identifier,state):
        folder=self.root/identifier
        temp=folder/'status.tmp';temp.write_text(json.dumps(state,ensure_ascii=False));temp.replace(folder/'status.json')

    def start(self,project,mode,replace,scene_indices=None,excluded_asset_ids=None):
        with self.lock:
            if any(s['status'] in ('queued','running','cancelling') for s in self.states.values()):
                raise ValueError('Дождитесь текущего подбора или остановите его.')
            identifier=uuid.uuid4().hex;(self.root/identifier).mkdir()
            (self.root/identifier/'project.json').write_text(project.model_dump_json(indent=2))
            state={'id':identifier,'status':'queued','progress':0,'message':'Подготовка автоматического подбора',
                   'assignments':[],'outcomes':[],'errors':[]}
            self.states[identifier]=state;self.cancellations[identifier]=threading.Event()
            self._write(identifier,state)
        self.pool.submit(self._run,identifier,project,mode,replace,scene_indices,excluded_asset_ids)
        return json.loads(json.dumps(state))

    def _run(self,identifier,project,mode,replace,scene_indices=None,excluded_asset_ids=None):
        event=self.cancellations[identifier]
        def update(changes):
            with self.lock:
                state=self.states[identifier]
                state.update(changes,status='cancelling' if event.is_set() else 'running')
                self._write(identifier,state)
        try:
            result=arrange(project,mode,replace,update,event.is_set,scene_indices,excluded_asset_ids)
            with self.lock:
                state=self.states[identifier]
                state.update(result,status='cancelled' if event.is_set() else 'done',progress=state['progress'] if event.is_set() else 100,
                             message='Подбор остановлен' if event.is_set() else f'Назначено материалов: {len(result["assignments"])}')
                self._write(identifier,state)
        except Exception:
            with self.lock:
                state=self.states[identifier];state.update(status='error',message='Подбор прерван из-за ошибки. Уже найденные материалы сохранены.')
                self._write(identifier,state)

    def get(self,identifier):
        with self.lock:
            if identifier not in self.states:raise KeyError(identifier)
            return json.loads(json.dumps(self.states[identifier]))

    def cancel(self,identifier):
        with self.lock:
            if identifier not in self.states:raise KeyError(identifier)
            if self.states[identifier]['status'] in ('queued','running','cancelling'):
                self.cancellations[identifier].set()
                self.states[identifier].update(status='cancelling',message='Останавливаем после текущего запроса…')
                self._write(identifier,self.states[identifier])
        return self.get(identifier)
