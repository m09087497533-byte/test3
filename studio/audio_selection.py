"""Copy exact original narration intervals into a bounded-memory preview WAV."""
import subprocess
import tempfile
import wave


def copy_ranges(source,segments,target):
    # Most filled scenes are consecutive. Decode each contiguous run once.
    ranges=[]
    for segment in segments:
        a,b=segment['source_start'],segment['source_end']
        if ranges and abs(ranges[-1][1]-a)<1e-6:ranges[-1]=(ranges[-1][0],b)
        else:ranges.append((a,b))
    with wave.open(str(target),'wb') as output:
        output.setparams((2,2,48000,0,'NONE','not compressed'))
        for a,b in ranges:
            expected=round((b-a)*48000)*4
            command=['ffmpeg','-v','error','-ss',str(a),'-i',str(source),'-vn','-t',str(b-a),
                     '-ac','2','-ar','48000','-c:a','pcm_s16le','-f','s16le','pipe:1']
            with tempfile.TemporaryFile() as log:
                process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=log)
                try:
                    remaining=expected
                    while remaining:
                        chunk=process.stdout.read(min(65536,remaining))
                        if not chunk:break
                        output.writeframesraw(chunk);remaining-=len(chunk)
                    process.stdout.close()
                    if process.wait(timeout=60):
                        raise ValueError('Не удалось подготовить озвучку выбранных сцен.')
                    # Shorter source audio ends in silence, just as in full export.
                    while remaining:
                        count=min(65536,remaining);output.writeframesraw(bytes(count));remaining-=count
                finally:
                    if process.poll() is None:process.kill();process.wait()
    return target
