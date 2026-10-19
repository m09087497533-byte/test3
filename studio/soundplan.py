"""Deterministic sparse accents: a shared plan for the UI and final audio."""
import math
from .models import Project, frame_count, total_duration


def sound_plan(project: Project) -> list[dict]:
    if project.sound_mode == 'off' or project.sfx_volume == 0:
        return []
    candidates = []
    cursor = 0.0
    for index, scene in enumerate(project.scenes):
        if scene.text_events:
            for event in scene.text_events:
                if event.elapsed > 0 or not event.text.strip():
                    continue  # Continuing text is already visible; do not retrigger.
                begin = cursor + round(event.start*30)/30
                finish = min(cursor+round(event.end*30)/30, begin+max(1,round(event.duration*30))/30)
                if event.decoration != 'none':
                    finish = min(cursor+round(event.end*30)/30, begin+max(event.duration,1.35))
                time = min(finish-1/30, begin+round(event.duration*.55*30)/30)
                if finish <= begin:
                    continue
                if project.sound_mode == 'manual':
                    if scene.title_sound == 'none':
                        continue
                    style = scene.title_sound
                else:
                    style = ('paper' if event.tier=='caption' else
                             'spark' if event.treatment == 'neon' else
                             'impact' if event.treatment=='red_ink' else
                             'stamp' if event.treatment == 'chromatic' or event.effect == 'stamp' else
                             'paper' if event.treatment == 'italic' else
                             'chime' if event.effect == 'typewriter' else
                             'sweep' if event.layout == 'grid' else
                             'swish' if event.effect in ('scroll','perspective') else
                             'impact' if event.layout in ('number','counter') else 'click')
                candidates.append(dict(time=time, start=begin, end=finish, style=style, kind='title', scene=index,
                                       score=5 if event.layout in ('grid','number','counter') else 2))
        elif scene.title.strip() or scene.caption.strip():
            begin = cursor + round(scene.title_delay*30)/30
            finish = min(cursor+(scene.title_end or scene.duration),begin+max(.18,scene.title_duration))
            if finish > begin:
                if project.sound_mode == 'manual':
                    style = scene.title_sound
                else:
                    style = ('stamp' if scene.title_effect == 'stamp' else
                             'chime' if scene.title_effect == 'typewriter' else
                             'swish' if scene.title_effect in ('scroll','slide','perspective') else
                             'impact' if scene.template in ('thesis','statement') else 'click')
                if style != 'none':
                    candidates.append(dict(time=begin, start=begin, end=finish, style=style, kind='title', scene=index,
                                           score=5 if scene.template in ('thesis','statement') else 3))
        # Transitions and photograph movement never create sound candidates,
        # including legacy projects with transition_sounds=True.
        cursor += frame_count(scene)/30
    if project.sound_mode == 'manual':
        return [{k:v for k,v in e.items() if k!='score'} for e in sorted(candidates,key=lambda e:e['time'])]
    # A finite budget, not independent random choices on every cut. Short clips may be silent.
    duration = total_duration(project)
    budget = int(math.floor(duration*project.sounds_per_minute/60+.5))
    if project.effects_version>=19 and duration>=12:
        budget=math.ceil(duration*project.sounds_per_minute/60)
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
