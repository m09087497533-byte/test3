"""Visible, sparse light transitions even when every shot is a document."""
from .models import frame_count


def apply_color_transitions(project):
    result=project.model_copy(deep=True)
    result.transitions_version=1
    cursor=0.;last_color=None;number=0
    for index,scene in enumerate(result.scenes[:-1]):
        cursor+=frame_count(scene)/30
        following=result.scenes[index+1]
        same=scene.asset_id and scene.asset_id==following.asset_id
        entering=any(e.tier=='major' and e.start<.7 for e in following.text_events)
        if same or entering:
            if scene.transition_out in ('light_leak','color_wash'):scene.transition_out='cut'
            continue
        due=(last_color is None and cursor>=8) or (last_color is not None and cursor-last_color>=14)
        existing=scene.transition_out in ('light_leak','color_wash')
        if due or existing:
            scene.transition_out=('light_leak','color_wash')[number%2]
            scene.transition_duration=.6 if number%2==0 else .7
            last_color=cursor;number+=1
    return result
