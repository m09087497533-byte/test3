"""Sparse accents from the supplied words, independent of the effects rotation."""
import math
from types import SimpleNamespace
from .phrases import emphasis_events

STYLE_LABELS={'red_ink':'Красные цифры с обводкой','neon':'Светящиеся цифры',
              'chromatic':'Дата с цветной тенью','editorial':'Текст с перспективой',
              'italic':'Курсивная фраза','marquee':'Бегущая строка на сетке',
              'caption':'Короткая подпись','classic':'Появление текста'}


def plan_accents(segments,duration=None,major_rate=2,fps=25):
    if not segments:return []
    if duration is None:duration=max(float(s['end']) for s in segments)
    cues=[SimpleNamespace(text=s['text'],start=float(s['start']),end=float(s['end'])) for s in segments]
    events=[];number=0
    for original in emphasis_events(cues,major_rate=max(0,min(4,int(major_rate)))):
        event={k:v for k,v in original.items() if k!='priority'}
        start=max(0,round(event['start']*fps));end=min(math.ceil(duration*fps),round(event['end']*fps))
        if end<=start:continue
        event.update(start=start/fps,end=end/fps,span=(end-start)/fps,elapsed=0)
        event.setdefault('tier','major');event.setdefault('treatment','classic')
        if event['tier']=='caption':
            event.update(layout='label',treatment='classic',effect='typewriter',duration=.45)
        elif event['layout']=='grid':event['effect']='marquee'
        elif event['layout']=='number':
            event['treatment']=('red_ink','neon','chromatic')[number%3];number+=1
        elif event['effect']=='perspective':event['treatment']='editorial'
        event['style_label']=STYLE_LABELS['caption' if event['tier']=='caption' else 'marquee' if event['effect']=='marquee' else event['treatment']]
        events.append(event)
    return events
