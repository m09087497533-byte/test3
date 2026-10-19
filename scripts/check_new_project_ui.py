"""Browser regression for a fresh story and scene-level controls."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='kadr-new-project-') as tmp:
    work=Path(tmp)
    env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data'))
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'run.py','--no-browser','--port','8005'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8005/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto('http://127.0.0.1:8005')
                page.wait_for_function('typeof project!=="undefined" && !!project')
                page.evaluate("project.name='Старый рассказ';project.voice_id='a'.repeat(32);project.director_version=12;project.scenes.forEach(s=>{s.template='quote';s.asset_id=null});project.subtitles=[{start:0,end:2,text:'Старый текст'}];save()")
                page.reload();page.wait_for_function("typeof project!=='undefined' && !!project && project.name==='Старый рассказ'")
                page.locator('#new-project').click()
                page.wait_for_function("project.name==='Новая история' && project.voice_id===null && project.subtitles.length===0 && project.scenes.length===1")
                assert page.evaluate("JSON.parse(localStorage.getItem('kadr-previous-project')).name")=='Старый рассказ'
                page.reload();page.wait_for_function("typeof project!=='undefined' && !!project && project.name==='Новая история'")
                assert page.locator('#voice-info').is_hidden()
                page.evaluate("project.name='Перенесённый проект';localStorage.setItem('kadr-project-v15',JSON.stringify(project));localStorage.removeItem('kadr-project-v16')")
                page.reload();page.wait_for_function("typeof project!=='undefined' && !!project && project.name==='Перенесённый проект'")
                assert page.evaluate("JSON.parse(localStorage.getItem('kadr-project-v16')).name")=='Перенесённый проект'
                page.locator('#edit-screen-text').click()
                assert page.evaluate("document.activeElement.id")=='scene-title'
                vtt=work/'new.vtt'
                vtt.write_text('WEBVTT\n\n00:00.000 --> 00:03.000\nНовая история о лесной реке.\n\n00:05.000 --> 00:09.000\nСтаринная рукопись из Эфиопии.',encoding='utf-8')
                page.locator('#auto-after-subtitles').uncheck()
                page.locator('#file-srt').set_input_files(vtt)
                page.wait_for_function("project.subtitles.length===2 && project.subtitles[0].text.includes('лесной')")
                assert all('Старый' not in cue['text'] for cue in page.evaluate('project.subtitles'))
                requests=[]
                job='d'*32
                def start(route):
                    requests.append(route.request.post_data_json)
                    route.fulfill(status=202,content_type='application/json',body=json.dumps({'id':job,'status':'queued'}))
                page.route('**/api/auto-media',start)
                page.route('**/api/auto-media/'+job,lambda route:route.fulfill(content_type='application/json',body=json.dumps({'id':job,'status':'done','progress':100,'message':'Готово','assignments':[],'outcomes':[],'errors':[]})))
                page.locator('#regenerate-scene').click()
                page.wait_for_timeout(1000)
                if not requests:raise AssertionError({'toast':page.locator('#toast').inner_text(),'status':page.locator('#arrangement-status').inner_text(),'errors':errors})
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('завершён')")
                assert requests[-1]['scene_indices']==[0]
                page.locator('#another-variant').click()
                page.wait_for_timeout(1000)
                assert len(requests)==2 and requests[-1]['scene_indices']==[0]
                assert not errors,errors
                browser.close()
            print('PASS: new project clears old voice/cues, survives reload, keeps browser backup; scene controls and new VTT work.')
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
