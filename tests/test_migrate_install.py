import tempfile
import unittest
from pathlib import Path

from scripts.migrate_install import migrate


class InstallMigrationTest(unittest.TestCase):
    def test_neighboring_install_keeps_visuals_voice_and_stock_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp);old=parent/'kadr-studio';new=parent/'kadr-studio 2'
            folder=old/'.data/assets'/('a'*32);folder.mkdir(parents=True)
            (old/'Запустить Кадр.command').write_text('launcher')
            (folder/'meta.json').write_text('{"kind":"audio"}')
            (folder/'voice.mp3').write_bytes(b'voice')
            (old/'.data/stock-keys.json').write_text('{"pexels":"private"}')
            new.mkdir()
            self.assertTrue(migrate(new))
            self.assertEqual((new/'.data/assets'/('a'*32)/'voice.mp3').read_bytes(),b'voice')
            self.assertEqual((new/'.data/stock-keys.json').stat().st_mode&0o777,0o600)
            self.assertFalse(migrate(new))
