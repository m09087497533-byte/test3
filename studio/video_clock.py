"""Verify frame counts and repair timestamps before assembling the narration timeline."""
from fractions import Fraction
from pathlib import Path
from .media import probe,run


def video_clock(path):
    stream=next(s for s in probe(path)['streams'] if s['codec_type']=='video')
    count=stream.get('nb_frames')
    if not count or count=='N/A':
        import json
        result=run(['ffprobe','-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames','-of','json',str(path)])
        count=json.loads(result.stdout)['streams'][0]['nb_read_frames']
    return int(count),float(stream.get('duration',0)),Fraction(stream.get('avg_frame_rate','0/1'))


def require_frames(path,expected):
    count,duration,rate=video_clock(path)
    if count!=expected:
        raise ValueError(f'Неполная видеодорожка: получено {count} из {expected} кадров. Файл не будет выдан как готовый ролик.')
    if abs(duration-expected/30)>.04 or rate!=30:
        raise ValueError(f'Неверная длительность видеоряда: {duration:.3f} с вместо {expected/30:.3f} с. Файл не будет выдан как готовый ролик.')


def normalize_clock(path,expected):
    count,duration,rate=video_clock(path)
    if count!=expected:return False
    if abs(duration-expected/30)<.02 and rate==30:return True
    repaired=path.with_name(path.stem+'-clock.mp4')
    # Reset timestamps BEFORE fps; fps first would irreversibly discard frames.
    run(['ffmpeg','-v','error','-y','-i',str(path),'-vf','settb=1/30,setpts=N,fps=30,setsar=1',
         '-frames:v',str(expected),'-an','-r','30','-fps_mode','cfr','-c:v','libx264','-preset','veryfast',
         '-crf','20','-threads','2','-video_track_timescale','15360',str(repaired)],timeout=600)
    require_frames(repaired,expected);repaired.replace(path)
    return True
