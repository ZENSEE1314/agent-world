// ---------------------------------------------------------------------------
// Agent World — God View client
// ---------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const canvas = $("#world-canvas");
const ctx = canvas.getContext("2d");

const state = {
  snapshot: null,
  hoveredAgent: null,
};

// --------------------------------------------------------------- WebSocket
function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.addEventListener("open", () => {
    $("#m-ws").textContent = "live";
    $("#m-ws").className = "val ws-on";
  });
  ws.addEventListener("close", () => {
    $("#m-ws").textContent = "off";
    $("#m-ws").className = "val ws-off";
    setTimeout(connectWS, 2000);
  });
  ws.addEventListener("error", () => ws.close());
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "snapshot" || msg.type === "tick") {
        state.snapshot = msg.snapshot;
        render();
      } else if (msg.type === "house_added" || msg.type === "agent_added") {
        fetch("/api/state").then(r => r.json()).then(s => {
          state.snapshot = s; render();
        });
      }
    } catch (err) { console.error(err); }
  });
}

// Initial pull in case WS is slow
fetch("/api/state").then(r => r.json()).then(s => {
  state.snapshot = s; render();
}).catch(console.error);
connectWS();

// --------------------------------------------------------------- rendering
function render() {
  if (!state.snapshot) return;
  drawWorld();
  drawTopbar();
  drawMarketBar();
  drawLeaderboard();
  drawPaperOpen();
  drawReviewQueue();
  drawAgentsList();
  drawLogs();
  drawChat();
  drawDivineHistory();
}

// -------- Open paper positions
function drawPaperOpen() {
  const s = state.snapshot;
  const el = document.getElementById("paper-open");
  if (!el) return;
  const open = (s.paper && s.paper.open) || [];
  if (open.length === 0) {
    el.innerHTML = `<div class="paper-empty">No open positions. Crypto jobs open paper trades at live Coingecko prices.</div>`;
    return;
  }
  el.innerHTML = open.map(p => {
    const pnl = p.unrealized_pnl || 0;
    const cls = pnl > 0.01 ? "up" : pnl < -0.01 ? "down" : "flat";
    const sign = pnl >= 0 ? "+" : "";
    return `<div class="pos-row">
      <span class="coin">${p.coin}</span>
      <span class="who">${escapeHtml(p.agent)} · $${p.size_usd.toFixed(2)}</span>
      <span class="price">$${p.entry_price.toFixed(2)} → $${p.current_price.toFixed(2)}</span>
      <span class="pnl ${cls}">${sign}$${pnl.toFixed(2)}</span>
    </div>`;
  }).join("");
}

