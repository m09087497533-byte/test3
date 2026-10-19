"""Shot decisions derived from the comparison, without moving voice or cue clocks."""
from .models import frame_count,TextEvent
from .narration import has_stem

VERSION=19


def apply_reference_effects(project):
    result=project.model_copy(deep=True)
    if project.effects_version<19 and project.director_version>=9 and project.subtitles:
        from .narration import emphasis_events
        events=emphasis_events(project.subtitles);cursor=0.
        for scene in result.scenes:
            end=cursor+frame_count(scene)/30
            if scene.text_events or (not scene.title.strip() and not scene.caption.strip()):
                had_events=bool(scene.text_events)
                scene.text_events=[]
                if had_events:
                    scene.title='';scene.caption='';scene.title_layout='caption';scene.title_delay=0;scene.title_end=None
                for event in events:
                    if event['start']>=end or event['end']<=cursor:continue
                    local={k:v for k,v in event.items() if k!='priority'}
                    local.update(start=max(0,event['start']-cursor),end=min(scene.duration,event['end']-cursor),
                                 elapsed=max(0,cursor-event['start']),span=event['end']-event['start'])
                    scene.text_events.append(TextEvent.model_validate(local))
                if scene.text_events:
                    first=scene.text_events[0]
                    scene.title=first.text;scene.title_layout=first.layout;scene.title_effect=first.effect
                    scene.title_delay=first.start;scene.title_end=first.end
            cursor=end
    result.effects_version=VERSION
    result.transition_sounds=False
    if result.sound_mode!='off':result.sound_mode='auto'
    result.animate_titles=True
    result.photo_motion='smooth'
    cursor=0.;number=0
    for index,scene in enumerate(result.scenes):
        finish=cursor+frame_count(scene)/30
        basis=' '.join(c.text for c in result.subtitles if c.start<finish and c.end>cursor)
        document=has_stem(('рукопис','манускрип','книг','страниц','документ','manuscript','book'),basis+' '+scene.title)
        if document and scene.picture_effect=='plain':scene.picture_effect='inset'
        if document and scene.template=='archive':
            scene.template='cinema'
            if scene.picture_effect=='plain':scene.picture_effect='inset'
        major=next((e for e in scene.text_events if e.tier=='major'),None)
        detail=document and (has_stem(('страниц','строк','букв','стих','детал','page','detail'),basis) or (index>0 and result.scenes[index-1].shot_role=='document' and scene.duration>=6))
        scene.shot_role='graphic' if scene.automatic_text else 'detail' if detail else 'document' if document else 'context'
        scene.camera_style='hold' if scene.automatic_text else 'detail' if scene.shot_role=='detail' else 'push' if document else 'pull' if index%2 else 'left'
        scene.camera_amount=.085 if document else .055
        if scene.picture_effect=='split':scene.camera_style='hold'
        scene.camera_beat=max(.5,major.start-.35) if major else scene.duration*.52 if scene.duration>=6 else None
        scene.motion=not scene.automatic_text
        scene.edit_reason=('Деталь страницы: плавное укрупнение перед акцентом.' if scene.shot_role=='detail' else
                           'Документ: спокойный наезд, края страницы сохраняются.' if document else
                           'Общий план: мягкое движение без повторяющихся зумов.')
        for event in scene.text_events:
            if event.layout=='grid':event.effect='marquee'
            if event.tier=='caption':
                event.layout='label';event.treatment='classic';event.effect='typewriter';event.duration=.45
            elif event.layout=='number':
                if not event.elapsed:
                    event.treatment=('red_ink','neon','chromatic')[number%3];number+=1
                elif index:
                    previous=next((e for e in result.scenes[index-1].text_events if e.text==event.text),None)
                    if previous:event.treatment=previous.treatment
            elif event.effect=='perspective':event.treatment='editorial'
        if not scene.text_events and (scene.title.strip() or scene.caption.strip()) and scene.title_effect in ('inherit','static','fade'):
            scene.title_effect='perspective';scene.title_duration=.65
        if scene.template in ('quote','thesis') and not scene.title.strip() and not scene.caption.strip() and not scene.text_events:
            scene.template='cinema';scene.automatic_text=False
            if not scene.asset_id:scene.media_reason='Ожидает визуал: пустая карточка требует материала.'
        if scene.automatic_text and not scene.asset_id and scene.text_events:
            visible=sum(e.end-e.start for e in scene.text_events)
            if visible<scene.duration-.25:
                scene.template='cinema';scene.automatic_text=False
                scene.media_reason='Ожидает визуал: под короткой надписью нужен кадр на всю сцену.'
        cursor=finish
    starts={};ends={};cursor=0.
    for scene in result.scenes:
        for event in scene.text_events:
            key=(event.text,round(cursor+event.start-event.elapsed,3))
            starts[key]=cursor+event.start-event.elapsed
            ends[key]=max(ends.get(key,0),cursor+event.end)
        cursor+=frame_count(scene)/30
    cursor=0.
    for scene in result.scenes:
        for event in scene.text_events:
            key=(event.text,round(cursor+event.start-event.elapsed,3))
            if event.span is None:event.span=ends[key]-starts[key]
        cursor+=frame_count(scene)/30
    last_accent=-30.;cursor=0.
    for index,scene in enumerate(result.scenes[:-1]):
        cursor+=frame_count(scene)/30
        following=result.scenes[index+1]
        scene.transition_out='cut';scene.transition_duration=.23
        entering_text=any(e.tier=='major' and e.start<.5 for e in following.text_events)
        if entering_text or (scene.asset_id and scene.asset_id==following.asset_id):continue
        if following.shot_role=='detail' and cursor-last_accent>=8:
            scene.transition_out='zoom';scene.transition_duration=.3;last_accent=cursor
        elif scene.shot_role!=following.shot_role and following.shot_role=='document' and cursor-last_accent>=12:
            scene.transition_out='light_leak';scene.transition_duration=.3;last_accent=cursor
        elif scene.shot_role=='document' and following.shot_role=='document':scene.transition_out='dissolve'
    return result
