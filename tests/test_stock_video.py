import io
import json
import os
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageChops,ImageStat
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.stock_video import search_videos,import_video,search_photos,import_photo,footage_query,media_url,StockError,api_response,save_keys,key_status
from studio.render import render
from studio.media import probe

PEXELS={'videos':[{'id':1,'url':'https://www.pexels.com/video/test-1/','duration':2,'image':'https://images.pexels.com/a.jpg','user':{'name':'Author'},'video_files':[
 {'file_type':'video/mp4','width':3840,'height':2160,'link':'https://videos.pexels.com/4k.mp4'},
 {'file_type':'video/mp4','width':1920,'height':1080,'link':'https://videos.pexels.com/hd.mp4'},
 {'file_type':'video/mp4','width':640,'height':360,'link':'https://videos.pexels.com/small.mp4'}]}]}
PIXABAY={'hits':[{'id':2,'pageURL':'https://pixabay.com/videos/test-2/','duration':2,'tags':'book, candle','user':'Artist','videos':{'medium':{'url':'https://cdn.pixabay.com/test.mp4','width':1280,'height':720,'thumbnail':'https://cdn.pixabay.com/t.jpg'}}}]}
PEXELS_PHOTOS={'photos':[{'url':'https://www.pexels.com/photo/old-book-1/','alt':'Ancient manuscript parchment',
                          'photographer':'Author','width':1600,'height':900,'src':{'large':'https://images.pexels.com/photos/1/a.jpeg'}}]}
PIXABAY_PHOTOS={'hits':[{'pageURL':'https://pixabay.com/photos/old-book-2/','tags':'Ancient manuscript parchment',
                          'user':'Artist','webformatWidth':1280,'webformatHeight':720,
                          'webformatURL':'https://cdn.pixabay.com/photo/2/a.jpg'}]}

class StockVideoTest(unittest.TestCase):
    def test_context_uses_scene_subtitles_for_footage(self):
        p=Project(scenes=[Scene(duration=4)],subtitles=[Subtitle(start=0,end=3,text='Результат сохранился в книге Еноха')])
        self.assertEqual(footage_query(p,0)['query'],'Enoch manuscript')

    def test_both_providers_choose_usable_landscape_mp4(self):
        with patch('studio.stock_video.api_response',return_value=PEXELS):p=search_videos('pexels','book')[0]
        self.assertEqual(p['width'],1920);self.assertTrue(p['preview_url'].endswith('small.mp4'))
        with patch('studio.stock_video.api_response',return_value=PIXABAY):q=search_videos('pixabay','book')[0]
        self.assertEqual(q['author'],'Artist');self.assertEqual(q['license'],'Pixabay License')

    def test_stock_photos_can_be_found_and_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets=Path(tmp)/'assets';assets.mkdir()
            for provider,data in [('pexels',PEXELS_PHOTOS),('pixabay',PIXABAY_PHOTOS)]:
                with patch('studio.stock_video.api_response',return_value=data):item=search_photos(provider,'ancient manuscript')[0]
                self.assertIn('manuscript',item['title'].lower())
                def fake_download(url,provider,path,max_bytes):Image.new('RGB',(800,450),'#a18162').save(path,format='JPEG')
                with patch('studio.stock_video.ASSETS',assets),patch('studio.stock_video.download_media',side_effect=fake_download):
                    meta=import_photo(item['token'])
                self.assertEqual(meta['kind'],'image')
                self.assertEqual(meta['provenance']['provider'],provider)
                self.assertTrue((assets/meta['id']/'thumb.jpg').is_file())

    def test_missing_key_is_actionable_and_no_request_is_sent(self):
        with patch('studio.stock_video.get_key',return_value=''),patch('studio.stock_video.urllib.request.build_opener') as network:
            with TestClient(app) as client:
                response=client.post('/api/videos/search',json={'provider':'pexels','project':Project(scenes=[Scene(title='Книга')]).model_dump(),'scene_index':0})
            self.assertEqual(response.status_code,400);self.assertIn('API-ключ',response.json()['detail']);network.assert_not_called()

    def test_keys_are_private_and_status_does_not_echo_them(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio.stock_video.SETTINGS',Path(tmp)/'keys.json'),patch.dict(os.environ,{'PEXELS_API_KEY':'','PIXABAY_API_KEY':''}):
            response=save_keys({'pexels':'secret-example','pixabay':None})
            self.assertTrue(response['pexels']['configured']);self.assertNotIn('secret-example',json.dumps(response))
            self.assertEqual((Path(tmp)/'keys.json').stat().st_mode&0o777,0o600)
            save_keys({'pexels':''});self.assertFalse(key_status()['pexels']['configured'])

    def test_api_auth_and_24_hour_cache(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio.stock_video.CACHE',Path(tmp)),patch('studio.stock_video.get_key',return_value='test-secret'),patch('studio.stock_video.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=io.BytesIO(json.dumps(PEXELS).encode())
            self.assertEqual(api_response('pexels','book'),PEXELS)
            request=factory.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'),'test-secret')
            self.assertNotIn('test-secret',request.full_url)
            self.assertEqual(api_response('pexels','book'),PEXELS)
            self.assertEqual(factory.return_value.open.call_count,1)
            self.assertNotIn('test-secret',next(Path(tmp).glob('*.json')).read_text())

    def test_no_arbitrary_downloads_or_stale_tokens(self):
        for url in ['http://videos.pexels.com/a','https://127.0.0.1/a','https://videos.pexels.com.evil.test/a']:
            with self.assertRaises(StockError):media_url(url,'pexels')
        with self.assertRaises(StockError):import_video('0'*32)

    def test_import_real_mp4_and_export_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);assets=tmp/'assets';assets.mkdir()
            clip=tmp/'sample.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','2','-c:v','libx264','-threads','2',str(clip)],check=True)
            with patch('studio.stock_video.api_response',return_value=PEXELS):item=search_videos('pexels','book')[0]
            with patch('studio.stock_video.ASSETS',assets),patch('studio.stock_video.download_media',side_effect=lambda url,provider,path:path.write_bytes(clip.read_bytes())):
                with TestClient(app) as client:
                    response=client.post('/api/videos/import',json={'token':item['token']})
                self.assertEqual(response.status_code,200,response.text);meta=response.json()
            self.assertEqual(meta['kind'],'video');self.assertEqual(meta['provenance']['provider'],'pexels')
            self.assertTrue((assets/meta['id']/'thumb.jpg').is_file())
            folder=tmp/'render';folder.mkdir()
            with patch('studio.media.ASSETS',assets):
                result=render(Project(scenes=[Scene(duration=2,asset_id=meta['id'],picture_effect='inset')],transition='cut'),folder,lambda *a:None)
            self.assertEqual(int(probe(result)['streams'][0]['nb_frames']),60)
            def frame(at):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(result),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB')
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(frame(.3),frame(1.2))).mean),6)
            self.assertIn('Author',(folder/'sources.txt').read_text())
