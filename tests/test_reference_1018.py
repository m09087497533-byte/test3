import json, tempfile, unittest, wave
from pathlib import Path
from array import array
from unittest.mock import patch
from PIL import Image, ImageChops, ImageStat
from studio.models import Project,Scene,Subtitle,TextEvent
from studio.director import automatic_project
from studio.text_track import event_frame
from studio.soundplan import sound_plan
from studio.effects import synthesize_sfx

class Reference1018Test(unittest.TestCase):
    def test_dates_and_century_are_selected_from_actual_narration(self):
        cues=[Subtitle(start=0,end=5,text='Книга Еноха датируется 15 веком.'),Subtitle(start=5,end=9,text='В 1773 году нашли рукопись.'),Subtitle(start=10,end=14,text='В 1821 году издали книгу.')]
        p=automatic_project(Project(scenes=[Scene(duration=15)],subtitles=cues))
        events=[e for s in p.scenes for e in s.text_events if not e.elapsed]
        self.assertEqual([e.treatment for e in events],['italic','neon'])
        self.assertEqual(p.scenes[0].picture_effect,'split')
        self.assertEqual(p.scenes[0].document_title,'Книга Еноха')
        self.assertEqual(p.subtitles,cues)
        self.assertEqual(p.director_version,12)
        absent=automatic_project(Project(scenes=[Scene(duration=5)],subtitles=[Subtitle(start=0,end=5,text='Одна древняя книга.')]))
        self.assertFalse(any(e.layout=='number' for s in absent.scenes for e in s.text_events))

    def test_glowing_stroke_moves_then_stops_without_flicker(self):
        e=TextEvent(text='1773',start=0,end=3.5,layout='number',effect='reveal',decoration='ring',duration=.85,treatment='neon')
        s=Scene(duration=4);size=(640,360)
        a,b,c,d=[event_frame(s,e,size,t,2) for t in (.2,.8,1.6,2.2)]
        self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(a,b)).mean),2)
        self.assertEqual(c.tobytes(),d.tobytes(),'Settled graphics must not jitter or flicker')
        self.assertGreater(sum(1 for r,g,b,a in c.getdata() if g>150 and b>170 and r<190),30)
        white=e.model_copy(update={'text':'1821','treatment':'chromatic'})
        image=event_frame(s,white,size,1.7,2)
        self.assertGreater(sum(1 for r,g,b,a in image.getdata() if min(r,g,b)>225 and a>200),200)
        self.assertGreater(sum(1 for r,g,b,a in image.getdata() if r>g*1.3 and r>b*1.2 and a>150),10)

    def test_sound_is_sparse_stereo_and_not_retriggered_on_continuation(self):
        cue=TextEvent(text='1773',start=1,end=3,layout='number',treatment='neon')
        continued=cue.model_copy(update={'start':0,'end':1,'elapsed':2})
        p=Project(scenes=[Scene(duration=3,text_events=[cue]),Scene(duration=27,text_events=[continued])],sound_mode='auto',sounds_per_minute=4,sfx_volume=.28,transition='cut')
        plan=sound_plan(p);self.assertEqual(len(plan),1)
        self.assertAlmostEqual(plan[0]['time'],1.23333333,places=5)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fx.wav';self.assertTrue(synthesize_sfx(p,path))
            with wave.open(str(path)) as f:
                self.assertEqual(f.getnchannels(),2);self.assertEqual(f.getnframes(),30*48000)
                pcm=array('h',f.readframes(f.getnframes()))
            self.assertEqual(max(abs(x) for x in pcm[:48000]),0)
            self.assertNotEqual(pcm[::2],pcm[1::2])
            self.assertLess(max(abs(x) for x in pcm),int(.95*32768))

    def test_mono_voice_does_not_downmix_stereo_effects_or_lose_level(self):
        import math,subprocess
        from studio.render import render
        from studio.media import probe
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);identifier='e'*32;assets=root/'assets';folder=assets/identifier;folder.mkdir(parents=True)
            source=array('h',(round(3000*math.sin(2*math.pi*440*i/48000)) for i in range(48000)))
            with wave.open(str(folder/'voice.wav'),'wb') as f:f.setparams((1,2,48000,0,'NONE','not compressed'));f.writeframes(source.tobytes())
            (folder/'meta.json').write_text(json.dumps({'id':identifier,'kind':'audio','file':'voice.wav','name':'Voice','duration':1,'has_audio':True}))
            out=root/'render';out.mkdir()
            p=Project(scenes=[Scene(duration=1,title='Акцент',title_delay=.6,title_sound='click')],voice_id=identifier,sound_mode='manual',transition='cut',show_subtitles=False)
            with patch('studio.media.ASSETS',assets):movie=render(p,out,lambda *a:None,draft=True)
            audio=next(s for s in probe(movie)['streams'] if s['codec_type']=='audio')
            self.assertEqual(audio['channels'],2)
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(movie),'-f','s16le','-acodec','pcm_s16le','-'])
            samples=array('h',raw);left=samples[::2];right=samples[1::2]
            # Before the effect, mono voice is duplicated at unity, not attenuated by 3 dB.
            rms=lambda x:math.sqrt(sum(v*v for v in x)/len(x))
            self.assertAlmostEqual(rms(left[4800:14400])/rms(source[4800:14400]),1,delta=.08)
            self.assertGreater(sum(abs(a-b) for a,b in zip(left[28800:40000],right[28800:40000])),1000)
