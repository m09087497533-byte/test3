"""Deterministic sparse accents: a shared plan for the UI and final audio."""
import math
from .models import Project, frame_count, total_duration


def sound_plan(project: Project) -> list[dict]:
    from .effects import boundary_style, transition_frames
    if project.sound_mode == 'off' or project.sfx_volume == 0:
        return []
    candidates = []
    cursor = 0.0
    for index, scene in enumerate(project.scenes):
        if scene.text_events and project.sound_mode=='auto':
            for event in scene.text_events:
                if event.elapsed>0:continue  # A carried-over accent must not sound twice.
                time=cursor+round((event.start+event.duration*.55)*30)/30
                if time<.25:continue
                style='swish' if event.effect in ('scroll','perspective') else 'impact' if event.layout in ('number','counter') else 'click'
                candidates.append(dict(time=time,style=style,kind='title',scene=index,score=5 if event.layout in ('grid','number','counter') else 2))
        elif (scene.title or scene.caption) and not (scene.automatic_text and project.sound_mode=='auto'):
            time = cursor + round(scene.title_delay*30)/30
            if project.sound_mode == 'manual':
                if scene.title_sound != 'none':
                    candidates.append(dict(time=time, style=scene.title_sound, kind='title', scene=index))
            elif time >= .25:
                style = ('swish' if scene.title_effect in ('scroll','slide','perspective') else
                         'impact' if scene.template in ('thesis','statement') else 'click')
                score = 5 if scene.template in ('thesis','statement') else 3
                candidates.append(dict(time=time, style=style, kind='title', scene=index, score=score))
        cursor += frame_count(scene)/30
        length = transition_frames(project,index)
        if index < len(project.scenes)-1:
            if project.sound_mode == 'manual':
                if length and project.transition_sounds:
                    candidates.append(dict(time=cursor+length/60,style=boundary_style(project,index),kind='transition',scene=index))
            else:
                style = boundary_style(project,index)
                if style != 'cut':
                    candidates.append(dict(time=cursor+length/60,style=style,kind='transition',scene=index,score=2))
    if project.sound_mode == 'manual':
        return sorted(candidates,key=lambda e:e['time'])
    # A finite budget, not independent random choices on every cut. Short clips may be silent.
    duration = total_duration(project)
    budget = int(math.floor(duration*project.sounds_per_minute/60+.5))
    if not budget:
        return []
    gap = max(3.0, min(8.0, 30/max(1,project.sounds_per_minute)))
    chosen = []
    for slot in range(budget):
        target = (slot+.5)*duration/budget
        eligible = [c for c in candidates if c['time'] < duration-.25 and
                    all(abs(c['time']-old['time']) >= gap for old in chosen)]
        if not eligible:
            continue
        best = max(eligible, key=lambda c:c['score']-abs(c['time']-target)*.35)
        chosen.append(best)
    return [{k:v for k,v in e.items() if k!='score'} for e in sorted(chosen,key=lambda e:e['time'])]
