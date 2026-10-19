import math
import re
import html

from .models import Scene, Subtitle
from .limits import MAX_PROJECT_SECONDS, MAX_SCENES, MAX_SUBTITLES, DURATION_LIMIT_MESSAGE

TIMESTAMP = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
TIMING = re.compile(r'^' + TIMESTAMP + r'\s*-->\s*' + TIMESTAMP + r'\s*$')
VTT_TIME = r'(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})'
VTT_TIMING = re.compile(r'^' + VTT_TIME + r'\s+-->\s+' + VTT_TIME + r'(?:\s+.*)?$')


def parse_subtitles(source: str) -> list[Subtitle]:
    normalized = source.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not re.match(r'^WEBVTT(?:[ \t\n]|$)', normalized):
        return parse_srt(normalized)
    blocks = re.split(r'\n[ \t]*\n', normalized)
    result = []
    for block in blocks[1:]:
        lines = block.split('\n')
        if re.match(r'^(NOTE(?:\s|$)|STYLE\s*$|REGION\s*$)', lines[0]):
            continue
        if '-->' not in lines[0]:
            lines = lines[1:]  # Optional WebVTT cue identifier.
        match = VTT_TIMING.match(lines[0].strip()) if lines else None
        if not match or len(lines) < 2:
            raise ValueError('Не удалось прочитать VTT: нужен таймкод и текст в каждом блоке')
        groups = tuple(v or '0' for v in match.groups())
        text = '\n'.join(lines[1:]).strip()
        # WebVTT styling, speaker labels and karaoke timestamp markup are not
        # executable instructions and are not burned into the caption text.
        text = html.unescape(re.sub(r'<[^>]*>', '', text))
        cue = Subtitle(start=seconds(groups[:4]), end=seconds(groups[4:]), text=text)
        if result and cue.start < result[-1].start:
            raise ValueError('Субтитры должны идти по возрастанию времени')
        result.append(cue)
    if not result:
        raise ValueError('VTT-файл не содержит субтитров')
    if len(result) > MAX_SUBTITLES:
        raise ValueError(f'Максимум {MAX_SUBTITLES} субтитров')
    return result


def seconds(parts):
    h, m, s, ms = map(int, parts)
    if m >= 60 or s >= 60:
        raise ValueError('Некорректный таймкод SRT')
    value=h*3600 + m*60 + s + ms/1000
    if value>MAX_PROJECT_SECONDS:raise ValueError(DURATION_LIMIT_MESSAGE)
    return value


def parse_srt(source: str) -> list[Subtitle]:
    source = source.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not source:
        raise ValueError('SRT-файл пуст')
    result = []
    for block in re.split(r'\n[ \t]*\n', source):
        lines = block.split('\n')
        if lines[0].strip().isdigit():
            lines = lines[1:]
        match = TIMING.match(lines[0].strip()) if lines else None
        if not match or len(lines) < 2:
            raise ValueError('Не удалось прочитать SRT: нужен таймкод и текст в каждом блоке')
        text = re.sub(r'</?(?:b|i|u|font)(?:\s[^>]*)?>', '', '\n'.join(lines[1:]).strip(), flags=re.I)
        cue = Subtitle(start=seconds(match.groups()[:4]), end=seconds(match.groups()[4:]), text=text)
        if result and cue.start < result[-1].start:
            raise ValueError('Субтитры должны идти по возрастанию времени')
        result.append(cue)
    if len(result) > MAX_SUBTITLES:
        raise ValueError(f'Максимум {MAX_SUBTITLES} субтитров')
    return result


def plan_scenes(cues: list[Subtitle], minimum_duration: float = 0) -> list[Scene]:
    """Cut near cue boundaries, retaining original subtitle timings and gaps."""
    end = max(max(c.end for c in cues), minimum_duration, 1)
    if end > MAX_PROJECT_SECONDS:
        raise ValueError(DURATION_LIMIT_MESSAGE)
    end_frame = math.ceil(end*30)
    boundaries = paced_boundaries(0, end_frame, cues, MAX_SCENES, first=True)
    scenes = [Scene(duration=(b-a)/30) for a, b in zip(boundaries, boundaries[1:])]
    return scenes



def paced_boundaries(start, end, cues, slots, *, first=False):
    """Frame-aligned cuts near speech boundaries, with a short opening shot."""
    points = [start]
    while end - points[-1] >= 60 and len(points) < slots:
        remaining_slots = slots - len(points) + 1
        target = 75 if first and len(points) == 1 else max(120, math.ceil((end-points[-1])/remaining_slots))
        if end-points[-1] <= target+30:
            break
        desired = points[-1]+target
        candidates = [round(c.start*30) for c in cues
                      if max(points[-1]+30, desired-30) <= round(c.start*30) <= min(end-30, desired+30)]
        point = min(candidates, key=lambda f: abs(f-desired)) if candidates else desired
        points.append(point)
    points.append(end)
    return points


def apply_reference_rhythm(project):
    """Split long shots without moving audio, cues or existing scene boundaries.

    Keep the original material at each scene's start. New shots are left for the
    semantic media picker, rather than repeating the same photograph.
    """
    result = []
    position = 0
    for index, original in enumerate(project.scenes):
        frames = round(original.duration*30)
        slots = MAX_SCENES-len(result)-(len(project.scenes)-index-1)
        points = paced_boundaries(position, position+frames, project.subtitles, slots, first=index==0)
        title_frame = position+round(original.title_delay*30)
        for part, (a,b) in enumerate(zip(points,points[1:])):
            scene = original.model_copy(deep=True)
            scene.duration = (b-a)/30
            if a <= title_frame < b:
                scene.title_delay = (title_frame-a)/30
            else:
                scene.title = scene.caption = ''
                scene.title_delay = 0
                scene.title_decoration = 'none'
            if part:
                scene.asset_id = None
                scene.offset = 0
                scene.media_reason = ''
            scene.transition_out = ('dissolve','wipe','blur','dissolve')[len(result)%4]
            scene.motion = False
            scene.title_sound = 'none'
            result.append(scene)
        position += frames
    return project.model_copy(update={'scenes': result, 'transition':'mixed', 'photo_motion':'static',
                                      'sound_mode':'auto','sounds_per_minute':4,'sfx_volume':.28})

def ass_time(t: float) -> str:
    total = round(t*100)
    return f'{total//360000}:{total//6000%60:02}:{total//100%60:02}.{total%100:02}'


def ass_document(cues: list[Subtitle], width: int, height: int) -> str:
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{round(height*.042)},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,{round(width*.08)},{round(width*.08)},{round(height*.035)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for cue in cues:
        # Treat uploaded subtitles as literal text, never ASS commands.
        text = cue.text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', r'\N')
        header += f'Dialogue: 0,{ass_time(cue.start)},{ass_time(cue.end)},Default,,0,0,0,,{text}\n'
    return header
