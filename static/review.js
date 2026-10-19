'use strict';
let framesFilter='ready';
const colorBackupStorage='kadr-before-color-transitions-v21';
function readySceneIndices(){const missing=new Set(missingVisuals().map(({i})=>i));return project.scenes.map((s,i)=>i).filter(i=>!missing.has(i));}

function paintReviewControls(){
  if(!project)return;
  const ready=readySceneIndices(),busy=!!autoMediaTask || !!speechRecord;
  $$('[data-watch-available]').forEach(button=>{
    button.disabled=!ready.length || busy || playback.busy;
    button.textContent=`▶ Смотреть найденные сцены (${ready.length})`;
  });
  $('#review-frames').disabled=!hasMontageContent();
  $('#retry-missing').disabled=busy || !missingVisuals().length;
  $('#retry-missing').hidden=!missingVisuals().length;
  $('#apply-color-transitions').disabled=busy || project.scenes.length<2;
  $('#undo-color-transitions').classList.toggle('hidden',!localStorage.getItem(colorBackupStorage));
  let time=0;const colored=[];
  project.scenes.slice(0,-1).forEach(scene=>{time+=Math.round(scene.duration*30)/30;if(['light_leak','color_wash'].includes(scene.transition_out))colored.push(seconds(time));});
  $('#color-transition-summary').textContent=colored.length?`Цветные переходы: ${colored.length} · ${colored.join(' · ')}. Видны при просмотре и в MP4.`:'Цветные переходы назначатся на сменах кадров; яркий текст в начале сцены остаётся читаемым.';
}
function paintFrames(){
  const ready=new Set(readySceneIndices());$('#frames-grid').replaceChildren();
  $('#frames-summary').textContent=`С кадром: ${ready.size} из ${project.scenes.length}. Выбери сцену для просмотра или замены. Исходная озвучка и таймкоды сохраняются.`;
  $$('[data-frame-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.frameFilter===framesFilter)));
  project.scenes.forEach((scene,index)=>{
    const found=ready.has(index);
    if(framesFilter==='ready' && !found || framesFilter==='missing' && found)return;
    const card=document.createElement('article');card.className='frame-review-card';card.dataset.sceneIndex=index;
    const art=document.createElement('div');art.className='frame-review-art';
    if(scene.asset_id){const img=document.createElement('img');img.src=`/api/assets/${scene.asset_id}/thumb`;img.alt=`Кадр сцены ${index+1}`;img.loading='lazy';art.append(img);}
    else{art.textContent=found?(scene.title || 'Текстовая композиция'):'Кадр ещё не найден';art.classList.add(found?'graphic':'empty');}
    const heading=document.createElement('h3');heading.textContent=`Сцена ${index+1} · ${seconds(sceneStart(index))}–${seconds(sceneStart(index)+scene.duration)}`;
    const caption=document.createElement('p');caption.textContent=sceneText(index) || scene.title || scene.caption || 'Без текста';
    const source=document.createElement('small');source.textContent=scene.asset_id?(media(scene.asset_id)?.name || 'Назначенный материал'):found?'Графика из текста':'Нужен кадр';
    const actions=document.createElement('div');actions.className='frame-review-actions';
    function action(label,cls,fn){const b=document.createElement('button');b.textContent=label;b.className=cls;b.addEventListener('click',()=>Promise.resolve().then(fn).catch(e=>toast(e.message,true)));actions.append(b);return b;}
    if(found)action('▶ Смотреть сцену','primary',async()=>{$('#frames-dialog').close();selected=index;await watchMontage(0,[index]);});
    action(found?'Заменить кадр':'Добавить кадр','secondary',()=>{
      $('#frames-dialog').close();selected=index;openSceneEditor();refresh();$('#replace-frame').click();
    });
    const next=action('Найти другой вариант','text-button',async()=>{
      $('#frames-dialog').close();selected=index;await enterWorkflowStep(3,{auto:false});
      await startAutomaticMedia(true,[index],scene.asset_id?[scene.asset_id]:[]);
    });next.disabled=!!autoMediaTask || !!speechRecord;
    card.append(art,heading,caption,source,actions);$('#frames-grid').append(card);
  });
  if(!$('#frames-grid').children.length){const empty=document.createElement('p');empty.className='empty-step';empty.textContent=framesFilter==='missing'?'Для всех сцен уже есть кадры.':'Найденных кадров пока нет. Переключись на «Без кадра» или запусти подбор.';$('#frames-grid').append(empty);}
}
function openFrames(){framesFilter=readySceneIndices().length?'ready':'missing';paintFrames();$('#frames-dialog').showModal();}
async function applyColorTransitions(){
  const owner=project,before=JSON.stringify(project);
  const revised=await (await api('/api/project/color-transitions',jsonPost(project))).json();
  if(project!==owner || JSON.stringify(project)!==before)throw new Error('Проект изменился. Повтори обновление переходов.');
  localStorage.setItem(colorBackupStorage,before);project=revised;
}
function initReview(){
  on('#review-frames','click',openFrames);on('#review-missing-frames','click',openFrames);
  on('#close-frames','click',()=>$('#frames-dialog').close());on('#frames-back','click',()=>$('#frames-dialog').close());
  $$('[data-frame-filter]').forEach(button=>button.addEventListener('click',()=>{framesFilter=button.dataset.frameFilter;paintFrames();}));
  $$('[data-watch-available]').forEach(button=>button.addEventListener('click',()=>{
    $('#frames-dialog').close();watchMontage(null,readySceneIndices()).catch(e=>toast(e.message,true));
  }));
  on('#retry-missing','click',()=>startAutomaticMedia(false,missingVisuals().map(({i})=>i)));
  on('#apply-color-transitions','click',async()=>{
    if(autoMediaTask || speechRecord)throw new Error('Дождись завершения текущей задачи.');
    await applyColorTransitions();refresh();toast('Цветные засветки и переливы обновлены. Пересобери просмотр, чтобы увидеть их.');
  });
  on('#undo-color-transitions','click',()=>{
    if(autoMediaTask || speechRecord)throw new Error('Дождись завершения текущей задачи.');
    const saved=localStorage.getItem(colorBackupStorage);if(!saved)return;
    const before=JSON.parse(saved);
    // Restore only transitions; edits to media, text and audio stay intact.
    project.scenes.forEach((s,i)=>{if(before.scenes[i] && s.duration===before.scenes[i].duration){s.transition_out=before.scenes[i].transition_out;s.transition_duration=before.scenes[i].transition_duration;}});
    project.transitions_version=1;localStorage.removeItem(colorBackupStorage);refresh();
  });
}
