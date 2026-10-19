import io
import json
import math
import os
import struct
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path

test_data = tempfile.TemporaryDirectory(prefix='kadr-tests-')
os.environ['STUDIO_DATA_DIR'] = test_data.name

from fastapi.testclient import TestClient
from PIL import Image

from studio.app import app, worker
from studio.media import JOBS, probe
from studio.models import Project, Scene, Subtitle
from studio.render import overlay, render
from studio.effects import boundary_style, transition_frames
from studio.subtitles import ass_document, parse_srt, parse_subtitles, plan_scenes


def teardown_module():
    worker.shutdown(wait=True)
    test_data.cleanup()


def tearDownModule():
    teardown_module()


def sound(seconds=4):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(b''.join(struct.pack('<h', round(6000*math.sin(2*math.pi*440*i/16000)))
                                 for i in range(seconds*16000)))
    return buffer.getvalue()


class SubtitlesTest(unittest.TestCase):
    def test_transition_overrides_and_short_scenes(self):
        project = Project(scenes=[Scene(duration=1), Scene(duration=1, transition_out='cut'), Scene(duration=2)], transition='mixed')
        self.assertEqual(boundary_style(project, 0), 'dissolve')
        self.assertEqual(transition_frames(project, 0), 10)
        self.assertEqual(transition_frames(project, 1), 0)
        self.assertEqual(transition_frames(project, 2), 0)

    def test_vtt_hours_and_millisecond_precision(self):
        cues = parse_subtitles('WEBVTT\n\n00:00:24.309 --> 00:00:24.800\nраньше.\n\n00:00:32.848 --> 00:00:35.600\nрезультат рядом')
        self.assertEqual(cues[0].start, 24.309)
        self.assertEqual(cues[1].start, 32.848)
        self.assertEqual(cues[1].end, 35.6)

    def test_vtt_metadata_tags_and_identifiers(self):
        cues = parse_subtitles('\ufeffWEBVTT\nKind: captions\n\nNOTE a comment\nnot a cue\n\nSTYLE\n::cue {color:red}\n\ncue-name\n00:01.125 --> 00:02.500 align:start\n<v Narrator><b>Текст</b> &amp; <00:01.500>слово\nвторая строка')
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, 'Текст & слово\nвторая строка')
        self.assertEqual(cues[0].start, 1.125)

    def test_empty_or_invalid_vtt(self):
        for source in ['WEBVTT', 'WEBVTT\n\nNOTE only a comment', 'WEBVTT\n\n00:01.000 --> 00:00.500\nText']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_subtitles(source)

    def test_multiline_bom_and_gaps(self):
        cues = parse_srt('\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\nПервая\r\nфраза\r\n\r\n2\r\n00:00:11,000 --> 00:00:12,200\r\n<b>Вторая</b>')
        self.assertEqual(cues[0].text, 'Первая\nфраза')
        self.assertEqual(cues[1].text, 'Вторая')
        scenes = plan_scenes(cues, 14.111)
        self.assertAlmostEqual(sum(s.duration for s in scenes), 14.1333333, places=5)
        self.assertEqual(cues[1].start, 11)
        self.assertLessEqual(scenes[0].duration, 3.5)
        self.assertTrue(all(s.duration <= 5 for s in scenes))

    def test_invalid_timing(self):
        for data in ['', 'Hello', '1\n00:00:04,000 --> 00:00:03,000\nText',
                     '1\n00:61:00,000 --> 00:62:00,000\nText']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_srt(data)

    def test_long_silence(self):
        cues = [Subtitle(start=90, end=91, text='После паузы')]
        scenes = plan_scenes(cues)
        self.assertAlmostEqual(sum(s.duration for s in scenes), 91)
        self.assertTrue(all(1 <= s.duration <= 30 for s in scenes))

    def test_ass_treats_overrides_as_text(self):
        text = ass_document([Subtitle(start=0, end=1, text=r'{\pos(1,1)}Текст')], 1280, 720)
        self.assertNotIn(r'{\pos', text)
        self.assertIn('Текст', text)

    def test_prevents_silent_caption_truncation(self):
        with self.assertRaises(ValueError):
            Project(scenes=[Scene(duration=1)], subtitles=[Subtitle(start=0, end=3, text='Фраза')])

    def test_overlay_all_export_sizes(self):
        for size in [(960, 540), (1280, 720), (1920, 1080)]:
            for template in ['cinema', 'archive', 'statement', 'quote', 'thesis']:
                with self.subTest(size=size, template=template):
                    self.assertEqual(overlay(Scene(template=template, title='Проверка'), size).size, size)


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def upload(self, name, content, mime):
        response = self.client.post('/api/assets', files={'file': (name, content, mime)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_unreadable_upload_is_rejected(self):
        response = self.client.post('/api/assets', files={'file': ('fake.mp4', b'not a video', 'video/mp4')})
        self.assertEqual(response.status_code, 400)

    def test_five_transitions_keep_timing_and_stay_silent_without_title_sounds(self):
        folder = Path(test_data.name)/'effects'
        folder.mkdir()
        project = Project(scenes=[Scene(duration=1, title='Сцена', motion_style=m) for m in
                                 ['push','pull','left','right','push','pull']],
                          transition='mixed',transition_sounds=True,animate_titles=True,
                          subtitles=[Subtitle(start=4.9,end=5.9,text='После переходов')])
        result = render(project,folder,lambda *args: None)
        info = probe(result)
        self.assertEqual(int(next(s for s in info['streams'] if s['codec_type']=='video')['nb_frames']),180)
        self.assertAlmostEqual(float(info['format']['duration']),6,delta=.05)
        self.assertFalse(any(s['codec_type']=='audio' for s in info['streams']),
                         'Without voice, music or text sounds, transitions must not create an audio track')
        frame = subprocess.run(['ffmpeg','-v','error','-ss','3.166667','-i',str(result),'-frames:v','1',
             '-threads','2','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True)
        screen = Image.open(io.BytesIO(frame.stdout)).convert('RGB')
        center = screen.crop((400,200,880,450)).resize((1,1)).getpixel((0,0))
        self.assertGreater(min(center),200,'White-flash transition was not rendered')
        run = subprocess.run(['ffmpeg','-v','error','-i',str(result),'-f','null','-'],capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_unknown_or_invalid_asset(self):
        response = self.client.post('/api/render', json={'scenes': [{'asset_id': 'a'*32}]})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/render', json={'scenes': [{'asset_id': '../../etc/passwd'}]})
        self.assertEqual(response.status_code, 422)

    def test_voice_srt_images_video_and_export(self):
        voice = self.upload('../../voice.wav', sound(4), 'audio/wav')
        music = self.upload('music.wav', sound(2), 'audio/wav')
        image_bytes = io.BytesIO()
        Image.new('RGB', (640, 360), '#36584c').save(image_bytes, 'PNG')
        image = self.upload('still.png', image_bytes.getvalue(), 'image/png')
        video_path = Path(test_data.name) / 'clip.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30',
                        '-t','0.5','-c:v','libx264','-threads','2',str(video_path)], check=True)
        video = self.upload('clip.mp4', video_path.read_bytes(), 'video/mp4')
        srt = '1\n00:00:00,200 --> 00:00:01,500\nТест озвучки\n\n2\n00:00:02,000 --> 00:00:03,800\nСила слова'
        response = self.client.post('/api/subtitles', files={'file':('sample.srt',srt.encode(),'text/plain')},data={'voice_id':voice['id']})
        self.assertEqual(response.status_code, 200, response.text)
        imported = response.json()
        self.assertAlmostEqual(sum(s['duration'] for s in imported['scenes']), 4)
        project = Project(voice_id=voice['id'], music_id=music['id'], transition='cut', subtitles=imported['subtitles'], scenes=[
            Scene(duration=1, asset_id=image['id']),
            Scene(duration=1, template='archive', asset_id=video['id'], offset=.1, title='Архив'),
            Scene(duration=1, template='statement', title='СЛОВО'),
            Scene(duration=1, template='quote', title='История', caption='Проверка экспорта')
        ])
        preview_response = self.client.post('/api/preview', json=project.scenes[1].model_dump())
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(preview_response.content)).size, (960, 540))
        response = self.client.post('/api/render', json=project.model_dump())
        self.assertEqual(response.status_code, 202, response.text)
        identifier = response.json()['id']
        # Only one renderer is admitted at once.
        self.assertEqual(self.client.post('/api/render', json=project.model_dump()).status_code, 409)
        deadline = time.monotonic()+120
        while time.monotonic() < deadline:
            state = self.client.get(f'/api/jobs/{identifier}').json()
            if state['status'] in ('done', 'error'):
                break
            time.sleep(.25)
        self.assertEqual(state['status'], 'done', state)
        result = JOBS / identifier / 'film.mp4'
        info = probe(result)
        video_stream = next(s for s in info['streams'] if s['codec_type']=='video')
        self.assertEqual((video_stream['width'], video_stream['height']), (1280,720))
        self.assertEqual(int(video_stream['nb_frames']), 120)
        self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
        self.assertAlmostEqual(float(info['format']['duration']), 4, delta=.1)
        decoded = subprocess.run(['ffmpeg','-v','error','-i',str(result),'-f','null','-'],capture_output=True)
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        frame = subprocess.run(['ffmpeg','-v','error','-ss','0.5','-i',str(result),'-frames:v','1',
            '-threads','2','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True)
        screen = Image.open(io.BytesIO(frame.stdout)).convert('RGB')
        region = screen.crop((100,610,1180,710))
        white_pixels = sum(1 for r,g,b in region.getdata() if min(r,g,b)>200)
        self.assertGreater(white_pixels, 100, 'Burned-in subtitle not visible')
        pcm = subprocess.run(['ffmpeg','-v','error','-i',str(result),'-vn','-ac','1','-ar','8000','-f','s16le','-'],capture_output=True,check=True).stdout
        samples = struct.unpack('<'+'h'*(len(pcm)//2),pcm)
        rms = math.sqrt(sum(x*x for x in samples)/len(samples))
        self.assertGreater(rms, 1000, 'Export unexpectedly silent')
        tail = samples[-3200:]
        middle = samples[20000:23200]
        tail_rms = math.sqrt(sum(x*x for x in tail)/len(tail))
        middle_rms = math.sqrt(sum(x*x for x in middle)/len(middle))
        self.assertGreater(tail_rms, middle_rms*.85, 'Narration ending must not fade out')
        download = self.client.get(f'/api/jobs/{identifier}/video')
        self.assertEqual(download.status_code, 200)
        sources = self.client.get(f'/api/jobs/{identifier}/sources')
        self.assertEqual(sources.status_code, 200)
        self.assertIn('Источники изображений', sources.text)
        self.assertEqual(download.headers['content-type'], 'video/mp4')
        partial = self.client.get(f'/api/jobs/{identifier}/video',headers={'Range':'bytes=0-99'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(len(partial.content), 100)
