import io,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageChops,ImageStat
from studio.models import Project,Scene,Subtitle,TextEvent,frame_count
from studio.subtitles import parse_subtitles,plan_scenes
from studio.director import automatic_project
from studio.image_search import scene_query
from studio.auto_media import topic_words,scene_context,relevant_candidate
from studio.render import render,framed_card
from studio.narration import emphasis_events
from studio.media import probe

class ReferenceRegressionsTest(unittest.TestCase):
    def test_inner_syllables_do_not_become_rome_or_manuscripts(self):
        p=Project(scenes=[Scene(duration=4),Scene(duration=4)],subtitles=[Subtitle(start=0,end=4,text='Книга Еноха'),Subtitle(start=4,end=8,text='Примерить каждый к собственной жизни. Описание опыта.')])
        self.assertEqual(scene_query(p,1)['query'],'Book of Enoch manuscript')
        self.assertNotIn('rome',topic_words('примерить'))
        self.assertNotIn('manusc',topic_words('описание'))

    def test_country_and_building_do_not_pass_for_a_manuscript(self):
        p=Project(scenes=[Scene(duration=4,title='Эфиопская рукопись')])
        context=scene_context(p,0)
        self.assertFalse(relevant_candidate({'title':'Ethiopian church tower skyline'},context))
        self.assertTrue(relevant_candidate({'title':'Ethiopian illuminated manuscript'},context))
        self.assertFalse(relevant_candidate({'title':'Greek manuscript'},context))

    def test_split_cue_negation_stays_complete_and_accents_are_short(self):
        cues=[Subtitle(start=0,end=3,text='перечисляет семь признаков его жизни'),Subtitle(start=3,end=6,text='результат был не'),Subtitle(start=6.1,end=7,text='наказанием.'),Subtitle(start=10,end=12,text='главу, стих за стихом.')]
        events=emphasis_events(cues)
        self.assertIn('7 ПРИЗНАКОВ',[e['text'] for e in events])
        negative=next(e for e in events if e['text']=='НЕ НАКАЗАНИЕ')
        self.assertGreater(negative['start'],3)
        self.assertFalse(any(e['text'].endswith('НЕ') for e in events))
        p=Project(scenes=[Scene(duration=3),Scene(duration=4),Scene(duration=5)],subtitles=cues)
        self.assertNotEqual(scene_query(p,1)['concept'],'Суд и воздаяние')

    def test_multiple_accents_keep_their_timing_and_grid_does_not_slide(self):
        from studio.text_track import event_frame
        scene=Scene(duration=6,text_events=[TextEvent(text='7 ПРИЗНАКОВ',start=.5,end=2.5,layout='grid',effect='scroll'),TextEvent(text='НЕ НАКАЗАНИЕ',start=3,end=5.5,layout='label')])
        layer=event_frame(scene,scene.text_events[0],(960,540),.2,2)
        self.assertGreater(layer.getpixel((0,0))[3],240,'The grid must already cover the frame while letters enter')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            with patch('studio.render.source_still',return_value=Image.new('RGB',(960,540),'#4d7890')):
                movie=render(Project(scenes=[scene],transition='cut',show_subtitles=False),folder,lambda *a:None,draft=True)
            self.assertEqual(int(probe(movie)['streams'][0]['nb_frames']),180)
            def frame(t):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB')
            before,grid,between,label,after=map(frame,(.2,1.4,2.8,4,5.9))
            self.assertLess(sum(ImageStat.Stat(ImageChops.difference(before,after)).mean),1)
            self.assertLess(sum(ImageStat.Stat(ImageChops.difference(before,between)).mean),1)
            self.assertGreater(sum(1 for r,g,b in grid.getdata() if r>100 and g>120 and b<70),100)
            self.assertGreater(sum(1 for r,g,b in label.crop((0,440,960,500)).getdata() if r>180 and g>180 and b>150),200)

    def test_portrait_manuscript_fits_the_card_without_cutting_off_its_top(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);identifier='f'*32;folder=root/identifier;folder.mkdir()
            image=Image.new('RGB',(400,800),'#c8ba99');image.paste('#ff0000',(0,0,400,100));image.save(folder/'image.png')
            (folder/'meta.json').write_text(json.dumps({'id':identifier,'kind':'image','file':'image.png'}))
            with patch('studio.media.ASSETS',root):card=framed_card(Scene(asset_id=identifier),(960,540))
            self.assertLess(card.width,card.height)
            self.assertGreater(sum(1 for r,g,b,a in card.getdata() if r>180 and g<90),100)

    def test_word_boundary_and_negation_rules_keep_affirmative_sentences(self):
        p=Project(scenes=[Scene(duration=3),Scene(duration=3)],subtitles=[Subtitle(start=0,end=3,text='Это было наказание.'),Subtitle(start=3,end=6,text='Это было не наказание.')])
        self.assertEqual(scene_query(p,0)['concept'],'Суд и воздаяние')
        self.assertNotEqual(scene_query(p,1)['concept'],'Суд и воздаяние')

    def test_typographic_fallback_preserves_real_accents(self):
        from studio.visual_fallback import text_scene
        source=Scene(duration=4,text_events=[TextEvent(text='7 ПРИЗНАКОВ',start=1,end=3,layout='grid',effect='scroll')])
        p=Project(director_version=10,scenes=[source],subtitles=[Subtitle(start=0,end=4,text='Семь признаков')])
        fallback=text_scene(p,0)
        self.assertEqual(fallback.text_events,source.text_events)
        self.assertEqual(fallback.title_layout,'grid')
        self.assertEqual(frame_count(fallback),frame_count(source))

    def test_scene_order_and_fast_seeking_match_the_frame_clock(self):
        pictures=[Image.new('RGB',(960,540),color) for color in ('#dd1111','#1111dd','#11dd11')]
        with tempfile.TemporaryDirectory() as temp,patch('studio.render.source_still',side_effect=pictures):
            movie=render(Project(scenes=[Scene(duration=1) for _ in pictures],transition='cut',sound_mode='off'),Path(temp),lambda *a:None,draft=True)
            def image(args):
                raw=subprocess.check_output(['ffmpeg','-v','error',*args,'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB')
            for timepoint,channel in ((.5,0),(1.5,2),(2.5,1)):
                fast=image(['-ss',str(timepoint),'-i',str(movie)])
                exact=image(['-i',str(movie),'-vf',f'select=eq(n\\,{round(timepoint*30)})','-fps_mode','passthrough'])
                pixel=fast.getpixel((450,200))
                self.assertEqual(max(range(3),key=lambda c:pixel[c]),channel)
                self.assertLess(sum(ImageStat.Stat(ImageChops.difference(fast,exact)).mean),.1)

    def test_counter_uses_supplied_quantity_without_inventing_reference_numbers(self):
        events=emphasis_events([Subtitle(start=0,end=4,text='В исследовании участвовало 2320 человек.')])
        count=next(e for e in events if e['layout']=='counter')
        self.assertEqual(count['text'],'2320 человек')
        self.assertFalse(any(e['layout']=='counter' for e in emphasis_events([Subtitle(start=0,end=4,text='В исследовании участвовали люди.')])) )
        from studio.text_track import event_frame
        event=TextEvent.model_validate({k:v for k,v in count.items() if k!='priority'})
        a=event_frame(Scene(duration=4),event,(960,540),.1,2)
        b=event_frame(Scene(duration=4),event,(960,540),1,2)
        self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(a,b)).mean),.1)
        with tempfile.TemporaryDirectory() as temp:
            clipped=event.model_copy(update={'start':.1,'end':2.9})
            movie=render(Project(scenes=[Scene(duration=3,text_events=[clipped])],transition='cut',sound_mode='off'),Path(temp),lambda *a:None,draft=True)
            raw=subprocess.check_output(['ffmpeg','-v','error','-ss','2','-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
            image=Image.open(io.BytesIO(raw)).convert('RGB')
            self.assertGreater(sum(1 for r,g,b in image.getdata() if b>150 and r<130 and g>70),100)

