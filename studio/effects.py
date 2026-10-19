"""Frame-aligned transitions and original synthesized transition sound effects."""
import math
import random
import struct
import subprocess
import tempfile
from array import array
from pathlib import Path

from .media import run
from .models import Project, frame_count, total_duration
from .video_clock import normalize_clock, require_frames

TRANSITIONS = {'dissolve': 'fade', 'slide': 'smoothleft', 'flash': 'fadewhite',
               'blur': 'hblur', 'wipe': 'wipeleft', 'zoom': 'zoomin',
               'light_leak': 'custom', 'color_wash': 'custom'}
CYCLE = ('dissolve', 'slide', 'flash', 'blur', 'wipe')


def transition_filter(style, seconds):
    base = f'xfade=transition={TRANSITIONS[style]}:duration={seconds}:offset=0'
    if style not in ('light_leak','color_wash'):
        return base
    # P runs from 1 (outgoing) to 0 (incoming). Colored light is confined to
    # the transition; both endpoints retain the original picture and timing.
    pulse = 'pow(sin(PI*P),2)'
    if style == 'light_leak':
        band = f'{pulse}*exp(-pow((X/W-(1-P))/.28,2))'
        extra = f'if(eq(PLANE,0),115,if(eq(PLANE,1),-26,42))*{band}'
    else:
        band = f'{pulse}*sin(PI*X/W)'
        extra = f'if(eq(PLANE,0),55,if(eq(PLANE,1),36,-30))*{band}'
    return base + f":expr='clip(A*P+B*(1-P)+{extra},0,255)'"


def boundary_style(project: Project, index: int) -> str:
    override = project.scenes[index].transition_out
    if override != 'inherit':
        return override
    if project.transition == 'mixed':
        return CYCLE[index % len(CYCLE)]
    return project.transition if project.transition in TRANSITIONS else 'cut'


def transition_frames(project: Project, index: int) -> int:
    if index >= len(project.scenes)-1 or boundary_style(project, index) == 'cut':
        return 0
    # Leave readable time even in a one-second scene.
    return min(12, frame_count(project.scenes[index+1])//3)


def encode_segment(args: list[str], graph: str, frames: int, output: Path):
    run(['ffmpeg', '-v', 'error', '-y', '-filter_complex_threads', '1', *args,
         '-filter_complex', graph, '-map', '[out]', '-an', '-frames:v', str(frames),
         '-r', '30', '-fps_mode', 'cfr', '-c:v', 'libx264', '-preset', 'veryfast',
         '-crf', '20', '-threads', '2', '-video_track_timescale', '15360', str(output)], timeout=600)
    require_frames(output,frames)


def join_group(paths: list[Path], counts: list[int], output: Path):
    """Join decoded frames, independent of MP4 packet timestamps on FFmpeg 9."""
    args=['ffmpeg','-v','error','-y','-filter_complex_threads','1']
    filters=[]
    for index,(path,count) in enumerate(zip(paths,counts)):
        args+=['-i',str(path)]
        filters.append(f'[{index}:v]trim=end_frame={count},settb=1/30,setpts=N,setsar=1,format=yuv420p[v{index}]')
    inputs=''.join(f'[v{i}]' for i in range(len(paths)))
    filters.append(f'{inputs}concat=n={len(paths)}:v=1:a=0,settb=1/30,setpts=N,format=yuv420p[out]')
    expected=sum(counts)
    args+=['-filter_complex',';'.join(filters),'-map','[out]','-an','-frames:v',str(expected),
           '-r','30','-fps_mode','cfr','-c:v','libx264','-preset','veryfast','-crf','20','-threads','2',
           '-g','60','-keyint_min','30','-video_track_timescale','15360','-movflags','+faststart',str(output)]
    run(args,timeout=max(600,math.ceil(expected/30*6)))
    require_frames(output,expected)


def join_group_raw(paths: list[Path], counts: list[int], output: Path):
    """Frame-only fallback when an FFmpeg build mishandles concat timestamps."""
    from .media import probe
    stream=next(s for s in probe(paths[0])['streams'] if s['codec_type']=='video')
    width,height=int(stream['width']),int(stream['height'])
    frame_bytes=width*height*3//2
    expected=sum(counts)
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','yuv420p',
             '-video_size',f'{width}x{height}','-framerate','30','-i','pipe:0',
             '-frames:v',str(expected),'-an','-c:v','libx264','-preset','veryfast',
             '-crf','20','-threads','2','-video_track_timescale','15360','-movflags','+faststart',str(output)]
    with tempfile.TemporaryFile() as encoder_log:
        encoder=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=encoder_log)
        try:
            for path,count in zip(paths,counts):
                decode=['ffmpeg','-v','error','-i',str(path),'-vf',f'scale={width}:{height},format=yuv420p',
                        '-frames:v',str(count),'-fps_mode','passthrough','-f','rawvideo','-pix_fmt','yuv420p','pipe:1']
                with tempfile.TemporaryFile() as decoder_log:
                    decoder=subprocess.Popen(decode,stdout=subprocess.PIPE,stderr=decoder_log)
                    try:
                        remaining=count*frame_bytes
                        while remaining:
                            chunk=decoder.stdout.read(min(1024*1024,remaining))
                            if not chunk:raise ValueError(f'Не удалось декодировать все {count} кадров сегмента.')
                            encoder.stdin.write(chunk);remaining-=len(chunk)
                        decoder.stdout.close()
                        if decoder.wait()!=0:
                            decoder_log.seek(0)
                            raise ValueError('Сбой декодирования сегмента: '+decoder_log.read(500).decode(errors='replace'))
                    finally:
                        if decoder.poll() is None:decoder.kill();decoder.wait()
            encoder.stdin.close()
            if encoder.wait()!=0:
                encoder_log.seek(0)
                raise ValueError('Сбой резервной склейки кадров: '+encoder_log.read(500).decode(errors='replace'))
        finally:
            if encoder.poll() is None:encoder.kill();encoder.wait()
    require_frames(output,expected)


