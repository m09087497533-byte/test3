"""Exercise the actual 540p player, native seeking, revision tracking and cache."""
import io
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
screens=root/'test-results';screens.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='kadr-player-') as temp:
    work=Path(temp)
    env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data'))
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(root/'.venv/bin/python'),'run.py','--no-browser','--port','8002'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8002/api/health',timeout=1).close();break
                except OSError:
                    if server.poll() is not None:log.seek(0);raise RuntimeError(log.read())
                    time.sleep(.1)
            else:raise RuntimeError('Server not ready')
            voice=io.BytesIO()
            with wave.open(voice,'wb') as out:
                out.setparams((1,2,16000,0,'NONE','not compressed'))
                out.writeframes(b''.join(struct.pack('<h',round(5000*math.sin(i*2*math.pi*220/16000))) for i in range(80000)))
            photo=io.BytesIO();Image.new('RGB',(800,600),'#3b6954').save(photo,'PNG')
            clip=work/'clip.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','2','-c:v','libx264','-threads','2',str(clip)],check=True)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1200})
                errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:requests.append(r.url) if r.method=='POST' and r.url.endswith('/api/playback') else None)
                page.goto('http://127.0.0.1:8002')
                page.wait_for_function("document.querySelector('#scene-preview').naturalWidth===960")
                def upload(name,data,mime):
                    response=page.request.post('http://127.0.0.1:8002/api/assets',multipart={'file':{'name':name,'mimeType':mime,'buffer':data}})
                    assert response.ok,response.text();return response.json()['id']
                voice_id=upload('voice.wav',voice.getvalue(),'audio/wav');photo_id=upload('photo.png',photo.getvalue(),'image/png');clip_id=upload('clip.mp4',clip.read_bytes(),'video/mp4')
                project={'director_version':12,'name':'Просмотр монтажа','version':1,'resolution':'1080p','transition':'mixed','voice_id':voice_id,'music_volume':.15,
                         'sound_mode':'auto','sounds_per_minute':4,'auto_media_after_subtitles':False,
                         'scenes':[{'title':'ПЕРВАЯ СЦЕНА','duration':1.5,'asset_id':photo_id,'title_effect':'rise'},
                                   {'title':'ВТОРАЯ СЦЕНА','duration':1.5,'asset_id':photo_id,'template':'archive'},
                                   {'title':'ВИДЕОФУТАЖ','duration':2,'asset_id':clip_id,'title_effect':'perspective'}],
                         'subtitles':[{'start':.1,'end':1.4,'text':'Проверка первой сцены'},{'start':1.5,'end':2.9,'text':'Следующая мысль'},{'start':3,'end':4.9,'text':'Движение в кадре'}]}
                validated=page.request.post('http://127.0.0.1:8002/api/project/validate',data=project)
                assert validated.ok,validated.text()
                page.evaluate('(p)=>localStorage.setItem("kadr-project-v16",JSON.stringify(p))',validated.json());page.reload()
                page.wait_for_function("document.querySelector('#scene-title').value==='ПЕРВАЯ СЦЕНА'")
                # New film controls persist, hide caption overlays and apply real transitions.
                page.locator('#transcript').evaluate('(d)=>d.open=true')
                page.locator('#cue-list .cue').first.click()
                assert page.locator('#preview-subtitle').inner_text()=='Проверка первой сцены'
                page.locator('#show-subtitles').uncheck()
                assert page.evaluate('project.show_subtitles') is False
                assert page.locator('#preview-subtitle').inner_text()==''
                page.locator('#cue-list .cue').first.click()
                assert page.locator('#preview-subtitle').inner_text()==''
                assert len(page.evaluate('project.subtitles'))==3
                page.locator('#show-subtitles').check()
                page.locator('#photo-motion').select_option('smooth')
                assert page.evaluate('project.photo_motion')=='smooth'
                auto_id='f'*32
                picked=page.evaluate('(id)=>assets.find(a=>a.id===id)',photo_id)
                page.route('**/api/auto-media',lambda route:route.fulfill(status=202,content_type='application/json',body=json.dumps({'id':auto_id,'status':'queued'})))
                page.route('**/api/auto-media/'+auto_id,lambda route:route.fulfill(content_type='application/json',body=json.dumps({'id':auto_id,'status':'done','progress':100,'message':'Готово','assignments':[{'scene_index':0,'asset':picked,'offset':0,'reason':'Фото · проверено'}],'outcomes':[],'errors':[]})))
                page.locator('#reference-style').click()
                page.wait_for_function("!document.querySelector('#reference-style').disabled")
                assert page.evaluate('project.director_version')==12
                assert page.evaluate('project.scenes.length')==1
                assert page.evaluate('project.photo_motion')=='static'
                page.locator('#undo-rhythm').click()
                assert page.evaluate('project.photo_motion')=='smooth'
                assert page.locator('#undo-rhythm').is_hidden()
                # Restore the fixture to keep the remainder's playback timing expectations.
                page.evaluate('(p)=>localStorage.setItem("kadr-project-v16",JSON.stringify(p))',validated.json());page.reload()
                page.wait_for_function("document.querySelector('#scene-title').value==='ПЕРВАЯ СЦЕНА'")
                page.locator('#watch-montage').click()
                page.wait_for_function("document.querySelector('#montage-player').readyState>=1 && !document.querySelector('#montage-player').classList.contains('hidden')",timeout=90000)
                video=page.locator('#montage-player')
                assert abs(video.evaluate('(v)=>v.duration')-5)<.1
                assert not video.evaluate('(v)=>v.muted')
                first_src=video.get_attribute('src');assert len(requests)==1
                video.evaluate('(v)=>v.pause()');page.locator('#watch-montage').click()
                page.wait_for_function("document.querySelector('#montage-player').currentTime>.2 && !document.querySelector('#montage-player').paused")
                video.evaluate('(v)=>{v.pause();v.currentTime=2;}')
                page.wait_for_function("document.querySelector('#playing-scene').textContent.includes('Сцена 2')")
                assert 'playing' in page.locator('.scene-card').nth(1).get_attribute('class')
                page.locator('#edit-playing-scene').click()
                assert video.is_hidden();assert page.locator('#scene-title').input_value()=='ВТОРАЯ СЦЕНА'
                page.locator('#watch-scene').click()
                page.wait_for_function("document.querySelector('#montage-player').currentTime>=1.5")
                assert len(requests)==1
                video.evaluate('(v)=>v.pause()');page.reload()
                page.wait_for_function("document.querySelector('#montage-player').readyState>=1 && !document.querySelector('#montage-player').classList.contains('hidden')")
                assert video.get_attribute('src')==first_src;assert len(requests)==1
                assert video.evaluate('(v)=>v.paused')
                page.locator('#edit-frame').click()
                page.locator('#scene-title').fill('НОВЫЙ КАДР')
                assert 'Обновить' in page.locator('#watch-montage').inner_text()
                page.locator('#watch-montage').click()
                # Edit again while the old snapshot is being prepared. It must not autoplay as current.
                page.locator('#scene-title').fill('ЕЩЁ ОДНА ПРАВКА')
                page.wait_for_function("!document.querySelector('#watch-montage').disabled && document.querySelector('#watch-montage').textContent.includes('Обновить')",timeout=90000)
                assert video.is_hidden()
                page.locator('#watch-montage').click()
                page.wait_for_function("document.querySelector('#montage-player').readyState>=1 && !document.querySelector('#montage-player').classList.contains('hidden')",timeout=90000)
                assert video.get_attribute('src')!=first_src;assert len(requests)==3
                page.locator('#timeline .scene-card').nth(2).click()
                page.wait_for_function("document.querySelector('#montage-player').currentTime>=3")
                assert page.locator('#scene-title').input_value()=='ВИДЕОФУТАЖ'
                video.evaluate('(v)=>v.pause()')
                page.screenshot(path=str(screens/'playback-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.screenshot(path=str(screens/'playback-mobile.png'),full_page=True)
                # A real renderer failure must be visible and downloadable, not swallowed.
                (work/'data'/'assets'/photo_id/'image.jpg').write_bytes(b'invalid image fixture')
                page.evaluate("project.scenes[0].title='Проверка отчёта';save();")
                page.locator('#watch-montage').click()
                page.locator('#playback-error-details:not(.hidden)').wait_for(timeout=90000)
                assert 'Сцена 1' in page.locator('#playback-error').inner_text()
                assert 'UnidentifiedImageError' in page.locator('#playback-error').inner_text()
                with page.expect_download() as download:
                    page.locator('#download-playback-error').click()
                download.value.save_as(work/'error-report.txt')
                assert 'UnidentifiedImageError' in (work/'error-report.txt').read_text()
                assert str(work) not in (work/'error-report.txt').read_text() or '<данные>' in (work/'error-report.txt').read_text()
                assert not errors,errors
                browser.close()
                print('PASS: actual 540p movie, audio-enabled player, play/pause, seeking, scene highlight/edit, cached reload, dirty tracking, late snapshot guard, regeneration and responsive layout. No JS errors.')
        finally:
            server.terminate();server.wait(timeout=15)
