"""Regressions from the user's 49s audio / 16.5s picture export and saved project."""
import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from studio.media import probe
from studio.models import Project,Scene,Subtitle
from studio.effects import assemble
from studio.auto_media import scene_context,relevant_candidate
from studio.visual_fallback import complete_visuals

class UserFilmFailureTest(unittest.TestCase):
    def test_raw_frame_fallback_preserves_full_timeline_when_concat_filter_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);parts=[]
            for i,color in enumerate(('red','blue')):
                path=root/f'part-{i}.mp4'
                subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color={color}:s=320x180:r=30',
                                '-frames:v','30','-an','-c:v','libx264','-threads','2',str(path)],check=True)
                parts.append(path)
            with patch('studio.effects.join_group',side_effect=ValueError('FFmpeg 9 timestamp regression')):
                movie=assemble(Project(scenes=[Scene(duration=1) for _ in parts],transition='cut'),parts,root,lambda *a:None)
            stream=probe(movie)['streams'][0]
            self.assertEqual(int(stream['nb_frames']),60)
            self.assertAlmostEqual(float(stream['duration']),2,places=2)

    def test_compressed_input_timestamps_cannot_discard_two_thirds_of_picture(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);parts=[]
            for i,color in enumerate(('red','blue','green')):
                path=root/f'part-{i}.mp4'
                subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color={color}:s=320x180:r=30',
                                '-vf','setpts=PTS/3','-frames:v','60','-fps_mode','passthrough','-an','-c:v','libx264','-threads','2','-video_track_timescale','15360',str(path)],check=True)
                parts.append(path)
            p=Project(scenes=[Scene(duration=2) for _ in parts],transition='cut')
            movie=assemble(p,parts,root,lambda *a:None);stream=probe(movie)['streams'][0]
            self.assertEqual(int(stream['nb_frames']),180)
            self.assertAlmostEqual(float(stream['duration']),6,places=2)
            for at,channel in ((.5,0),(2.5,2),(4.5,1)):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(movie),'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'])
                self.assertEqual(max(range(3),key=lambda c:raw[c]),channel)

    def test_missing_frames_are_not_announced_as_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);part=root/'short.mp4'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=red:s=160x90:r=30','-frames:v','15','-c:v','libx264','-threads','2',str(part)],check=True)
            with self.assertRaisesRegex(ValueError,'15 из 60'):
                assemble(Project(scenes=[Scene(duration=2)],transition='cut'),[part],root,lambda *a:None)

    def test_testing_people_and_scripture_do_not_become_office_or_hiking(self):
        p=Project(scenes=[Scene(duration=5),Scene(duration=5),Scene(duration=5),Scene(duration=5)],subtitles=[
            Subtitle(start=0,end=5,text='Последняя глава книги Еноха - это список людей.'),
            Subtitle(start=5,end=10,text='Говорит, что Бог намеренно испытывал этих'),
            Subtitle(start=10,end=15,text='людей в их телах, записывает результат и называет, что им причитается.'),
            Subtitle(start=15,end=20,text='Писание уже занесло их в дело с результатом рядом.')])
        for index in (1,2,3):
            context=scene_context(p,index)
            self.assertEqual(context['query'],'Book of Enoch manuscript')
            for title in ('Man writing in notebook near laptop','Person studying an ancient book at office','Person hiking mountain path'):
                self.assertFalse(relevant_candidate({'title':title},context),title)
            self.assertTrue(relevant_candidate({'title':'Book of Enoch illuminated manuscript'},context))
        generic={'query':'illuminated manuscript parchment','words':{'book','manusc'}}
        self.assertFalse(relevant_candidate({'title':'Book and pen on a desk'},generic))

    def test_previous_automatic_bad_pick_is_replaced_but_manual_choice_is_preserved(self):
        wrong={'id':'a'*32,'kind':'video','name':'Person writing in notebook near laptop','file':'bad.mp4'}
        right={'id':'b'*32,'kind':'image','name':'Book of Enoch manuscript','file':'book.jpg'}
        source=Scene(duration=4,asset_id=wrong['id'],media_reason='Видео · Рукопись · из библиотеки')
        p=Project(director_version=12,scenes=[source],subtitles=[Subtitle(start=0,end=4,text='Книга Еноха')])
        with patch('studio.auto_media.local_assets',return_value=[wrong,right]),patch('studio.media.asset_info',return_value=wrong):
            fixed=complete_visuals(p)
            self.assertEqual(fixed.scenes[0].asset_id,right['id'])
            self.assertEqual(p.scenes[0].asset_id,wrong['id'])
            p.scenes[0].media_reason=''
            self.assertEqual(complete_visuals(p).scenes[0].asset_id,wrong['id'])
