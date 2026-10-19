"""Automatic documentary composition from timed narration, without scene setup."""
import math
import re
from .models import Project, Scene, TextEvent, frame_count
from .narration import emphasis_events,has_stem
from .limits import MAX_SCENES
from .reference_effects import apply_reference_effects


def story_boundaries(cues, end, slots=MAX_SCENES):
    """Hold a meaningful phrase group, typically 6–9 seconds, opening 4–5s."""
    points=[0]
    while len(points)<slots:
        start=points[-1]
        target=max(195, math.ceil((end-start)/(slots-len(points)+1)))
        if start==0:target=135
        if end-start<=target+60:break
        desired=start+target
        if end-desired<120:break
        candidates=[]
        for i,cue in enumerate(cues):
            frame=round(cue.start*30)
            if not start+105<=frame<=min(end-90,desired+60) or frame<desired-60:continue
            previous=cues[i-1] if i else None
            sentence=bool(previous and previous.text.rstrip().endswith(('.', '!', '?', '…')))
            gap=max(0,cue.start-previous.end) if previous else 0
            score=abs(frame-desired)-25*sentence-min(30,gap*30)
            candidates.append((score,frame))
        points.append(min(candidates)[1] if candidates else desired)
    points.append(end)
    # A clear switch of named subject can justify a shorter shot; a fixed timer
    # alone cannot. Keep tiny passing mentions inside their surrounding block.
    previous=None
    for cue in cues:
        lower=cue.text.lower()
        entity=next((key for key,stems in [('enoch',('енох','enoch')),('ethiopia',('эфиоп','ethiop')),('greek',('гречес','greek'))] if any(s in lower for s in stems)),None)
        if entity:
            frame=round(cue.start*30)
            if previous and entity!=previous and len(points)-1<slots:
                before=max((p for p in points if p<=frame),default=0)
                after=min((p for p in points if p>=frame),default=end)
                if frame-before>=75 and after-frame>=75:points.append(frame);points.sort()
            previous=entity
    return points


def accent(cue):
    text=cue.text.replace('\n',' ').strip()
    if re.search(r'\b(?:1[5-9]\d{2}|20\d{2})\b',text):return 5,'number','reveal','ring'
    if re.search(r'(?i)\b(?:семь|7|seven)\b.*(?:признак|sign)',text):return 4,'center','scroll','none'
    if re.search(r'(?i)(?:не\s+наказан|не\s+.*наказан|не\s+.*punish)',text):return 3,'center','perspective','none'
    if re.search(r'(?i)(?:увидите|воздаяни|результат|единственн)',text):return 2,'center','perspective','underline'
    return 0,'caption','fade','none'


def automatic_project(project, *, rebuild=True):
    """Automatic defaults are applied on import and with one rebuild action.

    Absolute subtitle/voice timing is retained. Existing material can only carry
    over when a new shot starts in its old interval; the picker rechecks it.
    """
    if not project.subtitles:
        from types import SimpleNamespace
        scenes=[]
        for original in project.scenes:
            scene=original.model_copy(deep=True)
            score,layout,effect,decoration=accent(SimpleNamespace(text=scene.title))
            scene.title_layout=layout;scene.title_effect=effect
            scene.title_decoration=decoration;scene.title_duration=.75
            scene.motion=False;scene.transition_out='cut'
            if 'рукопис' in (scene.title+' '+scene.caption).lower():scene.picture_effect='inset'
            scenes.append(scene)
        return apply_reference_effects(project.model_copy(update={'scenes':scenes,'director_version':12,'photo_motion':'static','transition':'cut',
                                          'sound_mode':'auto','sounds_per_minute':4,'sfx_volume':.28}))
    end=sum(frame_count(s) for s in project.scenes)
    points=story_boundaries(project.subtitles,end) if rebuild else [0]
    if not rebuild:
        for s in project.scenes:points.append(points[-1]+frame_count(s))
    scenes=[]
    events=emphasis_events(project.subtitles)
    for index,(a,b) in enumerate(zip(points,points[1:])):
        start,finish=a/30,b/30
        cues=[c for c in project.subtitles if c.start<finish and c.end>start]
        scene=Scene(duration=(b-a)/30,motion=False,transition_out='cut')
        old_start=0
        for old in project.scenes:
            old_end=old_start+frame_count(old)
            if old_start<=a<old_end:
                scene.asset_id=old.asset_id
                scene.media_reason=old.media_reason
                scene.offset=min(36000,old.offset+(a-old_start)/30)
                break
            old_start=old_end
        basis=' '.join(c.text for c in cues).lower()
        if any(w in basis for w in ('рукопис','манускрип','греческ','эфиоп','manuscript','папирус','книг','book')):
            scene.picture_effect='inset'
        if index==0:
            title=re.search(r'(?i)книг[аиуе]\s+([А-ЯЁA-Z][а-яёa-z]+)', ' '.join(c.text for c in cues))
            if title:
                scene.picture_effect='split';scene.document_title='Книга '+title.group(1)
                scene.document_caption='Последняя глава' if has_stem(('последн',),basis) else ''
        for event in events:
            if event['start']>=finish or event['end']<=start:continue
            local={key:value for key,value in event.items() if key!='priority'}
            local['span']=event['end']-event['start']
            local['elapsed']=max(0,start-event['start'])
            local['start']=max(0,event['start']-start)
            local['end']=min(scene.duration,event['end']-start)
            scene.text_events.append(TextEvent.model_validate(local))
        if scene.text_events:
            event=scene.text_events[0]
            scene.title=event.text;scene.title_layout=event.layout;scene.title_effect=event.effect
            scene.title_delay=event.start;scene.title_end=event.end
            scene.title_duration=event.duration;scene.title_decoration=event.decoration
        # Sparse soft changes of composition, rather than an effect on every cut.
        if index and scenes[-1].picture_effect!=scene.picture_effect:
            scenes[-1].transition_out='dissolve'
        elif index and has_stem(('эфиоп','гречес'),basis) and scene.picture_effect=='inset':
            scenes[-1].transition_out='slide'
        elif index and has_stem(('худши','испытан','испытыв'),basis):
            scenes[-1].transition_out='blur'
        elif index and index%3==0:
            scenes[-1].transition_out='dissolve'
        scenes.append(scene)
    return apply_reference_effects(Project.model_validate({**project.model_dump(),'scenes':[s.model_dump() for s in scenes],
                                   'transition':'cut','photo_motion':'static','animate_titles':True,
                                   'sound_mode':'auto','sounds_per_minute':4,'sfx_volume':.28,
                                   'director_version':12}))
