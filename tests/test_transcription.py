import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import io
import math
import struct
import wave
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from studio.app import app
from studio import transcription as speech
from studio.models import Project, Scene, Subtitle


class TranscriptionTests(unittest.TestCase):
    def factory(self, segments, language='ru'):
        class Model:
            def __init__(self, *args, **kwargs):
                self.kwargs=kwargs
            def transcribe(self, path, **kwargs):
                return iter(segments), NS(language=language)
        return Model

    def test_recognition_uses_real_segment_times_and_builds_directed_scenes(self):
        segments=[NS(start=1.234,end=3.456,text='Ракета летит к Луне.',words=None)]
        result=speech.recognize(Path('voice.wav'),'tiny','ru',9,lambda *a:None,self.factory(segments))
        self.assertEqual(result['subtitles'],[dict(start=1.234,end=3.456,text='Ракета летит к Луне.')])
        self.assertAlmostEqual(sum(s['duration'] for s in result['scenes']),9)
        self.assertEqual(result['director_version'],12)

    def test_word_groups_keep_word_boundaries(self):
        words=[NS(start=i,end=i+.8,word=' слово') for i in range(12)]
        result=speech.recognize(Path('a'),'small','auto',12,lambda *a:None,
                               self.factory([NS(words=words,start=0,end=12,text='ignored')]))
        self.assertEqual(len(result['subtitles']),2)
        self.assertEqual(result['subtitles'][1]['start'],7)
        self.assertEqual(result['subtitles'][-1]['end'],11.8)

    def test_recognition_clips_to_voice_and_drops_empty(self):
        segments=[NS(start=-.1,end=4,text='Текст',words=[]),NS(start=4,end=5,text='  ',words=[])]
        result=speech.recognize(Path('a'),'base','ru',3,lambda *a:None,self.factory(segments))
        self.assertEqual(result['subtitles'][0],dict(start=0.,end=3.,text='Текст'))

    def test_empty_audio_is_actionable_error(self):
        with self.assertRaisesRegex(ValueError,'Речь не найдена'):
            speech.recognize(Path('a'),'base','ru',3,lambda *a:None,self.factory([]))

    def test_too_long_phrase_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError,'длинную фразу'):
            speech.recognize(Path('a'),'base','ru',3,lambda *a:None,
                             self.factory([NS(start=0,end=2,text='x'*501,words=None)]))

    def test_recognizer_is_cpu_int8_and_honors_language(self):
        options={}
        class Model:
            def __init__(self, name, **kwargs):options.update(kwargs,name=name)
            def transcribe(self,path,**kwargs):
                options.update(kwargs)
                return iter([NS(start=0,end=1,text='Hello',words=[])]),NS(language='en')
        speech.recognize(Path('a'),'tiny','auto',3,lambda *a:None,Model)
        self.assertEqual(options['device'],'cpu');self.assertEqual(options['compute_type'],'int8')
        self.assertIsNone(options['language']);self.assertTrue(options['word_timestamps'])

    def test_download_and_proxy_errors_are_understandable(self):
        import httpx
        for exc in [httpx.ProxyError('403 Forbidden'),ConnectionError('network failure'),RuntimeError('download failed')]:
            self.assertIn('загрузить модель',speech.error_message(exc))

    def test_memory_error_suggests_smaller_model(self):
        self.assertIn('памяти',speech.error_message(MemoryError('memory')))

    def test_request_rejects_path_and_unbounded_model(self):
        client=TestClient(app)
        for payload in [dict(voice_id='../a'),dict(voice_id='a'*32,model='../../model'),dict(voice_id='a'*32,language='xx')]:
            self.assertEqual(client.post('/api/transcription',json=payload).status_code,422)

    def test_uninstalled_recognizer_keeps_app_usable(self):
        client=TestClient(app)
        with patch.object(speech,'available',return_value=False),patch.object(speech,'asset_info',return_value=dict(has_audio=True,duration=5)):
            response=client.post('/api/transcription',json=dict(voice_id='a'*32))
        self.assertEqual(response.status_code,400);self.assertIn('не установлено',response.json()['detail'])
        self.assertEqual(client.get('/').status_code,200)

    def test_restart_marks_unfinished_task_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(speech,'TASKS',Path(tmp)):
            folder=Path(tmp)/('a'*32);folder.mkdir()
            speech.write_state(folder,dict(id='a'*32,status='running'))
            self.assertEqual(speech.status('a'*32)['status'],'interrupted')

    def test_cancel_cannot_be_overwritten_by_late_worker_result(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(speech,'TASKS',Path(tmp)):
            folder=Path(tmp)/('a'*32);folder.mkdir()
            speech.write_state(folder,dict(id='a'*32,status='running'))
            self.assertEqual(speech.cancel('a'*32)['status'],'cancelled')
            (folder/'worker.json').write_text(json.dumps(dict(status='done',result={'subtitles':[]})))
            self.assertEqual(speech.status('a'*32)['status'],'cancelled')

    def test_invalid_and_missing_jobs(self):
        client=TestClient(app)
        self.assertEqual(client.get('/api/transcription/not-an-id').status_code,404)
        self.assertEqual(client.post('/api/transcription/'+('f'*32)+'/cancel').status_code,404)

    def test_voice_duration_limit_checked_before_model_download(self):
        with patch.object(speech,'asset_info',return_value=dict(has_audio=True,duration=3601)):
            with self.assertRaisesRegex(ValueError,'60 минут'):
                speech.start(speech.TranscriptionRequest(voice_id='a'*32))

    def test_transcribed_project_renders_complete_video_with_voice(self):
        from PIL import Image
        from studio.render import render
        from studio.media import probe
        client=TestClient(app)
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','not compressed'))
            wav.writeframes(b''.join(struct.pack('<h',round(4000*math.sin(i*2*math.pi*440/16000))) for i in range(8*16000)))
        voice=client.post('/api/assets',files={'file':('voice.wav',buffer.getvalue(),'audio/wav')}).json()
        image=io.BytesIO();Image.new('RGB',(640,360),'#27475a').save(image,'PNG')
        picture=client.post('/api/assets',files={'file':('space.png',image.getvalue(),'image/png')}).json()
        result=speech.recognize(Path('unused'),'tiny','ru',8,lambda *a:None,
                               self.factory([NS(start=1.234,end=6.345,text='В 1773 году открыли звезду.',words=[])]))
        p=Project(scenes=result['scenes'],subtitles=result['subtitles'],voice_id=voice['id'],director_version=12)
        for scene in p.scenes:scene.asset_id=picture['id']
        with tempfile.TemporaryDirectory() as tmp:
            movie=render(p,Path(tmp),lambda *a:None)
            info=probe(movie);video=next(s for s in info['streams'] if s['codec_type']=='video')
            audio=next(s for s in info['streams'] if s['codec_type']=='audio')
            self.assertEqual(int(video['nb_frames']),240)
            self.assertAlmostEqual(float(video['duration']),8,delta=.04)
            self.assertAlmostEqual(float(audio['duration']),8,delta=.1)
            decoded=subprocess.run(['ffmpeg','-v','error','-i',str(movie),'-vn','-ac','1','-ar','8000','-f','s16le','-'],capture_output=True,check=True)
            samples=struct.unpack('<'+'h'*(len(decoded.stdout)//2),decoded.stdout)
            self.assertGreater(math.sqrt(sum(x*x for x in samples)/len(samples)),100)


class TranscriptEditingTests(unittest.TestCase):
    def project(self):
        return Project(voice_id='a'*32,music_id='b'*32,scenes=[Scene(duration=5,asset_id='c'*32,offset=12,media_reason='Вручную',title='Старый текст')],
                       subtitles=[Subtitle(start=1.234,end=3.456,text='В 1821 году нашли рукопись.')])

    def test_edit_preserves_times_media_keys_and_voice(self):
        p=self.project();client=TestClient(app)
        response=client.post('/api/project/transcript',json=dict(project=p.model_dump(),texts=['В 1773 году нашли рукопись.']))
        self.assertEqual(response.status_code,200,response.text)
        result=Project.model_validate(response.json())
        self.assertEqual(result.voice_id,p.voice_id);self.assertEqual(result.music_id,p.music_id)
        self.assertEqual(result.scenes[0].asset_id,p.scenes[0].asset_id);self.assertEqual(result.scenes[0].offset,12)
        self.assertEqual(result.subtitles[0].start,1.234);self.assertEqual(result.subtitles[0].end,3.456)
        self.assertIn('1773',result.subtitles[0].text)
        self.assertTrue(any('1773' in e.text for e in result.scenes[0].text_events))
        self.assertFalse(any('1821' in e.text for e in result.scenes[0].text_events))

    def test_invalid_edits_do_not_mutate_original(self):
        p=self.project();client=TestClient(app)
        for texts in [[],['  '],['a'*501],['one','two']]:
            r=client.post('/api/project/transcript',json=dict(project=p.model_dump(),texts=texts))
            self.assertEqual(r.status_code,400,r.text)
        self.assertIn('1821',p.subtitles[0].text)

