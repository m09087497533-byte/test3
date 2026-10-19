'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const labels = {cinema: 'Кино', archive: 'Архив', statement: 'Акцент', quote: 'Цитата', thesis:'Тезис'};
let project, assets = [], selected = 0, previewUrl, previewTimer, previewVersion = 0, toastTimer, polling = false;
const storageKey = 'kadr-project-v16';
const jobStorage = 'kadr-job-v16';
const rhythmStorage = 'kadr-before-rhythm-v16';
const effectsStorage='kadr-before-effects-v18';
const seconds = (n) => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
const duration = () => project.scenes.reduce((sum, s) => sum + Math.round(s.duration * 30) / 30, 0);
const sceneStart = (index) => project.scenes.slice(0, index).reduce((sum, s) => sum + Math.round(s.duration * 30) / 30, 0);
const media = (id) => assets.find(a => a.id === id);

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = error ? 'error' : '';
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), error ? 10000 : 5000);
}
function errorText(data) {
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map(e => `${e.loc.slice(1).join('.') || 'Проект'}: ${e.msg}`).join('; ');
  return 'Не удалось выполнить запрос';
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { data = {detail: `Ошибка сервера (${response.status})`}; }
    const error = new Error(errorText(data)); error.status=response.status; throw error;
  }
  return response;
}
const jsonPost = (value) => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
function on(selector, event, handler) {
  $(selector).addEventListener(event, async (e) => {
    try { await handler(e); } catch (error) { toast(error.message, true); }
  });
}
function save() {
  try { localStorage.setItem(storageKey, JSON.stringify(project)); $('#save-state').textContent = 'Сохранено в браузере'; }
  catch { $('#save-state').textContent = 'Скачай проект для сохранения'; }
  updatePlaybackState();
}
function refresh() {
  save();
  paintTimeline();
  paintInspector();
  paintAssets();
  paintAudio();
  paintTranscript();
  paintAutoControls();
  paintSpeech();
  refreshSoundPlan();
  queuePreview();
}

