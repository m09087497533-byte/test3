"""Complete automatic films without random pictures or missing-media failures."""
import re
from .models import Scene, frame_count


def text_scene(project, index):
    scene=project.scenes[index]
    start=sum(frame_count(s) for s in project.scenes[:index])/30
    end=start+frame_count(scene)/30
    cues=[c for c in project.subtitles if c.start<end and c.end>start]
    text=' '.join(c.text.replace('\n',' ') for c in cues).strip() or scene.title or scene.caption or project.name
    if len(text)>160:text=text[:157].rsplit(' ',1)[0]+'…'
    from .narration import has_stem
    heading='ЭФИОПИЯ' if has_stem(('эфиоп','ethiop'),text) else 'ГРЕЧЕСКАЯ КОПИЯ' if has_stem(('гречес','greek'),text) else text
    body=text if heading!=text else ''
    layout='folio';effect='fade';decoration='none';template='thesis'
    year=re.search(r'(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)',text)
    count=re.search(r'(?<!\d)\d{1,6}\s+(?:человек|людей|участник\w*|people)(?!\w)',text,re.I)
    quote=re.search(r'[«“]([^»”]{8,120})[»”]',text)
    if count:
        heading=count.group().upper();body=text;layout='counter';effect='reveal'
    elif year:
        heading=year.group();body=text;layout='number';effect='reveal';decoration='ring'
    elif quote:
        heading=quote.group(1);body='';template='quote';layout='center'
    elif has_stem(('глав','спис','признак','пункт'),text):
        heading=text;body='';layout='grid';effect='scroll'
    if scene.text_events:
        event=scene.text_events[0]
        heading=event.text;body='';layout=event.layout;effect=event.effect;decoration=event.decoration
    values=scene.model_dump()
    values.update(text_events=scene.text_events,automatic_text=True,template=template,asset_id=None,offset=0,title=heading,caption=body,
                  title_layout=layout,title_effect=effect,title_delay=0,title_end=None,
                  title_duration=.4,title_decoration=decoration,title_exit=False,
                  title_sound='none',picture_effect='plain',motion=False,
                  media_reason='Графика из текста озвучки: подходящий фото- или видеоматериал не найден.')
    return Scene.model_validate(values)


def complete_visuals(project):
    """Offline last resort for saved v9 projects and interrupted media searches.

    Reuse a described matching picture if available; otherwise compose supplied
    narration as typography. Never replace it with a random demo landscape.
    """
    if project.director_version<9:return project
    from .auto_media import local_assets,scene_context,search_contexts,relevant_candidate,rank_candidate,topic_words
    result=project.model_copy(deep=True)
    library=[a for a in local_assets() if a.get('created_at',0)>=project.library_since]
    usage={}
    minutes={};minute_usage={};cursor=0
    for i,scene in enumerate(result.scenes):
        minutes[i]=int((cursor/30)//60)
        for event in scene.text_events:
            if event.elapsed>0:continue
            bucket=int((cursor/30+event.start)//60)
            minute_usage[bucket]=minute_usage.get(bucket,0)+1
        if scene.automatic_text and not scene.text_events:
            minute_usage[minutes[i]]=minute_usage.get(minutes[i],0)+1
        cursor+=frame_count(scene)
    for index,scene in enumerate(result.scenes):
        previous=result.scenes[index-1].asset_id if index else None
        if not scene.asset_id and scene.media_reason.startswith('Ожидает визуал'):
            raise ValueError(f'Сцена {index+1} ждёт фото или футаж. Повторите автоматический подбор после восстановления доступа к источникам.')
        if scene.asset_id:
            # Revalidate previous automatic picks too; manual choices have no such reason.
            if not scene.media_reason.startswith(('Фото ·','Видео ·','Повтор подходящего материала')):continue
            from .media import asset_info
            context=scene_context(result,index)
            if scene.media_query:
                context['query']=scene.media_query
                context['words']=topic_words(scene.media_query+' '+context['basis'])
            if scene.asset_id!=previous and relevant_candidate(asset_info(scene.asset_id),context,local=True) and usage.get(scene.asset_id,0)<2:
                usage[scene.asset_id]=usage.get(scene.asset_id,0)+1
                continue
            scene.asset_id=None;scene.offset=0;scene.media_query=''
        if scene.template in ('quote','thesis'):continue
        try:
            context=scene_context(result,index)
            allowed={'photos':{'image'},'videos':{'video'},'mixed':{'image','video'}}[project.auto_media_mode]
            candidates=[]
            if not context.get('text_only'):
                for kind in allowed:
                    for variant in search_contexts(context,kind):
                        candidates.extend((a,variant) for a in library if a['kind']==kind and a['id']!=previous and usage.get(a['id'],0)<2 and relevant_candidate(a,variant,local=True))
        except ValueError:candidates=[]
        if candidates:
            asset,matched=max(candidates,key=lambda pair:rank_candidate(pair[0],pair[1],scene.duration,local=True))
            scene.asset_id=asset['id'];scene.offset=0;scene.motion=False
            scene.media_query=matched['query']
            usage[asset['id']]=usage.get(asset['id'],0)+1
            scene.media_reason='Повтор подходящего материала по теме: '+matched['concept']
        else:
            if project.director_version>=12 and minute_usage.get(minutes[index],0)>=2:
                raise ValueError(f'Для сцены {index+1} не найдено подходящее фото или видео. Повторите автоматический подбор после восстановления доступа к источникам.')
            result.scenes[index]=text_scene(result,index)
            minute_usage[minutes[index]]=minute_usage.get(minutes[index],0)+1
    return result
