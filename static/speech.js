'use strict';
let speechRecord = null, speechAvailable = false, transcriptOwner = null;
const speechStorage = 'kadr-speech-v17';
const speechSignature = () => JSON.stringify(project.subtitles);

function paintSpeech() {
  $('#speech-start').disabled = !!speechRecord || !project?.voice_id || !speechAvailable;
  $('#speech-cancel').classList.toggle('hidden', !speechRecord);
  $('#speech-progress').classList.toggle('hidden', !speechRecord);
  paintWorkflow();
}
async function stopSpeech() {
  let old = speechRecord;
  if(!old) {try {old=JSON.parse(localStorage.getItem(speechStorage));} catch { /* Invalid saved status is discarded below. */ }}
  speechRecord = null; localStorage.removeItem(speechStorage); paintSpeech();
  if (old?.id) await api(`/api/transcription/${old.id}/cancel`, {method:'POST'});
}
function applyTranscript(data) {
  project.scenes = data.scenes; project.subtitles = data.subtitles;
  project.transition = 'cut'; project.photo_motion=data.photo_motion || 'smooth'; project.director_version=data.director_version;
  project.effects_version=data.effects_version || 19;
  project.animate_titles=true; project.sound_mode='auto'; project.sounds_per_minute=4; project.sfx_volume=.28;
  selected=0; refresh();
}
async function pollSpeech(record, owner) {
  if (speechRecord !== record || project !== owner) return;
  try {
    const state = await (await api(`/api/transcription/${record.id}`)).json();
    if (speechRecord !== record || project !== owner) return;
    $('#speech-status').textContent = state.message;
    $('#speech-progress').value = state.progress || 0;
    if (state.status === 'done') {
      speechRecord=null; localStorage.removeItem(speechStorage); paintSpeech();
      if (project.voice_id !== record.voice_id || speechSignature() !== record.signature) {
        $('#speech-status').textContent='Текст или озвучка изменились. Результат распознавания не заменяет ваши правки.'; return;
      }
      applyTranscript(state.result);
      transcriptReady();
      toast(`Распознано ${project.subtitles.length} фраз. Проверь имена и термины в редакторе.`);
      return;
    }
    if (['error','cancelled','interrupted'].includes(state.status)) {
      speechRecord=null; localStorage.removeItem(speechStorage); paintSpeech(); return;
    }
    setTimeout(() => pollSpeech(record, owner), 900);
  } catch (e) {
    if (speechRecord !== record) return;
    // Keep the saved job so a reload or click can resume after a network error.
    speechRecord=null; paintSpeech(); $('#speech-status').textContent=e.message+' Нажми «Распознать озвучку», чтобы продолжить проверку.';
  }
}
async function startSpeech() {
  if (speechRecord) return;
  if (!project.voice_id) throw new Error('Сначала загрузи озвучку.');
  if (autoMediaTask) throw new Error('Сначала останови подбор материалов.');
  if(!speechAvailable)throw new Error('Распознавание не установлено. Запусти «Обновить Кадр.command» или добавь SRT/VTT.');
  await enterWorkflowStep(1,{scroll:false,auto:false});
  const old=localStorage.getItem(speechStorage);
  if (old) {
    const record=JSON.parse(old);
    if (record.voice_id===project.voice_id && record.signature===speechSignature()) {
      speechRecord=record; paintSpeech(); await pollSpeech(record,project); return;
    }
    if (record.id) await api(`/api/transcription/${record.id}/cancel`,{method:'POST'}).catch(()=>{});
    localStorage.removeItem(speechStorage);
  }
  if (project.subtitles.length && !confirm('Заменить существующие фразы и монтаж результатом распознавания? Сначала скачай проект для сохранения ручных правок.')) return;
  const owner=project, record={id:null, voice_id:project.voice_id, signature:speechSignature()};
  speechRecord=record; paintSpeech();
  $('#speech-status').textContent='Готовим распознавание. Первая загрузка модели может занять несколько минут…';
  try {
    const state=await (await api('/api/transcription',jsonPost({voice_id:record.voice_id, model:$('#speech-model').value, language:$('#speech-language').value}))).json();
    record.id=state.id;
    if (speechRecord!==record || project!==owner || project.voice_id!==record.voice_id) {
      await api(`/api/transcription/${state.id}/cancel`,{method:'POST'}); return;
    }
    localStorage.setItem(speechStorage,JSON.stringify(record));
    await pollSpeech(record,owner);
  } catch (e) {
    if (speechRecord!==record) return;
    speechRecord=null; paintSpeech(); $('#speech-status').textContent=e.message; throw e;
  }
}
async function initSpeech() {
  on('#speech-start','click',startSpeech);
  on('#speech-cancel','click',async()=>{await stopSpeech();$('#speech-status').textContent='Распознавание остановлено.';});
  for(const key of ['model','language','auto']) {
    const element=$('#speech-'+key), saved=localStorage.getItem('kadr-speech-'+key);
    if(saved!==null) {if(key==='auto')element.checked=saved==='true';else element.value=saved;}
    element.addEventListener('change',()=>localStorage.setItem('kadr-speech-'+key,key==='auto'?element.checked:element.value));
  }
  const capabilities=await (await api('/api/transcription')).json();
  speechAvailable=capabilities.available; paintSpeech();
  if(!speechAvailable)$('#speech-status').textContent='Распознавание не установлено. Запусти «Обновить Кадр.command». SRT/VTT работает без него.';
  const saved=localStorage.getItem(speechStorage);
  if(saved) {
    try {
      const record=JSON.parse(saved);
      if(record.id && record.voice_id===project.voice_id && record.signature===speechSignature()) {
        speechRecord=record; paintSpeech(); await pollSpeech(record,project);
      } else {if(record.id)await api(`/api/transcription/${record.id}/cancel`,{method:'POST'}).catch(()=>{});localStorage.removeItem(speechStorage);}
    } catch {localStorage.removeItem(speechStorage);}
  }
  on('#edit-transcript','click',()=>{
    if(!project.subtitles.length)throw new Error('Сначала распознай речь или загрузи SRT/VTT.');
    if(speechRecord || autoMediaTask)throw new Error('Сначала останови распознавание и подбор.');
    transcriptOwner=project;
    $('#transcript-fields').replaceChildren();
    project.subtitles.forEach((cue,i)=>{
      const label=document.createElement('label');label.className='field-label';label.textContent=`${i+1}. ${seconds(cue.start)} — ${seconds(cue.end)}`;
      const input=document.createElement('textarea');input.value=cue.text;input.maxLength=500;input.rows=2;input.dataset.cue=i;
      label.append(input);$('#transcript-fields').append(label);
    });
    $('#transcript-dialog').showModal();
  });
  on('#transcript-close','click',()=>$('#transcript-dialog').close());
  on('#transcript-apply','click',async()=>{
    if(project!==transcriptOwner)throw new Error('Проект изменился. Открой редактор заново.');
    const owner=project, before=JSON.stringify(project), texts=$$('#transcript-fields textarea').map(e=>e.value.trim());
    if(texts.some(t=>!t))throw new Error('Фразы не должны быть пустыми.');
    const result=await (await api('/api/project/transcript',jsonPost({project,texts}))).json();
    if(project!==owner || JSON.stringify(project)!==before)throw new Error('Проект изменился во время сохранения. Повтори правку.');
    project=result;refresh();$('#transcript-dialog').close();toast('Фразы и акценты обновлены. Таймкоды сохранены.');
  });
  on('#download-subtitles','click',()=>{
    if(!project.subtitles.length)throw new Error('Нет фраз для скачивания.');
    const stamp=s=>{const ms=Math.round(s*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')},${String(ms%1000).padStart(3,'0')}`;};
    const text=project.subtitles.map((c,i)=>`${i+1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${c.text}\n`).join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='kadr-subtitles.srt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  const semantic=await (await api('/api/semantic')).json();
  $('#semantic-status').textContent=semantic.message;
  on('#semantic-prepare','click',async()=>{
    $('#semantic-prepare').disabled=true;$('#semantic-status').textContent='Скачиваем и загружаем модель. При первом запуске потребуется время…';
    try {const state=await (await api('/api/semantic/prepare',{method:'POST'})).json();$('#semantic-status').textContent=state.message;}
    finally {$('#semantic-prepare').disabled=false;}
  });
}
