"""Four-step creation in Chromium with real editing, preview, MP4 and reloads.
Only ASR model output and external stock search are mocked.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import wave
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8021'
with tempfile.TemporaryDirectory(prefix='kadr20-workflow-') as tmp:
    work=Path(tmp)
    voice=work/'voice.wav'
    with wave.open(str(voice),'wb') as wav:
        wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*9*16000)
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'run.py','--no-browser','--port','8021'],cwd=ROOT,
          env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data')),stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1080});errors=[];starts=[];searches=[];renders=[];previews=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                job='d'*32;stockjob='e'*32
                speech_state=dict(id=job,status='running',progress=20,message='Распознаём речь…')
                stock_state=dict(id=stockjob,status='running',progress=20,message='Ищем кадры…',assignments=[],outcomes=[],fallbacks=[],errors=[])
                def speech(route):
                    if route.request.method=='POST':starts.append(route.request.post_data_json);route.fulfill(status=202,json=speech_state)
                    elif route.request.url.endswith('/'+job):route.fulfill(json=speech_state)
                    else:route.fulfill(json={'available':True})
                def stock(route):
                    if route.request.method=='POST':searches.append(route.request.post_data_json);route.fulfill(status=202,json=stock_state)
                    else:route.fulfill(json=stock_state)
                page.route('**/api/transcription',speech);page.route('**/api/transcription/**',speech)
                page.route('**/api/auto-media',stock);page.route('**/api/auto-media/**',stock)
                def requests(req):
                    if req.method=='POST' and req.url.endswith('/api/render'):renders.append(req.url)
                    if req.method=='POST' and req.url.endswith('/api/playback'):previews.append(req.url)
                page.on('request',requests)
                page.goto(BASE);page.wait_for_function('workflowInitialized && speechAvailable')
                assert page.locator('#step-1').is_visible() and page.locator('#step-3').is_hidden()
                assert page.locator('#workflow-next').is_disabled()
                assert not searches
                page.screenshot(path='/tmp/kadr20-step1.png',full_page=True)
                page.locator('#file-voice').set_input_files(voice)
                page.wait_for_function('speechRecord && speechRecord.id')
                assert len(starts)==1 and page.locator('#workflow-next').is_disabled()
                page.reload();page.wait_for_function('speechRecord && speechRecord.id')
                assert len(starts)==1
                raw=page.evaluate('structuredClone(project)');raw['scenes'][0]['duration']=9
                raw['subtitles']=[dict(start=4.49,end=7.4,text='История начинается с одного открытия.')]
                response=page.request.post(BASE+'/api/project/reference-rhythm',data=raw)
                assert response.ok,response.text()
                directed=response.json()
                speech_state.update(status='done',progress=100,message='Речь распознана',result={k:directed[k] for k in ('scenes','subtitles','director_version','effects_version','photo_motion')})
                page.wait_for_function('project.workflow_step===2 && project.subtitles.length===1 && !speechRecord')
                assert page.locator('#step-2').is_visible() and not searches,'Text review was skipped'
                assert page.locator('#scene-editor').is_hidden()
                page.locator('#edit-transcript').click()
                page.locator('#transcript-fields textarea').fill('История начинается с нового открытия.')
                page.locator('#transcript-apply').click()
                page.wait_for_function("project.subtitles[0].text.includes('нового')")
                assert page.evaluate('project.subtitles[0].start')==4.49
                assert page.evaluate('project.subtitles[0].end')==7.4
                assert page.evaluate('project.workflow_step')==2
                page.screenshot(path='/tmp/kadr20-step2.png',full_page=True)
                page.reload();page.wait_for_function('workflowInitialized && speechAvailable')
                assert page.locator('#step-2').is_visible() and not searches
                # Two local source images; no external service or keys are needed.
                uploaded=[]
                for n,color in enumerate(('#b19a70','#879f8e')):
                    image=Image.new('RGB',(640,360),color);draw=ImageDraw.Draw(image)
                    for y in range(35,340,24):draw.line((50,y,580,y),fill='#574938',width=3)
                    path=work/f'frame{n}.png';image.save(path)
                    r=page.request.post(BASE+'/api/assets',multipart={'file':{'name':path.name,'mimeType':'image/png','buffer':path.read_bytes()}})
                    assert r.ok,r.text();uploaded.append(r.json())
                page.locator('#workflow-next').click()
                page.wait_for_function('project.workflow_step===3 && !!autoMediaTask')
                assert len(searches)==1
                assert not page.locator('#scene-editor').get_attribute('open')
                page.reload();page.wait_for_function('workflowInitialized && !!autoMediaTask')
                assert len(searches)==1,'Reload duplicated the stock search'
                scenes=page.evaluate('structuredClone(project.scenes)')
                assignments=[]
                for i,scene in enumerate(scenes):
                    assignments.append({'scene_index':i,'asset':uploaded[i%2], 'offset':0,'reason':'','query':''})
                stock_state.update(status='done',progress=100,message='Кадры готовы',assignments=assignments,
                    outcomes=[{'scene_index':i,'status':'assigned','reason':'Локальный тестовый кадр'} for i in range(len(scenes))])
                page.wait_for_function('!autoMediaTask && project.scenes.every(s=>!!s.asset_id)')
                page.screenshot(path='/tmp/kadr20-step3.png',full_page=True)
                page.locator('#timeline .scene-card').nth(1).click()
                assert page.locator('#scene-title').is_visible(),'Scene editor did not open from timeline'
                page.locator('#scene-editor summary').click()
                page.locator('#workflow-next').click()
                page.wait_for_function('project.workflow_step===4')
                assert page.locator('#export').is_visible() and page.locator('#scene-title').is_hidden()
                assert page.locator('#workflow-missing').is_hidden()
                page.screenshot(path='/tmp/kadr20-step4.png',full_page=True)
                page.locator('#watch-montage').click()
                page.wait_for_function('playback.ready && !playback.busy',timeout=120000)
                page.wait_for_function('document.querySelector("#montage-player").duration===9')
                assert len(previews)==1
                signature=page.evaluate('playbackSignature()')
                page.locator('[data-step="2"]').click()
                assert page.evaluate('document.querySelector("#montage-player").paused')
                assert page.evaluate('playbackSignature()')==signature,'Step navigation invalidated the video'
                page.locator('[data-step="4"]').click()
                page.locator('#watch-montage').click()
                assert len(previews)==1,'Step navigation rebuilt an unchanged preview'
                page.locator('#export').click()
                page.wait_for_function("!document.querySelector('#download-video').classList.contains('hidden')",timeout=120000)
                href=page.locator('#download-video').get_attribute('href')
                movie=work/'film.mp4';movie.write_bytes(page.request.get(BASE+href).body())
                probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(movie)]))
                video=next(s for s in probe['streams'] if s['codec_type']=='video')
                assert int(video['nb_frames'])==270,video
                assert float(video['duration'])==9,video
                assert any(s['codec_type']=='audio' for s in probe['streams'])
                subprocess.run(['ffmpeg','-v','error','-i',str(movie),'-f','null','-'],check=True,stdout=subprocess.DEVNULL)
                assert len(renders)==1
                page.locator('#close-export').click()
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.locator('[data-step="1"]').click()
                page.screenshot(path='/tmp/kadr20-mobile.png',full_page=True)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.locator('#new-project').click()
                page.wait_for_function('project.workflow_step===1 && !project.voice_id && !project.subtitles.length')
                assert page.locator('#step-1').is_visible()
                # Manual SRT remains a valid entry, including with auto-search off.
                page.evaluate('project.auto_media_after_subtitles=false;save()')
                srt=work/'manual.srt';srt.write_text('1\n00:00:04,490 --> 00:00:07,400\nИстория начинается с одного открытия.\n')
                page.locator('#file-srt').set_input_files(srt)
                page.wait_for_function('project.workflow_step===2 && project.subtitles.length===1')
                page.locator('#workflow-next').click()
                page.wait_for_function('project.workflow_step===3')
                assert len(searches)==1,'Disabled automatic search started'
                page.locator('#workflow-next').click()
                assert page.locator('#workflow-missing').is_visible()
                page.locator('#export').click()
                page.wait_for_function('project.workflow_step===3')
                assert len(renders)==1,'Incomplete montage was exported'
                page.locator('#timeline .scene-card').first.click()
                page.locator('#replace-frame').click()
                assert page.locator('#upload-visual').is_visible(),'Replacement library stayed collapsed'
                page.locator('#connect-sources').click()
                assert page.locator('#pexels-key').is_visible(),'Source key fields stayed collapsed'
                assert not errors,errors
                browser.close()
            print('PASS: four visible steps; ASR boundary result; exact cue editing; deferred automatic media; job reloads without duplicates; scene editor; preview reuse; real 9s / 270-frame MP4 with audio; mobile; new project. ASR and stock responses mocked.')
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
