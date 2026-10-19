"""Real Tk window tests; run with DISPLAY under Linux or on a desktop."""
import os
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('DISPLAY') or os.sys.platform=='darwin', 'Requires a real Tk display')
class NativeWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import main
        self.tk=tk;self.main=main
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.audio=self.path/'voice.wav'
        with wave.open(str(self.audio),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(48000)
            output.writeframes(b'\0\0'*48000*16)
        self.root=tk.Tk();self.app=main.App(self.root)
        self.messages=[]
        self.info=patch.object(main.messagebox,'showinfo',side_effect=lambda *a,**k:self.messages.append(a));self.info.start()
        with patch.object(main.filedialog,'askopenfilename',return_value=str(self.audio)):
            self.app._pick_audio()
        self.pump(.15)

    def tearDown(self):
        self.info.stop()
        for job in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.after_cancel(job)
        self.root.destroy();self.tmp.cleanup()

    def pump(self,seconds=.1):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.root.update();time.sleep(.01)

    def wait_task(self):
        end=time.monotonic()+90
        while self.app._busy and time.monotonic()<end:self.pump(.05)
        self.assertFalse(self.app._busy,'Background operation did not finish')
        self.pump(.1)

    def import_srt(self):
        subtitle=self.path/'story.srt'
        subtitle.write_text('1\n00:00:00,000 --> 00:00:04,000\n1773 год.\n\n'
            '2\n00:00:04,000 --> 00:00:08,000\nПродолжение рассказа.\n\n'
            '3\n00:00:08,000 --> 00:00:12,000\nЕдинственный глагол.\n\n'
            '4\n00:00:12,000 --> 00:00:16,000\nКонец рассказа.\n',encoding='utf-8')
        with patch.object(self.main.filedialog,'askopenfilename',return_value=str(subtitle)):
            self.app._import_subtitles()
        self.wait_task()
        self.assertEqual(len(self.app.tree.get_children()),4)

    def screenshot(self,name):
        directory=os.environ.get('NATIVE_UI_ARTIFACTS')
        if directory:
            from PIL import ImageGrab
            self.pump(.3)
            Path(directory).mkdir(parents=True,exist_ok=True)
            ImageGrab.grab(xdisplay=os.environ.get('DISPLAY')).save(Path(directory)/name)

    def test_controls_save_reopen_and_scroll(self):
        self.import_srt()
        self.assertEqual(len(self.app._effect_checkboxes),18)
        for key in ['pan_right','light_leak','color_wash']:
            index=self.main.effects.EFFECT_KEYS.index(key)
            self.app._effect_checkboxes[index].invoke()
        self.assertEqual(self.app._selected_effects(),['pan_right','light_leak','color_wash'])
        self.app.accent_sounds_var.set(True);self.app.accent_rate_var.set('3');self.app._on_accents_changed()
        self.app._begin_task()
        self.assertTrue(all(w.cget('state')=='disabled' for w in self.app._accent_controls))
        self.app._finish_task()
        self.assertEqual(self.app._accent_controls[-1].cget('state'),'readonly')
        with patch.object(self.main.filedialog,'askopenfilename',return_value=str(self.audio)):
            self.app._pick_audio()
        self.assertEqual(self.app._selected_effects(),['pan_right','light_leak','color_wash'])
        self.assertTrue(self.app.accent_sounds_var.get())
        self.assertEqual(self.app.accent_rate_var.get(),'3')
        self.app.accent_sounds_var.set(False);self.app._on_accents_changed()
        self.screenshot('native-window.png')
        self.app.details_canvas.yview_moveto(1);self.pump(.1)
        self.assertAlmostEqual(self.app.details_canvas.yview()[1],1)
        self.screenshot('native-window-scrolled.png')

    def test_accent_plan_displays_real_graphics_and_closes_cleanly(self):
        self.import_srt();self.app._show_accents();self.pump(.2)
        dialog=next(w for w in self.root.winfo_children() if isinstance(w,self.tk.Toplevel))
        preview=next(w for w in dialog.winfo_children() if hasattr(w,'image'))
        self.assertGreater(preview.image.width(),500)
        self.screenshot('native-accent-plan.png')
        dialog.destroy();self.app._begin_task();self.app._finish_task()

    def test_render_button_runs_real_ffmpeg_with_selected_cycle(self):
        self.import_srt()
        source=self.path/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=0x385f59:s=320x180:r=25',
            '-t','1','-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(source)],check=True,timeout=30)
        for segment in self.app.project.segments:segment['footage_path']=str(source)
        self.app._set_effect_checkboxes(['light_leak','color_wash','transition_zoom'])
        self.app._on_effects_changed();self.app._refresh_tree()
        with patch.object(self.main.render,'WIDTH',320),patch.object(self.main.render,'HEIGHT',180), \
             patch.object(self.main.render,'build_video',wraps=self.main.render.build_video) as build:
            self.app._render();self.wait_task()
        self.assertEqual(build.call_args.kwargs['effects'],['light_leak','color_wash','transition_zoom','light_leak'])
        self.assertFalse(build.call_args.kwargs['accent_sounds'])
        output=self.app.project.project_dir/'final_video.mp4'
        self.assertTrue(output.is_file(),self.app.log.get('1.0','end'))
        count=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','stream=nb_frames','-of','csv=p=0',str(output)],text=True).strip()
        self.assertEqual(count,'400')
        self.assertTrue(self.messages)


if __name__=='__main__':unittest.main()
