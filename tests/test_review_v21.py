import io
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

from PIL import Image
from studio.models import Project,Scene,Subtitle
from studio import auto_media as auto
from studio.color_transitions import apply_color_transitions
from studio.reference_effects import apply_reference_effects
from studio.preview_selection import select_preview
from studio.playback import PlaybackJobs
from studio.media import probe
from studio.render import render
from studio.soundplan import sound_plan
from tests.test_auto_media import asset


class StockFailoverTests(unittest.TestCase):
    def project(self):return Project(director_version=12,scenes=[Scene(duration=5,title='Книга Еноха')])

    def test_rate_limited_commons_continues_with_alternative_stock_video_queries(self):
        item=dict(token='clip',provider='pexels',kind='video',title='Antique old book pages turning parchment manuscript',source_url='https://example.test/clip')
        def search(provider,query):return [item] if query=='old book pages' else []
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',side_effect=lambda p:'test' if p=='pexels' else ''),\
             patch.object(auto.image_search,'search_images',side_effect=ValueError('HTTP 429')) as commons,\
             patch.object(auto.stock_video,'search_photos',return_value=[]),patch.object(auto.stock_video,'search_videos',side_effect=search) as stock,\
             patch.object(auto.stock_video,'import_video',return_value=asset('a'*32,'video',item['title'])):
            result=auto.arrange(self.project(),'mixed',False,lambda x:None,lambda:False)
        self.assertEqual(result['outcomes'][0]['status'],'assigned')
        self.assertEqual(result['assignments'][0]['asset']['kind'],'video')
        self.assertTrue(any(c.args[1]=='old book pages' for c in stock.call_args_list))
        self.assertEqual(commons.call_count,1)
        self.assertEqual(result['sources']['connected'],['pexels'])

    def test_found_but_unreadable_file_does_not_prevent_a_second_query(self):
        bad=dict(token='bad',title='Book of Enoch manuscript',source_url='https://example.test/bad')
        good=dict(token='good',title='Book of Enoch manuscript second page',source_url='https://example.test/good')
        def search(query):return [good] if query=='Enoch manuscript' else [bad]
        def download(token):
            if token=='bad':raise ValueError('Слишком большое изображение')
            return asset('a'*32,'image',good['title'])
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',side_effect=search) as searcher,patch.object(auto.image_search,'import_image',side_effect=download):
            result=auto.arrange(self.project(),'photos',False,lambda x:None,lambda:False)
        self.assertEqual(len(result['assignments']),1)
        self.assertIn('Enoch manuscript',[c.args[0] for c in searcher.call_args_list])

    def test_stock_download_rate_limit_does_not_disable_another_stock(self):
        def search(provider,query):return [dict(token=provider,kind='image',provider=provider,title='Book of Enoch manuscript',source_url='https://example.test/'+provider)]
        def download(token):
            if token=='pexels':raise ValueError('HTTP 429')
            return asset('a'*32,'image','Book of Enoch manuscript')
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value='test'),\
             patch.object(auto.image_search,'search_images',return_value=[]),patch.object(auto.stock_video,'search_photos',side_effect=search),\
             patch.object(auto.stock_video,'import_photo',side_effect=download) as importer:
            result=auto.arrange(self.project(),'photos',False,lambda x:None,lambda:False)
        self.assertEqual([c.args[0] for c in importer.call_args_list],['pexels','pixabay'])
        self.assertEqual(result['outcomes'][0]['status'],'assigned')

    def test_missing_keys_explained_without_claiming_stock_was_searched(self):
        with patch.object(auto,'local_assets',return_value=[]),patch.object(auto.stock_video,'get_key',return_value=''),\
             patch.object(auto.image_search,'search_images',side_effect=ValueError('HTTP 429')),patch.object(auto.stock_video,'search_photos') as stock:
            result=auto.arrange(self.project(),'mixed',False,lambda x:None,lambda:False)
        self.assertIn('подключите Pexels / Pixabay',result['outcomes'][0]['reason'])
        self.assertIn('посмотреть найденные сцены',result['outcomes'][0]['reason'])
        self.assertEqual(result['sources']['connected'],[]);stock.assert_not_called()


