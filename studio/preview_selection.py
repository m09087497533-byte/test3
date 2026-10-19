"""A disposable preview of filled scenes; the source montage stays untouched."""
from .models import Project, Subtitle, frame_count
from .soundplan import sound_plan


def has_visual(scene):
    if scene.asset_id:return True
    if scene.template not in ('quote','thesis'):return False
    if not (scene.title.strip() or scene.caption.strip() or scene.text_events):return False
    return not (scene.automatic_text and scene.text_events and
                sum(e.end-e.start for e in scene.text_events)<scene.duration-.25)


def select_preview(project, indices=None):
    selected=[i for i,s in enumerate(project.scenes) if has_visual(s)] if indices is None else indices
    if not selected:raise ValueError('Пока нет сцен с готовыми кадрами. Запустите подбор или добавьте свои материалы.')
    if (len(set(selected))!=len(selected) or selected!=sorted(selected) or
            any(i<0 or i>=len(project.scenes) for i in selected)):
        raise ValueError('Неверный список сцен для просмотра.')
    if any(not has_visual(project.scenes[i]) for i in selected):
        raise ValueError('Выбранная сцена ещё ждёт кадр.')
    starts=[];cursor=0
    for scene in project.scenes:
        starts.append(cursor);cursor+=frame_count(scene)
    scenes=[];cues=[];segments=[];events=[];dest=0
    original_sounds=sound_plan(project)
    for index in selected:
        scene=project.scenes[index].model_copy(deep=True)
        begin=starts[index]/30;end=(starts[index]+frame_count(scene))/30;offset=dest/30
        scenes.append(scene)
        segments.append(dict(scene_index=index,source_start=begin,source_end=end,
                             preview_start=offset,preview_end=offset+frame_count(scene)/30))
        dest+=frame_count(scene)
    runs=[]
    for segment in segments:
        if runs and runs[-1]['source_end']==segment['source_start']:
            runs[-1]['source_end']=segment['source_end']
        else:runs.append(dict(segment))
    for run in runs:
        begin,end,offset=run['source_start'],run['source_end'],run['preview_start']
        for cue in project.subtitles:
            a,b=max(begin,cue.start),min(end,cue.end)
            if b>a:cues.append(Subtitle(start=offset+a-begin,end=offset+b-begin,text=cue.text))
        for event in original_sounds:
            a,b=max(begin,event['start']),min(end,event['end'])
            if b>a:
                events.append({**event,'start':offset+a-begin,'end':offset+b-begin,'time':offset+event['time']-begin})
    for i,scene in enumerate(scenes):
        if i==len(scenes)-1 or selected[i+1]!=selected[i]+1:scene.transition_out='cut'
    preview=Project.model_validate({**project.model_dump(),'scenes':[s.model_dump() for s in scenes],
                                    'subtitles':[c.model_dump() for c in cues]})
    return preview,segments,events
