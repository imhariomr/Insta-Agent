const EMPLOYEES = {
  Michael: { avatar: "🧑‍💼", role: "AI Manager", room: "#5aa6f7" },
  Alex: { avatar: "👨‍💻", role: "Engineer", room: "#34d399" },
  Emma: { avatar: "👩‍💼", role: "Marketing", room: "#f5b93d" },
  Ryan: { avatar: "🎬", role: "Editor", room: "#a855f7" },
  David: { avatar: "🧑‍🔬", role: "QA", room: "#4dd0d8" },
  Sophia: { avatar: "📱", role: "Social Media", room: "#ef6bb0" },
};

const AGENT_ICON = { Michael: "🧑‍💼", Alex: "⬇️", Emma: "✍️", Ryan: "🎬", David: "🔍", Sophia: "📤" };

const ACTIVE_STATUS_BY_EMPLOYEE = {
  Alex: "DOWNLOADING", Emma: "CAPTION_GENERATING", Ryan: "EDITING", David: "QA", Sophia: "PUBLISHING",
};

const ACTION_VERB = {
  Alex: "Downloading", Emma: "Writing caption for", Ryan: "Editing", David: "Reviewing", Sophia: "Publishing",
};

const AGENT_BY_STATUS = {
  QUEUED: "—", DOWNLOADING: "Alex", DOWNLOADED: "Alex", CAPTION_GENERATING: "Emma", CAPTION_READY: "Emma",
  EDITING: "Ryan", EDITED: "Ryan", QA: "David", QA_FAILED: "David", QA_PASSED: "David",
  WAITING_APPROVAL: "Michael", APPROVED: "Michael", PUBLISHING: "Sophia", PUBLISHED: "Sophia", FAILED: "—",
};

const KANBAN_COLUMNS = [
  { label: "Queued", statuses: ["QUEUED"] },
  { label: "Downloading", statuses: ["DOWNLOADING", "DOWNLOADED"] },
  { label: "Caption", statuses: ["CAPTION_GENERATING", "CAPTION_READY"] },
  { label: "Editing", statuses: ["EDITING", "EDITED"] },
  { label: "QA", statuses: ["QA", "QA_FAILED"] },
  { label: "Ready for Approval", statuses: ["QA_PASSED", "WAITING_APPROVAL", "APPROVED", "PUBLISHING"] },
  { label: "Published", statuses: ["PUBLISHED"] },
  { label: "Failed", statuses: ["FAILED"] },
];

let state = { batches: [], videos: [], events: [], chat_messages: [], live_progress: {}, instagram: { connected: false }, settings: {} };

function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + "…" : (s || ""); }
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function timeLabel(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }

const STALE_SECONDS = 90;
function elapsedLabel(sinceTs) {
  const secs = Math.max(0, Date.now() / 1000 - sinceTs);
  return secs < 60 ? `${Math.floor(secs)}s` : `${Math.floor(secs / 60)}m ${Math.floor(secs % 60)}s`;
}

function activeVideoFor(name) {
  const status = ACTIVE_STATUS_BY_EMPLOYEE[name];
  if (!status) return null;
  return state.videos.find(v => v.status === status) || null;
}

function qaCheckHtml(c) {
  const cls = c.passed === true ? "ok" : c.passed === false ? "fail" : "skip";
  const mark = c.passed === true ? "✓" : c.passed === false ? "✗" : "•";
  const title = c.detail ? ` title="${escapeHtml(c.detail)}"` : "";
  return `<div class="qa-check"${title}><span class="${cls}">${mark}</span> ${escapeHtml(c.name)}</div>`;
}

function lastAgentForVideo(videoId) {
  const relevant = state.events.filter(e => e.video_id === videoId);
  return relevant.length ? relevant[relevant.length - 1].agent : null;
}

// ---- rendering ----

function renderAll() {
  renderTeamList();
  renderIgStatus();
  renderOfficeFloor();
  renderKanban();
  renderReview();
  renderActivity();
  renderChat();
  renderBatchLabel();
  renderNotifications();
}

function pendingApprovalBatches() { return state.batches.filter(b => b.status === "WAITING_APPROVAL"); }
function failedVideos() { return state.videos.filter(v => v.status === "FAILED"); }

