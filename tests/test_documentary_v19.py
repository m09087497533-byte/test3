import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image,ImageChops,ImageDraw,ImageStat
from studio.models import Project,Scene,Subtitle,TextEvent
from studio.director import automatic_project
from studio.reference_effects import apply_reference_effects
from studio.text_track import event_frame
from studio.soundplan import sound_plan
from studio.render import render
from studio.media import probe


def read_frame(movie,at):
    raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
    return Image.open(io.BytesIO(raw)).convert('RGB')


class DocumentaryLogicTests(unittest.TestCase):
    def test_replaced_automatic_title_cannot_survive_as_a_stale_manual_title(self):
        p=Project(scenes=[Scene(duration=6,title='2010',text_events=[TextEvent(text='2010',start=1,end=3)])],
                  subtitles=[Subtitle(start=0,end=6,text='Книга Еноха')],director_version=12,effects_version=18)
        migrated=apply_reference_effects(p)
        self.assertNotEqual(migrated.scenes[0].title,'2010')
        self.assertFalse(any(e.text=='2010' for e in migrated.scenes[0].text_events))

    def test_legacy_dark_archive_frame_becomes_a_document_card(self):
        p=Project(scenes=[Scene(duration=5,template='archive',asset_id='a'*32)],subtitles=[Subtitle(start=0,end=5,text='Древняя книга Еноха.')])
        migrated=apply_reference_effects(p)
        self.assertEqual(migrated.scenes[0].asset_id,'a'*32)
        self.assertEqual(migrated.scenes[0].template,'cinema')
        self.assertEqual(migrated.scenes[0].picture_effect,'inset')

    def test_short_labels_do_not_displace_two_major_accents(self):
        from studio.narration import emphasis_events
        cues=[Subtitle(start=1,end=4,text='Семь признаков.'),
              Subtitle(start=10,end=13,text='Результат был не'),Subtitle(start=13,end=14,text='наказанием.'),
              Subtitle(start=22,end=25,text='Единственный глагол в тексте.'),
              Subtitle(start=35,end=38,text='В 1773 году нашли документ.')]
        events=emphasis_events(cues)
        major=[e for e in events if e.get('tier')!='caption'];minor=[e for e in events if e.get('tier')=='caption']
        self.assertEqual(len(major),2);self.assertEqual(len(minor),2)
        negative=next(e for e in minor if e['text'].startswith('РЕЗУЛЬТАТ'))
        self.assertEqual(negative['text'],'РЕЗУЛЬТАТ БЫЛ НЕ НАКАЗАНИЕМ')
        self.assertFalse(any(e['text']=='2010' for e in events))

    def test_transition_choices_follow_shots_and_same_source_stays_a_cut(self):
        p=Project(scenes=[Scene(duration=5,asset_id='a'*32),Scene(duration=8,asset_id='b'*32),
                          Scene(duration=7,asset_id='c'*32),Scene(duration=5,asset_id='c'*32)],
                  subtitles=[Subtitle(start=0,end=5,text='Начало истории.'),Subtitle(start=5,end=13,text='Древняя книга.'),
                             Subtitle(start=13,end=20,text='Буквы и строки страницы.'),Subtitle(start=20,end=25,text='Страница крупным планом.')])
        edited=apply_reference_effects(p)
        self.assertEqual(edited.scenes[0].transition_out,'light_leak')
        self.assertEqual(edited.scenes[1].transition_out,'zoom')
        self.assertEqual(edited.scenes[2].transition_out,'cut')
        self.assertEqual(edited.scenes[2].camera_style,'detail')
        self.assertEqual([(s.asset_id,s.duration,s.offset) for s in edited.scenes],[(s.asset_id,s.duration,s.offset) for s in p.scenes])

    def test_marquee_moves_after_entry_but_grid_is_stationary(self):
        scene=Scene(duration=5)
        event=TextEvent(text='СТИХ ЗА СТИХОМ',start=0,end=3,span=3,effect='marquee',layout='grid',duration=.6)
        a,b=[event_frame(scene,event,(640,360),t,1) for t in (1,1.8)]
        self.assertNotEqual(a.tobytes(),b.tobytes())
        self.assertEqual(a.crop((0,0,640,80)).tobytes(),b.crop((0,0,640,80)).tobytes())
        continued=event.model_copy(update={'elapsed':1,'start':0,'end':2})
        same=event_frame(scene,continued,(640,360),1.8,1)
        self.assertEqual(b.tobytes(),same.tobytes())

    def test_blank_card_is_recovered_to_media_or_stops_export(self):
        from studio.visual_fallback import complete_visuals
        p=Project(scenes=[Scene(template='quote',duration=6)],director_version=12)
        migrated=apply_reference_effects(p)
        self.assertEqual(migrated.scenes[0].template,'cinema')
        with self.assertRaisesRegex(ValueError,'ждёт фото'):
            complete_visuals(migrated)
        p.scenes[0].asset_id='a'*32
        restored=apply_reference_effects(p)
        self.assertEqual(restored.scenes[0].asset_id,'a'*32)
        self.assertFalse(restored.scenes[0].automatic_text)

    def test_no_blank_background_after_an_automatic_accent_expires(self):
        from studio.visual_fallback import complete_visuals
        p=Project(scenes=[Scene(template='quote',automatic_text=True,duration=6,title='Фраза',
                               text_events=[TextEvent(text='Фраза',start=1,end=2)])],director_version=12,effects_version=19)
        with self.assertRaisesRegex(ValueError,'нет кадра'):
            complete_visuals(p)

    def test_historical_story_rejects_the_modern_film2_stock(self):
        from studio.auto_media import scene_context,relevant_candidate
        p=Project(scenes=[Scene(duration=4),Scene(duration=5)],
                  subtitles=[Subtitle(start=0,end=4,text='Книга Еноха.'),Subtitle(start=4,end=9,text='Это были испытания его жизни.')])
        context=scene_context(p,1)
        for title in ['Tourists hiking in mountains','Turtle walking on rocks','Coffee and lilac flowers next to an old book']:
            self.assertFalse(relevant_candidate({'title':title},context),title)

    def test_sound_plan_has_only_text_even_with_camera_beats(self):
        p=apply_reference_effects(Project(scenes=[Scene(duration=10),Scene(duration=10)],sound_mode='auto',transition_sounds=True))
        self.assertEqual(sound_plan(p),[])


