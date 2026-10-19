"""Prove five regression tests detect faults, in disposable copies of the app."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
MUTATIONS=[
 ('language', 'studio/transcription.py', "language=None if language == 'auto' else language", "language='ru'",
  'test_transcription.TranscriptionTests.test_recognizer_is_cpu_int8_and_honors_language'),
 ('timing', 'studio/transcription.py', 'a, b = max(0, a), min(duration, b)', 'a, b = 0, min(duration, b)',
  'test_transcription.TranscriptionTests.test_recognition_uses_real_segment_times_and_builds_directed_scenes'),
 ('cancel', 'studio/transcription.py', "if state['status'] in ('queued', 'running') and (folder / 'worker.json').is_file():", "if (folder / 'worker.json').is_file():",
  'test_transcription.TranscriptionTests.test_cancel_cannot_be_overwritten_by_late_worker_result'),
 ('editor', 'studio/app.py', 'cue.text = text.strip()', 'cue.text = cue.text',
  'test_transcription.TranscriptEditingTests.test_edit_preserves_times_media_keys_and_voice'),
 ('semantic', 'studio/auto_media.py', 'meaning >= .58', 'meaning >= 2.',
  'test_semantic.SemanticTests.test_cross_language_paraphrase_can_pass_without_word_overlap'),
]
for name,file,old,new,test in MUTATIONS:
    with tempfile.TemporaryDirectory(prefix='kadr-mutation-') as tmp:
        clone=Path(tmp)/'app'
        shutil.copytree(ROOT,clone,ignore=shutil.ignore_patterns('.venv','.data','__pycache__','*.pyc','.git'))
        path=clone/file;source=path.read_text(encoding='utf-8')
        assert source.count(old)==1,(name,'Mutation target changed')
        path.write_text(source.replace(old,new),encoding='utf-8')
        env=dict(os.environ,PYTHONPATH=str(clone),STUDIO_DATA_DIR=str(Path(tmp)/'data'))
        result=subprocess.run([sys.executable,'-m','unittest',test],cwd=clone/'tests',env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode!=0 and 'FAILED (failures=1)' in result.stderr,(name,result.stdout,result.stderr)
        print('DETECTED:',name,flush=True)
print('PASS: all five intentional faults were detected; original source unchanged.')
