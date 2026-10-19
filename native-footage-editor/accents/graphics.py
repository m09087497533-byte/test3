"""Original animated graphics modelled on the supplied 1018 screen recording.

The source picture stays fixed; glyphs, strokes and highlights have their own clocks.
"""
import math
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .drawing import FONTS


@lru_cache(maxsize=128)
def face(size, serif=True):
    return ImageFont.truetype(str(FONTS/('DejaVuSerifCondensed-Italic.ttf' if serif else 'DejaVuSans-BoldOblique.ttf')),size)


def reference_frame(event,size,elapsed):
    w,h=size;scale=w/1280
    layer=Image.new('RGBA',size)
    if event.treatment in ('red_ink','editorial'):
        from .drawing import font,text_block
        from .animation import title_frame
        from types import SimpleNamespace
        layer.paste((32,7,16,145) if event.treatment=='red_ink' else (3,7,14,130),(0,0,w,h))
        glyphs=Image.new('RGBA',size);draw=ImageDraw.Draw(glyphs)
        red=event.treatment=='red_ink'
        box=(w*.09,h*.28,w*.82,h*.40)
        text_block(draw,event.text.upper(),box,round((190 if red else 68)*scale),
                   '#fa3f58' if red else '#fffbea',serif=red,heavy=red,condensed=not red,center=True)
        glyphs=title_frame(glyphs,SimpleNamespace(title_duration=event.duration,title_decoration='none'),
                           'reveal' if red else 'perspective',elapsed)
        layer.alpha_composite(glyphs)
        if red and event.decoration=='ring':
            q=min(1,max(0,elapsed/1.3));points=[]
            for i in range(max(2,round(200*q))):
                a=-.4+2*math.pi*1.04*i/199
                points.append((w*.5+w*.32*math.cos(a),h*.49+h*.15*math.sin(a)-h*.04*math.cos(a)))
            if q:ImageDraw.Draw(layer).line(points,fill='#f5dcc0',width=max(1,round(2*scale)),joint='curve')
        return layer
    if event.treatment=='italic':
        # The date phrase sweeps in from the left, then holds still over a dimmed source.
        text=event.text;f=face(round(66*scale),False)
        while f.getlength(text)>w*.90:f=face(f.size-1,False)
        p=min(1,max(0,elapsed/event.duration));ease=1-(1-p)**3
        layer.paste((3,5,8,125),(0,0,w,h))
        letters=Image.new('RGBA',size);d=ImageDraw.Draw(letters)
        x=(w-f.getlength(text))/2-w*(1-ease);y=h*.48
        d.text((x+2*scale,y+3*scale),text,font=f,anchor='lm',fill=(0,0,0,180))
        d.text((x,y),text,font=f,anchor='lm',fill='#fffdf3')
        if event.effect=='perspective':
            from types import SimpleNamespace
            from .animation import title_frame
            # Render letters at their settled position before applying the plane entrance.
            letters=Image.new('RGBA',size);draw=ImageDraw.Draw(letters)
            draw.text(((w-f.getlength(text))/2,y),text,font=f,anchor='lm',fill='#fffdf3')
            letters=title_frame(letters,SimpleNamespace(title_duration=event.duration,title_decoration='none'),'perspective',elapsed)
        layer.alpha_composite(letters)
        return layer
    neon=event.treatment=='neon';f=face(round((190 if neon else 205)*scale))
    text=event.text
    if neon:f=ImageFont.truetype(str(FONTS/'DejaVuSans-ExtraLight.ttf'),round(180*scale))
    while f.getlength(text)>w*.70:f=face(f.size-1)
    width=f.getlength(text);x=(w-width)/2;y=h*.51
    if neon:
        # Cool purple veil and a pink central wash, without moving the source image.
        layer.paste((18,11,40,160),(0,0,w,h))
        wash=Image.new('RGBA',size);ImageDraw.Draw(wash).ellipse((w*.15,h*.25,w*.85,h*.85),fill=(113,20,83,100))
        layer.alpha_composite(wash.filter(ImageFilter.GaussianBlur(w*.09)))
    glyphs=Image.new('RGBA',size)
    # Stagger each digit; no random flicker and no motion after the entrance.
    for i,char in enumerate(text):
        p=min(1,max(0,(elapsed-.12-i*.085)/.33));dy=(1-p)**3*h*.035
        if p<=0:continue
        tile=Image.new('RGBA',size);d=ImageDraw.Draw(tile);alpha=round(255*p)
        if not neon:
            d.text((x-2.2*scale,y+dy),char,font=f,anchor='lm',fill=(220,80,93,alpha))
            d.text((x+2.2*scale,y+dy),char,font=f,anchor='lm',fill=(58,188,214,alpha))
        d.text((x,y+dy),char,font=f,anchor='lm',fill=(255,228 if neon else 255,253 if neon else 246,alpha),stroke_width=0)
        glyphs.alpha_composite(tile);x+=f.getlength(char)
    if neon:
        glow=glyphs.filter(ImageFilter.GaussianBlur(w*.008))
        layer.alpha_composite(glow)
    elif event.effect=='stamp':
        from types import SimpleNamespace
        from .animation import title_frame
        glyphs=title_frame(glyphs,SimpleNamespace(title_duration=event.duration,title_decoration='none'),'stamp',elapsed)
    layer.alpha_composite(glyphs)
    # Draw the tilted ellipse independently of the glyph reveal.
    if event.decoration=='ring':
        q=min(1,max(0,elapsed/1.30));points=[]
        rx=max(width*.67,w*.19);ry=h*.105;cx=w*.5;cy=h*.51
        for i in range(max(2,round(240*q))):
            a=math.pi*.4+2*math.pi*1.035*i/239
            px=rx*math.cos(a);py=ry*math.sin(a)
            points.append((cx+px,cy+py-px*.13))
        stroke=Image.new('RGBA',size);d=ImageDraw.Draw(stroke)
        if q>0:
            d.line(points,fill=(137,239,255,230) if neon else (39,43,40,185),width=max(1,round(1.6*scale)),joint='curve')
            if neon:
                layer.alpha_composite(stroke.filter(ImageFilter.GaussianBlur(4*scale)))
                if q<1:
                    px,py=points[-1];d.ellipse((px-4*scale,py-4*scale,px+4*scale,py+4*scale),fill='#efffff')
                    flare=Image.new('RGBA',size);fd=ImageDraw.Draw(flare)
                    fd.ellipse((px-15*scale,py-15*scale,px+15*scale,py+15*scale),fill=(100,240,255,160))
                    layer.alpha_composite(flare.filter(ImageFilter.GaussianBlur(8*scale)))
            layer.alpha_composite(stroke)
    return layer


def grid_background(size):
    w,h=size;layer=Image.new('RGBA',size,'#080b09');d=ImageDraw.Draw(layer)
    # Slight perspective in the reference; the grid itself never slides.
    step=w*.105
    for i in range(-7,8):
        d.line((w/2+i*step*.87,0,w/2+i*step*1.13,h),fill='#28302a',width=max(1,round(w/1100)))
    for i in range(9):
        y=h*(i/8)**1.18;d.line((0,y,w,y),fill='#28302a',width=max(1,round(w/1100)))
    return layer
