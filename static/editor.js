"use strict";

(() => {
  // The source-review workspace owns writing, saving, and review. The legacy
  // block editor stays available for older clients, but must not create a
  // second manuscript authority or start hidden automatic reviews here.
  if (document.body?.dataset.writingEditor === "source-review") return;
  const $ = (id) => document.getElementById(id);
  const list = (v) => Array.isArray(v) ? v : [];
  const state = { projectId: "", project: null, doc: null, latest: null, sectionId: "", blockId: "", entries: new Map(), jobs: new Map(),
    changed: new Map(), requested: new Set(), loading: false, loadSerial: 0, source: null, suggestion: null, suggestionSerial: 0, suggestionId: "", visible: true, commandBusy: false, lastChange: null };
  const activeJobs = new Set(["queued", "running", "pending", "syncing", "reviewing", "revising"]);
  const pendingSuggestions = new Set(["pending", "proposed", "open", "candidate", "needs_review"]);
  const statusLabels = { queued: "排队中", pending: "等待中", running: "处理中", reviewing: "专家检查中", revising: "专家修改中", syncing: "关联内容同步中", completed: "已完成", failed: "处理失败", cancelled: "已停止", interrupted: "已中断", needs_evidence: "需要补充依据", needs_revision: "建议待处理", needs_review: "有修改建议", stale: "已因版本变化过期", accepted: "已采纳", rejected: "未采纳" };
  function expertLabel(value) { const names = { introduction: "Introduction 专家", method: "Method 专家", related_work: "相关工作专家", experiments: "实验与机制专家", figures: "科研绘图专家", calibration: "格式与证据校准", reviewer: "独立审稿", innovation_teacher: "创新点教师", global_teacher: "全局教师" }; if (typeof value === "string") return names[value] || value; return [names[value?.role_id || value?.role] || value?.name || value?.role_id || value?.role, value?.model].filter(Boolean).join(" · "); }
  function node(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = String(text); return e; }
  function notice(text, error = false) { const box = $("editor-notice"); box.textContent = text; box.classList.toggle("hidden", !text); box.classList.toggle("error", error); }
  function saveStatus(text, error = false) { $("editor-save-status").textContent = text; $("editor-save-status").classList.toggle("editor-save-error", error); }
  function guarded(fn) { return (...args) => Promise.resolve().then(() => fn(...args)).catch((e) => notice(e.message || String(e), true)); }
  async function api(path, body) {
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 60000);
    try {
      const response = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: abort.signal });
      let data; try { data = await response.json(); } catch (_) { throw new Error("服务器没有返回可识别的文稿数据。"); }
      if (!response.ok) { const error = new Error(data.error || data.message || "文稿请求失败"); error.status = response.status; error.data = data; throw error; }
      return data;
    } catch (error) { if (error.name === "AbortError") throw new Error("本地服务响应超时，请检查连接后重试；保存请求可能已在服务器完成。"); throw error; }
    finally { clearTimeout(timer); }
  }
  const section = () => list(state.doc?.sections).find((s) => s.id === state.sectionId);
  const allBlocks = (doc = state.doc) => list(doc?.sections).flatMap((s) => list(s.blocks).map((b) => ({ ...b, section_id: b.section_id || s.id })));
  const getBlock = (id, doc = state.doc) => allBlocks(doc).find((b) => b.id === id);
  const suggestions = (id) => list(state.doc?.suggestions).filter((s) => s.block_id === id && pendingSuggestions.has(s.status || "pending"));
  function commentOnly(block) {
    const lines = String(block.tex || "").split(/\r?\n/);
    return lines.some((line) => /^\s*%/.test(line)) && lines.every((line) => /^\s*(?:%.*)?$/.test(line));
  }
  function figureCaption(tex) { const match = /\\caption(?:\[[^\]]*\])?\s*\{/.exec(tex); return match ? grouped(tex, match.index + match[0].lastIndexOf("{"))?.inner || "" : ""; }
  function substantive(s) { return !s?.placeholder && list(s?.blocks).some((b) => !["heading", "comment"].includes(b.kind) && String(b.tex || "").replace(/^\s*%.*$/gm, "").trim() && !/^(?:(?:TODO|TBD|placeholder)\b|待补|待完善|尚无|未提供)/i.test(String(b.tex).trim())); }
  function sectionNumbers() { const values = new Map(), counters = [0, 0, 0]; for (const s of list(state.doc?.sections)) { const level = Math.min(3, Math.max(1, Number(s.level || (s.parent_id ? 2 : 1)))); counters[level - 1]++; for (let j = level; j < counters.length; j++) counters[j] = 0; values.set(s.id, counters.slice(0, level).join(".")); } return values; }
  const autoPrefKey = () => "paper-auto-review-" + state.projectId;
  function protectedEditing() { return $("editor-source-dialog").open || $("editor-suggestion-dialog").open || [...state.entries.values()].some((e) => e.dirty || e.saving || e.conflict) || Boolean(document.activeElement?.closest(".editor-block-body[contenteditable=true], .editor-block-tools, .editor-warning-button")); }
  const paperKey = (doc) => JSON.stringify([doc?.revision, doc?.sections, doc?.suggestions]);
  // Keep an action's target attached between pointer-down, editable blur, and click.
  function preserveActionFocus(button) { button.addEventListener("pointerdown", (event) => { if (event.button === 0) event.preventDefault(); }); button.addEventListener("blur", () => setTimeout(applyDeferred, 0)); }

  function mathInner(tex) {
    let value = String(tex || "").trim();
    if (value.startsWith("$$") && value.endsWith("$$")) value = value.slice(2, -2);
    else if (value.startsWith("\\[") && value.endsWith("\\]")) value = value.slice(2, -2);
    else if (value.startsWith("\\(") && value.endsWith("\\)")) value = value.slice(2, -2);
    else if (value.startsWith("$") && value.endsWith("$")) value = value.slice(1, -1);
    value = value.replace(/^\\begin\{equation\*?\}/, "").replace(/\\end\{equation\*?\}$/, "");
    value = value.replace(/\\begin\{align\*?\}/g, "\\begin{aligned}").replace(/\\end\{align\*?\}/g, "\\end{aligned}");
    value = value.replace(/\\label\{[^{}]*\}/g, "");
    return value.trim();
  }
  function math(tex, target, display = false) {
    target.replaceChildren();
    if (!window.katex) { target.textContent = tex; return; }
    try { window.katex.render(mathInner(tex), target, { displayMode: display, trust: false, throwOnError: false, strict: "ignore", maxSize: 20, maxExpand: 1000, output: "htmlAndMathml" }); }
    catch (_) { target.textContent = tex; }
  }
  function grouped(text, start) {
    let depth = 0;
    for (let i = start; i < text.length; i++) {
      if (text[i] === "\\") { i++; continue; }
      if (text[i] === "{") depth++;
      if (text[i] === "}" && --depth === 0) return { inner: text.slice(start + 1, i), end: i + 1 };
    }
    return null;
  }
  function renderInline(tex, target, blockId = null) {
    let i = 0, plain = "";
    const flush = () => { if (plain) target.append(document.createTextNode(plain)); plain = ""; };
    while (i < tex.length) {
      const opener = tex.startsWith("\\(", i) ? "\\(" : tex[i] === "$" && tex[i - 1] !== "\\" ? "$" : null;
      if (opener) {
        const closer = opener === "$" ? "$" : "\\)";
        const end = tex.indexOf(closer, i + opener.length);
        if (end >= 0) {
          flush(); const raw = tex.slice(i, end + closer.length), span = node("span", "editor-inline-math");
          span.contentEditable = "false"; span.dataset.tex = raw; span.title = "点击编辑公式的 LaTeX"; math(raw, span);
          if (blockId) span.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); openSource(blockId, span); });
          target.append(span); i = end + closer.length; continue;
        }
      }
      if (tex[i] === "\\") {
        const command = /^\\(textbf|textit|emph|textrm|textsf|texttt|section|subsection|subsubsection|paragraph|citep|citet|cite|eqref|ref|label|href|IEEEPARstart)\*?\{/.exec(tex.slice(i));
        if (command) {
          const group = grouped(tex, i + command[0].length - 1);
          if (group) {
            flush(); const name = command[1], raw = tex.slice(i, group.end);
            if (name === "IEEEPARstart") {
              let secondStart = group.end; while (/\s/.test(tex[secondStart] || "") && secondStart < tex.length) secondStart++;
              const remainder = tex[secondStart] === "{" ? grouped(tex, secondStart) : null;
              if (!remainder) { plain += raw; i = group.end; continue; }
              // Preserve the author's drop-cap command, including its first
              // argument and spacing. Only its word remainder is edited here;
              // the existing TeX action edits both arguments explicitly.
              const span = node("span", "editor-dropcap-word"); span.contentEditable = "false";
              span.dataset.latexPrefix = tex.slice(i, secondStart + 1);
              const initial = node("span"); initial.dataset.tex = ""; initial.contentEditable = "false";
              initial.title = "期刊首字母格式已保留；使用旁边的 TeX 按钮修改首字母。";
              renderInline(group.inner, initial);
              const rest = node("span"); rest.contentEditable = blockId ? "true" : "false";
              if (blockId) rest.setAttribute("aria-label", "编辑首词其余部分；首字母可通过 TeX 修改");
              renderInline(remainder.inner, rest, blockId);
              span.append(initial, rest); target.append(span); i = remainder.end; continue;
            } else if (name === "href") {
              let secondStart = group.end; while (/\s/.test(tex[secondStart] || "") && secondStart < tex.length) secondStart++;
              const label = tex[secondStart] === "{" ? grouped(tex, secondStart) : null;
              if (!label) { plain += raw; i = group.end; continue; }
              const span = node("a", "editor-source-link"); span.dataset.latexPrefix = tex.slice(i, secondStart + 1);
              try { const url = new URL(group.inner.replace(/\\([_%#&{}])/g, "$1")); if (url.protocol === "https:") { span.href = url.href; span.target = "_blank"; span.rel = "noopener noreferrer"; span.title = "按住 Ctrl / ⌘ 点击查看来源"; } } catch (_) { /* Unverified/unsafe URL remains a source-preserving label. */ }
              span.addEventListener("click", (event) => { if (!event.ctrlKey && !event.metaKey) event.preventDefault(); });
              renderInline(label.inner, span, blockId); target.append(span); i = label.end; continue;
            } else if (/^(cite|ref|eqref|label)/.test(name)) {
              const span = node("span", "editor-citation", name === "label" ? "" : name.startsWith("cite") ? "[" + group.inner + "]" : "(" + group.inner + ")");
              span.contentEditable = "false"; span.dataset.tex = raw; span.title = raw; target.append(span);
            } else {
              const span = node(name === "textbf" ? "strong" : /textit|emph/.test(name) ? "em" : name === "texttt" ? "code" : "span");
              span.dataset.latexCommand = name + (command[0].includes("*") ? "*" : ""); renderInline(group.inner, span, blockId); target.append(span);
            }
            i = group.end; continue;
          }
        }
        if (/^[%&#_{}$]/.test(tex[i + 1] || "")) { flush(); const span = node("span", "", tex[i + 1]); span.dataset.tex = tex.slice(i, i + 2); span.contentEditable = "false"; target.append(span); i += 2; continue; }
        if (tex[i + 1] === "\\") { flush(); const br = node("br"); br.dataset.tex = "\\\\"; target.append(br); i += 2; continue; }
      }
      plain += tex[i++];
    }
    flush();
  }
  function serialize(target) {
    const walk = (n) => {
      if (n.nodeType === Node.TEXT_NODE) return n.nodeValue.replace(/[&%#_]/g, (c) => "\\" + c);
      if (n.nodeType !== Node.ELEMENT_NODE) return "";
      if (n.dataset.tex !== undefined) return n.dataset.tex;
      if (n.tagName === "BR") return "\n";
      let text = [...n.childNodes].map(walk).join("");
      if (n.dataset.latexPrefix !== undefined) return n.dataset.latexPrefix + text + "}";
      if (n.dataset.latexCommand) return "\\" + n.dataset.latexCommand + "{" + text + "}";
      if (["B", "STRONG"].includes(n.tagName)) return "\\textbf{" + text + "}";
      if (["I", "EM"].includes(n.tagName)) return "\\emph{" + text + "}";
      if (n.tagName === "DIV" && n !== target && n.previousSibling) text = "\n" + text;
      return text;
    };
    return [...target.childNodes].map(walk).join("").replace(/\u00a0/g, " ");
  }

  async function loadDoc({ projectId = state.projectId, force = false, initial = false } = {}) {
    if (!projectId) { $("editor-paper").replaceChildren(node("div", "editor-empty", "先选择一个课题，再开始编辑论文。")); return; }
    const serial = ++state.loadSerial; state.loading = true;
    try {
      const response = await api("/api/manuscript/" + encodeURIComponent(projectId));
      if (projectId !== state.projectId || serial !== state.loadSerial) return;
      const doc = response.manuscript || response.document || response;
      for (const job of list(doc.jobs)) if (job.id) state.jobs.set(job.id, job);
      if (!force && state.doc && paperKey(doc) === paperKey(state.doc)) { state.doc = doc; state.latest = null; renderHeader(); renderJobs(); return; }
      if (!force && protectedEditing() && state.doc) { state.latest = doc; renderHeader(); renderJobs(); return; }
      const old = new Map(allBlocks().map((b) => [b.id, b.sha256]));
      for (const b of allBlocks(doc)) if (old.has(b.id) && old.get(b.id) !== b.sha256) state.changed.set(b.id, Date.now());
      state.doc = doc; state.latest = null;
      if (!list(doc.sections).some((s) => s.id === state.sectionId)) state.sectionId = doc.sections?.[0]?.id || "";
      renderHeader(); renderOutline(); renderPaper(); renderJobs();
      if (initial && state.visible) maybeAutoReview();
    } catch (error) { if (projectId === state.projectId) notice(error.message, true); }
    finally { if (serial === state.loadSerial) state.loading = false; }
  }
  function renderHeader() {
    $("editor-version").textContent = state.doc ? "版本 " + (state.doc.revision ?? "—") : "等待文稿";
    const source = state.doc?.source_type || state.doc?.provenance?.source_type || "";
    const labels = { pdf_extracted: "由 PDF 重建的 LaTeX 工作稿", reconstructed: "重建的 LaTeX 工作稿", approved_draft: "已确定方案的可编辑工作稿", agent_candidate: "智能体候选工作稿", original_latex_revision: "基于原始 LaTeX 的修订工作稿" };
    const note = state.doc?.source_note || state.doc?.provenance?.note || labels[source] || source;
    const reference = state.doc?.source_reference || state.doc?.original_pdf || state.doc?.source_pdf || state.doc?.manuscript_source;
    $("editor-provenance").textContent = [note || "编辑和导出的是当前 LaTeX 工作稿；保留来源与版本记录。", reference ? "原稿参照：" + (typeof reference === "string" ? reference : reference.title || reference.id || "") : ""].filter(Boolean).join("\n");
    $("editor-auto-review").checked = state.doc?.auto_review !== false && localStorage.getItem(autoPrefKey()) !== "off";
    $("editor-auto-review").disabled = state.doc?.auto_review === false;
    $("editor-auto-note").textContent = state.doc?.auto_review === false ? "自动专家检查尚未启用，可手动检查本节。" : "进入章节时按当前版本检查一次，刷新不会重复发起。";
  }
  function renderOutline() {
    const nav = $("editor-sections"); nav.replaceChildren();
    const numbers = sectionNumbers();
    for (const s of list(state.doc?.sections)) {
      const button = node("button", "editor-section" + (s.id === state.sectionId ? " active" : "") + (Number(s.level || (s.parent_id ? 2 : 1)) > 1 ? " nested" : ""));
      button.append(node("span", "", /^\d+[.\s]/.test(s.title || "") ? s.title : numbers.get(s.id) + " " + (s.title || s.id)));
      const count = list(state.doc.suggestions).filter((x) => list(s.blocks).some((b) => b.id === x.block_id) && pendingSuggestions.has(x.status || "pending")).length;
      button.append(node("small", "", list(s.blocks).length + " 个段落 / 公式" + (count ? " · " + count + " 处建议" : "")));
      button.addEventListener("click", guarded(async () => { await flushDrafts(); const child = !list(s.blocks).length ? list(state.doc.sections).find((x) => x.parent_id === s.id && substantive(x)) || list(state.doc.sections).find((x) => x.parent_id === s.id) : null; state.sectionId = child?.id || s.id; state.blockId = ""; renderOutline(); renderPaper(); maybeAutoReview(); })); nav.append(button);
      if (s.id === state.sectionId) for (const b of list(s.blocks).filter((b) => b.kind === "heading").slice(0, 12)) {
        const sub = node("button", "editor-subsection", b.tex.replace(/\\(?:sub)*section\*?\{([^}]*)\}/g, "$1").slice(0, 90));
        sub.addEventListener("click", () => { document.getElementById("editor-block-" + b.id)?.scrollIntoView({ block: "start" }); selectBlock(b.id); }); nav.append(sub);
      }
    }
  }
  function selectBlock(id) {
    state.blockId = id;
    document.querySelectorAll(".editor-block").forEach((e) => e.classList.toggle("selected", e.dataset.blockId === id));
    const b = state.entries.get(id)?.block;
    $("editor-selection").textContent = (section()?.title || "") + (b ? " · " + (b.kind === "math" ? "选中公式" : "选中段落") : "");
  }
  function renderPaper() {
    const target = $("editor-paper"), scroll = $("editor-canvas").scrollTop;
    target.replaceChildren(); state.entries.clear();
    const s = section();
    if (!s) { target.append(node("div", "editor-empty", "当前还没有可编辑正文。可先在协作任务中形成稿件版本。")); return; }
    target.append(node("div", "editor-page-label", "MANUSCRIPT · EDITABLE LATEX"));
    const number = sectionNumbers().get(s.id);
    if (!list(s.blocks).some((b) => b.kind === "heading" && /section\{|^\s*(?:Introduction|Method)/i.test(b.tex))) target.append(node("h1", "", number + " " + (s.title || s.id)));
    for (const b of list(s.blocks)) {
      if (commentOnly(b)) continue;
      const block = { ...b, section_id: b.section_id || s.id }, card = node("section", "editor-block");
      card.id = "editor-block-" + b.id; card.dataset.blockId = b.id;
      if (Date.now() - (state.changed.get(b.id) || 0) < 12000) card.classList.add("changed");
      const body = node("div", "editor-block-body" + (b.kind === "heading" ? " paper-heading" : b.kind === "raw" ? " paper-raw" : ""));
      const entry = { block, card, body, dirty: false, saving: null, timer: null, conflict: null }; state.entries.set(b.id, entry);
      if (b.kind === "raw" && /^\s*\\begin\s*\{figure\*?\}/.test(b.tex)) {
        entry.displayOnly = true; entry.savedDom = b.tex;
        body.tabIndex = 0; body.setAttribute("role", "button"); body.setAttribute("aria-label", "查看原工程图稿的 LaTeX");
        body.append(node("div", "editor-paragraph-status", "原工程图稿 · 点击查看 LaTeX"));
        const caption = node("div", "editor-help"); renderInline(figureCaption(b.tex) || "图稿及路径随原始 LaTeX 工程保留。", caption); body.append(caption);
        body.addEventListener("click", guarded(() => openSource(b.id)));
        body.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); guarded(() => openSource(b.id))(); } });
      } else if (b.kind === "math") {
        body.classList.add("editor-block-math"); body.tabIndex = 0; body.setAttribute("role", "button"); body.setAttribute("aria-label", "点击编辑此公式");
        math(b.tex, body, true); body.addEventListener("click", () => openSource(b.id)); body.addEventListener("keydown", (e) => { if (e.key === "Enter") openSource(b.id); });
      } else {
        body.contentEditable = "true"; body.spellcheck = false; body.setAttribute("role", "textbox"); body.setAttribute("aria-label", "编辑" + (s.title || "正文") + "段落");
        if (b.kind === "raw") body.textContent = b.tex; else { if (b.kind === "heading" && /\\(?:sub)*section\*?\{/.test(b.tex)) { const prefix = node("span", "", number + " "); prefix.dataset.tex = ""; prefix.contentEditable = "false"; body.append(prefix); } renderInline(b.tex, body, b.id); }
        entry.savedDom = b.kind === "raw" ? body.textContent : serialize(body);
        body.addEventListener("input", () => { entry.dirty = currentTex(entry) !== entry.savedDom; clearTimeout(entry.timer); if (entry.dirty) { saveStatus("尚未保存"); entry.timer = setTimeout(() => saveBlock(b.id).catch((e) => notice(e.message, true)), 1500); } });
        body.addEventListener("focus", () => { selectBlock(b.id); body.classList.add("editing"); });
        body.addEventListener("blur", () => { body.classList.remove("editing"); clearTimeout(entry.timer); saveBlock(b.id).then(() => applyDeferred()).catch((e) => notice(e.message, true)); });
        body.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); body.blur(); } });
        body.addEventListener("paste", (event) => { event.preventDefault(); document.execCommand("insertText", false, event.clipboardData.getData("text/plain")); });
      }
      card.append(body); card.addEventListener("click", () => selectBlock(b.id));
      const tools = node("div", "editor-block-tools"), sourceButton = node("button", "editor-source-button", "TeX"); sourceButton.title = "编辑本段实际 LaTeX";
      preserveActionFocus(sourceButton);
      sourceButton.addEventListener("click", guarded((event) => { event.stopPropagation(); return openSource(b.id); })); tools.append(sourceButton);
      const warnings = suggestions(b.id);
      if (warnings.length) {
        card.classList.add("warned");
        const warning = node("button", "editor-warning-button", "⚠ 查看修改建议 · " + warnings.length + " 处");
        warning.title = warnings.map((x) => x.reason || "待处理修改建议").join("\n");
        warning.setAttribute("aria-label", "展开此处的修改原因和差异");
        warning.setAttribute("aria-haspopup", "dialog"); preserveActionFocus(warning);
        warning.addEventListener("click", guarded((event) => { event.stopPropagation(); return openSuggestion(warnings[0].id); }));
        card.prepend(warning);
        card.addEventListener("click", guarded((event) => { if (!event.target.closest(".editor-block-body, button, a")) return openSuggestion(warnings[0].id); }));
      }
      card.append(tools); target.append(card);
    }
    selectBlock(state.entries.has(state.blockId) ? state.blockId : ""); $("editor-canvas").scrollTop = scroll;
  }
  const currentTex = (entry) => entry.displayOnly ? entry.block.tex : entry.block.kind === "raw" ? entry.body.textContent : serialize(entry.body);
  function applyDeferred() {
    if (!state.latest || protectedEditing()) return;
    if (paperKey(state.latest) === paperKey(state.doc)) { state.doc = state.latest; state.latest = null; renderHeader(); return; }
    if (Number(state.latest.revision) < Number(state.doc?.revision)) { state.latest = null; loadDoc(); return; }
    const old = new Map(allBlocks().map((b) => [b.id, b.sha256]));
    for (const b of allBlocks(state.latest)) if (old.has(b.id) && old.get(b.id) !== b.sha256) state.changed.set(b.id, Date.now());
    state.doc = state.latest; state.latest = null; renderHeader(); renderOutline(); renderPaper();
  }
  function registerContextJob(data) {
    const changed = data.block || data.updated_block;
    if (changed?.id && data.change_id) state.lastChange = { block_id: changed.id, change_id: data.change_id };
    else if (changed?.id) state.lastChange = { block_id: changed.id, change_id: null };
    const job = data.context_job || data.job;
    if (job?.id) state.jobs.set(job.id, job);
    else if (data.context_job_id) state.jobs.set(data.context_job_id, { id: data.context_job_id, action: "sync", status: "queued" });
    renderJobs();
  }
  async function saveBlock(id, explicitTex = null, forcedBase = null) {
    const entry = state.entries.get(id); if (!entry) return;
    clearTimeout(entry.timer);
    if (entry.saving) await entry.saving;
    const tex = explicitTex === null ? currentTex(entry) : explicitTex;
    if (explicitTex === null && tex === entry.savedDom) { entry.dirty = false; return; }
    if (entry.conflict && !forcedBase) throw new Error("此段有版本冲突，请先点击段落中的“对比版本”。");
    const base = forcedBase || entry.block.sha256;
    saveStatus("正在保存…");
    const operation = (async () => {
      const response = await api("/api/manuscript/" + encodeURIComponent(state.projectId) + "/blocks/" + encodeURIComponent(id), { tex, base_hash: base, reason: "manual edit" });
      const saved = response.block || response.updated_block || (response.sha256 ? response : null);
      if (saved) Object.assign(entry.block, saved); else { const fresh = await api("/api/manuscript/" + encodeURIComponent(state.projectId)); const found = getBlock(id, fresh.manuscript || fresh); if (found) Object.assign(entry.block, found); }
      entry.savedDom = tex; entry.dirty = explicitTex === null && currentTex(entry) !== tex; entry.conflict = null; entry.card.classList.remove("conflict");
      if (state.doc) { const original = list(state.doc.sections).flatMap((s) => list(s.blocks)).find((b) => b.id === id); if (original) Object.assign(original, entry.block, { tex }); const revision = response.document_revision ?? response.revision; if (revision !== undefined) state.doc.revision = revision; }
      registerContextJob(response); saveStatus(entry.dirty ? "尚未保存" : "已保存"); renderHeader();
      if (explicitTex !== null) { entry.block.tex = tex; state.changed.set(id, Date.now()); }
    })();
    entry.saving = operation;
    try { await operation; }
    catch (error) {
      entry.dirty = true; saveStatus(error.status === 409 ? "版本冲突" : "保存失败", true);
      if (error.status === 409) {
        entry.conflict = true; entry.card.classList.add("conflict");
        if (!entry.card.querySelector(".editor-conflict-action")) { const action = node("button", "editor-conflict-action", "此段已更新 · 对比版本"); action.addEventListener("click", guarded(() => openConflict(id))); entry.card.append(action); }
      }
      throw error;
    } finally { entry.saving = null; }
  }
  async function flushDrafts() {
    for (const entry of state.entries.values()) { clearTimeout(entry.timer); if (entry.saving) await entry.saving; if (entry.dirty || entry.conflict) await saveBlock(entry.block.id); }
  }

  async function openSource(id, inlineSpan = null) {
    const entry = state.entries.get(id); if (!entry) return; selectBlock(id);
    state.source = { id, inlineSpan, math: Boolean(inlineSpan || entry.block.kind === "math"), original: inlineSpan?.dataset.tex || (entry.dirty ? currentTex(entry) : entry.block.tex) };
    $("editor-source-title").textContent = state.source.math ? "编辑公式" : "编辑段落 LaTeX";
    $("editor-source-input").value = state.source.original;
    $("editor-source-dialog").showModal(); sourcePreview(); $("editor-source-input").focus();
  }
  function sourcePreview() {
    const preview = $("editor-source-preview"); preview.replaceChildren();
    if (state.source?.math) math($("editor-source-input").value, preview, !state.source.inlineSpan);
    else renderInline($("editor-source-input").value, preview);
    $("editor-source-error").textContent = preview.querySelector(".katex-error") ? "预览未能识别此公式；请检查语法或自定义宏。源代码会原样保存。" : "";
  }
  async function saveSource() {
    const source = state.source; if (!source) return; const entry = state.entries.get(source.id); if (!entry) return;
    $("editor-source-save").disabled = true;
    try {
      let value = $("editor-source-input").value;
      if (value === source.original && !entry.dirty) { $("editor-source-dialog").close(); state.source = null; applyDeferred(); return; }
      if (source.inlineSpan) {
        if (!/^\s*(?:\\\(|\$)/.test(value)) value = "\\(" + value + "\\)";
        source.inlineSpan.dataset.tex = value; math(value, source.inlineSpan); entry.dirty = true; await saveBlock(source.id);
      } else await saveBlock(source.id, value);
      await flushDrafts();
      $("editor-source-dialog").close(); state.source = null; await loadDoc({ force: true });
    } catch (error) { $("editor-source-error").textContent = error.message; }
    finally { $("editor-source-save").disabled = false; }
  }
  function fillSuggestion(s, conflict = null) {
    state.suggestion = { ...s, conflict };
    $("editor-suggestion-reason").textContent = s.reason || "对这段表述、推导或符号的修改建议。";
    const kinds = { paragraph: "段落", prose: "表述", math: "公式", consistency: "前后一致性", notation: "符号", logic: "逻辑", citation: "引用", method: "方法" }, severities = { critical: "关键问题", major: "重要问题", minor: "细节修改", info: "说明" };
    $("editor-suggestion-meta").textContent = [s.expert ? "负责专家：" + expertLabel(s.expert) : "", kinds[s.kind] || s.kind || "", severities[s.severity] || s.severity || "", s.status === "pending" ? "待处理" : statusLabels[s.status] || s.status || ""].filter(Boolean).join(" · ");
    const target = $("editor-suggestion-diff"); target.replaceChildren();
    const b = getBlock(s.block_id); const formula = s.kind === "math" || b?.kind === "math";
    if (formula) {
      for (const [value, cls, label] of [[s.original_tex, "editor-delete", "原公式"], [s.proposed_tex, "editor-insert", "建议公式"]]) { target.append(node("p", "editor-diff-formula-label " + cls, label)); const f = node("div", cls); math(value || "", f, true); target.append(f); }
    } else for (const part of list(s.diff).length ? s.diff : [{ kind: "delete", text: s.original_tex || "" }, { kind: "insert", text: s.proposed_tex || "" }]) {
      const span = node("span", part.kind === "insert" ? "editor-insert" : part.kind === "delete" ? "editor-delete" : "editor-diff-equal"); renderInline(part.text || "", span); target.append(span);
    }
    const evidence = $("editor-suggestion-evidence-body"); evidence.replaceChildren();
    const values = Array.isArray(s.evidence) ? s.evidence : s.evidence ? [s.evidence] : [];
    if (!values.length) evidence.append(node("p", "", "当前建议没有附加独立来源。接受前请结合原因与上下文核对。"));
    for (const item of values) evidence.append(node("p", "", typeof item === "string" ? item : JSON.stringify(item, null, 2)));
    $("editor-proposal-edit").classList.add("hidden"); $("editor-proposal-input").value = s.proposed_tex || ""; $("editor-suggestion-error").textContent = "";
    $("editor-suggestion-accept").textContent = conflict ? "保留本地并保存" : "接受修改";
    $("editor-suggestion-reject").textContent = conflict ? "使用服务器版本" : "暂不采用";
    $("editor-suggestion-accept").disabled = $("editor-suggestion-reject").disabled = $("editor-edit-proposal").disabled = false;
    $("editor-suggestion-retry").classList.add("hidden");
    $("editor-suggestion-dialog").removeAttribute("aria-busy");
    if (!$("editor-suggestion-dialog").open) $("editor-suggestion-dialog").showModal();
  }
  async function openSuggestion(id) {
    const serial = ++state.suggestionSerial, projectId = state.projectId, dialog = $("editor-suggestion-dialog");
    state.suggestion = null; state.suggestionId = id;
    $("editor-suggestion-meta").textContent = "正在读取修改建议…";
    $("editor-suggestion-reason").textContent = "正在核对当前段落并加载修改原因与差异。";
    $("editor-suggestion-diff").replaceChildren(node("p", "editor-help", "读取中…"));
    $("editor-suggestion-evidence-body").replaceChildren(); $("editor-proposal-edit").classList.add("hidden");
    $("editor-suggestion-error").textContent = ""; $("editor-suggestion-retry").classList.add("hidden");
    $("editor-suggestion-accept").disabled = $("editor-suggestion-reject").disabled = $("editor-edit-proposal").disabled = true;
    dialog.setAttribute("aria-busy", "true"); if (!dialog.open) dialog.showModal();
    try {
      await flushDrafts();
      const result = await api("/api/suggestions/" + encodeURIComponent(id));
      if (serial !== state.suggestionSerial || projectId !== state.projectId || !dialog.open) return;
      fillSuggestion(result.suggestion || result);
    } catch (error) {
      if (serial !== state.suggestionSerial || projectId !== state.projectId || !dialog.open) return;
      dialog.removeAttribute("aria-busy"); $("editor-suggestion-meta").textContent = "修改建议读取失败";
      $("editor-suggestion-reason").textContent = "建议尚未载入，请重试。";
      $("editor-suggestion-diff").replaceChildren();
      $("editor-suggestion-error").textContent = error.message || String(error);
      $("editor-suggestion-retry").classList.remove("hidden");
    }
  }
  function closeSuggestion() { ++state.suggestionSerial; $("editor-suggestion-dialog").close(); state.suggestion = null; state.suggestionId = ""; applyDeferred(); }
  async function openConflict(id) {
    const entry = state.entries.get(id), fresh = await api("/api/manuscript/" + encodeURIComponent(state.projectId));
    const server = getBlock(id, fresh.manuscript || fresh); if (!server) throw new Error("服务器已移除此段；本地文字仍保留，请复制后刷新。");
    fillSuggestion({ block_id: id, original_tex: server.tex, proposed_tex: currentTex(entry), reason: "其他任务已更新此段。以下以服务器当前版本为黑/蓝色基线，你尚未保存的本地文字为红色。请选择保留哪一版。", kind: entry.block.kind, expert: "版本冲突检查", evidence: [] }, { server });
  }
  function proposalPreview() { const box = $("editor-proposal-preview"); box.replaceChildren(); if (state.suggestion?.kind === "math" || getBlock(state.suggestion?.block_id)?.kind === "math") math($("editor-proposal-input").value, box, true); else renderInline($("editor-proposal-input").value, box); }
  async function resolveSuggestion(accept) {
    const s = state.suggestion; if (!s) return;
    const serial = state.suggestionSerial, projectId = state.projectId;
    const sameSession = () => serial === state.suggestionSerial && projectId === state.projectId && state.suggestion === s && $("editor-suggestion-dialog").open;
    $("editor-suggestion-accept").disabled = $("editor-suggestion-reject").disabled = true;
    try {
      const edited = !$("editor-proposal-edit").classList.contains("hidden"); const tex = edited ? $("editor-proposal-input").value : s.proposed_tex;
      if (s.conflict) {
        if (accept) await saveBlock(s.block_id, tex, s.conflict.server.sha256);
        else { const entry = state.entries.get(s.block_id); entry.dirty = false; entry.conflict = null; }
      } else {
        const response = await api("/api/suggestions/" + encodeURIComponent(s.id) + (accept ? "/accept" : "/reject"), edited && accept ? { proposed_tex: tex } : {});
        if (projectId === state.projectId) registerContextJob(response);
      }
      if (projectId !== state.projectId) return;
      if (accept) state.changed.set(s.block_id, Date.now());
      if (!sameSession()) { await loadDoc(); return; }
      closeSuggestion(); await loadDoc(); saveStatus(accept ? "已应用修改" : "正文未改动");
    } catch (error) { if (sameSession()) $("editor-suggestion-error").textContent = error.message; else if (projectId === state.projectId) notice(error.message, true); }
    finally { if (sameSession()) $("editor-suggestion-accept").disabled = $("editor-suggestion-reject").disabled = false; }
  }

  function renderJobs() {
    const target = $("editor-jobs"); target.replaceChildren();
    const jobs = [...state.jobs.values()].slice(-6).reverse();
    for (const job of jobs) { const action = job.action || job.kind || ""; const label = action.includes("sync") ? "关联内容同步" : action.includes("review") ? "专家检查" : "专家修改"; const line = node("span", "editor-job " + job.status, label + " · " + (statusLabels[job.status] || job.status || "等待中")); line.title = [job.message, job.error, job.current_role].filter(Boolean).join("\n"); target.append(line); }
    if ([...state.jobs.values()].some((j) => activeJobs.has(j.status) && /sync/.test(j.action || j.kind || ""))) saveStatus("已保存 · 关联内容同步中");
  }
  function commandPolicy() {
    const sync = $("editor-action").value === "sync";
    $("editor-auto-apply-label").classList.toggle("hidden", !sync);
    $("editor-auto-apply").disabled = !sync;
    if (!sync) $("editor-auto-apply").checked = false;
    $("editor-command-policy").textContent = sync ? "未通过检查的内容仍保留为建议" : "生成建议，采纳后更新稿件";
  }
  async function submitJob(action, message = "", automatic = false) {
    if (!state.projectId || !section()) { if (automatic) return; throw new Error("请先选择包含正文的课题和章节。"); }
    if (action === "review" && !substantive(section())) { if (!automatic) throw new Error("当前目录项还没有可检查的正文，请选择具体小节或先补充内容。"); return; }
    if (!automatic) await flushDrafts();
    const data = { project_id: state.projectId, action, section_id: state.sectionId, message, auto_apply: !automatic && action === "sync" && $("editor-auto-apply").checked };
    if (!automatic && $("editor-scope").value === "block") { if (!state.blockId) throw new Error("请先点击要修改的段落或公式。"); data.block_id = state.blockId; }
    if (action === "sync") {
      const changed = state.blockId || state.lastChange?.block_id;
      if (!changed) throw new Error("请先选择一段正文或公式，或保存一处修改，再检查关联内容。");
      data.changed_block_id = changed;
      if (state.lastChange?.block_id === changed && state.lastChange.change_id) data.change_id = state.lastChange.change_id;
    }
    const response = await api("/api/editor/jobs", data); const job = response.job || response;
    if (job.id) state.jobs.set(job.id, { action, ...job }); renderJobs();
    return job;
  }
  function maybeAutoReview() {
    const s = section(); if (!s || !substantive(s) || state.doc?.auto_review === false || localStorage.getItem(autoPrefKey()) === "off") return;
    const identity = state.projectId + ":" + (state.doc.context_hash || state.doc.revision || "") + ":" + s.id + ":" + list(s.blocks).map((b) => b.sha256 || b.revision || b.tex).join("|");
    if (state.requested.has(identity)) return; state.requested.add(identity);
    submitJob("review", "自动检查当前章节的逻辑、公式、术语与关联一致性；只提出建议，不覆盖正文。", true).catch((e) => notice("自动专家检查未启动：" + e.message, true));
  }
  async function exportTex(tracked = false, copy = false) {
    await flushDrafts();
    const response = await api("/api/manuscript/" + encodeURIComponent(state.projectId) + "/export?tracked=" + (tracked ? "1" : "0"));
    const tex = response.tex || "";
    if (copy) { await navigator.clipboard.writeText(tex); saveStatus("LaTeX 已复制"); return; }
    const link = node("a"); link.href = URL.createObjectURL(new Blob([tex], { type: "application/x-tex;charset=utf-8" })); link.download = "manuscript" + (tracked ? "-tracked" : "") + "-v" + (response.revision || state.doc?.revision || 1) + ".tex"; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); saveStatus("LaTeX 已导出");
  }
  window.addEventListener("paper-project-changed", (event) => {
    const detail = event.detail || {}; state.project = detail.project;
    if (detail.project_id === state.projectId) return;
    ++state.suggestionSerial; $("editor-suggestion-dialog").close(); state.suggestion = null; state.suggestionId = "";
    if ([...state.entries.values()].some((entry) => entry.dirty)) notice("切换课题前有未保存文字；请返回原课题处理版本冲突或复制本地文字。", true);
    state.projectId = detail.project_id || ""; state.sectionId = ""; state.blockId = ""; state.doc = null; state.latest = null; state.lastChange = null; state.entries.clear(); state.jobs.clear(); loadDoc({ initial: true });
  });
  window.paperEditorBeforeProjectChange = flushDrafts;
  window.addEventListener("beforeunload", (event) => { if ([...state.entries.values()].some((entry) => entry.dirty || entry.saving)) { event.preventDefault(); event.returnValue = ""; } });
  window.addEventListener("paper-view-changed", (event) => { state.visible = event.detail?.view === "editor"; if (state.visible) { if (state.doc) { if (event.detail?.review !== false) maybeAutoReview(); } else loadDoc({ initial: event.detail?.review !== false }); } });
  window.addEventListener("paper-open-revisions", guarded(async () => {
    await flushDrafts(); await loadDoc();
    const suggestion = list(state.doc?.suggestions).find((s) => pendingSuggestions.has(s.status || "pending") && getBlock(s.block_id));
    if (!suggestion) { notice("当前工作稿没有待处理的段落建议。运行中的教师意见仍可在协作记录中查看。"); return; }
    const block = getBlock(suggestion.block_id); state.sectionId = block.section_id; state.blockId = block.id;
    renderOutline(); renderPaper(); document.getElementById("editor-block-" + block.id)?.scrollIntoView({ block: "center" });
    await openSuggestion(suggestion.id);
  }));
  $("editor-reload").addEventListener("click", guarded(async () => { await flushDrafts(); await loadDoc({ force: true }); }));
  $("editor-auto-review").addEventListener("change", () => { localStorage.setItem(autoPrefKey(), $("editor-auto-review").checked ? "on" : "off"); if ($("editor-auto-review").checked) maybeAutoReview(); });
  $("editor-source-input").addEventListener("input", sourcePreview); $("editor-source-save").addEventListener("click", saveSource);
  for (const id of ["editor-source-close", "editor-source-cancel"]) $(id).addEventListener("click", () => { $("editor-source-dialog").close(); state.source = null; applyDeferred(); });
  $("editor-source-dialog").addEventListener("cancel", () => { state.source = null; setTimeout(applyDeferred, 0); });
  $("editor-suggestion-close").addEventListener("click", closeSuggestion);
  $("editor-suggestion-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeSuggestion(); });
  $("editor-suggestion-retry").addEventListener("click", guarded(() => { if (state.suggestionId) return openSuggestion(state.suggestionId); }));
  $("editor-suggestion-accept").addEventListener("click", () => resolveSuggestion(true)); $("editor-suggestion-reject").addEventListener("click", () => resolveSuggestion(false));
  $("editor-edit-proposal").addEventListener("click", () => { $("editor-proposal-edit").classList.toggle("hidden"); proposalPreview(); });
  $("editor-proposal-input").addEventListener("input", proposalPreview);
  $("editor-review").addEventListener("click", guarded(() => submitJob("review", "请本节专家逐项检查当前内容，并给出可追溯的修改建议。")));
  $("editor-command-form").addEventListener("submit", guarded(async (event) => { event.preventDefault(); const message = $("editor-command").value.trim(); if (!message) throw new Error("请先说明要修改或讨论的问题。"); if (state.commandBusy) return; state.commandBusy = true; $("editor-send").disabled = true; try { await submitJob($("editor-action").value, message); $("editor-command").value = ""; } finally { state.commandBusy = false; $("editor-send").disabled = false; } }));
  $("editor-command").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("editor-command-form").requestSubmit(); } });
  $("editor-action").addEventListener("change", commandPolicy); commandPolicy();
  $("editor-copy").addEventListener("click", guarded(() => exportTex(false, true))); $("editor-export").addEventListener("click", guarded(() => exportTex(false))); $("editor-export-tracked").addEventListener("click", guarded(() => exportTex(true)));
  setInterval(async () => {
    if (!state.projectId || document.hidden || !state.visible) return;
    try {
      for (const [id, job] of state.jobs) if (activeJobs.has(job.status)) { const response = await api("/api/editor/jobs/" + encodeURIComponent(id)); state.jobs.set(id, response.job || response); }
      renderJobs(); if (!state.loading) await loadDoc();
    } catch (error) { notice(error.message, true); }
  }, 4000);
})();
