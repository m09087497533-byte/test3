import io
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageChops,ImageStat
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle,frame_count
from studio.director import automatic_project
from studio.image_search import scene_query
from studio.auto_media import scene_context,relevant_candidate,arrange
from studio.render import render,preview
from studio.media import probe

class DirectorTest(unittest.TestCase):
    def test_automatic_import_builds_slower_shots_and_timed_reference_appearances(self):
        cues=[Subtitle(start=0,end=4,text='Книга Еноха'),Subtitle(start=4.5,end=8,text='Рукопись сохранилась в Эфиопии'),
              Subtitle(start=9,end=12,text='Семь признаков'),Subtitle(start=15,end=18,text='Греческая рукопись'),
              Subtitle(start=23,end=26,text='В 2010 году'),Subtitle(start=32,end=35,text='Вы увидите воздаяние')]
        original=Project(scenes=[Scene(duration=18),Scene(duration=18)],subtitles=cues,show_subtitles=False)
        with TestClient(app) as client:
            response=client.post('/api/project/reference-rhythm',json=original.model_dump())
        self.assertEqual(response.status_code,200,response.text)
        directed=Project.model_validate(response.json())
        self.assertEqual(directed.subtitles,cues);self.assertFalse(directed.show_subtitles)
        self.assertLessEqual(len(directed.scenes),6)
        self.assertEqual(sum(frame_count(s) for s in directed.scenes),1080)
        self.assertTrue(any(s.picture_effect=='inset' for s in directed.scenes))
        self.assertTrue(any(s.title_effect=='scroll' for s in directed.scenes))
        self.assertTrue(any(s.title_layout=='number' and s.title_decoration=='ring' for s in directed.scenes))
        self.assertTrue(all(not s.motion for s in directed.scenes))
        start=0
        for scene in directed.scenes:
            if scene.title: self.assertTrue(any(c.start-.04<=start+scene.title_delay<c.end for c in cues))
            start+=scene.duration
        self.assertEqual(automatic_project(directed).model_dump(),directed.model_dump())

    def test_duration_limits_and_automatic_subtitle_endpoint(self):
        with TestClient(app) as client:
            response=client.post('/api/subtitles',files={'file':('x.vtt',b'WEBVTT\n\n00:00.000 --> 00:05.000\nSeven signs\n\n00:05.000 --> 00:15.000\nGreek manuscript','text/vtt')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['director_version'],12)
        self.assertTrue(any(s['title_effect']=='scroll' for s in response.json()['scenes']))
        p=automatic_project(Project(scenes=[Scene(duration=30) for _ in range(20)],subtitles=[Subtitle(start=0,end=600,text='Рукопись')]))
        self.assertLessEqual(len(p.scenes),600)
        self.assertEqual(sum(frame_count(s) for s in p.scenes),18000)

    def test_semantics_uses_dominant_spoken_subject_not_first_matching_dictionary_entry(self):
        p=Project(scenes=[Scene(duration=8)],subtitles=[Subtitle(start=0,end=1,text='Эфиопия'),Subtitle(start=1,end=8,text='Греческая рукопись')])
        self.assertEqual(scene_query(p,0)['query'],'Greek biblical manuscript')
        context=scene_context(p,0)
        self.assertFalse(relevant_candidate({'title':'Ethiopian illuminated manuscript'},context))
        self.assertTrue(relevant_candidate({'title':'Greek biblical manuscript'},context))
        self.assertFalse(relevant_candidate({'title':'ocean sunset'},context))
        p=Project(scenes=[Scene(duration=4),Scene(duration=4)],subtitles=[Subtitle(start=0,end=4,text='Книга Еноха'),Subtitle(start=4,end=8,text='Это было не наказанием')])
        self.assertEqual(scene_query(p,1)['query'],'Book of Enoch manuscript')

    def test_unrelated_remote_first_result_is_not_imported(self):
        p=Project(scenes=[Scene(title='Греческая рукопись')])
        with patch('studio.auto_media.local_assets',return_value=[]),patch('studio.auto_media.stock_video.get_key',return_value=''),patch('studio.auto_media.image_search.search_images',return_value=[{'title':'Ocean sunset','token':'x','source_url':'https://example.test'}]),patch('studio.auto_media.image_search.import_image') as download:
            result=arrange(p,'photos',False,lambda x:None,lambda:False)
        self.assertFalse(result['assignments']);download.assert_not_called()
        self.assertEqual(result['outcomes'][0]['status'],'missing')

    def test_real_framed_document_entrance_settles_and_timed_title_disappears(self):
        picture=Image.new('RGB',(960,540),'#b6a67d')
        scene=Scene(duration=2,picture_effect='inset',title='2010',title_layout='number',title_effect='reveal',title_delay=.2,title_end=1,title_decoration='ring',motion=False)
        with tempfile.TemporaryDirectory() as temp,patch('studio.render.source_still',return_value=picture):
            movie=render(Project(scenes=[scene],transition='cut',show_subtitles=False),Path(temp),lambda *a:None,draft=True)
            self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),60)
            def frame(t):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB')
            a,b,c,d=map(frame,(.03,.7,1.3,1.7))
            self.assertGreater(ImageStat.Stat(ImageChops.difference(a,b)).mean[0],2)
            self.assertLess(sum(ImageStat.Stat(ImageChops.difference(c,d)).mean),1)
            self.assertGreater(sum(1 for r,g,b in b.getdata() if r>g*1.5 and r>b*1.5),100)
            self.assertLess(sum(1 for r,g,b in c.getdata() if r>g*1.5 and r>b*1.5),20)
            self.assertEqual(preview(scene).size,(960,540))

    def test_missing_media_becomes_narration_composition_without_mutating_project(self):
        from studio.visual_fallback import complete_visuals
        project=automatic_project(Project(scenes=[Scene(duration=5)],subtitles=[Subtitle(start=0,end=5,text='Книга Еноха')]))
        original=project.model_dump()
        with patch('studio.auto_media.local_assets',return_value=[]):
            ready=complete_visuals(project)
        self.assertEqual(ready.scenes[0].template,'thesis')
        self.assertEqual(ready.scenes[0].title,'Книга Еноха')
        self.assertEqual(project.model_dump(),original)

    def test_year_after_a_long_phrase_does_not_break_automatic_build(self):
        cue=Subtitle(start=0,end=5,text='слово '*30+'2010')
        p=automatic_project(Project(scenes=[Scene(duration=5)],subtitles=[cue]))
        self.assertEqual(p.scenes[0].title,'2010')
