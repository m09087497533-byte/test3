import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageChops, ImageStat
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project, Scene, Subtitle, frame_count
from studio.subtitles import plan_scenes, apply_reference_rhythm
from studio.effects import boundary_style
from studio.playback import fingerprint
from studio.media import probe
from studio.render import render

class RhythmTest(unittest.TestCase):
    def test_short_opening_and_ten_minute_bound_preserve_narration(self):
        for duration in (14.111,49.24,600):
            cues=[Subtitle(start=i,end=min(i+2,duration),text='Фраза') for i in range(0,int(duration),3)]
            original=[c.model_dump() for c in cues]
            scenes=plan_scenes(cues,duration)
            self.assertLessEqual(scenes[0].duration,3.5)
            self.assertLessEqual(len(scenes),600)
            self.assertTrue(all(1<=s.duration<=30 for s in scenes))
            self.assertEqual(sum(frame_count(s) for s in scenes),__import__('math').ceil(duration*30))
            self.assertEqual([c.model_dump() for c in cues],original)

    def test_existing_project_rhythm_keeps_audio_cues_and_delayed_titles(self):
        project=Project(scenes=[Scene(duration=12,title='Поздний заголовок',title_delay=8,asset_id='a'*32),Scene(duration=3,asset_id='b'*32)],subtitles=[Subtitle(start=0,end=15,text='Текст')],voice_id='c'*32,show_subtitles=False)
        # Legacy rhythm helper remains compatible; v9 API is covered by director tests.
        revised=apply_reference_rhythm(project)
        self.assertEqual(revised.voice_id,project.voice_id)
        self.assertEqual(revised.subtitles,project.subtitles)
        self.assertFalse(revised.show_subtitles)
        self.assertEqual(sum(frame_count(s) for s in revised.scenes),450)
        self.assertLessEqual(revised.scenes[0].duration,3.5)
        self.assertEqual(revised.scenes[0].asset_id,'a'*32)
        self.assertIsNone(revised.scenes[1].asset_id)
        self.assertEqual(revised.scenes[-1].asset_id,'b'*32)
        start=0;titles=[]
        for scene in revised.scenes:
            if scene.title:titles.append(start+scene.title_delay)
            start+=scene.duration
        self.assertEqual(titles,[8])
        self.assertGreater(len(set(boundary_style(revised,i) for i in range(len(revised.scenes)-1))),1)
        again=apply_reference_rhythm(revised)
        self.assertEqual([s.duration for s in revised.scenes],[s.duration for s in again.scenes])
        revised=apply_reference_rhythm(Project(scenes=[Scene(duration=15) for _ in range(40)]))
        self.assertGreater(len(revised.scenes),40)
        self.assertLessEqual(len(revised.scenes),600)
        self.assertEqual(sum(frame_count(s) for s in revised.scenes),18000)

    def test_profile_transitions_render_between_different_pictures_without_shortening(self):
        project=apply_reference_rhythm(Project(scenes=[Scene(duration=1) for _ in range(4)]))
        colors=['#dd1515','#1515dd','#15dd15','#dddd15']
        with tempfile.TemporaryDirectory() as temp,patch('studio.render.source_still',side_effect=[Image.new('RGB',(960,540),c) for c in colors]):
            movie=render(project,Path(temp),lambda *a:None,draft=True)
            self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),120)
            raw=subprocess.check_output(['ffmpeg','-v','error','-ss','1.2','-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
            r,g,b=Image.open(io.BytesIO(raw)).convert('RGB').getpixel((400,100))
            self.assertGreater(r,40);self.assertGreater(b,40);self.assertLess(g,40)

    def test_subtitle_switch_skips_both_renderers_and_preserves_search_text(self):
        for ass in (True,False):
            with self.subTest(ass=ass),tempfile.TemporaryDirectory() as temp:
                project=Project(scenes=[Scene(duration=1)],transition='cut',subtitles=[Subtitle(start=0,end=1,text='ПОИСК СОХРАНЁН')],show_subtitles=False)
                before=fingerprint(project)
                with patch('studio.render.capabilities',return_value={'ass':ass}),patch('studio.render.burn_captions',side_effect=AssertionError('Hidden captions must not render')):
                    movie=render(project,Path(temp),lambda *a:None,draft=True)
                self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),30)
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss','0.5','-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                im=Image.open(io.BytesIO(raw)).convert('RGB').crop((80,465,880,530))
                self.assertLess(sum(1 for rgb in im.getdata() if min(rgb)>220),15)
                self.assertEqual(project.subtitles[0].text,'ПОИСК СОХРАНЁН')
                project.show_subtitles=True
                self.assertNotEqual(before,fingerprint(project))

    def test_photos_are_static_by_default_and_smooth_motion_has_no_large_jumps(self):
        picture=Image.new('RGB',(960,540),'#343434');draw=ImageDraw.Draw(picture)
        for x in range(40,920,80):draw.rectangle((x,20,x+20,380),fill='white')
        def frames(movie):
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(movie),'-f','rawvideo','-pix_fmt','gray','-'])
            return [Image.frombytes('L',(960,540),raw[i:i+960*540]).crop((80,40,880,350)) for i in range(0,len(raw),960*540)]
        for motion in ('static','smooth'):
            with tempfile.TemporaryDirectory() as temp,patch('studio.render.source_still',return_value=picture):
                p=Project(scenes=[Scene(duration=2)],transition='cut',photo_motion=motion)
                movie=render(p,Path(temp),lambda *a:None,draft=True)
                images=frames(movie)
                diffs=[ImageStat.Stat(ImageChops.difference(a,b)).mean[0] for a,b in zip(images,images[1:])]
                if motion=='static':self.assertLess(max(diffs),.25)
                else:
                    self.assertGreater(sum(diffs),1)
                    self.assertLess(max(diffs),4)
                    self.assertLess(sum(diffs[:5]),sum(diffs[25:30]))

    def test_portrait_document_gets_automatic_smooth_camera_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';source=assets/('a'*32);source.mkdir(parents=True)
            picture=Image.new('RGB',(560,800),'#e8d1a4');draw=ImageDraw.Draw(picture)
            for row in range(18):draw.rectangle((80,40+row*40,470,48+row*40),fill='#503524')
            picture.save(source/'image.jpg')
            (source/'meta.json').write_text(json.dumps({'id':'a'*32,'name':'Portrait manuscript','kind':'image','file':'image.jpg'}))
            output=root/'render';output.mkdir()
            with patch('studio.media.ASSETS',assets):
                movie=render(Project(scenes=[Scene(duration=2,asset_id='a'*32,picture_effect='inset')],show_subtitles=False),output,lambda *a:None,draft=True)
            self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),60)
            def frame(at):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB').crop((250,60,710,490))
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(frame(.6),frame(1.7))).mean),1)
