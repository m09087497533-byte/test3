import io
import json
import math
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.media import probe
from studio.playback import PlaybackJobs,PlaybackBusy,fingerprint

class PlaybackTest(unittest.TestCase):
    def test_fingerprint_tracks_movie_changes_and_ignores_editor_metadata(self):
        p=Project(scenes=[Scene(title='Глава')])
        original=fingerprint(p)
        p.name='Другое имя';p.resolution='1080p';p.auto_media_mode='photos';p.scenes[0].media_reason='Фото · тема'
        self.assertEqual(fingerprint(p),original)
        p.scenes[0].title='Новая глава';self.assertNotEqual(fingerprint(p),original)
        p.scenes[0].title='Глава';p.music_volume=.6;self.assertNotEqual(fingerprint(p),original)

    def test_inflight_and_ready_requests_are_cached_and_competing_job_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            manager=PlaybackJobs(Path(temp));started=threading.Event();release=threading.Event()
            def fake_render(project,folder,progress,*,draft):
                self.assertTrue(draft);started.set();release.wait(5);(folder/'film.mp4').write_bytes(b'test')
            try:
                with patch('studio.playback.render',side_effect=fake_render) as renderer:
                    p=Project(scenes=[Scene(title='Глава')]);first=manager.start(p);self.assertTrue(started.wait(2))
                    self.assertEqual(manager.start(p)['id'],first['id'])
                    with self.assertRaises(PlaybackBusy):manager.start(Project(scenes=[Scene(title='Другая')]))
                    release.set()
                    for _ in range(100):
                        if manager.get(first['id'])['status']=='done':break
                        time.sleep(.02)
                    self.assertEqual(manager.get(first['id'])['status'],'done')
                    p.name='Новое имя';p.resolution='1080p'
                    self.assertEqual(manager.start(p)['id'],first['id']);self.assertEqual(renderer.call_count,1)
                restored=PlaybackJobs(Path(temp));self.assertEqual(restored.get(first['id'])['status'],'done');restored.pool.shutdown()
            finally:release.set();manager.pool.shutdown(wait=True)

    def test_real_preview_contains_video_audio_subtitles_and_supports_seeking(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';assets.mkdir();manager=PlaybackJobs(root/'previews')
            wav=io.BytesIO()
            with wave.open(wav,'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'))
                f.writeframes(b''.join(struct.pack('<h',round(6000*math.sin(i*2*math.pi*250/16000))) for i in range(48000)))
            image=io.BytesIO();Image.new('RGB',(640,360),'#36584c').save(image,'PNG')
            clip=root/'clip.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','0.5','-c:v','libx264','-threads','2',str(clip)],check=True)
            try:
                with patch('studio.app.playback_jobs',manager),patch('studio.app.ASSETS',assets),patch('studio.media.ASSETS',assets),TestClient(app) as client:
                    def upload(name,data,mime):
                        response=client.post('/api/assets',files={'file':(name,data,mime)})
                        self.assertEqual(response.status_code,200,response.text);return response.json()['id']
                    voice=upload('voice.wav',wav.getvalue(),'audio/wav');photo=upload('photo.png',image.getvalue(),'image/png');video=upload('clip.mp4',clip.read_bytes(),'video/mp4')
                    p=Project(resolution='1080p',voice_id=voice,transition='mixed',sound_mode='manual',transition_sounds=True,
                              scenes=[Scene(duration=1,asset_id=photo,title='Первая'),Scene(duration=1,asset_id=video,offset=.1),Scene(duration=1,template='thesis',title='СЛОВО',title_effect='scroll')],
                              subtitles=[Subtitle(start=.1,end=.9,text='Проверка субтитров'),Subtitle(start=1.1,end=2.8,text='Следующий кадр')])
                    response=client.post('/api/playback',json=p.model_dump());self.assertEqual(response.status_code,202,response.text);identifier=response.json()['id']
                    deadline=time.monotonic()+60
                    while time.monotonic()<deadline:
                        state=client.get('/api/playback/'+identifier).json()
                        if state['status'] in ('done','error'):break
                        time.sleep(.05)
                    self.assertEqual(state['status'],'done',state)
                    film=manager.root/identifier/'film.mp4';info=probe(film)
                    stream=next(s for s in info['streams'] if s['codec_type']=='video')
                    self.assertEqual((stream['width'],stream['height'],int(stream['nb_frames'])),(960,540,90))
                    self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
                    self.assertAlmostEqual(float(info['format']['duration']),3,delta=.05)
                    decoded=subprocess.run(['ffmpeg','-v','error','-i',str(film),'-f','null','-'],capture_output=True);self.assertEqual(decoded.returncode,0,decoded.stderr)
                    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(film),'-vn','-ac','1','-ar','8000','-f','s16le','-']);samples=struct.unpack('<'+'h'*(len(pcm)//2),pcm)
                    self.assertGreater(math.sqrt(sum(x*x for x in samples)/len(samples)),1000)
                    raw=subprocess.check_output(['ffmpeg','-v','error','-ss','0.5','-i',str(film),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                    im=Image.open(io.BytesIO(raw)).convert('RGB').crop((60,450,900,530))
                    self.assertGreater(sum(1 for rgb in im.getdata() if min(rgb)>200),40)
                    response=client.get('/api/playback/'+identifier+'/video',headers={'Range':'bytes=0-99'})
                    self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
                    self.assertIn('inline',response.headers['content-disposition'])
                    self.assertEqual(client.post('/api/playback',json=p.model_dump()).json()['id'],identifier)
                    self.assertEqual(client.get('/api/playback/unknown/video').status_code,404)
            finally:manager.pool.shutdown(wait=True)

    def test_missing_assets_are_rejected_before_starting_a_job(self):
        with TestClient(app) as client:
            p=Project(scenes=[Scene(asset_id='0'*32)])
            self.assertEqual(client.post('/api/playback',json=p.model_dump()).status_code,400)
