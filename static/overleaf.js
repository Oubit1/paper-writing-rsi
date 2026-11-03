"use strict";

(async () => {
  const $ = id => document.getElementById(id);
  const arr = v => Array.isArray(v) ? v : [];
  const el = (tag, cls, value) => { const n=document.createElement(tag); if(cls)n.className=cls; if(value!==undefined)n.textContent=String(value); return n; };
  const pending = v => !v.status || ["pending","partially_accepted"].includes(v.status);
  const active = v => v && ["queued","running","cancelling"].includes(v.status);
  function reviewCollections(doc){
    const all=arr(doc?.proposals);
    const local=all.filter(p=>p.review_mode==="local"&&pending(p)&&p.active!==false&&arr(p.edits||p.hunks).some(pending));
    const firstOffset=p=>Math.min(...arr(p.edits||p.hunks).filter(pending).map(h=>h.current_start??h.start??Infinity));
    local.sort((a,b)=>firstOffset(a)-firstOffset(b));
    const layout=all.filter(p=>p.review_mode==="layout"&&pending(p)&&p.active!==false&&arr(p.edits||p.hunks).some(pending));
    const structural=all.filter(p=>p.review_mode==="structural"&&pending(p)&&p.active!==false&&arr(p.edits||p.hunks).some(pending));
    return {local,layout,structural,legacy:all.filter(p=>!["local","layout","structural"].includes(p.review_mode))};
  }
  function sourceCharacterOffset(text,index){const characters=Array.from(text);return Number.isInteger(index)&&index>=0&&index<=characters.length?characters.slice(0,index).join("").length:null;}
  function exactSourceRange(text,hunk){
    const original=hunk.original_tex||"",position=hunk.current_start??hunk.start,endPosition=hunk.current_end??hunk.end;
    const start=sourceCharacterOffset(text,position),end=sourceCharacterOffset(text,endPosition);
    if(start!==null&&end!==null&&text.slice(start,end)===original)return {start,end};
    if(original){const first=text.indexOf(original);if(first>=0&&first===text.lastIndexOf(original))return {start:first,end:first+original.length};}
    return null;
  }
  function lineIssueIndex(text,issues){
    const map=new Map();
    for(const proposal of issues)for(const hunk of arr(proposal.edits||proposal.hunks).filter(pending)){
      const range=exactSourceRange(text,hunk);if(!range)continue;
      const line=text.slice(0,range.start).split("\n").length,entries=map.get(line)||[];
      if(!entries.some(item=>item.proposal.id===proposal.id))entries.push({proposal,hunkId:hunk.id,range,title:proposal.title||hunk.title||"局部文字问题",reason:hunk.reason||proposal.reason||"待审修改"});
      map.set(line,entries);
    }
    return map;
  }
  function issuePresentation(proposal){
    const local=proposal?.review_mode==="local",layout=proposal?.review_mode==="layout",structural=proposal?.review_mode==="structural";
    return {local,layout,structural,hunks:local||layout?arr(proposal.hunks).filter(pending):arr(proposal?.hunks),context:local?proposal.context||null:null,editable:(local||layout||structural)&&pending(proposal)&&proposal.read_only!==true};
  }
  function structuralUnits(proposal){
    return arr(proposal?.hunks).map((h,index)=>{const heading=/\\subsection\*?\{([^{}]+)\}/.exec(h.proposed_tex||"");return {id:h.id,title:h.title||"3."+(index+1)+" "+(heading?.[1]||"结构单元"),heading:heading?.[1]||(h.title||"").replace(/^3\.\d+\s*[:：.]?\s*/,""),status:h.status||"pending",hunk:h};});
  }
  function structuralSelectionPlan(proposal,selected){
    const units=structuralUnits(proposal),byId=new Map(units.map(unit=>[String(unit.id),unit])),missing=[];
    for(const unit of units.filter(unit=>selected.has(unit.id)&&pending(unit.hunk)))for(const dependency of arr(unit.hunk.depends_on)){const id=typeof dependency==="string"?dependency:dependency?.id||dependency?.hunk_id,target=byId.get(String(id));if(target&&target.status!=="accepted"&&!selected.has(target.id))missing.push({unit:unit.title,dependency:target.title});}
    return {units,total:units.length,pending:units.filter(unit=>pending(unit.hunk)).length,accepted:units.filter(unit=>unit.status==="accepted").length,selected:units.filter(unit=>pending(unit.hunk)&&selected.has(unit.id)).length,missing};
  }
  function structuralScopeSummary(proposal){
    const reason=String(proposal?.reason||"").replace(/\s+/g," ").trim(),end=reason.search(/[。！？]|[!?](?=\s|$)|\.(?=\s|$)/);
    return end<0?reason:reason.slice(0,end+1);
  }
  function localContextParts(proposal,selectedIds=null){
    const context=proposal.context||{},text=context.original||"",parts=[];let cursor=0;
    const edits=arr(proposal.edits||proposal.hunks).filter(pending).map(h=>{
      const relative={...h,start:(h.current_start??h.start)-context.start,end:(h.current_end??h.end)-context.start};delete relative.current_start;delete relative.current_end;
      return {h,range:exactSourceRange(text,relative)};
    }).filter(item=>item.range).sort((a,b)=>a.range.start-b.range.start);
    for(const {h,range} of edits){if(range.start<cursor)continue;if(range.start>cursor)parts.push({kind:"equal",text:text.slice(cursor,range.start)});
      if(selectedIds===null||selectedIds.has(h.id))parts.push(...diffParts(h.original_tex||"",h.proposed_tex||""));else parts.push({kind:"equal",text:text.slice(range.start,range.end)});
      cursor=range.end;
    }
    if(cursor<text.length)parts.push({kind:"equal",text:text.slice(cursor)});
    return parts;
  }
  const state={pid:"",doc:null,file:"main.tex",fileData:null,dirty:false,saving:null,saveTimer:null,serial:0,loading:false,visible:true,mode:"code",sourceVisible:false,proposal:null,proposalSerial:0,selected:new Set(),previewJob:null,pdfJob:null,pdf:null,pdfPage:1,zoom:1,pdfSerial:0,pdfLoadSerial:0,pdfIndexSerial:0,pdfPages:[],pdfAnchors:new Map(),pdfUnmatched:new Map(),pdfDomIndex:null,compileTimer:null,selection:null,chatSelection:null,conversationId:"",translation:null,translationSerial:0,translations:[],chatSerial:0,sourceJob:null,layoutSerial:0,layoutDocs:[],layoutPage:1,layoutSelected:[],layoutBaseHash:""};
  const sourceRoot = () => "/api/source/"+encodeURIComponent(state.pid);
  async function api(path,body){
    const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),60000);
    try{
      const response=await fetch(path,{method:body===undefined?"GET":"POST",headers:body===undefined?{}:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),signal:ctl.signal});
      let data;try{data=await response.json();}catch(_){throw Error("工作台返回了无法读取的数据。");}
      if(!response.ok)throw Object.assign(Error(data.error||data.message||"请求失败"),{status:response.status});
      return data;
    }catch(e){if(e.name==="AbortError")throw Error("请求超时，请检查连接后重试。");throw e;}finally{clearTimeout(timer);}
  }
  try{
    const response=await fetch("/static/overleaf.html?v=20261031-fast-translation");if(!response.ok)throw Error("编辑器界面载入失败。");
    const host=el("div");host.innerHTML=await response.text();$("view-editor").append(host.firstElementChild);
  }catch(e){$("view-editor").append(el("p","ol-notice error",e.message));return;}
  const notice=(message="",error=false)=>{$("ol-notice").textContent=message;$("ol-notice").classList.toggle("hidden",!message);$("ol-notice").classList.toggle("error",error);};
  const guarded=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>notice(e.message||String(e),true));
  const saveLabel=(text,error=false)=>{$("ol-save-status").textContent=text;$("ol-save-status").classList.toggle("error",error);};
  const mainFile=()=>arr(state.doc?.files).find(f=>f.editable||f.name==="main.tex")?.name||"main.tex";
  const isMain=()=>state.file===mainFile();
  const proposals=()=>reviewCollections(state.doc).local;
  function mirrorProjects(){const original=$("project-select"),mirror=$("ol-project-select");if(mirror.innerHTML!==original.innerHTML)mirror.innerHTML=original.innerHTML;mirror.value=original.value;}
  new MutationObserver(mirrorProjects).observe($("project-select"),{childList:true,subtree:true,attributes:true});
  $("ol-project-select").addEventListener("change",()=>{$("project-select").value=$("ol-project-select").value;$("project-select").dispatchEvent(new Event("change",{bubbles:true}));});
  document.querySelectorAll("[data-ol-view]").forEach(b=>b.addEventListener("click",()=>document.querySelector('[data-view="'+b.dataset.olView+'"]')?.click()));
  mirrorProjects();
  function reviewCount(doc=state.doc){const groups=reviewCollections(doc);$("ol-review-count").textContent=String(groups.local.length+groups.layout.length);}
  function setSourceVisible(visible){state.sourceVisible=visible;$("overleaf-workbench").classList.toggle("ol-pdf-first",!visible);$("ol-source-toggle").textContent=visible?"隐藏源码":"显示源码";$("ol-source-toggle").setAttribute("aria-expanded",String(visible));$("ol-save").classList.toggle("hidden",!visible);$("ol-file-details").open=visible;$("ol-file-title").textContent=visible?state.file:"论文正文";if(state.pdf)requestAnimationFrame(()=>renderPdfPage().catch(e=>notice(e.message,true)));}
  $("ol-source-toggle").addEventListener("click",()=>setSourceVisible(!state.sourceVisible));
  function documentStatus(){
    $("ol-file-title").textContent=state.sourceVisible?state.file:"论文正文";$("ol-file-status").textContent=isMain()?"当前稿 · 原文与已接受修改":"工程素材 · 只读";
    $("ol-source-version").textContent=state.doc?"版本 "+state.doc.revision+" · "+String(state.doc.sha256||"").slice(0,8):"—";
    $("ol-source").readOnly=!state.doc||!isMain();$("ol-save").disabled=!state.doc||!isMain();
    $("ol-provenance").textContent=state.doc?"原稿作为基线。Agent 建议经你选择后才会写入。模板、图片与参考文献沿用原工程。":"";
    if(!state.dirty&&!state.saving)saveLabel("已保存");
    if(state.pdfJob?.source_hash&&state.pdfJob.source_hash!==state.doc?.sha256)previewBanner("正文已有更新；当前 PDF 保留上次编译结果，更新 PDF 后重新定位问题。");
    updatePdfIssueAnchors();updateSelectionPill();historyButtons();
  }
  async function loadState(force=false){
    if(!state.pid)return;const serial=++state.serial,pid=state.pid;state.loading=true;
    try{
      const data=await api(sourceRoot());if(pid!==state.pid||serial!==state.serial)return;
      if(!force&&(state.dirty||state.saving)){reviewCount(data);return;}
      const changed=data.sha256!==state.doc?.sha256;state.doc=data;
      if(changed){editHistory.undo=[];editHistory.redo=[];editHistory.previous=null;editHistory.pid=state.pid;editHistory.versionCursor=null;}
      if(force||changed||!$("ol-source").value){if(isMain())$("ol-source").value=data.tex||"";state.dirty=false;}
      renderFiles();renderOutline();renderProposals();renderGutter();documentStatus();
      if(state.mode==="visual")renderVisual(isMain()?$("ol-source").value:state.fileData?.text||"");
      notice();
    }catch(e){if(pid!==state.pid)return;if(e.status===404){state.doc=null;$("ol-source").value="";saveLabel("尚未绑定原稿");notice("当前项目尚未建立原文审阅工程，请选择原版 LaTeX 项目。");}else notice(e.message,true);}
    finally{if(serial===state.serial)state.loading=false;}
  }
  function setMode(mode){
    state.mode=mode;$("ol-code-area").classList.toggle("hidden",mode!=="code");$("ol-visual").classList.toggle("hidden",mode!=="visual");
    $("ol-code-tab").classList.toggle("active",mode==="code");$("ol-visual-tab").classList.toggle("active",mode==="visual");
    if(mode==="visual")renderVisual(isMain()?$("ol-source").value:state.fileData?.text||"");
  }
  function renderFiles(){
    const box=$("ol-files");box.replaceChildren();
    for(const f of arr(state.doc?.files)){const b=el("button","ol-file"+(state.file===f.name?" active":""));b.title=f.name;b.append(el("span","",/\.(pdf|png|jpe?g|eps)$/i.test(f.name)?"▧":"▤"),el("span","",f.name));b.addEventListener("click",guarded(()=>{setSourceVisible(true);return openFile(f);}));box.append(b);}
  }
  function localUrl(value){try{const u=new URL(value,location.href);return u.origin===location.origin&&u.pathname.startsWith("/api/")?u.href:null;}catch(_){return null;}}
  async function openFile(file){
    await flushDrafts();state.file=file.name;$("ol-selection-bubble").classList.add("hidden");
    if(isMain()){state.fileData=null;$("ol-source").value=state.doc.tex;setMode("code");}
    else{
      const data=await api(sourceRoot()+"/file?name="+encodeURIComponent(file.name));state.fileData=data;
      if(data.text!==undefined){$("ol-source").value=data.text;setMode("code");}
      else{
        $("ol-source").value="工程素材："+file.name;setMode("visual");$("ol-visual").replaceChildren(el("h2","",file.name));
        const url=localUrl(data.url||file.url);
        if(url&&/\.(png|jpe?g)$/i.test(file.name)){const img=el("img","ol-asset-image");img.src=url;img.alt=file.name;$("ol-visual").append(img);}
        else if(url&&/\.pdf$/i.test(file.name)){const frame=el("iframe","ol-asset-pdf");frame.src=url;frame.title=file.name;$("ol-visual").append(frame);}
        else $("ol-visual").append(el("p","","该素材保留在原工程中，编译时使用原文件。"));
      }
    }
    renderFiles();renderGutter();documentStatus();
  }
  function renderOutline(){
    const opened=new Set([...$("ol-outline").querySelectorAll('details[open]')].map(n=>n.dataset.section));
    const box=$("ol-outline"),scope=$("ol-chat-scope"),selected=scope.value;box.replaceChildren();scope.replaceChildren(new Option("全文","document"));
    for(const item of arr(state.doc?.outline)){scope.append(new Option(item.title,item.id));const b=el("button","ol-outline-item level-"+Math.min(3,Number(item.level)||1),item.title);b.title="定位 "+item.title;b.addEventListener("click",guarded(async()=>{if(state.sourceVisible){if(!isMain())await openFile({name:mainFile()});setMode("code");gotoLine(item.line||1);}else await navigateOutlinePdf(item);$("ol-current-section").textContent=item.title;scope.value=item.id;$("ol-chat-role").value=/introduction/i.test(item.title)?"introduction":/related|prior work/i.test(item.title)?"related_work":/experiment|result|dataset|metric|ablation/i.test(item.title)?"experiments":"method";updateSelectionPill();}));box.append(b);}
    if([...scope.options].some(o=>o.value===selected))scope.value=selected;
    // Attach each precise issue to its narrowest manuscript section.
    const sections=arr(state.doc?.outline),groups=new Map();
    for(const proposal of proposals()){
      const edit=arr(proposal.edits||proposal.hunks).find(pending);
      const offset=edit?.current_start??edit?.start;
      const end=edit?.current_end??edit?.end??offset;
      const section=sections.filter(s=>s.start<=offset&&s.end>=end&&s.end>offset).sort((a,b)=>(a.end-a.start)-(b.end-b.start))[0];
      if(section){const list=groups.get(section.id)||[];list.push(proposal);groups.set(section.id,list);}
    }
    [...box.children].forEach((button,index)=>{
      const section=sections[index],issues=groups.get(section.id)||[];
      const details=el("details","ol-outline-issues"),summary=el("summary","",issues.length?issues.length+" 处细改":"检查本节");
      details.dataset.section=section.id;details.open=opened.has(section.id);
      details.append(summary);
      for(const issue of issues){const link=el("button","ol-outline-issue",issue.title);link.addEventListener("click",guarded(()=>openProposal(issue.id)));details.append(link);}
      const review=el("button","ol-outline-review","对照原文逐句检查");
      review.addEventListener("click",()=>{scope.value=section.id;$("ol-chat-action").value="review";$("ol-chat-role").value=/introduction/i.test(section.title)?"introduction":"method";$("ol-chat-input").value="检查「"+section.title+"」的具体原句。每个问题只修改必要词句或一个完整公式，并说明依据；不要整段替换。";$("ol-agent-toggle").click();document.querySelector('[data-agent-tab="discuss"]').click();});
      details.append(review);button.after(details);
    });
    $("ol-candidate-outline").replaceChildren();$("ol-candidate-outline").classList.add("hidden");$("ol-structural-banner").classList.add("hidden");
  }
  function renderStructuralOutline(){
    const box=$("ol-candidate-outline"),candidates=reviewCollections(state.doc).structural;box.replaceChildren();box.classList.toggle("hidden",!candidates.length);$("ol-structural-banner").classList.toggle("hidden",!candidates.length);if(candidates.length){const scope=structuralScopeSummary(candidates[0]);$("ol-structural-banner-note").textContent=scope?scope+" · 候选尚未接受":structuralUnits(candidates[0]).length+" 个结构单元可分别审阅，候选尚未写入正文。";}
    for(const proposal of candidates){const group=el("section","ol-candidate-outline-group");group.append(el("strong","","Method 五节重构候选"),el("small","","尚未接受 · 当前正文保持原稿"));const preview=el("button","ol-candidate-pdf-link","预览完整候选 PDF");preview.addEventListener("click",guarded(async()=>{await openProposal(proposal.id);if(state.proposal?.id===proposal.id)await previewLayout();}));group.append(preview);for(const unit of structuralUnits(proposal)){const button=el("button","ol-candidate-unit-link",unit.title+(unit.status==="accepted"?" · 已接受":""));button.addEventListener("click",guarded(()=>openProposal(proposal.id,unit.id)));group.append(button);}box.append(group);}
  }
  $("ol-structural-open-review").addEventListener("click",guarded(()=>{const proposal=reviewCollections(state.doc).structural[0];if(proposal)return openProposal(proposal.id);}));
  $("ol-structural-open-pdf").addEventListener("click",guarded(async()=>{const proposal=reviewCollections(state.doc).structural[0];if(!proposal)return;await openProposal(proposal.id);if(state.proposal?.id===proposal.id)await previewLayout();}));
  function gotoLine(line){const input=$("ol-source"),parts=input.value.split("\n"),start=parts.slice(0,Math.max(0,line-1)).reduce((n,x)=>n+x.length+1,0);input.focus();input.setSelectionRange(start,start);input.scrollTop=Math.max(0,(line-1)*20-100);syncScroll();cursorStatus();}
  function markerLines(){
    return isMain()?lineIssueIndex($("ol-source").value,proposals()):new Map();
  }
  function renderGutter(){
    const box=$("ol-gutter"),frag=document.createDocumentFragment(),markers=markerLines(),count=Math.max(1,$("ol-source").value.split("\n").length);
    for(let i=1;i<=count;i++){const line=el("div","ol-line",i),issues=markers.get(i);if(issues?.length){const b=el("button","ol-line-warning",issues.length>1?issues.length:"!");b.title=issues.length>1?"本行 "+issues.length+" 个独立问题":issues[0].title;b.setAttribute("aria-label","第 "+i+" 行："+issues.length+" 个局部问题");b.addEventListener("click",guarded(()=>issues.length===1?openProposal(issues[0].proposal.id,issues[0].hunkId):showLineIssues(i,issues,b)));line.append(b);}frag.append(line);}box.replaceChildren(frag);syncScroll();
  }
  function showLineIssues(line,issues,anchor){
    const box=$("ol-line-issues-list");box.replaceChildren();$("ol-line-issues-title").textContent="第 "+line+" 行 · "+issues.length+" 个问题";
    for(const issue of issues){const button=el("button","",issue.title);button.addEventListener("click",guarded(()=>{$("ol-line-issues").classList.add("hidden");return openProposal(issue.proposal.id,issue.hunkId);}));box.append(button);}
    const rect=anchor.getBoundingClientRect(),popover=$("ol-line-issues");popover.style.left=Math.min(innerWidth-340,rect.right+8)+"px";popover.style.top=Math.min(innerHeight-220,Math.max(90,rect.top-8))+"px";popover.classList.remove("hidden");
  }
  $("ol-line-issues-close").addEventListener("click",()=>$("ol-line-issues").classList.add("hidden"));
  document.addEventListener("pointerdown",event=>{if(!$("ol-line-issues").contains(event.target)&&!event.target.closest?.(".ol-line-warning"))$("ol-line-issues").classList.add("hidden");});
  function selectSourceTarget(proposal,hunkId=null,focus=false){
    const items=arr(proposal.edits||proposal.hunks).filter(pending),hunk=items.find(item=>String(item.id)===String(hunkId))||items[0];
    const range=hunk?exactSourceRange($("ol-source").value,hunk):null;if(!range||!isMain())return;
    const input=$("ol-source");if(focus)input.focus();input.setSelectionRange(range.start,range.end);input.scrollTop=Math.max(0,(input.value.slice(0,range.start).split("\n").length-1)*20-100);syncScroll();cursorStatus();
  }
  function syncScroll(){$("ol-gutter").scrollTop=$("ol-source").scrollTop;}
  function cursorStatus(){const parts=$("ol-source").value.slice(0,$("ol-source").selectionStart).split("\n");$("ol-cursor").textContent="Ln "+parts.length+", Col "+(parts.at(-1).length+1);}
  async function saveSource(){
    clearTimeout(state.saveTimer);if(state.saving){await state.saving;if(!state.dirty)return;}if(!state.doc||!isMain()||!state.dirty)return;
    const pid=state.pid,tex=$("ol-source").value,hash=state.doc.sha256;saveLabel("保存中…");
    const task=(async()=>{try{const data=await api(sourceRoot()+"/save",{tex,base_hash:hash,reason:"Author edits original LaTeX in workspace"});if(pid!==state.pid)return;state.doc=data;state.dirty=$("ol-source").value!==tex;renderOutline();renderProposals();renderGutter();documentStatus();saveLabel(state.dirty?"有未保存修改":"已保存");}catch(e){state.dirty=true;saveLabel(e.status===409?"版本冲突 · 本地文字保留":"保存失败",true);throw e;}})();
    state.saving=task;try{await task;}finally{state.saving=null;}
  }
  async function flushDrafts(){clearTimeout(state.saveTimer);if(state.saving)await state.saving;if(state.dirty)await saveSource();if(state.dirty)throw Error("仍有未保存的源码，请先保存或处理版本冲突。");}
  $("ol-source").addEventListener("input",()=>{if(!isMain())return;state.dirty=$("ol-source").value!==state.doc?.tex;saveLabel(state.dirty?"有未保存修改":"已保存");renderGutter();clearTimeout(state.saveTimer);if(state.dirty)state.saveTimer=setTimeout(()=>saveSource().catch(e=>notice(e.message,true)),1500);});
  // Explicit history survives autosave. Native textarea history is lost when
  // polling/acceptance replaces .value, so keep snapshots per project here.
  const editHistory={pid:"",undo:[],redo:[],previous:null,restoring:false};
  const snapshot=()=>({text:$("ol-source").value,start:$("ol-source").selectionStart,end:$("ol-source").selectionEnd,cursor:editHistory.versionCursor??state.doc?.revision});
  function historyButtons(){for(const kind of ["undo","redo"]){const b=$("ol-"+kind);if(b)b.disabled=!isMain()||!state.doc||(!editHistory[kind].length&&!(kind==="undo"&&arr(state.doc?.versions).some(v=>v.revision<state.doc.revision)));}}
  $("ol-source").addEventListener("beforeinput",()=>{
    if(editHistory.pid!==state.pid){editHistory.pid=state.pid;editHistory.undo=[];editHistory.redo=[];}
    editHistory.previous=snapshot();
    editHistory.versionCursor=null;
  });
  $("ol-source").addEventListener("input",()=>{
    if(!editHistory.restoring&&editHistory.previous){editHistory.undo.push(editHistory.previous);if(editHistory.undo.length>100)editHistory.undo.shift();editHistory.redo=[];editHistory.previous=null;}
    historyButtons();
  });
  async function restoreEdit(kind){
    if(!isMain()||!state.doc||state.historyBusy)return;
    if(!editHistory[kind].length){
      if(kind!=="undo")return;await flushDrafts();const version=arr(state.doc.versions).find(v=>v.revision<(editHistory.versionCursor??state.doc.revision)&&v.sha256!==state.doc.sha256);if(!version)return;
      const pid=state.pid,previous=snapshot();state.historyBusy=true;
      try{const data=await api(sourceRoot()+"/restore",{revision:version.revision,base_hash:state.doc.sha256});if(pid!==state.pid)return;state.doc=data;$("ol-source").value=data.tex;state.dirty=false;editHistory.pid=pid;editHistory.versionCursor=version.revision;editHistory.redo.push(previous);renderOutline();renderGutter();renderProposals();documentStatus();compile().catch(e=>notice(e.message,true));}finally{state.historyBusy=false;}return;
    }
    const next=editHistory[kind].pop(),other=kind==="undo"?"redo":"undo";
    editHistory[other].push(snapshot());editHistory.restoring=true;
    editHistory.versionCursor=next.cursor;
    const input=$("ol-source");input.value=next.text;input.focus();input.setSelectionRange(next.start,next.end);input.dispatchEvent(new Event("input"));editHistory.restoring=false;historyButtons();
  }
  for(const kind of ["undo","redo"]){const button=el("button","ol-button",kind==="undo"?"撤销":"重做");button.id="ol-"+kind;button.title=kind==="undo"?"Ctrl / ⌘ Z":"Ctrl / ⌘ Shift Z · Ctrl Y";button.disabled=true;button.addEventListener("click",guarded(()=>restoreEdit(kind)));$("ol-save").before(button);}
  document.addEventListener("keydown",event=>{if(!(event.ctrlKey||event.metaKey)||event.altKey||!state.visible||document.querySelector('dialog[open]'))return;if(event.target!==$("ol-source")&&event.target.closest?.('textarea,input,[contenteditable="true"]'))return;const key=event.key.toLowerCase();if(key==="z"||key==="y"){event.preventDefault();guarded(()=>restoreEdit(key==="y"||event.shiftKey?"redo":"undo"))();}});
  window.addEventListener("paper-project-changed",()=>{if(editHistory.pid!==state.pid){editHistory.pid=state.pid;editHistory.undo=[];editHistory.redo=[];editHistory.previous=null;historyButtons();}});
  $("ol-source").addEventListener("scroll",syncScroll);$("ol-source").addEventListener("click",cursorStatus);$("ol-source").addEventListener("keyup",cursorStatus);
  $("ol-source").addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="s"){e.preventDefault();guarded(saveSource)();}if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){e.preventDefault();guarded(compile)();}});
  $("ol-save").addEventListener("click",guarded(saveSource));$("ol-code-tab").addEventListener("click",()=>setMode("code"));$("ol-visual-tab").addEventListener("click",()=>setMode("visual"));
  $("ol-download").addEventListener("click",guarded(async()=>{await flushDrafts();const data=await api(sourceRoot()+"/export"),a=el("a");a.href=URL.createObjectURL(new Blob([data.tex],{type:"application/x-tex;charset=utf-8"}));a.download="manuscript-v"+data.revision+".tex";a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}));

  function drawer(tab="review"){$("ol-agent-drawer").classList.remove("hidden");document.querySelectorAll("[data-agent-tab]").forEach(b=>b.classList.toggle("active",b.dataset.agentTab===tab));for(const name of ["review","discuss","context","translate"])$("ol-agent-"+name).classList.toggle("hidden",name!==tab);if(tab==="context")window.paperDiscussionContext?.load(state.pid);}
  $("ol-review-toggle").addEventListener("click",()=>drawer("review"));$("ol-agent-toggle").addEventListener("click",()=>drawer("discuss"));$("ol-agent-close").addEventListener("click",()=>$("ol-agent-drawer").classList.add("hidden"));document.querySelectorAll("[data-agent-tab]").forEach(b=>b.addEventListener("click",()=>drawer(b.dataset.agentTab)));
  function renderProposals(){
    reviewCount();const box=$("ol-proposals");box.replaceChildren();
    const collection=reviewCollections(state.doc);
    for(const p of collection.structural){const group=el("section","ol-structural-proposal-card");group.append(el("span","ol-proposal-tag","完整 Method 候选 · 尚未接受"),el("h3","",p.title||"Method 五节重构候选"),el("p","",p.reason||"按既定五节组织方法、公式与理论衔接。"),el("small","",structuralUnits(p).length+" 个结构单元 · 默认不勾选"));const review=el("button","ol-primary","逐节对照原文与新版"),preview=el("button","ol-secondary","查看完整候选 PDF");review.addEventListener("click",guarded(()=>openProposal(p.id)));preview.addEventListener("click",guarded(async()=>{await openProposal(p.id);if(state.proposal?.id===p.id)await previewLayout();}));group.append(review,preview);box.append(group);}
    for(const p of collection.local){const card=el("div","ol-pdf-proposal"),b=el("button","ol-proposal-card"),anchor=state.pdfAnchors.get(p.id);b.append(el("span","ol-proposal-tag","局部问题 · 待审"),el("strong","",p.title||"局部文字问题"),el("p","",p.reason||"查看这一处的具体修改及理由。"),el("small","",arr(p.edits||p.hunks).filter(pending).length+" 处细改 · 原文尚未改动"));b.addEventListener("click",guarded(()=>openProposal(p.id)));card.append(b);const locate=el("button","ol-issue-pdf-link",anchor?"→ PDF 第 "+anchor.page+" 页 · 已匹配文字":"→ 查看相关章节");locate.addEventListener("click",guarded(()=>locateProposalPdf(p)));card.append(locate);if(!anchor)card.append(el("p","ol-pdf-unmatched",state.pdfUnmatched.get(p.id)||"正在建立 PDF 文字索引。"));box.append(card);}
    if(!collection.local.length)box.append(el("div","ol-agent-empty","当前没有待审的局部问题。可请专家检查具体词句。"));
    if(collection.layout.length){const group=el("div","ol-layout-proposals");group.append(el("h3","","图表与排版建议"));for(const p of collection.layout){const button=el("button","ol-proposal-card");button.append(el("span","ol-proposal-tag","排版候选 · 尚未接受"),el("strong","",p.title||"排版调整"),el("p","",p.reason||"对比真实编译 PDF 后决定是否接受。"));button.addEventListener("click",guarded(()=>openProposal(p.id)));group.append(button);}box.append(group);}
    if(collection.legacy.length){const history=el("details","ol-legacy-proposals"),summary=el("summary","","历史整节方案（"+collection.legacy.length+"）");history.append(summary,el("p","ol-panel-note","旧版本的整节思路仅供参考，不进入黄色局部问题标记。"));for(const p of collection.legacy){const b=el("button","ol-history-proposal",p.title||"历史整节方案");b.append(el("small","",p.status==="superseded"?"已由局部问题替代":p.status||"历史方案"));b.addEventListener("click",guarded(()=>openProposal(p.id)));history.append(b);}box.append(history);}
  }
  async function openProposal(id,hunkId=null){
    await flushDrafts();$("ol-line-issues").classList.add("hidden");if(!isMain())await openFile({name:mainFile()});setMode("code");const serial=++state.proposalSerial,pid=state.pid;state.proposal=null;state.selected.clear();selectionSummary();$("ol-issue-nav").classList.add("hidden");$("ol-review-title").textContent="正在读取当前问题…";$("ol-review-reason").textContent="";$("ol-review-hunks").replaceChildren();$("ol-local-context").classList.add("hidden");$("ol-review-error").textContent="";if(!$("ol-review-dialog").open)$("ol-review-dialog").showModal();
    try{const p=await api(sourceRoot()+"/proposal/"+encodeURIComponent(id));if(pid!==state.pid||serial!==state.proposalSerial||!$("ol-review-dialog").open)return;state.proposal=p;state.selected=new Set();$("ol-use-edited").checked=false;$("ol-edited-proposal").value=p.proposed_tex||"";$("ol-review-title").textContent=p.title||"局部文字问题";$("ol-review-reason").textContent=(["local","layout","structural"].includes(p.review_mode)?"":"历史整节方案，仅供参考。\n")+(p.reason||"");$("ol-composed-preview").classList.add("hidden");$("ol-edit-proposal").classList.add("hidden");$("ol-review-dialog").classList.toggle("ol-local-review",p.review_mode==="local");$("ol-review-dialog").classList.toggle("ol-structural-review",p.review_mode==="structural");const rows=renderHunks();renderLocalContext(p);renderIssueNavigation();$("ol-review-dialog").scrollTop=0;if(state.sourceVisible)selectSourceTarget(p,hunkId);const target=hunkId===null?null:rows.get(String(hunkId));if(target){target.classList.add("ol-targeted-hunk");if(p.review_mode==="structural")requestAnimationFrame(()=>{if(serial===state.proposalSerial)target.scrollIntoView({block:"start",behavior:"instant"});});}selectionSummary();}
    catch(e){if(serial===state.proposalSerial)$("ol-review-error").textContent=e.message;}
  }
  function closeProposal(){++state.proposalSerial;state.proposal=null;state.selected.clear();$("ol-review-dialog").close();$("ol-selection-bubble").classList.add("hidden");$("overleaf-workbench").append($("ol-selection-bubble"),$("ol-translation-popover"));}
  function renderLocalContext(proposal){
    const context=issuePresentation(proposal).context,box=$("ol-local-context");box.classList.toggle("hidden",!context);if(!context)return;
    for(const [id,text] of [["ol-context-before",context.before],["ol-context-after",context.after]]){$(id).replaceChildren();if(text)renderInline(text,$(id));$(id).classList.toggle("hidden",!text);}
    const target=$("ol-context-target");target.replaceChildren();renderInlineParts(localContextParts(proposal),target);
  }
  function renderIssueNavigation(){
    const local=state.proposal?.review_mode==="local",issues=proposals(),index=issues.findIndex(p=>p.id===state.proposal?.id);$("ol-issue-nav").classList.toggle("hidden",!local);
    $("ol-issue-position").textContent=index<0?"当前问题":(index+1)+" / "+issues.length+" 个待审问题";
    $("ol-issue-prev").disabled=index<=0;$("ol-issue-next").disabled=index<0||index>=issues.length-1;$("ol-locate-source").textContent=state.sourceVisible?"定位源码":"在 PDF 中定位";
  }
  async function navigateIssue(delta){const issues=proposals(),index=issues.findIndex(p=>p.id===state.proposal?.id),target=issues[index+delta];if(index>=0&&target)await openProposal(target.id);}
  $("ol-issue-prev").addEventListener("click",guarded(()=>navigateIssue(-1)));$("ol-issue-next").addEventListener("click",guarded(()=>navigateIssue(1)));
  $("ol-locate-source").addEventListener("click",guarded(async()=>{const proposal=state.proposal;if(!proposal)return;closeProposal();if(state.sourceVisible){setMode("code");selectSourceTarget(proposal,null,true);}else await locateProposalPdf(proposal);}));
  function renderHunks(){
    const box=$("ol-review-hunks"),rows=new Map(),view=issuePresentation(state.proposal);box.replaceChildren();
    for(const [index,h] of view.hunks.entries()){const snippet=(h.original_tex||"").replace(/\s+/g," ").trim(),title=view.local?"细改"+(index+1)+"："+(snippet?(snippet.length>56?snippet.slice(0,56)+"…":snippet):"插入文字"):(h.title||"结构单元 "+(index+1));const row=el("section","ol-hunk"+(!pending(h)?" resolved":"")),label=el("label","ol-hunk-heading"),check=el("input");row.dataset.hunkId=String(h.id);rows.set(String(h.id),row);check.type="checkbox";check.checked=state.selected.has(h.id);check.disabled=!view.editable||!pending(h)||h.selectable===false;check.setAttribute("aria-label","选择"+title);check.addEventListener("change",()=>{if(check.checked)state.selected.add(h.id);else state.selected.delete(h.id);selectionSummary();});label.append(check,el("strong","",title));row.append(label);if(h.reason&&h.reason.replace(/\s+/g," ").trim()!==(state.proposal.reason||"").replace(/\s+/g," ").trim())row.append(el("p","ol-hunk-reason",h.reason));if(view.structural&&arr(h.depends_on).length){const units=structuralUnits(state.proposal),names=arr(h.depends_on).map(dependency=>units.find(unit=>String(unit.id)===String(typeof dependency==="string"?dependency:dependency?.id||dependency?.hunk_id))?.title).filter(Boolean);if(names.length)row.append(el("p","ol-structural-dependencies","使用前节定义："+names.join("；")));}const diff=el("div","ol-hunk-diff");if(view.local)renderInlineParts(diffParts(h.original_tex||"",h.proposed_tex||""),diff);else if(view.layout){diff.classList.add("ol-layout-code");diff.textContent="原配置：\n"+(h.original_tex||"")+" \n\n候选配置：\n"+(h.proposed_tex||"");}else renderDiff(h.original_tex||"",h.proposed_tex||"",diff,view.structural);if(view.layout){const details=el("details","ol-layout-technical");details.append(el("summary","","技术修改详情"),diff);row.append(details);}else row.append(diff);if(!pending(h))row.append(el("small","ol-hunk-state",h.status==="accepted"?"已接受":"已处理"));if(pending(h)&&h.selectable===false)row.append(el("small","ol-hunk-state","当前版本暂不能直接采纳这项，请重新核对候选。"));box.append(row);}
    const outline=$("ol-structural-review-outline");outline.replaceChildren();outline.classList.toggle("hidden",!view.structural);if(view.structural)for(const unit of structuralUnits(state.proposal)){const button=el("button","",unit.title);button.addEventListener("click",()=>rows.get(String(unit.id))?.scrollIntoView({block:"start",behavior:"smooth"}));outline.append(button);}
    selectionSummary();return rows;
  }
  function selectionSummary(){const view=issuePresentation(state.proposal),n=state.selected.size,reviewable=view.local||view.layout||view.structural;$("ol-review-selected").textContent=!reviewable?"历史整节方案 · 只读参考":n?"已选 "+n+" 项，未选内容保持原样":"选择希望采用的具体修改";$("ol-accept-selected").textContent=view.structural?"接受已选"+(n||"")+"节":view.layout?"接受已选排版":"接受已选细改";for(const id of ["ol-accept-selected","ol-reject-selected","ol-select-all","ol-select-none"])$(id).classList.toggle("hidden",!reviewable);$("ol-select-all").textContent=view.structural?"选择全部待审小节":"选择全部待审项";$("ol-preview-selected").classList.toggle("hidden",!view.local);$("ol-layout-preview").classList.toggle("hidden",!view.layout&&!view.structural);$("ol-layout-preview").textContent=view.structural?(n?"预览所选 "+n+" 节的合并 PDF":"查看完整 Method 候选 PDF"):"对比排版前后 PDF";$("ol-layout-preview").disabled=!view.editable;$("ol-accept-selected").disabled=!view.editable||!n;$("ol-reject-selected").disabled=!view.editable||!n;$("ol-preview-selected").disabled=!n;$("ol-preview-selected").textContent="预览已选细改";renderStructuralPlan();}
  function renderStructuralPlan(){
    const box=$("ol-structural-plan"),isStructural=state.proposal?.review_mode==="structural";box.classList.toggle("hidden",!isStructural);if(!isStructural)return;
    const plan=structuralSelectionPlan(state.proposal,state.selected);let text=plan.selected?"本次选择 "+plan.selected+" / "+plan.pending+" 个待审小节。":"完整候选含 "+plan.total+" 个结构单元，默认不勾选。可先阅读候选 PDF，再逐节或全部采纳。";
    if(plan.accepted)text+=" 已接受 "+plan.accepted+" 节。";
    if(plan.selected&&plan.selected<plan.pending)text+=" 未选小节保留当前稿，需核对跨节符号、状态定义与推断目标是否统一。";
    if(plan.missing.length)text+=" 尚未同时采纳的定义依赖："+plan.missing.map(item=>item.unit+" 使用 "+item.dependency).join("；")+"。可按写作进度逐节采纳，之后继续闭合这些联系。";
    else if(plan.selected===plan.pending&&plan.selected)text+=" 所选小节将作为一组更新 Method，正文以实际采纳结果为准。";
    box.textContent=text;box.classList.toggle("has-dependency-gap",plan.missing.length>0);
  }
  $("ol-review-close").addEventListener("click",closeProposal);$("ol-review-dialog").addEventListener("cancel",e=>{e.preventDefault();closeProposal();});$("ol-select-all").addEventListener("click",()=>{state.selected=new Set(issuePresentation(state.proposal).hunks.filter(h=>pending(h)&&h.selectable!==false).map(h=>h.id));renderHunks();});$("ol-select-none").addEventListener("click",()=>{state.selected.clear();renderHunks();});$("ol-use-edited").addEventListener("change",selectionSummary);
  async function resolveProposal(accept){
    if(!state.proposal||!issuePresentation(state.proposal).editable)return;const serial=state.proposalSerial,pid=state.pid,id=state.proposal.id,body={base_hash:state.doc.sha256,selected_hunk_ids:[...state.selected]};
    if(!body.selected_hunk_ids.length)return;
    $("ol-accept-selected").disabled=$("ol-reject-selected").disabled=true;
    try{const data=await api(sourceRoot()+"/proposal/"+encodeURIComponent(id)+(accept?"/accept":"/reject"),body);if(pid!==state.pid)return;state.doc=data;state.dirty=false;if(isMain())$("ol-source").value=data.tex||"";renderFiles();renderOutline();renderGutter();renderProposals();documentStatus();if(state.mode==="visual")renderVisual($("ol-source").value);if(serial===state.proposalSerial){const groups=reviewCollections(state.doc),remaining=[...groups.local,...groups.layout,...groups.structural].some(p=>p.id===id);if(remaining)await openProposal(id);else closeProposal();notice(accept?"所选修改已接受，正在更新 PDF。"+(data.linkage_job_id?"已启动一次全文关联检查，结果仍需你审阅。":""):"所选修改已暂不采用，正文未改动。");}if(accept){compile().catch(e=>notice(e.message,true));if(data.linkage_job_id)watchLinkage(data.linkage_job_id);else if(data.linkage_notice||data.linkage_error)notice(data.linkage_notice||data.linkage_error);}}
    catch(e){if(serial===state.proposalSerial)$("ol-review-error").textContent=e.message;}finally{if(serial===state.proposalSerial)selectionSummary();}
  }
  $("ol-accept-selected").addEventListener("click",()=>resolveProposal(true));$("ol-reject-selected").addEventListener("click",()=>resolveProposal(false));
  $("ol-preview-selected").addEventListener("click",()=>{if(!state.proposal||state.proposal.review_mode!=="local")return;const context=state.proposal.context||{};$("ol-composed-source").value=[context.before,localContextParts(state.proposal,state.selected).filter(p=>p.kind!=="delete").map(p=>p.text).join(""),context.after].filter(Boolean).join("\n");$("ol-composed-preview").classList.remove("hidden");$("ol-composed-preview").open=true;});
  async function watchLinkage(id){
    const pid=state.pid,box=$("ol-chat-messages"),line=el("div","ol-chat-message agent","全文关联检查正在进行。建议不会自动写入正文。");box.append(line);
    try{let job=await api("/api/source-agent/jobs/"+encodeURIComponent(id));while(active(job)){await new Promise(resolve=>setTimeout(resolve,1600));if(pid!==state.pid)return;job=await api("/api/source-agent/jobs/"+encodeURIComponent(id));}if(pid!==state.pid)return;const result=job.result||{};line.textContent="接受后的关联检查\n"+[result.summary,result.explanation,result.notice,job.error].filter(Boolean).join("\n\n");const ids=arr(result.proposal_ids);if(ids.length){await loadState();for(const [index,proposalId] of ids.entries()){const button=el("button","ol-secondary",arr(result.issues)[index]?.title||"查看关联问题");button.addEventListener("click",guarded(()=>openProposal(proposalId)));line.append(button);}notice("全文关联检查提出 "+ids.length+" 个待审问题，请逐项查看。");}else notice(result.notice||"关联检查已结束；没有新增待审修改。");}
    catch(e){if(pid===state.pid)line.textContent="关联检查记录暂未读取："+e.message;}
  }

  function tokens(text){return text.match(/\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}[\s\S]*?\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?:\\.|[^$])*\$|\s+|[A-Za-z0-9]+|[^\sA-Za-z0-9]/g)||[];}
  function diffParts(before,after){
    const a=tokens(before),b=tokens(after),out=[],push=(kind,text)=>{if(!text)return;if(out.at(-1)?.kind===kind)out.at(-1).text+=text;else out.push({kind,text});};
    if(a.length*b.length>900000){let first=0,last=0;while(first<Math.min(a.length,b.length)&&a[first]===b[first])first++;while(last<Math.min(a.length,b.length)-first&&a[a.length-1-last]===b[b.length-1-last])last++;push("equal",a.slice(0,first).join(""));push("delete",a.slice(first,a.length-last).join(""));push("insert",b.slice(first,b.length-last).join(""));push("equal",a.slice(a.length-last).join(""));return out;}
    const rows=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)rows[i][j]=a[i]===b[j]?rows[i+1][j+1]+1:Math.max(rows[i+1][j],rows[i][j+1]);let i=0,j=0;
    while(i<a.length||j<b.length){if(i<a.length&&j<b.length&&a[i]===b[j]){push("equal",a[i++]);j++;}else if(j<b.length&&(i===a.length||rows[i][j+1]>rows[i+1][j]))push("insert",b[j++]);else push("delete",a[i++]);}return out;
  }
  function reviewSentences(text){
    const sentences=[];let current="";
    for(const token of tokens(text)){
      if(current&&/[.!?。！？]\s*$/.test(current)&&/\S/.test(token)){sentences.push(current);current="";}
      current+=token;
      if(/\n[ \t]*\n/.test(token)){sentences.push(current);current="";}
    }
    if(current)sentences.push(current);
    return sentences;
  }
  function reviewDisplay(before,after,parts=diffParts(before,after)){
    const changed=parts.filter(p=>p.kind!=="equal"),changedChars=changed.reduce((n,p)=>n+p.text.length,0);
    const ratio=changedChars/Math.max(1,before.length+after.length),sentencesBefore=reviewSentences(before),sentencesAfter=reviewSentences(after);
    const significant=changedChars>120,fragmented=parts.length>20&&ratio>.35,rewritten=ratio>.62&&significant;
    const sentenceStructure=Math.abs(sentencesBefore.length-sentencesAfter.length)>=2&&ratio>.4&&significant;
    return {mode:fragmented||rewritten||sentenceStructure?"separated":"inline",parts,before:sentencesBefore,after:sentencesAfter};
  }
  function separatedSentenceParts(sentences,other){
    const retained=new Set(other.map(s=>s.trim()).filter(s=>s.length>1));
    return sentences.map(text=>({text,retained:retained.has(text.trim())}));
  }
  function renderMath(raw,target,display=false){
    let value=raw.replace(/^\\begin\{equation\*?\}|\\end\{equation\*?\}$/g,"").replace(/\\label\{[^{}]+\}/g,"").replace(/\\begin\{align\*?\}/g,"\\begin{aligned}").replace(/\\end\{align\*?\}/g,"\\end{aligned}").replace(/^\$\$|\$\$$|^\\\[|\\\]$|^\\\(|\\\)$|^\$|\$$/g,"");
    if(window.katex){try{window.katex.render(value,target,{displayMode:display,throwOnError:false,trust:false,strict:"ignore",output:"htmlAndMathml"});}catch(_){target.textContent=raw;}}else target.textContent=raw;
  }
  function renderInline(text,target){
    for(const part of text.split(/(\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}[\s\S]*?\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?:\\.|[^$])*\$)/g)){if(!part)continue;if(/^\\begin\{|^\\\(|^\\\[|^\$/.test(part)){const m=el("span","ol-inline-math");renderMath(part,m,/^\\begin\{|^\\\[|^\$\$/.test(part));target.append(m);}else target.append(document.createTextNode(part.replace(/\\IEEEPARstart\{([^{}]*)\}\{([^{}]*)\}/g,"$1$2").replace(/\\(?:cite|citep|citet)\{([^{}]+)\}/g,"[$1]").replace(/\\(?:textbf|textit|emph)\{([^{}]*)\}/g,"$1").replace(/\\label\{[^{}]*\}/g,"").replace(/\\(?:sub)*section\{([^{}]*)\}/g,"\n$1\n").replace(/~|--/g,m=>m==="~"?" ":"–")));}
  }
  function renderInlineParts(parts,target){for(const part of parts){const span=el("span",part.kind==="insert"?"ol-insert":part.kind==="delete"?"ol-delete":"ol-equal");renderInline(part.text,span);target.append(span);}}
  window.paperSourceRender=(text,target)=>{target.replaceChildren();renderInline(text.replace(/(^|[^\\])%[^\n]*/g,"$1"),target);};
  function renderDiff(before,after,target,forceSeparated=false){
    const display=reviewDisplay(before,after);
    if(display.mode==="inline"&&!forceSeparated){for(const p of display.parts){const s=el("span",p.kind==="insert"?"ol-insert":p.kind==="delete"?"ol-delete":"ol-equal");renderInline(p.text,s);target.append(s);}return;}
    target.classList.add("ol-separated-diff");
    target.append(el("p","ol-diff-layout-note",forceSeparated?"按完整小节对照原文与新版；正式排版见候选 PDF。":"本段改写较多，分开展示原文和候选，便于逐段阅读。"));
    for(const [name,sentences,other,kind] of [["原文",display.before,display.after,"delete"],["修改后 · 候选，尚未接受",display.after,display.before,"insert"]]){
      const version=el("section","ol-diff-version "+kind),heading=el("div","ol-diff-version-heading"),body=el("div","ol-diff-version-body");
      heading.append(el("strong","",name),el("small","",kind==="delete"?"蓝色：建议删除 · 黑色：保留":"红色：建议新增 · 黑色：保留"));version.append(heading,body);
      for(const sentence of separatedSentenceParts(sentences,other)){const span=el("span",sentence.retained?"ol-equal":kind==="delete"?"ol-delete":"ol-insert");renderInline(sentence.text,span);body.append(span);}
      if(!sentences.length)body.append(el("span","ol-diff-empty",kind==="delete"?"此处原文为空":"此处建议整段删除"));
      target.append(version);
    }
  }
  function renderVisual(text){
    const box=$("ol-visual");box.replaceChildren(el("div","ol-visual-note","可视阅读 · 原文与已接受修改 · 切换 Code 编辑"));
    const a=text.indexOf("\\begin{document}"),b=text.lastIndexOf("\\end{document}");let body=a>=0?text.slice(a+16,b>=0?b:undefined):text;
    body=body.replace(/\\begin\{(?:figure\*?|table\*?)\}[\s\S]*?\\end\{(?:figure\*?|table\*?)\}/g,s=>{const c=/\\caption\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/.exec(s);return "\n\n[原工程图表] "+(c?c[1]:"")+"\n\n";});
    body=body.split("\n").filter(line=>!line.trim().startsWith("%")).join("\n");
    for(const textBlock of body.split(/\n\s*\n/).filter(Boolean)){const heading=/^\s*\\(section|subsection|subsubsection)\{([^{}]+)\}/.exec(textBlock);const p=el(heading?heading[1]==="section"?"h2":"h3":"div",heading?"":"ol-visual-paragraph");renderInline(textBlock,p);box.append(p);}
  }

  function previewBanner(text=""){$("ol-preview-banner").textContent=text;$("ol-preview-banner").classList.toggle("hidden",!text);}
  async function indexPdfText(pdf,serial){
    const pages=[];state.pdfTextContents=new Map();$("ol-pdf-issue-status").textContent="建立文字索引…";
    for(let number=1;number<=pdf.numPages;number++){const page=await pdf.getPage(number),content=await page.getTextContent();if(serial!==state.pdfIndexSerial)return;state.pdfTextContents.set(number,content);const fragments=content.items.map(item=>item.str||""),raw=fragments.join("");pages.push({page:number,raw,fragments,text:window.PaperPdfReview.normalizeMapped(raw).text});}
    if(serial!==state.pdfIndexSerial)return;state.pdfPages=pages;updatePdfIssueAnchors();
  }
  function updatePdfIssueAnchors(){
    if(!window.PaperPdfReview)return;
    if(state.pdfJob?.source_hash!==state.doc?.sha256||!state.pdfPages.length){state.pdfAnchors=new Map();state.pdfUnmatched=new Map(proposals().map(p=>[p.id,state.pdfJob?.source_hash&&state.pdfJob.source_hash!==state.doc?.sha256?"当前 PDF 早于正文版本，请先更新 PDF。":"PDF 文字索引准备中。"]));}
    else{const result=window.PaperPdfReview.issueAnchors(state.pdfPages,proposals());state.pdfAnchors=result.anchors;state.pdfUnmatched=result.unmatched;}
    $("ol-pdf-issue-status").textContent=state.pdfPages.length?state.pdfAnchors.size+" 个问题已定位"+(state.pdfUnmatched.size?" · "+state.pdfUnmatched.size+" 个见列表":""):"";
    renderProposals();paintPdfIssueMarkers();
  }
  function pdfDomTextIndex(layer){
    const entries=[],walker=document.createTreeWalker(layer,NodeFilter.SHOW_TEXT);let raw="",node;
    while((node=walker.nextNode())){const value=node.nodeValue||"";if(value){entries.push({node,start:raw.length,end:raw.length+value.length});raw+=value;}}
    return {...window.PaperPdfReview.normalizeMapped(raw),entries,layer};
  }
  function pdfTextRange(index,start,end){
    const a=index.positions[start]?.start,b=index.positions[end-1]?.end;if(a===undefined||b===undefined)return null;
    const first=index.entries.find(entry=>a>=entry.start&&a<entry.end),last=index.entries.find(entry=>b>entry.start&&b<=entry.end);if(!first||!last)return null;
    const range=document.createRange();range.setStart(first.node,a-first.start);range.setEnd(last.node,b-last.start);return range;
  }
  function paintPdfIssueMarkers(){
    const wrap=$("ol-pdf-pages").querySelector(".ol-pdf-page"),layer=wrap?.querySelector(".textLayer");if(!wrap||!layer||!window.PaperPdfReview)return;
    wrap.querySelector(".ol-pdf-annotations")?.remove();if(state.pdfJob?.source_hash!==state.doc?.sha256)return;
    const index=pdfDomTextIndex(layer),overlay=el("div","ol-pdf-annotations"),bounds=wrap.getBoundingClientRect(),occupied=[];
    for(const anchor of state.pdfAnchors.values()){
      if(anchor.page!==state.pdfPage)continue;
      if(index.text.slice(anchor.start,anchor.end)!==anchor.query)continue;
      const range=pdfTextRange(index,anchor.start,anchor.end);if(!range)continue;const rects=[...range.getClientRects()].filter(rect=>rect.width>0&&rect.height>0);if(!rects.length)continue;
      for(const rect of rects){const highlight=el("span","ol-pdf-issue-highlight");highlight.style.left=(rect.left-bounds.left)+"px";highlight.style.top=(rect.top-bounds.top)+"px";highlight.style.width=rect.width+"px";highlight.style.height=rect.height+"px";overlay.append(highlight);}
      const first=rects[0],marker=el("button","ol-pdf-issue-marker","!");let top=first.top-bounds.top-2;const left=Math.min(wrap.clientWidth-22,first.right-bounds.left+4);while(occupied.some(pos=>Math.abs(pos.left-left)<20&&Math.abs(pos.top-top)<17))top+=19;occupied.push({left,top});marker.style.left=left+"px";marker.style.top=top+"px";marker.title=anchor.title||"查看这处问题";marker.setAttribute("aria-label",marker.title);marker.dataset.proposalId=anchor.proposalId;marker.addEventListener("click",guarded(()=>openProposal(anchor.proposalId,anchor.hunkId)));overlay.append(marker);
    }
    wrap.append(overlay);state.pdfDomIndex=index;
  }
  async function navigateOutlinePdf(item){
    if(!state.pdf||!state.pdfPages.length){notice("PDF 文字索引尚未准备好，请稍后再定位目录。");return;}
    const match=window.PaperPdfReview.headingMatch(state.pdfPages,item.title);if(!match){notice("暂未匹配这个标题的 PDF 页面，可用页码浏览或展开源码。");return;}state.pdfPage=match.page;await renderPdfPage();
    const host=$("ol-pdf-pages"),layer=host.querySelector(".textLayer");if(!layer)return;const index=pdfDomTextIndex(layer);
    const mapped=index.text.slice(match.start,match.end)===match.query?match:window.PaperPdfReview.headingMatch([{page:match.page,text:index.text,fragments:index.entries.map(entry=>entry.node.nodeValue||"")}],item.title);
    const range=mapped?pdfTextRange(index,mapped.start,mapped.end):null;if(!range){notice("已到标题相关页；该标题的文字层暂未匹配，未猜测页内位置。");return;}
    const rect=range.getBoundingClientRect(),viewport=host.getBoundingClientRect();host.scrollTo({top:Math.max(0,host.scrollTop+rect.top-viewport.top-45),behavior:"smooth"});
  }
  async function locateProposalPdf(proposal){
    const anchor=state.pdfAnchors.get(proposal.id);
    if(anchor){state.pdfPage=anchor.page;await renderPdfPage();const marker=[...$("ol-pdf-pages").querySelectorAll(".ol-pdf-issue-marker")].find(node=>node.dataset.proposalId===proposal.id);marker?.scrollIntoView({block:"center",behavior:"smooth"});return;}
    const outline=arr(state.doc?.outline),section=outline.find(item=>item.id===proposal.section_id)||outline.filter(item=>item.start<=proposal.start).at(-1);
    if(section)await navigateOutlinePdf(section);notice((state.pdfUnmatched.get(proposal.id)||"没有可靠的精确 PDF 锚点。")+(section?"已定位相关章节页面，具体问题可在列表中审阅。":"可在列表中审阅；需要时展开源码定位。"));
  }
  let pdfjsPromise=null;
  async function pdfjs(){if(!pdfjsPromise)pdfjsPromise=import("/static/vendor/pdfjs/pdf.mjs").then(lib=>{lib.GlobalWorkerOptions.workerSrc="/static/vendor/pdfjs/pdf.worker.mjs";return lib;});return pdfjsPromise;}
  async function setPdf(job){
    const url=localUrl(job.pdf_url||job.preview_url);if(!url)return;state.previewJob=job;
    $("ol-preview-empty").classList.add("hidden");$("ol-pdf-download").href=url;$("ol-pdf-download").classList.remove("hidden");$("ol-compile-status").textContent="已更新";
    previewBanner(job.source_hash&&job.source_hash!==state.doc?.sha256?"正文已有更新；右侧是上次编译结果。":"");
    const serial=++state.pdfLoadSerial;
    try{const lib=await pdfjs(),pdf=await lib.getDocument({url,isEvalSupported:false,cMapUrl:"/static/vendor/pdfjs/cmaps/",cMapPacked:true,standardFontDataUrl:"/static/vendor/pdfjs/standard_fonts/",wasmUrl:"/static/vendor/pdfjs/wasm/"}).promise;if(serial!==state.pdfLoadSerial){pdf.destroy();return;}if(state.pdf)state.pdf.destroy();const indexSerial=++state.pdfIndexSerial;state.pdf=pdf;state.pdfJob=job;state.pdfPages=[];state.pdfAnchors=new Map();state.pdfUnmatched=new Map();state.pdfTextContents=new Map();state.pdfPage=Math.min(state.pdfPage,pdf.numPages);$("ol-pdf-fallback").classList.add("hidden");$("ol-pdf-pages").classList.remove("hidden");$("ol-pdf-controls").classList.remove("hidden");await renderPdfPage();indexPdfText(pdf,indexSerial).catch(e=>{$("ol-pdf-issue-status").textContent="PDF 文字索引未完成";notice(e.message,true);});}
    catch(e){if(serial!==state.pdfLoadSerial)return;const frame=$("ol-pdf-fallback");frame.src=url;frame.classList.remove("hidden");$("ol-pdf-pages").classList.add("hidden");$("ol-pdf-controls").classList.add("hidden");previewBanner("PDF 使用浏览器预览；可在源码或可视阅读中划词翻译。");}
  }
  async function renderPdfPage(){
    if(!state.pdf)return;const serial=++state.pdfSerial,lib=await pdfjs(),page=await state.pdf.getPage(state.pdfPage),host=$("ol-pdf-pages");
    const base=page.getViewport({scale:1}),fit=Math.max(0.4,Math.min(state.sourceVisible?1.5:1.75,(host.clientWidth-64)/base.width)),viewport=page.getViewport({scale:fit*state.zoom});
    if(serial!==state.pdfSerial)return;
    const wrap=el("div","ol-pdf-page"),canvas=el("canvas"),layer=el("div","textLayer");
    wrap.style.width=viewport.width+"px";wrap.style.height=viewport.height+"px";wrap.style.setProperty("--scale-factor",String(viewport.scale));wrap.style.setProperty("--total-scale-factor",String(viewport.scale));
    const ratio=window.devicePixelRatio||1;canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);canvas.style.width=viewport.width+"px";canvas.style.height=viewport.height+"px";wrap.append(canvas,layer);host.replaceChildren(wrap);
    await page.render({canvasContext:canvas.getContext("2d"),viewport,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null}).promise;
    if(serial!==state.pdfSerial)return;
    await new lib.TextLayer({textContentSource:state.pdfTextContents?.get(state.pdfPage)||await page.getTextContent(),container:layer,viewport}).render();if(serial!==state.pdfSerial)return;paintPdfIssueMarkers();
    $("ol-page-count").textContent=state.pdfPage+" / "+state.pdf.numPages;$("ol-zoom").textContent=Math.round(state.zoom*100)+"%";$("ol-page-prev").disabled=state.pdfPage<=1;$("ol-page-next").disabled=state.pdfPage>=state.pdf.numPages;
  }
  $("ol-page-prev").addEventListener("click",guarded(async()=>{if(state.pdfPage>1){state.pdfPage--;await renderPdfPage();}}));$("ol-page-next").addEventListener("click",guarded(async()=>{if(state.pdf&&state.pdfPage<state.pdf.numPages){state.pdfPage++;await renderPdfPage();}}));
  let wheelBusy=false,wheelLast=0,wheelDistance=0,wheelDirection=0;
  $("ol-pdf-pages").addEventListener("wheel",event=>{
    if(event.ctrlKey||event.metaKey||!state.pdf||Math.abs(event.deltaX)>Math.abs(event.deltaY))return;
    const host=$("ol-pdf-pages"),direction=Math.sign(event.deltaY),bottom=host.scrollTop+host.clientHeight>=host.scrollHeight-3;
    if(!(direction>0?bottom:host.scrollTop<=3)){wheelDistance=0;return;}
    if(direction>0?state.pdfPage>=state.pdf.numPages:state.pdfPage<=1)return;
    event.preventDefault();const now=performance.now();if(wheelBusy||now-wheelLast<550)return;
    if(direction!==wheelDirection)wheelDistance=0;wheelDirection=direction;
    wheelDistance+=Math.abs(event.deltaY)*(event.deltaMode===1?16:1);if(wheelDistance<60)return;
    wheelDistance=0;wheelLast=now;wheelBusy=true;state.pdfPage+=direction;
    renderPdfPage().then(()=>{host.scrollTop=direction>0?0:host.scrollHeight;}).catch(e=>notice(e.message,true)).finally(()=>{wheelBusy=false;});
  },{passive:false});
  $("ol-zoom-out").addEventListener("click",guarded(async()=>{state.zoom=Math.max(.65,state.zoom-.15);await renderPdfPage();}));$("ol-zoom-in").addEventListener("click",guarded(async()=>{state.zoom=Math.min(2.5,state.zoom+.15);await renderPdfPage();}));
  async function compile(){if(!state.pid||!state.doc)return;await flushDrafts();$("ol-compile").disabled=true;$("ol-compile-status").textContent="正在编译当前稿…";try{const job=await api("/api/preview/"+encodeURIComponent(state.pid)+"/compile",{base_hash:state.doc.sha256});state.previewJob=job;await pollCompile(job);}catch(e){$("ol-compile-status").textContent="编译失败";notice(e.message,true);$("ol-compile").disabled=false;}}
  async function pollCompile(job){
    const pid=state.pid;clearTimeout(state.compileTimer);const data=active(job)?await api("/api/preview/"+encodeURIComponent(pid)+"/jobs/"+encodeURIComponent(job.id)):job;if(pid!==state.pid)return;state.previewJob=data;
    if(active(data)){$("ol-compile-status").textContent=data.status==="queued"?"编译排队中…":"正在编译当前稿…";state.compileTimer=setTimeout(()=>pollCompile(data).catch(e=>{notice(e.message,true);$("ol-compile").disabled=false;}),1200);return;}
    $("ol-compile").disabled=false;if(data.status==="completed")await setPdf(data);else{$("ol-compile-status").textContent=data.status==="stale"?"编译版本已过期":"编译未完成";notice(data.error||"请查看编译信息。",true);}
    const logs=typeof data.diagnostics==="string"?data.diagnostics:data.diagnostics?JSON.stringify(data.diagnostics,null,2):data.error||"";$("ol-diagnostics-text").textContent=logs;$("ol-diagnostics").classList.toggle("hidden",!logs);
  }
  async function latestPreview(){const pid=state.pid;try{const job=await api("/api/preview/"+encodeURIComponent(pid)+"/latest");if(pid!==state.pid||!job)return;if(active(job)){state.previewJob=job;$("ol-compile").disabled=true;await pollCompile(job);}else if(job.status==="completed")await setPdf(job);}catch(e){if(e.status!==404)notice(e.message,true);}}
  $("ol-compile").addEventListener("click",guarded(compile));
  async function waitPreview(job,serial,pid){while(active(job)){await new Promise(resolve=>setTimeout(resolve,1000));if(serial!==state.layoutSerial||pid!==state.pid)return null;job=await api("/api/preview/"+encodeURIComponent(pid)+"/jobs/"+encodeURIComponent(job.id));}if(job.status!=="completed"||!job.pdf_url)throw Error(job.error||"PDF 编译未完成，请查看候选排版。");return job;}
  async function comparisonPdf(job){const url=localUrl(job.pdf_url);if(!url)throw Error("PDF 地址无效。");const lib=await pdfjs();return lib.getDocument({url,isEvalSupported:false,cMapUrl:"/static/vendor/pdfjs/cmaps/",cMapPacked:true,standardFontDataUrl:"/static/vendor/pdfjs/standard_fonts/",wasmUrl:"/static/vendor/pdfjs/wasm/"}).promise;}
  async function renderLayoutPages(){
    if(state.layoutDocs.length!==2)return;const serial=state.layoutSerial,current=state.layoutPage,max=Math.max(...state.layoutDocs.map(pdf=>pdf.numPages));
    await Promise.all(state.layoutDocs.map(async(pdf,index)=>{const host=$(index===0?"ol-layout-before":"ol-layout-after"),number=Math.min(current,pdf.numPages),page=await pdf.getPage(number),base=page.getViewport({scale:1}),viewport=page.getViewport({scale:Math.max(.3,(host.clientWidth-24)/base.width)}),canvas=el("canvas"),sheet=el("div","ol-layout-sheet"),layer=el("div","textLayer"),ratio=window.devicePixelRatio||1;canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);canvas.style.width=viewport.width+"px";canvas.style.height=viewport.height+"px";sheet.style.width=viewport.width+"px";sheet.style.height=viewport.height+"px";sheet.style.setProperty("--total-scale-factor",String(viewport.scale));sheet.append(canvas,layer);if(serial!==state.layoutSerial)return;host.replaceChildren(el("p","ol-layout-page-caption","第 "+number+" / "+pdf.numPages+" 页"),sheet);await page.render({canvasContext:canvas.getContext("2d"),viewport,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null}).promise;if(serial!==state.layoutSerial)return;const lib=await pdfjs();await new lib.TextLayer({textContentSource:await page.getTextContent(),container:layer,viewport}).render();}));
    $("ol-layout-page").textContent=current+" / "+max;$("ol-layout-prev").disabled=current<=1;$("ol-layout-next").disabled=current>=max;
  }
  function closeLayout(){++state.layoutSerial;$("ol-layout-dialog").close();for(const pdf of state.layoutDocs)pdf.destroy();state.layoutDocs=[];state.layoutCandidatePages=[];}
  function renderComparisonOutline(){
    const box=$("ol-structural-pdf-outline");box.replaceChildren();box.classList.toggle("hidden",state.layoutMode!=="structural");if(state.layoutMode!=="structural")return;
    box.append(el("span","","候选五节目录"));for(const unit of state.layoutUnits||[]){const included=state.layoutSelected.includes(unit.id)||unit.status==="accepted",button=el("button","",unit.title+(included?"":" · 本次保留当前稿"));button.disabled=!state.layoutCandidatePages?.length||!included;button.addEventListener("click",guarded(()=>navigateComparisonUnit(unit)));box.append(button);}
  }
  async function indexCandidatePdf(pdf,serial){
    const pages=[];for(let number=1;number<=pdf.numPages;number++){const page=await pdf.getPage(number),content=await page.getTextContent();if(serial!==state.layoutSerial)return;const fragments=content.items.map(item=>item.str||"");pages.push({page:number,fragments,text:window.PaperPdfReview.normalizeMapped(fragments.join("")).text});}
    if(serial!==state.layoutSerial)return;state.layoutCandidatePages=pages;renderComparisonOutline();const first=state.layoutUnits?.find(unit=>state.layoutSelected.includes(unit.id))||state.layoutUnits?.find(unit=>unit.status==="accepted");if(first)await navigateComparisonUnit(first);
  }
  async function navigateComparisonUnit(unit){
    const match=window.PaperPdfReview.headingMatch(state.layoutCandidatePages||[],unit.heading||unit.title);if(!match){$("ol-layout-error").textContent="尚未可靠匹配这个候选小节标题，可按页阅读完整候选 PDF。";return;}
    state.layoutPage=match.page;await renderLayoutPages();const host=$("ol-layout-after"),layer=host.querySelector(".textLayer");if(layer){const index=pdfDomTextIndex(layer),range=index.text.slice(match.start,match.end)===match.query?pdfTextRange(index,match.start,match.end):null;if(range){const rect=range.getBoundingClientRect();host.scrollTo({top:Math.max(0,host.scrollTop+rect.top-host.getBoundingClientRect().top-30),behavior:"smooth"});}}
    const link=$("ol-candidate-pdf-open");if(state.layoutCandidateUrl)link.href=state.layoutCandidateUrl+"#page="+match.page;
  }
  async function previewLayout(){
    if(!["layout","structural"].includes(state.proposal?.review_mode)||!issuePresentation(state.proposal).editable)return;await flushDrafts();const serial=++state.layoutSerial,pid=state.pid,proposal=state.proposal,isStructural=proposal.review_mode==="structural";
    const eligible=issuePresentation(proposal).hunks.filter(h=>pending(h)&&h.selectable!==false);
    state.layoutSelected=state.selected.size?eligible.filter(h=>state.selected.has(h.id)).map(h=>h.id):eligible.map(h=>h.id);state.layoutBaseHash=state.doc.sha256;state.layoutPage=state.pdfPage;state.layoutMode=proposal.review_mode;state.layoutUnits=isStructural?structuralUnits(proposal):[];state.layoutCandidatePages=[];state.layoutCandidateUrl="";renderComparisonOutline();$("ol-layout-title").textContent=proposal.title||(isStructural?"Method 五节重构候选":"排版对比");$("ol-comparison-kind").textContent=isStructural?"完整 Method 对照 · 真实编译 PDF":"排版对比 · 真实编译 PDF";$("ol-candidate-pdf-label").textContent=isStructural?"候选 Method · 尚未接受":"候选排版 · 尚未接受";$("ol-layout-status").textContent="正在分别编译当前稿与独立候选；正文保持不变。";$("ol-layout-error").textContent="";$("ol-layout-before").replaceChildren();$("ol-layout-after").replaceChildren();$("ol-candidate-pdf-open").classList.add("hidden");$("ol-layout-accept").textContent=isStructural?"接受预览中的 "+state.layoutSelected.length+" 节":"接受此排版";$("ol-layout-accept").disabled=true;if(!$("ol-layout-dialog").open)$("ol-layout-dialog").showModal();
    if(!state.layoutSelected.length){$("ol-layout-status").textContent="当前没有可预览的待审单元。";return;}
    try{
      const baseJob=state.pdfJob?.source_hash===state.doc.sha256?state.pdfJob:await api("/api/preview/"+encodeURIComponent(pid)+"/compile",{base_hash:state.doc.sha256});
      const candidateJob=await api("/api/preview/"+encodeURIComponent(pid)+"/candidate",{proposal_id:proposal.id,base_hash:state.layoutBaseHash,selected_hunk_ids:state.layoutSelected});
      const jobs=await Promise.all([waitPreview(baseJob,serial,pid),waitPreview(candidateJob,serial,pid)]);if(serial!==state.layoutSerial||pid!==state.pid||jobs.some(job=>!job))return;
      if(jobs[1].base_is_current===false||jobs[1].can_accept===false||state.doc.sha256!==state.layoutBaseHash)throw Error("原稿或候选在预览期间已变化，请关闭后重新生成对比。");
      const docs=await Promise.all(jobs.map(comparisonPdf));if(serial!==state.layoutSerial){docs.forEach(pdf=>pdf.destroy());return;}state.layoutDocs=docs;state.layoutCandidateUrl=localUrl(jobs[1].pdf_url);$("ol-candidate-pdf-open").href=state.layoutCandidateUrl;$("ol-candidate-pdf-open").classList.remove("hidden");await renderLayoutPages();$("ol-layout-status").textContent="对比包含 "+state.layoutSelected.length+(isStructural?" 个 Method 结构单元。":" 项排版调整。")+"右侧为真实候选 PDF，尚未接受。"+(isStructural&&state.layoutSelected.length<eligible.length?"其余小节保留当前稿，跨节定义需继续统一。":"");$("ol-layout-accept").disabled=false;if(isStructural)await indexCandidatePdf(docs[1],serial);
    }catch(e){if(serial===state.layoutSerial){$("ol-layout-error").textContent=e.message;$("ol-layout-status").textContent="候选未通过编译或版本核查；正文保持不变。";}}
  }
  $("ol-layout-preview").addEventListener("click",guarded(previewLayout));$("ol-layout-close").addEventListener("click",closeLayout);$("ol-layout-dialog").addEventListener("cancel",event=>{event.preventDefault();closeLayout();});
  $("ol-layout-prev").addEventListener("click",guarded(async()=>{if(state.layoutPage>1){state.layoutPage--;await renderLayoutPages();}}));$("ol-layout-next").addEventListener("click",guarded(async()=>{if(state.layoutDocs.length&&state.layoutPage<Math.max(...state.layoutDocs.map(pdf=>pdf.numPages))){state.layoutPage++;await renderLayoutPages();}}));
  $("ol-layout-accept").addEventListener("click",guarded(async()=>{if(state.doc.sha256!==state.layoutBaseHash)throw Error("当前稿已变化，请重新预览候选。");state.selected=new Set(state.layoutSelected);closeLayout();renderHunks();await resolveProposal(true);}));

  function captureSelection(event){
    if($("ol-translation-popover").contains(event.target)||$("ol-selection-bubble").contains(event.target))return;let text="",context="",selected=null;
    if(event.target===$("ol-source")&&isMain()){const input=$("ol-source"),a=input.selectionStart,b=input.selectionEnd;text=input.value.slice(a,b);context=input.value.slice(Math.max(0,a-450),Math.min(input.value.length,b+450));selected={text,context,source:"source",original_tex:text,before_context:input.value.slice(Math.max(0,a-100),a),after_context:input.value.slice(b,b+100)};}
    else if(event.target.closest?.("#ol-pdf-pages")){
      const selection=window.getSelection();if(selection?.rangeCount){const range=selection.getRangeAt(0),node=range.startContainer.nodeType===3?range.startContainer.parentElement:range.startContainer,layer=node.closest?.(".textLayer");
        if(layer&&layer.contains(range.endContainer)){const before=document.createRange(),after=document.createRange();before.selectNodeContents(layer);before.setEnd(range.startContainer,range.startOffset);after.selectNodeContents(layer);after.setStart(range.endContainer,range.endOffset);const read=range=>window.PaperPdfReview.fragmentText(range.cloneContents());text=read(range);const beforeText=read(before).slice(-100),afterText=read(after).slice(0,100);context=beforeText+text+afterText;selected={text,context:context.slice(0,2000),source:"pdf",page:state.pdfPage,pdf_source_hash:state.pdfJob?.source_hash||"",before_context:beforeText,after_context:afterText};}
      }
    }
    else if(event.target.closest?.("#ol-visual, #ol-review-hunks, #ol-local-context")){text=window.getSelection()?.toString()||"";context=event.target.closest(".ol-visual-paragraph, .ol-hunk-diff, #ol-local-context")?.textContent?.slice(0,2000)||"";selected={text,context,source:"review"};}
    if(text.trim().length<2){$("ol-selection-bubble").classList.add("hidden");return;}state.selection={...(selected||{}),text:text.trim(),context:context.slice(0,2000)};if(selected?.source==="pdf")setChatSelection(state.selection);
    const b=$("ol-selection-bubble"),parent=$("ol-review-dialog").open?$("ol-review-dialog"):$("overleaf-workbench");parent.append(b);b.style.left=Math.min(innerWidth-190,Math.max(10,event.clientX||400))+"px";b.style.top=Math.min(innerHeight-48,Math.max(50,(event.clientY||150)+12))+"px";b.classList.remove("hidden");
    $("ol-selection-discuss").disabled=$("ol-selection-revise").disabled=selected?.source==="review";
  }
  $("overleaf-workbench").addEventListener("mouseup",captureSelection);$("ol-review-dialog").addEventListener("mouseup",captureSelection);$("ol-source").addEventListener("keyup",e=>{if(e.shiftKey)captureSelection(e);});
  $("ol-selection-bubble").addEventListener("pointerdown",e=>e.preventDefault());$("ol-selection-translate").addEventListener("click",guarded(startTranslation));
  for(const action of ["discuss","revise"])$("ol-selection-"+action).addEventListener("click",()=>{setChatSelection(state.selection);$("ol-chat-action").value=action;$("ol-prompt-action").value=action;$("ol-selection-bubble").classList.add("hidden");drawer("discuss");$("ol-chat-input").focus();});
  $("ol-translate-selection").addEventListener("pointerdown",e=>e.preventDefault());$("ol-translate-selection").addEventListener("click",guarded(async()=>{const input=$("ol-source");if(state.mode==="code"&&input.selectionEnd>input.selectionStart)state.selection={text:input.value.slice(input.selectionStart,input.selectionEnd),context:input.value.slice(Math.max(0,input.selectionStart-400),Math.min(input.value.length,input.selectionEnd+400)).slice(0,2000)};if(!state.selection?.text){drawer("translate");notice("请先选中要翻译的文字。");return;}await startTranslation();}));
  function translationAttribution(job,engine="auto",waitingSeconds=null){
    const result=job?.result||{},actual=job?.engine||result.engine||(engine==="agent"?"agent":"local"),provider=job?.provider||result.provider||"",version=job?.model_version||result.model_version||"";
    const labels={argos:"Argos Translate",argos_translate:"Argos Translate",argostranslate:"Argos Translate",project_glossary_local:"专业术语词典",ctranslate2_local:"本地 NLLB",codex:"GPT",codex_app_server:"GPT"};
    const parts=[actual==="agent"?"Agent 校订":"离线快译"];
    let source=labels[provider]||(actual==="agent"?"GPT":"本地翻译工具"),model="";
    if(provider==="ctranslate2_local"&&version.startsWith("argos-opus"))source="轻量 OPUS · INT8";
    if(provider==="project_glossary_local"){const match=version.match(/(?:^|[^a-z0-9])v(\d+(?:\.\d+)*)(?=$|[^a-z0-9])/i);if(match)source+=" v"+match[1];}
    if(provider==="ctranslate2_local"){const size=[job?.model||"",version].join(" ").match(/(\d+(?:\.\d+)?[mb])(?:\b|[-_:])/i);if(size)model="NLLB-"+size[1].toUpperCase();}
    if(actual==="agent")model=job?.model||version.match(/gpt-[a-z0-9_.-]+/i)?.[0]||"";
    parts.push(source);if(model)parts.push(model);
    const elapsed=job?.elapsed_s??result.elapsed_s;
    if(typeof elapsed==="number"&&Number.isFinite(elapsed))parts.push("耗时 "+Math.max(0,elapsed).toFixed(1)+"s");
    else if(typeof waitingSeconds==="number")parts.push("等待 "+Math.max(0,waitingSeconds).toFixed(1)+"s");
    if(job?.cached)parts.push("缓存");else if(job?.reused)parts.push("复用任务");
    return parts.join(" · ");
  }
  function translationText(session=state.translationSession){return session?.displayJob?.result?.translation||"";}
  function renderTranslationSession(){
    const session=state.translationSession,display=session?.displayJob,result=display?.result||{},text=translationText(session),job=display||session?.job;
    $("ol-translation-original").textContent=session?.selected?.text||"";
    $("ol-translation-model").textContent=job?translationAttribution(job,session.requestedEngine,(performance.now()-(session.startedAt||performance.now()))/1000):"快速离线翻译 · 默认不调用 Agent";
    $("ol-translation-status").textContent=session?.message||"";$("ol-translation-status").classList.toggle("error",!!session?.error);
    $("ol-translation-result").textContent=text;$("ol-translation-copy").disabled=!text;
    $("ol-translation-terms").replaceChildren();for(const term of arr(result.terms))$("ol-translation-terms").append(el("dt","",term.source),el("dd","",term.target));
    $("ol-translation-note").textContent=[result.note,...arr(result.quality_warnings),result.protected_spans_preserved===true?"公式与引用已按原文保留。":""].filter(Boolean).join("\n");
    const qc=result.quality_checks;if(qc){$("ol-translation-note").textContent=(qc.terms_checked?"术语检查 "+qc.terms_passed+"/"+qc.terms_checked+" · ":"")+(qc.numbers_preserved?"数字保留":"数字需复核")+" · 语义待复核";$("ol-translation-note").title=[result.note,...arr(result.quality_warnings)].filter(Boolean).join("\n");}
    $("ol-translation-cancel").classList.toggle("hidden",!session?.busy);$("ol-translation-cancel").textContent=session?.requestedEngine==="agent"?"停止 Agent 校订（保留快速译文）":"停止快速翻译";
    $("ol-translation-retry").disabled=!session||!!session.busy;$("ol-translation-refine").disabled=!session||!!session.busy;
    $("ol-translation-refine").textContent=session?.quickJob?"Agent 校订":"Agent 翻译 / 校订";
  }
  function renderTranslationHistory(){
    const box=$("ol-translation-history");box.replaceChildren();
    for(const job of state.translations){const card=el("div","ol-translation-history-card");card.append(el("small","",translationAttribution(job,job.engine==="agent"?"agent":"auto")),el("p","",job.text),el("p","",job.result?.translation));box.append(card);}
  }
  function rememberTranslation(job){state.translations=[job,...state.translations.filter(previous=>previous.id!==job.id)].slice(0,5);renderTranslationHistory();}
  function stopTranslationQuietly(job){if(job?.id&&active(job))api("/api/translation/jobs/"+encodeURIComponent(job.id)+"/cancel",{}).catch(()=>{});}
  function newTranslationSession(selected){
    stopTranslationQuietly(state.translationSession?.job);++state.translationSerial;
    const session={projectId:state.pid,selected:{text:selected.text.trim(),context:selected.context||""},targetLanguage:$("ol-target-language").value,quickJob:null,displayJob:null,job:null,busy:false,requestedEngine:"auto",message:"",error:false};
    state.translationSession=session;state.translation=null;return session;
  }
  async function startTranslation(){
    const selected=state.selection;if(!selected?.text)return;if(selected.text.length>5000)throw Error("一次最多翻译 5000 个字符，请缩小选择范围。");
    const session=newTranslationSession(selected);$("ol-selection-bubble").classList.add("hidden");($("ol-review-dialog").open?$("ol-review-dialog"):$("overleaf-workbench")).append($("ol-translation-popover"));$("ol-translation-popover").classList.remove("hidden");
    await requestTranslation("auto",session);
  }
  async function requestTranslation(engine,session=state.translationSession){
    if(!session||session!==state.translationSession||session.projectId!==state.pid||session.busy)return;
    const serial=++state.translationSerial,pid=state.pid;session.busy=true;session.requestedEngine=engine;session.error=false;session.startedAt=performance.now();
    session.message=engine==="agent"?(translationText(session)?"Agent 正在校订；下方保留快速译文，可继续复制。":"正在按所选上下文进行 Agent 翻译。"):(translationText(session)?"正在重新快速翻译；下方保留已有译文。":"正在使用本地快速翻译工具…");
    renderTranslationSession();
    const body={project_id:pid,text:session.selected.text,context:session.selected.context,target_language:session.targetLanguage,engine};
    if(engine==="agent"&&translationText(session))body.draft_translation=session.quickJob?.result?.translation||translationText(session);
    try{
      const job=await api("/api/translation/jobs",body);
      if(serial!==state.translationSerial||pid!==state.pid||session!==state.translationSession){stopTranslationQuietly(job);return;}
      if(engine==="auto"&&(job.engine||job.result?.engine)!=="local"){stopTranslationQuietly(job);throw Error("快速翻译通道尚未就绪，未使用 Agent 结果。请稍后重试快速翻译。");}
      session.job=job;state.translation=job;session.cacheFlags={cached:!!job.cached,reused:!!job.reused};await pollTranslation(job,serial,pid,session,engine);
    }catch(error){if(serial===state.translationSerial&&pid===state.pid&&session===state.translationSession){session.busy=false;session.error=true;session.message=(engine==="agent"&&translationText(session)?"Agent 校订未完成，已保留快速译文。\n":"")+(error.message||"翻译暂未完成，请重试。");renderTranslationSession();}}
  }
  async function pollTranslation(job,serial,pid,session,engine,attempt=0){
    if(serial!==state.translationSerial||pid!==state.pid||session!==state.translationSession)return;
    job={...job,cached:!!job.cached||!!session.cacheFlags?.cached,reused:!!job.reused||!!session.cacheFlags?.reused};session.job=job;state.translation=job;
    if(engine==="auto"&&(job.engine||job.result?.engine)!=="local"){stopTranslationQuietly(job);throw Error("快速通道未返回离线译文，未继续使用 Agent。");}
    if(active(job)){renderTranslationSession();await new Promise(resolve=>setTimeout(resolve,engine==="auto"?Math.min(500,120+attempt*50):1000));if(serial!==state.translationSerial||pid!==state.pid||session!==state.translationSession)return;return pollTranslation(await api("/api/translation/jobs/"+encodeURIComponent(job.id)),serial,pid,session,engine,attempt+1);}
    session.busy=false;
    if(job.status!=="completed"){
      const unavailable=job.error_code==="local_unavailable",retained=!!translationText(session);
      session.error=!["cancelled","interrupted"].includes(job.status);
      session.message=(engine==="agent"&&retained?"Agent 校订未完成，已保留快速译文。\n":retained?"已保留已有译文。\n":"")+(unavailable?"当前没有可用的本地翻译模型。可稍后重试快速翻译，或显式点击 Agent 翻译 / 校订。":job.error||(job.status==="cancelled"?"本次翻译已停止。":"本次翻译未完成，可重新尝试。"));
      renderTranslationSession();return;
    }
    if(job.target_language&&job.target_language!==session.targetLanguage)throw Error("返回译文的语言与当前选择不符，未替换已有内容。");
    if(!job.result?.translation?.trim())throw Error("未返回可读译文，已有快速译文保持不变。");
    if(job.result.protected_spans_preserved===false)throw Error("公式或引用未通过保护检查，已有快速译文保持不变。");
    if(engine==="auto")session.quickJob=job;
    session.displayJob=job;session.error=false;session.message=engine==="agent"?"Agent 校订完成。":"快速译文已就绪；需要更细的上下文校订时，可另行点击 Agent 校订。";
    rememberTranslation(job);renderTranslationSession();
  }
  async function cancelTranslation(){
    const session=state.translationSession;if(!session)return;const job=session.job,pid=state.pid,serial=++state.translationSerial;
    session.busy=false;session.error=false;session.message=translationText(session)?"已停止本次处理，快速译文与复制功能保持可用。":"本次翻译已停止。";renderTranslationSession();
    if(!job?.id||!active(job))return;
    try{const cancelled=await api("/api/translation/jobs/"+encodeURIComponent(job.id)+"/cancel",{});if(serial===state.translationSerial&&pid===state.pid&&session===state.translationSession){session.job=cancelled;state.translation=cancelled;}}
    catch(error){if(serial===state.translationSerial&&pid===state.pid&&session===state.translationSession){session.message="停止请求暂未确认；已有译文保持不变。"+(error.message?"\n"+error.message:"");renderTranslationSession();}}
  }
  $("ol-translation-retry").addEventListener("click",guarded(()=>requestTranslation("auto")));
  $("ol-translation-refine").addEventListener("click",guarded(()=>requestTranslation("agent")));
  const feedback=el("div","ol-translation-feedback");feedback.append(el("span","","译文评价 "));
  for(const [rating,label] of [["accurate","准确"],["needs_correction","需修正"]]){const button=el("button","ol-link-button",label);button.addEventListener("click",guarded(async()=>{const job=state.translationSession?.displayJob;if(!job?.id||job.status!=="completed")return;await api("/api/translation/jobs/"+encodeURIComponent(job.id)+"/feedback",{rating});feedback.querySelector("span").textContent="已记录："+label+" ";}));feedback.append(button);}
  $("ol-translation-note").after(feedback);
  $("ol-translation-copy").addEventListener("click",guarded(()=>navigator.clipboard.writeText(translationText())));
  $("ol-translation-close").addEventListener("click",()=>$("ol-translation-popover").classList.add("hidden"));
  $("ol-translation-cancel").addEventListener("click",guarded(cancelTranslation));
  $("ol-target-language").addEventListener("change",guarded(async()=>{const selected=state.translationSession?.selected;if(!selected)return;const session=newTranslationSession(selected);renderTranslationSession();await requestTranslation("auto",session);}));
  function setChatSelection(selection){state.chatSelection=selection?{...selection}:null;updateSelectionPill();}
  function updateSelectionPill(){
    const selection=state.chatSelection,selected=!!selection?.text,stale=selected&&selection.source==="pdf"&&selection.pdf_source_hash!==state.doc?.sha256;$("ol-chat-selection").classList.toggle("hidden",!selected);$("ol-prompt-clear").classList.toggle("hidden",!selected);
    $("ol-chat-selection-label").textContent=(selection?.source==="pdf"?"PDF 第 "+selection.page+" 页选区":"选中文字")+(stale?" · PDF 待更新":"");$("ol-chat-selection-text").textContent=selection?.text||"";
    $("ol-prompt-scope").textContent=selected?("PDF 第 "+(selection.page||"—")+" 页 · "+selection.text.slice(0,90)+(selection.text.length>90?"…":"")+(stale?" · 需更新 PDF":"")):($("ol-chat-scope").selectedOptions[0]?.textContent||"全文");
    $("ol-chat-selection").classList.toggle("stale",stale);
  }
  $("ol-chat-selection-clear").addEventListener("click",()=>setChatSelection(null));$("ol-prompt-clear").addEventListener("click",()=>setChatSelection(null));$("ol-chat-scope").addEventListener("change",()=>{setChatSelection(null);updateSelectionPill();});
  function conversationId(){const key="paper-conversation:"+state.pid;let id=localStorage.getItem(key);if(!id){id=crypto.randomUUID();localStorage.setItem(key,id);}return id;}
  const discussionPill=el("div","ol-chat-selection hidden"),discussionLabel=el("p"),discussionClear=el("button","ol-secondary","取消引用主题");discussionClear.type="button";discussionPill.id="ol-discussion-context-pill";discussionPill.append(discussionLabel,discussionClear);$("ol-chat-messages").before(discussionPill);
  function clearDiscussionTopic(){state.discussionSectionId="";discussionPill.classList.add("hidden");}
  discussionClear.addEventListener("click",clearDiscussionTopic);
  $("ol-new-conversation").addEventListener("click",clearDiscussionTopic);
  window.addEventListener("paper-context-discuss",event=>{const detail=event.detail||{};if(detail.project_id!==state.pid||typeof detail.section_id!=="string")return;state.discussionSectionId=detail.section_id;discussionLabel.textContent="本轮引用："+detail.title+" · 发送时附带主题摘要及有界原文";discussionPill.classList.remove("hidden");setChatSelection(null);$("ol-chat-role").value="method";$("ol-chat-action").value="discuss";const area=arr(state.doc?.outline).find(x=>/method/i.test(x.title||""));if(area)$("ol-chat-scope").value=area.id;$("ol-chat-input").value=detail.request||("参考“"+detail.title+"”的交互，继续讨论当前Method。");updateSelectionPill();drawer("discuss");$("ol-chat-input").focus();notice("已引用所选主题。可修改要求后发送；尚未调用模型。");});
  $("ol-new-conversation").addEventListener("click",()=>{state.conversationId=crypto.randomUUID();localStorage.setItem("paper-conversation:"+state.pid,state.conversationId);$("ol-chat-messages").replaceChildren();setChatSelection(null);notice("已开始新的讨论，之前的记录仍保留。");});
  async function loadConversation(){
    const pid=state.pid,id=state.conversationId;try{const response=await api("/api/source-agent/jobs?project_id="+encodeURIComponent(pid)+"&conversation_id="+encodeURIComponent(id));if(pid!==state.pid||id!==state.conversationId)return;const box=$("ol-chat-messages");if(box.childNodes.length)return;for(const job of arr(response.jobs)){box.append(el("div","ol-chat-message author",job.message||job.request||"此前的要求"));const result=job.result||{};box.append(el("div","ol-chat-message agent",[result.summary,result.explanation,job.error].filter(Boolean).join("\n\n")||({queued:"任务仍在排队",running:"任务仍在处理中"}[job.status]||"此前任务："+job.status)));}}catch(e){if(e.status!==404)notice("此前讨论记录暂未载入，可继续当前讨论。");}
  }
  $("ol-pdf-prompt-form").addEventListener("submit",event=>{event.preventDefault();if($("ol-chat-send").disabled)return;const value=$("ol-pdf-prompt-input").value.trim();if(!value)return;$("ol-chat-input").value=value;$("ol-chat-action").value=$("ol-prompt-action").value;drawer("discuss");$("ol-chat-form").requestSubmit();});
  $("ol-pdf-prompt-input").addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key==="Enter"){event.preventDefault();$("ol-pdf-prompt-form").requestSubmit();}});
  $("ol-chat-form").addEventListener("submit",async e=>{
    e.preventDefault();const message=$("ol-chat-input").value.trim();if(!message||!state.pid)return;
    const pid=state.pid,serial=++state.chatSerial,box=$("ol-chat-messages"),action=$("ol-chat-action").value,scope=$("ol-chat-scope").value,selection=state.chatSelection?{...state.chatSelection}:null;box.append(el("div","ol-chat-message author",message));$("ol-chat-send").disabled=$("ol-pdf-prompt-send").disabled=true;
    try{
      await flushDrafts();
      if(selection?.source==="pdf"&&selection.pdf_source_hash!==state.doc?.sha256&&action!=="discuss")throw Error("这份 PDF 早于当前正文。请先更新 PDF 并重新选择，避免修改错位。");
      const payload={project_id:pid,message,scope,role_id:action==="layout"?"calibration":$("ol-chat-role").value,action,conversation_id:state.conversationId};if(state.discussionSectionId)payload.discussion_section_id=state.discussionSectionId;if(selection)payload.selection=selection.source==="pdf"?selection:{original_tex:selection.text,before_context:selection.before_context||"",after_context:selection.after_context||""};
      let job=await api("/api/source-agent/jobs",payload);
      if(pid!==state.pid||serial!==state.chatSerial)return;
      if($("ol-chat-input").value.trim()===message)$("ol-chat-input").value="";if($("ol-pdf-prompt-input").value.trim()===message)$("ol-pdf-prompt-input").value="";state.sourceJob=job;$("ol-chat-cancel").classList.remove("hidden");
      const line=el("div","ol-chat-message agent","专家正在阅读当前稿…"),meta=el("small","ol-chat-meta",""),content=el("div");line.replaceChildren(meta,content);box.append(line);
      while(active(job)){
        meta.textContent=(job.model||"GPT")+" · "+(job.effort||"")+" · 处理中";
        content.textContent="正在基于当前稿件分析。生成结果将作为待审建议。";
        await new Promise(r=>setTimeout(r,1400));if(pid!==state.pid||serial!==state.chatSerial)return;
        job=await api("/api/source-agent/jobs/"+encodeURIComponent(job.id));state.sourceJob=job;
      }
      if(pid!==state.pid||serial!==state.chatSerial)return;
      meta.textContent=(job.model||"GPT")+" · "+(job.effort||"")+" · "+({needs_review:"待你审阅",completed:"已完成",stale:"源稿已更新",failed:"未完成",cancelled:"已停止"}[job.status]||job.status);
      const result=job.result||{};content.textContent=[result.summary,result.explanation,result.notice,job.error].filter(Boolean).join("\n\n")||"本次任务已结束。";
      if(arr(result.needs_evidence).length)line.append(el("p","ol-agent-evidence","仍需依据："+result.needs_evidence.join("；")));
      const createdIds=arr(result.proposal_ids).length?result.proposal_ids:result.proposal_id?[result.proposal_id]:[];
      if(createdIds.length&&job.status!=="stale"){await loadState();for(const [index,id] of createdIds.entries()){const title=arr(result.issues)[index]?.title||"查看局部问题 "+(index+1),button=el("button","ol-secondary",title);button.addEventListener("click",guarded(()=>openProposal(id)));line.append(button);}}
      if(job.status==="stale")line.append(el("p","ol-agent-evidence","任务执行期间正文已变化。此结果未写入正文，请基于最新稿件重新提出要求。"));
    }catch(error){box.append(el("div","ol-chat-message agent",error.message));}
    finally{if(serial===state.chatSerial){$("ol-chat-send").disabled=$("ol-pdf-prompt-send").disabled=false;$("ol-chat-cancel").classList.add("hidden");}}
  });
  $("ol-chat-cancel").addEventListener("click",guarded(async()=>{if(state.sourceJob?.id){const job=await api("/api/source-agent/jobs/"+encodeURIComponent(state.sourceJob.id)+"/cancel",{});state.sourceJob=job;$("ol-chat-cancel").disabled=true;setTimeout(()=>$("ol-chat-cancel").disabled=false,1000);}}));
  async function selectProject(pid){
    if(pid!==state.pid)clearDiscussionTopic();
    mirrorProjects();if(pid===state.pid)return;state.pid=pid;state.doc=null;state.file="main.tex";state.fileData=null;state.dirty=false;state.selection=null;state.chatSelection=null;state.conversationId=pid?conversationId():"";state.translations=[];state.translationSession=null;state.translation=null;++state.translationSerial;++state.chatSerial;++state.pdfSerial;++state.pdfLoadSerial;++state.pdfIndexSerial;closeProposal();closeLayout();clearTimeout(state.saveTimer);clearTimeout(state.compileTimer);state.previewJob=null;state.pdfJob=null;state.pdfPages=[];state.pdfAnchors=new Map();state.pdfUnmatched=new Map();if(state.pdf)state.pdf.destroy();state.pdf=null;setSourceVisible(false);
    $("ol-source").value="";$("ol-source").readOnly=true;$("ol-line-issues").classList.add("hidden");$("ol-pdf-pages").replaceChildren();$("ol-pdf-pages").classList.add("hidden");$("ol-pdf-controls").classList.add("hidden");$("ol-pdf-fallback").classList.add("hidden");$("ol-preview-empty").classList.remove("hidden");$("ol-pdf-download").classList.add("hidden");$("ol-diagnostics").classList.add("hidden");$("ol-compile").disabled=false;$("ol-chat-send").disabled=$("ol-pdf-prompt-send").disabled=false;$("ol-chat-cancel").classList.add("hidden");$("ol-chat-messages").replaceChildren();$("ol-translation-history").replaceChildren();$("ol-translation-popover").classList.add("hidden");previewBanner();setMode("code");renderTranslationSession();updateSelectionPill();
    if(pid){await loadState(true);await Promise.all([latestPreview(),loadConversation()]);window.paperDiscussionContext?.load(pid);}
  }
  window.addEventListener("paper-project-changed",e=>guarded(()=>selectProject(e.detail?.project_id||""))());
  window.addEventListener("paper-view-changed",e=>{state.visible=e.detail?.view==="editor";document.body.classList.toggle("overleaf-mode",state.visible);if(state.visible&&state.pid&&!state.doc)loadState();});
  window.addEventListener("paper-open-revisions",()=>drawer("review"));window.paperEditorBeforeProjectChange=flushDrafts;
  window.addEventListener("beforeunload",e=>{if(state.dirty||state.saving){e.preventDefault();e.returnValue="";}});
  setInterval(()=>{if(state.visible&&state.pid&&!document.hidden&&!state.loading&&!state.dirty&&!state.saving&&!$("ol-review-dialog").open)loadState().catch(e=>notice(e.message,true));},5000);
  state.visible=!$("view-editor").classList.contains("hidden");document.body.classList.toggle("overleaf-mode",state.visible);
  if($("project-select").value)guarded(()=>selectProject($("project-select").value))();
})();