function paintTimeline() {
  $('#project-name').value = project.name;
  $('#total-time').textContent = seconds(duration());
  $('#scene-count').textContent = `${project.scenes.length} сцен · ${project.resolution} · 30 fps`;
  $('#timeline').replaceChildren();
  project.scenes.forEach((s, index) => {
    const button = document.createElement('button');
    button.className = `scene-card ${index === selected ? 'selected' : ''}`;
    button.setAttribute('aria-label', `Сцена ${index + 1}: ${s.title || labels[s.template]}`);
    button.setAttribute('aria-pressed', index === selected ? 'true' : 'false');
    const art = document.createElement('div'); art.className = `card-art ${s.template}-art`;
    if (s.asset_id && s.template !== 'quote') {
      const image = document.createElement('img'); image.loading='lazy'; image.src = `/api/assets/${s.asset_id}/thumb`; image.alt = ''; art.classList.add('has-media');art.append(image);
    }
    const label=s.title || (s.asset_id ? '' : sceneText(index) || labels[s.template]);
    if(label){const title=document.createElement('span');title.textContent=label;art.append(title);}
    const meta = document.createElement('small');
    const n = document.createElement('span'); n.textContent = `${String(index+1).padStart(2, '0')} / ${labels[s.template]}`;
    const d = document.createElement('span'); d.textContent = `${Number(s.duration.toFixed(1))} с`; meta.append(n, d);
    button.append(art, meta);
    const material=media(s.asset_id);
    if(material) { const badge=document.createElement('span');badge.className='media-badge'+(s.media_reason?' auto':'');badge.textContent=(material.kind==='video'?'▶ Видео':'▧ Фото')+(s.media_reason?' · авто':'');button.append(badge); }
    button.addEventListener('click', () => { selected = index; refresh(); seekVisiblePlayback(sceneStart(index)); });
    $('#timeline').append(button);
  });
}
function paintInspector() {
  const s = project.scenes[selected];
  $('#inspector-title').textContent = `Сцена ${String(selected + 1).padStart(2, '0')}`;
  $('#scene-counter').textContent = `${String(selected + 1).padStart(2, '0')} / ${String(project.scenes.length).padStart(2, '0')}`;
  $('#scene-title').value = s.title;
  for (const [key, fallback] of Object.entries({title_effect:'inherit', title_delay:0, title_duration:.3, title_sound:'none', title_decoration:'none'})) {
    $('#' + key.replace('_','-')).value = s[key] ?? fallback;
  }
  $('#title-exit').checked = !!s.title_exit;
  $('#scene-match').textContent = s.media_reason || '';
  $('#scene-match').classList.toggle('hidden', !s.media_reason);
  $('#scene-caption').value = s.caption;
  $('#scene-accents').textContent=(s.text_events||[]).length?'Автоматические появления: '+s.text_events.map(e=>seconds(sceneStart(selected)+e.start)+' · '+e.text).join(' / '):'';
  $('#scene-duration').value = Number(s.duration.toFixed(3));
  $('#scene-offset').value = s.offset;
  $('#scene-offset').disabled = media(s.asset_id)?.kind !== 'video';
  $('#scene-motion').checked = s.motion;
  $('#scene-motion').disabled = s.template === 'quote' || media(s.asset_id)?.kind === 'video';
  $('#transition').value = project.transition;
  $('#scene-transition').value = s.transition_out || 'inherit';
  $('#motion-style').value = s.motion_style || 'push';
  $('#motion-style').disabled = !s.motion || s.template === 'quote' || media(s.asset_id)?.kind === 'video';
  $('#sound-mode').value = project.sound_mode || 'auto';
  $('#sounds-per-minute').value = project.sounds_per_minute ?? 4;
  const manual = project.sound_mode === 'manual';
  $('#manual-title-sound').classList.toggle('hidden', !manual);
  $('#transition-sounds').closest('label').classList.add('hidden');
  $('#auto-sound-rate').classList.toggle('hidden', project.sound_mode !== 'auto');
  $('#transition-sounds').checked = !!project.transition_sounds;
  $('#animate-titles').checked = !!project.animate_titles;
  $('#sfx-volume').value = Math.round((project.sfx_volume ?? .35)*100);
  $('#sfx-value').value = `${$('#sfx-volume').value}%`;
  $('#resolution').value = project.resolution;
  $('#show-subtitles').checked = project.show_subtitles !== false;
  $('#photo-motion').value = project.photo_motion || 'static';
  $('#undo-effects').classList.toggle('hidden',!localStorage.getItem(effectsStorage));
  $('#undo-rhythm').classList.toggle('hidden',!localStorage.getItem(rhythmStorage));
  $$('[data-template]').forEach(b => { b.classList.toggle('active', b.dataset.template === s.template); b.setAttribute('aria-pressed', String(b.dataset.template === s.template)); });
  $('#clear-asset').classList.toggle('hidden', !s.asset_id);
  $('#move-left').disabled = selected === 0;
  $('#move-right').disabled = selected === project.scenes.length - 1;
  $('#delete-scene').disabled = project.scenes.length === 1;
  $('#scene-source').textContent = s.template === 'quote' ? 'Карточка цитаты' : (media(s.asset_id)?.name || 'Демонстрационный фон · добавь свой материал');
  $('#scene-time').textContent = `${seconds(sceneStart(selected))} — ${seconds(sceneStart(selected) + s.duration)}`;
}
function paintAssets() {
  $('#asset-grid').replaceChildren();
  const visuals = assets.filter(a => a.kind !== 'audio' && (a.created_at || 0) >= (project.library_since || 0));
  $('#asset-empty').classList.toggle('hidden', visuals.length > 0);
  for (const a of visuals) {
    const button = document.createElement('button'); button.className = `asset ${project.scenes[selected].asset_id === a.id ? 'active' : ''}`;
    button.title = a.name;
    const img = document.createElement('img'); img.src = `/api/assets/${a.id}/thumb`; img.alt = a.name;
    const name = document.createElement('span'); name.textContent = `${a.kind === 'video' ? '▶ ' : ''}${a.name}`;
    button.append(img, name);
    button.addEventListener('click', () => {
      if (project.scenes[selected].template === 'quote') toast('Материал сохранён в сцене. Шаблон «Цитата» использует бумажный фон.');
      project.scenes[selected].asset_id = a.id; project.scenes[selected].offset = 0; project.scenes[selected].media_reason = ''; project.scenes[selected].media_query = ''; refresh();
    });
    $('#asset-grid').append(button);
  }
}
function paintAudio() {
  const voice = media(project.voice_id), music = media(project.music_id);
  $('#voice-info').classList.toggle('hidden', !voice);
  if (voice) {
    $('#voice-info>span').textContent = `${voice.name} · ${seconds(voice.duration)}`;
    const url = `/api/assets/${voice.id}/file`;
    if ($('#voice-info audio').getAttribute('src') !== url) $('#voice-info audio').src = url;
  } else { $('#voice-info audio').removeAttribute('src'); }
  $('#music-info').textContent = music ? `${music.name} · ${seconds(music.duration)}` : project.auto_music ? 'Фоновая музыка добавится при сборке фильма.' : 'Музыка выключена. Озвучка остаётся.';
  $('#auto-music').checked=!!project.auto_music;
  $('#remove-music').classList.toggle('hidden', !music);
  $('#music-volume').value = Math.round(project.music_volume * 100);
  $('#volume-value').value = `${Math.round(project.music_volume * 100)}%`;
  const count = project.subtitles.length;
  $('#subtitle-info').textContent = count ? `${count} фраз · до ${seconds(Math.max(...project.subtitles.map(c => c.end)))}. Таймкоды фиксированы; изменение сцен не сдвигает субтитры.` : 'Таймкоды остаются такими, как в файле.';
}
function paintTranscript() {
  $('#cue-count').textContent = `${project.subtitles.length} фраз`;
  $('#cue-list').replaceChildren();
  project.subtitles.forEach(cue => {
    const button = document.createElement('button'); button.className = 'cue';
    const time = document.createElement('time'); time.textContent = seconds(cue.start);
    const text = document.createElement('span'); text.textContent = cue.text;
    button.append(time, text);
    button.addEventListener('click', () => {
      let time = 0;
      const index = project.scenes.findIndex(s => { time += s.duration; return time > cue.start; });
      selected = index < 0 ? project.scenes.length-1 : index; refresh();
      // Show the selected cue; a static composition is not a real-time player.
      $('#preview-subtitle').textContent = project.show_subtitles !== false ? cue.text : '';
      seekVisiblePlayback(cue.start);
    });
    $('#cue-list').append(button);
  });
}
function queuePreview() {
  clearTimeout(previewTimer);
  const requestId = ++previewVersion;
  const s = structuredClone(project.scenes[selected]);
  const start = sceneStart(selected);
  const cue = project.subtitles.find(c => c.start <= start + .1 && c.end > start);
  $('#preview-subtitle').textContent = project.show_subtitles !== false ? cue?.text || '' : '';
  previewTimer = setTimeout(async () => {
    if ($('#montage-player').classList.contains('hidden')) $('#preview-loading').classList.remove('hidden');
    try {
      const response = await api('/api/preview', jsonPost(s));
      const blob = await response.blob();
      if (requestId !== previewVersion) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob); $('#scene-preview').src = previewUrl;
    } catch (e) { if (requestId === previewVersion) toast(e.message, true); }
    finally { if (requestId === previewVersion) $('#preview-loading').classList.add('hidden'); }
  }, 220);
}
function changed() { save(); paintTimeline(); refreshSoundPlan(); queuePreview(); }
async function uploadOne(file) {
  if (file.size > 256 * 1024 * 1024) throw new Error('Максимальный размер файла — 256 МБ');
  const form = new FormData(); form.append('file', file);
  toast(`Загружаем ${file.name}…`);
  const asset = await (await api('/api/assets', {method: 'POST', body: form})).json();
  assets.push(asset); return asset;
}
for (const kind of ['voice', 'music', 'visual', 'srt']) {
  on(`#upload-${kind}`, 'click', () => $(`#file-${kind}`).click());
}
on('#upload-visual-empty', 'click', () => $('#file-visual').click());
for (const kind of ['voice', 'music']) {
  on(`#file-${kind}`, 'change', async (e) => {
    const file = e.target.files[0]; e.target.value = ''; if (!file) return;
    const owner=project;
    const asset = await uploadOne(file);
    if(project!==owner)return;
    if (!asset.has_audio) { paintAssets(); throw new Error('В этом файле нет звуковой дорожки'); }
    if(kind==='voice')await stopSpeech();
    project[`${kind}_id`] = asset.id; refresh();
    if(kind==='voice' && $('#speech-auto').checked && speechAvailable){await startSpeech();return;}
    toast(kind === 'voice' ? 'Озвучка добавлена. Теперь загрузи SRT или VTT, чтобы построить монтаж по таймкодам.' : 'Музыка добавлена');
  });
}
on('#file-visual', 'change', async (e) => {
  const files = [...e.target.files]; e.target.value = '';
  for (const file of files) {
    const asset = await uploadOne(file);
    if (asset.kind === 'audio') { toast('Аудиофайл добавлен в библиотеку, но для сцены нужно изображение или видео.', true); continue; }
    if (files.length === 1) { project.scenes[selected].asset_id = asset.id; project.scenes[selected].offset = 0; project.scenes[selected].media_reason = ''; project.scenes[selected].media_query = ''; }
    refresh();
  }
  if (files.length) toast('Материалы добавлены. Нажми на изображение, чтобы назначить его выбранной сцене.');
});
on('#file-srt', 'change', async (e) => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return;
  if (autoMediaTask) throw new Error('Сначала останови текущий автоподбор.');
  await stopSpeech();
  const owner=project;
  const form = new FormData(); form.append('file', file); if (project.voice_id) form.append('voice_id', project.voice_id);
  const data = await (await api('/api/subtitles', {method: 'POST', body: form})).json();
  if(project!==owner)return;
  applyTranscript(data);
  toast(`Монтаж создан: ${data.scenes.length} сцен, ${data.subtitles.length} субтитров.`);
  if (project.auto_media_after_subtitles ?? true) await startAutomaticMedia(false);
});
on('#remove-voice', 'click', async () => { await stopSpeech(); project.voice_id = null; refresh(); });
on('#remove-music', 'click', () => { project.music_id = null; refresh(); });
on('#auto-music','change',e=>{project.auto_music=e.target.checked;refresh();});
on('#music-volume', 'input', e => { project.music_volume = Number(e.target.value)/100; save(); $('#volume-value').value = `${e.target.value}%`; });
on('#project-name', 'input', e => { project.name = e.target.value; save(); });
on('#scene-title', 'input', e => { project.scenes[selected].text_events=[]; project.scenes[selected].title = e.target.value; changed(); });
on('#scene-caption', 'input', e => { project.scenes[selected].text_events=[]; project.scenes[selected].caption = e.target.value; changed(); });
on('#scene-duration', 'change', e => {
  const value = Number(e.target.value);
  if (!Number.isFinite(value) || value < 1 || value > 30) { paintInspector(); throw new Error('Длительность сцены — от 1 до 30 секунд'); }
  project.scenes[selected].duration = Math.round(value*30)/30;
  const scene=project.scenes[selected];scene.text_events=(scene.text_events||[]).filter(e=>e.start<scene.duration).map(e=>({...e,end:Math.min(e.end,scene.duration)}));
  if(scene.title_end>scene.duration)scene.title_end=scene.duration;
  if(scene.title_delay>=scene.duration){scene.title_delay=0;scene.title_end=null;}
  refresh();
});
on('#scene-offset', 'change', e => {
  const value = Number(e.target.value), asset = media(project.scenes[selected].asset_id);
  if (!Number.isFinite(value) || value < 0 || value >= (asset?.duration || 1)) { paintInspector(); throw new Error('Начало фрагмента должно находиться внутри видео'); }
  project.scenes[selected].offset = value; refresh();
});
on('#scene-motion', 'change', e => { project.scenes[selected].motion = e.target.checked; refresh(); });
on('#motion-style', 'change', e => { project.scenes[selected].motion_style = e.target.value; save(); });
on('#scene-transition', 'change', e => { project.scenes[selected].transition_out = e.target.value; save(); refreshSoundPlan(); });
on('#animate-titles', 'change', e => { project.animate_titles = e.target.checked; save(); });
on('#transition-sounds', 'change', e => { project.transition_sounds = e.target.checked; save(); refreshSoundPlan(); });
on('#sfx-volume', 'input', e => { project.sfx_volume = Number(e.target.value)/100; $('#sfx-value').value = `${e.target.value}%`; save(); refreshSoundPlan(); });
on('#clear-asset', 'click', () => { project.scenes[selected].asset_id = null; project.scenes[selected].offset = 0; project.scenes[selected].media_reason = ''; project.scenes[selected].media_query = ''; refresh(); });
on('#transition', 'change', e => { project.transition = e.target.value; save(); refreshSoundPlan(); });
on('#resolution', 'change', e => { project.resolution = e.target.value; changed(); });
$$('[data-template]').forEach(button => button.addEventListener('click', () => { project.scenes[selected].template = button.dataset.template; refresh(); }));
on('#add-scene', 'click', () => {
  if (project.scenes.length >= 600) throw new Error('Максимум 600 сцен');
  project.scenes.push({template:'cinema', title:'', caption:'', duration:4, asset_id:null, offset:0, motion:true});
  selected = project.scenes.length - 1; refresh(); $('#timeline').lastElementChild.scrollIntoView({behavior:'smooth', block:'nearest'});
});
on('#duplicate', 'click', () => {
  if (project.scenes.length >= 600) throw new Error('Максимум 600 сцен');
  project.scenes.splice(selected+1, 0, structuredClone(project.scenes[selected])); selected++; refresh();
});
on('#delete-scene', 'click', () => {
  if (project.scenes.length === 1) return;
  project.scenes.splice(selected, 1); selected = Math.min(selected, project.scenes.length - 1); refresh();
});
for (const [id, direction] of [['#move-left', -1], ['#move-right', 1]]) {
  on(id, 'click', () => {
    const target = selected + direction;
    if (target < 0 || target >= project.scenes.length) return;
    [project.scenes[target], project.scenes[selected]] = [project.scenes[selected], project.scenes[target]];
    selected = target; refresh();
  });
}
on('#demo', 'click', async () => {
  if (!confirm('Открыть демо-проект вместо текущего монтажа? Сначала скачай текущий проект, если хочешь его сохранить.')) return;
  await stopSpeech();
  project = await (await api('/api/demo')).json(); selected = 0; refresh(); toast('Открыт демо-проект. В нём нет озвучки.');
});
on('#new-project', 'click', async () => {
  if (!confirm('Создать новый проект? Текущий монтаж сохранится в браузере как резервная копия.')) return;
  if (autoMediaTask === 'starting') throw new Error('Подожди начала подбора и нажми «Новый проект» ещё раз.');
  if (autoMediaTask) await api(`/api/auto-media/${autoMediaTask}/cancel`,{method:'POST'}).catch(()=>{});
  await stopSpeech();
  if (project) localStorage.setItem('kadr-previous-project', JSON.stringify(project));
  project = await (await api('/api/project/new')).json(); selected = 0;
  localStorage.removeItem(jobStorage); localStorage.removeItem(playbackStorage);
  localStorage.removeItem(rhythmStorage); localStorage.removeItem(effectsStorage); localStorage.removeItem(autoMediaStorage);
  autoMediaTask=null;
  playback.record=null; playback.ready=false; playback.busy=false;
  polling=false;
  $('#montage-player').removeAttribute('src'); $('#montage-player').load(); showEditingFrame();
  if($('#export-dialog').open)$('#export-dialog').close();
  $('#result-video').removeAttribute('src');$('#result-video').classList.add('hidden');
  $('#download-video').classList.add('hidden');$('#download-sources').classList.add('hidden');
  $('#export').textContent='Экспорт MP4 ↗';$('#export').disabled=false;
  $('#arrangement-report').classList.add('hidden'); $('#arrangement-status').textContent='Загрузи новую озвучку — распознаем речь и создадим сцены.';
  refresh(); toast('Новый проект создан. Загрузи озвучку нового рассказа.');
});
on('#save-project', 'click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(project, null, 2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'kadr-project.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Монтаж сохранён в JSON. Исходные файлы остаются на этом сервере.');
});
on('#import-project', 'click', () => $('#file-project').click());
on('#file-project', 'change', async e => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return;
  if (file.size > 2*1024*1024) throw new Error('Файл проекта слишком большой');
  let value; try { value = JSON.parse(await file.text()); } catch { throw new Error('Не удалось прочитать JSON проекта'); }
  const valid = await (await api('/api/project/validate', jsonPost(value))).json();
  if (!confirm('Заменить текущий монтаж открываемым проектом?')) return;
  await stopSpeech();
  project = valid; selected = 0;
  if((project.effects_version||0)<18)await applyReferenceLook();
  refresh(); toast('Проект открыт');
});
on('#close-export', 'click', () => $('#export-dialog').close());
async function pollJob(id) {
  if (polling) return;
  polling = true;
  const poll = async () => {
    if (localStorage.getItem(jobStorage)!==id) {polling=false;return;}
    try {
      const job = await (await api(`/api/jobs/${id}`)).json();
      if (localStorage.getItem(jobStorage)!==id) {polling=false;return;}
      $('#export-message').textContent = job.message;
      $('#export-progress').value = job.progress;
      $('#export-percent').textContent = `${job.progress}%`;
      if (job.status === 'done' || job.status === 'error') {
        polling = false; localStorage.removeItem(jobStorage); $('#export').textContent = 'Экспорт MP4 ↗';
        if (job.status === 'error') { $('#export-heading').textContent = 'Экспорт не завершён'; toast(job.message, true); return; }
        $('#export-heading').textContent = 'Твоя история готова';
        const url = `/api/jobs/${id}/video`;
        $('#result-video').src = url; $('#result-video').classList.remove('hidden');
        $('#download-video').href = url; $('#download-video').classList.remove('hidden');
        $('#download-sources').href = `/api/jobs/${id}/sources`; $('#download-sources').classList.remove('hidden');
        if (!$('#export-dialog').open) { $('#export-dialog').showModal(); toast('Экспорт готов'); }
      } else { setTimeout(poll, 1000); }
    } catch (e) {
      polling = false; $('#export').textContent = 'Проверить экспорт ↗';
      $('#export-message').textContent = 'Связь прервалась. Нажми «Проверить экспорт», чтобы узнать результат.';
      toast(e.message, true);
    }
  };
  await poll();
}
on('#export', 'click', async () => {
  const oldId = localStorage.getItem(jobStorage);
  if (oldId) { $('#export-dialog').showModal(); await pollJob(oldId); return; }
  const missing = project.scenes.filter(s => !s.asset_id && !['quote','thesis'].includes(s.template)).length;
  const voice = media(project.voice_id);
  const warnings = [];
  if (missing && (project.director_version||0)<12) warnings.push(`${missing} сцен используют демонстрационный фон.`);
  if (!voice && !project.music_id) warnings.push(project.sound_mode !== 'off' ? 'Озвучки нет: возможны только звуковые акценты.' : 'Ролик будет без звука.');
  if (voice && voice.duration > duration()+.1) warnings.push('Озвучка длиннее монтажа: конец звука будет обрезан.');
  if (warnings.length && !confirm(warnings.join('\n') + '\n\nПродолжить экспорт?')) return;
  $('#export').disabled = true;
  try {
    const job = await (await api('/api/render', jsonPost(project))).json();
    localStorage.setItem(jobStorage, job.id);
    $('#result-video').classList.add('hidden'); $('#result-video').removeAttribute('src');
    $('#download-video').classList.add('hidden'); $('#download-sources').classList.add('hidden'); $('#export-progress').value = 0;
    $('#export-heading').textContent = 'Собираем твою историю';
    $('#export-message').textContent = 'Подготовка кадров';
    $('#export').textContent = 'Ход экспорта ↗'; $('#export-dialog').showModal(); await pollJob(job.id);
  } finally { $('#export').disabled = false; }
});
async function init() {
  assets = await (await api('/api/assets')).json();
  const currentSaved = localStorage.getItem(storageKey);
  const saved = currentSaved || localStorage.getItem('kadr-project-v15');
  if (saved) {
    try {
      const candidate = JSON.parse(saved);
      // Retain an unfinished montage even when its durations currently conflict
      // with SRT timing. Export validation must not discard editable work.
      const templateNames = Object.keys(labels);
      if (!candidate || candidate.version !== 1 || typeof candidate.name !== 'string' || !Array.isArray(candidate.scenes) || !candidate.scenes.length || candidate.scenes.length > 600 ||
          !candidate.scenes.every(s => templateNames.includes(s.template) && typeof s.title === 'string' && typeof s.caption === 'string' && Number.isFinite(s.duration) && s.duration >= 1 && s.duration <= 30 && Number.isFinite(s.offset) && (!s.asset_id || /^[a-f0-9]{32}$/.test(s.asset_id))) ||
          !Array.isArray(candidate.subtitles) || !candidate.subtitles.every(c => typeof c.text === 'string' && Number.isFinite(c.start) && Number.isFinite(c.end))) throw new Error('Повреждённый проект');
      const ids=[candidate.voice_id,candidate.music_id,...candidate.scenes.map(s=>s.asset_id)].filter(Boolean);
      if (!currentSaved && !ids.every(id=>assets.some(a=>a.id===id))) {
        toast('Предыдущий проект сохранён, но его исходники не найдены в этой папке. Помести новую папку рядом со старой и запусти Кадр снова.',true);
      } else {
        project = candidate;
        if (!currentSaved)localStorage.setItem(storageKey,saved);
      }
    } catch {
      localStorage.setItem('kadr-project-recovery', saved);
      toast('Сохранённый проект повреждён. Резервная копия сохранена в браузере под ключом kadr-project-recovery.', true);
    }
  }
  if (!project) project = await (await api('/api/project/new')).json();
  let directorNeedsMedia=false, directorReplaceMedia=false;
  if(project.subtitles.length && (project.director_version||0)<12) {
    const before=JSON.stringify(project);
    try {
    project=await (await api('/api/project/reference-rhythm',jsonPost(project))).json();
    localStorage.setItem(rhythmStorage,before);
    project.auto_media_after_subtitles=true;
    directorNeedsMedia=true;directorReplaceMedia=true;
    const oldExport=localStorage.getItem(jobStorage);
    if(oldExport){localStorage.setItem('kadr-previous-export',oldExport);localStorage.removeItem(jobStorage);}
    } catch(e) {toast('Не удалось автоматически обновить монтаж: '+e.message,true);}
  }
  if((project.effects_version||0)<18)await applyReferenceLook().catch(e=>toast(e.message,true));
  if(project.subtitles.length && project.director_version>=9 && project.scenes.some(s=>!s.asset_id && !['quote','thesis'].includes(s.template)))directorNeedsMedia=true;
  project.sound_mode ??= 'auto';
  project.sounds_per_minute ??= 4;
  refresh();
  const job = localStorage.getItem(jobStorage);
  if (job) { $('#export').textContent = 'Ход экспорта ↗'; await pollJob(job); }
  await resumeAutomaticMedia();
  await initSpeech();
  if(directorNeedsMedia && !autoMediaTask && !speechRecord) await startAutomaticMedia(directorReplaceMedia).catch(e=>toast(e.message,true));
  await restorePlayback();
}
init().catch(e => {
  $('#connection-detail').textContent = e.message;
  $('#connection-error').classList.remove('hidden');
});

