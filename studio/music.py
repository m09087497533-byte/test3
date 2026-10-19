"""Quiet, original ambient loop for new projects without supplied music."""
from array import array
import math
import wave
from pathlib import Path


def ambient_loop(path: Path):
    """Write a 16-second chord bed with a quiet loop seam and no external audio."""
    rate=24000
    seconds=16
    chords=((146.83,174.61,220.00,293.66),
            (116.54,146.83,174.61,233.08),
            (130.81,164.81,196.00,261.63),
            (130.81,164.81,196.00,261.63))
    samples=array('h')
    for i in range(rate*seconds):
        t=i/rate
        position=t/4
        first=int(position)%4
        blend=(1-math.cos(math.pi*(position%1)))/2
        def chord(index):
            return sum(math.sin(2*math.pi*frequency*t+.12*math.sin(2*math.pi*.21*t))
                       for frequency in chords[index])/4
        value=(1-blend)*chord(first)+blend*chord((first+1)%4)
        seam=min(1,t/.7,(seconds-t)/.7)
        slow=.82+.18*math.sin(2*math.pi*.13*t)**2
        samples.append(round(max(-1,min(1,value*.55*seam*slow))*32767))
    with wave.open(str(path),'wb') as output:
        output.setparams((1,2,rate,0,'NONE','not compressed'))
        output.writeframes(samples.tobytes())
    return path
