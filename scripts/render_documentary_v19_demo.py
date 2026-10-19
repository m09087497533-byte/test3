"""Full-length technical regression for the 49.267 s film comparison.

The drawings and control phrases are test inputs, not a reconstruction of the
user's flattened MP4. No competitor audio, browser UI or sampled figures ship.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'downloads/documentary-v19-demo.mp4')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kadr19-demo-') as tmp:
        os.environ['STUDIO_DATA_DIR']=str(Path(tmp)/'data')
        from PIL import Image,ImageDraw,ImageFont
        from studio.media import ASSETS,probe
        from studio.models import Project,Scene,Subtitle
        from studio.director import automatic_project
        from studio.soundplan import sound_plan
        from studio.render import render,FONTS
        cues=[(0,4.4,'Книга Еноха: последняя глава.'),(4.5,8.2,'В рукописи описаны испытания.'),
              (8.3,11.8,'Семь признаков.'),(12,18,'Страница за страницей.'),
              (18.2,23,'Древний документ сохранился.'),(23.2,27,'Единственный глагол в тексте.'),
              (27.2,33,'Строки рукописи и страницы книги.'),(33.2,36,'Результат был не'),
              (36,38,'наказанием.'),(38.3,42,'Старинная рукопись и её страницы.'),
              (42.2,46,'Вы увидите воздаяние.'),(46,1478/30,'Последняя глава книги Еноха.')]
        project=automatic_project(Project(name='Логика монтажа — технический пример',scenes=[Scene(duration=24),Scene(duration=1478/30-24)],
                                          subtitles=[Subtitle(start=a,end=b,text=t) for a,b,t in cues],sound_mode='auto',sounds_per_minute=5,show_subtitles=False))
        for index,scene in enumerate(project.scenes):
            identifier=f'{index+1:032x}';folder=ASSETS/identifier;folder.mkdir()
            image=Image.new('RGB',(600,850),('#d1c2a1','#d9cdaf','#c8b58c','#ddd2b5')[index%4])
            draw=ImageDraw.Draw(image);font=ImageFont.truetype(str(FONTS/'DejaVuSerif.ttf'),26)
            draw.rectangle((25,25,575,825),outline='#735f48',width=3)
            draw.text((65,65),'КНИГА ЕНОХА',font=font,fill='#413626')
            for row in range(23):
                y=145+row*25
                draw.line((60,y,535-(row+index)%5*14,y),fill='#75674f',width=3)
            draw.text((50,790),f'Контрольная графика · {index+1:02d}',font=ImageFont.truetype(str(FONTS/'DejaVuSans-Bold.ttf'),18),fill='#695a45')
            image.save(folder/'source.png')
            meta=dict(id=identifier,kind='image',file='source.png',name='Контрольный документ',duration=0)
            if index in (0,3):
                # A genuinely moving foreground card exercises the video branch.
                subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-framerate','30','-i',str(folder/'source.png'),
                                '-vf',"zoompan=z=1.04:x='(iw-iw/zoom)*(.5+.4*sin(on/35))':y='(ih-ih/zoom)/2':d=1:s=600x850:fps=30",
                                '-t',str(scene.duration+1),'-c:v','libx264','-pix_fmt','yuv420p','-threads','2',str(folder/'source.mp4')],check=True)
                meta.update(kind='video',file='source.mp4',duration=scene.duration+1,has_audio=False)
            (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False))
            scene.asset_id=identifier;scene.offset=0;scene.automatic_text=False
            if scene.shot_role=='context':scene.picture_effect='plain'
        folder=Path(tmp)/'render';folder.mkdir()
        plan=sound_plan(project)
        movie=render(project,folder,lambda p,m:print(f'{p}% {m}',flush=True),draft=True)
        info=probe(movie);video=next(s for s in info['streams'] if s['codec_type']=='video')
        assert int(video['nb_frames'])==1478
        assert abs(float(video['duration'])-1478/30)<.04
        shutil.copy2(movie,args.output)
        # Full recipe for inspection; media IDs point to temporary test assets.
        args.output.with_suffix('.json').write_text(json.dumps(dict(note='Технический пример с контрольными фразами, без исходной озвучки и медиа пользовательского проекта.',
                      project=project.model_dump(),events=plan,probe=info),ensure_ascii=False,indent=2))
        subprocess.run(['ffmpeg','-v','error','-i',str(args.output),'-f','null','-'],check=True)
        print('PASS: 1478 frames, full decode, exact 49.267-second timeline.',flush=True)


if __name__=='__main__':main()
