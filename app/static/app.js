"use strict";

const $ = (id) => document.getElementById(id);
const MIN_JOB = 50;

const PRIVACY_VERSION = "1";
const CONSENT_KEY = "privacyConsent";

const state = { file: null, markdown: "", outputType: "cv", stepTimer: null };

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
  if (!res.ok) throw new Error(errorMessage(data, res.status));
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
    state.markdown = toMarkdown(data.output_type, data.document);
    const view = $("documentView");
    view.replaceChildren(RENDERERS[data.output_type](data.document));
    renderEvidence(data.evidence);
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
  updateGenerateState();
  checkHealth();
  if (hasConsent()) {
    loadModels();
    loadSources();
  }
  setInterval(checkHealth, 30000);
});