for (const key of ['title_effect', 'title_delay', 'title_duration', 'title_sound', 'title_decoration']) {
  on('#' + key.replace('_','-'), 'change', e => {
    project.scenes[selected].text_events=[];
    project.scenes[selected][key] = ['title_delay','title_duration'].includes(key) ? Number(e.target.value) : e.target.value;
    save(); refreshSoundPlan();
  });
}

let soundPlanTimer, soundPlanVersion = 0, searchVersion = 0, stopImageFill = false;
function refreshSoundPlan() {
  clearTimeout(soundPlanTimer);
  const version = ++soundPlanVersion;
  soundPlanTimer = setTimeout(async () => {
    try {
      const plan = await (await api('/api/sound-plan', jsonPost(project))).json();
      if (version !== soundPlanVersion) return;
      $('#sound-plan').textContent = `${plan.count} звуковых акцентов в ролике${plan.count ? ': ' + plan.events.map(e => seconds(e.time)+' · '+({paper:'шелест бумаги',sweep:'пролёт',chime:'мягкий звон',stamp:'штамп',spark:'искристый блик',click:'щелчок',impact:'мягкий удар',swish:'воздушный взмах'}[e.style]||e.style)).join(', ') : ''}. Остальные моменты — без эффектов.`;
    } catch { if (version === soundPlanVersion) $('#sound-plan').textContent = 'План звуков обновится после исправления таймингов.'; }
  }, 200);
}
on('#sound-mode', 'change', e => { project.sound_mode = e.target.value; refresh(); });
on('#sounds-per-minute', 'change', e => { project.sounds_per_minute = Math.max(0,Math.min(12,Number(e.target.value)||0)); refresh(); });
on('#title-exit', 'change', e => { project.scenes[selected].title_exit = e.target.checked; save(); });
on('#undo-rhythm','click',()=>{
  if(autoMediaTask) {toast('Дождись завершения подбора или останови его.',true);return;}
  const saved=localStorage.getItem(rhythmStorage);
  if(!saved)return;
  project=JSON.parse(saved);project.director_version=12;selected=0;localStorage.removeItem(rhythmStorage);refresh();
  toast('Предыдущий монтаж восстановлен.');
});
on('#show-subtitles','change',e=>{project.show_subtitles=e.target.checked;changed();});
on('#photo-motion','change',e=>{project.photo_motion=e.target.value;changed();});
on('#reference-style', 'click', async () => {
  if(autoMediaTask) {toast('Дождись завершения подбора или останови его перед изменением ритма.',true);return;}
  const owner=project, snapshot=JSON.stringify(project);
  $('#reference-style').disabled=true;
  try {
    const revised=await (await api('/api/project/reference-rhythm',jsonPost(project))).json();
    if(project!==owner || JSON.stringify(project)!==snapshot) {toast('Проект изменился. Нажми применение ритма ещё раз.',true);return;}
    localStorage.setItem(rhythmStorage,snapshot);
    project=revised;selected=0;refresh();
    toast('Автомонтаж применён: спокойный темп, появления и смысловой подбор. Нажми «Смотреть монтаж» после подбора.');
    await startAutomaticMedia(true);
  } catch(e) {toast(e.message,true);}
  finally {$('#reference-style').disabled=false;}
});

