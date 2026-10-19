'use strict';
let materialCheck={key:null,report:null,promise:null,error:''},materialCheckTimer;
function materialCheckKey(){
  return JSON.stringify([project.director_version,project.scenes,project.subtitles,project.voice_id,project.music_id]);
}
function checkedMaterials(){return materialCheck.key===materialCheckKey()?materialCheck.report:null;}
function scheduleMaterialCheck(){
  if(!project)return;
  const key=materialCheckKey();
  if(materialCheck.key===key)return;
  clearTimeout(materialCheckTimer);
  materialCheck={key,report:null,promise:null,error:''};
  materialCheckTimer=setTimeout(()=>ensureMaterialCheck().catch(()=>{}),150);
}
async function ensureMaterialCheck(force=false){
  clearTimeout(materialCheckTimer);
  const owner=project,key=materialCheckKey();
  if(!force && materialCheck.key===key && materialCheck.report)return materialCheck.report;
  if(materialCheck.key===key && materialCheck.promise)return materialCheck.promise;
  const state={key,report:null,promise:null,error:''};materialCheck=state;
  state.promise=(async()=>{
    try{
      const report=await (await api('/api/project/readiness',jsonPost(owner))).json();
      if(project!==owner || key!==materialCheckKey())throw new Error('Монтаж изменился во время проверки. Повтори действие.');
      if(materialCheck===state){state.report=report;state.error='';}
      return report;
    }catch(e){if(materialCheck===state)state.error=e.message;throw e;}
    finally{
      state.promise=null;
      if(materialCheck===state){paintWorkflow();if($('#frames-dialog').open)paintFrames();}
    }
  })();
  return state.promise;
}
function sceneReadinessReason(index){return checkedMaterials()?.scenes[index]?.reason || '';}
