import io
import json
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.render import render
from studio.caption_track import caption_states
from studio.media import probe
from studio.playback import PlaybackJobs
from studio.render_diagnostics import failure_report,redact
from run import reserve_port

class RenderCompatibilityTest(unittest.TestCase):
    def test_caption_track_preserves_gaps_and_overlaps_at_frame_boundaries(self):
        cues=[Subtitle(start=.2,end=.8,text='Первая'),Subtitle(start=.6,end=1.1,text='Вторая')]
        self.assertEqual(caption_states(cues,45),[(0,6,''),(6,12,'Первая'),(18,6,'Первая\nВторая'),(24,9,'Вторая'),(33,12,'')])

    def test_real_preview_without_ass_has_subtitles_and_exact_frame_count(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            project=Project(scenes=[Scene(duration=2,motion=False)],transition='cut',subtitles=[Subtitle(start=.2,end=.8,text='Субтитры без libass'),Subtitle(start=1,end=1.8,text='Вторая фраза')])
            with patch('studio.render.capabilities',return_value={'ass':False}):
                film=render(project,folder,lambda *a:None,draft=True)
            stream=probe(film)['streams'][0]
            self.assertEqual((stream['width'],stream['height'],int(stream['nb_frames'])),(960,540,60))
            def white_count(timepoint):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(timepoint),'-i',str(film),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                crop=Image.open(io.BytesIO(raw)).convert('RGB').crop((80,465,880,530))
                return sum(1 for pixel in crop.getdata() if min(pixel)>220)
            self.assertGreater(white_count(.4),80)
            self.assertLess(white_count(.1),15)
            self.assertLess(white_count(.9),15)
            self.assertGreater(white_count(1.2),80)
            subprocess.run(['ffmpeg','-v','error','-i',str(film),'-f','null','-'],check=True)

    def test_error_report_preserves_ffmpeg_reason_and_redacts_paths_and_urls(self):
        error=subprocess.CalledProcessError(1,['ffmpeg'],stderr="No such filter: 'ass' /Users/private-name/project/source https://example.test/?key=SECRET")
        report=failure_report(error,'Субтитры',90)
        self.assertIn('libass',report['message'])
        self.assertIn('No such filter',report['detail'])
        self.assertNotIn('private-name',report['detail']);self.assertNotIn('SECRET',report['detail'])

    def test_preview_failure_is_reported_and_downloadable(self):
        with tempfile.TemporaryDirectory() as temp:
            manager=PlaybackJobs(Path(temp))
            try:
                def failing(project,folder,progress,**kwargs):
                    progress(90,'Субтитры и финальная сборка')
                    raise subprocess.CalledProcessError(1,['ffmpeg'],stderr="No such filter: 'ass'")
                with patch('studio.app.playback_jobs',manager),patch('studio.playback.render',side_effect=failing),TestClient(app) as client:
                    state=client.post('/api/playback',json=Project(scenes=[Scene()]).model_dump()).json();identifier=state['id']
                    for _ in range(100):
                        state=client.get('/api/playback/'+identifier).json()
                        if state['status']=='error':break
                        time.sleep(.01)
                    self.assertEqual(state['status'],'error');self.assertEqual(state['diagnostic']['stage'],'Субтитры и финальная сборка')
                    report=client.get('/api/playback/'+identifier+'/diagnostic')
                    self.assertEqual(report.status_code,200);self.assertIn('No such filter',report.text)
                    self.assertIn('kadr-playback-error.txt',report.headers['content-disposition'])
            finally:manager.pool.shutdown(wait=True)

    def test_occupied_port_is_detected_before_opening_browser(self):
        listener=reserve_port(0)
        try:
            port=listener.getsockname()[1]
            with self.assertRaises(OSError):reserve_port(port)
        finally:listener.close()
