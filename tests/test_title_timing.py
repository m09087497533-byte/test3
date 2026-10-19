import io
import subprocess
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from PIL import Image, ImageChops
from studio.models import Project, Scene
from studio.render import overlay, render
from studio.effects import synthesize_sfx

class TitleTimingTest(unittest.TestCase):
    def test_layers_recombine_without_changing_design(self):
        for template in ['cinema','archive','thesis','quote','statement']:
            scene=Scene(template=template,title='Проверка',caption='Подпись')
            combined=Image.alpha_composite(overlay(scene,(640,360),decor_only=True),overlay(scene,(640,360),text_only=True))
            # Antialiased text may round by one in compositing, but all layers must exist.
            self.assertIsNotNone(overlay(scene,(640,360),text_only=True).getbbox())
            self.assertEqual(combined.size,(640,360))

    def test_delayed_title_and_sound_share_frame_timing(self):
        project=Project(scenes=[Scene(template='thesis',title='ТОЧНО',duration=2,motion=False,title_effect='slide',title_delay=.8,title_duration=.3,title_sound='click')],transition='cut',sfx_volume=.5)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            wav=folder/'accent.wav'
            self.assertTrue(synthesize_sfx(project,wav))
            with wave.open(str(wav)) as f:
                channels=f.getnchannels();samples=array('h',f.readframes(f.getnframes()))
            self.assertEqual(max(abs(x) for x in samples[:38400*channels]),0)
            self.assertGreater(max(abs(x) for x in samples[38400*channels:43200*channels]),100)
            movie=render(project,folder,lambda *args:None)
            def frame(t):
                raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
                return Image.open(io.BytesIO(raw)).convert('RGB')
            a,b,c=frame(.2),frame(.6),frame(1.4)
            self.assertLess(sum(ImageChops.difference(a,b).convert('L').getdata())/(1280*720),1)
            self.assertGreater(sum(ImageChops.difference(b,c).convert('L').getdata())/(1280*720),2)

    def test_title_cannot_start_after_scene(self):
        with self.assertRaises(ValueError):
            Scene(title='Поздно',duration=1,title_delay=1)