function renderNotifications() {
  const items = [
    ...pendingApprovalBatches().map(b => ({
      text: `Batch #${b.id} is waiting for your approval`, view: "review",
    })),
    ...failedVideos().map(v => ({
      text: `Video #${v.idx + 1} failed (batch #${v.batch_id})`, view: "office",
    })),
  ];

  const badge = document.getElementById("notif-badge");
  badge.style.display = items.length ? "flex" : "none";
  badge.textContent = items.length;

  const navBadge = document.getElementById("nav-review-badge");
  const approvalCount = pendingApprovalBatches().length;
  navBadge.style.display = approvalCount ? "flex" : "none";
  navBadge.textContent = approvalCount;

  const dropdown = document.getElementById("notif-dropdown");
  dropdown.innerHTML = items.length
    ? items.map(i => `<div class="notif-item" data-view="${i.view}">${escapeHtml(i.text)}</div>`).join("")
    : `<div class="notif-empty">No notifications</div>`;
  dropdown.querySelectorAll(".notif-item").forEach(el => {
    el.onclick = () => { switchView(el.dataset.view); dropdown.style.display = "none"; };
  });
}

function switchView(view) {
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + view));
}

function renderBatchLabel() {
  const label = document.getElementById("current-batch-label");
  const stopBtn = document.getElementById("stop-batch-btn");
  const deleteBtn = document.getElementById("delete-batch-btn");
  if (!state.batches.length) {
    label.textContent = "No batch yet";
    stopBtn.style.display = "none";
    deleteBtn.style.display = "none";
    return;
  }
  const latest = state.batches[0];
  label.textContent = `Batch #${latest.id} · ${latest.status}`;
  const active = latest.status === "IN_PROGRESS" || latest.status === "PUBLISHING";
  stopBtn.style.display = active ? "inline-block" : "none";
  stopBtn.dataset.batchId = latest.id;
  deleteBtn.style.display = active ? "none" : "inline-block";
  deleteBtn.dataset.batchId = latest.id;
}

function renderTeamList() {
  const root = document.getElementById("team-list");
  root.innerHTML = Object.keys(EMPLOYEES).map(name => {
    const working = name === "Michael" || !!activeVideoFor(name);
    return `<div class="team-row" data-name="${name}">
      <span class="status-dot ${working ? "status-working" : "status-idle"}"></span>${name}
    </div>`;
  }).join("");
  root.querySelectorAll(".team-row").forEach(row => {
    row.onclick = () => openEmployeeDetail(row.dataset.name, activeVideoFor(row.dataset.name));
  });
}

const TUNNEL_LABEL = {
  active: "Active", fixed: "Active (fixed URL)", starting: "Starting…",
  reconnecting: "Reconnecting…", unavailable: "Unavailable", not_installed: "Not installed",
  stopped: "Stopped",
};

function renderIgStatus() {
  const ig = state.instagram || { connected: false, reason: "unknown", tunnel_status: "stopped", tunnel_url: null };

  const el = document.getElementById("ig-status");
  el.className = "ig-status " + (ig.connected ? "connected" : "blocked");
  el.textContent = ig.connected ? "Connected" : `Not connected — ${ig.reason}`;

  const detail = document.getElementById("tunnel-detail");
  detail.textContent = "Media tunnel: " + (TUNNEL_LABEL[ig.tunnel_status] || ig.tunnel_status) +
    (ig.tunnel_url ? ` (${ig.tunnel_url})` : "");

  const pill = document.getElementById("tunnel-pill");
  const ok = ig.tunnel_status === "active" || ig.tunnel_status === "fixed";
  pill.className = "pill" + (ok ? " pill-live" : "");
  pill.textContent = "● Media Tunnel " + (TUNNEL_LABEL[ig.tunnel_status] || ig.tunnel_status);
}

function renderOfficeFloor() {
  const floor = document.getElementById("office-floor");
  floor.innerHTML = "";
  Object.keys(EMPLOYEES).forEach(name => floor.appendChild(buildDeskCard(name)));
}