class PartialPreviewTests(unittest.TestCase):
    def project(self):
        return Project(scenes=[Scene(duration=2,asset_id='a'*32),Scene(duration=2),Scene(duration=2,asset_id='b'*32)],
                       subtitles=[Subtitle(start=.25,end=1.75,text='Первая фраза'),Subtitle(start=2.25,end=3.75,text='Нет кадра'),Subtitle(start=4.25,end=5.75,text='Третья фраза')])

    def test_selection_rebases_only_preview_clocks_and_preserves_original(self):
        original=self.project();before=original.model_dump()
        selected,segments,events=select_preview(original)
        self.assertEqual([s['scene_index'] for s in segments],[0,2])
        self.assertEqual([c.model_dump() for c in selected.subtitles],[dict(start=.25,end=1.75,text='Первая фраза'),dict(start=2.25,end=3.75,text='Третья фраза')])
        self.assertEqual(selected.scenes[0].transition_out,'cut')
        self.assertEqual(original.model_dump(),before)

    def test_consecutive_selection_does_not_split_a_subtitle_at_every_shot(self):
        original=self.project();original.scenes[1].asset_id='c'*32
        original.subtitles=[Subtitle(start=.2,end=5.8,text='Одна непрерывная фраза')]
        selected,_,_=select_preview(original)
        self.assertEqual(selected.subtitles,original.subtitles)

    def test_invalid_or_empty_selections_fail_clearly(self):
        for indices in ([],[-1],[3],[2,0],[0,0],[1]):
            with self.subTest(indices=indices),self.assertRaises(ValueError):select_preview(self.project(),indices)
        with self.assertRaisesRegex(ValueError,'Пока нет сцен'):select_preview(Project(scenes=[Scene()]))

    def test_real_selected_video_uses_third_scene_voice_after_a_missing_second_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';assets.mkdir();manager=PlaybackJobs(root/'playbacks')
            for identifier,color in [('a'*32,'#768b76'),('b'*32,'#aa9977')]:
                folder=assets/identifier;folder.mkdir();Image.new('RGB',(320,180),color).save(folder/'image.png')
                (folder/'meta.json').write_text(json.dumps(dict(id=identifier,kind='image',file='image.png',name='Test frame')))
            voice_folder=assets/('c'*32);voice_folder.mkdir()
            with wave.open(str(voice_folder/'voice.wav'),'wb') as f:
                f.setparams((1,2,8000,0,'NONE','not compressed'))
                f.writeframes(b''.join(struct.pack('<h',round(6000*math.sin(2*math.pi*(440,880,1320)[i//16000]*i/8000))) for i in range(48000)))
            (voice_folder/'meta.json').write_text(json.dumps(dict(id='c'*32,kind='audio',file='voice.wav',name='Control voice',duration=6,has_audio=True)))
            project=self.project();project.voice_id='c'*32;project.sound_mode='off';project.show_subtitles=False
            project.director_version=12
            for scene in project.scenes:
                if scene.asset_id:scene.media_reason='Фото · старое автоматическое назначение'
            before=project.model_dump()
            try:
                with patch('studio.media.ASSETS',assets):
                    state=manager.start(project,selection=True)
                    manager.pool.shutdown(wait=True)
                state=manager.get(state['id']);self.assertEqual(state['status'],'done',state)
                movie=manager.root/state['id']/'film.mp4';info=probe(movie)
                video=next(s for s in info['streams'] if s['codec_type']=='video')
                self.assertEqual(int(video['nb_frames']),120);self.assertAlmostEqual(float(video['duration']),4)
                raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(movie),'-vn','-ac','1','-ar','8000','-f','s16le','-'])
                pcm=struct.unpack('<'+'h'*(len(raw)//2),raw)
                def power(frequency,at):
                    samples=pcm[round(at*8000):round((at+.5)*8000)]
                    return abs(sum(value*complex(math.cos(2*math.pi*frequency*i/8000),math.sin(2*math.pi*frequency*i/8000)) for i,value in enumerate(samples)))
                self.assertGreater(power(440,.5),power(1320,.5)*10)
                self.assertGreater(power(1320,2.5),power(880,2.5)*10)
                self.assertEqual(project.model_dump(),before)
            finally:manager.pool.shutdown(wait=True)


class ColorTransitionTests(unittest.TestCase):
    def test_document_only_story_has_visible_sparse_colors_and_preserved_clocks(self):
        original=Project(scenes=[Scene(duration=6,asset_id=f'{i+1:032x}') for i in range(8)],voice_id='f'*32,
                         subtitles=[Subtitle(start=i*6,end=(i+1)*6,text='Строки старинной рукописи') for i in range(8)])
        result=apply_reference_effects(original)
        colors=[s for s in result.scenes[:-1] if s.transition_out in ('light_leak','color_wash')]
        self.assertGreaterEqual(len(colors),2)
        self.assertEqual({s.transition_out for s in colors},{'light_leak','color_wash'})
        self.assertTrue(all(s.transition_duration>=.6 for s in colors))
        self.assertEqual(result.subtitles,original.subtitles);self.assertEqual(result.voice_id,original.voice_id)
        self.assertEqual([(s.duration,s.asset_id,s.offset) for s in result.scenes],[(s.duration,s.asset_id,s.offset) for s in original.scenes])
        self.assertEqual(apply_color_transitions(result),result)
        self.assertFalse(sound_plan(result))

    def test_encoded_transitions_are_colored_without_transition_audio(self):
        project=Project(scenes=[Scene(duration=2,transition_out='light_leak',transition_duration=.6),
                                Scene(duration=2,transition_out='color_wash',transition_duration=.7),Scene(duration=2)],
                        sound_mode='auto',transition_sounds=True)
        with tempfile.TemporaryDirectory() as tmp,patch('studio.render.source_still',return_value=Image.new('RGB',(960,540),'#777777')):
            movie=render(project,Path(tmp),lambda *a:None,draft=True)
            info=probe(movie);self.assertFalse(any(s['codec_type']=='audio' for s in info['streams']))
            self.assertEqual(int(info['streams'][0]['nb_frames']),180)
            for time in (2.3,4.3):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                frame=Image.open(io.BytesIO(raw)).convert('RGB')
                colored=sum(max(rgb)-min(rgb)>45 for rgb in frame.getdata())
                self.assertGreater(colored,frame.width*frame.height*.2)
