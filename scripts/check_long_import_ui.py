"""Import the reported 601.334s VTT through the actual browser file control."""
import json,os,shutil,subprocess,tempfile,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='kadr-long-import-') as temp:
    work=Path(temp);env=dict(os.environ,STUDIO_DATA_DIR=str(work/'data'))
    with (work/'server.log').open('w+') as log:
        server=subprocess.Popen([str(root/'.venv/bin/python'),'run.py','--no-browser','--port','8004'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8004/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
                page.goto('http://127.0.0.1:8004');page.wait_for_function("typeof project!=='undefined' && !!project")
                page.evaluate('project.auto_media_after_subtitles=false')
                vtt=work/'long.vtt';vtt.write_text('WEBVTT\n\n00:00.000 --> 00:03.000\nКнига Еноха\n\n09:59.000 --> 10:01.334\nПоследняя фраза')
                page.locator('#file-srt').set_input_files(vtt)
                page.wait_for_function('project.subtitles.length===2 && project.subtitles[1].end===601.334 && project.scenes.length>40')
                assert '10:01' in page.locator('#total-time').inner_text()
                saved=page.evaluate('JSON.parse(localStorage.getItem("kadr-project-v16"))')
                assert saved['subtitles'][-1]['end']==601.334
                assert sum(round(s['duration']*30) for s in saved['scenes'])==18041
                # Import and reopen a >40-scene JSON without remigration or media searches.
                for scene in saved['scenes']:scene.update(template='quote',asset_id=None,automatic_text=False)
                project_file=work/'long-project.json';project_file.write_text(json.dumps(saved,ensure_ascii=False))
                page.locator('#file-project').set_input_files(project_file)
                page.wait_for_function("project.scenes.length>40 && project.scenes.every(s=>s.template==='quote')")
                page.reload();page.wait_for_function('typeof project!=="undefined" && !!project && project.subtitles.length===2')
                assert page.evaluate('project.subtitles[1].end')==601.334
                assert page.evaluate('project.scenes.length')>40
                assert not errors,errors
                print('PASS: actual VTT import at 601.334 seconds, >40 scenes, frame clock, saved JSON import and browser reopening; no truncation or JS errors.')
                browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