function buildDeskCard(name) {
  const emp = EMPLOYEES[name];
  const video = activeVideoFor(name);
  const card = document.createElement("div");
  card.className = "desk-card";
  card.style.setProperty("--room-color", emp.room);

  let badgeClass = "", statusLabel = "Idle", taskLine = "No active task", metaLine = "", metaStale = false, progressHtml = "";

  if (name === "Michael") {
    badgeClass = "b-working"; statusLabel = "Online";
    const lastEvent = state.events[state.events.length - 1];
    taskLine = lastEvent ? `${lastEvent.agent}: ${truncate(lastEvent.message, 55)}` : "Waiting for a new batch";
  } else if (video) {
    const stale = (Date.now() / 1000 - video.updated_at) > STALE_SECONDS;
    badgeClass = stale ? "b-stale" : (name === "David" ? "b-reviewing" : "b-working");
    statusLabel = stale ? "Running long" : (name === "David" ? "Reviewing" : "Working");
    taskLine = `${ACTION_VERB[name]} Video #${video.idx + 1}`;
    const progress = state.live_progress[String(video.id)];
    if (progress && progress.percent !== undefined) {
      progressHtml = `<div class="progress-bar"><div style="width:${progress.percent}%"></div></div>`;
      const bits = [`${Math.round(progress.percent)}%`];
      if (progress.eta_seconds) bits.push(`ETA ${Math.round(progress.eta_seconds)}s`);
      metaLine = bits.join(" · ");
    } else {
      progressHtml = `<div class="progress-bar indeterminate"><div></div></div>`;
      metaLine = `Running ${elapsedLabel(video.updated_at)}`;
      metaStale = stale;
    }
  }

  // Scoped to the current/latest batch only — otherwise a FAILED video from
  // an old, already-resolved batch keeps showing this agent as permanently
  // errored forever, even though nothing is actually wrong right now.
  const currentBatchId = state.batches[0]?.id;
  const failed = state.videos.find(v => v.batch_id === currentBatchId && v.status === "FAILED" && lastAgentForVideo(v.id) === name);
  if (!video && failed) { badgeClass = "b-error"; statusLabel = "Error"; taskLine = `Video #${failed.idx + 1} failed`; }
  if (failed) card.classList.add("room-error");
  else if (badgeClass === "b-stale") card.classList.add("room-stale");
  const clickVideo = video || failed || null;

  card.innerHTML = `
    <div class="desk-head">
      <div class="desk-head-left">
        <div class="avatar">${emp.avatar}</div>
        <div><div class="desk-name">${name}</div><div class="desk-role">${emp.role}</div></div>
      </div>
      <span class="status-badge ${badgeClass}">● ${statusLabel}</span>
    </div>
    <div class="desk-monitor">
      <div class="desk-task">${escapeHtml(taskLine)}</div>
      ${progressHtml}
      ${metaLine ? `<div class="desk-meta${metaStale ? " stale" : ""}">${metaLine}</div>` : ""}
    </div>
  `;
  card.onclick = () => openEmployeeDetail(name, clickVideo);
  return card;
}

function renderKanban() {
  const root = document.getElementById("kanban");
  root.innerHTML = "";
  KANBAN_COLUMNS.forEach(col => {
    const items = state.videos.filter(v => col.statuses.includes(v.status));
    const div = document.createElement("div");
    div.className = "kanban-col";
    const activeStatuses = new Set(Object.keys(AGENT_BY_STATUS).filter(s => !["QUEUED", "PUBLISHED", "FAILED"].includes(s)));
    div.innerHTML = `<h4>${col.label} (${items.length})</h4>` + items.map(v => {
      const stale = activeStatuses.has(v.status) && (Date.now() / 1000 - v.updated_at) > STALE_SECONDS;
      return `
      <div class="kanban-card">
        Video #${v.idx + 1}
        <div class="who">${AGENT_BY_STATUS[v.status] || ""} ${v.title ? "· " + escapeHtml(truncate(v.title, 22)) : ""}</div>
        ${activeStatuses.has(v.status) ? `<div class="elapsed${stale ? " stale" : ""}">${stale ? "⚠ " : ""}${elapsedLabel(v.updated_at)} in this stage</div>` : ""}
        ${v.status === "FAILED" ? `<button type="button" class="small-btn" data-retry="${v.id}" style="margin-top:6px">Retry</button>` : ""}
        ${stale ? `<button type="button" class="small-btn" data-force-retry="${v.id}" style="margin-top:6px">Force Retry</button>` : ""}
      </div>
    `;
    }).join("");
    root.appendChild(div);
  });
  root.querySelectorAll("[data-retry]").forEach(btn => btn.onclick = () => retryVideo(btn.dataset.retry));
  root.querySelectorAll("[data-force-retry]").forEach(btn => btn.onclick = () => {
    if (confirm("This video hasn't updated in a while but may still be running. Force-retrying can start a duplicate run if it isn't actually stuck. Continue?")) {
      retryVideo(btn.dataset.forceRetry);
    }
  });
}

