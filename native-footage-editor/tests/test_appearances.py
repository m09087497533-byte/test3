"""Editable text appearances: persistence, animation and real exported frames."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import render
import test_editing as support
from project import Project
from accents import plan_accents
from accents.appearances import APPEARANCE_KEYS, apply_appearance, clean_overrides
from accents.track import encode_track, event_frame


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.segments=[support.cue(0,5,'В 1773 году нашли рукопись.',footage_path='original.mp4'),
                       support.cue(8,13,'Единственный глагол в тексте.',selected_manually=True)]

    def test_stable_ids_and_overrides_preserve_words_times_and_media(self):
        original=copy.deepcopy(self.segments)
        before=plan_accents(self.segments,15)
        override={before[0]['id']:{'appearance':'focus'}}
        after=plan_accents(self.segments,15,overrides=override,major_rate=4)
        self.assertEqual([e['id'] for e in before],[e['id'] for e in after])
        self.assertEqual([e[k] for e in before for k in ('start','end','text')],
                         [e[k] for e in after for k in ('start','end','text')])
        self.assertEqual(after[0]['effect'],'focus')
        self.assertEqual(after[1]['appearance'],'auto')
        self.assertEqual(self.segments,original)

    def test_saved_choices_survive_reopening_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Project(Path(tmp)/'voice.wav');project.segments=copy.deepcopy(self.segments)
            project.effects=['pan_right','color_wash'];project.accent_sounds=True
            first=plan_accents(project.segments)[0]
            project.accent_overrides={first['id']:{'appearance':'neon'}};project.save()
            reopened=Project(project.audio_path);self.assertTrue(reopened.load())
            events=plan_accents(reopened.segments,overrides=reopened.accent_overrides)
            self.assertEqual(events[0]['appearance'],'neon')
            self.assertEqual(reopened.effects,project.effects)
            self.assertTrue(reopened.accent_sounds)
            self.assertEqual(reopened.segments[0]['footage_path'],'original.mp4')

    def test_old_project_and_invalid_overrides_use_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Project(Path(tmp)/'voice.wav');project.ensure_dirs()
            project.project_file.write_text(json.dumps(dict(schema_version=3,segments=self.segments)))
            self.assertTrue(project.load());self.assertEqual(project.accent_overrides,{})
        invalid={'accent_bad':{'appearance':'unknown'},'wrong_key':{'appearance':'focus'},
                 'accent_unsafe':'not a dictionary','accent_auto':{'appearance':'auto'},
                 'accent_list':{'appearance':['neon']}}
        self.assertEqual(clean_overrides(invalid),{})
        self.assertEqual(clean_overrides(None),{})
        self.assertTrue(all(e['appearance']=='auto' for e in plan_accents(self.segments,overrides=invalid)))

    def test_reset_recovers_original_auto_plan(self):
        before=plan_accents(self.segments)
        overrides={e['id']:{'appearance':'marquee'} for e in before}
        changed=plan_accents(self.segments,overrides=overrides)
        self.assertTrue(all(e['effect']=='marquee' for e in changed))
        self.assertEqual(plan_accents(self.segments,overrides={}),before)

    def test_all_presets_animate_and_settle_without_mutation(self):
        base=plan_accents(self.segments)[0];original=copy.deepcopy(base)
        self.assertGreaterEqual(len(APPEARANCE_KEYS)-1,25)
        for key in APPEARANCE_KEYS[1:]:
            with self.subTest(appearance=key):
                event=apply_appearance(base,key)
                early=np.array(event_frame(event,(640,360),.16,3))
                middle=np.array(event_frame(event,(640,360),.48,3))
                self.assertFalse(np.array_equal(early,middle),key)
                settled=np.array(event_frame(event,(640,360),1.7,2))
                later=np.array(event_frame(event,(640,360),2.2,1.5))
                if key=='marquee':self.assertFalse(np.array_equal(settled,later))
                else:self.assertTrue(np.array_equal(settled,later),key)
                self.assertGreater(settled[:,:,3].sum(),0)
        self.assertEqual(base,original)

    def test_opposite_entrances_have_opposite_positions(self):
        base=plan_accents(self.segments)[0]
        centers=[]
        for key in ('from_left','from_right'):
            alpha=np.array(event_frame(apply_appearance(base,key),(640,360),.16,3))[:,:,3]
            ys,xs=np.nonzero(alpha);centers.append(xs.mean())
        self.assertLess(centers[0],320);self.assertGreater(centers[1],320)

    def test_long_phrases_work_in_every_preset(self):
        base=plan_accents(self.segments)[0]
        base['text']='Самое важное происходит между двумя сторонами договора'
        for key in APPEARANCE_KEYS[1:]:
            with self.subTest(appearance=key):
                frame=event_frame(apply_appearance(base,key),(640,360),1.5,2)
                self.assertIsNotNone(frame.getbbox())


class RealAppearanceRenderTests(unittest.TestCase):
    def test_all_manual_appearances_encode_to_a_real_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);events=[]
            base=plan_accents([support.cue(0,4,'Короткая фраза из озвучки.')])[0]
            for i,key in enumerate(APPEARANCE_KEYS[1:]):
                event=apply_appearance(base,key)
                event.update(start=i*3,end=i*3+2.8,span=2.8)
                events.append(event)
            track=root/'appearances.mov';encode_track(events,(320,180),len(events)*75,track)
            indices=[i*75+40 for i in range(len(events))]
            select='+'.join(f'eq(n,{i})' for i in indices)
            raw=support.command('ffmpeg','-v','error','-i',str(track),'-vf',f"fps=25,select='{select}'",
                                '-fps_mode','vfr','-pix_fmt','rgba','-f','rawvideo','-')
            frames=np.frombuffer(raw,np.uint8).reshape(len(events),180,320,4)
            for key,frame in zip(APPEARANCE_KEYS[1:],frames):
                self.assertGreater(frame[:,:,3].sum(),0,key)
            support.command('ffmpeg','-v','error','-i',str(track),'-f','null','-')

    def test_selected_appearance_changes_actual_export_without_changing_cycle(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(render,'WIDTH',320),patch.object(render,'HEIGHT',180):
            root=Path(tmp);source=root/'source.mp4';audio=root/'voice.wav'
            support.voice(audio,6)
            support.command('ffmpeg','-v','error','-y','-f','lavfi','-i','color=0x385f59:s=320x180:r=25',
                            '-t','1','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(source))
            segments=[support.cue(0,3,'1773 год.',footage_path=str(source)),
                      support.cue(3,6,'Продолжение.',footage_path=str(source))]
            original=copy.deepcopy(segments);event=plan_accents(segments)[0]
            outputs=[]
            for key in ('red_ink','card'):
                out=root/f'{key}.mp4'
                render.build_video(segments,audio,out,['none','color_wash'],log_fn=lambda _:None,
                                   accent_overrides={event['id']:{'appearance':key}},accent_sounds=True)
                outputs.append(support.frame(out,1.6))
                data=support.probe(out)
                self.assertEqual(next(s['nb_frames'] for s in data['streams'] if s['codec_type']=='video'),'150')
            self.assertGreater(np.abs(outputs[0].astype(float)-outputs[1]).mean(),15)
            self.assertGreater(outputs[1][60,40].min(),180)
            self.assertEqual(segments,original)


if __name__=='__main__':unittest.main()