class DocumentaryRenderTests(unittest.TestCase):
    def test_live_video_inside_frame_and_sidebar_is_not_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';folder=assets/('a'*32);folder.mkdir(parents=True)
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x480:r=30',
                            '-vf','drawbox=x=0:y=0:w=iw:h=45:color=red:t=fill,drawbox=x=0:y=435:w=iw:h=45:color=blue:t=fill',
                            '-t','2','-c:v','libx264','-threads','2',str(folder/'source.mp4')],check=True)
            (folder/'meta.json').write_text(json.dumps(dict(id='a'*32,kind='video',file='source.mp4',name='Test live document',duration=2)))
            for effect in ('inset','split'):
                out=root/effect;out.mkdir()
                p=Project(scenes=[Scene(duration=2,asset_id='a'*32,picture_effect=effect,camera_style='push',document_title='ДОКУМЕНТ')],photo_motion='smooth',transition='cut',show_subtitles=False)
                with patch('studio.media.ASSETS',assets):movie=render(p,out,lambda *a:None,draft=True)
                self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),60)
                a,b=read_frame(movie,.7),read_frame(movie,1.5)
                self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(a,b)).mean),2)
                pixels=list(b.getdata())
                self.assertGreater(sum(r>150 and b<80 for r,g,b in pixels),300)
                self.assertGreater(sum(b>150 and r<80 for r,g,b in pixels),300)

    def test_whole_portrait_card_moves_without_cutting_its_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';folder=assets/('a'*32);folder.mkdir(parents=True)
            image=Image.new('RGB',(400,800),'#d9cbaa');image.paste('red',(0,0,400,80));image.paste('blue',(0,720,400,800))
            ImageDraw.Draw(image).line((100,100,300,700),fill='black',width=25);image.save(folder/'source.png')
            (folder/'meta.json').write_text(json.dumps(dict(id='a'*32,kind='image',file='source.png',name='Page')))
            out=root/'render';out.mkdir()
            p=Project(scenes=[Scene(duration=2,asset_id='a'*32,picture_effect='inset',camera_style='push',camera_amount=.09)],photo_motion='smooth',transition='cut')
            with patch('studio.media.ASSETS',assets):movie=render(p,out,lambda *a:None,draft=True)
            a,b=read_frame(movie,.6),read_frame(movie,1.7)
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(a,b)).mean),.5)
            for image in (a,b):
                pixels=list(image.getdata())
                self.assertGreater(sum(r>180 and b<80 for r,g,b in pixels),300)
                self.assertGreater(sum(b>180 and r<80 for r,g,b in pixels),300)
