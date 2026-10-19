import io,json,subprocess,tempfile,time,unittest,wave
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from studio.models import Project,Scene,Subtitle,frame_count
from studio.auto_media import arrange
from studio.visual_fallback import complete_visuals
from studio.render import render
from studio.playback import PlaybackJobs
from studio.media import probe
from studio.app import app

class VisualFallbackTest(unittest.TestCase):
    def test_export_does_not_repeat_one_automatic_picture_through_five_scenes(self):
        picture={'id':'a'*32,'name':'Enoch manuscript','kind':'image'}
        project=Project(director_version=12,scenes=[Scene(duration=2,asset_id=picture['id'],media_reason='Фото · Книга Еноха · Wikimedia') for _ in range(5)],
                        subtitles=[Subtitle(start=i*2,end=(i+1)*2,text='Книга Еноха') for i in range(5)])
        with patch('studio.auto_media.local_assets',return_value=[picture]),patch('studio.media.asset_info',return_value=picture):
            with self.assertRaisesRegex(ValueError,'не найдено подходящее фото'):
                complete_visuals(project)

    def test_alternate_query_finds_media_before_fallback(self):
        project=Project(director_version=9,scenes=[Scene(duration=2)],subtitles=[Subtitle(start=0,end=2,text='Книга Еноха')])
        result_item={'title':'Enoch manuscript','token':'a','source_url':'https://example.test/1'}
        asset={'id':'a'*32,'name':'Enoch manuscript','kind':'image'}
        with patch('studio.auto_media.local_assets',return_value=[]),patch('studio.auto_media.stock_video.get_key',return_value=''),patch('studio.auto_media.image_search.search_images',side_effect=[[],[result_item]]) as search,patch('studio.auto_media.image_search.import_image',return_value=asset):
            result=arrange(project,'photos',False,lambda *a:None,lambda:False)
        self.assertEqual(search.call_args_list[1].args,('Enoch manuscript',))
        self.assertEqual(len(result['assignments']),1);self.assertFalse(result['fallbacks'])

    def test_exhausted_unique_images_do_not_repeat_adjacent_picture(self):
        matching={'id':'a'*32,'name':'Enoch manuscript','kind':'image'}
        wrong={'id':'b'*32,'name':'Greek manuscript','kind':'image'}
        p=Project(director_version=9,scenes=[Scene(asset_id='a'*32,duration=2),Scene(duration=2)],subtitles=[Subtitle(start=0,end=4,text='Книга Еноха')])
        with patch('studio.auto_media.local_assets',return_value=[matching,wrong]),patch('studio.auto_media.stock_video.get_key',return_value=''),patch('studio.auto_media.image_search.search_images',return_value=[]):
            result=arrange(p,'photos',False,lambda *a:None,lambda:False)
        self.assertFalse(result['assignments'])
        self.assertEqual(len(result['fallbacks']),1)

    def test_offline_search_returns_complete_text_scene_without_rewriting_timings(self):
        p=Project(director_version=9,scenes=[Scene(duration=2,title='2010',title_delay=1,title_end=1.8)],subtitles=[Subtitle(start=0,end=2,text='Книга Еноха')],show_subtitles=False)
        with patch('studio.auto_media.local_assets',return_value=[]),patch('studio.auto_media.stock_video.get_key',return_value=''),patch('studio.auto_media.image_search.search_images',side_effect=OSError('offline')):
            result=arrange(p,'photos',False,lambda *a:None,lambda:False)
        self.assertEqual(result['outcomes'][0]['status'],'text')
        scene=Scene.model_validate(result['fallbacks'][0]['scene'])
        self.assertEqual(scene.title,p.subtitles[0].text)
        self.assertEqual(scene.duration,2);self.assertEqual(scene.title_delay,0);self.assertIsNone(scene.title_end)
        self.assertTrue(result['errors'])

    def test_preview_and_export_with_holes_and_no_ass_keep_voice_and_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';assets.mkdir()
            identifier='c'*32;folder=assets/identifier;folder.mkdir()
            with wave.open(str(folder/'voice.wav'),'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\x00\x10'*96000)
            (folder/'meta.json').write_text(json.dumps({'id':identifier,'name':'Voice','kind':'audio','file':'voice.wav','has_audio':True,'duration':6}))
            project=Project(director_version=9,voice_id=identifier,sound_mode='off',transition='cut',show_subtitles=False,scenes=[Scene(duration=2) for _ in range(3)],subtitles=[Subtitle(start=0,end=2,text='Книга Еноха'),Subtitle(start=2,end=4,text='Греческая рукопись'),Subtitle(start=4,end=6,text='Эфиопская рукопись')])
            with patch('studio.media.ASSETS',assets),patch('studio.auto_media.local_assets',return_value=[]),patch('studio.render.capabilities',return_value={'ass':False}):
                manager=PlaybackJobs(root/'previews')
                try:
                    with patch('studio.app.playback_jobs',manager),TestClient(app) as client:
                        start=client.post('/api/playback',json=project.model_dump());self.assertEqual(start.status_code,202,start.text)
                        job=start.json()['id']
                        for _ in range(500):
                            state=client.get('/api/playback/'+job).json()
                            if state['status'] in ('done','error'):break
                            time.sleep(.02)
                        self.assertEqual(state['status'],'done',state)
                        self.assertEqual(client.get('/api/playback/'+job+'/video',headers={'Range':'bytes=0-255'}).status_code,206)
                    export=root/'export';export.mkdir();movie=render(project,export,lambda *a:None)
                    streams=probe(movie)['streams']
                    self.assertEqual(int(next(s for s in streams if s['codec_type']=='video')['nb_frames']),180)
                    self.assertTrue(any(s['codec_type']=='audio' for s in streams))
                    self.assertAlmostEqual(float(probe(movie)['format']['duration']),6,delta=.05)
                    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(movie),'-vn','-f','s16le','-'])
                    self.assertGreater(len(set(pcm)),2)
                finally:manager.pool.shutdown(wait=True)

    def test_automatic_text_card_can_be_replaced_when_matching_media_is_available(self):
        project=Project(director_version=9,scenes=[Scene(duration=2,template='thesis',title='Греческая рукопись',automatic_text=True)],subtitles=[Subtitle(start=0,end=2,text='Греческая рукопись')])
        asset={'id':'a'*32,'name':'Greek manuscript','kind':'image'}
        with patch('studio.auto_media.local_assets',return_value=[asset]),patch('studio.auto_media.stock_video.get_key',return_value=''):
            result=arrange(project,'photos',False,lambda *a:None,lambda:False)
        self.assertEqual(len(result['assignments']),1)
        self.assertTrue(result['assignments'][0]['restore_visual'])
        self.assertFalse(result['fallbacks'])
