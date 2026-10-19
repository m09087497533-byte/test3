"""Real browser/API regression for the one-time effects migration and its undo."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='kadr18-ui-') as tmp:
    with open(Path(tmp)/'server.log','w') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'run.py','--no-browser','--port','8019'],cwd=ROOT,
                                env=dict(os.environ,STUDIO_DATA_DIR=str(Path(tmp)/'data')),stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8019/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page();errors=[];migrations=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:migrations.append(r.url) if r.url.endswith('/api/project/reference-effects') else None)
                page.goto('http://127.0.0.1:8019');page.wait_for_function('!!project && speechAvailable')
                page.evaluate("""project.effects_version=0;project.sound_mode='manual';project.transition_sounds=true;
                    project.scenes=[{...project.scenes[0],template:'quote',title:'ПЕРВАЯ ФРАЗА',title_sound:'impact'},
                                    {...project.scenes[0],template:'quote',title:'ВТОРАЯ ФРАЗА',title_sound:'impact'}];save()""")
                page.reload();page.wait_for_function("typeof project!=='undefined' && !!project && project.effects_version===19")
                assert page.evaluate('project.scenes.map(s=>s.duration)')==[4,4]
                assert page.evaluate('project.scenes[0].title')=='ПЕРВАЯ ФРАЗА'
                assert page.evaluate('project.scenes[0].transition_out')=='cut'
                assert page.evaluate('project.scenes[0].title_effect')=='perspective'
                assert page.locator('#transition-sounds').is_hidden()
                assert page.locator('#undo-effects').is_visible()
                assert page.evaluate("!!localStorage.getItem('kadr-before-effects-v19')")
                count=len(migrations);page.reload();page.wait_for_function("typeof project!=='undefined' && !!project && project.effects_version===19 && speechAvailable")
                assert len(migrations)==count,'Profile reapplied after reload'
                page.locator('#undo-effects').click()
                assert page.evaluate('project.sound_mode')=='manual'
                assert page.evaluate('project.scenes[0].title_effect')=='inherit'
                assert page.evaluate('project.effects_version')==19
                page.locator('#reference-effects').click()
                page.wait_for_function("project.scenes[0].title_effect==='perspective'")
                page.select_option('#sound-mode','manual')
                page.select_option('#title-sound','spark')
                page.select_option('#title-effect','typewriter')
                page.select_option('#scene-transition','color_wash')
                assert page.evaluate('project.scenes[0].title_sound')=='spark'
                assert page.evaluate('project.scenes[0].title_effect')=='typewriter'
                assert not errors,errors
                browser.close()
            print('PASS: Chromium; one-time migration, stable durations/text, backup, undo, silent-transition UI, new appearance/sound/transition controls.')
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
