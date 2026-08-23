'use strict';

function renderOrgContent(){
  var members=(state.plan.input.members||[]).slice();
  var byManager={};
  members.forEach(function(m){var key=(m.manager||'').trim();(byManager[key]=byManager[key]||[]).push(m)});
  function renderNode(m){
    var children=byManager[m.name]||[];
    return '<li class="org-item"><div class="org-node"><span class="org-name">'+esc(m.name)+'</span><span class="org-role">'+esc(m.role||'执行成员')+'</span><small>上级：'+(m.manager?esc(m.manager):'顶层')+'</small></div>'+(children.length?'<ul class="org-tree-children">'+children.map(renderNode).join('')+'</ul>':'')+'</li>';
  }
  var roots=members.filter(function(m){var mgr=(m.manager||'').trim();return !mgr||!byManager[mgr]});
  return members.length?'<div class="org-summary">'+members.length+' 名成员 · '+roots.length+' 个顶层节点</div><ul class="org-tree">'+roots.map(renderNode).join('')+'</ul>':'<div class="success-box">暂无成员</div>';
}

function bindOrgContent(){}

function participantNamesOptions(){
  var names=[];
  (state.plan.input.members||[]).forEach(function(m){if(names.indexOf(m.name)<0)names.push(m.name)});
  (state.plan.volunteer_pool||[]).forEach(function(v){if(v.name&&names.indexOf(v.name)<0)names.push(v.name)});
  var dl=document.getElementById('participantNames');
  if(dl)dl.innerHTML=names.map(function(n){return '<option value="'+esc(n)+'"></option>'}).join('');
  return names;
}

function participantRowHtml(p){
  p=p||{};
  var isVol=!!p.is_volunteer;
  var hours=p.contribution_hours!=null?p.contribution_hours:0;
  var roleControl=isVol?'<input class="participant-role" value="志愿者" readonly>':'<select class="participant-role">'+roleOptionsHtml(p.role||'执行成员')+'</select>';
  return '<div class="participant-row"><input class="participant-name" list="participantNames" value="'+esc(p.name||'')+'" placeholder="姓名">'+roleControl+'<input class="participant-hours" type="number" min="0" step="0.5" value="'+hours+'"><label class="participant-vol"><input type="checkbox" class="participant-volunteer" '+(isVol?'checked':'')+'>志愿者</label><button type="button" class="participant-remove icon-danger">×</button></div>';
}

function memberRole(name){
  var m=(state.plan.input.members||[]).find(function(x){return x.name===name});
  return m?m.role:'';
}

function buildDefaultParticipants(task){
  var list=[];
  if(task.assignee_id)list.push({name:task.assignee_id,role:memberRole(task.assignee_id)||'执行成员',contribution_hours:task.estimated_hours||0,is_volunteer:false});
  (task.collaborator_ids||[]).forEach(function(c){list.push({name:c,role:'协作',contribution_hours:Math.round(task.estimated_hours*0.3*10)/10,is_volunteer:false})});
  (state.plan.volunteer_pool||[]).filter(function(v){return v.task_id===task.id&&v.status!=='已婉拒'}).forEach(function(v){list.push({name:v.name,role:'志愿者 / 外部协作者',contribution_hours:Math.round(task.estimated_hours*0.5*10)/10,is_volunteer:true})});
  return list;
}

function renderParticipantTask(task){
  var rows=(task.participants&&task.participants.length?task.participants:buildDefaultParticipants(task)).map(participantRowHtml).join('');
  return '<div class="participant-task" data-task-id="'+esc(task.id)+'"><header><span class="task-code">'+esc(task.id)+'</span><div><strong>'+esc(task.name)+'</strong><small>计划 '+task.estimated_hours+'h · '+(task.assignee_id||'未分配')+'</small></div></header><div class="participant-list">'+rows+'</div><div class="participant-actions"><button class="btn-small participant-add" type="button">＋ 添加参与者</button><button class="btn-small participant-save" type="button">保存参与清单</button></div></div>';
}

function renderParticipantsContent(){
  participantNamesOptions();
  var tasks=state.plan.plan.tasks||[];
  return '<div class="participant-panel">'+tasks.map(renderParticipantTask).join('')+'</div>';
}

function bindParticipantsControls(){
  document.querySelectorAll('.participant-task').forEach(function(card){
    var addBtn=card.querySelector('.participant-add');
    var saveBtn=card.querySelector('.participant-save');
    if(addBtn)addBtn.onclick=function(){var list=card.querySelector('.participant-list');if(list)list.insertAdjacentHTML('beforeend',participantRowHtml({role:'执行成员'}))};
    if(saveBtn)saveBtn.onclick=function(){saveTaskParticipants(card.dataset.taskId)};
    card.querySelectorAll('.participant-remove').forEach(function(btn){btn.onclick=function(){btn.closest('.participant-row').remove()}});
  });
}

