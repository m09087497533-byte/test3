import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from studio.models import Project, Scene
from studio.music import ambient_loop
from studio.render import render
from studio.media import probe


class AutoMusicTest(unittest.TestCase):
    def test_original_music_loop_has_quiet_seam(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=ambient_loop(Path(tmp)/'music.wav')
            with wave.open(str(path),'rb') as wav:
                self.assertEqual(wav.getnframes(),16*24000)
                self.assertEqual(wav.getnchannels(),1)
                first=wav.readframes(1)
                wav.setpos(wav.getnframes()-1)
                last=wav.readframes(1)
            self.assertEqual(first,b'\0\0')
            self.assertLess(abs(int.from_bytes(last,'little',signed=True)),50)

    def test_render_adds_music_when_new_project_has_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';assets.mkdir()
            identifier='a'*32;folder=assets/identifier;folder.mkdir()
            with wave.open(str(folder/'voice.wav'),'wb') as wav:
                wav.setparams((1,2,24000,0,'NONE','not compressed'))
                wav.writeframes(b'\0\0'*24000)
            (folder/'meta.json').write_text(json.dumps({'id':identifier,'kind':'audio','name':'voice.wav','file':'voice.wav','has_audio':True,'duration':1}))
            project=Project(scenes=[Scene(duration=1)],voice_id=identifier,auto_music=True,
                            show_subtitles=False,sound_mode='off',transition='cut')
            out=root/'render';out.mkdir()
            with patch('studio.media.ASSETS',assets):
                film=render(project,out,lambda *args:None,draft=True)
            self.assertEqual(next(s for s in probe(film)['streams'] if s['codec_type']=='video')['nb_frames'],'30')
            self.assertTrue(any(s['codec_type']=='audio' for s in probe(film)['streams']))


if __name__=='__main__':unittest.main()
