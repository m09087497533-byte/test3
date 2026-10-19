"""
Сборка финального видео: на каждый сегмент транскрипта — свой футаж (и, опционально,
свой эффект — затемнение/Кен Бёрнс/зум/панорама/растворение), поверх всего —
оригинальная озвучка.

Выбранные межкадровые переходы собираются через xfade-цепочку;
остальные границы остаются обычными склейками. Автоматический текст
накладывается единым слоем поверх сборки и не меняет ротацию эффектов.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from effects import TRANSITION_STYLES, transition_filter

WIDTH = 1280
HEIGHT = 720
FPS = 25


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=20,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _base_scale_crop(width: int, height: int, oversized: bool = False) -> str:
    """Приводит кадр к нужному размеру. oversized — с запасом (для панорамы, чтобы было куда двигаться)."""
    if oversized:
        ow, oh = int(width * 1.3), int(height * 1.3)
        return f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh}"
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def _effect_vf(effect: str, duration: float, fps: int, width: int, height: int) -> str:
    """Строит filter graph для одного клипа под конкретный эффект."""
    d = max(duration, 0.1)

    if effect == "pan_right":
        base = _base_scale_crop(width, height, oversized=True)
        return f"{base},crop=w={width}:h={height}:x='(in_w-{width})*t/{d}':y='(in_h-{height})/2'"
    if effect == "pan_left":
        base = _base_scale_crop(width, height, oversized=True)
        return f"{base},crop=w={width}:h={height}:x='(in_w-{width})*(1-t/{d})':y='(in_h-{height})/2'"
    if effect == "pan_down":
        base = _base_scale_crop(width, height, oversized=True)
        return f"{base},crop=w={width}:h={height}:x='(in_w-{width})/2':y='(in_h-{height})*t/{d}'"
    if effect == "pan_up":
        base = _base_scale_crop(width, height, oversized=True)
        return f"{base},crop=w={width}:h={height}:x='(in_w-{width})/2':y='(in_h-{height})*(1-t/{d})'"
    if effect == "zoom_in":
        base = _base_scale_crop(width, height)
        return (f"{base},zoompan=z='min(zoom+0.0015,1.3)':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}")
    if effect == "zoom_out":
        base = _base_scale_crop(width, height)
        return (f"{base},zoompan=z='if(eq(on,0),1.3,max(zoom-0.0015,1.0))':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}")
    if effect == "ken_burns_ur":
        base = _base_scale_crop(width, height)
        return (f"{base},zoompan=z='min(zoom+0.0012,1.25)':"
                f"x='iw/2-(iw/zoom/2)+(on*2.5)':y='ih/2-(ih/zoom/2)-(on*1.5)':"
                f"d=1:s={width}x{height}:fps={fps}")
    if effect == "ken_burns_dl":
        base = _base_scale_crop(width, height)
        return (f"{base},zoompan=z='if(eq(on,0),1.25,max(zoom-0.0012,1.0))':"
                f"x='iw/2-(iw/zoom/2)-(on*2.5)':y='ih/2-(ih/zoom/2)+(on*1.5)':"
                f"d=1:s={width}x{height}:fps={fps}")
    if effect == "fade":
        base = _base_scale_crop(width, height)
        fade_dur = min(0.5, d * 0.3)
        return f"{base},fade=t=in:st=0:d={fade_dur},fade=t=out:st={max(0, d - fade_dur)}:d={fade_dur}"

    # Межкадровые переходы обрабатываются отдельно, на этапе склейки.
    return _base_scale_crop(width, height)


def _encode_options():
    return ["-r", str(FPS), "-c:v", "libx264", "-preset", "fast", "-threads", "2",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "12800"]


def _clip_to_duration(src: Path, duration: float, out_path: Path, effect: str = "none"):
    # Loop only when needed; fixed output frame counts avoid cumulative rounding drift.
    count = max(1, round(duration * FPS))
    vf = _effect_vf(effect, count / FPS, FPS, WIDTH, HEIGHT)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-filter_threads", "1",
           "-stream_loop", "-1", "-i", str(src), "-an", "-vf",
           f"fps={FPS},{vf},setsar=1,setpts=PTS-STARTPTS", "-frames:v", str(count)]
    subprocess.run(cmd + _encode_options() + [str(out_path)], check=True, timeout=max(90,duration*10))


def _black_clip(duration: float, out_path: Path):
    count = max(1,round(duration*FPS))
    subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi","-i",
                    f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}","-frames:v",str(count),
                    *_encode_options(),str(out_path)],check=True,timeout=max(60,duration*6))


def _concat_simple(clip_paths: list[Path], tmp: Path) -> Path:
    # All internal paths use generated filenames. Quote apostrophes for concat demuxer.
    concat_file = tmp / "concat.txt"
    with concat_file.open('w',encoding='utf-8') as file:
        for path in clip_paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            file.write("file '" + escaped + "'\n")
    out = tmp / "video_only.mp4"
    subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0",
                    "-i",str(concat_file),"-an","-c:v","copy",str(out)],
                   check=True,timeout=max(120,len(clip_paths)*10))
    return out


def _xfade_batch(clip_paths, clip_durations, transitions, out_path, timeout, transition_styles=None):
    if len(clip_paths)==1:
        shutil.copyfile(clip_paths[0],out_path)
        return clip_durations[0]
    filters = [f"[{i}:v]settb=1/{FPS},setpts=N,fps={FPS},setsar=1[v{i}]"
               for i in range(len(clip_paths))]
    prev='[v0]'
    total=clip_durations[0]
    for i in range(1,len(clip_paths)):
        transition=transitions[i-1]
        out=f'[join{i}]'
        raw=f'[raw{i}]'
        if transition>0:
            style=transition_styles[i-1] if transition_styles else 'dissolve'
            filters.append(f"{prev}[v{i}]{transition_filter(style,transition,total-transition)}{raw}")
        else:
            # A hard cut stays a real hard cut, with no hidden two-frame overlap.
            filters.append(f"{prev}[v{i}]concat=n=2:v=1:a=0{raw}")
        # concat/setpts may clear CFR metadata. Restore the exact 25 fps clock
        # before another xfade; narration boundaries and handles stay the same.
        filters.append(f'{raw}settb=1/{FPS},setpts=N,fps={FPS}{out}')
        total = round((total+clip_durations[i]-transition)*FPS)/FPS
        prev=out
    inputs=[]
    for path in clip_paths:
        inputs += ['-i',str(path)]
    subprocess.run(['ffmpeg','-y','-loglevel','error','-filter_complex_threads','1',*inputs,
                    '-filter_complex',';'.join(filters),'-map',prev,'-an',
                    '-frames:v',str(round(total*FPS)),*_encode_options(),str(out_path)],
                   check=True,timeout=timeout)
    return total


def _transition_lengths(base_durations, effects, dissolve_dur=.6):
    transitions=[]
    for i in range(1,len(base_durations)):
        t = min(dissolve_dur,base_durations[i-1]*.4,base_durations[i]*.4) if effects[i] in TRANSITION_STYLES else 0
        # Even number of frames: two equal transition handles align on frame boundaries.
        frames=int(t*FPS)//2*2
        transitions.append(frames/FPS if frames>=2 else 0)
    return transitions


def _concat_with_dissolve(clip_paths, clip_durations, resolved_effects, tmp,
                           dissolve_dur=.6,batch_size=12,log_fn=print,transitions=None):
    if transitions is None:
        transitions=_transition_lengths(clip_durations,resolved_effects,dissolve_dur)
    paths=list(clip_paths); durations=list(clip_durations); links=list(transitions)
    styles=list(resolved_effects[1:])
    level=0
    while len(paths)>1:
        next_paths=[];next_durations=[];next_links=[];next_styles=[]
        for start in range(0,len(paths),batch_size):
            end=min(start+batch_size,len(paths))
            out=tmp/f'join_{level}_{start}.mp4'
            duration=_xfade_batch(paths[start:end],durations[start:end],links[start:end-1],out,
                                   timeout=max(300,sum(durations[start:end])*10),transition_styles=styles[start:end-1])
            next_paths.append(out);next_durations.append(duration)
            if end<len(paths):
                next_links.append(links[end-1])
                next_styles.append(styles[end-1])
            log_fn(f'   Переходы: проход {level+1}, блок {start//batch_size+1}')
        paths,durations,links=next_paths,next_durations,next_links
        styles=next_styles
        level+=1
    return paths[0]


def _build_timeline_with_gaps(segments, effects, gap_eps=.05, audio_duration=None):
    """Contiguous absolute frame boundaries; each pause belongs to exactly one clip."""
    if not segments:
        return []
    import math
    previous_end=0.0
    for i,seg in enumerate(segments):
        start,end=float(seg['start']),float(seg['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:
            raise ValueError(f'Некорректный таймкод сцены {i+1}')
        if i and start<previous_end-.05:
            raise ValueError(f'Сцены {i} и {i+1} перекрываются; проверь субтитры')
        previous_end=end
    finish = float(audio_duration) if audio_duration is not None else float(segments[-1]['end'])
    if not math.isfinite(finish) or finish<=0:
        raise ValueError('Не удалось определить длительность аудио')
    if segments[-1]['end'] > finish+.15:
        raise ValueError('Субтитры заканчиваются позже аудио; проверь выбранную дорожку')
    boundaries=[0]+[round(float(s['start'])*FPS) for s in segments[1:]]+[math.ceil(finish*FPS)]
    timeline=[]
    for i,seg in enumerate(segments):
        if boundaries[i+1]<=boundaries[i]:
            raise ValueError(f'Сцена {i+1} короче одного кадра или таймкоды идут не по порядку')
        timeline.append({**seg,'start':boundaries[i]/FPS,'end':boundaries[i+1]/FPS,
                         'effect':effects[i],'is_filler':False})
    return timeline


def build_video(segments, audio_path, output_path, effects=None, log_fn=print,
                text_accents=True, accent_rate=2, accent_sounds=False, accent_overrides=None):
    if not segments:
        raise ValueError('Нет сцен для сборки')
    output_path=Path(output_path)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    audio_duration=_probe_duration(Path(audio_path))
    effects=effects if effects and len(effects)==len(segments) else ['none']*len(segments)
    timeline=_build_timeline_with_gaps(segments,effects,audio_duration=audio_duration)
    base=[item['end']-item['start'] for item in timeline]
    transitions=_transition_lengths(base,effects)
    # Each crossfade is centred on its original narration boundary. Additional handles
    # exactly compensate its overlap locally, not by appending a frozen frame at the end.
    durations=[d+(transitions[i-1]/2 if i else 0)+(transitions[i]/2 if i<len(transitions) else 0)
               for i,d in enumerate(base)]
    with tempfile.TemporaryDirectory() as directory:
        tmp=Path(directory)
        paths=[]
        for i,(item,duration) in enumerate(zip(timeline,durations)):
            path=tmp/f'clip_{i:06d}.mp4'
            source=item.get('footage_path')
            if source and Path(source).exists():
                try:
                    _clip_to_duration(Path(source),duration,path,effect=effects[i])
                except (subprocess.SubprocessError,OSError) as exc:
                    # An assigned corrupt clip must not silently become a black screen.
                    raise RuntimeError(f'Не удалось обработать футаж сцены {i+1}; выбери другой файл') from exc
            elif source:
                raise FileNotFoundError(f'Футаж сцены {i+1} не найден: {source}')
            else:
                _black_clip(duration,path)
            paths.append(path)
            log_fn(f'   Сцена {i+1}/{len(timeline)} готова')
        if any(transitions):
            video=_concat_with_dissolve(paths,durations,effects,tmp,log_fn=log_fn,transitions=transitions)
        else:
            video=_concat_simple(paths,tmp)
        actual=_probe_duration(video)
        expected=sum(base)
        if abs(actual-expected)>2/FPS:
            raise RuntimeError(f'Нарушена длина видеоряда: {actual:.3f} вместо {expected:.3f} с')
        events=[];track=None;sfx=None
        if text_accents:
            from accents import plan_accents
            from accents.track import encode_track
            events=plan_accents(segments,audio_duration,major_rate=accent_rate,fps=FPS,overrides=accent_overrides)
            if events:
                log_fn(f'   Текстовые акценты: {len(events)}; очередь эффектов сохранена')
                track=tmp/'text-accents.mov'
                encode_track(events,(WIDTH,HEIGHT),round(expected*FPS),track,FPS)
            if events and accent_sounds:
                from accents.audio import synthesize_sfx
                sound=tmp/'text-accents.wav'
                if synthesize_sfx(events,audio_duration,sound):sfx=sound
        log_fn('   Добавляю озвучку...')
        # Stage beside destination for atomic replace. Existing exports survive a failed render.
        with tempfile.NamedTemporaryFile(dir=output_path.parent,suffix='.mp4',delete=False) as f:
            staged=Path(f.name)
        try:
            args=['ffmpeg','-y','-loglevel','error','-filter_complex_threads','1','-i',str(video),'-i',str(audio_path)]
            filters=[];next_input=2
            if track:
                args+=['-i',str(track)];next_input+=1
                filters.append(f'[0:v]fps={FPS},setpts=PTS-STARTPTS[base];[2:v]fps={FPS},setpts=PTS-STARTPTS[words];[base][words]overlay=eof_action=pass:format=auto,format=yuv420p[outv]')
            if sfx:
                args+=['-i',str(sfx)]
                filters.append(f'[1:a]aresample=48000,apad[voice];[{next_input}:a]aresample=48000[sfx];[voice][sfx]amix=inputs=2:normalize=0,alimiter=limit=0.95:level=0:latency=1[outa]')
            if filters:args+=['-filter_complex',';'.join(filters)]
            args+=['-map','[outv]' if track else '0:v:0','-map','[outa]' if sfx else '1:a:0']
            args+=_encode_options() if track else ['-c:v','copy']
            args+=['-c:a','aac','-b:a','192k','-t',str(audio_duration),'-movflags','+faststart',str(staged)]
            subprocess.run(args,
                           check=True,timeout=max(180,audio_duration*2))
            check=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=nb_frames',
                                  '-of','default=noprint_wrappers=1:nokey=1',str(staged)],capture_output=True,text=True,check=True,timeout=30)
            if check.stdout.strip().isdigit() and int(check.stdout.strip())!=round(expected*FPS):
                raise RuntimeError('Число кадров готового видео не совпало с длительностью озвучки')
            staged.replace(output_path)
        finally:
            staged.unlink(missing_ok=True)
    log_fn(f'   Готово: {output_path}')