async function saveTaskParticipants(taskId){
  var card=document.querySelector('.participant-task[data-task-id="'+taskId+'"]');
  if(!card)return;
  var rows=[];
  card.querySelectorAll('.participant-row').forEach(function(row){
    var name=row.querySelector('.participant-name').value.trim();
    if(!name)return;
    var role=row.querySelector('.participant-role').value;
    var hours=+row.querySelector('.participant-hours').value||0;
    var isVol=row.querySelector('.participant-volunteer').checked;
    rows.push({name:name,role:role,contribution_hours:hours,is_volunteer:isVol,status:'已确认'});
  });
  try{
    state.plan=await jsonRequest('/api/task-participants',{plan:state.plan,task_id:taskId,participants:rows});
    renderResultTab('collaboration');
    showNotice('参与清单已保存','success');
  }catch(e){
    showNotice(e.message,'error');
  }
}

async function loadResourceCalendarTab(targetId){
  var target=el(targetId||'resultContent');
  if(!target)return;
  try{
    var data=await jsonRequest('/api/resource-calendar',state.plan);
    target.innerHTML=renderResourceCalendarHtml(data);
  }catch(e){
    target.innerHTML='<div class="resource-calendar">'+alertPanelHtml('资源日历加载失败',e.message,'error')+'</div>';
  }
}

function renderResourceCalendarHtml(data){
  if(!data.days||!data.days.length){
    return '<div class="resource-calendar"><div class="success-box">暂无带排期的任务</div></div>';
  }
  var members=data.members||{};
  var vols=data.volunteers||{};
  var warnings=data.warnings&&data.warnings.length
    ? alertPanelHtml('资源冲突',data.warnings,'warning')
    : '';
  function dayCells(load,unavailable){
    return data.days.map(function(d){
      var day=String(d).slice(0,10);
      var h=load&&load[d]?load[d]:0;
      var cls='cal-cell'+(h>0?' has-load':'')+((unavailable||[]).indexOf(d)>=0?' unavailable':'');
      return '<div class="'+cls+'" title="'+day+'">'+(h>0?Math.round(h*10)/10:'')+'</div>';
    }).join('');
  }
  function memberCard(name,m){
    var unavailable=m.unavailable_dates||[];
    var tasks=m.tasks&&m.tasks.length
      ? '<div class="cal-tasks">'+m.tasks.map(function(t){return '<span>'+esc(t.id)+' '+esc(t.name)+' · '+t.hours+'h</span>'}).join('')+'</div>'
      : '';
    return '<div class="cal-member"><div class="cal-member-head"><strong>'+esc(name)+'</strong><span>'+esc(m.role||'执行成员')+' · 每日 '+m.daily_available_hours+'h</span></div><div class="cal-row"><div class="cal-name">'+esc(name)+'</div><div class="cal-cells">'+dayCells(m.daily_load,unavailable)+'</div></div>'+tasks+'</div>';
  }
  var memberHtml=Object.keys(members).map(function(name){return memberCard(name,members[name])}).join('');
  var volHtml=Object.keys(vols).map(function(name){
    var v=vols[name];
    var tasks=v.tasks&&v.tasks.length
      ? '<div class="cal-tasks">'+v.tasks.map(function(t){return '<span>'+esc(t.id)+' '+esc(t.name)+' · '+t.hours+'h</span>'}).join('')+'</div>'
      : '';
    return '<div class="cal-member cal-volunteer"><div class="cal-member-head"><strong>'+esc(name)+'</strong><span>志愿者 / 外部协作者</span></div><div class="cal-row"><div class="cal-name">'+esc(name)+'</div><div class="cal-cells">'+dayCells(v.daily_load,[])+'</div></div>'+tasks+'</div>';
  }).join('');
  var header='<div class="cal-row cal-header-row"><div class="cal-name">成员 / 日期</div><div class="cal-cells">'+data.days.map(function(d){return '<div class="cal-cell cal-day">'+String(d).slice(0,10).slice(5)+'</div>'}).join('')+'</div></div>';
  return '<div class="resource-calendar">'+warnings+'<div class="cal-summary"><span>'+data.days.length+' 天 · '+String(data.days[0]).slice(0,10)+' → '+String(data.days[data.days.length-1]).slice(0,10)+'</span></div>'+header+memberHtml+(volHtml?'<h3 class="cal-vol-title">志愿者 / 外部协作者</h3>'+volHtml:'')+'</div>';
}

async function shareCurrentPlan(){
  try{
    if(!state.lastSavedFilename){
      var saved=await jsonRequest('/api/save',state.plan);
      state.lastSavedFilename=saved.filename;
    }
    var data=await jsonRequest('/api/share',{filename:state.lastSavedFilename});
    var url=location.origin+'/?share='+data.token;
    showLinkModal('只读分享链接已生成',url);
  }catch(e){showNotice(e.message,'error')}
}

