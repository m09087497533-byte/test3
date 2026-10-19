import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('kadr_runtime',Path(__file__).resolve().parents[1]/'scripts/install_runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


def info(version=(3,12), **extra):
    return dict(version=list(version),implementation='CPython',free_threaded=False,machine='arm64',macos='15.6.1',**extra)


class RuntimeSelectionTest(unittest.TestCase):
    def test_mac_requires_standard_cpython312(self):
        with patch.object(runtime.sys,'platform','darwin'):
            self.assertTrue(runtime.compatible(info()))
            self.assertFalse(runtime.compatible(info((3,14))))
            threaded=info();threaded['free_threaded']=True
            self.assertFalse(runtime.compatible(threaded))

    def test_reuses_compatible_environment(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.sys,'platform','darwin'),patch.object(runtime,'runtime_info',return_value=info()),patch.object(runtime.subprocess,'run') as run:
            root=Path(tmp);python=root/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            self.assertEqual(runtime.choose_python(root),python)
            run.assert_not_called()

    def test_python314_environment_is_preserved_and312_sidecar_created(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.sys,'platform','darwin'):
            root=Path(tmp);old=root/'.venv/bin/python';old.parent.mkdir(parents=True);old.write_text('old python')
            data=root/'.data/stock-keys.json';data.parent.mkdir();data.write_text('keep existing keys')
            def probe(path):return info() if str(path)=='/supported/python3.12' else info((3,14))
            with patch.object(runtime,'runtime_info',side_effect=probe),patch.object(runtime.shutil,'which',return_value='/supported/python3.12'),patch.object(runtime.subprocess,'run') as run:
                selected=runtime.choose_python(root)
            self.assertEqual(selected,root/'.venv-py312/bin/python')
            run.assert_called_once_with(['/supported/python3.12','-m','venv',str(root/'.venv-py312')],check=True,stdout=runtime.sys.stderr)
            self.assertEqual(old.read_text(),'old python');self.assertEqual(data.read_text(),'keep existing keys')

    def test_missing_python_and_homebrew_give_precise_instruction(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.sys,'platform','darwin'),patch.object(runtime,'runtime_info',return_value=None),patch.object(runtime.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'brew install python@3.12'):
                runtime.choose_python(Path(tmp))

    def test_homebrew_installs_versioned_python_and_uses_its_prefix(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.sys,'platform','darwin'):
            root=Path(tmp)
            def probe(path):return info() if str(path)=='/brew/python312/bin/python3.12' else info((3,14))
            def which(name):return '/brew/bin/brew' if name=='brew' else None
            def run(args,**kwargs):return subprocess.CompletedProcess(args,0,stdout='/brew/python312\n' if '--prefix' in args else '')
            with patch.object(runtime,'runtime_info',side_effect=probe),patch.object(runtime.shutil,'which',side_effect=which),patch.object(runtime.subprocess,'run',side_effect=run) as command:
                self.assertEqual(runtime.choose_python(root),root/'.venv/bin/python')
            self.assertEqual(command.call_args_list[0].args[0],['/brew/bin/brew','install','python@3.12'])
            self.assertEqual(command.call_args_list[-1].args[0][0],'/brew/python312/bin/python3.12')

    def test_broken_sidecar_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.sys,'platform','darwin'):
            root=Path(tmp);(root/'.venv').mkdir();(root/'.venv-py312').mkdir();marker=root/'.venv-py312/keep';marker.write_text('backup')
            with patch.object(runtime,'runtime_info',return_value=info()),patch.object(runtime.shutil,'which',return_value='/supported/python3.12'):
                with self.assertRaisesRegex(RuntimeError,'Папка сохранена'):
                    runtime.choose_python(root)
            self.assertEqual(marker.read_text(),'backup')

    def test_optional_failures_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'choose_python',return_value=Path('/python')),patch.object(runtime,'runtime_info',return_value=info()),patch.object(runtime,'installed',return_value=False),patch.object(runtime,'install',side_effect=[True,False,True]) as install:
            self.assertEqual(runtime.prepare_runtime(Path(tmp)),Path('/python'))
            self.assertEqual([c.args[2] for c in install.call_args_list],['requirements.txt','requirements-speech.txt','requirements-semantic.txt'])

    def test_base_failure_prevents_unusable_startup(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'choose_python',return_value=Path('/python')),patch.object(runtime,'runtime_info',return_value=info()),patch.object(runtime,'installed',return_value=False),patch.object(runtime,'install',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'основные библиотеки'):
                runtime.prepare_runtime(Path(tmp))

    def test_install_requires_wheels_and_preserves_diagnostics(self):
        import subprocess
        with patch.object(runtime.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout='',stderr='No matching distribution')) as run:
            self.assertFalse(runtime.install(Path('/python'),Path('/app'),'requirements-speech.txt'))
        self.assertIn('--only-binary=:all:',run.call_args.args[0])