async function retryVideo(videoId) {
  await fetch(`/api/videos/${videoId}/retry`, { method: "POST" });
  closeModal();
  await fetchState();
}

function renderActivity() {
  document.getElementById("activity-feed-full").innerHTML =
    state.events.slice().reverse().map(e => `
      <div class="activity-row">
        <span class="time">${timeLabel(e.created_at)}</span>
        <span class="activity-icon">${AGENT_ICON[e.agent] || "•"}</span>
        <span class="agent">${e.agent}</span>
        <span>${escapeHtml(e.message)}</span>
      </div>`).join("") || `<p style="color:var(--text-dim)">No activity yet.</p>`;

  document.getElementById("activity-ticker").innerHTML =
    state.events.slice(-8).map(e => `
      <span class="ticker-item">
        <span class="activity-icon">${AGENT_ICON[e.agent] || "•"}</span>
        ${timeLabel(e.created_at)} <strong>${e.agent}</strong> ${escapeHtml(e.message)}
      </span>`).join("") || `<span class="ticker-item">No activity yet.</span>`;
}

function renderChat() {
  const box = document.getElementById("chat-messages");
  const messagesHtml = state.chat_messages.map(m =>
    `<div class="chat-msg ${m.role === "user" ? "user" : "michael"}">${escapeHtml(m.content)}
      <div class="msg-time">${timeLabel(m.created_at)}</div>
    </div>`
  ).join("");

  const approvalHtml = pendingApprovalBatches().map(b => {
    const count = state.videos.filter(v => v.batch_id === b.id).length;
    return `<div class="approval-card">
      <div class="a-title">⚠ Approval Needed</div>
      <p>Review Batch #${b.id} — ${count} video${count !== 1 ? "s" : ""} ready for your review.</p>
      <button type="button" class="btn btn-primary" data-review="${b.id}">Review Now</button>
    </div>`;
  }).join("");

  box.innerHTML = messagesHtml + approvalHtml;
  box.querySelectorAll("[data-review]").forEach(btn => btn.onclick = () => switchView("review"));
  box.scrollTop = box.scrollHeight;
}

function renderReview() {
  const root = document.getElementById("review-screen");
  const batches = state.batches.filter(b => b.status === "WAITING_APPROVAL");
  if (!batches.length) {
    root.innerHTML = `<p style="color:var(--text-dim)">No batches waiting for approval right now.</p>`;
    return;
  }
  root.innerHTML = batches.map(renderBatchReview).join("<hr style='border:none;border-top:1px solid var(--border);margin:24px 0'>");
  root.querySelectorAll("[data-approve]").forEach(btn => btn.onclick = () => confirmApprove(btn.dataset.approve));
  root.querySelectorAll("[data-reject]").forEach(btn => btn.onclick = () => rejectBatch(btn.dataset.reject));
}

function renderBatchReview(batch) {
  const videos = state.videos.filter(v => v.batch_id === batch.id);
  const cards = videos.map(v => {
    const filename = v.final_path ? v.final_path.split(/[\\/]/).pop() : "";
    const src = filename ? `/media/${batch.id}/${filename}` : "";
    const qa = v.qa_report_json ? JSON.parse(v.qa_report_json) : null;
    const checks = qa ? qa.checks.map(qaCheckHtml).join("") : "";
    return `<div class="review-card">
      ${src ? `<video src="${src}" controls></video>` : "<p>(no preview)</p>"}
      <div><strong>Video #${v.idx + 1}</strong></div>
      <div class="caption-text">"${escapeHtml(v.caption_text || "")}"</div>
      ${checks}
    </div>`;
  }).join("");

  return `<h3>Batch #${batch.id} — ${videos.length} Video${videos.length !== 1 ? "s" : ""} Ready</h3>
    <div class="review-grid">${cards}</div>
    <div class="review-actions">
      <button class="btn btn-danger" data-reject="${batch.id}">Reject Batch</button>
      <button class="btn btn-primary" data-approve="${batch.id}">Approve &amp; Publish</button>
    </div>`;
}

