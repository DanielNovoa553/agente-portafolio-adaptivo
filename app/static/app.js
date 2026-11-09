"use strict";

const $ = (id) => document.getElementById(id);
const MIN_JOB = 50;

const PRIVACY_VERSION = "2";
const CONSENT_KEY = "privacyConsent";

const state = {
  file: null,
  markdown: "",
  outputType: "cv",
  stepTimer: null,
  jobText: "",
  document: null,
  documentType: null,
  evidence: [],
  retrievedEvidence: [],
  interview: {
    sessionId: null,
    roleTitle: "",
    questions: [],
    current: 0,
    answered: [],
    total: 0,
    nextQuestionId: null,
    expiresAt: null,
  },
};

function hasConsent() {
  return localStorage.getItem(CONSENT_KEY) === PRIVACY_VERSION;
}

/* ---------- Utilidades ---------- */

function el(tag, { cls, text, attrs } = {}, children = []) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  if (attrs) Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  children.forEach((c) => c && node.append(c));
  return node;
}

function toast(message, kind = "ok") {
  const t = $("toast");
  t.className = `toast align-items-center border-0 ${kind}`;
  $("toastBody").textContent = message;
  bootstrap.Toast.getOrCreateInstance(t, { delay: 3500 }).show();
}

function setLoading(btn, loading) {
  btn.classList.toggle("loading", loading);
  btn.disabled = loading || btn.dataset.forceDisabled === "1";
}

function errorMessage(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg).join(" · ");
  if (status === 429) return "Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.";
  return `Error inesperado (${status}).`;
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), "X-Privacy-Consent": hasConsent() ? PRIVACY_VERSION : "" },
    });
  } catch {
    throw new Error("No se pudo conectar con el servidor.");
  }
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* respuesta sin JSON */
  }
  if (!res.ok) {
    const err = new Error(errorMessage(data, res.status));
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------- Tema ---------- */

function applyTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  $("themeToggle").innerHTML =
    theme === "dark" ? '<i class="bi bi-sun"></i>' : '<i class="bi bi-moon-stars"></i>';
  localStorage.setItem("theme", theme);
}

function initTheme() {
  const saved = localStorage.getItem("theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
  $("themeToggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-bs-theme");
    applyTheme(current === "dark" ? "light" : "dark");
  });
}

/* ---------- Estado de la API ---------- */

async function checkHealth() {
  const pill = $("apiStatus");
  try {
    await api("/health");
    pill.className = "badge rounded-pill status-pill online";
    $("apiStatusText").textContent = "API en línea";
  } catch {
    pill.className = "badge rounded-pill status-pill offline";
    $("apiStatusText").textContent = "Sin conexión";
  }
}

/* ---------- Perfil: subida y fuentes ---------- */

function setFile(file) {
  state.file = file;
  $("fileName").textContent = file ? file.name : "Ningún archivo seleccionado";
  $("dropzone").classList.toggle("has-file", !!file);
  $("uploadBtn").disabled = !file;
}

function initDropzone() {
  const zone = $("dropzone");
  const input = $("fileInput");
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => setFile(input.files[0] || null));
  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });
}

async function uploadFile() {
  if (!state.file) return;
  const form = new FormData();
  form.append("file", state.file);
  form.append("doc_type", $("docType").value);
  const btn = $("uploadBtn");
  setLoading(btn, true);
  try {
    const r = await api("/ingest", { method: "POST", body: form });
    toast(`"${r.source}" indexado (${r.chunks} fragmentos)`);
    setFile(null);
    $("fileInput").value = "";
    await loadSources();
  } catch (e) {
    toast(e.message, "err");
  } finally {
    setLoading(btn, false);
    btn.disabled = !state.file;
  }
}

async function loadSources() {
  try {
    const data = await api("/sources");
    renderSources(data);
  } catch (e) {
    toast(e.message, "err");
  }
}

function renderSources({ sources, total_chunks }) {
  const list = $("sourcesList");
  list.replaceChildren();
  const names = Object.keys(sources);
  $("sourcesEmpty").hidden = names.length > 0;
  $("chunkCount").textContent = `${total_chunks} fragmentos`;
  names.forEach((name) => {
    const remove = el("button", {
      cls: "remove",
      attrs: { type: "button", "aria-label": `Eliminar ${name}`, title: "Eliminar" },
    });
    remove.append(el("i", { cls: "bi bi-trash3" }));
    remove.addEventListener("click", () => removeSource(name));
    list.append(
      el("li", {}, [
        el("i", { cls: "bi bi-file-earmark-text text-primary" }),
        el("span", { cls: "name", text: name, attrs: { title: name } }),
        el("span", { cls: "meta", text: `${sources[name]} frag.` }),
        remove,
      ])
    );
  });
  updateGenerateState();
}

