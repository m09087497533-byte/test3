"""Apply the 1018 look without moving cues, replacing media, or rebuilding shots."""
VERSION = 18
TRANSITIONS = ('light_leak','dissolve','zoom','color_wash','cut','slide')


def apply_reference_effects(project):
    result=project.model_copy(deep=True)
    result.effects_version=VERSION
    result.transition_sounds=False
    if result.sound_mode != 'off':
        result.sound_mode='auto'
    result.animate_titles=True
    for index,scene in enumerate(result.scenes):
        # Keep shot lengths, voice, assets and user offsets exactly as supplied.
        if index<len(result.scenes)-1:
            scene.transition_out=TRANSITIONS[index%len(TRANSITIONS)]
        for event in scene.text_events:
            if event.treatment=='chromatic':
                event.effect='stamp'
            elif event.treatment=='classic' and event.effect=='fade' and event.layout in ('center','label'):
                event.effect=('stamp','typewriter')[index%2]
                event.duration=.65
        if not scene.text_events and (scene.title.strip() or scene.caption.strip()) and scene.title_effect in ('inherit','static','fade'):
            scene.title_effect=('stamp','typewriter','perspective')[index%3]
            scene.title_duration=.65
    return result