async function importSearchResult(item, scene, owner) {
  const expectedAsset = scene.asset_id;
  const asset = await (await api('/api/images/import', jsonPost({token:item.token}))).json();
  assets.push(asset);
  if (project === owner && project.scenes.includes(scene) && scene.asset_id === expectedAsset) { scene.asset_id = asset.id; scene.offset = 0; scene.media_reason = ''; scene.media_query = ''; }
  refresh();
  return asset;
}
on('#search-images', 'click', async () => {
  const version = ++searchVersion, owner = project, scene = project.scenes[selected];
  $('#search-images').disabled = true; $('#image-results').replaceChildren();
  $('#image-status').textContent = 'Ищем изображения по тексту сцены…';
  try {
    const response = await (await api('/api/images/search', jsonPost({project, scene_index:selected, query:$('#image-query').value}))).json();
    if (version !== searchVersion || project !== owner) return;
    $('#image-status').textContent = response.results.length ? `${response.concept}: ${response.query}. Найдено ${response.results.length}.` : `По запросу «${response.query}» ничего не найдено. Измени фразу.`;
    for (const item of response.results) {
      const card = document.createElement('div'); card.className = 'search-card';
      const img = document.createElement('img'); img.src=item.url; img.alt=item.title; img.loading='lazy';
      const title = document.createElement('small'); title.textContent=item.title;
      const source = document.createElement('a'); source.href=item.source_url; source.target='_blank'; source.rel='noopener noreferrer'; source.textContent=`Источник · ${item.license}`;
      const use = document.createElement('button'); use.className='secondary'; use.textContent='Добавить в эту сцену';
      use.addEventListener('click', async () => {
        use.disabled=true;
        try { await importSearchResult(item,scene,owner); toast('Изображение добавлено; источник сохранён.'); }
        catch(e) { toast(e.message,true); }
        finally { use.disabled=false; }
      });
      card.append(img,title,source,use); $('#image-results').append(card);
    }
  } catch(e) { $('#image-status').textContent=e.message; throw e; }
  finally { $('#search-images').disabled=false; }
});
on('#cancel-images','click',()=>{ stopImageFill=true; $('#image-status').textContent='Останавливаем подбор после текущего запроса…'; });
on('#auto-images', 'click', async () => {
  const owner=project, scenes=project.scenes.filter(s=>!s.asset_id && !['quote','thesis'].includes(s.template));
  if (!scenes.length) { toast('Все сцены с изображениями уже заполнены.'); return; }
  stopImageFill=false; $('#auto-images').disabled=true; $('#cancel-images').classList.remove('hidden');
  let added=0, empty=0; const used=new Set(assets.map(a=>a.provenance?.source_url).filter(Boolean));
  try {
    for (const [i,scene] of scenes.entries()) {
      if (stopImageFill || project!==owner) break;
      const index=project.scenes.indexOf(scene); if(index<0 || scene.asset_id) continue;
      $('#image-status').textContent=`Подбор ${i+1} / ${scenes.length}…`;
      const result=await (await api('/api/images/search',jsonPost({project,scene_index:index}))).json();
      if (stopImageFill || project!==owner) break;
      const item=result.results.find(r=>!used.has(r.source_url));
      if (!item) { empty++; continue; }
      if (scene.asset_id) continue;
      await importSearchResult(item,scene,owner); used.add(item.source_url); added++;
    }
    $('#image-status').textContent=`Добавлено: ${added}. Без подходящей картинки: ${empty}.${stopImageFill ? ' Подбор остановлен.' : ' Просмотри подобранные изображения.'}`;
  } catch(e) { $('#image-status').textContent=`Добавлено: ${added}. ${e.message}`; throw e; }
  finally { $('#auto-images').disabled=false; $('#cancel-images').classList.add('hidden'); }
});