async function removeSource(name) {
  try {
    await api(`/sources/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast(`"${name}" eliminado`);
    await loadSources();
  } catch (e) {
    toast(e.message, "err");
  }
}

/* ---------- Oferta ---------- */

function updateGenerateState() {
  const len = $("jobText").value.trim().length;
  $("jobCounter").textContent = `${$("jobText").value.length} / 8000`;
  const ready = len >= MIN_JOB;
  $("jobHint").textContent = ready ? "Listo para generar" : `Mínimo ${MIN_JOB} caracteres`;
  const btn = $("generateBtn");
  btn.dataset.forceDisabled = ready ? "0" : "1";
  if (!btn.classList.contains("loading")) btn.disabled = !ready;
}

/* ---------- Estados del panel de resultado ---------- */

function showPanel(name) {
  $("emptyState").hidden = name !== "empty";
  $("loadingState").hidden = name !== "loading";
  $("errorBox").hidden = name !== "error";
  $("documentView").hidden = name !== "document";
  $("resultActions").hidden = name !== "document";
}

function startLoadingSteps() {
  const items = [...$("loadingSteps").children];
  let i = 0;
  const paint = () => {
    items.forEach((li, idx) => {
      li.classList.toggle("done", idx < i);
      li.classList.toggle("active", idx === i);
    });
  };
  paint();
  state.stepTimer = setInterval(() => {
    if (i < items.length - 1) {
      i += 1;
      paint();
    }
  }, 6000);
}

function stopLoadingSteps() {
  clearInterval(state.stepTimer);
  state.stepTimer = null;
}

/* ---------- Render de documentos (solo textContent: sin inyección HTML) ---------- */

function section(label, ...nodes) {
  return el("div", { cls: "doc-section" }, [el("div", { cls: "doc-label", text: label }), ...nodes]);
}

function cite(ids) {
  const wrap = el("div", { cls: "cite", text: "Respaldado por: " });
  ids.forEach((id, i) => {
    wrap.append(el("code", { text: id }));
    if (i < ids.length - 1) wrap.append(document.createTextNode(" "));
  });
  return wrap;
}

function renderCv(doc) {
  const root = el("div", {}, [
    el("h2", { cls: "doc-title", text: doc.headline }),
    section("Resumen", el("p", { text: doc.summary })),
    section(
      "Habilidades",
      el("div", {}, doc.skills.map((s) => el("span", { cls: "skill-chip", text: s })))
    ),
  ]);
  if (doc.experience.length) {
    root.append(
      section(
        "Experiencia",
        ...doc.experience.map((x) =>
          el("div", { cls: "exp-item" }, [
            el("p", { cls: "role", text: x.role }),
            el("div", { cls: "company", text: x.company }),
            el("ul", {}, x.bullets.map((b) => el("li", { text: b }))),
            cite(x.evidence_ids),
          ])
        )
      )
    );
  }
  return root;
}

function renderProjects(doc) {
  const items = [...doc.projects].sort((a, b) => b.relevance_score - a.relevance_score);
  return el("div", {}, [
    el("h2", { cls: "doc-title", text: "Proyectos priorizados" }),
    ...items.map((p) => {
      const pct = Math.round(p.relevance_score * 100);
      const bar = el("div", { cls: "progress-bar", attrs: { style: `width:${pct}%` } });
      return el("div", { cls: "project-card" }, [
        el("div", { cls: "head" }, [
          el("h3", { text: p.name }),
          el("span", { cls: "score", text: `${pct}%` }),
        ]),
        el("div", { cls: "progress", attrs: { role: "progressbar", "aria-valuenow": pct } }, [bar]),
        el("p", { cls: "mb-0", text: p.why_relevant }),
        cite(p.evidence_ids),
      ]);
    }),
  ]);
}

function renderLetter(doc) {
  return el("div", { cls: "letter" }, [
    el("h2", { cls: "doc-title", text: "Carta de presentación" }),
    el("p", { cls: "fw-semibold mt-3", text: doc.greeting }),
    ...doc.body.map((p) => el("p", { text: p })),
    el("p", { text: doc.closing }),
    cite(doc.evidence_ids),
  ]);
}

const RENDERERS = { cv: renderCv, projects: renderProjects, cover_letter: renderLetter };

/* ---------- Markdown para copiar / descargar ---------- */

function toMarkdown(type, doc) {
  const lines = [];
  if (type === "cv") {
    lines.push(`# ${doc.headline}`, "", "## Resumen", doc.summary, "", "## Habilidades", doc.skills.join(" · "));
    if (doc.experience.length) {
      lines.push("", "## Experiencia");
      doc.experience.forEach((x) => {
        lines.push("", `### ${x.role} — ${x.company}`);
        x.bullets.forEach((b) => lines.push(`- ${b}`));
      });
    }
  } else if (type === "projects") {
    lines.push("# Proyectos priorizados");
    [...doc.projects]
      .sort((a, b) => b.relevance_score - a.relevance_score)
      .forEach((p) => {
        lines.push("", `## ${p.name} (${Math.round(p.relevance_score * 100)}%)`, p.why_relevant);
      });
  } else {
    lines.push(doc.greeting, "");
    doc.body.forEach((p) => lines.push(p, ""));
    lines.push(doc.closing);
  }
  return lines.join("\n") + "\n";
}

/* ---------- Evidencias ---------- */

const TYPE_LABEL = { cv: "CV", project: "Proyecto", certification: "Certificación", other: "Otro" };

function renderEvidence(evidence) {
  const list = $("evidenceList");
  list.replaceChildren();
  $("evidenceBadge").textContent = evidence.length;
  $("evidenceEmpty").hidden = evidence.length > 0;
  evidence.forEach((e) => {
    list.append(
      el("div", { cls: "evidence-item" }, [
        el("div", { cls: "top" }, [
          el("span", { cls: "id", text: e.id }),
          el("span", { cls: "sim", text: `${Math.round(e.score * 100)}% similitud` }),
        ]),
        el("div", { cls: "tags mb-2" }, [
          el("span", { cls: "tag", text: TYPE_LABEL[e.doc_type] || e.doc_type }),
          e.section ? el("span", { cls: "tag", text: e.section }) : null,
        ]),
        el("p", { cls: "text", text: e.text }),
      ])
    );
  });
}

/* ---------- Entrevista ---------- */

const CATEGORY_LABEL = {
  technical: "Técnica",
  project: "Proyecto",
  behavioral: "Conductual",
  role_fit: "Encaje",
  gap: "Gap",
};
const DIFFICULTY_LABEL = { easy: "Fácil", medium: "Media", hard: "Difícil" };
const VERDICT_LABEL = { strong: "Sólida", partial: "Parcial", weak: "A mejorar" };
const CRITERION_LABEL = {
  relevance: "Relevancia",
  evidence_alignment: "Alineación con la evidencia",
  specificity: "Especificidad",
  structure: "Estructura",
  technical_depth: "Profundidad técnica",
};

let suggestionSeq = 0;

function emptyInterview() {
  return {
    sessionId: null,
    roleTitle: "",
    questions: [],
    current: 0,
    answered: [],
    total: 0,
    nextQuestionId: null,
    expiresAt: null,
  };
}

function showInterviewState(name) {
  $("interviewIntro").hidden = name !== "intro";
  $("interviewLoading").hidden = name !== "loading";
  $("interviewError").hidden = name !== "error";
  $("interviewActive").hidden = name !== "active";
  $("interviewSummary").hidden = name !== "summary";
}

function updateInterviewAvailability() {
  const enabled = Boolean(state.document);
  const tab = $("interviewTab");
  tab.classList.toggle("disabled", !enabled);
  tab.setAttribute("aria-disabled", String(!enabled));
  tab.title = enabled ? "" : "Genera un documento primero";
  $("startInterviewBtn").disabled = !enabled;
  $("interviewIntroHint").hidden = enabled;
}

function setInterviewBadge() {
  const iv = state.interview;
  const total = iv.questions.length || iv.total || 0;
  $("interviewBadge").textContent = `${iv.answered.length}/${total}`;
}

function resetInterview({ deleteSession = true } = {}) {
  const sessionId = state.interview.sessionId;
  if (deleteSession && sessionId) {
    api(`/interview/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }).catch(() => {});
  }
  state.interview = emptyInterview();
  $("interviewFeedback").replaceChildren();
  $("interviewFeedback").hidden = true;
  $("interviewSummary").replaceChildren();
  $("interviewAnswerError").hidden = true;
  $("interviewNav").hidden = true;
  $("answerText").value = "";
  $("answerText").readOnly = false;
  $("answerCounter").textContent = "0 / 5000";
  $("evaluateAnswerBtn").disabled = true;
  clearSuggestion();
  setInterviewBadge();
  showInterviewState("intro");
  updateInterviewAvailability();
}

function handleInterviewError(error) {
  if (error.status === 404) {
    resetInterview({ deleteSession: false });
    toast("La sesión expiró. Inicia una nueva entrevista.", "err");
    return true;
  }
  return false;
}

function updateAnswerState() {
  const value = $("answerText").value;
  $("answerCounter").textContent = `${value.length} / 5000`;
  $("evaluateAnswerBtn").disabled = !value.trim();
}

function clearSuggestion() {
  suggestionSeq += 1;
  const card = $("interviewSuggestion");
  card.replaceChildren();
  card.hidden = true;
  setLoading($("suggestAnswerBtn"), false);
}

function suggestionBlock(title, icon, items, cls = "", ordered = false) {
  if (!items || !items.length) return null;
  return el("div", { cls: `suggestion-block ${cls}`.trim() }, [
    el("h5", {}, [
      el("i", { cls: `bi ${icon} me-1` }),
      document.createTextNode(title),
    ]),
    el(ordered ? "ol" : "ul", { cls: "mb-0" }, items.map((t) => el("li", { text: t }))),
  ]);
}

function renderSuggestion(data) {
  const blocks = [
    el("div", { cls: "suggestion-head" }, [
      el("h4", {}, [
        el("i", { cls: "bi bi-stars me-1" }),
        document.createTextNode("Borrador de respuesta"),
      ]),
      el("span", { cls: "suggestion-ai", text: "Generado por IA" }),
    ]),
    el("p", {
      cls: "suggestion-notice",
      text: "Borrador generado por IA. Revísalo y ajústalo antes de usarlo.",
    }),
    el("p", { cls: "suggestion-answer", text: data.answer }),
    suggestionBlock("Esquema", "bi-list-ol", data.outline, "", true),
    suggestionBlock(
      "Avisos para no inventar",
      "bi-exclamation-triangle",
      data.caveats,
      "warn"
    ),
    suggestionBlock(
      "Notas de preparación",
      "bi-journal-check",
      data.preparation_notes,
      "ok"
    ),
  ].filter(Boolean);

  if (data.evidence.length) {
    const chips = el("div", { cls: "interview-evidence-chips" });
    data.evidence.forEach((e) => {
      const chip = el("span", { cls: "interview-evidence-chip", text: e.id });
      chip.title = e.text;
      chips.append(chip);
    });
    blocks.push(
      el("div", { cls: "suggestion-block" }, [
        el("h5", {}, [
          el("i", { cls: "bi bi-link-45deg me-1" }),
          document.createTextNode("Evidencias citadas"),
        ]),
        chips,
      ])
    );
  }

  const actions = el("div", { cls: "d-flex gap-2 flex-wrap mt-3" });
  const useBtn = el("button", { cls: "btn btn-sm btn-primary", attrs: { type: "button" } }, [
    el("i", { cls: "bi bi-arrow-down-square me-1" }),
    document.createTextNode("Usar borrador"),
  ]);
  useBtn.addEventListener("click", () => useSuggestion(data.answer));
  const copyBtn = el("button", {
    cls: "btn btn-sm btn-outline-secondary",
    attrs: { type: "button" },
  }, [
    el("i", { cls: "bi bi-clipboard me-1" }),
    document.createTextNode("Copiar"),
  ]);
  copyBtn.addEventListener("click", () => copySuggestion(data.answer));
  actions.append(useBtn, copyBtn);
  blocks.push(actions);

  const card = $("interviewSuggestion");
  card.replaceChildren(...blocks);
  card.hidden = false;
}

async function suggestAnswer() {
  const iv = state.interview;
  const index = iv.current;
  const sessionId = iv.sessionId;
  const question = iv.questions[index];
  if (!sessionId || !question) return;
  const btn = $("suggestAnswerBtn");
  if (btn.disabled) return;

  const seq = suggestionSeq + 1;
  suggestionSeq = seq;
  setLoading(btn, true);
  $("interviewAnswerError").hidden = true;
  try {
    const data = await api(
      `/interview/sessions/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(question.id)}/suggestion`,
      { method: "POST" }
    );
    if (seq !== suggestionSeq || iv.sessionId !== sessionId || iv.current !== index) return;
    if (iv.answered.includes(question.id) || data.question_id !== question.id) return;
    renderSuggestion(data);
  } catch (e) {
    if (seq !== suggestionSeq) return;
    if (handleInterviewError(e)) return;
    $("interviewAnswerErrorText").textContent = e.message;
    $("interviewAnswerError").hidden = false;
  } finally {
    if (seq === suggestionSeq) setLoading(btn, false);
  }
}

function useSuggestion(text) {
  const area = $("answerText");
  if (
    area.value.trim() &&
    !window.confirm("Ya hay texto en tu respuesta. ¿Quieres reemplazarlo con el borrador?")
  ) {
    return;
  }
  area.value = text;
  updateAnswerState();
  area.focus();
  area.setSelectionRange(text.length, text.length);
  area.scrollIntoView({ behavior: "smooth", block: "center" });
  toast("Borrador insertado. Revísalo y ajústalo antes de evaluar.");
}

async function copySuggestion(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Borrador copiado al portapapeles");
  } catch {
    toast("No se pudo copiar el borrador", "err");
  }
}

function renderInterviewEvidence(ids) {
  const box = $("interviewEvidenceChips");
  box.replaceChildren();
  const all = state.retrievedEvidence.length ? state.retrievedEvidence : state.evidence;
  ids.forEach((id) => {
    const ev = all.find((e) => e.id === id);
    const chip = el("span", { cls: "interview-evidence-chip", text: id });
    if (ev) chip.title = ev.text;
    box.append(chip);
  });
  $("interviewEvidenceDetails").hidden = ids.length === 0;
  if (!ids.length) $("interviewEvidenceDetails").open = false;
}

function showQuestion(index) {
  const iv = state.interview;
  const question = iv.questions[index];
  if (!question) return;
  iv.current = index;
  $("interviewProgress").textContent = `Pregunta ${index + 1} de ${iv.total}`;
  $("interviewCategory").textContent = CATEGORY_LABEL[question.category] || question.category;
  $("interviewCategory").className = `badge interview-cat cat-${question.category}`;
  $("interviewDifficulty").textContent =
    DIFFICULTY_LABEL[question.difficulty] || question.difficulty;
  $("interviewDifficulty").className = `badge interview-diff diff-${question.difficulty}`;
  $("interviewQuestionText").textContent = question.question;
  $("interviewWhy").textContent = question.why_asked;
  renderInterviewEvidence(question.evidence_ids || []);
  $("interviewAnswerError").hidden = true;
  $("interviewFeedback").replaceChildren();
  $("interviewFeedback").hidden = true;
  $("interviewNav").hidden = true;
  $("answerForm").hidden = false;
  $("answerText").readOnly = false;
  $("answerText").value = "";
  clearSuggestion();
  updateAnswerState();
  showInterviewState("active");
  $("answerText").focus();
}

async function startInterview() {
  if (!state.document) {
    updateInterviewAvailability();
    return;
  }
  const btn = $("interviewError").hidden ? $("startInterviewBtn") : $("interviewRetryBtn");
  setLoading(btn, true);
  showInterviewState("loading");
  try {
    const model = $("modelSelect").value;
    const body = {
      job_text: state.jobText || $("jobText").value.trim(),
      output_type: state.documentType || state.outputType,
      document: state.document,
      evidence: state.retrievedEvidence.length ? state.retrievedEvidence : state.evidence,
      question_count: Number($("questionCount").value),
    };
    const data = await api(
      `/interview/sessions${model ? `?model=${encodeURIComponent(model)}` : ""}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    const iv = state.interview;
    iv.sessionId = data.session_id;
    iv.roleTitle = data.role_title;
    iv.questions = data.questions;
    iv.total = data.questions.length;
    iv.current = 0;
    iv.answered = [];
    iv.nextQuestionId = null;
    iv.expiresAt = data.expires_at;
    setInterviewBadge();
    showQuestion(0);
    toast(`Entrevista preparada: ${data.questions.length} preguntas`);
  } catch (e) {
    if (!handleInterviewError(e)) {
      $("interviewErrorText").textContent = e.message;
      showInterviewState("error");
    }
  } finally {
    setLoading(btn, false);
    updateInterviewAvailability();
  }
}

function scoreBadge(score, verdict, answered, total) {
  return el("div", { cls: `interview-verdict verdict-${verdict}` }, [
    el("div", { cls: "verdict-score" }, [
      el("span", { cls: "num", text: String(score) }),
      el("span", { cls: "of", text: "/100" }),
    ]),
    el("div", {}, [
      el("div", { cls: "verdict-label", text: VERDICT_LABEL[verdict] || verdict }),
      el("div", {
        cls: "small text-body-secondary",
        text: `${answered} de ${total} preguntas evaluadas`,
      }),
    ]),
  ]);
}

function criterionBars(criteria) {
  return el(
    "div",
    { cls: "interview-criteria" },
    criteria.map((c) => {
      const pct = Math.round((c.score / 5) * 100);
      return el("div", { cls: "criterion" }, [
        el("div", { cls: "criterion-head" }, [
          el("span", { text: CRITERION_LABEL[c.criterion] || c.criterion }),
          el("span", { cls: "criterion-score", text: `${c.score}/5` }),
        ]),
        el(
          "div",
          {
            cls: "progress",
            attrs: {
              role: "progressbar",
              "aria-valuenow": pct,
              "aria-valuemin": 0,
              "aria-valuemax": 100,
            },
          },
          [el("div", { cls: "progress-bar", attrs: { style: `width:${pct}%` } })]
        ),
        el("p", { cls: "criterion-comment", text: c.comment }),
      ]);
    })
  );
}

function feedbackBlock(title, items, icon, cls = "", ordered = false) {
  return el("div", { cls: `feedback-block ${cls}`.trim() }, [
    el("h4", {}, [
      el("i", { cls: `bi ${icon} me-1` }),
      document.createTextNode(title),
    ]),
    el(ordered ? "ol" : "ul", { cls: "mb-0" }, items.map((t) => el("li", { text: t }))),
  ]);
}

function evidenceChips(evidence) {
  const chips = el("div", { cls: "interview-evidence-chips" });
  evidence.forEach((e) => {
    const chip = el("span", { cls: "interview-evidence-chip", text: e.id });
    chip.title = e.text;
    chips.append(chip);
  });
  return chips;
}

function renderFeedback(data) {
  const nodes = [
    scoreBadge(data.score, data.verdict, data.answered, data.total),
    criterionBars(data.criteria),
    el("div", { cls: "feedback-grid" }, [
      feedbackBlock("Fortalezas", data.strengths, "bi-check-circle", "ok"),
      feedbackBlock("Carencias", data.gaps, "bi-arrow-up-right-circle", "warn"),
    ]),
  ];
  if (data.unsupported_claims.length) {
    nodes.push(
      feedbackBlock(
        "Afirmaciones no respaldadas por el material",
        data.unsupported_claims,
        "bi-exclamation-triangle",
        "flag"
      )
    );
  }
  nodes.push(
    feedbackBlock("Esquema sugerido", data.suggested_outline, "bi-list-ol", "", true)
  );
  if (data.evidence.length) {
    nodes.push(
      el("div", { cls: "feedback-block" }, [
        el("h4", {}, [
          el("i", { cls: "bi bi-link-45deg me-1" }),
          document.createTextNode("Evidencias citadas"),
        ]),
        evidenceChips(data.evidence),
      ])
    );
  }
  $("interviewFeedback").replaceChildren(...nodes);
}

async function submitInterviewAnswer() {
  const iv = state.interview;
  const question = iv.questions[iv.current];
  const answer = $("answerText").value.trim();
  if (!question || !answer) return;

  const btn = $("evaluateAnswerBtn");
  setLoading(btn, true);
  $("answerText").readOnly = true;
  $("interviewAnswerError").hidden = true;
  try {
    const data = await api(
      `/interview/sessions/${encodeURIComponent(iv.sessionId)}/answers`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: question.id, answer }),
      }
    );
    if (!iv.answered.includes(question.id)) iv.answered.push(question.id);
    iv.nextQuestionId = data.next_question_id;
    renderFeedback(data);
    $("answerForm").hidden = true;
    clearSuggestion();
    $("interviewFeedback").hidden = false;
    $("interviewNav").hidden = false;
    $("nextQuestionBtn").hidden = !data.next_question_id;
    $("summaryBtn").classList.toggle("btn-primary", !data.next_question_id);
    $("summaryBtn").classList.toggle("btn-outline-primary", Boolean(data.next_question_id));
    setInterviewBadge();
  } catch (e) {
    if (handleInterviewError(e)) return;
    $("interviewAnswerErrorText").textContent = e.message;
    $("interviewAnswerError").hidden = false;
  } finally {
    setLoading(btn, false);
    $("answerText").readOnly = false;
    updateAnswerState();
  }
}

