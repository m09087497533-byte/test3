"""Independent timed text accents; entrances do not move the background grid."""
import math
from PIL import Image,ImageDraw
from .media import run
from .title_animation import title_frame



def counter_layer(size,text,progress):
    import re
    from .render import font,text_block
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

def event_frame(scene,event,size,elapsed,remaining):
    from .render import overlay
    view=scene.model_copy(update={'title':event.text,'caption':'','title_layout':event.layout,
                                  'title_duration':event.duration,'title_decoration':event.decoration,
                                  'template':'cinema','text_events':[]})
    if event.treatment!='classic':
        from .reference_graphics import reference_frame
        layer=reference_frame(event,size,elapsed)
    elif event.layout=='counter':
        layer=counter_layer(size,event.text,elapsed/event.duration)
    else:
        glyphs=overlay(view,size,text_only=True,glyph_only=True)
        layer=title_frame(glyphs,view,event.effect,elapsed)
    if event.layout=='grid':
        from .reference_graphics import grid_background
        grid=grid_background(size)
        grid.alpha_composite(layer);layer=grid
    opacity=min(1,max(0,elapsed/.12),max(0,remaining/.18))
    if opacity<1:layer.putalpha(layer.getchannel('A').point(lambda v:round(v*opacity)))
    return layer


def encode_events(scene,size,frames,output):
    """VFR entrance/hold/exit track: encode still holds once, not every frame."""
    blank=Image.new('RGBA',size);blank_name=output.stem+'-blank.png';blank.save(output.parent/blank_name)
    entries=['ffconcat version 1.0'];cursor=0;counter=0
    def append(name,count):
        if count:entries.extend([f"file '{name}'",'option framerate 30',f'duration {count/30:.12f}'])
    def save(frame,count=1):
        nonlocal counter
        name=f'{output.stem}-{counter:04d}.png';counter+=1
        frame.save(output.parent/name,compress_level=2);append(name,count)
    for event in sorted(scene.text_events,key=lambda e:e.start):
        start=max(cursor,round(event.start*30));end=min(frames,round(event.end*30))
        if end<=start:continue
        append(blank_name,start-cursor)
        animation_end=event.duration if event.decoration=='none' else max(event.duration,1.35 if event.treatment!='classic' else event.duration*.65+.65)
        i=start
        while i<end:
            elapsed=event.elapsed+(i-start)/30;remaining=(end-i)/30
            # Only the settled middle can be held as one VFR image.
            hold_until=end-6
            count=hold_until-i if elapsed>=animation_end and i<hold_until else 1
            save(event_frame(scene,event,size,elapsed,remaining),count);i+=count
        cursor=end
    append(blank_name,frames-cursor)
    entries.extend([f"file '{blank_name}'",'option framerate 30'])
    timeline=output.with_suffix('.txt');timeline.write_text('\n'.join(entries)+'\n')
    run(['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-f','concat','-safe','0','-i',str(timeline),
         '-an','-fps_mode','vfr','-c:v','qtrle','-threads','2',str(output)],timeout=600)
