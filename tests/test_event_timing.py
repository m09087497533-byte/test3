import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from pydantic import ValidationError

from studio.director import automatic_project
from studio.event_timing import scene_events
from studio.models import Project, Scene, Subtitle, frame_count
from studio.reference_effects import apply_reference_effects
from studio.soundplan import sound_plan
from studio.transcription import error_message, recognize


class EventTimingTests(unittest.TestCase):
    def test_recognition_at_rounded_shot_boundary_keeps_voice_and_cue_clock(self):
        # Actual ASR output uses milliseconds; the cut is on frame 135 (4.5s).
        for start in (4.484, 4.49, 4.499, 4.5, 4.516):
            with self.subTest(start=start):
                class Model:
                    def __init__(self, *args, **kwargs): pass
                    def transcribe(self, *args, **kwargs):
                        return iter([NS(start=start, end=7.4, text='История начинается с одного открытия.', words=None)]), NS(language='ru')
                data=recognize(Path('unused.wav'),'tiny','ru',9,lambda *args:None,Model)
                project=Project(scenes=data['scenes'], subtitles=data['subtitles'], sound_mode='auto', sounds_per_minute=12)
                self.assertEqual(project.subtitles[0].start,start)
                self.assertEqual(project.subtitles[0].end,7.4)
                self.assertEqual(sum(frame_count(s) for s in project.scenes),270)
                self.assertFalse(project.scenes[0].text_events)
                self.assertEqual(project.scenes[1].text_events[0].start,0)
                self.assertEqual(project.scenes[1].text_events[0].elapsed,0)
                self.assertEqual(len(sound_plan(project)),1)

    def test_old_project_migration_uses_same_boundary_clock_and_is_idempotent(self):
        original=Project(scenes=[Scene(duration=4.5),Scene(duration=4.5)], director_version=12,
                         subtitles=[Subtitle(start=4.49,end=7.4,text='История начинается с одного открытия.')])
        ready=Project.model_validate(apply_reference_effects(original).model_dump())
        self.assertEqual(ready.subtitles, original.subtitles)
        self.assertEqual(ready.scenes[0].title,'')
        self.assertEqual(ready.scenes[1].title_delay,0)
        self.assertEqual(apply_reference_effects(ready),ready)

    def test_continuation_preserves_animation_phase_and_has_no_new_sound(self):
        event=dict(text='Дата', start=4.4667, end=7.27, duration=.6)
        before=scene_events([event],0,135)
        after=scene_events([event],135,270)
        self.assertEqual(before[0].end,4.5)
        self.assertAlmostEqual(after[0].elapsed,1/30)
        self.assertEqual(before[0].span,after[0].span)
        project=Project(scenes=[Scene(duration=4.5,text_events=before),Scene(duration=4.5,text_events=after)],
                        sound_mode='auto',sounds_per_minute=12)
        sounds=sound_plan(project)
        self.assertEqual(len(sounds),1)
        self.assertLessEqual(sounds[0]['end'],4.5)

    def test_invalid_manual_title_still_rejected_and_error_does_not_expose_project(self):
        with self.assertRaises(ValidationError) as caught:
            Scene(duration=4.5,title='Приватный текст',title_delay=4.49)
        message=error_message(caught.exception)
        self.assertIn('Обновите Кадр',message)
        for technical in ('validation errors','input_value','Приватный текст','pydantic','https://'):
            self.assertNotIn(technical,message)

    def test_migration_can_place_a_title_in_last_second_of_a_long_shot(self):
        original=Project(scenes=[Scene(duration=30)],director_version=12,
                         subtitles=[Subtitle(start=29.49,end=30,text='История начинается с одного открытия.')])
        ready=Project.model_validate(apply_reference_effects(original).model_dump())
        self.assertAlmostEqual(ready.scenes[0].title_delay,29.5)
        self.assertEqual(ready.scenes[0].title_end,30)

    def test_migration_preserves_a_fractional_scene_duration(self):
        original=Project(scenes=[Scene(duration=4.49),Scene(duration=4.51)],director_version=12,
                         subtitles=[Subtitle(start=3,end=6,text='История начинается с одного открытия.')])
        ready=Project.model_validate(apply_reference_effects(original).model_dump())
        self.assertEqual([s.duration for s in ready.scenes],[4.49,4.51])
        self.assertEqual(ready.scenes[0].text_events[0].end,4.49)
        self.assertEqual(ready.scenes[1].text_events[0].elapsed,1.5)
