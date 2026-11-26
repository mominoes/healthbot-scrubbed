// healthbot web UI. Plain JS, no build. All server text is inserted with textContent,
// except Markdown HTML rendered server-side by markdown-it with raw HTML disabled.
"use strict";

const $ = (id) => document.getElementById(id);
let busy = false;
let current = null;     // { el, text } of the assistant bubble being streamed
let pendingIngest = [];

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", ...opts };
  if (opts.body !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch (_) { /* not json */ }
  return { ok: r.ok, status: r.status, data };
}

function banner(text, kind = "warn") {
  const d = document.createElement("div");
  d.className = "banner " + kind;
  d.textContent = text;
  $("banners").appendChild(d);
}

// ---------------------------------------------------------------- status

async function loadStatus() {
  const { data } = await api("/api/status");
  if (!data) return;
  if (data.api_key_warning) banner(data.api_key_warning);
  if (!data.identity_present) banner(data.identity_message);
}

// ---------------------------------------------------------------- picker + scrub

function fmtCounts(c) {
  const parts = Object.entries(c || {}).map(([k, n]) => `${k}×${n}`);
  return parts.length ? parts.join(", ") : "—";
}

async function pick() {
  const btn = $("pick");
  btn.disabled = true;
  btn.textContent = "Waiting for folder dialog…";
  $("scrub-note").textContent = "The dialog may open behind this window.";
  try {
    const { ok, data } = await api("/api/pick", { method: "POST", body: {} });
    $("scrub-note").textContent = "";
    if (!data) { $("scrub-note").textContent = "Something went wrong."; return; }
    if (data.cancelled) { $("scrub-note").textContent = "Cancelled."; return; }
    if (!ok) { $("scrub-note").textContent = data.message || "Error."; return; }
    const tbody = $("scrub-results").querySelector("tbody");
    tbody.replaceChildren();
    for (const r of data.results) {
      const tr = document.createElement("tr");
      for (const v of [r.doc_id, r.status,
                       r.status === "ok" ? fmtCounts(r.counts)
                       : (r.findings && r.findings.length ? r.findings.join(", ") : "")]) {
        const td = document.createElement("td");
        td.textContent = v || "";
        tr.appendChild(td);
      }
      tr.className = "st-" + r.status;
      tbody.appendChild(tr);
    }
    $("scrub-results").hidden = data.results.length === 0;
    const notes = [];
    if (!data.results.length) notes.push("No PDFs found in that folder.");
    if (data.skipped_non_pdf) notes.push(`${data.skipped_non_pdf} non-PDF file(s) skipped.`);
    if (data.results.some((r) => !["ok", "duplicate"].includes(r.status)))
      notes.push("Quarantined files never reach Claude. needs_ocr = scanned page; " +
                 "encrypted = remove the password; leak = add the missing variant to " +
                 "identity.yaml in your editor; error = re-export the file.");
    $("scrub-note").textContent = notes.join(" ");
    pendingIngest = data.new_ok || [];
    const ib = $("ingest");
    ib.hidden = pendingIngest.length === 0;
    ib.textContent = `Ingest ${pendingIngest.length} new document${pendingIngest.length === 1 ? "" : "s"}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Choose folder…";
  }
}

function ingest() {
  if (!pendingIngest.length) return;
  const msg = `Ingest ${pendingIngest.join(", ")} using the ingest-records skill. ` +
              "They are already scrubbed; skip the scrub step.";
  pendingIngest = [];
  $("ingest").hidden = true;
  sendMessage(msg);
}

// ---------------------------------------------------------------- records viewer

async function loadRecords() {
  const { data } = await api("/api/records");
  if (!data) return;
  const views = $("views");
  views.replaceChildren();
  for (const v of data.views) {
    const b = document.createElement("button");
    b.className = "small";
    b.textContent = v;
    b.onclick = () => openView(v);
    views.appendChild(b);
  }
  const ul = $("records");
  ul.replaceChildren();
  for (const r of data.records.slice().reverse()) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#";
    a.textContent = `${r.date} · ${r.title}`;
    a.onclick = (e) => { e.preventDefault(); openRecord(r.id); };
    const t = document.createElement("span");
    t.className = "type";
    t.textContent = r.type;
    li.append(a, " ", t);
    ul.appendChild(li);
  }
  if (!data.records.length) {
    const li = document.createElement("li");
    li.textContent = "No records yet.";
    ul.appendChild(li);
  }
}

function showViewer(title, html) {
  $("viewer-title").textContent = title;
  $("viewer-body").innerHTML = html;  // server-rendered, raw HTML disabled
  $("viewer").hidden = false;
}

async function openRecord(id) {
  const { data } = await api(`/api/records/${encodeURIComponent(id)}`);
  if (data && data.html !== undefined)
    showViewer(`${data.date} · ${data.title} [${data.id}]`, data.html);
}

async function openView(name) {
  const { data } = await api(`/api/views/${encodeURIComponent(name)}`);
  if (data && data.html !== undefined) showViewer(name, data.html);
}

// ---------------------------------------------------------------- chat

function addMessage(cls, text) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.textContent = text;
  $("messages").appendChild(d);
  d.scrollIntoView({ block: "end" });
  return d;
}

async function finishBubble() {
  if (!current) return;
  const { el, text } = current;
  current = null;
  const { data } = await api("/api/render", { method: "POST", body: { text } });
  if (data && data.html !== undefined) {
    el.innerHTML = data.html;  // server-rendered, raw HTML disabled
    el.classList.add("md");
  }
}

function setBusy(b) {
  busy = b;
  $("send").disabled = b;
  $("stop").disabled = !b;
  $("pick").disabled = b;
}

async function sendMessage(text) {
  if (busy || !text.trim()) return;
  addMessage("user", text);
  setBusy(true);
  const { ok, data } = await api("/api/chat", { method: "POST", body: { message: text } });
  if (!ok) {
    addMessage("error", (data && data.error) || "Could not send.");
    setBusy(false);
  }
}

// Permission prompt for a tool call (WebSearch), the browser version of the terminal's
// "Do you want to proceed?". The query is shown verbatim so the user sees what leaves.
function addPermission(ev) {
  finishBubble();
  const box = document.createElement("div");
  box.className = "msg permission";
  box.dataset.id = ev.id;
  const label = document.createElement("div");
  label.textContent = `Claude wants to run ${ev.tool}. This sends the query below to an ` +
                      "outside search service:";
  const q = document.createElement("code");
  q.textContent = ev.summary;
  const btns = document.createElement("div");
  btns.className = "perm-buttons";
  const answer = async (allow) => {
    for (const b of btns.querySelectorAll("button")) b.disabled = true;
    const { ok } = await api("/api/chat/permission", { method: "POST",
                                                        body: { id: ev.id, allow } });
    if (!ok) settlePermission(ev.id, "No longer pending.");
  };
  const yes = document.createElement("button");
  yes.className = "allow";
  yes.textContent = "Allow";
  yes.onclick = () => answer(true);
  const no = document.createElement("button");
  no.textContent = "Deny";
  no.onclick = () => answer(false);
  btns.append(yes, no);
  box.append(label, q, btns);
  $("messages").appendChild(box);
  box.scrollIntoView({ block: "end" });
  yes.focus();
}

function settlePermission(id, text) {
  const sel = id ? `.permission[data-id="${CSS.escape(id)}"]` : ".permission:not(.settled)";
  for (const box of $("messages").querySelectorAll(sel)) {
    if (box.classList.contains("settled")) continue;
    box.classList.add("settled");
    const btns = box.querySelector(".perm-buttons");
    const s = document.createElement("div");
    s.className = "perm-status";
    s.textContent = text;
    btns.replaceWith(s);
  }
}

function onEvent(ev) {
  const d = $("messages");
  switch (ev.type) {
    case "text":
      if (!current) current = { el: addMessage("assistant", ""), text: "" };
      current.text += ev.text;
      current.el.textContent = current.text;
      current.el.scrollIntoView({ block: "end" });
      break;
    case "tool": {
      finishBubble();
      const t = addMessage("tool", "⚙ " + ev.summary);
      t.dataset.id = ev.id;
      break;
    }
    case "tool_status": {
      const t = d.querySelector(`.tool[data-id="${CSS.escape(ev.id)}"]`);
      if (t) t.classList.add(ev.status);
      break;
    }
    case "permission":
      addPermission(ev);
      break;
    case "permission_resolved":
      settlePermission(ev.id, ev.allowed ? "Allowed." : "Denied.");
      break;
    case "result": {
      finishBubble();
      const cost = typeof ev.cost_usd === "number" ? ` · $${ev.cost_usd.toFixed(4)}` : "";
      addMessage("meta", `${ev.is_error ? "ended with error" : "done"} · ${ev.turns} turn(s)${cost}`);
      loadRecords();
      break;
    }
    case "error":
      finishBubble();
      addMessage("error", ev.message);
      break;
    case "idle":
      finishBubble();
      settlePermission(null, "Denied (turn ended).");
      setBusy(false);
      break;
    case "reset":
      current = null;
      d.replaceChildren();
      setBusy(false);
      break;
  }
}

function connectStream() {
  const es = new EventSource("/api/chat/stream");
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (_) { /* ignore */ } };
}

// ---------------------------------------------------------------- wire up

$("pick").onclick = pick;
$("ingest").onclick = ingest;
$("refresh").onclick = loadRecords;
$("viewer-close").onclick = () => { $("viewer").hidden = true; };
$("stop").onclick = () => api("/api/chat/interrupt", { method: "POST", body: {} });
$("new-chat").onclick = () => api("/api/chat/new", { method: "POST", body: {} });
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const t = $("input").value;
  $("input").value = "";
  sendMessage(t);
};
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});

loadStatus();
loadRecords();
connectStream();