// -------- Review queue
async function voteOnProject(pid, vote) {
  try {
    await fetch(`/api/projects/${encodeURIComponent(pid)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vote }),
    });
    // Optimistic update — remove this card; snapshot will catch up
    if (state.snapshot && state.snapshot.review_queue) {
      state.snapshot.review_queue = state.snapshot.review_queue.filter(p => p.id !== pid);
      drawReviewQueue();
    }
  } catch (err) { console.error(err); }
}

function copyProjectBody(pid, btn) {
  const proj = (state.snapshot && state.snapshot.review_queue || []).find(p => p.id === pid);
  if (!proj) return;
  navigator.clipboard.writeText(proj.body).then(() => {
    const orig = btn.textContent;
    btn.textContent = "copied!";
    setTimeout(() => { btn.textContent = orig; }, 1200);
  });
}

function drawReviewQueue() {
  const s = state.snapshot;
  const el = document.getElementById("review-queue");
  const countEl = document.getElementById("queue-count");
  if (!el) return;
  const q = s.review_queue || [];
  if (countEl) countEl.textContent = q.length ? q.length : "";
  if (q.length === 0) {
    el.innerHTML = `<div class="review-empty">Nothing pending. Agents ship new artifacts when they pick "build_project".</div>`;
    return;
  }
  el.innerHTML = q.map(p => `
    <div class="review-card" data-id="${p.id}">
      <div class="rhead">
        <span class="rtitle">${escapeHtml(p.title)}</span>
      </div>
      <div class="rmeta">
        <span class="rkind">${p.kind}</span>
        by ${escapeHtml(p.agent_name)} · ${new Date(p.created_at * 1000).toLocaleTimeString()}
      </div>
      <div class="rbody">${escapeHtml(p.body)}</div>
      <div class="ractions">
        <button class="approve" data-action="approve" data-id="${p.id}">👍 Approve</button>
        <button class="reject"  data-action="reject"  data-id="${p.id}">👎 Reject</button>
        <button class="copy"    data-action="copy"    data-id="${p.id}">📋 Copy</button>
      </div>
    </div>
  `).join("");
}

// Delegated click handler for review-queue buttons
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".ractions button");
  if (!btn) return;
  const pid = btn.dataset.id;
  const action = btn.dataset.action;
  if (action === "approve" || action === "reject") voteOnProject(pid, action);
  else if (action === "copy") copyProjectBody(pid, btn);
});

function drawMarketBar() {
  const s = state.snapshot;
  const el = document.getElementById("market-bar");
  if (!el) return;
  const market = s.market || {};
  const syms = Object.keys(market);
  const pills = syms.map(sym => {
    const d = market[sym];
    const chg = d.change_24h_pct || 0;
    const cls = chg > 0.05 ? "up" : chg < -0.05 ? "down" : "flat";
    const sign = chg >= 0 ? "+" : "";
    return `<span class="market-pill">
      <span class="sym">${sym}</span>
      <span class="price">$${d.price_usd.toLocaleString(undefined, {maximumFractionDigits: 2})}</span>
      <span class="chg ${cls}">${sign}${chg.toFixed(2)}%</span>
    </span>`;
  }).join("");
  let brainLabel = s.brain || "—";
  let brainClass = "";
  const bs = s.brain_status || {};
  if (s.brain === "ollama") {
    if (bs.pulling) { brainLabel = `ollama · pulling ${bs.ollama_model || ""}`; brainClass = "warn"; }
    else if (bs.model_ready) { brainLabel = `ollama · ${bs.ollama_model || ""} ready`; }
    else { brainLabel = `ollama · ${bs.ollama_model || ""} warming up`; brainClass = "warn"; }
  }
  el.innerHTML = pills + `<span class="brain-pill ${brainClass}">brain: ${escapeHtml(brainLabel)}</span>`;
}

function drawTopbar() {
  const s = state.snapshot;
  $("#m-tick").textContent = s.tick;
  $("#m-brain").textContent = s.brain;
  const alive = s.agents.filter(a => a.alive).length;
  $("#m-alive").textContent = `${alive}/${s.agents.length}`;
  const totalReal = s.agents.reduce((acc, a) => acc + (a.real_usd || 0), 0);
  $("#m-real").textContent = `$${totalReal.toFixed(2)}`;

  const pauseBtn = document.getElementById("btn-pause");
  const banner = document.getElementById("pause-banner");
  if (pauseBtn) {
    if (s.paused) {
      pauseBtn.textContent = "▶ Resume";
      pauseBtn.classList.add("paused");
    } else {
      pauseBtn.textContent = "⏸ Pause";
      pauseBtn.classList.remove("paused");
    }
  }
  if (banner) banner.style.display = s.paused ? "block" : "none";
}

// -------- World canvas
function drawWorld() {
  const s = state.snapshot;
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Isometric-ish ground grid
  ctx.save();
  ctx.strokeStyle = "rgba(122,162,255,0.08)";
  ctx.lineWidth = 1;
  for (let x = 0; x < w; x += 60) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let y = 0; y < h; y += 60) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  ctx.restore();

  // Soft blobs for atmosphere
  for (let i = 0; i < 6; i++) {
    const gx = 120 + (i * 170) % w;
    const gy = 80 + ((i * 230) % (h - 120));
    const grad = ctx.createRadialGradient(gx, gy, 10, gx, gy, 180);
    grad.addColorStop(0, "rgba(176,122,255,0.08)");
    grad.addColorStop(1, "rgba(176,122,255,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
  }

  // Build agent-by-house map
  const byHouse = {};
  for (const a of s.agents) {
    if (!byHouse[a.house_id]) byHouse[a.house_id] = [];
    byHouse[a.house_id].push(a);
  }

  // Houses
  for (const house of s.houses) {
    drawHouse(house, (byHouse[house.id] || []).length);
  }

  // Agents (positioned slightly offset from their house)
  for (const house of s.houses) {
    const residents = byHouse[house.id] || [];
    residents.forEach((a, i) => {
      const offset = residents.length === 1
        ? { dx: 0, dy: 60 }
        : polarOffset(i, residents.length, 56);
      drawAgent(a, house.x + offset.dx, house.y + offset.dy);
    });
  }
}

function polarOffset(i, n, r) {
  const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
  return { dx: Math.cos(angle) * r, dy: 50 + Math.sin(angle) * (r * 0.6) };
}

function drawHouse(house, residentCount) {
  const { x, y, color } = house;
  // Shadow
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.35)";
  ctx.shadowBlur = 20;
  ctx.shadowOffsetY = 8;
  // Base
  ctx.fillStyle = color;
  roundRect(ctx, x - 60, y - 30, 120, 80, 10);
  ctx.fill();
  // Roof
  ctx.beginPath();
  ctx.moveTo(x - 70, y - 30);
  ctx.lineTo(x, y - 80);
  ctx.lineTo(x + 70, y - 30);
  ctx.closePath();
  ctx.fillStyle = shade(color, -0.2);
  ctx.fill();
  // Door
  ctx.fillStyle = shade(color, -0.45);
  roundRect(ctx, x - 14, y + 10, 28, 40, 4);
  ctx.fill();
  // Window
  ctx.fillStyle = "rgba(255,255,255,0.7)";
  roundRect(ctx, x - 48, y - 10, 22, 18, 3);
  ctx.fill();
  roundRect(ctx, x + 26, y - 10, 22, 18, 3);
  ctx.fill();
  ctx.restore();

  // Label
  ctx.fillStyle = "rgba(232,236,255,0.75)";
  ctx.font = "11px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(`${house.id} · ${residentCount} 👤`, x, y + 72);
}

function drawAgent(a, x, y) {
  // Body circle
  ctx.save();
  if (!a.alive) {
    ctx.globalAlpha = 0.4;
  }
  // health ring
  const ringR = 20;
  ctx.beginPath();
  ctx.arc(x, y, ringR, 0, Math.PI * 2);
  ctx.fillStyle = a.role === "mentor" ? "#4c2c75" : "#1e2b5c";
  ctx.fill();
  ctx.strokeStyle = healthColor(a.health);
  ctx.lineWidth = 3;
  ctx.stroke();

  // Avatar emoji
  ctx.font = "18px system-ui";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(a.avatar || "🙂", x, y + 1);
  ctx.globalAlpha = 1;

  // Name
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.font = "11px system-ui";
  ctx.fillText(a.name, x, y + 36);

  // Mini bars under name
  drawMiniBar(x - 22, y + 42, 44, 4, a.health / 100, healthColor(a.health));
  drawMiniBar(x - 22, y + 48, 44, 4, a.hunger / 100, hungerColor(a.hunger));

  if (!a.alive) {
    ctx.fillStyle = "#fca5a5";
    ctx.font = "10px system-ui";
    ctx.fillText("† dead", x, y - 30);
  } else if (a.mentor_ticks > 0) {
    ctx.fillStyle = "#facc15";
    ctx.font = "10px system-ui";
    ctx.fillText(`✨ ×${a.skill_multiplier.toFixed(1)}`, x, y - 30);
  }
  ctx.restore();
}

function drawMiniBar(x, y, w, h, pct, color) {
  ctx.fillStyle = "rgba(255,255,255,0.12)";
  roundRect(ctx, x, y, w, h, 2); ctx.fill();
  ctx.fillStyle = color;
  roundRect(ctx, x, y, Math.max(0, Math.min(1, pct)) * w, h, 2);
  ctx.fill();
}

function healthColor(h) {
  if (h < 25) return "#ef4444";
  if (h < 60) return "#fbbf24";
  return "#4ade80";
}
function hungerColor(h) {
  if (h < 25) return "#ef4444";
  if (h < 50) return "#fb923c";
  return "#fcd34d";
}

function roundRect(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function shade(hex, amt) {
  // amt in [-1, 1]
  const { r, g, b } = hexToRgb(hex);
  const f = 1 + amt;
  const clamp = v => Math.max(0, Math.min(255, Math.round(v)));
  return `rgb(${clamp(r * f)}, ${clamp(g * f)}, ${clamp(b * f)})`;
}
function hexToRgb(hex) {
  const m = hex.replace("#", "");
  const n = parseInt(m.length === 3 ? m.split("").map(c => c + c).join("") : m, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

// -------- Leaderboard
function drawLeaderboard() {
  const s = state.snapshot;
  const rows = s.leaderboard.slice(0, 8);
  const el = $("#leaderboard");
  const byId = Object.fromEntries(s.agents.map(a => [a.id, a]));
  el.innerHTML = rows.map((r, i) => {
    const a = byId[r.agent];
    if (!a) return "";
    return `<div class="leader-row">
      <span class="rank">${i + 1}</span>
      <span class="lname"><span class="avatar">${a.avatar || "🙂"}</span>${a.name}</span>
      <span class="lval">$${r.cash.toLocaleString()}</span>
      <span class="lval real">$${r.real_usd.toFixed(2)}</span>
    </div>`;
  }).join("");
}

// -------- Agents side list
function drawAgentsList() {
  const s = state.snapshot;
  const el = $("#agents-list");
  el.innerHTML = s.agents.map(a => {
    const hLow = a.health < 30 ? "low" : "";
    const nLow = a.hunger < 30 ? "low" : "";
    return `<div class="agent-card ${a.alive ? "" : "dead"}">
      <div class="row1">
        <span class="who">
          <span>${a.avatar || "🙂"}</span>
          <span>${a.name}</span>
          <span class="role-tag ${a.role}">${a.role}</span>
        </span>
        <span class="cash">$${a.cash.toLocaleString()}</span>
      </div>
      <div class="bars">
        <div class="bar health ${hLow}"><span>❤️ HP</span>
          <span class="track"><span class="fill" style="width:${a.health}%"></span></span>
          <span class="n">${Math.round(a.health)}</span>
        </div>
        <div class="bar hunger ${nLow}"><span>🍞 Hunger</span>
          <span class="track"><span class="fill" style="width:${a.hunger}%"></span></span>
          <span class="n">${Math.round(a.hunger)}</span>
        </div>
      </div>
      <div class="last">→ ${escapeHtml(a.last_action || "idle")}
        · built ${a.projects_built} · real $${a.real_usd.toFixed(2)}</div>
    </div>`;
  }).join("");
}

// -------- Logs
function drawLogs() {
  const s = state.snapshot;
  const el = $("#log-feed");
  const lines = s.logs.slice(-40).reverse();
  el.innerHTML = lines.map(l => {
    const tag = (l.kind || "info").toLowerCase();
    return `<div class="log-line ${tag}"><span class="tag tag-${tag}">${tag}</span><b>${escapeHtml(l.agent)}</b> ${escapeHtml(l.msg)}</div>`;
  }).join("");
}

// -------- Chat stream
function drawChat() {
  const s = state.snapshot;
  const el = $("#chat-feed");
  const msgs = s.messages.slice(-30).reverse();
  el.innerHTML = msgs.map(m => {
    const tag = m.kind === "broadcast" ? "📣" :
                m.kind === "knowledge_request" ? "❓" :
                m.kind === "mentorship" ? "🎓" :
                m.kind === "divine" ? "⚡" : "💬";
    const to = m.to === "*" ? "everyone" : m.to;
    return `<div class="chat-line">${tag} <span class="who">${escapeHtml(m.from)} → ${escapeHtml(to)}</span>: ${escapeHtml(m.text || "")}</div>`;
  }).join("");
}

function drawDivineHistory() {
  const s = state.snapshot;
  const el = $("#divine-history");
  el.innerHTML = (s.divine || []).slice().reverse().map(d =>
    `<div class="d">${escapeHtml(d.text)}</div>`
  ).join("");
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// --------------------------------------------------------------- UI wiring
$("#btn-add-house").addEventListener("click", () => {
  $("#modal-house").classList.remove("hidden");
});
$("#btn-add-agent").addEventListener("click", () => {
  $("#modal-agent").classList.remove("hidden");
});
document.querySelectorAll("[data-close]").forEach(btn => {
  btn.addEventListener("click", () => {
    btn.closest(".modal").classList.add("hidden");
  });
});

$("#form-house").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = { style: fd.get("style") || "cottage" };
  const color = (fd.get("color") || "").trim();
  if (color) body.color = color;
  await fetch("/api/houses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("#modal-house").classList.add("hidden");
  e.target.reset();
});

$("#form-agent").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    name: (fd.get("name") || "").toString().trim(),
    avatar: (fd.get("avatar") || "🙂").toString().trim() || "🙂",
    role: fd.get("role") || "worker",
    personality: (fd.get("personality") || "").toString().trim(),
  };
  if (!body.name) return;
  await fetch("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("#modal-agent").classList.add("hidden");
  e.target.reset();
});

$("#divine-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#divine-text").value.trim();
  if (!text) return;
  await fetch("/api/divine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  $("#divine-text").value = "";
});

$("#btn-pause").addEventListener("click", async () => {
  const nowPaused = !(state.snapshot && state.snapshot.paused);
  try {
    await fetch("/api/pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: nowPaused }),
    });
    if (state.snapshot) {
      state.snapshot.paused = nowPaused;
      drawTopbar();
    }
  } catch (err) { console.error(err); }
});

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Reset world? Wipes all cash, paper trades, projects, logs, and chat — agents themselves stay.")) return;
  const btn = $("#btn-reset");
  btn.disabled = true;
  btn.textContent = "resetting...";
  try {
    await fetch("/api/reset", { method: "POST" });
    const s = await fetch("/api/state").then(r => r.json());
    state.snapshot = s;
    render();
  } finally {
    btn.disabled = false;
    btn.textContent = "🔥 Reset World";
  }
});

// close modal on outside click
document.querySelectorAll(".modal").forEach(m => {
  m.addEventListener("click", (e) => {
    if (e.target === m) m.classList.add("hidden");
  });
});