async function paintStockStatus() {
  const status=await (await api('/api/stock/status')).json();
  $('#stock-key-status').textContent=Object.entries(status).map(([name,s])=>`${name==='pexels'?'Pexels':'Pixabay'}: ${s.configured ? 'ключ добавлен'+(s.environment?' через окружение':'') : 'нужен API-ключ'}`).join(' · ');
  $('#auto-source-status').textContent='Источники: библиотека, Wikimedia'+(status.pexels.configured?', Pexels':'')+(status.pixabay.configured?', Pixabay':'')+'.'+(!status.pexels.configured && !status.pixabay.configured ? ' Для поиска новых видео подключи Pexels или Pixabay в левой панели.' : '');
}
on('#save-stock-keys','click',async()=>{
  const updates={};
  for(const name of ['pexels','pixabay']) {const key=$('#'+name+'-key').value.trim();if(key)updates[name]=key;}
  if(!Object.keys(updates).length)throw new Error('Введи ключ хотя бы одного сервиса.');
  await api('/api/stock/keys',jsonPost(updates));
  $('#pexels-key').value='';$('#pixabay-key').value='';
  await paintStockStatus();toast('Ключи сохранены. Проверим доступ при поиске.');
});
on('#remove-stock-key','click',async()=>{
  await api('/api/stock/keys',jsonPost({[$('#video-provider').value]:''}));
  await paintStockStatus();
});
on('#search-videos','click',async()=>{
  const owner=project, scene=project.scenes[selected], provider=$('#video-provider').value;
  $('#search-videos').disabled=true;$('#video-results').replaceChildren();
  $('#video-status').textContent='Ищем футажи по теме сцены…';
  try {
    const response=await (await api('/api/videos/search',jsonPost({project,scene_index:selected,provider,query:$('#video-query').value}))).json();
    if(project!==owner)return;
    $('#video-status').textContent=response.results.length ? `${response.query} · ${response.results.length} футажей` : `По запросу «${response.query}» ничего не найдено. Уточни фразу.`;
    for(const item of response.results) {
      const card=document.createElement('div');card.className='search-card';
      const preview=document.createElement('video');preview.controls=true;preview.muted=true;preview.preload='none';preview.playsInline=true;
      preview.src=item.preview_url;if(item.thumbnail)preview.poster=item.thumbnail;
      const label=document.createElement('small');label.textContent=`${item.title} · ${item.width}×${item.height} · ${seconds(item.duration)}`;
      const source=document.createElement('a');source.href=item.source_url;source.target='_blank';source.rel='noopener noreferrer';source.textContent=`${provider==='pexels'?'Pexels':'Pixabay'} · ${item.author || 'Источник'} ↗`;
      const use=document.createElement('button');use.className='secondary';use.textContent='Скачать и вставить в сцену';
      use.addEventListener('click',async()=>{
        const expected=scene.asset_id;use.disabled=true;use.textContent='Загружаем…';
        try {
          const asset=await (await api('/api/videos/import',jsonPost({token:item.token}))).json();
          assets.push(asset);
          if(project===owner && project.scenes.includes(scene) && scene.asset_id===expected) {scene.asset_id=asset.id;scene.offset=0;scene.media_reason='';scene.media_query='';}
          refresh();toast('Футаж добавлен. Начало фрагмента можно настроить в сцене.');
        } catch(e) {toast(e.message,true);}
        finally {use.disabled=false;use.textContent='Скачать и вставить в сцену';}
      });
      card.append(preview,label,source,use);$('#video-results').append(card);
    }
  } catch(e) {$('#video-status').textContent=e.message;throw e;}
  finally {$('#search-videos').disabled=false;}
});
paintStockStatus().catch(()=>{$('#stock-key-status').textContent='Не удалось проверить подключение сервисов.';});

