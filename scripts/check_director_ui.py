"""Real browser migration and VTT import: no scene effect controls required."""
import io,json,os,shutil,subprocess,tempfile,time,urllib.request
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='kadr-director-ui-') as temp:
    work=Path(temp);env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data'))
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(root/'.venv/bin/python'),'run.py','--no-browser','--port','8003'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8003/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];calls=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda d:d.accept())
                base='http://127.0.0.1:8003';page.goto(base)
                page.wait_for_function("document.querySelector('#scene-preview').naturalWidth===960")
                photo=io.BytesIO();Image.new('RGB',(800,600),'#ad9772').save(photo,'PNG')
                asset=page.request.post(base+'/api/assets',multipart={'file':{'name':'Enoch manuscript.png','mimeType':'image/png','buffer':photo.getvalue()}}).json()
                job='d'*32
                def begin(route):
                    calls.append(route.request.post_data_json)
                    route.fulfill(status=202,content_type='application/json',body=json.dumps({'id':job,'status':'queued'}))
                def finish(route):
                    assignments=[{'scene_index':i,'asset':asset,'offset':0,'reason':'Тестовый кадр для проверки интерфейса'} for i,s in enumerate(calls[-1]['project']['scenes']) if s['template'] not in ('thesis','quote')]
                    route.fulfill(content_type='application/json',body=json.dumps({'id':job,'status':'done','progress':100,'message':'Готово','assignments':assignments,'outcomes':[],'errors':[]}))
                page.route('**/api/auto-media',begin);page.route('**/api/auto-media/'+job,finish)
                old={'version':1,'name':'Старый проект','scenes':[{'duration':4,'offset':0,'asset_id':asset['id']} for _ in range(6)],
                     'subtitles':[{'start':0,'end':5,'text':'Книга Еноха'},{'start':5,'end':9,'text':'Эфиопская рукопись'},{'start':10,'end':13,'text':'Семь признаков'},{'start':22,'end':24,'text':'В 2010 году'}]}
                normalized=page.request.post(base+'/api/project/validate',data=old)
                assert normalized.ok,normalized.text()
                page.evaluate("localStorage.setItem('kadr-job-v16','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')")
                page.evaluate('(p)=>localStorage.setItem("kadr-project-v16",JSON.stringify(p))',normalized.json());page.reload()
                page.wait_for_function("typeof project!=='undefined' && project && project.director_version===12 && project.scenes.every(s=>!!s.asset_id) && !autoMediaTask")
                assert len(calls)==1 and calls[0]['replace_existing']
                assert page.evaluate("localStorage.getItem('kadr-job-v16')") is None
                assert page.evaluate("localStorage.getItem('kadr-previous-export')")==('a'*32)
                assert page.evaluate('project.scenes.length')<6
                assert page.evaluate("project.scenes.some(s=>s.title_effect==='scroll')")
                assert page.evaluate("project.scenes.some(s=>s.title_layout==='number' && s.title_decoration==='ring')")
                assert len(json.loads(page.evaluate('localStorage.getItem("kadr-before-rhythm-v16")'))['scenes'])==6
                page.reload();page.wait_for_function("document.querySelector('#scene-preview').naturalWidth===960")
                assert len(calls)==1,'Migration must run once, preserving later edits'
                vtt=work/'input.vtt';vtt.write_text('WEBVTT\n\n00:00.000 --> 00:05.000\nКнига Еноха\n\n00:05.000 --> 00:10.000\nСемь признаков\n\n00:10.000 --> 00:15.000\nГреческая рукопись')
                page.locator('#file-srt').set_input_files(vtt)
                page.wait_for_function("typeof project!=='undefined' && project && project.director_version===12 && project.subtitles.length===3 && project.scenes.every(s=>!!s.asset_id) && !autoMediaTask")
                assert len(calls)==2
                assert page.evaluate("project.scenes.some(s=>s.title_effect==='scroll')")
                page.locator('#watch-montage').click()
                page.wait_for_function("document.querySelector('#montage-player').readyState>=1 && !document.querySelector('#montage-player').classList.contains('hidden')",timeout=90000)
                assert abs(page.locator('#montage-player').evaluate('(v)=>v.duration')-15)<.1
                page.locator('#undo-rhythm').click()
                assert page.evaluate('project.scenes.length')==6
                page.reload();page.wait_for_function("document.querySelector('#scene-preview').naturalWidth===960")
                assert len(calls)==2,'Undo must survive reopening without another migration'
                # Reopen the exact v9 failure case: holes recover automatically,
                # including a narration card when no matching picture exists.
                page.unroute('**/api/auto-media/'+job)
                def finish_with_text(route):
                    source=calls[-1]['project']['scenes'][1]
                    card={**source,'automatic_text':True,'template':'thesis','title':'Греческая рукопись','caption':'','asset_id':None,'title_delay':0,'title_end':None,'title_layout':'caption','picture_effect':'plain','title_effect':'fade','title_decoration':'none','title_exit':False}
                    route.fulfill(content_type='application/json',body=json.dumps({'id':job,'status':'done','progress':100,'message':'Готово','assignments':[{'scene_index':0,'asset':asset,'offset':0,'reason':'Фото · Енох'}],'fallbacks':[{'scene_index':1,'scene':card,'reason':'Текстовая композиция из озвучки'}],'outcomes':[{'scene_index':1,'status':'text','reason':'Текстовая композиция из озвучки'}],'errors':[]}))
                page.route('**/api/auto-media/'+job,finish_with_text)
                broken={'director_version':12,'scenes':[{'duration':2},{'duration':2}], 'subtitles':[{'start':0,'end':2,'text':'Книга Еноха'},{'start':2,'end':4,'text':'Греческая рукопись'}]}
                normalized=page.request.post(base+'/api/project/validate',data=broken)
                assert normalized.ok,normalized.text()
                page.evaluate('(p)=>localStorage.setItem("kadr-project-v16",JSON.stringify(p))',normalized.json());page.reload()
                page.wait_for_function("typeof project!=='undefined' && project && project.scenes.length===2 && project.scenes[1].automatic_text && !autoMediaTask")
                assert len(calls)==3
                assert page.evaluate('project.scenes[1].title')=='Греческая рукопись'
                assert 'Текстовых композиций: 1' in page.locator('#arrangement-status').inner_text()
                page.locator('#watch-montage').click()
                page.wait_for_function("document.querySelector('#montage-player').readyState>=1 && !document.querySelector('#montage-player').classList.contains('hidden')",timeout=90000)
                assert abs(page.locator('#montage-player').evaluate('(v)=>v.duration')-4)<.1
                assert not errors,errors
                print('PASS: automatic one-time migration, backup, slower shots, reference effects, semantic pickup request, automatic VTT import, actual MP4 preview; no scene setup or JS errors.')
                browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
