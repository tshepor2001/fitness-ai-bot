const selectForm = document.getElementById("select-form");
const connectForm = document.getElementById("connect-form");
const askForm = document.getElementById("ask-form");
const disconnectBtn = document.getElementById("disconnect-btn");
const result = document.getElementById("result");
const history = document.getElementById("history");
const spinnerOverlay = document.getElementById("spinner-overlay");
const spinnerText = document.getElementById("spinner-text");
const sourceBadges = document.getElementById("source-badges");

const modeExistingBtn = document.getElementById("mode-existing");
const modeNewBtn = document.getElementById("mode-new");
const userSelect = document.getElementById("user-select");

const userIdEl = document.getElementById("user-id");
const garminEmailEl = document.getElementById("garmin-email");
const garminPasswordEl = document.getElementById("garmin-password");
const tpUsernameEl = document.getElementById("tp-username");
const tpPasswordEl = document.getElementById("tp-password");
const questionEl = document.getElementById("question");

let activeUserId = null; // the label string of the connected user

const SOURCE_META = {
  garmin: {
    label: "Garmin",
    icon: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2a8 8 0 0 1 7.75 6H12V4.25A7.97 7.97 0 0 1 12 4zm-1 .27V11h7.73A8 8 0 1 1 11 4.27z"/></svg>',
  },
  trainingpeaks: {
    label: "TrainingPeaks",
    icon: '<svg viewBox="0 0 24 24"><path d="M3 17l4-8 3 4 4-10 7 14H3z"/></svg>',
  },
};

function renderSources(sources) {
  sourceBadges.innerHTML = "";
  if (!sources || sources.length === 0) return;
  sources.forEach((src) => {
    const meta = SOURCE_META[src];
    if (!meta) return;
    const badge = document.createElement("span");
    badge.className = `source-badge ${src}`;
    badge.innerHTML = `${meta.icon} ${meta.label}`;
    sourceBadges.appendChild(badge);
  });
}

function buildConnectSummary(servers) {
  if (!servers || Object.keys(servers).length === 0) return "";
  const labels = { garmin: "Garmin", trainingpeaks: "TrainingPeaks" };
  return Object.entries(servers)
    .map(([name, status]) => {
      const label = labels[name] || name;
      if (status === "ok") return `  \u2713 ${label}: connected`;
      return `  \u2717 ${label}: ${status}`;
    })
    .join("\n");
}

function showSpinner(msg) {
  spinnerText.textContent = msg;
  spinnerOverlay.classList.remove("hidden");
}

function hideSpinner() {
  spinnerOverlay.classList.add("hidden");
}

// ── Mode toggle ──────────────────────────────────────────────────

function setMode(mode) {
  if (mode === "existing") {
    selectForm.classList.remove("hidden");
    connectForm.classList.add("hidden");
    modeExistingBtn.classList.add("active");
    modeNewBtn.classList.remove("active");
  } else {
    selectForm.classList.add("hidden");
    connectForm.classList.remove("hidden");
    modeExistingBtn.classList.remove("active");
    modeNewBtn.classList.add("active");
  }
}

modeExistingBtn.addEventListener("click", () => setMode("existing"));
modeNewBtn.addEventListener("click", () => setMode("new"));

// ── Load users into dropdown ─────────────────────────────────────

async function loadUsers() {
  try {
    const users = await request("/users", { method: "GET" });
    userSelect.innerHTML = "";
    if (users.length === 0) {
      userSelect.innerHTML = '<option value="" disabled selected>No users yet — add one</option>';
      setMode("new");
      return;
    }
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    placeholder.textContent = "Choose a user...";
    userSelect.appendChild(placeholder);
    users.forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u.label;
      opt.textContent = u.label;
      userSelect.appendChild(opt);
    });
  } catch {
    userSelect.innerHTML = '<option value="" disabled selected>Failed to load users</option>';
  }
}

loadUsers();

function userId() {
  if (activeUserId) return activeUserId;
  throw new Error("No user connected");
}