on('#check-network','click',async()=>{
  $('#check-network').disabled=true;$('#network-status').textContent='Проверяем HTTPS к трём сервисам…';
  try {
    const result=await (await api('/api/network/check')).json();
    $('#network-status').textContent=result.checks.map(c=>`${c.ok?'✓':'✕'} ${c.service}: ${c.message}`).join('\n\n');
  } catch(e) {$('#network-status').textContent=e.message;}
  finally {$('#check-network').disabled=false;}
});

// One plan for the whole montage; results are applied incrementally to their original scenes.
let autoMediaTask = null;
const autoMediaStorage='kadr-auto-media-v16';
const sceneSignature=s=>JSON.stringify([s.template,s.title,s.caption,s.duration]);
const layoutSignature=()=>JSON.stringify([project.scenes.map(s=>s.duration),project.subtitles]);
function sceneText(index) {
  const start=sceneStart(index),end=start+project.scenes[index].duration;
  return project.subtitles.filter(c=>c.start<end && c.end>start).map(c=>c.text.replace(/\n/g,' ')).join(' ').slice(0,65);
}
function paintAutoControls() {
  $('#semantic-enabled').checked=!!project.semantic_media;
  $('#auto-media-mode').value=project.auto_media_mode || 'mixed';
  $('#auto-after-subtitles').checked=project.auto_media_after_subtitles ?? true;
  $('#arrange-media').disabled=!!autoMediaTask;
  $('#auto-media-mode').disabled=!!autoMediaTask;
  $('#replace-media').disabled=!!autoMediaTask;
  $('#cancel-arrangement').classList.toggle('hidden',!autoMediaTask);
  if(autoMediaTask)$('#export').disabled=true;
  updatePlaybackState();
}
function rememberAutomaticMedia(record) {
  try {localStorage.setItem(autoMediaStorage,JSON.stringify(record));}
  catch {toast('Для восстановления подбора после перезагрузки не хватило места в браузере.',true);}
}
function renderArrangementReport(state,record) {
  const rows=state.outcomes || [];
  $('#arrangement-report').classList.toggle('hidden',!rows.length && !(state.errors||[]).length);
  $('#arrangement-results').replaceChildren();
  for(const row of rows) {
    const button=document.createElement('button');button.className='arrangement-row';
    const index=document.createElement('span');index.className='result-index';index.textContent=String(row.scene_index+1).padStart(2,'0');
    const copy=document.createElement('span');copy.className='result-copy';copy.textContent=row.reason;
    const badge=document.createElement('span');badge.className='result-badge';
    const assigned=(state.assignments||[]).find(a=>a.scene_index===row.scene_index);
    badge.textContent=record.skipped?.includes(row.scene_index)?'Изменена вручную':assigned?(assigned.asset.kind==='video'?'Видео':'Фото'):row.status==='text'?'Текст':row.status==='missing'?'Нужен кадр':'Сохранено';
    if(assigned) {const q=document.createElement('small');q.textContent=assigned.query;copy.append(q);}
    button.append(index,copy,badge);
    button.addEventListener('click',()=>{if(project.scenes[row.scene_index]){selected=row.scene_index;refresh();$('#scene-preview').scrollIntoView({behavior:'smooth',block:'center'});}});
    $('#arrangement-results').append(button);
  }
  $('#arrangement-errors').textContent=(state.errors||[]).join('\n');
  $('#arrangement-errors').style.whiteSpace='pre-line';
}
function applyAutomaticMedia(state,record,owner,targets) {
  if(project!==owner)return;
  let changedAny=false;
  for(const item of state.assignments || []) {
    if(!assets.some(a=>a.id===item.asset.id))assets.push(item.asset);
    if(record.applied.includes(item.scene_index) || record.skipped.includes(item.scene_index))continue;
    const index=item.scene_index,scene=targets[index],snap=record.scenes[index];
    // Keep edits made while a network request was in flight, including timeline changes.
    if(layoutSignature()!==record.layout || project.scenes[index]!==scene || sceneSignature(scene)!==snap.signature || (scene.asset_id||null)!==snap.asset_id) {
      record.skipped.push(index);continue;
    }
    if(item.restore_visual)Object.assign(scene,{automatic_text:false,template:'cinema',title:'',caption:'',title_layout:'caption',title_delay:0,title_end:null,title_effect:'fade'});
    scene.asset_id=item.asset.id;scene.offset=item.offset;scene.media_reason=item.reason;scene.media_query=item.query;
    record.applied.push(index);changedAny=true;
  }
  for(const item of state.fallbacks||[]) {
    const i=item.scene_index,scene=targets[i],snap=record.scenes[i];
    if(!scene || !snap || record.applied.includes(i) || record.skipped.includes(i))continue;
    if(layoutSignature()!==record.layout || project.scenes[i]!==scene || sceneSignature(scene)!==snap.signature || (scene.asset_id||null)!==snap.asset_id) {record.skipped.push(i);continue;}
    Object.assign(scene,item.scene);record.applied.push(i);changedAny=true;
  }
  for(const item of state.outcomes||[]) {
    if(item.status!=='missing')continue;
    const i=item.scene_index,scene=targets[i],snap=record.scenes[i];
    if(!scene || !snap || scene.asset_id || record.skipped.includes(i) || sceneSignature(scene)!==snap.signature)continue;
    const reason='Ожидает визуал: '+item.reason;
    if(scene.media_reason!==reason){scene.media_reason=reason;changedAny=true;}
  }
  rememberAutomaticMedia(record);
  if(changedAny)refresh();
}
async function pollAutomaticMedia(record,owner,targets) {
  if(autoMediaTask && autoMediaTask!==record.id)return;
  autoMediaTask=record.id;paintAutoControls();
  try {
    const state=await (await api(`/api/auto-media/${record.id}`)).json();
    if(state.semantic)$('#semantic-status').textContent=state.semantic.message;
    if(project!==owner) {
      await api(`/api/auto-media/${record.id}/cancel`,{method:'POST'});
      localStorage.removeItem(autoMediaStorage);autoMediaTask=null;paintAutoControls();$('#export').disabled=false;return;
    }
    applyAutomaticMedia(state,record,owner,targets);
    $('#arrangement-progress').classList.remove('hidden');$('#arrangement-progress').value=state.progress;
    $('#arrangement-status').textContent=state.message;
    renderArrangementReport(state,record);
    if(['done','cancelled','error','interrupted'].includes(state.status)) {
      autoMediaTask=null;localStorage.removeItem(autoMediaStorage);paintAutoControls();$('#export').disabled=false;
      const missing=(state.outcomes||[]).filter(o=>o.status==='missing').length;
      const textCount=(state.fallbacks||[]).length;
      $('#arrangement-status').textContent=`${missing?'Подбор не завершён: для '+missing+' сцен нужны фото или футажи.':state.status==='done'?'Подбор завершён.':state.message+'.'} Назначено фото и видео: ${(state.assignments||[]).length}.${textCount?' Текстовых композиций: '+textCount+'.':''}${record.skipped.length?' Сохранены ручные изменения: '+record.skipped.length+'.':''}`;
      $('#arrangement-report').open=!!missing || !!(state.errors||[]).length;
      if(state.status==='done' && !missing && record.applied.length)toast(`Материалы назначены ${(state.assignments||[]).length} сценам. Проверь монтаж и экспортируй MP4.`);
      return;
    }
    setTimeout(()=>pollAutomaticMedia(record,owner,targets),900);
  } catch(e) {
    autoMediaTask=null;paintAutoControls();$('#export').disabled=false;
    $('#arrangement-status').textContent=`${e.message} Нажми «Продолжить проверку подбора» — уже найденные материалы сохранятся.`;
    $('#arrange-media').textContent='Продолжить проверку подбора';
  }
}
async function startAutomaticMedia(replace, sceneIndices=null, excludedAssetIds=[]) {
  if(speechRecord)throw new Error('Дождись распознавания речи или останови его.');
  if(!project.subtitles.length && !project.scenes.some(s=>s.title || s.caption)) {$('#arrangement-status').textContent='Сначала добавь озвучку и распознай речь или загрузи SRT/VTT.';return;}
  if(autoMediaTask)throw new Error('Подбор уже идёт.');
  const old=localStorage.getItem(autoMediaStorage);
  if(old) {await resumeAutomaticMedia();return;}
  if(!project.scenes.some((s,i)=>(sceneIndices===null || sceneIndices.includes(i)) && (!['quote','thesis'].includes(s.template) || s.automatic_text) && (replace || !s.asset_id))) {
    $('#arrangement-status').textContent='Все сцены уже заполнены. Для нового подбора включи «Заменять назначенные материалы».';return;
  }
  const owner=project,targets=[...project.scenes];
  const record={id:'',replace,layout:layoutSignature(),scenes:targets.map(s=>({signature:sceneSignature(s),asset_id:s.asset_id||null})),applied:[],skipped:[]};
  autoMediaTask='starting';paintAutoControls();$('#arrangement-status').textContent='Начинаем подбор фото и видео…';
  try {
    const state=await (await api('/api/auto-media',jsonPost({project,mode:project.auto_media_mode||'mixed',replace_existing:replace,scene_indices:sceneIndices,excluded_asset_ids:excludedAssetIds}))).json();
    record.id=state.id;autoMediaTask=state.id;rememberAutomaticMedia(record);
    $('#arrange-media').textContent='✦ Подобрать и расставить';
    await pollAutomaticMedia(record,owner,targets);
  } catch(e) {autoMediaTask=null;paintAutoControls();$('#export').disabled=false;$('#arrangement-status').textContent=e.message;throw e;}
}
async function resumeAutomaticMedia() {
  const saved=localStorage.getItem(autoMediaStorage);if(!saved)return;
  let record;
  try {record=JSON.parse(saved);if(!record.id || !Array.isArray(record.scenes))throw new Error();}
  catch {localStorage.removeItem(autoMediaStorage);return;}
  record.applied??=[];record.skipped??=[];
  await pollAutomaticMedia(record,project,[...project.scenes]);
}
on('#arrange-media','click',()=>startAutomaticMedia($('#replace-media').checked));
on('#cancel-arrangement','click',async()=>{
  if(!autoMediaTask || autoMediaTask==='starting')return;
  await api(`/api/auto-media/${autoMediaTask}/cancel`,{method:'POST'});
  $('#arrangement-status').textContent='Останавливаем после текущего запроса. Уже назначенные материалы останутся.';
});
on('#auto-media-mode','change',e=>{project.auto_media_mode=e.target.value;save();});
on('#auto-after-subtitles','change',e=>{project.auto_media_after_subtitles=e.target.checked;save();});
on('#semantic-enabled','change',e=>{project.semantic_media=e.target.checked;save();});

