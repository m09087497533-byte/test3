"""One absolute text track: accents keep their phase across every scene cut."""
import math
import subprocess
from types import SimpleNamespace
from PIL import Image,ImageDraw,ImageFilter
from .drawing import font,text_block
from .animation import title_frame
from .graphics import reference_frame,grid_background
from .marquee import marquee_frame


def counter_layer(size,text,progress):
    import re
    w,h=size;layer=Image.new('RGBA',size);draw=ImageDraw.Draw(layer)
    match=re.match(r'([\d ]+)\s+(.+)',text)
    if not match:return layer
    target=int(match.group(1).replace(' ',''));p=max(0,min(1,progress));ease=1-(1-p)**3
    cx,cy=w*.5,h*.46;radius=h*.16;box=(cx-radius,cy-radius,cx+radius,cy+radius)
    draw.ellipse(box,fill=(30,34,36,190),outline='#f7f5e9',width=max(2,round(w/400)))
    draw.arc(box,-90,-90+360*ease,fill='#3d8ef3',width=max(5,round(w*.012)))
    text_block(draw,str(round(target*ease)),(cx-radius*.83,cy-radius*.5,radius*1.66,radius),round(h*.08),'white',center=True,condensed=True)
    label=(cx-w*.085,cy+radius+h*.025,cx+w*.085,cy+radius+h*.08)
    draw.rectangle(label,fill='#f0eadb')
    text_block(draw,match.group(2).upper(),(label[0],label[1],label[2]-label[0],label[3]-label[1]),round(h*.026),'#282825',center=True,condensed=True)
    return layer


def event_frame(event,size,elapsed,remaining):
    event=SimpleNamespace(**event) if isinstance(event,dict) else event
    w,h=size
    if event.effect=='marquee':
        layer=marquee_frame(event.text,size,elapsed,event.span)
    elif event.treatment not in ('classic','glow','color_shadow'):layer=reference_frame(event,size,elapsed)
    elif event.layout=='counter':layer=counter_layer(size,event.text,elapsed/event.duration)
    else:
        glyphs=Image.new('RGBA',size);draw=ImageDraw.Draw(glyphs)
        if event.layout=='card':
            draw.rounded_rectangle((w*.10,h*.29,w*.90,h*.69),radius=max(4,round(w*.012)),fill='#eee8d5')
            text_block(draw,event.text.upper(),(w*.14,h*.33,w*.72,h*.32),round(55*w/1280),'#24221e',center=True,condensed=True)
        elif event.layout=='label':
            face=font(max(12,round(30*w/1280)),True)
            width=min(w*.88,face.getlength(event.text.upper())+w*.055)
            box=((w-width)/2,h*.82,(w+width)/2,h*.905)
            draw.rectangle(box,fill='#eee8d5')
            text_block(draw,event.text.upper(),(box[0]+w*.018,box[1],width-w*.036,box[3]-box[1]),round(30*w/1280),'#24221e',serif=True,center=True)
        else:text_block(draw,event.text.upper(),(w*.08,h*.30,w*.84,h*.35),round(68*w/1280),'#fff8e9',center=True,condensed=True)
        if event.treatment=='glow':
            glow=Image.new('RGBA',size,'#ba58ed');glow.putalpha(glyphs.getchannel('A'))
            glow=glow.filter(ImageFilter.GaussianBlur(w*.009))
            glow.alpha_composite(glyphs);glyphs=glow
        elif event.treatment=='color_shadow':
            from PIL import ImageChops
            shadow=Image.new('RGBA',size)
            for color,shift in [('#eb4b6b',-max(1,round(w*.003))),('#39d7ee',max(1,round(w*.003)))]:
                tint=Image.new('RGBA',size,color);tint.putalpha(glyphs.getchannel('A'))
                shadow.alpha_composite(ImageChops.offset(tint,shift,0))
            shadow.alpha_composite(glyphs);glyphs=shadow
        layer=title_frame(glyphs,SimpleNamespace(title=event.text,title_duration=event.duration,title_decoration=event.decoration),event.effect,elapsed)
    if event.layout=='grid':
        grid=grid_background(size);grid.alpha_composite(layer);layer=grid
    opacity=min(1,max(0,elapsed/.12),max(0,remaining/.18))
    if opacity<1:layer.putalpha(layer.getchannel('A').point(lambda v:round(v*opacity)))
    return layer


def encode_track(events,size,frames,output,fps=25):
    blank=Image.new('RGBA',size);blank_name='accent-blank.png';blank.save(output.parent/blank_name)
    entries=['ffconcat version 1.0'];cursor=0;counter=0
    def append(name,count):
        if count:entries.extend([f"file '{name}'",f'option framerate {fps}',f'duration {count/fps:.12f}'])
    def save(frame,count=1):
        nonlocal counter
        name=f'accent-{counter:05d}.png';counter+=1;frame.save(output.parent/name,compress_level=2);append(name,count)
    for event in events:
        start=max(cursor,round(event['start']*fps));end=min(frames,round(event['end']*fps))
        if end<=start:continue
        append(blank_name,start-cursor)
        # The generic stroke starts after the entrance; do not freeze it mid-draw.
        animation_end=event['duration'] if event['decoration']=='none' else max(1.35,event['duration'],round(event['duration']*fps)/fps*.65+.65)
        i=start
        while i<end:
            elapsed=(i-start)/fps;remaining=(end-i)/fps
            hold_until=end-math.ceil(.2*fps)
            count=hold_until-i if event['effect']!='marquee' and elapsed>=animation_end and i<hold_until else 1
            save(event_frame(event,size,elapsed,remaining),count);i+=count
        cursor=end
    append(blank_name,frames-cursor)
    entries.extend([f"file '{blank_name}'",f'option framerate {fps}'])
    timeline=output.with_suffix('.txt');timeline.write_text('\n'.join(entries)+'\n',encoding='utf-8')
    subprocess.run(['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-f','concat','-safe','0','-i',str(timeline),
                    '-an','-fps_mode','vfr','-c:v','qtrle','-threads','2',str(output)],check=True,timeout=max(600,frames/fps*2))
