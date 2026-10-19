"""Chromium integration: real app/API/editor, mocked model results (no model download)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import wave

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='kadr-speech-ui-') as tmp:
    work=Path(tmp)
    voice=work/'voice.wav'
    with wave.open(str(voice),'wb') as wav:
        wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*8*16000)
    env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data'))
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'run.py','--no-browser','--port','8017'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8017/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];starts=[];cancels=[];media_requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda d:d.accept())
                job='d'*32
                state={'id':job,'status':'running','message':'Распознаём речь…','progress':25}
                def speech(route):
                    url=route.request.url
                    if url.endswith('/cancel'):
                        cancels.append(url);state.update(status='cancelled',message='Остановлено')
                        route.fulfill(json=state)
                    elif url.endswith('/'+job):route.fulfill(json=state)
                    elif route.request.method=='POST':
                        starts.append(route.request.post_data_json)
                        state.update(status='running',message='Распознаём речь…')
                        route.fulfill(status=202,json={'id':job,'status':'queued'})
                    else:route.fulfill(json={'available':True})
                page.route('**/api/transcription',speech);page.route('**/api/transcription/**',speech)
                page.on('request',lambda req:media_requests.append(req.url) if req.url.endswith('/api/auto-media') else None)
                page.goto('http://127.0.0.1:8017')
                page.wait_for_function('speechAvailable && !!project')
                page.wait_for_timeout(400)
                assert not media_requests,'Empty project started media search'
                page.locator('#auto-after-subtitles').uncheck()
                page.locator('#file-voice').set_input_files(voice)
                page.wait_for_function('speechRecord && speechRecord.id')
                assert len(starts)==1 and starts[0]['model']=='small' and starts[0]['language']=='ru'
                voice_id=page.evaluate('project.voice_id')
                page.reload();page.wait_for_function('speechRecord && speechRecord.id')
                assert len(starts)==1,'Reload restarted the recognizer'
                montage=page.evaluate('structuredClone(project)')
                montage['scenes'][0]['duration']=8
                montage['subtitles']=[dict(start=1.234,end=6.345,text='В 1821 году открыли новую звезду.')]
                response=page.request.post('http://127.0.0.1:8017/api/project/reference-rhythm',data=montage)
                assert response.ok,response.text()
                directed=response.json()
                state.update(status='done',progress=100,message='Речь распознана',result={k:directed[k] for k in ('scenes','subtitles','director_version')})
                page.wait_for_function('project.subtitles.length===1 && !speechRecord')
                assert page.evaluate('project.voice_id')==voice_id
                assert not media_requests,'Disabled automatic media search was ignored'
                page.locator('#transcript').evaluate('(e)=>e.open=true')
                page.locator('#edit-transcript').click()
                page.locator('#transcript-fields textarea').fill('В 1773 году открыли новую звезду.')
                page.locator('#transcript-apply').click()
                page.wait_for_function("project.subtitles[0].text.includes('1773')")
                assert page.evaluate('project.subtitles[0].start')==1.234
                assert page.evaluate('project.subtitles[0].end')==6.345
                assert page.evaluate('project.voice_id')==voice_id
                with page.expect_download() as event:page.locator('#download-subtitles').click()
                download=event.value;dest=work/'export.srt';download.save_as(dest)
                assert '00:00:01,234 --> 00:00:06,345' in dest.read_text()
                assert '1773' in dest.read_text()
                page.locator('#speech-start').click();page.wait_for_function('speechRecord && speechRecord.id')
                page.locator('#speech-cancel').click();page.wait_for_function('!speechRecord')
                assert cancels
                page.locator('#speech-start').click();page.wait_for_function('speechRecord && speechRecord.id')
                state.update(status='error',message='Не удалось загрузить модель. Проверьте интернет.')
                page.wait_for_function("!speechRecord && document.querySelector('#speech-status').textContent.includes('интернет')")
                page.locator('#speech-start').click();page.wait_for_function('speechRecord && speechRecord.id')
                page.locator('#new-project').click()
                page.wait_for_function('!speechRecord && project.voice_id===null && project.subtitles.length===0')
                state.update(status='done')
                page.wait_for_timeout(1100)
                assert page.evaluate('project.subtitles.length')==0,'Late result changed the new project'
                assert len(cancels)>=2
                # Manual start works when automatic recognition is off.
                page.locator('#speech-auto').uncheck()
                page.locator('#file-voice').set_input_files(voice)
                page.wait_for_function('!!project.voice_id')
                count=len(starts);page.wait_for_timeout(300);assert len(starts)==count
                page.locator('#speech-start').click();page.wait_for_function('speechRecord && speechRecord.id')
                page.locator('#speech-cancel').click();page.wait_for_function('!speechRecord')
                assert not errors,errors
                page.screenshot(path='/tmp/kadr-v17-ui.png',full_page=True)
                browser.close()
            print('PASS: Chromium; empty project; voice upload; auto/manual recognition; reload; cancel; model error; late result isolation; real transcript API; exact SRT timing. Recognition model responses were mocked.')
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