on('#replace-frame','click',()=>{
  $('#manual-photos').open=true;
  $('#manual-videos').open=true;
  $('#upload-visual').scrollIntoView({behavior:'smooth',block:'center'});
  toast('Выбери материал из библиотеки, найди фото или футаж для этой сцены.');
});
on('#another-variant','click',async()=>{
  const old=project.scenes[selected].asset_id;
  if(!project.subtitles.length)throw new Error('Сначала загрузи субтитры нового рассказа.');
  await startAutomaticMedia(true,[selected],old?[old]:[]);
});
on('#edit-screen-text','click',()=>{
  $('#scene-title').focus(); $('#scene-title').scrollIntoView({behavior:'smooth',block:'center'});
  toast('Измени заголовок или подпись сцены справа.');
});
on('#regenerate-scene','click',async()=>{
  if(autoMediaTask)throw new Error('Дождись завершения текущего подбора.');
  if(!project.subtitles.length)throw new Error('Сначала загрузи субтитры нового рассказа.');
  const index=selected,old=project.scenes[index].asset_id;
  const scene=await (await api('/api/project/scene/regenerate',jsonPost({project,scene_index:index}))).json();
  project.scenes[index]=scene;refresh();
  await startAutomaticMedia(true,[index],old?[old]:[]);
});