function nextQuestion() {
  const iv = state.interview;
  const index = iv.questions.findIndex((q) => q.id === iv.nextQuestionId);
  if (index >= 0) showQuestion(index);
}

async function showInterviewSummary() {
  const iv = state.interview;
  if (!iv.sessionId) return;
  const btn = $("summaryBtn");
  setLoading(btn, true);
  try {
    const data = await api(
      `/interview/sessions/${encodeURIComponent(iv.sessionId)}/summary`
    );
    renderSummary(data);
    showInterviewState("summary");
  } catch (e) {
    if (!handleInterviewError(e)) {
      $("interviewAnswerErrorText").textContent = e.message;
      $("interviewAnswerError").hidden = false;
    }
  } finally {
    setLoading(btn, false);
  }
}

function renderSummary(data) {
  const root = $("interviewSummary");
  const verdict =
    data.overall_score >= 80 ? "strong" : data.overall_score >= 60 ? "partial" : "weak";
  const nodes = [
    scoreBadge(data.overall_score, verdict, data.answered, data.total),
    el("p", {
      cls: "text-body-secondary small mt-2",
      text: data.completed
        ? "Entrevista completada."
        : `Has respondido ${data.answered} de ${data.total} preguntas.`,
    }),
  ];

  const byCategory = Object.entries(data.by_category);
  if (byCategory.length) {
    const bars = el(
      "div",
      { cls: "interview-criteria" },
      byCategory.map(([category, score]) => {
        const pct = Math.max(0, Math.min(100, score));
        return el("div", { cls: "criterion" }, [
          el("div", { cls: "criterion-head" }, [
            el("span", { text: CATEGORY_LABEL[category] || category }),
            el("span", { cls: "criterion-score", text: `${score}/100` }),
          ]),
          el("div", { cls: "progress" }, [
            el("div", { cls: "progress-bar", attrs: { style: `width:${pct}%` } }),
          ]),
        ]);
      })
    );
    nodes.push(
      el("div", { cls: "feedback-block" }, [
        el("h4", {}, [
          el("i", { cls: "bi bi-bar-chart me-1" }),
          document.createTextNode("Media por categoría"),
        ]),
        bars,
      ])
    );
  }

  nodes.push(
    el("div", { cls: "feedback-grid" }, [
      feedbackBlock("Fortalezas recurrentes", data.top_strengths, "bi-check-circle", "ok"),
      feedbackBlock("Carencias recurrentes", data.top_gaps, "bi-arrow-up-right-circle", "warn"),
    ]),
    feedbackBlock("Próximos pasos", data.next_steps, "bi-signpost-split")
  );

  if (data.unanswered_question_ids.length) {
    nodes.push(
      el("p", {
        cls: "small text-body-secondary",
        text: `Preguntas sin responder: ${data.unanswered_question_ids.join(" · ")}`,
      })
    );
  }

  const actions = el("div", { cls: "d-flex gap-2 mt-4 flex-wrap" });
  if (data.unanswered_question_ids.length) {
    const resume = el("button", {
      cls: "btn btn-primary",
      text: "Continuar entrevista",
      attrs: { type: "button" },
    });
    resume.addEventListener("click", () => {
      const index = state.interview.questions.findIndex(
        (q) => q.id === data.unanswered_question_ids[0]
      );
      if (index >= 0) showQuestion(index);
    });
    actions.append(resume);
  }
  const again = el("button", {
    cls: "btn btn-outline-primary",
    text: "Nueva entrevista",
    attrs: { type: "button" },
  });
  again.addEventListener("click", () => resetInterview());
  const finish = el("button", {
    cls: "btn btn-outline-secondary",
    text: "Terminar",
    attrs: { type: "button" },
  });
  finish.addEventListener("click", finishInterview);
  actions.append(again, finish);
  nodes.push(actions);

  root.replaceChildren(el("div", {}, nodes));
}

