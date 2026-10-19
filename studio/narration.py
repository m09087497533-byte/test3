"""Word boundaries and approximate word positions derived from supplied cues."""
import re


def has_stem(stems,text):
    # Russian morphology can extend a stem; an inner substring is not a word.
    return any(re.search(r'(?<![a-zа-яё])'+re.escape(stem),text.lower()) for stem in stems)


def timed_words(cues):
    result=[]
    for cue in cues:
        words=list(re.finditer(r'[\wёЁ]+(?:[-’\'][\wёЁ]+)*',cue.text))
        weights=[max(2,len(m.group())) for m in words];total=sum(weights) or 1;cursor=0
        for match,weight in zip(words,weights):
            result.append({'text':match.group(),'start':cue.start+(cue.end-cue.start)*cursor/total,
                           'end':cue.start+(cue.end-cue.start)*(cursor+weight)/total})
            cursor+=weight
    return result


def emphasis_events(cues):
    words=timed_words(cues);text='';positions=[]
    for word in words:
        if text:text+=' '
        a=len(text);text+=word['text'];positions.append((a,len(text),word))
    def position(char):return next((w['start'] for a,b,w in positions if a<=char<b),0)
    candidates=[]
    patterns=[(r'\b(?:семь|7|seven)\s+(?:признак\w*|signs?)', '7 ПРИЗНАКОВ','grid','scroll','none',2.8,4),
              (r'\bне\s+(?:\w+\s+){0,3}наказан\w*','НЕ НАКАЗАНИЕ','label','fade','none',3.0,5),
              (r'\bстих\s+за\s+стих\w*','СТИХ ЗА СТИХОМ','center','perspective','none',1.7,3),
              (r'\bвы\s+увидите\s+воздаяни\w*','ВЫ УВИДИТЕ ВОЗДАЯНИЕ','center','perspective','none',3.0,3),
              (r'\b(?:1[5-9]\d{2}|20\d{2})\b',None,'number','reveal','ring',2.8,4)]
    for pattern,title,layout,effect,decoration,hold,priority in patterns:
        for match in re.finditer(pattern,text,re.I):
            start=round(position(match.start())*30)/30
            candidates.append(dict(text=title or match.group(),start=start,end=start+hold,layout=layout,
                                   effect=effect,decoration=decoration,duration=.6 if effect=='perspective' else .45,priority=priority))
    # A count is only animated when the narration actually supplies the count
    # and its unit. Never manufacture the competitor's sample statistics.
    for match in re.finditer(r'\b(?:\d{1,3}(?: \d{3})+|\d{3,6})\s+(?:человек|людей|участник\w*|people|participants)',text,re.I):
        start=round(position(match.start())*30)/30
        candidates.append(dict(text=match.group(),start=start,end=start+3,layout='counter',effect='reveal',decoration='none',duration=.8,priority=6))
    for match in re.finditer(r'\b(?:датируется|датирован\w*|относится к)\s+(?:\d{1,2}|[IVX]{1,6})\s+век\w*',text,re.I):
        start=round(position(match.start())*30)/30
        candidates.append(dict(text=match.group(),start=start,end=start+4.5,layout='center',effect='scroll',decoration='none',duration=.65,priority=5,treatment='italic'))
    dates=sorted((e for e in candidates if e['layout']=='number'),key=lambda e:e['start'])
    for i,event in enumerate(dates):
        event.update(treatment='neon' if i%2==0 else 'chromatic',duration=.85,end=event['start']+3.5)
    for event in candidates:
        if event['text'] in ('НЕ НАКАЗАНИЕ','СТИХ ЗА СТИХОМ','ВЫ УВИДИТЕ ВОЗДАЯНИЕ'):
            event.update(treatment='italic',layout='center',duration=.65)
            if event['effect']=='fade':event['effect']='scroll'
    chosen=[]
    seen_text=set()
    minute_count={}
    for event in sorted(candidates,key=lambda e:(-e['priority'],e['start'])):
        if any(abs(event['start']-old['start'])<.6 for old in chosen):continue
        if event['text'] in seen_text:continue
        minute=int(event['start']//60)
        if minute_count.get(minute,0)>=2:continue
        chosen.append(event)
        seen_text.add(event['text'])
        minute_count[minute]=minute_count.get(minute,0)+1
    chosen.sort(key=lambda e:e['start'])
    for event,following in zip(chosen,chosen[1:]):
        event['end']=min(event['end'],following['start']-.12)
    return chosen
