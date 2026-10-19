import math
import io
import shutil
import subprocess
from pathlib import Path
from functools import lru_cache

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .media import asset_info, asset_path, run
from .models import Project, Scene, frame_count, total_duration
from .subtitles import ass_document
from .effects import assemble, transition_frames, synthesize_sfx
from .title_animation import ADVANCED, encode_title
from .credits import source_credits
from .render_diagnostics import capabilities
from .caption_track import burn_captions
from .text_track import encode_events
from .video_clock import normalize_clock,require_frames

FONTS = Path(__file__).resolve().parent / 'fonts'


@lru_cache(maxsize=256)
def font(size: int, serif=False,condensed=False,heavy=False):
    name='DejaVuSerif-Bold.ttf' if serif and heavy else 'DejaVuSerif.ttf' if serif else 'DejaVuSansCondensed-Bold.ttf' if condensed else 'DejaVuSans-Bold.ttf'
    return ImageFont.truetype(str(FONTS/name),size)


def wrapped(text: str, face, width: int) -> str:
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if face.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ''
            for char in word:
                if face.getlength(line + char) > width and line:
                    lines.append(line)
                    line = ''
                line += char
        lines.append(line)
    return '\n'.join(lines)


def text_block(draw, text, box, size, fill, serif=False, center=False,condensed=False,heavy=False):
    if not text.strip():
        return
    x, y, width, height = box
    for current in range(max(1, size), 0, -1):
        face = font(current, serif,condensed,heavy)
        value = wrapped(text, face, width)
        spacing = max(4, current // 5)
        bounds = draw.multiline_textbbox((0, 0), value, font=face, spacing=spacing)
        if bounds[3] - bounds[1] <= height:
            break
    if center:
        x += (width - (bounds[2] - bounds[0])) / 2
        y += (height - (bounds[3] - bounds[1])) / 2
    draw.multiline_text((x, y - bounds[1]), value, font=face, fill=fill,
                        spacing=spacing, align='center' if center else 'left')


def backdrop(width: int, height: int) -> Image.Image:
    """Built-in abstract landscape, independent of the uploaded reference."""
    image = Image.new('RGB', (width, height), '#c7bea5')
    draw = ImageDraw.Draw(image)
    for y in range(height):
        v = y / height
        draw.line((0, y, width, y), fill=(int(209 - v * 40), int(199 - v * 38), int(174 - v * 28)))
    draw.ellipse((width*.65, height*.12, width*.78, height*.12+width*.13), fill='#e7d9b8')
    for layer, color in enumerate(['#a6a18b', '#7a8276', '#4b6057', '#293f39']):
        points = [(0, height)]
        for x in range(0, width + 5, 4):
            y = height * (.48 + layer*.12 + .09*math.sin(x/width*7 + layer*1.8) + .04*math.cos(x/width*19))
            points.append((x, y))
        points += [(width, height)]
        draw.polygon(points, fill=color)
    return image


def overlay(scene: Scene, size: tuple[int, int], *, text_only=False, decor_only=False,glyph_only=False) -> Image.Image:
    w, h = size
    layer = Image.new('RGBA', size)
    d = ImageDraw.Draw(layer)
    scale = w / 1280
    if scene.title_layout == 'counter':
        from .text_track import counter_layer
        return layer if decor_only else counter_layer(size,scene.title,1)
    elif scene.title_layout == 'folio':
        if not text_only:
            d.rectangle((0,0,w,h),fill='#171c1b')
            d.rectangle((w*.13,h*.135,w*.89,h*.895),fill='#090c0c')
            d.rectangle((w*.11,h*.11,w*.87,h*.87),fill='#e9e1c8')
            d.rectangle((w*.13,h*.13,w*.85,h*.85),outline='#c6b994',width=max(1,round(2*scale)))
            d.line((w*.17,h*.45,w*.81,h*.45),fill='#b6a376',width=max(1,round(2*scale)))
            for y in range(round(h*.53),round(h*.79),max(1,round(h*.045))):
                d.line((w*.17,y,w*.81,y),fill='#d9ceb0',width=max(1,round(scale)))
        if not decor_only:
            text_block(d,scene.title,(w*.17,h*.23,w*.64,h*.18),round(54*scale),'#292720',serif=True,center=True)
            if scene.caption:text_block(d,scene.caption,(w*.18,h*.49,w*.62,h*.27),round(30*scale),'#454036',serif=True,center=True)
    elif scene.title_layout == 'grid':
        if not decor_only:
            if not glyph_only:
                d.rectangle((0,0,w,h),fill='#0b0e0d')
                for x in range(0,w,max(1,int(90*scale))):d.line((x,0,x,h),fill='#29332a')
                for y in range(0,h,max(1,int(90*scale))):d.line((0,y,w,y),fill='#29332a')
            text_block(d,scene.title.upper(),(w*.06,h*.33,w*.88,h*.30),int(124*scale),'#d7ec04',center=True,condensed=True)
    elif scene.title_layout == 'label':
        if not decor_only:
            face=font(max(12,round(30*scale)),True)
            text=scene.title.upper();width=min(w*.88,face.getlength(text)+w*.055)
            box=((w-width)/2,h*.82,(w+width)/2,h*.905)
            d.rectangle(box,fill='#eee8d5')
            text_block(d,text,(box[0]+w*.018,box[1],width-w*.036,box[3]-box[1]),round(30*scale),'#24221e',serif=True,center=True)
    elif scene.title_layout in ('center','number') and scene.template != 'thesis':
        if not text_only: d.rectangle((0,0,w,h),fill=(6,10,14,100))
        if not decor_only:
            text_block(d,scene.title.upper(),(w*.08,h*.30,w*.84,h*.35),int((170 if scene.title_layout=='number' else 68)*scale),'#f23649' if scene.title_layout=='number' else '#fff8e9',center=True,serif=scene.title_layout=='number',heavy=scene.title_layout=='number',condensed=scene.title_layout=='center')
    elif scene.template == 'quote':
        if not text_only: d.rectangle((0, 0, w, h), fill='#c2c0ae')
        if not text_only: d.arc((-w*.12, h*.08, w*.23, h*.4), 255, 350, fill='#8b8a7a', width=max(1, int(2*scale)))
        box = (w*.14, h*.17, w*.86, h*.85)
        if not text_only: d.rectangle((box[0]+12*scale, box[1]+12*scale, box[2]+12*scale, box[3]+12*scale), fill='#aaa895')
        if not text_only: d.rectangle(box, fill='#f2eedc')
        if not text_only: d.text((w*.19, h*.23), 'И З   А Р Х И В А', font=font(int(13*scale)), fill='#918b77')
        if not text_only: d.rectangle((w*.19, h*.52, w*.55, h*.55), fill='#d7c368')
        if not decor_only: text_block(d, scene.title, (w*.19, h*.31, w*.62, h*.22), int(56*scale), '#302f27', serif=True)
        if not text_only: d.line((w*.19, h*.61, w*.81, h*.61), fill='#c9c1a3', width=max(1, int(scale)))
        if not decor_only: text_block(d, scene.caption, (w*.19, h*.65, w*.62, h*.14), int(25*scale), '#514c3e', serif=True)
    elif scene.template == 'thesis':
        if not text_only: d.rectangle((0,0,w,h), fill='#0b1110')
        for x in range(0,w,max(1,int(90*scale))):
            if not text_only: d.line((x,0,x,h),fill='#26332b',width=max(1,int(scale)))
        for y in range(0,h,max(1,int(90*scale))):
            if not text_only: d.line((0,y,w,y),fill='#26332b',width=max(1,int(scale)))
        if not decor_only: text_block(d,scene.title.upper(),(w*.08,h*.28,w*.84,h*.40),int(104*scale),'#e1f266',center=True)
        if not decor_only: text_block(d,scene.caption,(w*.1,h*.75,w*.8,h*.1),int(24*scale),'#c0c6aa',center=True)
    elif scene.template == 'statement':
        if not text_only: d.rectangle((0, 0, w, h), fill=(142, 5, 31, 195))
        if not decor_only: text_block(d, scene.title.upper(), (w*.08, h*.24, w*.84, h*.44), int(110*scale), '#fff6e7', center=True)
        if not decor_only: text_block(d, scene.caption, (w*.12, h*.77, w*.76, h*.12), int(24*scale), '#f1c8c3', center=True)
        if not text_only: d.rectangle((w*.46, h*.16, w*.54, h*.165), fill='#eac896')
    elif scene.template == 'archive':
        m = int(w*.08)
        if not text_only: d.rectangle((0, 0, w, m), fill='#101516')
        if not text_only: d.rectangle((0, h-m, w, h), fill='#101516')
        if not text_only: d.rectangle((0, 0, m, h), fill='#101516')
        if not text_only: d.rectangle((w-m, 0, w, h), fill='#101516')
        if not text_only: d.rectangle((m-5, m-5, w-m+5, h-m+5), outline='#c4d86b', width=max(1, int(2*scale)))
        if not text_only: d.rectangle((m, h*.69, w-m, h-m), fill=(9, 14, 14, 170))
        if not decor_only: text_block(d, scene.title, (w*.12, h*.71, w*.76, h*.13), int(36*scale), '#f3f0e3')
        if not decor_only: text_block(d, scene.caption, (w*.12, h*.91, w*.76, h*.055), int(16*scale), '#c4d86b')
    else:
        if not scene.title and not scene.caption:return layer
        # A legible lower gradient; no text is baked into moving source material.
        for y in range(int(h*.35), h):
            alpha = int(190*max(0, (y/h-.35)/.65)**1.5)
            if not text_only: d.line((0, y, w, y), fill=(6, 13, 11, alpha))
        if not decor_only: text_block(d, scene.title, (w*.08, h*.58, w*.84, h*.22), int(55*scale), '#fff8e9')
        if not decor_only: text_block(d, scene.caption, (w*.08, h*.84, w*.84, h*.10), int(23*scale), '#e1dbc5')
    return layer

def tone(image: Image.Image, scene: Scene) -> Image.Image:
    if scene.template == 'archive':
        return ImageOps.colorize(ImageOps.grayscale(image), '#1c2421', '#e2ddc8')
    return ImageEnhance.Color(image).enhance(.78)


def source_still(scene: Scene, size: tuple[int, int]) -> Image.Image:
    if not scene.asset_id or scene.template == 'quote':
        return backdrop(*size)
    meta = asset_info(scene.asset_id)
    if meta['kind'] == 'video':
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-ss', str(scene.offset), '-i', str(asset_path(scene.asset_id)), '-frames:v', '1',
            '-vf', f'scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,crop={size[0]}:{size[1]},setsar=1',
            '-threads', '2', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'],
            capture_output=True, check=True, timeout=30)
        with Image.open(io.BytesIO(result.stdout)) as original:
            return original.convert('RGB')
    path = asset_path(scene.asset_id)
    with Image.open(path) as original:
        return ImageOps.fit(original.convert('RGB'), size, method=Image.Resampling.LANCZOS)



