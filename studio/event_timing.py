"""Place visual accents on the same 30 fps clock as the encoded film."""
from .models import TextEvent


def scene_events(events, first_frame, last_frame, duration=None):
    result = []
    for event in events:
        begin, end = round(event['start'] * 30), round(event['end'] * 30)
        local_begin, local_end = max(first_frame, begin), min(last_frame, end)
        # A sub-frame overlap has no visible frame. In particular, it must not
        # become a mirrored title whose entrance rounds to the scene's end.
        if local_end <= local_begin:
            continue
        local = {key: value for key, value in event.items() if key != 'priority'}
        end_time=(local_end-first_frame)/30
        # Imported/manual scene durations may be fractional (e.g. 4.49s).
        # Keep their declared boundary valid; encoding rounds it to frame 135.
        if duration is not None:end_time=min(end_time,duration)
        local.update(start=(local_begin-first_frame)/30, end=end_time,
                     elapsed=(local_begin-begin)/30, span=(end-begin)/30)
        result.append(TextEvent.model_validate(local))
    return result
