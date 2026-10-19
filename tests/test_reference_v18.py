import io
import math
import subprocess
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageStat
from studio.models import Project,Scene,Subtitle,TextEvent
from studio.soundplan import sound_plan
from studio.effects import synthesize_sfx
from studio.reference_effects import apply_reference_effects
from studio.text_track import event_frame
from studio.render import render
from studio.media import probe


class TextOnlySoundTests(unittest.TestCase):
    def test_transition_flags_never_create_sound_without_text(self):
        for mode in ('auto','manual'):
            p=Project(scenes=[Scene(duration=10,transition_out=t) for t in ('light_leak','color_wash','zoom','slide')],
                      sound_mode=mode,transition_sounds=True,sounds_per_minute=12)
            self.assertEqual(sound_plan(p),[])
            with tempfile.TemporaryDirectory() as tmp:
                self.assertFalse(synthesize_sfx(p,Path(tmp)/'fx.wav'))

    def test_continued_text_does_not_retrigger_in_either_mode(self):
        continued=TextEvent(text='1773',start=0,end=2,elapsed=1,layout='number',treatment='neon')
        for mode in ('auto','manual'):
            p=Project(scenes=[Scene(duration=10,title='1773',title_sound='impact',text_events=[continued])],sound_mode=mode,sounds_per_minute=12)
            self.assertEqual(sound_plan(p),[])

    def test_whitespace_is_not_a_visible_title(self):
        p=Project(scenes=[Scene(duration=10,title='   ',text_events=[TextEvent(text=' ',start=1,end=2)])],sound_mode='auto',sounds_per_minute=12)
        self.assertEqual(sound_plan(p),[])

    def test_pcm_is_zero_outside_each_text_entrance(self):
        events=[TextEvent(text='1773',start=2,end=4,duration=.85,treatment='neon',layout='number',decoration='ring'),
                TextEvent(text='Книга',start=7,end=9,duration=.65,effect='stamp')]
        p=Project(scenes=[Scene(duration=12,text_events=events)],sound_mode='auto',sounds_per_minute=12)
        plan=sound_plan(p);self.assertEqual(len(plan),2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fx.wav';self.assertTrue(synthesize_sfx(p,path))
            with wave.open(str(path)) as f:
                self.assertEqual(f.getnframes(),12*48000)
                pcm=array('h',f.readframes(f.getnframes()))
            for i in range(12*48000):
                inside=any(e['start']<=i/48000<e['end'] for e in plan)
                if not inside:self.assertEqual(pcm[2*i:2*i+2],array('h',[0,0]))
            self.assertGreater(max(abs(x) for x in pcm),100)

    def test_all_new_sound_variants_are_distinct_and_bounded(self):
        signatures=[]
        with tempfile.TemporaryDirectory() as tmp:
            for style in ('paper','sweep','chime','stamp','spark'):
                p=Project(scenes=[Scene(duration=2,title='Текст',title_delay=.6,title_duration=.6,title_sound=style)],sound_mode='manual')
                path=Path(tmp)/(style+'.wav');self.assertTrue(synthesize_sfx(p,path))
                with wave.open(str(path)) as f:pcm=array('h',f.readframes(f.getnframes()))
                self.assertEqual(max(abs(x) for x in pcm[:round(.6*48000)*2]),0)
                self.assertEqual(max(abs(x) for x in pcm[round(1.2*48000)*2:]),0)
                self.assertGreater(max(abs(x) for x in pcm),50)
                self.assertLess(max(abs(x) for x in pcm),round(.95*32768))
                signatures.append(pcm.tobytes())
        self.assertEqual(len(set(signatures)),5)


class ReferenceVisualTests(unittest.TestCase):
    def test_profile_keeps_media_times_voice_music_and_has_variety(self):
        p=Project(scenes=[Scene(duration=3,asset_id='a'*32,offset=i) for i in range(8)],
                  subtitles=[Subtitle(start=1.123,end=2.456,text='Настоящая фраза.')],voice_id='b'*32,music_id='c'*32)
        revised=apply_reference_effects(p)
        self.assertEqual(revised.subtitles,p.subtitles)
        self.assertEqual(revised.voice_id,p.voice_id);self.assertEqual(revised.music_id,p.music_id)
        self.assertEqual([(s.asset_id,s.offset,s.duration) for s in revised.scenes],[(s.asset_id,s.offset,s.duration) for s in p.scenes])
        self.assertTrue(all(s.transition_out=='cut' for s in revised.scenes[:-1]),'The same source is reframed, not put through an effect cycle')
        self.assertTrue(all(s.camera_style!='inherit' for s in revised.scenes))
        self.assertEqual(apply_reference_effects(revised),revised)

    def test_stamp_and_letter_reveal_animate_and_then_settle(self):
        scene=Scene(duration=4)
        for effect in ('stamp','typewriter'):
            e=TextEvent(text='НОВАЯ ИСТОРИЯ',start=0,end=3,effect=effect,duration=.7)
            a,b,c,d=[event_frame(scene,e,(640,360),t,2) for t in (.1,.4,1.1,1.6)]
            self.assertNotEqual(a.tobytes(),b.tobytes())
            self.assertEqual(c.tobytes(),d.tobytes())
        date=TextEvent(text='1821',start=0,end=3,effect='stamp',duration=.85,treatment='chromatic',layout='number')
        frames=[event_frame(scene,date,(640,360),t,2) for t in (.1,.4,1.3,1.8)]
        self.assertNotEqual(frames[0].tobytes(),frames[1].tobytes())
        self.assertEqual(frames[2].tobytes(),frames[3].tobytes())

    def test_other_topics_get_actual_spoken_phrases_with_new_entrances(self):
        from studio.director import automatic_project
        cues=[Subtitle(start=1,end=4,text='Умные роботы помогают врачам.'),
              Subtitle(start=13,end=16,text='Учёные изучают новые материалы.')]
        p=automatic_project(Project(scenes=[Scene(duration=24)],subtitles=cues))
        events=[e for s in p.scenes for e in s.text_events if not e.elapsed]
        self.assertEqual(len(events),2)
        self.assertEqual({e.effect for e in events},{'stamp','typewriter'})
        self.assertEqual({e.text for e in events},{c.text.rstrip('.') for c in cues})
        self.assertEqual(p.subtitles,cues)

    def test_real_colored_transitions_keep_frames_and_are_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Project(scenes=[Scene(duration=1,transition_out=t) for t in ('light_leak','color_wash','zoom','cut')],
                      sound_mode='auto',sounds_per_minute=12,transition_sounds=True,show_subtitles=False)
            with patch('studio.render.source_still',return_value=Image.new('RGB',(960,540),'#555555')):
                movie=render(p,Path(tmp),lambda *a:None,draft=True)
            info=probe(movie);video=next(s for s in info['streams'] if s['codec_type']=='video')
            self.assertEqual(int(video['nb_frames']),120)
            self.assertAlmostEqual(float(video['duration']),4,delta=.04)
            for time,kind in [(1.2,'warm'),(2.2,'cool')]:
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                frame=Image.open(io.BytesIO(raw)).convert('RGB')
                r,g,b=frame.getpixel((480,270))
                if kind=='warm':self.assertGreater(r,b+15)
                else:self.assertGreater(b,r+15)
            self.assertFalse(any(s['codec_type']=='audio' for s in info['streams']))