async function finishInterview() {
  const sessionId = state.interview.sessionId;
  if (sessionId) {
    try {
      await api(`/interview/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    } catch {
      /* la sesión pudo expirar */
    }
  }
  resetInterview({ deleteSession: false });
  toast("Entrevista terminada");
}

/* ---------- Generación ---------- */

async function generate() {
  const type = document.querySelector('input[name="outputType"]:checked').value;
  const btn = $("generateBtn");
  setLoading(btn, true);
  showPanel("loading");
  startLoadingSteps();
  bootstrap.Tab.getOrCreateInstance(document.querySelector('[data-bs-target="#tabDoc"]')).show();
  try {
    const model = $("modelSelect").value;
    const data = await api(`/generate${model ? `?model=${encodeURIComponent(model)}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_text: $("jobText").value.trim(), output_type: type }),
    });
    state.outputType = data.output_type;
    state.document = data.document;
    state.documentType = data.output_type;
    state.jobText = $("jobText").value.trim();
    state.evidence = data.evidence;
    state.retrievedEvidence =
      Array.isArray(data.retrieved_evidence) && data.retrieved_evidence.length
        ? data.retrieved_evidence
        : data.evidence;
    state.markdown = toMarkdown(data.output_type, data.document);
    const view = $("documentView");
    view.replaceChildren(RENDERERS[data.output_type](data.document));
    renderEvidence(data.evidence);
    resetInterview();
    showPanel("document");
    if (data.attempts > 1) toast(`Generado tras ${data.attempts} intentos de validación`);
  } catch (e) {
    $("errorText").textContent = e.message;
    showPanel("error");
  } finally {
    stopLoadingSteps();
    setLoading(btn, false);
    updateGenerateState();
  }
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(state.markdown);
    toast("Copiado al portapapeles");
  } catch {
    toast("No se pudo copiar", "err");
  }
}

function downloadResult() {
  const blob = new Blob([state.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { attrs: { href: url, download: `${state.outputType}.md` } });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ---------- Privacidad ---------- */

function setLocked(locked) {
  const main = document.querySelector("main");
  main.toggleAttribute("inert", locked);
  main.classList.toggle("locked", locked);
}

function initPrivacy() {
  const modalEl = $("privacyModal");
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  const check = $("privacyCheck");
  const open = () => {
    check.checked = false;
    $("privacyAccept").disabled = true;
    $("consentLock").hidden = true;
    modal.show();
  };

  check.addEventListener("change", () => ($("privacyAccept").disabled = !check.checked));
  $("privacyAccept").addEventListener("click", () => {
    localStorage.setItem(CONSENT_KEY, PRIVACY_VERSION);
    modal.hide();
    setLocked(false);
    loadModels();
    loadSources();
  });
  $("privacyDecline").addEventListener("click", () => {
    localStorage.removeItem(CONSENT_KEY);
    modal.hide();
    $("consentLock").hidden = false;
  });
  $("privacyReview").addEventListener("click", open);
  $("privacyOpen").addEventListener("click", () => {
    // Reabrirlo desde el pie equivale a revocar: hay que volver a aceptar
    localStorage.removeItem(CONSENT_KEY);
    setLocked(true);
    open();
  });

  if (!hasConsent()) {
    setLocked(true);
    open();
  }
}

/* ---------- Modelos ---------- */

async function loadModels() {
  const select = $("modelSelect");
  try {
    const data = await api("/models");
    const previous = localStorage.getItem("model");
    select.replaceChildren(
      ...data.models.map((m) => el("option", { text: m.name, attrs: { value: m.id } }))
    );
    const ids = data.models.map((m) => m.id);
    select.value = ids.includes(previous) ? previous : ids.includes(data.default) ? data.default : ids[0];
  } catch {
    select.replaceChildren(el("option", { text: "Modelo por defecto", attrs: { value: "" } }));
  }
}

/* ---------- Inicio ---------- */

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initDropzone();
  initPrivacy();
  $("modelSelect").addEventListener("change", (e) => localStorage.setItem("model", e.target.value));
  $("uploadBtn").addEventListener("click", uploadFile);
  $("generateBtn").addEventListener("click", generate);
  $("jobText").addEventListener("input", updateGenerateState);
  $("copyBtn").addEventListener("click", copyResult);
  $("downloadBtn").addEventListener("click", downloadResult);

  resetInterview({ deleteSession: false });
  $("startInterviewBtn").addEventListener("click", startInterview);
  $("interviewRetryBtn").addEventListener("click", startInterview);
  $("evaluateAnswerBtn").addEventListener("click", submitInterviewAnswer);
  $("suggestAnswerBtn").addEventListener("click", suggestAnswer);
  $("answerText").addEventListener("input", updateAnswerState);
  $("answerText").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      submitInterviewAnswer();
    }
  });
  $("nextQuestionBtn").addEventListener("click", nextQuestion);
  $("summaryBtn").addEventListener("click", showInterviewSummary);
  $("finishInterviewBtn").addEventListener("click", finishInterview);

  document.querySelectorAll('#resultTabs [data-bs-toggle="pill"]').forEach((tab) => {
    tab.addEventListener("shown.bs.tab", (event) => {
      const target = event.target.getAttribute("data-bs-target");
      if (target !== "#tabDoc") $("resultActions").hidden = true;
      else if (state.document) $("resultActions").hidden = false;
    });
  });

  updateGenerateState();
  checkHealth();
  if (hasConsent()) {
    loadModels();
    loadSources();
  }
  setInterval(checkHealth, 30000);
});
