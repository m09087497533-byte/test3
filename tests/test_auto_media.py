import json
import tempfile
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
import studio.auto_media as auto
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.render import render
from studio.media import probe
from studio.stock_video import StockError


def example_project():
    return Project(scenes=[Scene(duration=1),Scene(duration=1)],transition='cut',
                   subtitles=[Subtitle(start=0,end=.95,text='Глава сохранилась только в Эфиопии.'),
                              Subtitle(start=1,end=1.95,text='Худшие годы жизни — это испытание.')])


def asset(identifier,kind,name,query='',source=None):
    return {'id':identifier,'kind':kind,'name':name,'duration':2 if kind=='video' else 0,'file':'source' if kind=='video' else 'image.jpg',
            'has_audio':False,'provenance':{'query':query,'source_url':source or 'https://example.test/'+identifier,
                                          'author':'Test author','license':'Test fixture','license_url':'https://example.test/license'}}

class AutomaticMediaTest(unittest.TestCase):
    def test_real_user_eight_scenes_all_get_distinct_visuals_with_text_accent(self):
        from studio.director import automatic_project
        cues=[Subtitle(start=i*6,end=(i+1)*6,text=text) for i,text in enumerate((
            'Последняя глава книги Еноха.', 'Семь признаков его жизни.',
            'Книга Еноха записывает результат.', 'Рукопись сохранилась в Эфиопии.',
            'Греческая рукопись обрывается.', 'Один стих книги Еноха.',
            'Древняя рукопись и её страницы.', 'Последняя глава книги Еноха.'))]
        project=automatic_project(Project(scenes=[Scene(duration=6) for _ in cues],subtitles=cues),rebuild=False)
        catalog={
            'Book of Enoch manuscript':['Book of Enoch manuscript page one','Book of Enoch manuscript page two'],
            'Ethiopian illuminated manuscript':['Ethiopian illuminated manuscript first','Ethiopian illuminated manuscript second','Ethiopian illuminated manuscript third'],
            'Greek biblical manuscript':['Greek biblical manuscript codex'],
            'ancient manuscript parchment':['Ancient parchment manuscript illuminated page one','Ancient parchment manuscript illuminated page two'],
        }
        items={}
        for group in catalog.values():
            for name in group:
                token=f'{len(items)+1:032x}'
                items[token]={'token':token,'title':name,'description':'','source_url':'https://example.test/'+token}
        def search(query):
            return [item for item in items.values() if any(query==key and item['title'] in group for key,group in catalog.items())]
        def import_item(token):
            item=items[token]
            return asset(token,'image',item['title'],source=item['source_url'])
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',side_effect=search),\
             patch.object(auto.image_search,'import_image',side_effect=import_item):
            result=auto.arrange(project,'mixed',True,lambda x:None,lambda:False)
        self.assertEqual(len(result['assignments']),len(project.scenes))
        self.assertEqual(len(result['fallbacks']),0)
        self.assertEqual(len({a['asset']['id'] for a in result['assignments']}),len(result['assignments']))
        self.assertEqual(sum(e.text=='7 ПРИЗНАКОВ' for s in project.scenes for e in s.text_events),1)

    def test_new_project_has_no_previous_story_or_assets(self):
        with TestClient(app) as client:
            response=client.get('/api/project/new')
        self.assertEqual(response.status_code,200)
        project=Project.model_validate(response.json())
        self.assertEqual(project.name,'Новая история')
        self.assertIsNone(project.voice_id)
        self.assertEqual(project.subtitles,[])
        self.assertEqual(len(project.scenes),1)
        self.assertIsNone(project.scenes[0].asset_id)
        self.assertGreater(project.library_since,0)

    def test_fresh_project_does_not_reuse_old_story_library(self):
        old=asset('a'*32,'image','Ethiopian manuscript')
        old['created_at']=100
        project=Project(director_version=12,library_since=200,scenes=[Scene(title='Эфиопская рукопись')])
        with patch.object(auto,'local_assets',return_value=[old]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',return_value=[]):
            result=auto.arrange(project,'photos',False,lambda x:None,lambda:False)
        self.assertFalse(result['assignments'])
        self.assertEqual(len(result['fallbacks']),1)

    def test_one_text_accent_and_search_for_other_scenes(self):
        project=Project(director_version=12,scenes=[Scene(duration=4) for _ in range(6)],
                        subtitles=[Subtitle(start=i*4,end=(i+1)*4,text=text) for i,text in enumerate((
                            'Книга Еноха.', 'В 1821 году открыли рукопись.',
                            'Список из семи признаков записан здесь.', 'Рукопись хранилась долго.',
                            'Другие страницы показывают этот текст.', 'Читатель видит книгу Еноха.'))])
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',return_value=[]) as remote:
            result=auto.arrange(project,'mixed',False,lambda x:None,lambda:False)
        self.assertEqual(len(auto.graphic_indices(project)),1)
        self.assertGreater(remote.call_count,0)
        self.assertLessEqual(len(result['fallbacks']),2)
        self.assertEqual(len(result['fallbacks'])+sum(o['status']=='missing' for o in result['outcomes']),len(project.scenes))
        self.assertTrue(any(item['scene_index'] in auto.graphic_indices(project) for item in result['fallbacks']))

    def test_map_requires_location_and_map_description(self):
        project=Project(scenes=[Scene(duration=4)],subtitles=[Subtitle(start=0,end=4,text='Посмотрите карту Эфиопии.')])
        context=auto.scene_context(project,0)
        self.assertEqual(context['query'],'Ethiopia map')
        self.assertFalse(auto.relevant_candidate(asset('a'*32,'image','Ethiopian manuscript'),context,local=True))
        self.assertTrue(auto.relevant_candidate(asset('b'*32,'image','Map of Ethiopia'),context,local=True))

    def test_one_source_cannot_fill_entire_story(self):
        photo=asset('a'*32,'image','Ethiopian manuscript')
        project=Project(director_version=12,scenes=[Scene(title='Эфиопская рукопись') for _ in range(5)])
        with patch.object(auto,'local_assets',return_value=[photo]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',return_value=[]):
            result=auto.arrange(project,'photos',False,lambda x:None,lambda:False)
        self.assertLessEqual(len(result['assignments']),2)
        self.assertLessEqual(len(result['fallbacks']),2)
        self.assertEqual(len(result['fallbacks'])+len(result['assignments'])+sum(o['status']=='missing' for o in result['outcomes']),5)

    def test_single_scene_variant_excludes_previous_asset(self):
        old=asset('a'*32,'image','Ethiopian manuscript')
        new=asset('b'*32,'image','Ethiopian manuscript second page')
        project=Project(director_version=12,scenes=[Scene(title='Эфиопская рукопись',asset_id=old['id']),Scene(title='Эфиопская рукопись')])
        with patch.object(auto,'local_assets',return_value=[old,new]),patch.object(auto.stock_video,'get_key',return_value=''):
            result=auto.arrange(project,'photos',True,lambda x:None,lambda:False,[0],[old['id']])
        self.assertEqual([(a['scene_index'],a['asset']['id']) for a in result['assignments']],[(0,new['id'])])
        self.assertEqual(len(result['outcomes']),1)

    def test_uses_subtitles_to_assign_photo_and_video_from_library(self):
        photo=asset('a'*32,'image','Ethiopian manuscript','Ethiopian illuminated manuscript')
        video=asset('b'*32,'video','Hiking mountain path','solitary person mountain path')
        updates=[]
        with patch.object(auto,'local_assets',return_value=[video,photo]),patch.object(auto.stock_video,'get_key',return_value=''),patch.object(auto.image_search,'search_images') as remote:
            result=auto.arrange(example_project(),'mixed',False,updates.append,lambda:False)
        self.assertEqual([a['asset']['kind'] for a in result['assignments']],['image','video'])
        self.assertEqual([a['scene_index'] for a in result['assignments']],[0,1])
        self.assertIn('Эфиопская',result['assignments'][0]['reason'])
        self.assertEqual(updates[-1]['progress'],100)

    def test_compares_photos_and_footage_for_each_scene(self):
        project=Project(director_version=12,scenes=[Scene(duration=5)],subtitles=[Subtitle(start=0,end=5,text='Книга Еноха')])
        photo={'token':'photo','title':'Book of Enoch manuscript page','source_url':'https://example.test/photo'}
        footage={'token':'video','title':'Enoch manuscript old book pages turning','source_url':'https://example.test/video','provider':'pexels','duration':8}
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value='key'),\
             patch.object(auto.image_search,'search_images',return_value=[photo]) as image_search,\
             patch.object(auto.stock_video,'search_photos',return_value=[photo]) as stock_photos,\
             patch.object(auto.stock_video,'search_videos',return_value=[footage]) as stock_videos,\
             patch.object(auto.image_search,'import_image',return_value=asset('a'*32,'image',photo['title'])),\
             patch.object(auto.stock_video,'import_photo',return_value=asset('b'*32,'image',photo['title'])),\
             patch.object(auto.stock_video,'import_video',return_value=asset('c'*32,'video',footage['title'])):
            result=auto.arrange(project,'mixed',True,lambda x:None,lambda:False)
        self.assertTrue(image_search.called)
        self.assertTrue(stock_photos.called)
        self.assertTrue(stock_videos.called)
        self.assertEqual(len(result['assignments']),1)

    def test_rate_limit_does_not_turn_all_scenes_into_text(self):
        project=Project(director_version=12,scenes=[Scene(title='Книга Еноха') for _ in range(5)])
        result_item={'token':'photo','title':'Book of Enoch manuscript page','source_url':'https://example.test/photo'}
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',return_value=[result_item]),\
             patch.object(auto.image_search,'import_image',side_effect=ValueError('HTTP 429')):
            result=auto.arrange(project,'photos',True,lambda x:None,lambda:False)
        self.assertFalse(result['fallbacks'])
        self.assertEqual([o['status'] for o in result['outcomes']],['missing']*5)
        self.assertTrue(any('HTTP 429' in e for e in result['errors']))

    def test_russian_filenames_match_visual_topics(self):
        photo=asset('a'*32,'image','Эфиопская рукопись.jpg')
        video=asset('b'*32,'video','Путник в горах.mp4')
        with patch.object(auto,'local_assets',return_value=[photo,video]),patch.object(auto.stock_video,'get_key',return_value=''):
            result=auto.arrange(example_project(),'mixed',False,lambda x:None,lambda:False)
        self.assertEqual([a['asset']['id'] for a in result['assignments']],['a'*32,'b'*32])

    def test_keeps_assigned_media_and_text_backgrounds(self):
        p=Project(scenes=[Scene(asset_id='a'*32,title='Енох'),Scene(template='quote',title='Цитата')])
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),patch.object(auto.image_search,'search_images') as remote:
            result=auto.arrange(p,'mixed',False,lambda x:None,lambda:False)
        self.assertFalse(result['assignments']);self.assertEqual([o['status'] for o in result['outcomes']],['kept','skipped']);remote.assert_not_called()

    def test_downloads_video_then_falls_back_to_photo_without_repeated_failed_source(self):
        p=Project(scenes=[Scene(duration=2,title='Худшие годы'),Scene(duration=2,title='Древняя книга Еноха')])
        video={'token':'1'*32,'provider':'pexels','title':'hiking mountains','source_url':'https://example.test/hike','duration':5,'width':1920,'height':1080}
        photo={'token':'2'*32,'title':'Enoch manuscript','source_url':'https://example.test/book','width':1200,'height':800}
        def search(provider,query):
            if 'book' in query:raise StockError('Сервис недоступен.')
            return [video]
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value='test-key'),\
             patch.object(auto.stock_video,'search_videos',side_effect=search),\
             patch.object(auto.stock_video,'import_video',return_value=asset('a'*32,'video','hike',source=video['source_url'])),\
             patch.object(auto.image_search,'search_images',return_value=[photo]),\
             patch.object(auto.image_search,'import_image',return_value=asset('b'*32,'image','book',source=photo['source_url'])):
            result=auto.arrange(p,'mixed',False,lambda x:None,lambda:False)
        self.assertEqual([a['asset']['kind'] for a in result['assignments']],['video','image'])

    def test_unrelated_local_file_is_not_assigned_and_duplicates_are_not_used(self):
        photo=asset('a'*32,'image','Ethiopian manuscript','Ethiopian illuminated manuscript')
        unrelated=asset('b'*32,'image','ocean boat waves','ocean')
        p=Project(scenes=[Scene(title='Эфиопия'),Scene(title='Эфиопия')])
        with patch.object(auto,'local_assets',return_value=[photo,unrelated]),patch.object(auto.stock_video,'get_key',return_value=''),patch.object(auto.image_search,'search_images',return_value=[]):
            result=auto.arrange(p,'photos',False,lambda x:None,lambda:False)
        self.assertEqual(len(result['assignments']),1);self.assertEqual(result['outcomes'][1]['status'],'missing')

    def test_mode_and_missing_credentials_are_respected(self):
        p=Project(scenes=[Scene(title='Эфиопия')])
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),patch.object(auto.image_search,'search_images') as photo:
            result=auto.arrange(p,'videos',False,lambda x:None,lambda:False)
        self.assertEqual(len(result['assignments']),0);self.assertIn('Pexels',result['outcomes'][0]['reason']);photo.assert_not_called()

    def test_failed_source_is_not_retried_for_every_scene(self):
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),patch.object(auto.image_search,'search_images',side_effect=OSError('network')) as search:
            result=auto.arrange(example_project(),'photos',False,lambda x:None,lambda:False)
        self.assertEqual(search.call_count,1);self.assertEqual(len(result['outcomes']),2);self.assertEqual(len(result['errors']),1)

    def test_cancel_keeps_already_found_materials(self):
        photos=[asset('a'*32,'image','Ethiopian manuscript'),asset('b'*32,'video','mountain path')]
        cancelled=False
        def update(state):
            nonlocal cancelled
            if state['assignments']:cancelled=True
        with patch.object(auto,'local_assets',return_value=photos),patch.object(auto.stock_video,'get_key',return_value=''):
            result=auto.arrange(example_project(),'mixed',False,update,lambda:cancelled)
        self.assertEqual(len(result['assignments']),1);self.assertEqual(len(result['outcomes']),1)

    def test_real_job_applies_to_renderable_media_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';assets.mkdir()
            photo=asset('a'*32,'image','Ethiopian manuscript','Ethiopian illuminated manuscript')
            video=asset('b'*32,'video','mountain path hiking','solitary person mountain path')
            for item in [photo,video]:
                folder=assets/item['id'];folder.mkdir();(folder/'meta.json').write_text(json.dumps(item))
                Image.new('RGB',(640,360),'#386448').save(folder/'thumb.jpg')
                if item['kind']=='image':Image.new('RGB',(800,600),'#c7bb94').save(folder/'image.jpg')
                else:subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','2','-c:v','libx264','-threads','2','-f','mp4',str(folder/'source')],check=True)
            manager=auto.ArrangementJobs(root/'jobs')
            with patch('studio.app.arrangement_jobs',manager),patch.object(auto,'ASSETS',assets),patch.object(auto.stock_video,'get_key',return_value=''),TestClient(app) as client:
                response=client.post('/api/auto-media',json={'project':example_project().model_dump(),'mode':'mixed'})
                self.assertEqual(response.status_code,202,response.text);identifier=response.json()['id']
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    state=client.get('/api/auto-media/'+identifier).json()
                    if state['status']=='done':break
                    time.sleep(.02)
                self.assertEqual(state['status'],'done');self.assertEqual(len(state['assignments']),2)
            manager.pool.shutdown(wait=True)
            restored=auto.ArrangementJobs(root/'jobs');self.assertEqual(restored.get(identifier)['status'],'done');restored.pool.shutdown()
            project=example_project()
            for item in state['assignments']:project.scenes[item['scene_index']].asset_id=item['asset']['id']
            out=root/'render';out.mkdir()
            with patch('studio.media.ASSETS',assets):film=render(project,out,lambda *a:None)
            self.assertEqual(int(probe(film)['streams'][0]['nb_frames']),60)
