"""Real Chromium review of an incomplete montage, replacement and partial audio.
No model or stock service is invoked; the assets and playback API are real.
"""
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
import wave
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8023'
with tempfile.TemporaryDirectory(prefix='kadr21-review-ui-') as tmp:
    root=Path(tmp)
    with (root/'server.log').open('w') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'run.py','--no-browser','--port','8023'],cwd=ROOT,
            env=dict(os.environ,STUDIO_DATA_DIR=str(root/'data')),stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1080});errors=[];requests=[];upgrades=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                page.on('request',lambda r:requests.append(r.url) if r.method=='POST' and '/api/playback' in r.url else None)
                page.on('request',lambda r:upgrades.append(r.url) if r.method=='POST' and r.url.endswith('/api/project/color-transitions') else None)
                page.goto(BASE);page.wait_for_function('workflowInitialized && speechAvailable')
                uploaded=[]
                for name,color in [('Первый.png','#8c9673'),('Третий.png','#ab956e'),('Замена.png','#759db1')]:
                    img=Image.new('RGB',(640,360),color);draw=ImageDraw.Draw(img)
                    for y in range(35,340,24):draw.line((60,y,580,y),fill='#4e5245',width=3)
                    raw=io.BytesIO();img.save(raw,'PNG')
                    r=page.request.post(BASE+'/api/assets',multipart={'file':{'name':name,'mimeType':'image/png','buffer':raw.getvalue()}})
                    assert r.ok,r.text();uploaded.append(r.json())
                raw=io.BytesIO()
                with wave.open(raw,'wb') as f:
                    f.setparams((1,2,8000,0,'NONE','not compressed'))
                    f.writeframes(b''.join(struct.pack('<h',round(6000*math.sin(2*math.pi*(440,880,1320)[i//16000]*i/8000))) for i in range(48000)))
                r=page.request.post(BASE+'/api/assets',multipart={'file':{'name':'Озвучка.wav','mimeType':'audio/wav','buffer':raw.getvalue()}})
                assert r.ok,r.text();voice=r.json()
                # A v20 project gains only transitions, once; manual edits survive.
                page.evaluate('''({uploaded,voice})=>{
                    assets.push(...uploaded,voice);const blank=structuredClone(project.scenes[0]);
                    project.scenes=uploaded.map(a=>({...blank,duration:6,asset_id:a.id,camera_style:'pull',title:'МОЙ ТЕКСТ',title_effect:'stamp',title_sound:'spark',transition_out:'cut'}));
                    project.voice_id=voice.id;project.music_id=voice.id;project.sound_mode='manual';project.music_volume=.24;
                    project.workflow_step=3;project.transitions_version=0;project.auto_media_after_subtitles=false;save();
                }''',{'uploaded':uploaded,'voice':voice})
                def core(value):
                    value=dict(value);value.pop('transitions_version',None)
                    value['scenes']=[{k:v for k,v in s.items() if k not in ('transition_out','transition_duration')} for s in value['scenes']]
                    return value
                before=page.evaluate('structuredClone(project)')
                page.reload();page.wait_for_function('workflowInitialized && project.transitions_version===1 && speechAvailable')
                assert len(upgrades)==1 and core(page.evaluate('structuredClone(project)'))==core(before)
                assert page.evaluate("!!localStorage.getItem(colorBackupStorage)")
                page.reload();page.wait_for_function('workflowInitialized && speechAvailable')
                assert len(upgrades)==1,'Color upgrade repeated after reload'
                page.locator('[data-step="4"]').click();page.locator('#film-settings summary').click()
                page.locator('#undo-color-transitions').click()
                assert page.evaluate('project.scenes.every(s=>s.transition_out==="cut")')
                assert core(page.evaluate('structuredClone(project)'))=={**core(before),'workflow_step':4}
                page.evaluate('''({uploaded,voice})=>{
                    project.name='Проверка кадров';project.voice_id=voice.id;project.music_id=null;project.sound_mode='auto';
                    project.auto_music=false;project.semantic_media=false;project.auto_media_after_subtitles=false;project.library_since=0;
                    const blank=structuredClone(project.scenes[0]);
                    project.scenes=[{...blank,duration:2,asset_id:uploaded[0].id,transition_out:'light_leak',transition_duration:.6},
                                    {...blank,duration:2,asset_id:null}, {...blank,duration:2,asset_id:uploaded[1].id}];
                    project.subtitles=[{start:.25,end:1.75,text:'Первый документ'},{start:2.25,end:3.75,text:'Второй документ'},{start:4.25,end:5.75,text:'Третий документ'}];
                    project.workflow_step=3;project.workflow_media_pending=false;selected=0;refresh();
                }''',{'uploaded':uploaded,'voice':voice})
                original=page.evaluate('structuredClone(project)')
                page.locator('#review-frames').click()
                assert page.locator('#frames-grid article').count()==2
                assert page.locator('#frames-grid article[data-scene-index="2"]').is_visible()
                page.screenshot(path='/tmp/kadr21-gallery.png',full_page=True)
                page.locator('[data-frame-filter="missing"]').click()
                assert page.locator('#frames-grid article').count()==1
                assert page.locator('#frames-grid article').get_attribute('data-scene-index')=='1'
                page.locator('[data-frame-filter="all"]').click();assert page.locator('#frames-grid article').count()==3
                page.locator('[data-frame-filter="ready"]').click()
                page.locator('#frames-grid article[data-scene-index="2"]').get_by_role('button',name='Смотреть сцену').click()
                page.wait_for_function('playback.ready && !playback.busy',timeout=120000)
                assert page.evaluate('playback.record.selection')==[2]
                page.wait_for_function('document.querySelector("#montage-player").duration===2')
                assert page.evaluate('markPlayingScene()')==2
                assert page.evaluate('project.subtitles')==original['subtitles']
                assert page.evaluate('duration()')==6
                page.locator('#edit-playing-scene').click()
                assert page.evaluate('selected')==2 and page.locator('#scene-title').is_visible()
                page.locator('#replace-frame').click()
                page.locator('#asset-grid button[title="Замена.png"]').click()
                assert page.evaluate('project.scenes[2].asset_id')==uploaded[2]['id']
                assert page.evaluate('project.scenes[0].asset_id')==uploaded[0]['id']
                assert page.evaluate('project.voice_id')==voice['id']
                page.locator('#scene-editor summary').click()
                page.locator('#step-3 [data-watch-available]').click()
                page.wait_for_function('playback.ready && !playback.busy && playback.record.selection.length===2',timeout=120000)
                page.wait_for_function('document.querySelector("#montage-player").duration===4')
                assert page.evaluate('playback.record.selection')==[0,2]
                page.evaluate('document.querySelector("#montage-player").pause();document.querySelector("#montage-player").currentTime=2.5')
                page.wait_for_function('markPlayingScene()===2')
                page.screenshot(path='/tmp/kadr21-partial.png',full_page=True)
                identifier=page.evaluate('playback.record.id');count=len(requests)
                page.reload();page.wait_for_function('playback.ready && !playback.busy',timeout=120000)
                assert page.evaluate('playback.record.id')==identifier and len(requests)==count
                assert page.evaluate('project.subtitles')==original['subtitles']
                page.locator('#watch-montage').click()
                assert page.evaluate('project.workflow_step')==3
                assert len(requests)==count,'Incomplete full montage should use the partial preview button'
                # Fill the missing scene through the gallery; only it changes.
                page.locator('#review-frames').click();page.locator('[data-frame-filter="missing"]').click()
                page.locator('#frames-grid article').get_by_role('button',name='Добавить кадр').click()
                assert page.evaluate('selected')==1
                page.locator('#asset-grid button[title="Третий.png"]').click()
                assert page.evaluate('missingVisuals().length')==0
                page.locator('[data-step="4"]').click();page.locator('#watch-montage').click()
                page.wait_for_function('playback.ready && !playback.busy && playback.record.selection===null',timeout=120000)
                page.wait_for_function('document.querySelector("#montage-player").duration===6')
                assert page.evaluate('project.subtitles')==original['subtitles']
                # Full and partial playback have distinct cache identities.
                assert page.evaluate('playback.record.id')!=identifier
                movie=root/'complete.mp4';movie.write_bytes(page.request.get(BASE+'/api/playback/'+page.evaluate('playback.record.id')+'/video').body())
                probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(movie)]))
                assert int(next(s for s in probe['streams'] if s['codec_type']=='video')['nb_frames'])==180
                page.locator('[data-step="3"]').click();page.locator('#review-frames').click()
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.screenshot(path='/tmp/kadr21-gallery-mobile.png',full_page=True)
                assert not errors,errors
                browser.close()
            print('PASS: filled/missing/all gallery; correct original scene mapping; real single-scene and non-contiguous playback with audio; reload; replace and fill only selected scene; immutable voice/cue clocks; partial vs full caches; 180-frame complete montage; mobile. No stock/model calls.')
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
