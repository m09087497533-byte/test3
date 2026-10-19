import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.soundplan import sound_plan
from studio.title_animation import title_frame
from studio.render import overlay,render
from studio.media import probe
from studio.image_search import scene_query,search_images,import_image,safe_url

class AutomaticSoundTest(unittest.TestCase):
    def test_sparse_deterministic_budget_and_no_stacked_events(self):
        p=Project(sound_mode='auto',sounds_per_minute=4,transition='mixed',transition_sounds=True,
                  scenes=[Scene(duration=3,title='Глава',template='thesis',title_sound='impact') for _ in range(20)])
        plan=sound_plan(p)
        self.assertEqual(len(plan),4)
        self.assertEqual(plan,sound_plan(p))
        self.assertTrue(all(b['time']-a['time']>=7.5 for a,b in zip(plan,plan[1:])))
        p.sound_mode='off';self.assertEqual(sound_plan(p),[])
        p.sound_mode='manual';self.assertGreater(len(sound_plan(p)),4)

    def test_short_clips_can_have_no_sound(self):
        p=Project(sound_mode='auto',scenes=[Scene(duration=2,title='Коротко')])
        self.assertEqual(sound_plan(p),[])

class ReferenceAnimationTest(unittest.TestCase):
    def test_perspective_settles_and_ring_draws_progressively(self):
        s=Scene(title='2010',template='statement',title_effect='perspective',title_duration=.6)
        layer=overlay(s,(640,360),text_only=True)
        self.assertIsNone(title_frame(layer,s,'perspective',0).getbbox())
        self.assertIsNotNone(ImageChops.difference(title_frame(layer,s,'perspective',.2),layer).getbbox())
        self.assertIsNone(ImageChops.difference(title_frame(layer,s,'perspective',1),layer).getbbox())
        s.title_decoration='ring'
        self.assertIsNotNone(ImageChops.difference(title_frame(layer,s,'static',1.5),layer).getbbox())

    def test_real_advanced_animation_export_preserves_duration(self):
        p=Project(sound_mode='auto',scenes=[Scene(title='ПРОКРУТКА',template='thesis',duration=1,title_effect='scroll',title_exit=True),
                 Scene(title='ПЕРСПЕКТИВА',duration=1,title_effect='perspective',title_delay=.2),
                 Scene(title='2010',template='statement',duration=1,title_effect='reveal',title_decoration='ring')],transition='cut')
        with tempfile.TemporaryDirectory() as tmp:
            out=render(p,Path(tmp),lambda *a:None)
            info=probe(out)
            video=next(x for x in info['streams'] if x['codec_type']=='video')
            self.assertEqual(int(video['nb_frames']),90)
            self.assertAlmostEqual(float(video['duration']),3,places=2)

class ImageSearchTest(unittest.TestCase):
    def test_scene_subtitles_choose_visual_concept(self):
        p=Project(scenes=[Scene(duration=5)],subtitles=[Subtitle(start=0,end=4,text='Эта глава сохранилась только в Эфиопии.')])
        self.assertEqual(scene_query(p,0)['query'],'Ethiopian illuminated manuscript')
        p.subtitles[0].text='Вулкан внезапно проснулся'
        self.assertEqual(scene_query(p,0)['query'],'volcano eruption')

    def test_network_failure_is_visible(self):
        with TestClient(app) as client,patch('studio.image_search.fetch',side_effect=OSError('network denied')):
            response=client.post('/api/images/search',json={'project':Project(scenes=[Scene(title='Енох')]).model_dump(),'scene_index':0})
            self.assertEqual(response.status_code,502)
            self.assertIn('Wikimedia',response.json()['detail'])

    def test_real_image_import_with_provider_fixture_retains_attribution(self):
        page={'query':{'pages':{'1':{'title':'File:Test manuscript.jpg','index':1,'imageinfo':[{
            'mime':'image/jpeg','url':'https://upload.wikimedia.org/test.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Test.jpg',
            'width':1200,'height':800,'extmetadata':{'Artist':{'value':'<b>Artist</b>'},'LicenseShortName':{'value':'CC BY-SA 4.0'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0/'}}}]}}}}
        with patch('studio.image_search.fetch',return_value=json.dumps(page).encode()):
            items=search_images('manuscript')
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['author'],'Artist')
        data=io.BytesIO();Image.new('RGB',(800,600),'#b9a778').save(data,format='JPEG')
        with tempfile.TemporaryDirectory() as tmp,patch('studio.image_search.ASSETS',Path(tmp)),patch('studio.image_search.fetch',return_value=data.getvalue()):
            meta=import_image(items[0]['token'])
            self.assertTrue((Path(tmp)/meta['id']/'image.jpg').is_file())
            self.assertEqual(meta['provenance']['license'],'CC BY-SA 4.0')
            self.assertEqual(meta['provenance']['query'],'manuscript')

    def test_import_does_not_accept_arbitrary_urls(self):
        for url in ['http://upload.wikimedia.org/a','https://127.0.0.1/a','https://upload.wikimedia.org.evil.test/a','https://user@upload.wikimedia.org/a']:
            with self.assertRaises(ValueError):safe_url(url)
        with self.assertRaises(ValueError):import_image('0'*32)
