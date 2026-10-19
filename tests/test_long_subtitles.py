import math,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle,frame_count
from studio.subtitles import parse_subtitles,plan_scenes
from studio.director import automatic_project
from studio.effects import synthesize_sfx

class LongSubtitlesTest(unittest.TestCase):
    def test_exact_reported_vtt_time_imports_and_roundtrips_without_truncation(self):
        text='WEBVTT\n\n00:00.000 --> 00:02.000\nКнига Еноха\n\n09:59.000 --> 10:01.334\nПоследняя фраза'
        with TestClient(app) as client:
            response=client.post('/api/subtitles',files={'file':('long.vtt',text.encode(),'text/vtt')})
            self.assertEqual(response.status_code,200,response.text)
            body=response.json();self.assertEqual(body['subtitles'][-1]['end'],601.334)
            p=Project(scenes=body['scenes'],subtitles=body['subtitles'],director_version=12)
            self.assertEqual(sum(frame_count(s) for s in p.scenes),math.ceil(601.334*30))
            validated=client.post('/api/project/validate',json=p.model_dump())
            self.assertEqual(validated.status_code,200,validated.text)
            self.assertEqual(validated.json()['subtitles'][-1]['end'],601.334)
            self.assertGreater(len(p.scenes),40)
            self.assertTrue(all(1<=s.duration<=30 for s in p.scenes))

    def test_srt_and_full_hour_preserve_last_cue_and_frame_clock(self):
        for final,end in [('00:10:01,334',601.334),('01:00:00,000',3600)]:
            text=f'1\n00:00:00,000 --> 00:00:03,000\nРукопись\n\n2\n00:09:59,000 --> {final}\nПоследний текст'
            cues=parse_subtitles(text);p=automatic_project(Project(scenes=plan_scenes(cues),subtitles=cues))
            self.assertEqual(p.subtitles[-1].end,end)
            self.assertEqual(sum(frame_count(s) for s in p.scenes),math.ceil(end*30))
            self.assertLessEqual(len(p.scenes),600)
            self.assertTrue(all(s.duration<=30 for s in p.scenes))

    def test_beyond_supported_length_has_plain_russian_error(self):
        with TestClient(app) as client:
            r=client.post('/api/subtitles',files={'file':('too-long.vtt',b'WEBVTT\n\n00:00.000 --> 01:00:00.001\nText','text/vtt')})
        self.assertEqual(r.status_code,400)
        self.assertIn('60 минут',r.json()['detail'])
        self.assertNotIn('pydantic',r.text)
        self.assertNotIn('validation error',r.text)

    def test_hour_of_sound_is_sparse_and_preserves_stereo_sample_count(self):
        p=Project(scenes=[Scene(duration=30) for _ in range(120)],sound_mode='auto')
        plan=[{'time':601.334,'style':'swish','kind':'title','scene':20}]
        with tempfile.TemporaryDirectory() as temp,patch('studio.soundplan.sound_plan',return_value=plan):
            path=Path(temp)/'hour.wav';self.assertTrue(synthesize_sfx(p,path))
            with wave.open(str(path)) as f:
                self.assertEqual(f.getnframes(),3600*48000);self.assertEqual(f.getnchannels(),2)
                f.setpos(600*48000);raw=f.readframes(48000*2);self.assertNotEqual(set(raw),{0})
                f.setpos(3599*48000);self.assertEqual(set(f.readframes(48000)),{0})
            self.assertLess(path.stat().st_blocks*512,4*1024*1024)