function setResult(text) {
  result.textContent = text;
}

function addToHistory(question, answer) {
  const entry = document.createElement("div");
  entry.className = "history-entry";
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  entry.innerHTML =
    `<div class="history-meta"><span class="history-question">${escapeHtml(question)}</span><span>${time}</span></div>` +
    `<div class="history-answer">${escapeHtml(answer)}</div>`;
  history.prepend(entry);
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

// ── Existing user: reconnect with stored creds ───────────────────

selectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const uid = userSelect.value;
    if (!uid) {
      setResult("Please select a user.");
      return;
    }

    setResult("");
    showSpinner("Connecting & loading fitness data...");
    const data = await request(`/users/${encodeURIComponent(uid)}/reconnect`, {
      method: "POST",
      body: "{}",
    });

    hideSpinner();
    activeUserId = uid;
    renderSources(data.sources);
    const summary = buildConnectSummary(data.servers);
    setResult(`Connected as ${uid}.\n${summary}`);

    // Load previous history for this user
    try {
      const hist = await request(`/users/${encodeURIComponent(uid)}/history?limit=50`);
      history.innerHTML = "";
      hist.reverse().forEach(h => addToHistory(h.question, h.answer));
    } catch (_) { /* history load is best-effort */ }
  } catch (error) {
    hideSpinner();
    setResult(`Connect failed: ${error.message}`);
  }
});

// ── New user: add credentials and connect ────────────────────────

connectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const uid = userIdEl.value.trim();
    if (!uid) {
      setResult("User name is required.");
      return;
    }

    const body = {
      garmin_email: garminEmailEl.value.trim(),
      garmin_password: garminPasswordEl.value,
    };

    const tpUsername = tpUsernameEl.value.trim();
    const tpPassword = tpPasswordEl.value;
    if (tpUsername || tpPassword) {
      body.tp_username = tpUsername;
      body.tp_password = tpPassword;
    }

    setResult("");
    showSpinner("Adding user & loading fitness data...");
    const data = await request(`/users/${encodeURIComponent(uid)}/connect`, {
      method: "POST",
      body: JSON.stringify(body),
    });

    hideSpinner();
    activeUserId = uid;
    renderSources(data.sources);
    const summary = buildConnectSummary(data.servers);
    setResult(`Connected as ${uid}.\n${summary}`);

    // Refresh dropdown and switch to existing-user mode
    await loadUsers();
    userSelect.value = uid;
    setMode("existing");
  } catch (error) {
    hideSpinner();
    setResult(`Connect failed: ${error.message}`);
  }
});

askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const uid = userId();
    const question = questionEl.value.trim();
    if (!question) {
      setResult("Please enter a question.");
      return;
    }

    setResult("");
    showSpinner("Loading your fitness data & thinking...");

    // After a few seconds, update the message so the user knows it's still working
    const spinnerTimer = setTimeout(() => {
      spinnerText.textContent = "Still working — first question may take a moment...";
    }, 8000);

    const data = await request(`/users/${encodeURIComponent(uid)}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });

    clearTimeout(spinnerTimer);
    hideSpinner();
    renderSources(data.sources);
    const answer = data.answer || "No answer returned.";
    setResult(answer);
    addToHistory(question, answer);
  } catch (error) {
    hideSpinner();
    const errMsg = `Ask failed: ${error.message}`;
    setResult(errMsg);
    addToHistory(question, errMsg);
  }
});

disconnectBtn.addEventListener("click", async () => {
  try {
    const uid = activeUserId || userSelect.value;
    if (!uid) {
      setResult("No user selected.");
      return;
    }

    setResult("Disconnecting...");
    const data = await request(`/users/${encodeURIComponent(uid)}/connect`, {
      method: "DELETE",
    });

    renderSources([]);
    activeUserId = null;
    await loadUsers();
    setResult(data.deleted ? "Disconnected and credentials removed." : "No credentials found for this user.");
  } catch (error) {
    setResult(`Disconnect failed: ${error.message}`);
  }
});
