"""Original local sounds, strictly confined to text entrance windows."""
import math
import random
import struct
from array import array
from pathlib import Path


def sound_events(events,duration):
    plan=[];last=-10
    budget=math.ceil(duration/60*4)
    for event in events:
        begin=event['start']
        if begin-last<1.5 or len(plan)>=budget:continue
        style=('paper' if event['tier']=='caption' else 'sweep' if event['layout']=='grid' else
               {'red_ink':'stamp','neon':'chime','chromatic':'spark'}.get(event['treatment'],'swish'))
        span=max(event['duration'],1.35 if event['decoration']!='none' else 0)
        end=min(event['end'],begin+span)
        if end<=begin:continue
        plan.append(dict(start=begin,end=end,time=begin+min(.22,(end-begin)*.5),style=style,kind='title'))
        last=begin
    return plan


def synthesize_sfx(events, duration, output, gain=.28):
    if gain<=0:return False
    rate=48000
    plan=sound_events(events,duration)
    if not plan:return False
    count=math.ceil(duration*rate)
    # Sparse PCM file: silence occupies no full-duration Python arrays.
    # At one hour the logical WAV is 691 MB, but only accent windows are written.
    output=Path(output);data_bytes=count*4
    with output.open('wb') as file:
        file.write(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+data_bytes,b'WAVE',b'fmt ',16,1,2,rate,rate*4,4,16,b'data',data_bytes))
        file.truncate(44+data_bytes)
    for index,event in enumerate(plan):
        rng=random.Random(2100+index);style=event['style']
        click=style in ('click','paper');impact=style in ('impact','flash','stamp')
        # Automatic event time is the motion peak. Manual clicks retain their exact onset.
        lead=0 if click else .09 if impact else .22
        start=event['time']-lead
        length=.18 if click else .72 if impact else .62
        low=mid=other=0.0
        samples=array('f',[0.0])*(round(length*rate)*2)
        begin=event.get('start',0);end=event.get('end',count/rate)
        for j in range(round(length*rate)):
            t=j/rate;u=t/length;noise=rng.uniform(-1,1)
            low+=.025*(noise-low)
            cutoff=.08+.30*math.exp(-((t-lead)/.12)**2)
            mid+=cutoff*(noise-mid)
            other+=.19*(rng.uniform(-1,1)-other)
            if click:
                # Dry paper contact, followed by two much softer fibres settling.
                env=math.exp(-t*85)*min(1,t/.0015)
                flutter=sum(math.exp(-((t-c)/.007)**2)*v for c,v in ((.034,.18),(.071,.07)))
                value=(noise-low)*(.25*env+.12*flutter)
                if style=='paper':
                    value=(mid-low)*.75*(math.exp(-t*24)+.35*math.exp(-((t-.06)/.022)**2))
                width=.10
            elif impact:
                after=max(0,t-lead);gate=min(1,max(0,(t-lead)/.006))
                body=math.sin(2*math.pi*(72*after+1.5*(1-math.exp(-after*28))))
                value=.28*body*math.exp(-after*14)*gate
                value+=(mid-low)*.65*math.exp(-((t-lead)/.065)**2)
                if style=='stamp':
                    value=.13*math.sin(2*math.pi*135*after)*math.exp(-after*30)*gate+(noise-low)*.14*math.exp(-((t-lead)/.018)**2)
                width=.12
            else:
                # Air sweep with paper flutter, stereo width and a restrained landing.
                attack=math.exp(-((t-lead)/(.10 if t<lead else .17))**2)
                flutter=.78+.22*math.sin(2*math.pi*(21*t+12*t*t))
                value=(mid-low)*1.05*attack*flutter
                value+=low*.18*math.exp(-((t-lead-.035)/.09)**2)
                if style in ('chime','spark'):
                    after=max(0,t-lead)
                    gate=min(1,max(0,(t-lead)/.012))
                    frequencies=(880,1320,1760) if style=='chime' else (1650,2475,3300)
                    tone=sum(math.sin(2*math.pi*f*after)/(i+1) for i,f in enumerate(frequencies))
                    value=tone*.08*math.exp(-after*15)*gate+(mid-low)*.20*attack
                elif style=='sweep':
                    value=(mid-low)*1.5*attack*(.65+.35*math.sin(2*math.pi*35*t))
                width=.22*attack
            # Equal-power pan follows the lateral text/card motion; no hard channel jumps.
            pan=.45*math.tanh((t-lead)*7) if not click else 0
            left=math.sqrt((1-pan)/2);right=math.sqrt((1+pan)/2)
            side=other*width*(math.sin(math.pi*u)**2)
            edge=min(1,t/.003,(length-t)/.015)
            pos=round(start*rate)+j
            # No pre-roll or tail may extend into time without a text entrance.
            if 0<=pos<count and begin<=pos/rate<end:
                window_edge=min(1,(pos/rate-begin)/.003,(end-pos/rate)/.012)
                volume=gain*edge*max(0,window_edge)
                samples[2*j]+=(value*left+side)*volume
                samples[2*j+1]+=(value*right-side)*volume
        first=max(0,round(start*rate));last=min(count,round(start*rate)+len(samples)//2)
        if last<=first:continue
        source_offset=2*(first-round(start*rate))
        import sys
        with output.open('r+b') as file:
            file.seek(44+first*4)
            pcm=array('h');pcm.frombytes(file.read((last-first)*4))
            if sys.byteorder!='little':pcm.byteswap()
            for k,old in enumerate(pcm):
                pcm[k]=round(max(-.95,min(.95,old/32767+samples[source_offset+k]))*32767)
            if sys.byteorder!='little':pcm.byteswap()
            file.seek(44+first*4);file.write(pcm.tobytes())
    return True
