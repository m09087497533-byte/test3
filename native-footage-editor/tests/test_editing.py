"""Checks for this update; no stock credentials or model downloads required."""
import copy
import json
import math
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import effects
import render
from project import Project
from accents import plan_accents
from accents.audio import sound_events, synthesize_sfx
from accents.track import event_frame


def command(*args):
    return subprocess.run(list(args), check=True, capture_output=True, timeout=120).stdout


def voice(path, duration):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(48000)
        out.writeframes(b'\0\0' * round(duration * 48000))


def probe(path):
    return json.loads(command('ffprobe', '-v', 'error', '-show_streams', '-show_format',
                              '-of', 'json', str(path)))


def frame(path, time, size=(320, 180)):
    raw = command('ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path),
                  '-frames:v', '1', '-vf', f'scale={size[0]}:{size[1]}',
                  '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')
    return np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3)


def cue(start, end, text, **extra):
    return dict(start=start, end=end, text=text, **extra)


class PlanningTests(unittest.TestCase):
    def test_original_order_and_round_robin(self):
        self.assertEqual(effects.EFFECT_KEYS[:11], ['none', 'fade', 'ken_burns_ur',
            'ken_burns_dl', 'dissolve', 'zoom_in', 'zoom_out', 'pan_right',
            'pan_left', 'pan_down', 'pan_up'])
        selected = ['pan_right', 'light_leak', 'color_wash', 'transition_zoom']
        self.assertEqual(effects.assign_effects(13, selected), (selected * 4)[:13])
        self.assertEqual(effects.assign_effects(3, ['unknown']), ['none'] * 3)
        self.assertEqual(effects.assign_effects(4, ['none', 'light_leak']),
                         ['none', 'light_leak', 'none', 'light_leak'])

    def test_every_selected_transition_gets_a_boundary(self):
        keys = ['none'] + list(effects.TRANSITION_STYLES)
        links = render._transition_lengths([2] * len(keys), keys)
        self.assertEqual(links, [.56] * (len(keys) - 1))
        self.assertEqual(render._transition_lengths([2, 2], ['light_leak', 'none']), [0])

    def test_absolute_timing_with_pauses_and_500_scenes(self):
        cues = [cue(i * 2.013 + .11, i * 2.013 + 1.1, 'Рассказ.') for i in range(500)]
        original = copy.deepcopy(cues)
        length = cues[-1]['end'] + .4
        fx = effects.assign_effects(500, ['none', 'light_leak', 'color_wash'])
        timeline = render._build_timeline_with_gaps(cues, fx, audio_duration=length)
        base = [s['end'] - s['start'] for s in timeline]
        links = render._transition_lengths(base, fx)
        handled = [d + (links[i-1]/2 if i else 0) + (links[i]/2 if i < len(links) else 0)
                   for i, d in enumerate(base)]
        self.assertAlmostEqual(sum(handled)-sum(links), math.ceil(length*25)/25)
        for old, new in zip(cues[1:], timeline[1:]):
            self.assertEqual(new['start'], round(old['start']*25)/25)
        self.assertEqual(cues, original)

    def test_rejects_overlaps(self):
        with self.assertRaisesRegex(ValueError, 'перекрываются'):
            render._build_timeline_with_gaps([cue(0, 2, 'А'), cue(1, 3, 'Б')], ['none']*2)

    def test_complete_negative_across_cues(self):
        events = plan_accents([cue(0, 1, 'Результат был'), cue(1.2, 2.2, 'не наказанием.')], 4)
        self.assertEqual(events[0]['text'], 'РЕЗУЛЬТАТ БЫЛ НЕ НАКАЗАНИЕМ')
        self.assertEqual(events[0]['start'], 0)
        self.assertEqual(events[0]['tier'], 'caption')

    def test_rate_and_no_invented_numbers(self):
        cues = [cue(i*8, i*8+4, f'В {1773+i} году нашли рукопись.') for i in range(6)]
        original = copy.deepcopy(cues)
        events = plan_accents(cues, 60)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e['text'] in str(cues) for e in events))
        self.assertEqual(plan_accents(cues, 60, major_rate=0), [])
        self.assertEqual(cues, original)
        generic = plan_accents([cue(0, 5, 'Вода медленно превращается в пар.')], 5)
        self.assertEqual(generic[0]['text'], 'Вода медленно превращается в пар')

    def test_number_styles_rotate_and_settle(self):
        events = plan_accents([cue(t, t+5, f'В {year} году всё изменилось.')
                              for t, year in [(0,1773),(65,1821),(125,1905)]], 132)
        self.assertEqual([e['treatment'] for e in events], ['red_ink','neon','chromatic'])
        for event in events:
            a = np.array(event_frame(event, (640,360), 1.5, 2))
            b = np.array(event_frame(event, (640,360), 2.2, 1))
            self.assertTrue(np.array_equal(a,b), event['treatment'])

    def test_grid_stays_fixed_while_text_moves(self):
        event = plan_accents([cue(0, 4, 'Семь признаков настоящей истории.')], 5)[0]
        a = np.array(event_frame(event, (640,360), .4, 2))
        b = np.array(event_frame(event, (640,360), 1.4, 1))
        self.assertTrue(np.array_equal(a[:80],b[:80]))
        self.assertFalse(np.array_equal(a[100:250],b[100:250]))

    def test_sound_is_only_inside_text_entrances(self):
        events = plan_accents([cue(1, 6, 'В 1773 году нашли рукопись.'),
                               cue(8, 12, 'Единственный глагол в тексте.')], 14)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'sfx.wav'
            self.assertTrue(synthesize_sfx(events,14,path))
            with wave.open(str(path)) as src:
                samples = np.frombuffer(src.readframes(src.getnframes()), '<i2').reshape(-1,2)
            allowed = np.zeros(len(samples), dtype=bool)
            for event in sound_events(events,14):
                allowed[math.ceil(event['start']*48000):math.ceil(event['end']*48000)] = True
            self.assertEqual(np.count_nonzero(samples[~allowed]), 0)
            self.assertGreater(np.count_nonzero(samples[allowed]), 100)

    def test_legacy_project_round_trip_keeps_assignments_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(Path(tmp)/'voice.wav'); project.ensure_dirs()
            segment = cue(1, 3, 'Рассказ.', footage_path='my.mp4', footage_source='local',
                          candidates=[{'id':'pexels:22'}], selected_manually=True,
                          footage_query='свой запрос', _selected_candidate_id='pexels:22')
            project.project_file.write_text(json.dumps(dict(schema_version=2,
                segments=[segment], effects=['dissolve','pan_right'], story_context={'topic':'история'})))
            self.assertTrue(project.load())
            self.assertEqual((project.text_accents,project.accent_sounds,project.accent_rate), (True,False,2))
            project.text_accents=False; project.accent_sounds=True; project.accent_rate=4;project.save()
            reopened=Project(project.audio_path);reopened.load()
            self.assertEqual((reopened.text_accents,reopened.accent_sounds,reopened.accent_rate), (False,True,4))
            self.assertEqual(reopened.segments[0],dict(segment,index=0))
            self.assertEqual(reopened.effects,['dissolve','pan_right'])
            self.assertEqual(reopened.story_context,{'topic':'история'})

    def test_reimport_keeps_duplicate_footage_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Project(Path(tmp)/'voice.wav')
            project.segments=[cue(0,1,'Повтор.',footage_path='a.mp4',selected_manually=True),
                              cue(2,3,'Повтор.',footage_path='b.mp4',footage_query='ручной запрос')]
            project.set_segments_from_transcript([cue(1,2,'Повтор.'),cue(3,4,'Повтор.')])
            self.assertEqual([s['footage_path'] for s in project.segments],['a.mp4','b.mp4'])
            self.assertTrue(project.segments[0]['selected_manually'])
            self.assertEqual(project.segments[1]['footage_query'],'ручной запрос')


class RealRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        for name,color in [('red','0x763a37'),('blue','0x334c75'),('gray','0x586653')]:
            command('ffmpeg','-v','error','-y','-f','lavfi','-i',f'color={color}:s=320x180:r=25',
                    '-t','1','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(cls.root/f'{name}.mp4'))
        cls.audio=cls.root/'silence.wav';voice(cls.audio,6)
        cls.patches=[patch.object(render,'WIDTH',320),patch.object(render,'HEIGHT',180)]
        for p in cls.patches:p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:p.stop()
        cls.tmp.cleanup()

    def assert_video(self,path,frames=150):
        data=probe(path);video=next(s for s in data['streams'] if s['codec_type']=='video')
        self.assertEqual(int(video['nb_frames']),frames)
        self.assertEqual(video['avg_frame_rate'],'25/1')
        self.assertAlmostEqual(float(data['format']['duration']),frames/25,places=2)
        self.assertTrue(any(s['codec_type']=='audio' for s in data['streams']))
        command('ffmpeg','-v','error','-i',str(path),'-f','null','-')

    def test_all_seven_new_transitions_actual_ffmpeg(self):
        for effect in effects.EFFECT_KEYS[11:]:
            with self.subTest(effect=effect):
                out=self.root/f'{effect}.mp4'
                segments=[cue(0,2,'Один.',footage_path=str(self.root/'red.mp4')),
                          cue(2,4,'Два.',footage_path=str(self.root/'blue.mp4')),
                          cue(4,6,'Три.',footage_path=str(self.root/'red.mp4'))]
                render.build_video(segments,self.audio,out,['none',effect,effect],log_fn=lambda _:None,text_accents=False)
                self.assert_video(out)
                self.assertGreater(frame(out,.8)[:,:,0].mean(),frame(out,.8)[:,:,2].mean()+30)
                self.assertGreater(frame(out,3)[:,:,2].mean(),frame(out,3)[:,:,0].mean()+30)
                peak=frame(out,2)
                if effect=='light_leak':
                    self.assertGreater(peak[:,:,0].mean(),150)
                    self.assertGreater(peak[:,:,0].mean(),peak[:,:,2].mean()+50)
                elif effect=='color_wash':
                    self.assertGreater(peak[:,:,:].mean(),100)
                    self.assertGreater(peak[:,:,2].mean(),100)

    def test_cut_before_transition_and_batch_boundary(self):
        paths=[]
        fx=['none','none','light_leak','color_wash','dissolve','transition_zoom']
        links=render._transition_lengths([1]*6,fx)
        durations=[1+(links[i-1]/2 if i else 0)+(links[i]/2 if i<len(links) else 0) for i in range(6)]
        for i,d in enumerate(durations):
            path=self.root/f'batch-clip-{i}.mp4';render._clip_to_duration(self.root/'gray.mp4',d,path);paths.append(path)
        joined=render._concat_with_dissolve(paths,durations,fx,self.root,batch_size=2,transitions=links,log_fn=lambda _:None)
        self.assertAlmostEqual(render._probe_duration(joined),6,places=2)
        # Warm boundary at t=2 is between blocks; cool boundary at t=3 is inside one.
        warm=frame(joined,2);cool=frame(joined,3)
        self.assertGreater(warm[:,:,0].mean(),warm[:,:,2].mean()+50)
        self.assertGreater(cool[:,:,2].mean(),120)

    def test_overlay_keeps_phase_across_cut_and_encoded_silence(self):
        segments=[cue(0,2,'1773 год.',footage_path=str(self.root/'gray.mp4')),
                  cue(2,4,'Продолжение истории.',footage_path=str(self.root/'gray.mp4')),
                  cue(4,6,'Конец.',footage_path=str(self.root/'gray.mp4'))]
        original=copy.deepcopy(segments);out=self.root/'accents.mp4'
        render.build_video(segments,self.audio,out,['none']*3,log_fn=lambda _:None,accent_sounds=True)
        self.assert_video(out)
        self.assertEqual(segments,original)
        a=frame(out,1.6);b=frame(out,2.3)
        self.assertLess(np.abs(a.astype(float)-b).mean(),1)
        self.assertGreater(np.abs(a.astype(float)-frame(out,4.8)).mean(),10)
        audio=np.frombuffer(command('ffmpeg','-v','error','-i',str(out),'-vn','-ar','48000',
                                   '-ac','1','-f','f32le','-'),'<f4')
        self.assertGreater(np.max(np.abs(audio[:48000])),.001)
        self.assertLess(np.max(np.abs(audio[2*48000:])),.0001)

    def test_disabling_text_also_disables_its_sounds(self):
        out=self.root/'disabled.mp4'
        render.build_video([cue(0,6,'1773 год.',footage_path=str(self.root/'gray.mp4'))],
            self.audio,out,['zoom_in'],log_fn=lambda _:None,text_accents=False,accent_sounds=True)
        self.assert_video(out)
        audio=np.frombuffer(command('ffmpeg','-v','error','-i',str(out),'-vn','-f','f32le','-'),'<f4')
        self.assertLess(np.max(np.abs(audio)),.0001)

    def test_optional_sounds_do_not_delay_narration(self):
        narration=self.root/'narration.wav'
        time=np.arange(6*48000)/48000
        signal=(np.sin(2*np.pi*440*time)+np.sin(2*np.pi*733*time))*.15
        with wave.open(str(narration),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000)
            out.writeframes(np.round(signal*32767).astype('<i2').tobytes())
        segments=[cue(0,6,'1773 год.',footage_path=str(self.root/'gray.mp4'))]
        tracks=[]
        for enabled in (False,True):
            out=self.root/f'voice-sounds-{enabled}.mp4'
            render.build_video(segments,narration,out,['none'],log_fn=lambda _:None,accent_sounds=enabled)
            audio=np.frombuffer(command('ffmpeg','-v','error','-i',str(out),'-vn','-ar','48000',
                                       '-ac','1','-f','f32le','-'),'<f4')
            tracks.append(audio[3*48000:5*48000])
        self.assertGreater(np.corrcoef(*tracks)[0,1],.999)

    def test_failure_preserves_existing_export(self):
        out=self.root/'existing.mp4';out.write_bytes(b'previous export')
        with self.assertRaises(FileNotFoundError):
            render.build_video([cue(0,6,'Рассказ.',footage_path='missing-file.mp4')],self.audio,out)
        self.assertEqual(out.read_bytes(),b'previous export')


if __name__=='__main__':unittest.main()