def assemble(project: Project, pieces: list[Path], folder: Path, progress) -> Path:
    """Render only two adjacent handles at a time, avoiding an unbounded xfade graph.

    Each outgoing scene has a spare handle after its nominal end. Blend it with
    the first frames of the next scene, then consume those frames from its core.
    Result: no accumulated timeline shortening; SRT and voice remain absolute.
    """
    segments = []
    segment_frames=[]
    for index,piece in enumerate(pieces):
        expected=frame_count(project.scenes[index])+transition_frames(project,index)
        if not normalize_clock(piece,expected):require_frames(piece,expected)
    for index, (scene, piece) in enumerate(zip(project.scenes, pieces)):
        incoming = transition_frames(project, index-1) if index else 0
        outgoing = transition_frames(project, index)
        frames = frame_count(scene)
        if not incoming and not outgoing:
            segments.append(piece)
            segment_frames.append(frames)
        else:
            core = folder / f'core-{index:03d}.mp4'
            graph = f'[0:v]trim=start_frame={incoming}:end_frame={frames},setpts=N/(30*TB),format=yuv420p[out]'
            encode_segment(['-i', str(piece)], graph, frames-incoming, core)
            segments.append(core)
            segment_frames.append(frames-incoming)
        if outgoing:
            progress(73+int(index/len(pieces)*13), f'Переход {index+1}: {boundary_style(project,index)}')
            transition = folder / f'transition-{index:03d}.mp4'
            graph = (f'[0:v]trim=start_frame={frames}:end_frame={frames+outgoing},setpts=N/(30*TB),fps=30,settb=1/30[a];'
                     f'[1:v]trim=end_frame={outgoing},setpts=N/(30*TB),fps=30,settb=1/30[b];'
                     f'[a][b]{transition_filter(boundary_style(project,index),outgoing/30)},'
                     'format=yuv420p[out]')
            encode_segment(['-i', str(piece), '-i', str(pieces[index+1])], graph, outgoing, transition)
            segments.append(transition)
            segment_frames.append(outgoing)
    video = folder / 'picture.mp4'
    progress(85,'Сборка видеодорожки')
    expected=sum(frame_count(s) for s in project.scenes)
    if sum(segment_frames)!=expected:raise ValueError('Неверный расчёт кадров сцен и переходов.')
    # MP4 concat demuxing can collapse independent packet timelines on FFmpeg 9.
    # Decode each segment and reset its clock before the concat filter. Bounded
    # batches keep large projects below macOS file-descriptor and memory limits.
    level=0
    while len(segments)>1:
        merged=[];merged_frames=[]
        for index in range(0,len(segments),12):
            group=segments[index:index+12];counts=segment_frames[index:index+12]
            target=video if len(segments)<=12 else folder/f'joined-{level}-{index//12:03d}.mp4'
            try:join_group(group,counts,target)
            except (subprocess.CalledProcessError,ValueError):join_group_raw(group,counts,target)
            merged.append(target);merged_frames.append(sum(counts))
        segments,segment_frames=merged,merged_frames
        level+=1
    if segments[0]!=video:
        import shutil
        shutil.copy2(segments[0],video)
    require_frames(video,expected)
    return video


def synthesize_sfx(project: Project, output: Path) -> bool:
    if project.sfx_volume == 0:
        return False
    rate = 48000
    from .soundplan import sound_plan
    plan=sound_plan(project)
    if not plan:return False
    count=math.ceil(total_duration(project)*rate)
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
                volume=project.sfx_volume*edge*max(0,window_edge)
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
