"""Render original test graphics and all five text sounds; no borrowed audio/media.

This is a dense technical showcase, not the normal two-emphases-per-minute edit.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'downloads/reference-v18-demo.mp4')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kadr18-demo-') as tmp:
        os.environ['STUDIO_DATA_DIR']=str(Path(tmp)/'data')
        from PIL import Image,ImageDraw,ImageFont
        from studio.media import ASSETS,probe
        from studio.models import Project,Scene,TextEvent
        from studio.render import render,FONTS
        from studio.soundplan import sound_plan
        scenes=[]
        for i in range(8):
            identifier=f'{i+1:032x}';folder=ASSETS/identifier;folder.mkdir()
            image=Image.new('RGB',(960,540),('#263243','#3e303d','#253e40','#3f3530')[i%4])
            draw=ImageDraw.Draw(image)
            draw.rounded_rectangle((330,45,700,500),radius=8,fill='#d4c5a5',outline='#f9f1da',width=3)
            font=ImageFont.truetype(str(FONTS/'DejaVuSerif.ttf'),24)
            draw.text((380,85),'КНИГА ЕНОХА',font=font,fill='#3e352a')
            for row in range(14):draw.line((370,150+row*21,660-row%3*12,150+row*21),fill='#8c7c64',width=2)
            draw.text((25,505),f'Демонстрация приёмов · {i+1:02d}',font=ImageFont.truetype(str(FONTS/'DejaVuSans-Bold.ttf'),17),fill='#dcdedc')
            image.save(folder/'image.jpg')
            (folder/'meta.json').write_text(json.dumps(dict(id=identifier,kind='image',file='image.jpg',name='Авторская тестовая графика',duration=0,has_audio=False)))
            scenes.append(Scene(duration=4,asset_id=identifier,motion=False))
        specs={
            1:dict(text='Датируется 15 веком',treatment='italic',effect='scroll',duration=.65),
            2:dict(text='1773',treatment='neon',layout='number',effect='reveal',decoration='ring',duration=.85),
            4:dict(text='КНИГА ЕНОХА',layout='grid',effect='scroll',duration=.7),
            5:dict(text='1821',treatment='chromatic',layout='number',effect='stamp',decoration='ring',duration=.85),
            7:dict(text='КНИГА ЕНОХА',effect='typewriter',duration=.7),
        }
        for index,options in specs.items():scenes[index].text_events=[TextEvent(start=.6,end=3.2,**options)]
        project=Project(name='Приёмы 1018 — технический показ',scenes=scenes,
                                              sound_mode='auto',sounds_per_minute=12,sfx_volume=.55,show_subtitles=False,effects_version=18)
        for i,scene in enumerate(project.scenes[:-1]):scene.transition_out=('light_leak','dissolve','zoom','color_wash','cut','slide')[i%6]
        plan=sound_plan(project)
        assert {e['style'] for e in plan}=={'paper','spark','sweep','stamp','chime'},plan
        out=Path(tmp)/'render';out.mkdir()
        movie=render(project,out,lambda p,m:print(f'{p}% {m}',flush=True),draft=True)
        shutil.copy2(movie,args.output)
        args.output.with_suffix('.json').write_text(json.dumps(dict(note='Технический показ; плотность акцентов выше рабочего профиля.',duration=32,events=plan,probe=probe(movie)),ensure_ascii=False,indent=2))
        print('DONE',args.output,flush=True)


if __name__=='__main__':main()