def framed_card(scene,size):
    if scene.asset_id and asset_info(scene.asset_id)['kind']=='image':
        with Image.open(asset_path(scene.asset_id)) as source:
            picture=ImageOps.exif_transpose(source).convert('RGB')
    else:picture=source_still(scene,size)
    if scene.picture_effect=='split':
        w,h=size;panel=Image.new('RGBA',size);draw=ImageDraw.Draw(panel)
        draw.rectangle((0,0,w*.37,h),fill=(20,26,22,245))
        draw.line((w*.075,h*.20,w*.14,h*.20),fill='#d9c89c',width=max(2,round(w/400)))
        text_block(draw,scene.document_title,(w*.07,h*.27,w*.25,h*.23),round(w*.036),'#eee8d7',serif=True)
        text_block(draw,scene.document_caption,(w*.07,h*.56,w*.25,h*.23),round(w*.021),'#c5c7b9',serif=True)
        document=ImageOps.contain(tone(picture,scene),(round(w*.58),round(h*.90)),Image.Resampling.LANCZOS).convert('RGBA')
        panel.alpha_composite(document,(round(w*.68-document.width/2),round((h-document.height)/2)))
        return panel
    card=ImageOps.contain(tone(picture,scene),(round(size[0]*.74),round(size[1]*.76)),Image.Resampling.LANCZOS).convert('RGBA')
    return ImageOps.expand(card,border=max(2,round(size[0]/600)),fill='#e5d9b8')


