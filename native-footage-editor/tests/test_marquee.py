"""The whole grid phrase must remain readable, including after a shot cut."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import render
import test_editing as support
from accents import plan_accents
from accents.marquee import marquee_layout, marquee_frame
from accents.track import event_frame


PHRASE = 'Мы меняем планы и переносим встречи ради важного разговора.'


def yellow(image):
    pixels=np.asarray(image)
    return (pixels[:,:,0]>140)&(pixels[:,:,1]>140)&(pixels[:,:,2]<100)


class ReadableGridTests(unittest.TestCase):
    def test_word_wrap_uses_multiple_rows_without_losing_words(self):
        phrases=['7 ПРИЗНАКОВ',PHRASE,
                 'Под них меняют планы, переносят встречи, откладывают дела и поездки на другой день.']
        for size in [(320,180),(640,360),(1280,720)]:
            for phrase in phrases:
                with self.subTest(size=size,phrase=phrase):
                    lines,_,_=marquee_layout(phrase,size)
                    self.assertGreaterEqual(len(lines),2)
                    self.assertLessEqual(len(lines),4)
                    self.assertEqual(' '.join(lines),' '.join(phrase.upper().split()))
        self.assertEqual(marquee_layout('1773',(640,360))[0],('1773',))

    def test_whole_phrase_stays_inside_picture_at_every_phase(self):
        size=(640,360);areas=[]
        for elapsed in [0,.1,.6,2,3.9,4]:
            frame=marquee_frame(PHRASE,size,elapsed,4)
            bounds=frame.getbbox()
            self.assertGreater(bounds[0],size[0]*.06)
            self.assertLess(bounds[2],size[0]*.94)
            self.assertGreater(bounds[1],size[1]*.10)
            self.assertLess(bounds[3],size[1]*.90)
            areas.append(np.asarray(frame)[:,:,3].sum())
        self.assertEqual(len(set(areas)),1,'Some letters were cropped during the movement')

    def test_drift_is_small_even_for_a_long_phrase(self):
        for text in ['7 ПРИЗНАКОВ',PHRASE]:
            first=marquee_frame(text,(1280,720),0,5).getbbox()
            last=marquee_frame(text,(1280,720),5,5).getbbox()
            travel=first[0]-last[0]
            self.assertGreater(travel,0)
            self.assertLessEqual(travel,1280*.025)
            self.assertEqual(first[1],last[1])

    def test_grid_stays_fixed_and_cached_glyphs_are_not_mutated(self):
        source=[support.cue(0,3,PHRASE)]
        event=plan_accents(source,8)[0]
        event=plan_accents(source,8,overrides={event['id']:{'appearance':'marquee'}})[0]
        before=np.array(event_frame(event,(640,360),.8,3))
        after=np.array(event_frame(event,(640,360),2.8,1))
        self.assertTrue(np.array_equal(before[:60],after[:60]))
        # Antialiased edges blend with different grid pixels during the drift.
        self.assertLess(abs(int(yellow(before).sum())-int(yellow(after).sum())),yellow(before).sum()*.002)
        self.assertFalse(np.array_equal(before,after))
        event_frame(event,(640,360),.02,.05)  # Fade must not alter cached glyphs.
        repeated=np.array(event_frame(event,(640,360),.8,3))
        self.assertTrue(np.array_equal(before,repeated))

    def test_reading_hold_keeps_original_start_words_media_and_voice_clocks(self):
        segments=[support.cue(0,3,PHRASE,footage_path='my.mp4',selected_manually=True)]
        original=copy.deepcopy(segments);base=plan_accents(segments,10)[0]
        event=plan_accents(segments,10,overrides={base['id']:{'appearance':'marquee'}})[0]
        self.assertGreaterEqual(event['span'],4)
        self.assertLessEqual(event['span'],8)
        self.assertGreater(event['end'],base['end'])
        self.assertEqual((event['start'],event['text'],event['id']),(base['start'],base['text'],base['id']))
        self.assertEqual(segments,original)

    def test_reading_hold_respects_next_accent_and_audio_end(self):
        segments=[support.cue(0,2,'1773 год.'),support.cue(3,5,'Единственный глагол.')]
        base=plan_accents(segments,6)
        events=plan_accents(segments,6,overrides={base[0]['id']:{'appearance':'marquee'}})
        self.assertLessEqual(events[0]['end'],events[1]['start']-.12)
        tail=plan_accents([segments[0]],2,overrides={base[0]['id']:{'appearance':'marquee'}})[0]
        self.assertLessEqual(tail['end'],2)
        automatic=plan_accents([support.cue(0,3,'Семь признаков настоящей истории.')],7)[0]
        self.assertEqual(automatic['effect'],'marquee')
        self.assertGreaterEqual(automatic['span'],4)


class ReadableGridExportTests(unittest.TestCase):
    def test_real_export_has_all_rows_and_a_reading_hold_through_a_cut(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(render,'WIDTH',320),patch.object(render,'HEIGHT',180):
            root=Path(tmp);source=root/'source.mp4';voice=root/'voice.wav';out=root/'film.mp4'
            support.voice(voice,6)
            support.command('ffmpeg','-v','error','-y','-f','lavfi','-i','color=0x385f59:s=320x180:r=25',
                '-t','1','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(source))
            segments=[support.cue(0,3,PHRASE,footage_path=str(source)),
                      support.cue(3,6,'Финал.',footage_path=str(source))]
            original=copy.deepcopy(segments);event=plan_accents(segments,6)[0]
            render.build_video(segments,voice,out,['none','color_wash'],log_fn=lambda _:None,
                accent_overrides={event['id']:{'appearance':'marquee'}},accent_sounds=True)
            for time in [.6,1.6,3.6]:
                mask=yellow(support.frame(out,time));ys,xs=np.nonzero(mask)
                self.assertGreater(mask.sum(),500)
                self.assertGreater(ys.max()-ys.min(),180*.25)
                self.assertGreater(xs.min(),320*.06)
                self.assertLess(xs.max(),320*.94)
            self.assertEqual(yellow(support.frame(out,5.2)).sum(),0)
            data=support.probe(out)
            self.assertEqual(next(s['nb_frames'] for s in data['streams'] if s['codec_type']=='video'),'150')
            audio=np.frombuffer(support.command('ffmpeg','-v','error','-i',str(out),'-vn','-ar','48000',
                '-ac','1','-f','f32le','-'),'<f4')
            self.assertGreater(np.abs(audio[:48000]).max(),.001)
            self.assertLess(np.abs(audio[48000:]).max(),.0001)
            self.assertEqual(segments,original)
            support.command('ffmpeg','-v','error','-i',str(out),'-f','null','-')


if __name__=='__main__':unittest.main()