// ---- modal helpers ----

function openModal(html) {
  const root = document.getElementById("modal-root");
  root.innerHTML = `<div class="modal-overlay"><div class="modal">${html}</div></div>`;
  root.querySelector(".modal-overlay").addEventListener("click", e => {
    if (e.target.classList.contains("modal-overlay")) closeModal();
  });
  return root.querySelector(".modal");
}
function closeModal() { document.getElementById("modal-root").innerHTML = ""; }

function openEmployeeDetail(name, video) {
  const emp = EMPLOYEES[name];
  let body;
  if (video && video.status === "FAILED") {
    body = `<p><strong>Video #${video.idx + 1} failed</strong></p>
      <p style="color:var(--red)">${escapeHtml(video.error_message || "Unknown error")}</p>
      <button type="button" class="btn btn-primary" data-retry="${video.id}">Retry</button>`;
  } else if (name === "Michael") {
    body = `<p>Coordinates the whole team and is your point of contact.</p>`;
  } else if (!video) {
    body = `<p>${name} is idle right now — no active task.</p>`;
  } else if (name === "Alex") {
    body = `<p><strong>Task:</strong> Downloading Video #${video.idx + 1}</p>
      <p><strong>URL:</strong> ${escapeHtml(video.youtube_url)}</p>`;
  } else if (name === "Ryan") {
    body = `<p><strong>Video #${video.idx + 1}</strong></p>
      <p><strong>Start:</strong> ${video.start_time_seconds}s &nbsp; <strong>End:</strong> ${video.start_time_seconds + 30}s</p>
      <p><strong>Caption:</strong> ${escapeHtml(video.caption_text || "(pending)")}</p>`;
  } else if (name === "David") {
    const qa = video.qa_report_json ? JSON.parse(video.qa_report_json) : null;
    body = qa ? qa.checks.map(qaCheckHtml).join("") : "<p>QA in progress…</p>";
  } else if (name === "Emma") {
    const cands = video.caption_candidates_json ? JSON.parse(video.caption_candidates_json) : [];
    body = `<p><strong>Candidates:</strong></p>${cands.length ? `<ol>${cands.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ol>` : "<p>Generating candidates…</p>"}
      <p><strong>Selected:</strong> ${escapeHtml(video.caption_text || "(pending)")}</p>`;
  } else if (name === "Sophia") {
    body = `<p>Publishing Video #${video.idx + 1}…</p>`;
  }
  const modal = openModal(`<h3>${name} — ${emp.role}</h3>${body}
    <div class="modal-actions"><button type="button" class="btn btn-secondary" id="detail-close">Close</button></div>`);
  document.getElementById("detail-close").onclick = closeModal;
  const retryBtn = modal.querySelector("[data-retry]");
  if (retryBtn) retryBtn.onclick = () => retryVideo(retryBtn.dataset.retry);
}

function confirmApprove(batchId) {
  const batch = state.batches.find(b => b.id === Number(batchId));
  const count = state.videos.filter(v => v.batch_id === batch.id).length;
  const modal = openModal(`
    <h3>Are you sure?</h3>
    <p>This will publish ${count} video${count !== 1 ? "s" : ""} as one Instagram carousel.</p>
    <div class="modal-actions">
      <button type="button" class="btn btn-secondary" id="confirm-cancel">Cancel</button>
      <button type="button" class="btn btn-primary" id="confirm-ok">Approve &amp; Publish</button>
    </div>`);
  modal.querySelector("#confirm-cancel").onclick = closeModal;
  modal.querySelector("#confirm-ok").onclick = async () => {
    closeModal();
    const res = await fetch(`/api/batches/${batchId}/approve`, { method: "POST" });
    const data = await res.json();
    if (data.error) alert(data.error);
    await fetchState();
  };
}

async function rejectBatch(batchId) {
  if (!confirm("Reject this batch?")) return;
  await fetch(`/api/batches/${batchId}/reject`, { method: "POST" });
  await fetchState();
}

const NB_DRAFT_KEY = "nb-draft";

// Mobile browsers reload a backgrounded tab to reclaim memory (e.g. when the
// user switches apps to copy a URL), which wipes the in-memory modal along
// with whatever was typed. Persist the draft as they type so reopening after
// such a reload restores it instead of losing it.
function openNewBatchModal(draft) {
  const modal = openModal(`
    <h3>New Batch</h3>
    <div id="video-rows"></div>
    <button type="button" class="small-btn" id="add-video-row">+ Add Video</button>
    <div class="field" style="margin-top:16px"><label>Resolution</label>
      <select id="nb-resolution">
        <option${draft?.resolution === "1080p" ? " selected" : ""}>1080p</option>
        <option${!draft || draft.resolution === "720p" ? " selected" : ""}>720p</option>
        <option${draft?.resolution === "480p" ? " selected" : ""}>480p</option>
        <option${draft?.resolution === "360p" ? " selected" : ""}>360p</option>
      </select>
    </div>
    <div class="field check-row"><input type="checkbox" id="nb-watermark-enabled" ${!draft || draft.watermark_enabled ? "checked" : ""}><label style="margin:0">Enable watermark</label></div>
    <div class="field"><label>Watermark Text</label><input id="nb-watermark-text" placeholder="@myinstagram" value="${escapeHtml(draft?.watermark_text || "")}"></div>
    <div class="modal-actions">
      <button type="button" class="btn btn-secondary" id="nb-cancel">Cancel</button>
      <button type="button" class="btn btn-primary" id="nb-start">Start Batch</button>
    </div>`);

  const rowsDiv = modal.querySelector("#video-rows");
  let uidCounter = 0;

  function refreshCopyFromDropdowns() {
    const rows = [...rowsDiv.querySelectorAll(".video-row")];
    rows.forEach((row, i) => {
      const select = row.querySelector(".nb-copy-from");
      const prevValue = select.value;
      const earlier = rows.slice(0, i);
      select.innerHTML = [`<option value="">Don't copy — use caption/description below</option>`]
        .concat(earlier.map(r => `<option value="${r.dataset.uid}">Copy caption from Video #${rows.indexOf(r) + 1}</option>`))
        .join("");
      select.value = earlier.some(r => r.dataset.uid === prevValue) ? prevValue : "";
      row.querySelector(".video-row-extra").classList.toggle("copy-active", !!select.value);
    });
  }

  function addRow(data) {
    data = data || {};
    const uid = String(++uidCounter);
    const row = document.createElement("div");
    row.className = "video-row";
    row.dataset.uid = uid;
    row.innerHTML = `
      <div class="video-row-main">
        <input type="text" class="nb-url" placeholder="YouTube URL" value="${escapeHtml(data.url || "")}">
        <input type="text" class="start-input nb-start" placeholder="mm:ss" value="${escapeHtml(data.start || "")}">
        <button type="button" class="small-btn nb-remove">✕</button>
      </div>
      <div class="video-row-extra">
        <input type="text" class="nb-caption" placeholder="Caption (leave blank to let Emma write it)" value="${escapeHtml(data.caption || "")}">
        <textarea class="nb-description" placeholder="Description for Emma — what's in this clip, tone, style (optional)" rows="2">${escapeHtml(data.description || "")}</textarea>
        <select class="nb-copy-from"></select>
      </div>`;
    row.querySelector(".nb-remove").onclick = () => { row.remove(); refreshCopyFromDropdowns(); saveDraft(); };
    row.querySelector(".nb-copy-from").onchange = e => {
      row.querySelector(".video-row-extra").classList.toggle("copy-active", !!e.target.value);
      saveDraft();
    };
    rowsDiv.appendChild(row);
    refreshCopyFromDropdowns();
    if (Number.isInteger(data.copiedFromPosition)) {
      const rows = [...rowsDiv.querySelectorAll(".video-row")];
      const target = rows[data.copiedFromPosition];
      if (target) {
        row.querySelector(".nb-copy-from").value = target.dataset.uid;
        row.querySelector(".video-row-extra").classList.add("copy-active");
      }
    }
  }
  if (draft?.videos?.length) draft.videos.forEach(v => addRow(v));
  else { addRow(); addRow(); addRow(); }

  function saveDraft() {
    const rows = [...rowsDiv.querySelectorAll(".video-row")];
    sessionStorage.setItem(NB_DRAFT_KEY, JSON.stringify({
      videos: rows.map(row => {
        const copyUid = row.querySelector(".nb-copy-from").value;
        const copiedFromPosition = copyUid ? rows.findIndex(r => r.dataset.uid === copyUid) : null;
        return {
          url: row.querySelector(".nb-url").value,
          start: row.querySelector(".nb-start").value,
          caption: row.querySelector(".nb-caption").value,
          description: row.querySelector(".nb-description").value,
          copiedFromPosition: copiedFromPosition !== null && copiedFromPosition >= 0 ? copiedFromPosition : null,
        };
      }),
      resolution: modal.querySelector("#nb-resolution").value,
      watermark_enabled: modal.querySelector("#nb-watermark-enabled").checked,
      watermark_text: modal.querySelector("#nb-watermark-text").value,
    }));
  }
  modal.addEventListener("input", saveDraft);
  modal.addEventListener("change", saveDraft);

  modal.querySelector("#add-video-row").onclick = () => { addRow(); saveDraft(); };
  modal.querySelector("#nb-cancel").onclick = () => { sessionStorage.removeItem(NB_DRAFT_KEY); closeModal(); };
  modal.querySelector("#nb-start").onclick = async () => {
    const kept = [...rowsDiv.querySelectorAll(".video-row")].filter(row => row.querySelector(".nb-url").value.trim());
    if (!kept.length) { alert("Add at least one video URL."); return; }
    const uidToFinalIdx = {};
    kept.forEach((row, i) => { uidToFinalIdx[row.dataset.uid] = i; });

    const videos = kept.map(row => {
      const copyUid = row.querySelector(".nb-copy-from").value;
      return {
        url: row.querySelector(".nb-url").value.trim(),
        start_time: row.querySelector(".nb-start").value.trim(),
        caption: row.querySelector(".nb-caption").value.trim(),
        description: row.querySelector(".nb-description").value.trim(),
        copied_from: copyUid && uidToFinalIdx[copyUid] !== undefined ? uidToFinalIdx[copyUid] : null,
      };
    });

    const payload = {
      videos,
      resolution: modal.querySelector("#nb-resolution").value,
      watermark_enabled: modal.querySelector("#nb-watermark-enabled").checked,
      watermark_text: modal.querySelector("#nb-watermark-text").value.trim(),
    };
    const res = await fetch("/api/batches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    sessionStorage.removeItem(NB_DRAFT_KEY);
    closeModal();
    await fetchState();
  };
}

// ---- data + wiring ----

async function fetchState() {
  const res = await fetch("/api/state");
  state = await res.json();
  renderAll();
}

function connectStream() {
  const es = new EventSource("/api/stream");
  // Fires on the very first connect AND every reconnect (e.g. after the
  // backend restarts) — resync in full so a missed event (no replay on
  // this pub/sub) never leaves the page showing stale status.
  es.onopen = fetchState;
  es.onmessage = ev => {
    const data = JSON.parse(ev.data);
    if (data.type === "progress") {
      state.live_progress[String(data.video_id)] = data;
      renderOfficeFloor();
    } else {
      fetchState();
    }
  };
}

document.querySelectorAll(".nav-item").forEach(btn => {
  btn.onclick = () => { switchView(btn.dataset.view); closeDrawers(); };
});

const sidebar = document.getElementById("sidebar");
const chatPanel = document.getElementById("chat-panel");
const backdrop = document.getElementById("drawer-backdrop");

function closeDrawers() {
  sidebar.classList.remove("open");
  chatPanel.classList.remove("open");
  backdrop.classList.remove("open");
}
function openDrawer(panel) {
  closeDrawers();
  panel.classList.add("open");
  backdrop.classList.add("open");
}

document.getElementById("nav-toggle-btn").onclick = () => openDrawer(sidebar);
document.getElementById("chat-toggle-btn").onclick = () => openDrawer(chatPanel);
document.getElementById("chat-close-btn").onclick = closeDrawers;
backdrop.onclick = closeDrawers;

document.getElementById("new-batch-btn").onclick = openNewBatchModal;
document.getElementById("qa-new-batch").onclick = openNewBatchModal;

document.getElementById("stop-batch-btn").onclick = async e => {
  const id = e.target.dataset.batchId;
  if (!confirm("Stop this batch? Steps already running will finish, but no further steps will start.")) return;
  const res = await fetch(`/api/batches/${id}/stop`, { method: "POST" });
  const data = await res.json();
  if (data.error) alert(data.error);
  await fetchState();
};
document.getElementById("delete-batch-btn").onclick = async e => {
  const id = e.target.dataset.batchId;
  if (!confirm("Permanently delete this batch and all its videos? This can't be undone.")) return;
  const res = await fetch(`/api/batches/${id}/delete`, { method: "DELETE" });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  await fetchState();
};
document.getElementById("qa-reports").onclick = () => switchView("activity");
document.getElementById("qa-team-chat").onclick = () => document.getElementById("chat-input").focus();
document.getElementById("qa-ig-test").onclick = () => document.getElementById("ig-test-btn").click();

document.getElementById("notif-btn").onclick = e => {
  e.stopPropagation();
  const dropdown = document.getElementById("notif-dropdown");
  dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
};
document.addEventListener("click", e => {
  if (!e.target.closest("#notif-btn") && !e.target.closest("#notif-dropdown")) {
    document.getElementById("notif-dropdown").style.display = "none";
  }
});

document.getElementById("settings-btn").onclick = () => {
  const ig = state.instagram || {};
  openModal(`
    <h3>Settings</h3>
    <div class="settings-row"><span>Text model</span><span>${escapeHtml(state.settings.nim_text_model || "—")}</span></div>
    <div class="settings-row"><span>Vision model</span><span>${escapeHtml(state.settings.nim_vision_model || "—")}</span></div>
    <div class="settings-row"><span>Instagram</span><span>${ig.connected ? "Connected" : "Not connected"}</span></div>
    <div class="settings-row"><span>Media tunnel</span><span>${TUNNEL_LABEL[ig.tunnel_status] || ig.tunnel_status || "—"}</span></div>
    <div class="settings-row"><span>Tunnel URL</span><span style="word-break:break-all">${escapeHtml(ig.tunnel_url || "—")}</span></div>
    <div class="settings-row"><span>Batch cleanup retention</span><span>${escapeHtml(state.settings.cleanup_retention || "—")}</span></div>
    <div class="modal-actions"><button type="button" class="btn btn-secondary" id="settings-close">Close</button></div>
  `);
  document.getElementById("settings-close").onclick = closeModal;
};

document.getElementById("ig-test-btn").onclick = async () => {
  const res = await fetch("/api/instagram/test", { method: "POST" });
  const data = await res.json();
  alert(data.connected
    ? `Connected. Tunnel reachable: ${data.tunnel_reachable}`
    : `Not connected: ${data.reason}`);
};

document.getElementById("chat-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  state.chat_messages.push({ role: "user", content: message, created_at: Date.now() / 1000 });
  renderChat();

  const sendBtn = document.querySelector("#chat-form button[type=submit]");
  sendBtn.disabled = true;
  const typingEl = document.createElement("div");
  typingEl.className = "chat-msg michael typing";
  typingEl.textContent = "Michael is typing…";
  document.getElementById("chat-messages").appendChild(typingEl);
  document.getElementById("chat-messages").scrollTop = 1e9;

  let reply;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 65000);
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }), signal: controller.signal,
    });
    clearTimeout(timeoutId);
    const data = await res.json();
    reply = data.reply || data.error || "Sorry, something went wrong on my end.";
  } catch (err) {
    reply = err.name === "AbortError"
      ? "Sorry, that's taking too long to answer — please try again."
      : "Sorry, I couldn't reach the server just now — please try again.";
  }

  typingEl.remove();
  sendBtn.disabled = false;
  state.chat_messages.push({ role: "michael", content: reply, created_at: Date.now() / 1000 });
  renderChat();
});

fetchState();
connectStream();
setInterval(() => { renderOfficeFloor(); renderKanban(); }, 5000);

const nbDraft = sessionStorage.getItem(NB_DRAFT_KEY);
if (nbDraft) openNewBatchModal(JSON.parse(nbDraft));