def portrait_photo(scene):
    if not scene.asset_id or asset_info(scene.asset_id)['kind']!='image':return False
    with Image.open(asset_path(scene.asset_id)) as picture:
        return picture.height>picture.width*1.05


def portrait_card_motion(card,frames,index):
    """Continuous camera motion on vertical documents, varied by scene."""
    progress=f'(0.5-0.5*cos(PI*on/{max(1,frames-1)}))'
    style=index%3
    zoom=f'1+0.10*{progress}' if style==0 else f'1.10-0.10*{progress}' if style==1 else '1.09'
    x=f'iw/2-iw/zoom/2' if style!=2 else f'(iw-iw/zoom)*{progress}'
    return (f"zoompan=z='{zoom}':x='{x}':y='ih/2-ih/zoom/2':"
            f'd=1:s={card.width}x{card.height}:fps=30,format=rgba')

def preview(scene: Scene, size=(960, 540)) -> Image.Image:
    base = tone(source_still(scene, size), scene).convert('RGBA')
    if scene.picture_effect in ('inset','split'):
        from PIL import ImageFilter
        background=ImageEnhance.Brightness(base.filter(ImageFilter.GaussianBlur(size[0]/45))).enhance(.4)
        card=framed_card(scene,size)
        background.alpha_composite(card,((size[0]-card.width)//2,(size[1]-card.height)//2))
        base=background
    view=scene.model_copy(update={'title':'','caption':'','title_layout':'caption'}) if scene.text_events else scene
    result=Image.alpha_composite(base, overlay(view, size))
    if scene.text_events:
        from .text_track import event_frame
        event=scene.text_events[0]
        elapsed=min(event.end-event.start-.05,max(event.duration,1.36))
        result.alpha_composite(event_frame(scene,event,size,max(.1,elapsed),.5))
    return result.convert('RGB')


def validate_assets(project: Project):
    for scene in project.scenes:
        if scene.asset_id:
            meta = asset_info(scene.asset_id)
            if meta['kind'] not in ('image', 'video'):
                raise ValueError('Для сцены нужно изображение или видео')
            if meta['kind'] == 'video' and scene.offset >= meta['duration']:
                raise ValueError('Начало фрагмента выходит за пределы видео')
    for identifier in (project.voice_id, project.music_id):
        if identifier and not asset_info(identifier).get('has_audio'):
            raise ValueError('Выбранный файл не содержит звуковую дорожку')


def render(project: Project, folder: Path, progress, *, draft=False):
    validate_assets(project)
    from .visual_fallback import complete_visuals
    project=complete_visuals(project)
    (folder / 'sources.txt').write_text(source_credits(project), encoding='utf-8')
    size = (960, 540) if draft else ((1920, 1080) if project.resolution == '1080p' else (1280, 720))
    w, h = size
    pieces = []
    for index, scene in enumerate(project.scenes):
        progress(5 + int(index / len(project.scenes)*65), f'Сцена {index+1} из {len(project.scenes)}')
        frame_total = frame_count(scene) + transition_frames(project, index)
        duration = frame_total / 30
        decor = folder / f'overlay-{index}.png'
        view=scene.model_copy(update={'title':'','caption':'','title_layout':'caption'}) if scene.text_events else scene
        overlay(view, size, decor_only=not scene.automatic_text).save(decor)
        titles = folder / f'titles-{index}.png'
        (Image.new('RGBA',size) if scene.automatic_text and not scene.text_events else overlay(scene,size,text_only=True)).save(titles)
        is_video = scene.asset_id and asset_info(scene.asset_id)['kind'] == 'video' and scene.template != 'quote'
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1']
        input_start=len(args)
        if is_video:
            args += ['-ss', str(scene.offset), '-i', str(asset_path(scene.asset_id))]
            input_end=len(args)
            # Freeze the last frame when the chosen clip is shorter than the scene.
            saturation = '0' if scene.template == 'archive' else '.78'
            base_filter = (f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,'
                           f'fps=30,eq=saturation={saturation},tpad=stop_mode=clone:stop={frame_total},setpts=N/(30*TB)')
        else:
            base = folder / f'base-{index}.jpg'
            tone(source_still(scene, size), scene).save(base, quality=95)
            args += ['-loop', '1', '-framerate', '30', '-i', str(base)]
            if project.photo_motion == 'smooth' and scene.motion and scene.template != 'quote':
                step = f'(0.5-0.5*cos(PI*on/{max(1,frame_total-1)}))'
                zoom = f'1+0.035*{step}'
                x = 'iw/2-iw/zoom/2'
                if scene.motion_style == 'pull':
                    zoom = f'1.035-0.035*{step}'
                elif scene.motion_style in ('left', 'right'):
                    zoom = '1.035'
                    x = f'(iw-iw/zoom)*{step}' if scene.motion_style == 'left' else f'(iw-iw/zoom)*(1-{step})'
                base_filter = (f"scale={w*4}:{h*4}:flags=lanczos,zoompan=z='{zoom}':"
                               f"x='{x}':y='ih/2-ih/zoom/2':d=1:s={w*2}x{h*2}:fps=30,scale={w}:{h}:flags=lanczos,setsar=1")
            else:
                base_filter = 'setsar=1'
        args += ['-loop', '1', '-framerate', '30', '-i', str(decor)]
        fade = f',fade=t=in:st=0:d=0.18,fade=t=out:st={duration-.18}:d=0.18' if project.transition == 'fade' else ''
        effect = scene.title_effect
        if effect == 'inherit':
            effect = 'rise' if project.animate_titles else 'static'
        delay = round(scene.title_delay*30)/30
        entrance = max(1, round(scene.title_duration*30))/30
        advanced = effect in ADVANCED or scene.title_decoration != 'none'
        if scene.text_events:
            animation=folder/f'text-events-{index}.mov'
            encode_events(scene,size,frame_total,animation)
            args += ['-i',str(animation)]
            title_filter=f'format=rgba,fps=30,tpad=stop_mode=clone:stop={frame_total},setpts=N/(30*TB)'
            x,y='0','0'
        elif advanced:
            animation = folder / f'title-animation-{index}.mov'
            encode_title(overlay(scene,size,text_only=True),scene,effect,animation)
            args += ['-i', str(animation)]
            title_filter = f'format=rgba,tpad=stop_mode=clone:stop={frame_total},setpts=PTS+{delay}/TB'
            x,y='0','0'
        else:
            args += ['-loop', '1', '-framerate', '30', '-i', str(titles)]
            title_filter = 'format=rgba'
            if effect != 'static':
                title_filter += f',fade=t=in:st={delay}:d={entrance}:alpha=1'
            remaining = f'pow(max(0,1-(t-{delay})/{entrance}),3)'
            x = f'-{w}*{remaining}' if effect == 'slide' else '0'
            y = f'{h*.16}*{remaining}' if effect == 'rise' else '0'
        if scene.title_exit:
            exit_start=max(delay,frame_count(scene)/30-.35)
            x=f'({x})-{w}*pow(min(1,max(0,(t-{exit_start})/.35)),2)'
        picture_graph=f'[0:v]{base_filter}[bg];'
        if scene.picture_effect in ('inset','split') and not is_video:
            card_image=framed_card(scene,size)
            card=folder/f'card-{index}.png';card_image.save(card)
            args += ['-loop','1','-framerate','30','-i',str(card)]
            repeating=index>0 and project.scenes[index-1].asset_id==scene.asset_id
            entrance='' if repeating else ',fade=t=in:st=0:d=0.22:alpha=1'
            card_filter=portrait_card_motion(card_image,frame_total,index) if portrait_photo(scene) else 'format=rgba'
            direction=-1 if index%2 else 1
            picture_graph += (f'[bg]gblur=sigma={w/45}:steps=2,eq=brightness=-0.20[soft];'
                              f'[3:v]{card_filter}{entrance}[paper];'
                              f"[soft][paper]overlay=x='(W-w)/2+W*{0 if repeating else .18*direction}*pow(max(0,1-t/0.55),3)':y=(H-h)/2[picture];")
        else:
            picture_graph += '[bg]null[picture];'
        title_visible='1' if scene.text_events else f'gte(t,{delay})' if scene.title_end is None else f'between(t,{delay},{scene.title_end})'
        graph = (picture_graph+'[picture][1:v]overlay=0:0:shortest=1[framed];'
                 f'[2:v]{title_filter}[title];'
                 f"[framed][title]overlay=x='{x}':y='{y}':enable='{title_visible}':shortest=1{fade},format=yuv420p[out]")
        piece = folder / f'scene-{index:03d}.mp4'
        args += ['-filter_complex', graph, '-map', '[out]', '-an', '-frames:v', str(frame_total),
                 '-r', '30', '-fps_mode', 'cfr',
                 '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2',
                 '-video_track_timescale', '15360', str(piece)]
        run(args, timeout=600)
        if not normalize_clock(piece,frame_total):
            if is_video:
                # A problematic source must not shorten the following narration.
                still=folder/f'recovered-{index}.jpg'
                tone(source_still(scene,size),scene).save(still,quality=95)
                args[input_start:input_end]=['-loop','1','-framerate','30','-i',str(still)]
                run(args,timeout=600)
            if not normalize_clock(piece,frame_total):require_frames(piece,frame_total)
        pieces.append(piece)
    video = assemble(project, pieces, folder, progress)
    use_ass=project.show_subtitles and bool(project.subtitles) and capabilities()['ass']
    if project.show_subtitles and project.subtitles and not use_ass:
        progress(86, 'Субтитры: резервный способ для FFmpeg без libass')
        video=burn_captions(video,project.subtitles,size,sum(frame_count(s) for s in project.scenes),folder)
    progress(87, 'Сведение озвучки и звуков переходов')
    output = folder / 'film.mp4'
    audio_ids = [(asset_path(i), volume, role) for i,volume,role in
                 [(project.voice_id,1.0,'voice'),(project.music_id,project.music_volume,'music')] if i]
    generated_music=None
    if project.auto_music and not project.music_id and project.voice_id:
        from .music import ambient_loop
        generated_music=ambient_loop(folder/'ambient.wav')
        audio_ids.append((generated_music,project.music_volume,'music'))
    sfx = folder / 'transitions.wav'
    if synthesize_sfx(project, sfx):
        audio_ids.append((sfx, 1.0, 'sfx'))
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1', '-i', str(video)]
    if audio_ids:
        filters = []
        for i, (path, volume, role) in enumerate(audio_ids, 1):
            if generated_music and path==generated_music:
                args += ['-stream_loop','-1']
            args += ['-i', str(path)]
            from .media import probe
            channels=next(s.get('channels',2) for s in probe(path)['streams'] if s['codec_type']=='audio')
            stereo='pan=stereo|c0=c0|c1=c0' if channels==1 else 'aformat=channel_layouts=stereo'
            music_fade = (f',afade=t=out:st={max(0,total_duration(project)-.7)}:d=0.7'
                          if role == 'music' else '')
            filters.append(f'[{i}:a]aresample=48000,{stereo},asetpts=PTS-STARTPTS,volume={volume},apad{music_fade}[a{i}]')
        labels = ''.join(f'[a{i}]' for i in range(1, len(audio_ids)+1))
        duration = total_duration(project)
        filters.append(f'{labels}amix=inputs={len(audio_ids)}:normalize=0,alimiter=limit=0.95:level=0[audio]')
        args += ['-filter_complex', ';'.join(filters), '-map', '0:v', '-map', '[audio]',
                 '-c:a', 'aac', '-b:a', '192k', '-t', str(duration)]
    else:
        args += ['-map', '0:v', '-an']
    if use_ass:
        progress(90, 'Субтитры и финальная сборка')
        subs = folder / 'captions.ass'
        subs.write_text(ass_document(project.subtitles, w, h), encoding='utf-8')
        for path in FONTS.glob('*.ttf'):
            shutil.copy2(path, folder / path.name)
        # Use a relative, generated filename so client text never enters filter syntax.
        args += ['-vf', 'ass=captions.ass:fontsdir=.', '-c:v', 'libx264', '-preset', 'veryfast',
                 '-crf', '20', '-threads', '2']
    else:
        args += ['-c:v', 'copy']
    args += ['-movflags', '+faststart', str(output)]
    subprocess.run(args, cwd=folder, capture_output=True, text=True, check=True, timeout=max(600,math.ceil(total_duration(project)*4)))
    require_frames(output,sum(frame_count(s) for s in project.scenes))
    # Retain only the project, status and final deliverable.
    for path in folder.iterdir():
        if path.name not in ('film.mp4', 'project.json', 'status.json', 'sources.txt'):
            path.unlink()
    progress(100, 'Ролик готов')
    return output