async function applyShareQuery(){
  var params=new URLSearchParams(location.search);
  var token=params.get('share');
  if(!token)return;
  try{
    var r=await fetch('/api/share/'+encodeURIComponent(token));
    var d=await r.json();
    if(!r.ok)throw Error(d.detail||'分享链接无效');
    state.plan=d;
    state.input=d.input;
    state.draft=d.plan;
    state.automatic=JSON.parse(JSON.stringify(d));
    state.shareToken=token;state.readOnly=true;
    document.body.classList.add('readonly');
    enableAuthFetch();
    var controls=['saveBtn','exportMdBtn','exportDocxBtn','exportPdfBtn','exportExcelBtn','exportCsvBtn','exportIcsBtn','shareBtn','confirmDraftBtn','confirmAssignmentBtn'];
    controls.forEach(function(id){var b=el(id);if(b)b.disabled=true});
    renderFinal();
    setView('final',isLargeProject()?5:3);
    resetChatMemory();
    showNotice('只读分享模式：可查看，不可编辑','info');
  }catch(e){showNotice(e.message,'error')}
}

async function loadRemindersTab(){
  var target=el('remindersContent');
  if(!target)return;
  try{
    var data=await jsonRequest('/api/reminders',state.plan);
    var items=data.reminders||[];
    var listHtml=items.length
      ? items.map(function(r){return '<div class="reminder-card '+esc(r.type)+'"><strong>'+esc(r.title)+'</strong><span>'+esc(r.detail)+'</span></div>'}).join('')
      : '<div class="success-box">当前没有待处理提醒</div>';
    target.innerHTML='<div class="interview-actions"><button id="notifyBtn" class="btn btn-primary">发送提醒通知</button><button id="broadcastBtn" class="btn btn-ghost">🔊 今日播报</button></div>'+listHtml;
    var notifyBtn=el('notifyBtn');
    if(notifyBtn)notifyBtn.onclick=sendNotify;
    var broadcastBtn=el('broadcastBtn');
    if(broadcastBtn)broadcastBtn.onclick=todayBroadcast;
  }catch(e){target.innerHTML=alertPanelHtml('提醒加载失败',e.message,'error')}
}

