/* Imported manuscript discussions: read-only sources, explicit handoff to the author. */
(() => {
  "use strict";
  const state = {pid: "", serial: 0, data: null, selected: "", query: "", role: "all", limit: 16, pending: null, cache: new Map()};
  const arr = value => Array.isArray(value) ? value : [];
  const host = () => document.getElementById("ol-agent-context");
  const node = (tag, cls = "", text = "") => { const element = document.createElement(tag); element.className = cls; element.textContent = text; return element; };
  const text = value => typeof value === "string" ? value : "";
  const statusLabels = {current: "当前要求", confirmed: "作者要求", author_requirement: "作者要求", user_requirement: "作者要求", requirement: "作者要求", historical: "历史候选", candidate: "候选方案", pending: "待确认", needs_review: "待核查", needs_verification: "待核查", discussion: "参考讨论"};
  const statusLabel = value => statusLabels[value] || "参考讨论";
  function link(url) {
    try { const value = new URL(url); return value.protocol === "https:" && value.hostname === "chatgpt.com" ? value.href : ""; } catch (_) { return ""; }
  }
  function button(label, fn, cls = "ol-secondary") {
    const item = node("button", cls, label); item.type = "button"; item.addEventListener("click", fn); return item;
  }
  function coverageText(data) {
    const coverage = data.coverage || {};
    if (coverage.shared_snapshot && coverage.thread_read) {
      const shared = coverage.shared_snapshot, live = coverage.thread_read;
      return (coverage.total_messages ?? arr(data.messages).length) + " 条可见消息：共享快照 " + (shared.messages ?? "—") + " 条 + 最新 " + (live.turns ?? "—") + " 轮 " + (live.messages ?? "—") + " 条。\n" +
        (arr(live.truncated_message_ids).length ? arr(live.truncated_message_ids).length + " 条较早回复被截断。" : "") +
        (coverage.complete_original_conversation === false ? "这不是原会话的完整导出。" : "");
    }
    return [text(coverage.description || coverage.summary || coverage.note),
      text(coverage.reading_scope), text(coverage.incomplete_note)].filter(Boolean).join("\n") ||
      "仅包含已读取的可见对话；不包含隐藏推理、工具输出或未读取附件。";
  }
  function messageDate(value) {
    if (value === undefined || value === null || value === "") return "";
    const numeric = Number(value), date = Number.isFinite(numeric) && numeric > 0 ? new Date(numeric < 1e12 ? numeric * 1000 : numeric) : new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit"});
  }
  function sections() { return arr(state.data?.sections); }
  function activeSection() { return sections().find(section => section.id === state.selected) || null; }
  function matches(message) {
    const section = activeSection();
    if (section && !arr(section.message_ids).includes(message.id)) return false;
    if (state.role !== "all" && message.role !== state.role) return false;
    if (!state.query.trim()) return true;
    return (text(message.text) + "\n" + text(message.source) + "\n" + String(message.source_index ?? "")).toLocaleLowerCase().includes(state.query.trim().toLocaleLowerCase());
  }
  function preview(message) {
    return text(message.text).replace(/\s+/g, " ").slice(0, 155) + (text(message.text).length > 155 ? "…" : "");
  }
  // Source text becomes text nodes. Only delimited mathematical expressions are
  // rendered; fenced TeX documents and inline code remain readable source.
  function renderText(target, value) {
    const pieces = String(value || "").split(/(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g);
    for (const piece of pieces) {
      if (!piece) continue;
      if (piece.startsWith("```")) {
        target.append(node("pre", "lssm-source-code", piece.replace(/^```[^\n]*\n?/, "").replace(/```$/, "")));
      } else if (piece.startsWith("`")) {
        target.append(node("code", "", piece.slice(1, -1)));
      } else {
        const prose = node("span", "lssm-prose", piece); target.append(prose);
        if (typeof window.renderMathInElement === "function") {
          try {
            window.renderMathInElement(prose, {trust: false, throwOnError: true, strict: "ignore", maxSize: 20, maxExpand: 1000,
              delimiters: [{left: "$$", right: "$$", display: true}, {left: "\\[", right: "\\]", display: true},
                {left: "\\(", right: "\\)", display: false}, {left: "$", right: "$", display: false}],
              ignoredTags: ["script", "style", "textarea", "pre", "code"], errorCallback: () => {}});
          } catch (_) { prose.textContent = piece; }
        }
      }
    }
  }
  function carry(section) {
    if (!section || !state.data || state.pid !== state.data.project_id) return;
    const request = ["参考已导入的《" + state.data.title + "》中“" + section.title + "”的讨论，继续处理当前稿件。",
      "本轮关注：" + section.summary,
      "请先结合当前已接受稿核对这些意见；历史候选和最新回复均不自动视为已采纳方案。",
      arr(section.questions).length ? "需要说明：\n" + section.questions.map((question, index) => (index + 1) + ". " + question).join("\n") : ""].filter(Boolean).join("\n\n");
    window.dispatchEvent(new CustomEvent("paper-context-discuss", {detail: {project_id: state.pid,
      section_id: section.id, title: section.title, message_ids: [...arr(section.message_ids)], request}}));
  }
  function renderMessages() {
    const container = document.getElementById("lssm-context-messages");
    const count = document.getElementById("lssm-context-count");
    if (!container || !count || !state.data) return;
    container.replaceChildren();
    const messages = arr(state.data.messages).filter(matches);
    count.textContent = (activeSection() ? "本主题" : "全部对话") + " · " + messages.length + " 条" + (messages.length > state.limit ? "，先显示 " + state.limit + " 条" : "");
    if (!messages.length) { container.append(node("p", "lssm-empty", "没有匹配的对话。可以清空搜索，或切换到全部对话。")); return; }
    for (const message of messages.slice(0, state.limit)) {
      const detail = node("details", "lssm-message " + (message.role === "user" ? "author" : "agent"));
      detail.dataset.messageId = message.id;
      const heading = node("summary", "lssm-message-summary"), meta = node("span", "lssm-message-role", message.role === "user" ? "你 · 修改要求" : "原对话 Agent · 建议");
      const position = [text(message.source), message.source_index != null ? "原消息 " + message.source_index : messageDate(message.created_at) || "导入记录"].filter(Boolean).join(" · ");
      heading.append(meta, node("small", "lssm-message-source", position + (message.truncated ? " · 原文不完整" : "")), node("span", "lssm-message-preview", preview(message)));
      detail.append(heading);
      let mounted = false;
      detail.addEventListener("toggle", () => {
        if (!detail.open || mounted) return;
        mounted = true;
        const body = node("div", "lssm-message-body"); renderText(body, message.text);
        if (message.truncated) body.prepend(node("p", "lssm-warning", "本条原回复未完整读取。这里只展示实际读取的部分，不能据此认定已读完该版本。"));
        detail.append(body);
      });
      container.append(detail);
    }
    if (messages.length > state.limit) container.append(button("再显示 16 条", () => {state.limit += 16; renderMessages();}, "ol-secondary lssm-more"));
  }
  function selectSection(id) {
    if (!state.data) return;
    state.selected = sections().some(section => section.id === id) ? id : ""; state.limit = 16;
    render();
  }
  function render() {
    const root = host(); if (!root || !state.data) return;
    const data = state.data; root.replaceChildren();
    const heading = node("header", "lssm-heading"), title = node("div");
    title.append(node("strong", "", data.title || "导入的论文讨论"), node("small", "", "已导入的上下文 · 正文修订需单独审阅"));
    heading.append(title, button("刷新", () => load(state.pid, true))); root.append(heading);
    root.append(node("p", "lssm-coverage", coverageText(data)));
    const source = link(data.source_url);
    if (source) { const anchor = node("a", "lssm-source-link", "查看原始共享对话 ↗"); anchor.href = source; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; root.append(anchor); }
    if (data.summary) {
      const overview = node("details", "lssm-overview"); overview.append(node("summary", "", "整体梳理与版本边界"), node("p", "", data.summary)); root.append(overview);
    }
    if (data.coverage?.shared_snapshot || data.coverage?.thread_read) {
      const coverage = data.coverage, detail = node("details", "lssm-coverage-detail"); detail.append(node("summary", "", "读取范围与来源限制"), node("p", "", [text(coverage.shared_snapshot?.scope), text(coverage.thread_read?.scope), text(coverage.thread_read?.truncation_notice), text(coverage.notice)].filter(Boolean).join("\n"))); root.append(detail);
    }
    const topics = node("nav", "lssm-topics"); topics.setAttribute("aria-label", "LSSM讨论主题");
    const all = button("全部对话 · " + arr(data.messages).length + " 条", () => selectSection(""), "lssm-topic-all");
    all.setAttribute("aria-pressed", String(!state.selected)); topics.append(all);
    for (const section of sections()) {
      const item = button("", () => selectSection(section.id), "lssm-topic"); item.dataset.sectionId = section.id;
      item.setAttribute("aria-pressed", String(state.selected === section.id));
      item.append(node("span", "lssm-tag " + String(section.status || "discussion").replace(/[^a-z_]/g, ""), statusLabel(section.status)), node("strong", "", section.title), node("small", "", String(arr(section.message_ids).length) + " 条相关记录")); topics.append(item);
    }
    root.append(topics);
    const section = activeSection();
    if (section) {
      const card = node("section", "lssm-topic-detail"); card.append(node("h3", "", section.title), node("p", "", section.summary));
      if (arr(section.questions).length) { const list = node("ul"); for (const question of section.questions) list.append(node("li", "", question)); card.append(list); }
      card.append(button("带入讨论", () => carry(section), "ol-primary"), node("small", "lssm-carry-note", "把本主题放入输入框，检查后发送。")); root.append(card);
    }
    const controls = node("div", "lssm-search"), search = node("input"); search.type = "search"; search.value = state.query; search.placeholder = "搜索原文、公式符号或修改意见"; search.setAttribute("aria-label", "搜索导入的LSSM对话");
    search.addEventListener("input", () => {state.query = search.value; state.limit = 16; renderMessages();});
    const roles = node("select"); roles.setAttribute("aria-label", "筛选对话角色");
    for (const [value, label] of [["all", "全部角色"], ["user", "只看你的要求"], ["assistant", "只看 Agent 建议"]]) {const option = node("option", "", label); option.value = value; roles.append(option);}
    roles.value = state.role; roles.addEventListener("change", () => {state.role = roles.value; state.limit = 16; renderMessages();});
    controls.append(search, roles); root.append(controls);
    const count = node("p", "lssm-result-count"); count.id = "lssm-context-count"; count.setAttribute("role", "status");
    const messages = node("div", "lssm-messages"); messages.id = "lssm-context-messages"; root.append(count, messages); renderMessages();
  }
  function notice(message, retry = false) {
    const root = host(); if (!root) return;
    root.replaceChildren(node("p", "lssm-empty", message));
    if (retry) root.append(button("重新读取", () => load(state.pid, true)));
  }
  async function load(pid, force = false) {
    pid = String(pid || "");
    if (pid === state.pid && !force) {
      if (state.data) {if (host() && !document.getElementById("lssm-context-messages")) render(); return;}
      if (state.pending) return state.pending;
    }
    const serial = ++state.serial; state.pid = pid; state.data = null; state.selected = ""; state.query = ""; state.role = "all"; state.limit = 16; state.pending = null;
    if (!pid) { notice("请先选择课题，再查看已导入的讨论。"); return; }
    if (!host()) return;
    if (!force && state.cache.has(pid)) {state.data = state.cache.get(pid); render(); return;}
    notice("正在读取本课题的讨论记录…");
    const task = (async () => {
      try {
        const response = await fetch("/api/source/" + encodeURIComponent(pid) + "/discussion-context", {cache: "no-store"});
        if (serial !== state.serial || state.pid !== pid) return;
        if (response.status === 404) {notice("当前课题尚未导入讨论记录。"); return;}
        const data = await response.json();
        if (serial !== state.serial || state.pid !== pid) return;
        if (!response.ok) throw Error(data.error || "讨论记录暂时无法读取。");
        if (data.project_id !== pid || !Array.isArray(data.sections) || !Array.isArray(data.messages)) throw Error("讨论记录与当前课题不匹配，未加载。");
        state.data = data; state.cache.set(pid, data); render();
      } catch (error) {if (serial === state.serial && state.pid === pid) notice(error.message || "讨论记录读取失败，请重试。", true);}
      finally {if (serial === state.serial) state.pending = null;}
    })();
    state.pending = task; return task;
  }
  window.paperDiscussionContext = Object.freeze({load, openTopic: selectSection});
  window.addEventListener("paper-project-changed", event => {load(event.detail?.project_id || "");});
})();
