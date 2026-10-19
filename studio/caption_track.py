"""Burn readable subtitles with Pillow when FFmpeg was built without libass."""
import math
from PIL import Image,ImageDraw
from .media import run


def caption_image(text,size):
    # Imported at call time to share the exact bundled fonts and wrapping behavior.
    from .render import font,wrapped
    width,height=size
    image=Image.new('RGBA',size)
    if not text:return image
    draw=ImageDraw.Draw(image)
    stroke=max(1,round(height/360))
    for point in range(max(10,round(height*.042)),7,-1):
        face=font(point)
        value=wrapped(text,face,round(width*.84))
        spacing=max(2,round(point*.18))
        box=draw.multiline_textbbox((0,0),value,font=face,spacing=spacing,stroke_width=stroke)
        if box[3]-box[1]<=height*.42:break
    x=(width-(box[2]-box[0]))/2-box[0]
    y=height-round(height*.035)-(box[3]-box[1])-box[1]
    draw.multiline_text((x,y),value,font=face,fill='white',stroke_width=stroke,stroke_fill='black',spacing=spacing,align='center')
    return image


def caption_states(cues,frames):
    events={0:[],frames:[]}
    for index,cue in enumerate(cues):
        # Frames whose presentation time belongs to [start,end) show the cue.
        start=max(0,min(frames,math.ceil(cue.start*30-1e-8)))
        end=max(0,min(frames,math.ceil(cue.end*30-1e-8)))
        if end<=start:continue
        events.setdefault(start,[]).append((True,index));events.setdefault(end,[]).append((False,index))
    active=set();states=[];times=sorted(events)
    for i,frame in enumerate(times[:-1]):
        for enabled,index in events[frame]:
            if enabled:active.add(index)
            else:active.discard(index)
        states.append((frame,times[i+1]-frame,'\n'.join(cues[index].text for index in sorted(active))))
    return states


def burn_captions(video,cues,size,frames,folder):
    names={};lines=['ffconcat version 1.0']
    for _,duration,text in caption_states(cues,frames):
        if text not in names:
            name=f'caption-{len(names):04d}.png';names[text]=name;caption_image(text,size).save(folder/name)
        lines.extend([f"file '{names[text]}'",'option framerate 30',f'duration {duration/30:.12f}'])
    if '' not in names:
        names['']='caption-empty.png';caption_image('',size).save(folder/names[''])
    lines.extend([f"file '{names['']}'",'option framerate 30'])
    timeline=folder/'caption-timeline.txt';timeline.write_text('\n'.join(lines)+'\n')
    track=folder/'caption-track.mov'
    # All paths/options in this concat file are generated above, never user filenames.
    run(['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-f','concat','-safe','0','-i',str(timeline),
         '-an','-fps_mode','vfr','-c:v','qtrle','-threads','2',str(track)],timeout=600)
    output=folder/'captioned.mp4'
    graph=(f'[0:v]fps=30[base];[1:v]fps=30,tpad=stop_mode=clone:stop={frames}[captions];'
           '[base][captions]overlay=0:0:shortest=1,format=yuv420p[out]')
    run(['ffmpeg','-v','error','-y','-filter_complex_threads','1','-i',str(video),'-i',str(track),
         '-filter_complex',graph,'-map','[out]','-an','-frames:v',str(frames),'-r','30','-fps_mode','cfr',
         '-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-video_track_timescale','15360',str(output)],timeout=max(600,math.ceil(frames/30*4)))
    return output