// The movie is rendered at 540p with the same timeline and audio as the export.
const playbackStorage='kadr-playback-v16';
let playback={record:null,ready:false,busy:false,status:'',seek:0,autoplay:false};
function playbackSignature() {
  if(!project)return '';
  const copy=structuredClone(project);
  for(const key of ['name','version','resolution','auto_media_mode','auto_media_after_subtitles'])delete copy[key];
  copy.scenes.forEach(s=>delete s.media_reason);
  function ordered(value) {
    if(Array.isArray(value))return value.map(ordered);
    if(value && typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])]));
    return value;
  }
  return JSON.stringify(ordered(copy));
}
function playbackCurrent() {return playback.ready && playback.record?.signature===playbackSignature();}
function showEditingFrame() {
  const video=$('#montage-player');video.pause();video.classList.add('hidden');
  $('#scene-preview').classList.remove('hidden');$('#preview-subtitle').classList.remove('hidden');
  $('#edit-frame').classList.add('hidden');$('#playback-position').classList.add('hidden');
  $('#stage-label').textContent='КОМПОЗИЦИЯ СЦЕНЫ';
  $$('.scene-card').forEach(card=>card.classList.remove('playing'));
}
function updatePlaybackState() {
  if(!project)return;
  const current=playbackCurrent(),video=$('#montage-player');
  const stale=!!playback.record && playback.record.signature!==playbackSignature();
  if(stale) {
    if(!video.classList.contains('hidden'))showEditingFrame();
    if(!playback.busy)$('#playback-status').textContent='Монтаж изменился. Обнови просмотр, чтобы увидеть новые кадры, текст и звук.';
  }
  $('#watch-montage').disabled=playback.busy || !!autoMediaTask;
  $('#watch-scene').disabled=playback.busy || !!autoMediaTask;
  $('#watch-montage').textContent=playback.busy?'Готовим просмотр…':stale?'↻ Обновить просмотр':current && !video.paused?'⏸ Пауза':current && video.currentTime>0 && !video.ended?'▶ Продолжить':'▶ Смотреть монтаж';
  $('#watch-scene').textContent='С выбранной сцены';
  if(autoMediaTask && !playback.busy)$('#playback-status').textContent='Дождись окончания подбора материалов, затем запусти просмотр.';
}
function markPlayingScene() {
  if(!playbackCurrent())return;
  const time=$('#montage-player').currentTime;
  let cursor=0,index=project.scenes.length-1;
  for(let i=0;i<project.scenes.length;i++) {cursor+=Math.round(project.scenes[i].duration*30)/30;if(time<cursor){index=i;break;}}
  $$('.scene-card').forEach((card,i)=>card.classList.toggle('playing',i===index && !$('#montage-player').classList.contains('hidden')));
  $('#playing-scene').textContent=`${seconds(time)} / ${seconds(duration())} · Сцена ${index+1} из ${project.scenes.length}`;
  if(!$('#montage-player').classList.contains('hidden'))$('#scene-counter').textContent=`${String(index+1).padStart(2,'0')} / ${String(project.scenes.length).padStart(2,'0')}`;
  return index;
}
function seekVisiblePlayback(time) {
  const video=$('#montage-player');
  if(playbackCurrent() && !video.classList.contains('hidden')) {video.pause();video.currentTime=time;markPlayingScene();updatePlaybackState();}
}
async function showPlayback(time,autoplay) {
  if(!playbackCurrent())return;
  const video=$('#montage-player');
  video.classList.remove('hidden');$('#scene-preview').classList.add('hidden');$('#preview-subtitle').classList.add('hidden');$('#preview-loading').classList.add('hidden');
  $('#edit-frame').classList.remove('hidden');$('#playback-position').classList.remove('hidden');$('#stage-label').textContent='ПРОСМОТР МОНТАЖА · 540p';
  function seek() {if(Number.isFinite(time))video.currentTime=Math.min(time,Math.max(0,video.duration-.04));markPlayingScene();}
  if(video.readyState>=1)seek();else video.addEventListener('loadedmetadata',seek,{once:true});
  $('#playback-status').textContent='Просмотр готов. Перематывай видео или выбирай сцену в ленте. «Изменить этот кадр» вернёт к оформлению.';
  if(autoplay) {
    video.scrollIntoView({behavior:'smooth',block:'center'});
    $('#voice-info audio').pause();
    try {await video.play();}
    catch {$('#playback-status').textContent='Просмотр готов. Нажми ▶ в плеере, чтобы запустить со звуком.';}
  }
  updatePlaybackState();
}
function rememberPlayback(record) {
  try {localStorage.setItem(playbackStorage,JSON.stringify(record));}catch {}
}
async function pollPlayback(record) {
  if(playback.record!==record)return;
  try {
    const state=await (await api(`/api/playback/${record.id}`)).json();
    if(playback.record!==record)return;
    $('#playback-progress').classList.remove('hidden');$('#playback-progress').value=state.progress;
    $('#playback-status').textContent=`${state.message}${state.status==='done'?'':` · ${state.progress}%`}`;
    if(state.status==='error') {
      if(state.diagnostic) {
        const report=state.diagnostic;
        $('#playback-error').textContent=`${report.message}\nЭтап: ${report.stage} (${report.progress}%)\nТип: ${report.error_type}\n${JSON.stringify(report.capabilities,null,2)}\n\n${report.detail}`;
        $('#download-playback-error').href=`/api/playback/${record.id}/diagnostic`;
        $('#playback-error-details').classList.remove('hidden');$('#playback-error-details').open=true;
      }
      playback.busy=false;playback.ready=false;playback.record=null;localStorage.removeItem(playbackStorage);
      $('#playback-status').textContent=state.message;$('#playback-progress').classList.add('hidden');updatePlaybackState();return;
    }
    if(state.status==='done') {
      playback.busy=false;playback.ready=true;
      const video=$('#montage-player'),url=`/api/playback/${record.id}/video`;
      if(video.getAttribute('src')!==url) {video.src=url;video.load();}
      $('#playback-progress').classList.add('hidden');
      if(record.signature===playbackSignature())await showPlayback(playback.seek,playback.autoplay);
      updatePlaybackState();return;
    }
    playback.busy=true;updatePlaybackState();setTimeout(()=>pollPlayback(record),900);
  } catch(e) {
    playback.busy=false;
    if(e.status===404){playback.record=null;playback.ready=false;localStorage.removeItem(playbackStorage);}
    $('#playback-status').textContent=`${e.message} Нажми «Смотреть монтаж», чтобы повторить.`;
    $('#playback-progress').classList.add('hidden');updatePlaybackState();
  }
}
async function watchMontage(time=null) {
  if(autoMediaTask)throw new Error('Дождись завершения подбора материалов.');
  if(playback.busy)return;
  const video=$('#montage-player');
  if(playbackCurrent()) {
    if(time===null && !video.paused && !video.classList.contains('hidden')) {video.pause();updatePlaybackState();return;}
    await showPlayback(time===null?(video.ended?0:video.currentTime):time,true);return;
  }
  $('#playback-error-details').classList.add('hidden');
  const signature=playbackSignature();playback.busy=true;playback.ready=false;playback.seek=time ?? 0;playback.autoplay=true;
  updatePlaybackState();$('#playback-status').textContent='Подготовка просмотра: фото, видео, озвучка, субтитры и переходы…';
  try {
    const job=await (await api('/api/playback',jsonPost(project))).json();
    const record={id:job.id,signature};playback.record=record;rememberPlayback(record);
    await pollPlayback(record);
  } catch(e) {playback.busy=false;$('#playback-status').textContent=e.message;updatePlaybackState();throw e;}
}
async function restorePlayback() {
  const saved=localStorage.getItem(playbackStorage);if(!saved)return;
  let record;try {record=JSON.parse(saved);if(!/^[a-f0-9]{32}$/.test(record.id)||typeof record.signature!=='string')throw new Error();}
  catch {localStorage.removeItem(playbackStorage);return;}
  playback.record=record;playback.autoplay=false;playback.seek=0;
  if(record.signature!==playbackSignature()){updatePlaybackState();return;}
  playback.busy=true;updatePlaybackState();await pollPlayback(record);
}
on('#watch-montage','click',()=>watchMontage());
on('#watch-scene','click',()=>watchMontage(sceneStart(selected)));
on('#edit-frame','click',()=>{showEditingFrame();refresh();});
on('#edit-playing-scene','click',()=>{const index=markPlayingScene();if(Number.isInteger(index))selected=index;showEditingFrame();refresh();$('#inspector-title').scrollIntoView({behavior:'smooth',block:'start'});});
$('#montage-player').addEventListener('timeupdate',markPlayingScene);
$('#montage-player').addEventListener('play',()=>{$('#voice-info audio').pause();updatePlaybackState();});
$('#montage-player').addEventListener('pause',updatePlaybackState);
$('#montage-player').addEventListener('ended',updatePlaybackState);
$('#montage-player').addEventListener('error',()=>{
  if(!$('#montage-player').getAttribute('src'))return;
  showEditingFrame();playback.ready=false;playback.record=null;localStorage.removeItem(playbackStorage);
  $('#playback-status').textContent='Не удалось открыть видео просмотра. Нажми «Смотреть монтаж», чтобы подготовить его заново.';updatePlaybackState();
});
$('#voice-info audio').addEventListener('play',()=>{$('#montage-player').pause();});

on('#copy-playback-error','click',async()=>{
  try {await navigator.clipboard.writeText($('#playback-error').textContent);toast('Отчёт скопирован. Его можно прислать в чат.');}
  catch {toast('Нажми «Скачать отчёт» или выдели текст отчёта и скопируй его.',true);}
});

async function applyReferenceLook() {
  const owner=project,snapshot=JSON.stringify(project);
  const revised=await (await api('/api/project/reference-effects',jsonPost(project))).json();
  if(project!==owner || JSON.stringify(project)!==snapshot)throw new Error('Проект изменился. Примени оформление ещё раз.');
  localStorage.setItem(effectsStorage,snapshot);project=revised;
}
on('#reference-effects','click',async()=>{
  if(autoMediaTask || speechRecord)throw new Error('Сначала дождись распознавания и подбора или останови их.');
  await applyReferenceLook();refresh();toast('Приёмы применены. Звуки — только при появлении текста. Обнови просмотр или экспорт.');
});
on('#undo-effects','click',()=>{
  if(autoMediaTask || speechRecord)throw new Error('Сначала останови текущую задачу.');
  const saved=localStorage.getItem(effectsStorage);if(!saved)return;
  project=JSON.parse(saved);project.effects_version=18;selected=0;localStorage.removeItem(effectsStorage);refresh();
});