async function todayBroadcast(){
  try{
    var data=await jsonRequest('/api/reminders',state.plan);
    var items=data.reminders||[];
    var tasks=(state.plan&&state.plan.plan&&state.plan.plan.tasks)||[];
    var today=localIsoDate(new Date());
    var dueToday=tasks.filter(function(t){return t.status!=='completed'&&t.end_date&&String(t.end_date).slice(0,10)===today});
    var lines=['今天是 '+new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric'})+'。'];
    if(items.length){lines.push('提醒 '+items.length+' 条：');items.slice(0,5).forEach(function(r){lines.push(r.title+'：'+r.detail)})}
    if(dueToday.length){lines.push('今天到期 '+dueToday.length+' 项：'+dueToday.slice(0,3).map(function(t){return t.name}).join('、'))}
    var text=lines.join('\n');
    if(state.realtime&&state.realtime.enabled&&!state.realtimeFallback&&state.realtime.backend==='map'){
      var tts=await jsonRequest('/api/realtime/tts',{text:text.slice(0,1200)});
      if(tts.audio_wav_base64){playRealtimeAudio(tts.audio_wav_base64);showNotice('正在播报今日要点','success');return}
      throw Error('未返回音频');
    }
    showNotice(text,'info');
  }catch(e){showNotice('今日播报失败：'+e.message,'error')}
}

async function sendNotify(){
  try{
    var data=await jsonRequest('/api/notify',state.plan);
    if(!data.enabled){
      var browserResult=await sendBrowserNotifications(data.reminders||[]);
      if(browserResult.sent){
        showNotice('已发送 '+browserResult.count+' 条系统通知','success');
      }else{
        showNotice(browserResult.message,'info');
      }
      return;
    }
    if(data.sent){
      showNotice('已推送 '+data.reminders.length+' 条提醒','success');
    }else{
      showNotice('推送失败：'+(data.error||'未知错误'),'error');
    }
  }catch(e){showNotice(e.message,'error')}
}

async function sendBrowserNotifications(items){
  if(!items.length)return{sent:false,count:0,message:'当前没有需要发送的提醒'};
  if(!('Notification' in window))return{sent:false,count:0,message:'当前浏览器不支持系统通知，请配置外部通知地址'};
  var permission=Notification.permission;
  if(permission==='default')permission=await Notification.requestPermission();
  if(permission!=='granted')return{sent:false,count:0,message:'浏览器通知权限未开启，无法发送系统通知'};
  items.forEach(function(item){
    new Notification(item.title||'项目提醒',{
      body:item.detail||state.plan.input.course.name,
      tag:'workbuddy-'+(item.type||'reminder')+'-'+(item.title||''),
    });
  });
  return{sent:true,count:items.length,message:''};
}

function bindKnowledgeControls(){
  var btn=el('knowledgeAskBtn');
  if(btn)btn.onclick=askKnowledge;
  var agentBtn=el('agentAskBtn');
  if(agentBtn)agentBtn.onclick=askKnowledgeAgent;
  document.querySelectorAll('.tool-bar [data-tool]').forEach(function(b){
    b.onclick=function(){callKnowledgeTool(b)};
  });
}

async function askKnowledge(){
  var input=el('knowledgeQuestion');
  var q=input.value.trim();
  if(!q)return;
  var answer=el('knowledgeAnswer');
  answer.innerHTML='<p style="color:#6b7891">查询中…</p>';
  try{
    var data=await jsonRequest('/api/knowledge',{question:q,plan:state.plan});
    var sources=data.sources&&data.sources.length
      ? '<div class="knowledge-sources">'+data.sources.map(function(s){return '<span>'+esc(s.name)+'</span>'}).join('')+'</div>'
      : '';
    answer.innerHTML='<div class="assistant-msg">'+esc(data.answer).replace(/\n/g,'<br>')+'</div>'+sources;
  }catch(e){answer.innerHTML=alertPanelHtml('查询失败',e.message,'error')}
}

async function callKnowledgeTool(btn){
  var answer=el('knowledgeAnswer');
  answer.innerHTML='<p style="color:#6b7891">正在调用工具…</p>';
  try{
    var data=await jsonRequest('/api/tools/call',{tool:btn.dataset.tool,args:{},plan:state.plan});
    answer.innerHTML='<pre class="tool-output">'+esc(JSON.stringify(data.result,null,2))+'</pre>';
  }catch(e){answer.innerHTML=alertPanelHtml('工具调用失败',e.message,'error')}
}

async function askKnowledgeAgent(){
  var input=el('knowledgeQuestion');
  var q=input.value.trim();
  if(!q)return;
  var answer=el('knowledgeAnswer');
  answer.innerHTML='<p style="color:#6b7891">Agent 正在分析…</p>';
  try{
    var data=await jsonRequest('/api/agent/ask',{question:q,plan:state.plan});
    var trace=data.trace&&data.trace.length
      ? '<div class="knowledge-sources"><span>调用：'+data.trace.map(esc).join(' → ')+'</span></div>'
      : '';
    answer.innerHTML='<div class="assistant-msg">'+esc(data.answer).replace(/\n/g,'<br>')+'</div>'+trace;
  }catch(e){answer.innerHTML=alertPanelHtml('分析失败',e.message,'error')}
}

async function loadOrgReviewTab(){
  var target=el('orgReviewContent');
  if(!target)return;
  try{
    var data=await jsonRequest('/api/org-review',state.plan);
    var memberRows=Object.keys(data.members||{}).map(function(name){
      var m=data.members[name];
      return '<tr><td>'+esc(name)+'</td><td>'+esc(m.role||'执行成员')+'</td><td>'+m.planned_hours+'h</td><td>'+m.actual_hours+'h</td><td>'+Math.round((m.actual_hours-m.planned_hours)*10)/10+'h</td></tr>';
    }).join('');
    var roleRows=Object.keys(data.roles||{}).map(function(role){
      var r=data.roles[role];
      return '<tr><td>'+esc(role)+'</td><td>'+r.planned_hours+'h</td><td>'+r.actual_hours+'h</td><td>'+Math.round((r.actual_hours-r.planned_hours)*10)/10+'h</td></tr>';
    }).join('');
    var suggestions=(data.suggestions||[]).length
      ? '<div class="review-summary"><div><strong>'+data.suggestions.length+'</strong><span>经验建议</span></div></div><ul class="org-suggestions">'+data.suggestions.map(function(s){return '<li>'+esc(s)+'</li>'}).join('')+'</ul>'
      : '<div class="success-box">暂无经验建议</div>';
    target.innerHTML='<h3 class="cal-vol-title">成员复盘</h3><div class="review-table-wrap"><table><thead><tr><th>成员</th><th>角色</th><th>计划</th><th>实际</th><th>偏差</th></tr></thead><tbody>'+memberRows+'</tbody></table></div><h3 class="cal-vol-title">角色复盘</h3><div class="review-table-wrap"><table><thead><tr><th>角色</th><th>计划</th><th>实际</th><th>偏差</th></tr></thead><tbody>'+roleRows+'</tbody></table></div>'+suggestions;
  }catch(e){target.innerHTML=alertPanelHtml('组织复盘加载失败',e.message,'error')}
}
