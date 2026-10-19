"""Optional browser smoke test; uses an isolated server and temporary media."""
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
screens = root / 'test-results'
screens.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='kadr-ui-') as work:
    work = Path(work)
    env = dict(os.environ, STUDIO_DATA_DIR=str(work / 'data'))
    python = str(root / '.venv/bin/python') if (root / '.venv/bin/python').exists() else sys.executable
    with (work / 'server.log').open('w+') as log:
        server = subprocess.Popen([python, 'run.py', '--no-browser', '--port', '8001'], cwd=root, env=env, stdout=log, stderr=log)
        try:
            for attempt in range(100):
                try:
                    urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(.1)
            else:
                raise RuntimeError('Server did not become ready')
            with wave.open(str(work/'voice.wav'), 'wb') as out:
                out.setparams((1,2,16000,0,'NONE','not compressed'))
                out.writeframes(b''.join(struct.pack('<h',round(5000*math.sin(i*2*math.pi*440/16000))) for i in range(48000)))
            Image.new('RGB',(640,360),'#385a4b').save(work/'still.png')
            (work/'captions.srt').write_text('1\n00:00:00,200 --> 00:00:01,500\nПервое слово\n\n2\n00:00:02,000 --> 00:00:02,800\nИстория продолжается', encoding='utf-8')
            with sync_playwright() as p:
                executable = os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
                browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.on('dialog', lambda dialog: dialog.accept())
                page.goto('http://127.0.0.1:8001')
                page.wait_for_function("document.querySelector('#scene-preview').naturalWidth === 960")
                assert page.locator('.scene-card').count() == 1
                page.locator('#auto-after-subtitles').uncheck()
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.screenshot(path=str(screens/'desktop.png'),full_page=True)
                page.locator('#sound-mode').select_option('manual')
                page.locator('#title-effect').select_option('slide')
                page.locator('#title-delay').fill('0.5')
                page.locator('#title-delay').dispatch_event('change')
                page.locator('#title-sound').select_option('click')
                page.reload()
                page.wait_for_function("document.querySelector('#scene-preview').naturalWidth === 960")
                assert page.locator('#title-effect').input_value() == 'slide'
                assert page.locator('#title-delay').input_value() == '0.5'
                assert page.locator('#title-sound').input_value() == 'click'
                page.locator('#file-voice').set_input_files(work/'voice.wav')
                page.locator('#voice-info:not(.hidden)').wait_for()
                page.locator('#file-srt').set_input_files(work/'captions.srt')
                page.wait_for_function("project.subtitles.length === 2 && document.querySelectorAll('.scene-card').length === 1")
                assert '2 фраз' in page.locator('#subtitle-info').inner_text()
                vtt = work/'captions.vtt'
                vtt.write_text('WEBVTT\n\n'+(work/'captions.srt').read_text().replace(',', '.'), encoding='utf-8')
                assert '.vtt' in page.locator('#file-srt').get_attribute('accept')
                with page.expect_response('**/api/subtitles') as vtt_response:
                    page.locator('#file-srt').set_input_files(vtt)
                assert vtt_response.value.status == 200
                assert len(vtt_response.value.json()['subtitles']) == 2
                page.locator('#file-visual').set_input_files(work/'still.png')
                page.locator('.asset.active').wait_for()
                page.locator('[data-template="statement"]').click()
                page.locator('#scene-title').fill('СИЛА СЛОВА')
                page.locator('#sound-mode').select_option('manual')
                page.locator('#transition').select_option('mixed')
                page.locator('#transition-sounds').check()
                page.locator('#animate-titles').check()
                page.locator('#scene-motion').check()
                page.locator('#motion-style').select_option('pull')
                page.locator('#scene-transition').select_option('blur')
                page.locator('#duplicate').click()
                assert page.locator('.scene-card').count() == 2
                page.locator('#move-left').click()
                page.locator('#delete-scene').click()
                assert page.locator('.scene-card').count() == 1
                page.reload()
                page.wait_for_function("document.querySelector('#scene-title').value === 'СИЛА СЛОВА'")
                with page.expect_download() as download_event:
                    page.locator('#save-project').click()
                download_event.value.save_as(work/'saved.json')
                saved = json.loads((work/'saved.json').read_text())
                assert len(saved['subtitles']) == 2 and saved['voice_id']
                assert saved['transition_sounds'] and saved['animate_titles']
                assert saved['scenes'][0]['transition_out'] == 'blur'
                page.locator('#file-project').set_input_files(work/'saved.json')
                page.locator('#export').click()
                page.locator('#download-video:not(.hidden)').wait_for(timeout=90000)
                page.wait_for_function("document.querySelector('#result-video').readyState >= 1")
                assert abs(page.locator('#result-video').evaluate('(v)=>v.duration')-3) < .15
                assert page.locator('#export-heading').inner_text() == 'Твоя история готова'
                page.locator('#close-export').click()
                # Isolate provider I/O while exercising the real automatic director.
                profile_job='e'*32
                page.route('**/api/auto-media',lambda route:route.fulfill(status=202,content_type='application/json',body=json.dumps({'id':profile_job,'status':'queued'})))
                page.route('**/api/auto-media/'+profile_job,lambda route:route.fulfill(content_type='application/json',body=json.dumps({'id':profile_job,'status':'done','progress':100,'message':'Готово','assignments':[],'outcomes':[],'errors':[]})))
                page.locator('#reference-style').click()
                page.wait_for_function("!document.querySelector('#reference-style').disabled")
                assert page.locator('#sound-mode').input_value() == 'auto'
                assert not page.locator('#title-sound').is_visible()
                page.wait_for_function("document.querySelector('#sound-plan').textContent.includes('0 звуковых')")
                page.locator('#title-effect').select_option('perspective')
                page.locator('#title-decoration').select_option('ring')
                page.reload()
                page.wait_for_function("document.querySelector('#scene-preview').naturalWidth === 960")
                assert page.locator('#title-effect').input_value() == 'perspective'
                assert page.locator('#title-decoration').input_value() == 'ring'
                page.route('**/api/images/search', lambda route: route.fulfill(status=502,content_type='application/json',body=json.dumps({'detail':'Нет доступа к Wikimedia Commons'})))
                page.locator('#manual-photos').evaluate('(d)=>d.open=true')
                page.unroute('**/api/auto-media')
                page.unroute('**/api/auto-media/'+profile_job)
                page.locator('#search-images').click()
                page.wait_for_function("document.querySelector('#image-status').textContent.includes('Нет доступа')")
                page.unroute('**/api/images/search')
                item={'token':'a'*32,'title':'Test manuscript','url':'/static/favicon.svg','source_url':'https://commons.wikimedia.org/wiki/File:Test.jpg','license':'CC BY-SA 4.0'}
                page.route('**/api/images/search', lambda route: route.fulfill(content_type='application/json',body=json.dumps({'concept':'Рукопись','query':'manuscript','results':[item]})))
                asset=page.evaluate("assets.find(a=>a.kind==='image')")
                page.route('**/api/images/import', lambda route: route.fulfill(content_type='application/json',body=json.dumps(asset)))
                page.locator('#manual-photos').evaluate('(d)=>d.open=true')
                page.locator('#search-images').click()
                page.locator('.search-card button').wait_for()
                assert 'CC BY-SA' in page.locator('.search-card a').inner_text()
                page.locator('.search-card button').click()
                page.wait_for_function("document.querySelector('#toast').textContent.includes('Изображение добавлено')")
                page.locator('#manual-videos').evaluate('(d)=>d.open=true')
                page.locator('#search-videos').click()
                page.wait_for_function("document.querySelector('#video-status').textContent.includes('API-ключ')")
                page.get_by_text('Подключить Pexels / Pixabay',exact=True).click()
                page.locator('#pexels-key').fill('test-only-not-a-real-key')
                page.locator('#save-stock-keys').click()
                page.wait_for_function("document.querySelector('#stock-key-status').textContent.includes('Pexels: ключ добавлен')")
                assert page.locator('#pexels-key').input_value()==''
                assert 'test-only-not-a-real-key' not in page.evaluate("localStorage.getItem('kadr-project-v13')")
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','2','-c:v','libx264','-threads','2',str(work/'footage.mp4')],check=True)
                video_asset=page.request.post('http://127.0.0.1:8001/api/assets',multipart={'file':{'name':'footage.mp4','mimeType':'video/mp4','buffer':(work/'footage.mp4').read_bytes()}}).json()
                footage={'token':'b'*32,'provider':'pexels','title':'Footage','duration':2,'width':320,'height':180,'preview_url':'/api/assets/'+video_asset['id']+'/file','thumbnail':'','source_url':'https://www.pexels.com/video/test-1/','author':'Author'}
                page.route('**/api/videos/search',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'query':'old book','results':[footage]})))
                page.route('**/api/videos/import',lambda route:route.fulfill(content_type='application/json',body=json.dumps(video_asset)))
                page.locator('#manual-videos').evaluate('(d)=>d.open=true')
                page.locator('#search-videos').click()
                page.locator('#video-results video').wait_for()
                page.locator('#video-results video').evaluate('(v)=>{v.preload="auto";v.load();}')
                page.wait_for_function("document.querySelector('#video-results video').readyState>=1")
                page.locator('#video-results button').click()
                page.wait_for_function("document.querySelector('#scene-source').textContent.includes('footage.mp4')")
                assert page.locator('#scene-offset').is_enabled()
                page.locator('#remove-stock-key').click()
                page.wait_for_function("document.querySelector('#stock-key-status').textContent.includes('Pexels: нужен API-ключ')")
                # Whole-montage automation: assign, resume after reload, and trigger from VTT import.
                auto_requests=[]
                auto_reads=[0]
                auto_id='c'*32
                assignment={'scene_index':0,'asset':asset,'offset':0,'reason':'Фото · Рукопись · из библиотеки','query':'old book'}
                def start_arrangement(route):
                    auto_requests.append(route.request.post_data_json)
                    route.fulfill(status=202,content_type='application/json',body=json.dumps({'id':auto_id,'status':'queued'}))
                def read_arrangement(route):
                    auto_reads[0]+=1
                    done=auto_reads[0]>=2
                    route.fulfill(content_type='application/json',body=json.dumps({'id':auto_id,'status':'done' if done else 'running','progress':100 if done else 50,'message':'Подбор завершён' if done else 'Подбираем материалы…','assignments':[assignment],'outcomes':[{'scene_index':0,'status':'assigned','reason':assignment['reason']}],'errors':[]}))
                page.route('**/api/auto-media',start_arrangement)
                page.route('**/api/auto-media/'+auto_id,read_arrangement)
                page.locator('#replace-media').check()
                page.locator('#arrange-media').click()
                page.wait_for_function("document.querySelector('#scene-match').textContent.includes('Рукопись')")
                page.reload()
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('Подбор завершён')")
                assert page.evaluate("project.scenes[0].asset_id")==asset['id']
                assert 'Фото' in page.locator('.media-badge').inner_text()
                assert page.locator('#arrange-media').is_enabled()
                page.locator('#auto-after-subtitles').check()
                with page.expect_request('**/api/auto-media'):
                    page.locator('#file-srt').set_input_files(work/'captions.vtt')
                page.wait_for_function("document.querySelector('#scene-title').value === '' && document.querySelector('#scene-match').textContent.includes('Рукопись')")
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('Подбор завершён')")
                assert len(auto_requests)==2
                assert auto_requests[0]['replace_existing'] and auto_requests[0]['mode']=='mixed'
                assert not auto_requests[1]['replace_existing']
                # Editing a scene while the picker works must win over its late result.
                page.unroute('**/api/auto-media/'+auto_id)
                manual_reads=[0]
                replacement={**assignment,'asset':video_asset,'reason':'Видео · Путь · Pexels'}
                def read_late_arrangement(route):
                    manual_reads[0]+=1
                    done=manual_reads[0]>=2
                    route.fulfill(content_type='application/json',body=json.dumps({'id':auto_id,'status':'done' if done else 'running','progress':100 if done else 10,'message':'Ждём результат поиска','assignments':[replacement] if done else [],'outcomes':[{'scene_index':0,'status':'assigned','reason':replacement['reason']}] if done else [],'errors':[]}))
                page.route('**/api/auto-media/'+auto_id,read_late_arrangement)
                page.locator('#replace-media').check()
                page.locator('#arrange-media').click()
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('Ждём')")
                page.locator('#scene-title').fill('Ручная правка')
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('Сохранены ручные изменения: 1')")
                assert page.evaluate("project.scenes[0].asset_id")==asset['id']
                assert page.locator('#scene-title').input_value()=='Ручная правка'
                page.unroute('**/api/auto-media/'+auto_id)
                cancelled=[False]
                def read_cancellable_arrangement(route):
                    route.fulfill(content_type='application/json',body=json.dumps({'id':auto_id,'status':'cancelled' if cancelled[0] else 'running','progress':10,'message':'Подбор остановлен' if cancelled[0] else 'Работаем','assignments':[],'outcomes':[],'errors':[]}))
                def stop_arrangement(route):
                    cancelled[0]=True
                    route.fulfill(content_type='application/json',body=json.dumps({'status':'cancelling'}))
                page.route('**/api/auto-media/'+auto_id,read_cancellable_arrangement)
                page.route('**/api/auto-media/'+auto_id+'/cancel',stop_arrangement)
                page.locator('#arrange-media').click()
                page.locator('#cancel-arrangement').click()
                page.wait_for_function("document.querySelector('#arrangement-status').textContent.includes('Подбор остановлен')")
                assert page.locator('#arrange-media').is_enabled()
                assert page.evaluate("getComputedStyle(document.body).backgroundColor")=='rgb(11, 16, 26)'
                assert page.locator('#arrange-media').bounding_box()['height']>=43
                page.locator('#manual-photos').evaluate('(d)=>d.open=false')
                page.locator('#manual-videos').evaluate('(d)=>d.open=false')
                page.screenshot(path=str(screens/'desktop-v6.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.screenshot(path=str(screens/'mobile.png'),full_page=True)
                # A disconnected backend produces a visible explanation, not a blank UI.
                page.route('**/api/assets', lambda route: route.abort())
                page.reload()
                page.locator('#connection-error:not(.hidden)').wait_for()
                assert 'не подключена' in page.locator('#connection-error').inner_text()
                assert not errors, errors
                browser.close()
                print('PASS: uploads, SRT timeline, templates, editing, save/restore/import, MP4 export/player, responsive layout, disconnected-server message. No JS errors.')
        finally:
            server.terminate()
            server.wait(timeout=15)
