import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('kadr_update',Path(__file__).resolve().parents[1]/'scripts/update_mac.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class LocalUpdateTest(unittest.TestCase):
    def test_preserves_media_keys_venv_and_backs_up_code(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'new';target=Path(temp)/'old';source.mkdir();target.mkdir()
            (target/'run.py').write_text('old code')
            (source/'run.py').write_text('new code')
            (source/'studio').mkdir();(source/'studio/auto_media.py').write_text('new feature')
            (source/'.data').mkdir();(source/'.data/stock-keys.json').write_text('must not overwrite')
            (target/'.venv/bin').mkdir(parents=True);(target/'.venv/bin/python').write_text('existing python')
            (target/'.data').mkdir();(target/'.data/stock-keys.json').write_text('existing keys');(target/'.data/voice.mp3').write_bytes(b'existing voice')
            module.update_files(source,target)
            self.assertEqual((target/'run.py').read_text(),'new code')
            self.assertTrue((target/'studio/auto_media.py').is_file())
            self.assertEqual((target/'.data/stock-keys.json').read_text(),'existing keys')
            self.assertEqual((target/'.data/voice.mp3').read_bytes(),b'existing voice')
            self.assertEqual((target/'.venv/bin/python').read_text(),'existing python')
            self.assertEqual(next((target/'.data/code-backups').glob('*/run.py')).read_text(),'old code')
