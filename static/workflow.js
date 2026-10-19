'use strict';
let workflowInitialized=false;
const workflowTitles=[
  ['Расскажи свою историю.','Один аудиофайл — начало твоего фильма.'],
  ['Проверь, всё ли верно.','Исправь имена и термины или сразу переходи к монтажу.'],
  ['Пусть история оживёт.','Программа подберёт кадры и назначит оформление. Ты можешь заменить любой материал.'],
  ['Твоя история обретает форму.','Посмотри монтаж со звуком, затем сохрани готовый фильм.']
];

function hasMontageContent() {
  return !!project?.subtitles.length || project?.scenes.some(s=>s.asset_id || s.title?.trim() || s.caption?.trim());
}
function missingVisuals() {
  return project.scenes.map((s,i)=>({s,i})).filter(({s})=>!s.asset_id &&
    (!['quote','thesis'].includes(s.template) || !(s.title?.trim() || s.caption?.trim() || s.text_events?.length) ||
     (s.automatic_text && s.text_events?.length && s.text_events.reduce((sum,e)=>sum+e.end-e.start,0)<s.duration-.25)));
}
function paintWorkflow() {
  if(!workflowInitialized || !project)return;
  const step=[1,2,3,4].includes(project.workflow_step)?project.workflow_step:(project.workflow_step=hasMontageContent()?3:1);
  const hasText=!!project.subtitles.length,hasContent=hasMontageContent(),busy=!!speechRecord || !!autoMediaTask;
  const missing=missingVisuals();
  $$('.workflow-panel').forEach((panel,i)=>panel.hidden=i+1!==step);
  $$('.workflow-nav [data-step]').forEach(button=>{
    const n=Number(button.dataset.step);
    if(n===step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');
    button.disabled=n>=3 && (!hasContent || !!speechRecord || (n===4 && !!autoMediaTask));
    const done=n===1?!!project.voice_id || hasText:n===2?hasText:n===3?hasContent && !missing.length && !busy:false;
    button.querySelector('.step-check').textContent=done?'✓':'';
  });
  $('#workflow-counter').textContent=`ШАГ ${step} ИЗ 4`;
  $('#workflow-title').textContent=workflowTitles[step-1][0];
  $('#workflow-description').textContent=workflowTitles[step-1][1];
  $('#workflow-back').hidden=step===1;
  $('#workflow-next').hidden=step===4;
  $('#workflow-next').disabled=!!speechRecord || (step===1?!hasContent && !project.voice_id:!hasContent || (step===3 && !!autoMediaTask));
  $('#workflow-next').textContent=speechRecord?'Распознаём речь…':step===1?hasText?'Дальше: проверить текст →':project.voice_id?'Распознать и продолжить →':'Дальше: текст →':step===2?'Дальше: собрать монтаж →':autoMediaTask?'Подбираем кадры…':'Дальше: посмотреть фильм →';
  $('#workflow-hint').textContent=speechRecord?'Дождись распознавания. Оно продолжится после обновления страницы.':step===1?hasText?'Текст готов. Перейди к проверке фраз.':project.voice_id?'Озвучка загружена. После распознавания откроется проверка текста.':'Добавь озвучку или готовые субтитры, чтобы начать.':step===2?hasText?`${project.subtitles.length} фраз. Дальше — автоматический подбор и оформление.`:'Нет распознанных фраз. Можно вернуться к озвучке.':step===3?autoMediaTask?'Ищем и расставляем материалы. Уже найденные кадры сохраняются.':missing.length?`Для ${missing.length} сцен ещё нужны кадры. Повтори подбор или загрузи свои.`:'Кадры на месте. Можно смотреть монтаж.':'Готовый MP4 появится после экспорта.';
  $('#workflow-text-count').textContent=`${project.subtitles.length} фраз`;
  $('#transcript-empty').hidden=hasText;
  $('#transcript').hidden=!hasText;
  $('#workflow-material-status').textContent=hasContent?`${project.scenes.length-missing.length} из ${project.scenes.length} сцен готовы${missing.length?` · нужны кадры: ${missing.map(({i})=>i+1).join(', ')}`:' · можно переходить к просмотру'}.`:'Сначала добавь текст рассказа.';
  $('#workflow-missing').hidden=!missing.length;
  $('#workflow-missing-detail').textContent=`Сцены ${missing.map(({i})=>i+1).join(', ')}. Повтори подбор или добавь свои фото и видео на шаге «Монтаж».`;
  $('#workflow-review-summary').textContent=`${seconds(duration())} · ${project.scenes.length} сцен · ${project.voice_id?'с твоей озвучкой':'озвучка не добавлена'}`;
  // One canvas/player, moved between editing and review without duplicate IDs.
  const target=step===3?$('#edit-stage'):$('#review-stage'),stage=$('.stage-shell');
  if(stage.parentElement!==target)target.append(stage);
}

async function enterWorkflowStep(step, {scroll=true, auto=true}={}) {
  if(step>=3 && (!hasMontageContent() || speechRecord)) {
    toast(speechRecord?'Дождись распознавания речи.':'Сначала добавь озвучку и распознай речь или загрузи SRT/VTT.',true);return;
  }
  if(step!==4)$('#montage-player').pause();
  project.workflow_step=step;
  if(step===3)showEditingFrame();
  save();paintWorkflow();
  if(step>=3)queuePreview();
  if(scroll){$('#workflow-title').focus({preventScroll:true});$('.workflow-nav').scrollIntoView({behavior:'smooth',block:'start'});}
  if(step===3 && auto && project.workflow_media_pending && (project.auto_media_after_subtitles ?? true) && !autoMediaTask && !speechRecord) {
    project.workflow_media_pending=false;save();
    await startAutomaticMedia(false);
  }
}

function transcriptReady() {
  project.workflow_media_pending=true;
  // Review is a real step: media search starts after the user continues.
  enterWorkflowStep(2,{auto:false}).catch(e=>toast(e.message,true));
}
function openSceneEditor() {
  $('#scene-editor').open=true;
  if(project.workflow_step!==3)enterWorkflowStep(3,{auto:false});
}
function initWorkflow() {
  const legacy=![1,2,3,4].includes(project.workflow_step);
  if(legacy)project.workflow_step=hasMontageContent()?3:1;
  if(legacy && project.subtitles.length && missingVisuals().length)project.workflow_media_pending=true;
  if(!workflowInitialized){
    on('#workflow-next','click',async()=>{
      if(project.workflow_step===1 && !hasMontageContent() && project.voice_id){await startSpeech();return;}
      await enterWorkflowStep(Math.min(4,project.workflow_step+1));
    });
    on('#workflow-back','click',()=>enterWorkflowStep(Math.max(1,project.workflow_step-1),{auto:false}));
    $$('[data-step]').forEach(button=>button.addEventListener('click',()=>enterWorkflowStep(Number(button.dataset.step)).catch(e=>toast(e.message,true))));
    $$('[data-go-step]').forEach(button=>button.addEventListener('click',()=>enterWorkflowStep(Number(button.dataset.goStep),{auto:false}).catch(e=>toast(e.message,true))));
    on('#connect-sources','click',()=>{
      $('#media-library').open=true;$('#manual-videos').open=true;$('#manual-videos details').open=true;
      $('#stock-key-status').scrollIntoView({behavior:'smooth',block:'center'});
    });
  }
  workflowInitialized=true;paintWorkflow();save();
}

function canRenderWorkflow() {
  if(!hasMontageContent()){toast('Сначала создай монтаж на первых трёх шагах.',true);return false;}
  if(speechRecord){toast('Дождись распознавания речи.',true);return false;}
  if(autoMediaTask){toast('Дождись окончания подбора материалов.',true);return false;}
  const missing=missingVisuals();
  if(missing.length){
    enterWorkflowStep(3,{auto:false});
    toast(`Нужны кадры для сцен: ${missing.map(({i})=>i+1).join(', ')}. Повтори подбор или загрузи свои фото и видео.`,true);
    return false;
  }
  return true;
}
