"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const app = {
    state: {}, projectId: localStorage.getItem("paper-agent-project") || "",
    project: null, projectDetail: null, roleId: "", mode: "full", view: "editor",
    selectedRun: null, runDetail: null, selectedArtifact: null, artifact: null,
    busy: false, refreshing: false, output: "artifacts", modelSignature: "", rolesSignature: "",
    projectSignature: "", libraryLoaded: false, libraryPapers: [], toastTimer: null,
    detailGeneration: 0, libraryGeneration: 0, loadedRevisionRequest: "", revisionRequestProject: ""
  };
  const activeStatuses = new Set(["pending", "queued", "running", "cancelling", "planning", "revising"]);
  const statusNames = {
    pending: "待启动", queued: "排队中", running: "运行中", completed: "已完成",
    success: "已完成", failed: "运行失败", cancelled: "已停止", canceled: "已停止",
    cancelling: "正在停止", needs_evidence: "需要依据", awaiting_evidence: "需要依据",
    review_required: "待复核", blocked: "有待解决", planning: "规划中", revising: "修订中",
    candidate: "候选版本", project_verified: "项目内已验证", verified: "已核验",
    user_approved_baseline: "用户确认的基线", reviewed: "已审阅", needs_revision: "需要修订",
    demo_completed: "演示完成", interrupted: "运行中断",
    rejected: "未采用", draft: "草稿", accepted: "已采纳", mock: "离线演示"
  };
  const modeNames = { revision: "按既定方案改稿", full: "整篇协作", section: "定向优化", discuss: "角色讨论" };
  const kindNames = { teacher: "教师监督", writer: "章节写作", reviewer: "独立审稿", calibration: "规范校准", experience: "经验沉淀" };
  const idOf = (item) => String(item?.id ?? item?.role_id ?? "");
  const rows = (v) => Array.isArray(v) ? v : [];
  const role = (id) => rows(app.state.roles).find((r) => idOf(r) === String(id));
  const roleName = (id) => (role(id)?.name || id || "写作团队").replace(/\s*Argue\s*/gi," ");
  const visibleRole = r => !["experience","utility"].includes(r.kind);
  const isTeacher = (r) => /teacher|supervis|mentor/.test(r.kind || r.id || "") || /教师|导师/.test(r.name || "");
  const fmtDate = (v) => {
    if (!v) return "";
    const d = new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  };
  const short = (v, n = 95) => String(v || "").length > n ? String(v).slice(0, n) + "…" : String(v || "");
  const textOf = (v) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
  const el = (tag, className, content) => {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (content !== undefined && content !== null) n.textContent = String(content);
    return n;
  };
  function toast(message) {
    $("toast").textContent = message;
    $("toast").classList.remove("hidden");
    clearTimeout(app.toastTimer);
    app.toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 3600);
  }
  function notice(message) {
    $("notice-text").textContent = String(message);
    $("notice").classList.remove("hidden");
  }
  function handled(fn) {
    return (...args) => Promise.resolve().then(() => fn(...args)).catch((error) => notice(error.message || String(error)));
  }
  async function api(path, body, method) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 60000);
    try {
      const res = await fetch(path, {
        method: method || (body === undefined ? "GET" : "POST"),
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: abort.signal
      });
      const raw = await res.text();
      let data;
      try { data = raw ? JSON.parse(raw) : {}; }
      catch (_) { throw new Error("本地服务返回了无法读取的响应，请检查服务是否正常。"); }
      if (!res.ok) {
        const detail = data.error || data.detail || data.message || ("请求失败：" + res.status);
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("本地服务响应超时。任务可能仍在后台，请刷新检查记录。");
      if (error instanceof TypeError) throw new Error("无法连接本地工作台。请确认启动程序仍在运行。");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function statusPill(status, provider) {
    const s = String(status || "draft").toLowerCase();
    const tone = activeStatuses.has(s) ? "accent running" : ["completed", "success", "accepted", "project_verified", "verified", "user_approved_baseline", "reviewed"].includes(s) ? "success" :
      ["failed", "rejected", "interrupted"].includes(s) ? "error" : ["needs_evidence", "awaiting_evidence", "blocked", "review_required", "candidate", "needs_revision"].includes(s) ? "warning" : "neutral";
    return el("span", "pill " + tone, (provider === "mock" ? "演示 · " : "") + (statusNames[s] || status || "草稿"));
  }
  function options(select, choices, selected) {
    select.replaceChildren();
    for (const [value, label] of choices) {
      const option = el("option", "", label);
      option.value = String(value);
      select.append(option);
    }
    if ([...select.options].some((o) => o.value === String(selected))) select.value = String(selected);
  }
  function setView(view, options = {}) {
    app.view = view;
    for (const name of ["editor", "workspace", "library", "experience", "models"]) $("view-" + name).classList.toggle("hidden", view !== name);
    document.body.classList.toggle("editor-mode", view === "editor");
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    if (view === "library" && !app.libraryLoaded) handled(loadLibrary)();
    window.dispatchEvent(new CustomEvent("paper-view-changed", { detail: { view, project_id: app.projectId, ...options } }));
  }
  function openRevisionSuggestions() { setView("editor", { review: false }); window.dispatchEvent(new CustomEvent("paper-open-revisions", { detail: { project_id: app.projectId } })); }
  function setMode(mode) {
    app.mode = mode;
    document.querySelectorAll("[data-mode]").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === mode);
      b.setAttribute("aria-selected", String(b.dataset.mode === mode));
    });
    $("target-role-row").classList.add("hidden");
    $("rounds-field").classList.toggle("hidden", mode === "discuss");
    $("start-run").firstElementChild.textContent = mode === "revision" ? "开始本轮改稿" : mode === "discuss" ? "开始讨论" : mode === "section" ? "开始优化" : "开始协作";
    $("task-help").textContent = mode === "discuss" ?
      "可以直接质询某一项主张、推导或实验设计；该角色的讨论会保存在当前课题中。" :
      mode === "revision" ? "以绑定的原稿为输入，按已确定的 Introduction / Method 指引定向修改并规划配图；保留改动依据和未解决问题。" :
      "将按当前课题背景组织写作；缺失数据或实验依据会明确标记，不补造结果。";
    $("task-input").placeholder = mode === "discuss" ? "写下你想讨论的问题。\n\n例如：这个创新点与已有方法的实质区别是什么？哪些前提还需要验证？" :
      mode === "section" ? "粘贴需要优化的段落，或说明本轮修改要求。\n\n可以指定中心句、公式推导、相关工作比较或机制实验设计。" :
      "描述研究方向、需要优化的段落或这一轮的目标。\n\n例如：先明确可检验的技术问题，再梳理 Introduction 与 Method 的对应关系。";
    refreshRoleOptions();
    handled(renderRoleWorkspace)();
  }
  function renderRevisionPlan() {
    const plan = app.projectDetail?.revision_plan;
    const ready = Boolean(plan?.manuscript_id && rows(plan.inputs).length);
    $("revision-binding").classList.toggle("hidden", !ready);
    $("mode-revision").disabled = !ready;
    $("mode-revision").title = ready ? "使用本课题绑定的原稿与已确定修改指引" : "此课题尚未绑定原稿和修改指引";
    if (!ready) {
      if (app.mode === "revision") setMode("full");
      return;
    }
    const manuscript = plan.inputs.find((source) => idOf(source) === plan.manuscript_id);
    $("revision-manuscript").textContent = "输入原稿：" + (manuscript?.title || plan.manuscript_id);
    const guides = $("revision-guides"); guides.replaceChildren();
    const ids = new Set(rows(plan.guide_ids));
    for (const source of plan.inputs.filter((entry) => ids.has(idOf(entry)))) guides.append(el("span", "revision-guide", source.title || idOf(source)));
    const targets = rows(plan.target_sections).map(roleName);
    $("revision-targets").textContent = "本轮范围：" + (targets.join(" · ") || "Introduction · Method · 科研绘图");
    $("revision-principle").textContent = plan.principle || "沿用已经确定的研究主线，区分原稿事实与待实现设计。";
  }
  function refreshRoleOptions() {
    const eligible = rows(app.state.roles).filter(r=>visibleRole(r)&&(app.mode !== "section" || r.kind === "writer"));
    let chosen = $("target-role").value || app.roleId;
    if (!eligible.some((r) => idOf(r) === chosen)) chosen = "";
    if (!chosen && app.mode === "discuss") chosen = idOf(eligible.find((r) => r.id === "global_teacher"));
    options($("target-role"), eligible.map((r) => [idOf(r), r.name || r.id]), chosen);
    app.roleId = $("target-role").value;
  }
  function renderRoles() {
    const roles = [...rows(app.state.roles)].filter(visibleRole).map(r=>({...r,name:roleName(r.id)})).sort((a, b) => Number(isTeacher(b)) - Number(isTeacher(a)));
    const signature = JSON.stringify(roles);
    if (signature === app.rolesSignature) return;
    app.rolesSignature = signature;
    const list = $("role-list");
    list.replaceChildren();
    let roleTabs=$("workspace-role-tabs");if(!roleTabs){roleTabs=el("nav","workspace-role-tabs");roleTabs.id="workspace-role-tabs";roleTabs.setAttribute("aria-label","协作主题");document.querySelector(".composer").before(roleTabs);}roleTabs.replaceChildren();
    const fullTab=el("button","","整篇协作");fullTab.addEventListener("click",()=>setMode("full"));roleTabs.append(fullTab);
    let writersSeen = false;
    roles.forEach((r, index) => {
      if (!isTeacher(r) && !writersSeen && roles.some(isTeacher)) {
        list.append(el("div", "teacher-separator", "章节写作与独立审阅"));
        writersSeen = true;
      }
      const b = el("button", "role-button" + (isTeacher(r) ? " teacher" : ""));
      b.dataset.roleId = idOf(r);
      b.setAttribute("aria-label", "与" + r.name + "讨论");
      const initials = isTeacher(r) ? "T" + (index + 1) : (r.name || r.id || "A").replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase() || String(index + 1).padStart(2, "0");
      const avatar = el("span", "role-avatar", initials);
      const label = el("span");
      label.append(el("strong", "", r.name || r.id));
      b.append(avatar, label, el("span", "role-indicator"));
      b.addEventListener("click", () => {
        app.roleId = idOf(r);
        $("target-role").value = app.roleId;
        setMode("discuss"); setView("workspace"); highlightRole();
        $("task-input").focus();
        $("role-workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      list.append(b);
      const roleTab=el("button","",r.name);roleTab.dataset.teamRole=r.id;roleTab.addEventListener("click",()=>b.click());roleTabs.append(roleTab);
    });
    if (!roles.length) list.append(el("p", "muted small loading-text", "暂无角色配置"));
    refreshRoleOptions();
    options($("message-filter"), [["", "全部角色"], ...roles.map((r) => [idOf(r), r.name || r.id])], $("message-filter").value);
    highlightRole();
  }
  function highlightRole() {
    document.querySelectorAll(".role-button").forEach((b) => b.classList.toggle("selected", b.dataset.roleId === app.roleId && app.mode === "discuss"));
    document.querySelectorAll("[data-team-role]").forEach(b=>b.classList.toggle("selected",b.dataset.teamRole===app.roleId&&["discuss","section"].includes(app.mode)));
  }
  let roleWorkspaceSerial=0,sourceDocument=null,roleScope="document",roleWorkspaceKey="",rolePdfController=null;
  const enrichedProjects=new Set();
  async function renderRoleWorkspace(force=false){
    const key=[app.projectId,app.roleId,app.mode,app.projectDetail?.source_hash,rows(app.projectDetail?.messages).length].join(":");
    if(!force&&key===roleWorkspaceKey)return;roleWorkspaceKey=key;const serial=++roleWorkspaceSerial;
    let panel=$("role-workspace");if(!panel){panel=el("section","role-workspace");panel.id="role-workspace";document.querySelector(".composer .mode-tabs").after(panel);}
    rolePdfController?.destroy();rolePdfController=null;panel.replaceChildren();
    const contextual=["discuss","section"].includes(app.mode);panel.classList.toggle("hidden",!contextual);
    document.querySelector(".composer .panel-title h2").textContent=contextual?roleName(app.roleId):"本轮写作";
    document.querySelector(".workspace-grid").classList.toggle("role-focused",contextual);
    if(!contextual||!app.projectId)return;
    panel.append(el("p","muted","正在读取当前稿…"));
    let fetched;try{fetched=await api("/api/source/"+encodeURIComponent(app.projectId));}catch(e){if(serial===roleWorkspaceSerial)panel.replaceChildren(el("p","",e.message));return;}
    if(serial!==roleWorkspaceSerial)return;sourceDocument=fetched;panel.replaceChildren();roleScope="document";
    const teachers=["innovation_teacher","global_teacher"],teacher=teachers.includes(app.roleId);
    if(teacher){
      const cards=el("div","contribution-map"),latest=rows(app.projectDetail?.artifacts).find(a=>a.role_id===app.roleId&&a.status==="discussion");
      let payload=latest?.payload||{};if(typeof payload==="string"){try{payload=JSON.parse(payload);}catch(_){payload={};}}
      if(app.roleId==="innovation_teacher"){
        const tex=sourceDocument.tex||"",contribution=/contributions?[\s\S]{0,400}?\\begin\{itemize\}([\s\S]*?)\\end\{itemize\}/i.exec(tex);
        const claims=contribution?contribution[1].split(/\\item\s*/).filter(s=>s.trim()).slice(0,6):[];
        for(const [i,claim] of claims.entries()){
          const card=el("article","contribution-card"),text=el("p"),original=claim.trim();
          const lead=original.match(/^[\s\S]*?[.!?](?=\s+[A-Z]|$)/)?.[0]||original;
          (window.paperSourceRender||renderTextDocument)(lead,text);
          card.append(el("small","","贡献 "+(i+1)),text);
          if(lead!==original){const detail=el("details"),full=el("div");(window.paperSourceRender||renderTextDocument)(original,full);detail.append(el("summary","","完整原文"),full);card.append(detail);}
          cards.append(card);
        }
        if(!claims.length)cards.append(el("p","","当前稿未标明贡献列表，可在下方讨论并梳理创新点。"));
        for(const issue of rows(payload.issues).slice(0,4)){const card=el("article","contribution-card evidence");card.append(el("strong","",issue.claim||"待检验主张"),el("p","",issue.evidence||"依据待补充"),el("p","",issue.action||""));cards.append(card);}
      }else{
        for(const section of rows(sourceDocument.outline).filter(s=>s.level===1)){const card=el("article","contribution-card");card.append(el("strong","",section.title));const issues=rows(sourceDocument.proposals).filter(p=>p.scope===section.id&&p.status==="pending"&&p.review_mode==="local");card.append(el("small","",issues.length+" 处待审问题"));cards.append(card);}
      }
      panel.append(cards);
    }else{
      const select=el("select","role-section-select"),patterns={introduction:/introduction/i,method:/method|framework|formulation|inference|generation|optimization/i,related_work:/related|prior work/i,experiments:/experiment|result|dataset|evaluation/i,figures:/method|experiment/i};
      const all=rows(sourceDocument.outline),parent=all.find(s=>s.level===1&&patterns[app.roleId]?.test(s.title)),eligible=parent?all.filter(s=>s.start>=parent.start&&s.end<=parent.end):patterns[app.roleId]?all.filter(s=>patterns[app.roleId].test(s.title)):all;
      const sections=eligible.length?eligible:all;
      select.setAttribute("aria-label","对照章节");for(const s of sections)select.append(new Option(s.title,s.id));
      if(!sections.length)select.append(new Option("全文","document"));
      const content=el("div","role-source-content"),heading=el("div","role-source-heading","当前原文"),tools=el("div","role-source-tools");
      const focus={introduction:"研究问题 → 现有不足 → 本文贡献",method:"建模假设 → 推导 → 机制与实现",related_work:"方法脉络 → 差异 → 引文依据",experiments:"主张 → 验证设计 → 结果依据",figures:"图示内容 → 正文对应 → 可编辑输出",reviewer:"独立审阅：主张 · 证据 · 适用边界",calibration:"引用 · 符号 · 公式与格式"};
      heading.textContent=focus[app.roleId]||"当前原文";
      const action=el("select");action.id="role-edit-action";action.setAttribute("aria-label","章节操作");
      for(const [value,label] of [["review","独立检查"],["revise","局部修改"],["discuss","讨论"]])action.append(new Option(label,value));
      const compare=()=>{
        roleScope=select.value;const s=all.find(s=>s.id===roleScope),tex=s?Array.from(sourceDocument.tex||"").slice(s.start,s.end).join(""):sourceDocument.tex||"尚未绑定当前原稿";
        const usePdf=sourceDocument.initialized&&(app.roleId==="experiments"||/\\begin\{(?:table\*?|figure\*?|tabularx?)\}/.test(tex));
        if(usePdf&&window.paperSectionPdf){
          if(rolePdfController)rolePdfController.locate(s?.title||"");
          else rolePdfController=window.paperSectionPdf.mount(content,{projectId:app.projectId,hash:sourceDocument.hash||sourceDocument.sha256,title:s?.title||"",api});
        }else{rolePdfController?.destroy();rolePdfController=null;content.classList.remove("has-section-pdf");(window.paperSourceRender||renderTextDocument)(tex,content);}
      };
      select.addEventListener("change",compare);tools.append(select,action);panel.append(heading,tools,content);compare();
    }
    const conversation=el("div","role-conversation");conversation.id="role-conversation";panel.append(conversation);
    if(teacher){for(const message of rows(app.projectDetail?.messages).filter(m=>m.role_id===app.roleId).slice(-6)){const bubble=el("article",message.sender==="user"?"author-message":"expert-message");renderTextDocument(message.content,bubble);conversation.append(bubble);}}
    else if(sourceDocument.initialized){const jobs=await api("/api/source-agent/jobs?project_id="+encodeURIComponent(app.projectId));if(serial!==roleWorkspaceSerial)return;for(const job of rows(jobs.jobs).filter(j=>j.role_id===app.roleId).slice(0,4).reverse()){const bubble=el("article","expert-message");bubble.append(el("strong","",job.message),el("p","",job.result?.explanation||job.result?.summary||statusNames[job.status]||job.status));conversation.append(bubble);}}
  }
  function renderProjects() {
    const projects = rows(app.state.projects);
    const signature = JSON.stringify(projects.map((p) => [p.id, p.name]));
    if (signature !== app.projectSignature) {
      app.projectSignature = signature;
      options($("project-select"), [["", "选择或创建课题"], ...projects.map((p) => [idOf(p), p.name])], app.projectId);
    }
    if (app.projectId && !projects.some((p) => idOf(p) === String(app.projectId))) app.projectId = "";
    if (!app.projectId && projects.length) app.projectId = idOf(projects[0]);
    $("project-select").value = app.projectId;
    app.project = projects.find((p) => idOf(p) === String(app.projectId)) || null;
    const p = app.project;
    $("header-project").textContent = p?.name || "科研写作工作台";
    $("context-name").textContent = p?.name || "还没有选择课题";
    $("context-task").textContent = p?.task || "可以从一个研究方向开始，也可以接着已有稿件进行定向修改。";
    $("context-journal").textContent = p?.target_journal || "未设定";
  }
  function renderProvider() {
    const p = app.state.provider || {};
    const offline = !p.available;
    $("provider-status").className = "pill " + (p.authenticated ? "success" : "neutral");
    $("provider-status").textContent = p.authenticated ? "GPT 已连接" : offline ? "GPT 尚未连接" : "GPT 等待登录";
    $("provider-status").title = p.error || (p.authenticated ? "已有本地登录会话，模型权限以真实调用为准。" : "请通过官方 Codex 登录后刷新。");
    $("connection-dot").className = "status-dot online";
    $("connection-text").textContent = "本地服务已连接";
    const count = app.state.library?.count;
    $("library-badge").textContent = count === undefined ? "" : String(count);
    $("library-badge").title = [count !== undefined ? count + " 份资料" : "", app.state.library?.abstract_count !== undefined ? app.state.library.abstract_count + " 张写作语料卡（搜索时检索）" : ""].filter(Boolean).join(" · ");
    if (app.state.library?.abstract_count) $("library-query").placeholder = "搜索全文资料与 " + app.state.library.abstract_count + " 张写作语料卡";
    const tracks = app.state.library?.tracks || {};
    $("library-track-counts").textContent = [["writing", "写作"], ["domain", "领域"], ["project", "稿件/指引"]].filter(([key]) => tracks[key] !== undefined).map(([key, label]) => label + " " + tracks[key]).join(" · ");
    const mock = $("provider-select").value === "mock";
    $("run-estimate").textContent = mock ? "离线演示只验证工作流，不产出真实研究结论。" : "仅点击运行后才会调用模型。";
  }
  function renderRuns() {
    const all = rows(app.projectDetail?.runs).length ? app.projectDetail.runs : rows(app.state.runs);
    const runs = all.filter((r) => !r.project_id || String(r.project_id) === String(app.projectId));
    $("run-count").textContent = runs.length ? runs.length + " 次记录" : "";
    const list = $("run-list"); list.replaceChildren();
    if (!runs.length) {
      list.append(el("div", "empty compact", app.projectId ? "还没有运行记录。从一个清楚的写作目标开始。" : "创建课题后，协作记录会保存在这里。")); return;
    }
    for (const r of runs.slice(0, 15)) {
      const b = el("button", "run-row" + (String(app.selectedRun) === idOf(r) ? " selected" : ""));
      const info = el("span");
      info.append(el("strong", "", r.request || modeNames[r.mode] || "写作任务"),
        el("small", "", [modeNames[r.mode] || r.mode, fmtDate(r.created_at), r.current_role ? roleName(r.current_role) : ""].filter(Boolean).join(" · ")));
      b.append(info, statusPill(r.status, r.provider));
      if (r.status === "needs_revision") { b.querySelector(".pill").title = "查看工作稿中的修改建议"; b.querySelector(".pill").classList.add("revision-link"); }
      b.addEventListener("click", handled(async (event) => { const openSuggestions = r.status === "needs_revision" && Boolean(event.target.closest(".pill")); app.selectedRun = idOf(r); await loadRun(); renderRuns(); if (openSuggestions) openRevisionSuggestions(); }));
      list.append(b);
    }
    if (!app.selectedRun) app.selectedRun = idOf(runs[0]);
  }
  function renderLive() {
    const data = app.runDetail;
    const r = data?.run || data;
    if (!r || !r.id) return;
    const status = String(r.status || "").toLowerCase();
    const p = status === "needs_revision" ? el("button", "pill warning revision-link", "需要修订 · 查看建议 ↗") : statusPill(r.status, r.provider);
    if (status === "needs_revision") { p.type = "button"; p.addEventListener("click", openRevisionSuggestions); }
    p.id = "live-status"; $("live-status").replaceWith(p);
    const target = $("live-content"); target.replaceChildren();
    target.append(el("p", "live-request", short(r.request || modeNames[r.mode] || "协作任务", 170)));
    const detail = [r.current_role ? roleName(r.current_role) : "", r.round ? "第 " + r.round + " 轮" : "", fmtDate(r.created_at)].filter(Boolean).join(" · ");
    target.append(el("p", "live-meta", detail));
    const events = rows(data.events || r.events);
    if (events.length) {
      const track = el("div", "live-events");
      events.slice(-10).forEach((e) => {
        const item = el("div", "live-event");
        item.append(el("span", "", typeof e === "string" ? e : e.message || e.detail || e.type || e.event || textOf(e)));
        if (e.created_at || e.timestamp) item.append(el("time", "", fmtDate(e.created_at || e.timestamp)));
        track.append(item);
      });
      target.append(track);
    }
    const errors = rows(data.errors || r.errors);
    if (r.error && !errors.length) errors.push(r.error);
    if (errors.length) target.append(el("p", "live-error", errors.map(textOf).join("\n")));
    target.append(el("div", "live-id", "RUN · " + r.id));
    if (status === "needs_revision") { const review = el("button", "button secondary small-button", "查看文稿中的修改建议 ↗"); review.addEventListener("click", openRevisionSuggestions); target.append(review); }
    $("cancel-run").classList.toggle("hidden", !activeStatuses.has(status));
    $("cancel-run").disabled = status === "cancelling";
  }
  function renderArtifacts() {
    const discussionStatuses = new Set(["mock", "discussion", "discussion_mock", "memory_review", "memory_review_mock", "consultation", "consultation_mock"]);
    const artifacts = [...rows(app.projectDetail?.artifacts)].filter((a) => !discussionStatuses.has(a.status)).sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || "")) || Number(b.revision || 0) - Number(a.revision || 0));
    const list = $("artifact-list"); list.replaceChildren();
    if (!artifacts.length) {
      list.append(el("div", "empty compact", "尚无文稿版本"));
      app.selectedArtifact = null; app.artifact = null;
      $("document-toolbar").classList.add("hidden");
      $("document-content").replaceChildren(el("div", "empty document-empty", "运行完成后，在左侧选择章节或教师反馈。"));
    }
    for (const a of artifacts) {
      const b = el("button", "artifact-card" + (String(app.selectedArtifact) === idOf(a) ? " active" : ""));
      b.append(el("strong", "", a.title || roleName(a.role_id)),
        el("small", "", [a.revision ? "版本 " + a.revision : "", fmtDate(a.created_at)].filter(Boolean).join(" · ")),
        el("span", "artifact-card-type", [a.role_id ? roleName(a.role_id) : "", statusNames[a.status] || a.status].filter(Boolean).join(" / ")));
      b.addEventListener("click", handled(async () => {
        app.selectedArtifact = idOf(a);
        app.artifact = a.content !== undefined ? a : await api("/api/artifacts/" + encodeURIComponent(idOf(a)));
        if (app.artifact.artifact) app.artifact = app.artifact.artifact;
        renderArtifacts(); renderDocument();
      }));
      list.append(b);
    }
    if (!app.selectedArtifact && artifacts.length) {
      const a = artifacts[0];
      app.selectedArtifact = idOf(a);
      app.artifact = a;
      if (a.content === undefined) handled(async () => {
        const found = await api("/api/artifacts/" + encodeURIComponent(idOf(a)));
        if (app.selectedArtifact === idOf(a)) { app.artifact = found.artifact || found; renderDocument(); }
      })();
      renderArtifacts(); renderDocument();
    }
  }
  // KaTeX receives only text. Trust stays disabled, including inside source-derived formulas.
  function renderMath(target) {
    if (!window.katex || !window.renderMathInElement) return;
    const mathOptions = { trust: false, throwOnError: false, strict: "ignore", maxSize: 20, maxExpand: 1000, errorColor: "#93725a", output: "htmlAndMathml" };
    target.querySelectorAll("pre.equation").forEach((block) => {
      const display = el("div", "math-block");
      window.katex.render(block.textContent, display, { ...mathOptions, displayMode: true });
      block.replaceWith(display);
    });
    window.renderMathInElement(target, {
      ...mathOptions,
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false }
      ],
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"],
      ignoredClasses: ["katex", "math-block"]
    });
  }
  // Model and source text is rendered as DOM text, never executable HTML.
  function renderTextDocument(text, target) {
    target.replaceChildren();
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    let paragraph = [], list = null, code = null, codeType = "";
    const flush = () => {
      if (paragraph.length) target.append(el("p", "", paragraph.join("\n")));
      paragraph = []; list = null;
    };
    function tableCells(line) {
      const source = line.trim().replace(/^\|/, "").replace(/\|$/, ""), cells = [];
      let cell = "", math = "", inlineCode = false;
      for (let i = 0; i < source.length; i++) {
        const c = source[i], next = source[i + 1];
        if (c === "\\" && next === "(" && !math) { math = "paren"; cell += c + next; i++; continue; }
        if (c === "\\" && next === ")" && math === "paren") { math = ""; cell += c + next; i++; continue; }
        if (c === "$" && source[i - 1] !== "\\" && math !== "paren") math = math === "dollar" ? "" : "dollar";
        if (c.charCodeAt(0) === 96 && !math) inlineCode = !inlineCode;
        if (c === "|" && source[i - 1] !== "\\" && !math && !inlineCode) { cells.push(cell.trim()); cell = ""; }
        else cell += c;
      }
      cells.push(cell.trim()); return cells;
    }
    for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
      const line = lines[lineIndex];
      if (code === null && /^\s*\|/.test(line) && lines[lineIndex + 1] && tableCells(lines[lineIndex + 1]).every((cell) => /^:?-{3,}:?$/.test(cell))) {
        flush();
        const wrap = el("div", "markdown-table-wrap"), table = el("table"), head = el("thead"), header = el("tr"), body = el("tbody");
        tableCells(line).forEach((cell) => header.append(el("th", "", cell)));
        head.append(header); table.append(head); lineIndex += 2;
        for (; lineIndex < lines.length && /^\s*\|/.test(lines[lineIndex]); lineIndex++) {
          const row = el("tr"); tableCells(lines[lineIndex]).forEach((cell) => row.append(el("td", "", cell))); body.append(row);
        }
        lineIndex--; table.append(body); wrap.append(table); target.append(wrap); continue;
      }
      if (/^\s*\x60{3}/.test(line)) {
        flush();
        if (code !== null) { target.append(el("pre", "", code.join("\n"))); code = null; } else { code = []; codeType = "code"; }
        continue;
      }
      if (/^\s*(\$\$|\\\[)\s*$/.test(line) || /^\s*\\\]\s*$/.test(line)) {
        flush();
        if (code !== null && codeType === "math") { target.append(el("pre", "equation", code.join("\n"))); code = null; }
        else if (code === null) { code = []; codeType = "math"; }
        else code.push(line);
        continue;
      }
      if (code !== null) { code.push(line); continue; }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
      const ordered = /^\s*\d+[.)]\s+(.+)$/.exec(line);
      if (heading) { flush(); target.append(el("h" + Math.min(heading[1].length, 4), "", heading[2])); }
      else if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); target.append(el("hr")); }
      else if (/^>\s?/.test(line)) { flush(); target.append(el("blockquote", "", line.replace(/^>\s?/, ""))); }
      else if (bullet || ordered) {
        if (paragraph.length) flush();
        const tag = bullet ? "UL" : "OL";
        if (!list || list.tagName !== tag) { list = el(tag.toLowerCase()); target.append(list); }
        list.append(el("li", "", (bullet || ordered)[1]));
      } else if (!line.trim()) flush();
      else { list = null; paragraph.push(line); }
    }
    flush();
    if (code !== null) target.append(el("pre", codeType === "math" ? "equation" : "", code.join("\n")));
    renderMath(target);
  }
  function renderDocument() {
    const a = app.artifact;
    $("document-toolbar").classList.toggle("hidden", !a);
    if (!a) return;
    $("document-label").textContent = [a.title || roleName(a.role_id), a.revision ? "版本 " + a.revision : ""].filter(Boolean).join(" · ");
    renderTextDocument(a.content || a.text || "当前版本尚无可显示的正文。", $("document-content"));
    renderFigureAssets(a, $("document-content"));
  }
  function figureFileUrl(value, extension) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value, window.location.origin);
      const pattern = /^\/api\/figure-files\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+\.(png|svg|drawio|pdf)$/i;
      if (url.origin !== window.location.origin || url.search || url.hash || !pattern.test(url.pathname)) return null;
      if (!url.pathname.toLowerCase().endsWith("." + extension)) return null;
      return url.href;
    } catch (_) { return null; }
  }
  function renderFigureAssets(artifact, target) {
    const figures = rows(artifact.payload?.rendered_figures || artifact.rendered_figures);
    if (!figures.length) return;
    const section = el("section", "rendered-figures");
    section.append(el("h2", "", "配图预览与可编辑文件"), el("p", "field-help", "预览来自本地已保存文件。图中机制仍需与正文、实现和证据逐项核对。"));
    for (const figure of figures) {
      const card = el("figure", "rendered-figure");
      const heading = el("figcaption", "figure-caption");
      heading.append(el("strong", "", figure.title || figure.id || "科研配图"), el("span", "figure-status", figure.status || "图稿"));
      card.append(heading);
      const png = figureFileUrl(figure.files?.png, "png");
      if (png) {
        const img = el("img", "figure-preview"); img.src = png;
        img.alt = figure.title || "科研配图预览"; img.loading = "lazy"; card.append(img);
      }
      const downloads = el("div", "figure-downloads");
      for (const format of ["svg", "drawio", "pdf", "png"]) {
        const url = figureFileUrl(figure.files?.[format], format);
        if (!url) continue;
        const link = el("a", "button secondary small-button", format === "drawio" ? "下载 Draw.io 源文件" : "下载 " + format.toUpperCase());
        link.href = url; link.download = ""; link.rel = "noopener"; downloads.append(link);
      }
      card.append(downloads);
      if (rows(figure.warnings).length) card.append(el("p", "figure-warnings", figure.warnings.map(textOf).join("\n")));
      section.append(card);
    }
    target.append(section);
  }
  function renderMessages() {
    const filter = $("message-filter").value;
    const messages = rows(app.projectDetail?.messages).filter((m) => !filter || String(m.role_id) === filter);
    const target = $("messages-list"); target.replaceChildren();
    if (!messages.length) { target.append(el("div", "empty", "尚无讨论记录。在左侧选择一位角色，开始讨论。")); return; }
    for (const m of messages) {
      const isUser = ["user", "human", "operator"].includes(m.sender || m.role);
      const item = el("article", "message" + (isUser ? " user" : ""));
      const heading = el("div", "message-heading");
      heading.append(el("strong", "", isUser ? "你 → " + roleName(m.role_id) : roleName(m.role_id)), el("time", "", fmtDate(m.created_at)));
      const body = el("div", "message-body", m.content || m.message || "");
      renderMath(body); item.append(heading, body);
      target.append(item);
    }
  }
  function setOutput(name) {
    app.output = name;
    $("artifacts-pane").classList.toggle("hidden", name !== "artifacts");
    $("messages-pane").classList.toggle("hidden", name !== "messages");
    document.querySelectorAll("[data-output]").forEach((b) => b.classList.toggle("active", b.dataset.output === name));
  }
  function renderSkills() {
    const target = $("skills-list"); target.replaceChildren();
    const skills = rows(app.state.skills);
    for (const s of skills) {
      const card = el("div", "skill-card");
      card.append(el("strong", "", typeof s === "string" ? s : s.name || s.id),
        el("p", "", s.description || s.summary || ""),
        el("code", "", s.path || ""));
      target.append(card);
    }
    if (!skills.length) target.append(el("div", "empty compact", "暂无 Skill 清单"));
  }
  function renderExperiences() {
    const target = $("experience-list"); target.replaceChildren();
    const experiences = rows(app.state.experiences);
    $("experience-count").textContent = experiences.length + " 条记录";
    if (!experiences.length) { target.append(el("div", "empty", "经验将在写作和审阅后逐步积累。这里不会把一次自评提升当作普遍能力提升。")); return; }
    for (const x of experiences) {
      const item = el("article", "experience-item"), body = el("div");
      body.append(el("h3", "", x.title || x.rule || x.lesson || x.summary || "写作经验"),
        el("p", "", textOf(x.content || x.description || x.evidence || x.reason || "")), statusPill(x.status || "candidate"));
      if (x.project_id || x.run_id) body.append(el("p", "small muted", [x.project_id ? "课题：" + x.project_id : "", x.run_id ? "运行：" + x.run_id : ""].filter(Boolean).join(" · ")));
      item.append(el("time", "", fmtDate(x.created_at)), body); target.append(item);
    }
  }
  function renderModels() {
    const signature = JSON.stringify([app.state.roles, app.state.provider?.models]);
    if (signature === app.modelSignature) return;
    app.modelSignature = signature;
    const target = $("models-list"); target.replaceChildren();
    const models = rows(app.state.provider?.models).map((m) => typeof m === "string" ? { id: m, display_name: m } : m);
    for (const r of rows(app.state.roles).filter(visibleRole)) {
      const card = el("section", "model-card" + (isTeacher(r) ? " teacher" : ""));
      const head = el("div", "model-card-head");
      const title = el("div");
      title.append(el("h2", "", r.name || r.id), el("p", "", kindNames[r.kind] || r.kind || "写作角色"));
      head.append(el("span", "role-avatar", isTeacher(r) ? "T" : "A"), title);
      card.append(head, el("p", "role-description", r.description || ""));
      const modelField = el("div", "field"), modelLabel = el("label", "", "模型"), modelSelect = el("select");
      modelSelect.id = "model-" + idOf(r); modelLabel.htmlFor = modelSelect.id;
      const choices = models.map((m) => [m.id || m.model, m.display_name || m.displayName || m.id || m.model]);
      if (!choices.some(([v]) => v === r.model)) choices.unshift([r.model || "", (r.model || "未配置") + (r.model ? " · 当前配置" : "")]);
      options(modelSelect, choices, r.model);
      modelField.append(modelLabel, modelSelect); card.append(modelField);
      const effortField = el("div", "field"), effortLabel = el("label", "", "思考强度"), effortSelect = el("select");
      effortSelect.id = "effort-" + idOf(r); effortLabel.htmlFor = effortSelect.id;
      const updateEfforts = () => {
        const m = models.find((entry) => (entry.id || entry.model) === modelSelect.value);
        const found = m?.supported_efforts || m?.supportedReasoningEfforts;
        const efforts = rows(found).map((e) => typeof e === "string" ? e : e.reasoningEffort || e.reasoning_effort || e.effort).filter(Boolean);
        const current = effortSelect.value || r.effort || "high";
        const labels = { minimal: "极简", low: "低", medium: "中", high: "高", xhigh: "更高", max: "最大", ultra: "最高" };
        const offered = efforts.length ? efforts : ["low", "medium", "high", "xhigh"];
        if (!offered.includes(current)) offered.push(current);
        options(effortSelect, offered.map((e) => [e, (labels[e] || e) + " · " + e]), current);
      };
      updateEfforts(); modelSelect.addEventListener("change", updateEfforts);
      effortField.append(effortLabel, effortSelect); card.append(effortField);
      const footer = el("div", "model-card-footer"), save = el("button", "button secondary", "保存配置");
      footer.append(el("span", "small muted", "不自动替换失败模型"), save); card.append(footer);
      save.addEventListener("click", handled(async () => {
        save.disabled = true;
        try { await api("/api/roles/" + encodeURIComponent(idOf(r)), { model: modelSelect.value, effort: effortSelect.value }); toast("角色配置已保存"); await refresh(); }
        finally { save.disabled = false; }
      }));
      target.append(card);
    }
  }
  function renderLibrary() {
    const target = $("library-list"); target.replaceChildren();
    const papers = app.libraryPapers;
    $("library-count").textContent = papers.length + " 项资料";
    if (!papers.length) { target.append(el("div", "empty", "没有找到符合条件的资料。可以尝试其他关键词，或导入一份本地文件。")); return; }
    const scoped = new Set(rows(app.projectDetail?.project?.source_ids || app.projectDetail?.source_ids || app.project?.source_ids).map(String));
    for (const paper of papers) {
      const card = el("article", "library-card");
      const kind = { exemplar: "写作范本", reference: "参考文献", manuscript: "当前稿件", guide: "写作指南", guidance: "修改指引", corpus: "语料卡", abstract_card: "摘要卡" }[paper.kind] || paper.kind || "研究资料";
      card.append(el("span", "source-type", kind), el("h3", "", paper.title || paper.name || paper.filename || paper.id));
      const authors = Array.isArray(paper.authors) ? paper.authors.join(", ") : paper.authors || "";
      if (paper.venue || paper.year) card.append(el("p", "source-venue", [paper.venue, paper.year].filter(Boolean).join(" · ")));
      if (authors) card.append(el("p", "paper-authors", short(authors, 160)));
      if (paper.award?.name) card.append(el("p", "paper-award", [paper.award.year, paper.award.name, paper.award.status].filter(Boolean).join(" · ")));
      if (paper.description || paper.summary) card.append(el("p", "", short(paper.description || paper.summary, 240)));
      if (paper.writing_use) card.append(el("p", "paper-use", short(paper.writing_use, 200)));
      const reading = paper.read_scope || paper.reading_scope;
      if (reading || paper.reading_level) {
        const details = el("details", "source-reading");
        details.append(el("summary", "", paper.reading_level === "abstract_only" ? "仅摘要 · 查看阅读边界" : paper.reading_level === "metadata_only" ? "仅文献元数据" : "查看阅读范围与版本"));
        if (reading) details.append(el("p", "", textOf(reading)));
        if (paper.pdf_version) details.append(el("p", "", paper.pdf_version));
        card.append(details);
      }
      const links = el("div", "source-links");
      for (const [value, label] of [[paper.url || paper.source_url, "论文来源 ↗"], [paper.pdf_source_url, "公开 PDF ↗"], [paper.award?.evidence_url, "官方奖项依据 ↗"]]) {
        try {
          const url = new URL(value);
          if (url.protocol !== "https:") continue;
          const link = el("a", "", label); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; links.append(link);
        } catch (_) { /* Missing or unsafe source link remains text-only. */ }
      }
      if (links.childElementCount) card.append(links);
      if (paper.pages || paper.sha256) card.append(el("p", "paper-stats", [paper.pages ? paper.pages + " 页" : "", paper.sha256 ? "SHA-256 " + paper.sha256.slice(0, 10) + "…" : ""].filter(Boolean).join(" · ")));
      card.append(el("p", "paper-path", paper.path || paper.relative_path || paper.source_path || ""));
      const scopeLabel = el("label", "source-checkbox"), checkbox = el("input");
      checkbox.type = "checkbox"; checkbox.checked = scoped.has(idOf(paper)); checkbox.disabled = !app.projectId;
      checkbox.setAttribute("aria-label", "将" + (paper.title || paper.id) + "加入当前课题");
      scopeLabel.append(checkbox, el("span", "", app.projectId ? "用于当前课题" : "选择课题后可关联"));
      checkbox.addEventListener("change", handled(async () => {
        checkbox.disabled = true;
        const ids = new Set(rows(app.projectDetail?.project?.source_ids || app.projectDetail?.source_ids || app.project?.source_ids).map(String));
        if (checkbox.checked) ids.add(idOf(paper)); else ids.delete(idOf(paper));
        try {
          await api("/api/projects/" + encodeURIComponent(app.projectId) + "/sources", { source_ids: [...ids] });
          await loadProject(); toast(checkbox.checked ? "已关联到当前课题" : "已取消课题关联");
        } catch (error) { checkbox.checked = !checkbox.checked; throw error; }
        finally { checkbox.disabled = false; }
      }));
      card.append(scopeLabel); target.append(card);
    }
  }
  async function loadLibrary() {
    const generation = ++app.libraryGeneration;
    const data = await api("/api/library?q=" + encodeURIComponent($("library-query").value.trim()) + "&track=" + encodeURIComponent($("library-track").value)+"&project_id="+encodeURIComponent(app.projectId));
    if (generation !== app.libraryGeneration) return;
    app.libraryPapers = rows(data.papers || data.items || data);
    app.libraryLoaded = true; renderLibrary(); renderSkills();
  }
  async function loadProject() {
    const id = app.projectId, generation = ++app.detailGeneration;
    if (!id) { app.projectDetail = null; renderRevisionPlan(); renderRuns(); renderArtifacts(); renderMessages(); return; }
    const detail = await api("/api/projects/" + encodeURIComponent(id));
    if (generation !== app.detailGeneration || app.projectId !== id) return;
    app.projectDetail = detail;
    if(!enrichedProjects.has(id)){enrichedProjects.add(id);api("/api/library/enrich",{project_id:id}).then(()=>{app.libraryLoaded=false;}).catch(()=>enrichedProjects.delete(id));}
    if (detail.project) app.project = detail.project;
    renderRevisionPlan(); renderRuns(); renderArtifacts(); renderMessages();
    window.dispatchEvent(new CustomEvent("paper-project-changed", { detail: { project_id: id, project: app.project, revision_plan: detail.revision_plan } }));
    if (app.view === "library") renderLibrary();
    if(app.view==="workspace")await renderRoleWorkspace();
  }
  async function loadRun() {
    if (!app.selectedRun) return;
    const id = app.selectedRun;
    const found = await api("/api/runs/" + encodeURIComponent(id));
    if (app.selectedRun !== id) return;
    app.runDetail = found; renderLive();
  }
  async function refresh() {
    if (app.refreshing) return;
    app.refreshing = true;
    try {
      app.state = await api("/api/state");
      renderProvider(); renderRoles(); renderProjects(); renderModels(); renderSkills(); renderExperiences();
      await loadProject();
      await loadRun();
    } catch (error) {
      $("connection-dot").className = "status-dot error"; $("connection-text").textContent = "本地连接中断";
      throw error;
    } finally { app.refreshing = false; }
  }
  let pendingProjectZip=null,uploadingProject=false;
  const uploadStatus=text=>{$("project-upload-status").textContent=text;document.querySelectorAll('[data-upload-project]').forEach(b=>{b.disabled=uploadingProject;b.textContent=uploadingProject?text:(b.closest('.ol-menubar')?'上传 ZIP':'上传 ZIP 创建课题');});};
  document.addEventListener('click',event=>{if(event.target.closest('[data-upload-project]')&&!uploadingProject){$("project-zip-file").value='';$("project-zip-file").click();}});
  $("project-import-cancel").addEventListener('click',()=>$("project-import-dialog").close());
  async function uploadProjectZip(entry){
    if(!pendingProjectZip||uploadingProject)return;
    if(pendingProjectZip.size>64*1024*1024)throw Error('ZIP 最大支持 64 MB');
    if(window.paperEditorBeforeProjectChange)await window.paperEditorBeforeProjectChange();
    uploadingProject=true;uploadStatus('正在上传并解压…');
    try{
      const params=new URLSearchParams({name:pendingProjectZip.name});if(entry)params.set('entry',entry);
      const response=await fetch('/api/projects/upload-zip?'+params,{method:'POST',headers:{'Content-Type':'application/zip'},body:pendingProjectZip});
      const data=await response.json();if(!response.ok)throw Error(data.error||'ZIP 导入失败');
      if(data.requires_entry){
        options($("project-entry-select"),data.entries.map(p=>[p,p]));$("project-upload-entry").classList.remove('hidden');
        if(!$("project-import-dialog").open)$("project-import-dialog").showModal();return;
      }
      $("project-upload-entry").classList.add('hidden');if($("project-import-dialog").open)$("project-import-dialog").close();pendingProjectZip=null;$("project-zip-file").value='';
      await refresh();await chooseProject(data.project.id);setView('editor');toast('ZIP 已解压，工程已导入');
      if(data.preview_error)notice('工程已导入，PDF 暂未编译：'+data.preview_error);
    }finally{uploadingProject=false;uploadStatus('');}
  }
  $("project-zip-file").addEventListener('change',handled(async()=>{pendingProjectZip=$("project-zip-file").files[0]||null;await uploadProjectZip();}));
  $("project-entry-confirm").addEventListener('click',handled(()=>uploadProjectZip($("project-entry-select").value)));
  async function chooseProject(id) {
    if (window.paperEditorBeforeProjectChange && String(id) !== app.projectId) await window.paperEditorBeforeProjectChange();
    if (String(id) !== app.revisionRequestProject && $("task-input").value === app.loadedRevisionRequest) $("task-input").value = "";
    app.projectId = String(id); localStorage.setItem("paper-agent-project", app.projectId);
    app.selectedArtifact = null; app.artifact = null; app.selectedRun = null; app.runDetail = null;
    app.projectDetail = null;
    roleWorkspaceKey="";app.libraryLoaded=false;
    $("document-toolbar").classList.add("hidden");
    $("document-content").replaceChildren(el("div", "empty document-empty", "选择文稿版本以阅读正文。"));
    $("live-content").replaceChildren(el("div", "empty compact", "等待新的写作任务"));
    $("cancel-run").classList.add("hidden");
    const p = statusPill("draft"); p.id = "live-status"; p.textContent = "等待任务"; $("live-status").replaceWith(p);
    renderProjects(); await loadProject(); await loadRun();
  }
  async function startRun() {
    if (app.busy) return;
    if (!app.projectId) { $("project-dialog").showModal(); return; }
    const request = $("task-input").value.trim() || (app.mode !== "discuss" ? app.project?.task || "" : "");
    if (!request) { $("task-input").focus(); throw new Error(app.mode === "discuss" ? "请先写下要讨论的问题。" : "请描述这一轮要推进的研究或写作目标。"); }
    const provider = $("provider-select").value;
    if (provider === "codex" && !app.state.provider?.authenticated) throw new Error("GPT 尚未连接。请通过官方 Codex 完成登录后刷新；也可以选择离线演示验证流程。");
    const roleId = $("target-role").value;
    if (["section", "discuss"].includes(app.mode) && !roleId) throw new Error("请先选择一个写作或讨论角色。");
    if (app.mode === "revision" && !app.projectDetail?.revision_plan?.manuscript_id) throw new Error("当前课题尚未绑定修改原稿和指引。");
    app.busy = true; $("start-run").disabled = true;
    try {
      if(["section","discuss"].includes(app.mode)&&!isTeacher(role(roleId)||{})&&sourceDocument?.initialized){
        const job=await api("/api/source-agent/jobs",{project_id:app.projectId,role_id:roleId,scope:roleScope,action:$("role-edit-action")?.value||"review",message:request,base_hash:sourceDocument.hash,conversation_id:"workspace_"+roleId});
        const pid=app.projectId,bubble=el("article","expert-message","专家正在对照当前原文处理…");$("role-conversation")?.append(bubble);
        const poll=async()=>{const current=await api("/api/source-agent/jobs/"+encodeURIComponent(job.id));if(pid!==app.projectId)return;if(["queued","running"].includes(current.status)){setTimeout(()=>handled(poll)(),1000);return;}bubble.textContent=current.result?.explanation||current.result?.summary||current.error||current.status;await renderRoleWorkspace(true);};
        handled(poll)();$("task-input").value="";return;
      }
      const data = app.mode === "discuss" ?
        await api("/api/chat", { project_id: app.projectId, role_id: roleId, message: request, provider }) :
        await api("/api/runs", { project_id: app.projectId, mode: app.mode, request, role_id: app.mode === "revision" ? "global_teacher" : roleId, rounds: Number($("rounds-select").value), provider });
      const run = data.run || data;
      app.selectedRun = idOf(run) || String(data.run_id || "");
      if (!app.selectedRun) throw new Error("已提交请求，但服务未返回运行编号。请刷新检查。");
      if (app.mode === "discuss") setOutput("messages");
      toast(provider === "mock" ? "离线演示已启动" : "写作任务已提交");
      await refresh();
    } finally { app.busy = false; $("start-run").disabled = false; }
  }
  function openProject() { $("project-dialog").showModal(); setTimeout(() => $("project-name").focus(), 0); }
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); highlightRole(); }));
  document.querySelectorAll("[data-output]").forEach((b) => b.addEventListener("click", () => setOutput(b.dataset.output)));
  $("target-role").addEventListener("change", () => { app.roleId = $("target-role").value; highlightRole(); });
  $("project-select").addEventListener("change", handled(() => chooseProject($("project-select").value)));
  $("provider-select").addEventListener("change", renderProvider);
  $("message-filter").addEventListener("change", renderMessages);
  $("start-run").addEventListener("click", handled(startRun));
  $("load-revision-task").addEventListener("click", () => {
    const plan = app.projectDetail?.revision_plan;
    if (!plan?.default_request) return;
    setView("workspace"); setMode("revision");
    $("task-input").value = plan.default_request; app.loadedRevisionRequest = plan.default_request; app.revisionRequestProject = app.projectId; $("task-input").focus();
    toast("已载入原稿与既定方案。点击开始后才会调用模型。");
  });
  $("refresh").addEventListener("click", handled(async () => {
    $("refresh").disabled = true;
    try { await api("/api/provider/refresh", {}); await refresh(); toast("已刷新连接与工作台"); }
    finally { $("refresh").disabled = false; }
  }));
  $("notice-close").addEventListener("click", () => $("notice").classList.add("hidden"));
  $("new-project").addEventListener("click", openProject); $("show-new-project").addEventListener("click", openProject);
  $("close-project").addEventListener("click", () => $("project-dialog").close()); $("cancel-project").addEventListener("click", () => $("project-dialog").close());
  $("project-form").addEventListener("submit", handled(async (event) => {
    event.preventDefault();
    const name = $("project-name").value.trim();
    if (!name) return;
    if (window.paperEditorBeforeProjectChange) await window.paperEditorBeforeProjectChange();
    $("create-project").disabled = true;
    try {
      const data = await api("/api/projects", { name, task: $("project-task").value.trim(), target_journal: $("project-journal").value.trim() });
      const p = data.project || data;
      app.projectId = idOf(p); localStorage.setItem("paper-agent-project", app.projectId);
      app.projectSignature = ""; app.selectedRun = null; app.runDetail = null; app.selectedArtifact = null; app.artifact = null;
      $("project-dialog").close(); $("project-form").reset(); toast("课题已创建");
      await refresh(); await chooseProject(idOf(p)); setView("workspace");
    } finally { $("create-project").disabled = false; }
  }));
  $("cancel-run").addEventListener("click", handled(async () => {
    if (!app.selectedRun) return;
    $("cancel-run").disabled = true;
    try { await api("/api/runs/" + encodeURIComponent(app.selectedRun) + "/cancel", {}); toast("已请求停止当前运行"); await loadRun(); }
    finally { $("cancel-run").disabled = false; }
  }));
  $("copy-artifact").addEventListener("click", handled(async () => {
    const text = app.artifact?.content || app.artifact?.text || "";
    if (!text) return;
    if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不支持自动复制，可选中正文手动复制或下载文件。");
    await navigator.clipboard.writeText(text); toast("正文已复制");
  }));
  $("download-artifact").addEventListener("click", () => {
    if (!app.artifact) return;
    const a = document.createElement("a"), content = app.artifact.content || app.artifact.text || "";
    a.href = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    a.download = ((app.artifact.title || roleName(app.artifact.role_id) || "manuscript") + "-v" + (app.artifact.revision || 1)).replace(/[\\/:*?"<>|]/g, "_") + ".md";
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  $("search-library").addEventListener("click", handled(loadLibrary));
  $("library-track").addEventListener("change", handled(loadLibrary));
  $("library-query").addEventListener("keydown", (e) => { if (e.key === "Enter") handled(loadLibrary)(); });
  $("import-form").addEventListener("submit", handled(async (event) => {
    event.preventDefault(); const path = $("import-path").value.trim(); if (!path) return;
    $("import-source").disabled = true;
    try {
      await api("/api/library/import", { path, kind: $("import-kind").value });
      $("import-path").value = ""; toast("资料已导入，原文件保留"); await refresh(); await loadLibrary();
    } finally { $("import-source").disabled = false; }
  }));
  $("task-input").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") handled(startRun)(); });
  handled(refresh)();
  document.body.classList.add("editor-mode");
  setInterval(() => {
    if (!document.hidden && !app.busy) refresh().catch((error) => {
      $("connection-dot").className = "status-dot error";
      $("connection-text").textContent = "连接中断，正在重试";
      $("connection-text").title = error.message;
    });
  }, 5000);
})();
