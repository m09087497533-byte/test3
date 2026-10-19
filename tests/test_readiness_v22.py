"""Saved long films, honest readiness, and repair of existing assignments."""
import json
import math
import struct
import wave
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from studio.app import app
from studio.models import Project,Scene,Subtitle
from studio.readiness import project_readiness
from studio.visual_fallback import complete_visuals
from studio.auto_media import arrange
from studio.playback import PlaybackJobs
from studio.render import render
from studio.media import probe


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patch=patch('studio.media.ASSETS',self.root);self.patch.start()
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.patch.stop)

    def image(self,number):
        identifier=f'{number:032x}';folder=self.root/identifier;folder.mkdir()
        Image.new('RGB',(80,45),'tan').save(folder/'source.png')
        meta=dict(id=identifier,name='Book of Enoch manuscript',kind='image',file='source.png',created_at=1)
        (folder/'meta.json').write_text(json.dumps(meta))
        return meta

    def project(self,ids,duration=2):
        return Project(director_version=12,effects_version=19,semantic_media=False,library_since=0,
            scenes=[Scene(duration=duration,asset_id=i,media_reason='Фото · Книга Еноха · из библиотеки',media_query='Book of Enoch manuscript') for i in ids],
            subtitles=[Subtitle(start=0,end=len(ids)*duration,text='Книга Еноха — старинная рукопись')])

    def test_235_saved_scenes_can_reuse_46_matching_assets_later_in_film(self):
        assets=[self.image(i+1) for i in range(46)]
        p=self.project([assets[i%46]['id'] for i in range(235)],6.5)
        before=p.model_dump()
        report=project_readiness(p)
        self.assertTrue(report['can_render'],report)
        self.assertEqual(report['ready_count'],235)
        with patch('studio.auto_media.local_assets',return_value=assets):
            self.assertEqual(complete_visuals(p).model_dump(),before)
        self.assertEqual(p.model_dump(),before)

    def test_neighbor_automatic_repeat_is_reported_but_manual_choice_is_kept(self):
        a=self.image(1);p=self.project([a['id'],a['id']])
        report=project_readiness(p)
        self.assertEqual(report['ready_count'],1)
        self.assertIn('соседней',report['scenes'][1]['reason'])
        p.scenes[1].media_reason=''
        self.assertTrue(project_readiness(p)['can_render'])

    def test_metadata_without_file_is_not_ready_and_deletion_invalidates_cache(self):
        a=self.image(1);p=self.project([a['id']])
        self.assertTrue(project_readiness(p)['can_render'])
        (self.root/a['id']/'source.png').unlink()
        report=project_readiness(p)
        self.assertEqual(report['ready_count'],0)
        self.assertIn('отсутствует',report['scenes'][0]['reason'])

    def test_empty_and_corrupt_files_are_not_counted_as_ready(self):
        a,b=self.image(1),self.image(2);p=self.project([a['id'],b['id']])
        (self.root/a['id']/'source.png').write_bytes(b'')
        (self.root/b['id']/'source.png').write_bytes(b'invalid image')
        report=project_readiness(p)
        self.assertEqual(report['ready_count'],0)
        self.assertIn('пуст',report['scenes'][0]['reason'])
        self.assertIn('не читается',report['scenes'][1]['reason'])

    def test_wrong_automatic_subject_stays_visible_as_a_problem(self):
        a=self.image(1);p=self.project([a['id']])
        path=self.root/a['id']/'meta.json'
        path.write_text(json.dumps({**a,'name':'Person writing in notebook near laptop'}))
        self.assertIn('не подтверждает',project_readiness(p)['scenes'][0]['reason'])

    def test_missing_voice_blocks_render_without_changing_visual_count(self):
        a=self.image(1);p=self.project([a['id']]);p.voice_id='f'*32
        report=project_readiness(p)
        self.assertEqual(report['ready_count'],1)
        self.assertFalse(report['can_render']);self.assertIn('Озвучка',report['audio_issues'][0])

    def test_unavailable_project_opens_for_repair_and_fails_before_queuing(self):
        p=self.project(['a'*32])
        with TestClient(app) as client,patch('studio.app.worker.submit') as export,patch('studio.app.playback_jobs.pool.submit') as playback:
            self.assertEqual(client.post('/api/project/validate',json=p.model_dump()).status_code,200)
            status=client.post('/api/project/readiness',json=p.model_dump())
            self.assertEqual(status.json()['ready_count'],0)
            for endpoint in ('/api/render','/api/playback'):
                response=client.post(endpoint,json=p.model_dump())
                self.assertEqual(response.status_code,400,response.text)
                self.assertIn('Сцена 1',response.json()['detail'])
            response=client.post('/api/playback/selection',json={'project':p.model_dump(),'scene_indices':[0]})
            self.assertEqual(response.status_code,400,response.text)
            export.assert_not_called();playback.assert_not_called()

    def test_retry_repairs_missing_assigned_id_without_replacing_valid_scene(self):
        kept,new=self.image(1),self.image(2)
        p=self.project(['a'*32,kept['id']]);p.scenes[1].media_reason=''
        before=p.model_dump()
        with patch('studio.auto_media.local_assets',return_value=[kept,new]),patch('studio.auto_media.stock_video.get_key',return_value=''),patch('studio.auto_media.image_search.search_images',return_value=[]):
            result=arrange(p,'photos',False,lambda state:None,lambda:False)
        self.assertEqual([(a['scene_index'],a['asset']['id']) for a in result['assignments']],[(0,new['id'])])
        self.assertEqual(result['outcomes'][1]['status'],'kept')
        self.assertEqual(p.model_dump(),before)

    def test_real_full_playback_and_export_keep_seven_repeated_shots_and_voice_without_ass(self):
        a,b=self.image(1),self.image(2)
        p=self.project([a['id'],b['id'],a['id'],b['id'],a['id'],b['id'],a['id']],1)
        voice='f'*32;folder=self.root/voice;folder.mkdir()
        with wave.open(str(folder/'voice.wav'),'wb') as f:
            f.setparams((1,2,8000,0,'NONE','not compressed'))
            f.writeframes(b''.join(struct.pack('<h',round(6000*math.sin(2*math.pi*440*i/8000))) for i in range(56000)))
        (folder/'meta.json').write_text(json.dumps(dict(id=voice,kind='audio',file='voice.wav',name='Control voice',has_audio=True,duration=7)))
        p.voice_id=voice;p.sound_mode='off';p.auto_music=False;p.transition='cut'
        before=p.model_dump();manager=PlaybackJobs(self.root/'previews')
        try:
            with patch('studio.render.capabilities',return_value={'ass':False}),patch('studio.auto_media.local_assets',return_value=[a,b]):
                state=manager.start(p);manager.pool.shutdown(wait=True)
                state=manager.get(state['id']);self.assertEqual(state['status'],'done',state)
                export=self.root/'export';export.mkdir()
                movie=render(p,export,lambda *args:None)
            for path in (manager.root/state['id']/'film.mp4',movie):
                streams=probe(path)['streams'];video=next(s for s in streams if s['codec_type']=='video')
                audio=next(s for s in streams if s['codec_type']=='audio')
                self.assertEqual(int(video['nb_frames']),210)
                self.assertAlmostEqual(float(video['duration']),7,places=2)
                self.assertAlmostEqual(float(audio['duration']),7,delta=.1)
            self.assertEqual(p.model_dump(),before)
        finally:manager.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
