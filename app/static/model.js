(() => {
  const statusEl = document.getElementById("model-status");
  const msgEl = document.getElementById("msg");
  const namesEl = document.getElementById("class-names");
  const fileEl = document.getElementById("weights-file");
  const formEl = document.getElementById("upload-form");
  const uploadBtn = document.getElementById("btn-upload");
  const namesBtn = document.getElementById("btn-names");
  const checkBtn = document.getElementById("btn-check");
  const liveBtn = document.getElementById("btn-live");
  const thresholdEl = document.getElementById("threshold");
  const thresholdValue = document.getElementById("threshold-value");
  const speedEl = document.getElementById("speed");
  const resultEl = document.getElementById("result");
  const emptyEl = document.getElementById("result-empty");
  const countsEl = document.getElementById("counts");

  let model = null;
  let live = false;
  let busy = false;

  function setMsg(text, kind) {
    msgEl.textContent = text || "";
    msgEl.className = "msg" + (kind ? " " + kind : "");
  }

  function formatBytes(bytes) {
    if (!bytes) return "";
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(0) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function renderStatus() {
    if (!model) {
      statusEl.textContent = "Looking for a model…";
      statusEl.className = "msg";
      return;
    }
    if (!model.library_ready) {
      statusEl.textContent = "The model software is still installing on this kit. You can upload a file now, and Check will work when installation finishes.";
      statusEl.className = "msg";
    } else if (!model.installed) {
      statusEl.textContent = "No model yet. Upload a weights file to start.";
      statusEl.className = "msg";
    } else {
      const names = (model.class_names || []).join(", ");
      const size = formatBytes(model.bytes);
      statusEl.textContent = "Using " + (model.filename || "model") + (size ? " (" + size + ")" : "") + ". Classes: " + names + ".";
      statusEl.className = "msg ok";
      if (document.activeElement !== namesEl) {
        namesEl.value = (model.class_names || []).join(", ");
      }
    }
    if (model.error) {
      setMsg(model.error, "err");
    }
    checkBtn.disabled = busy || !model.installed || !model.library_ready;
    liveBtn.disabled = checkBtn.disabled && !live;
  }

  async function refreshStatus() {
    const response = await fetch("/api/model");
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || "Could not read the model.");
    model = data;
    renderStatus();
  }

  function renderResult(data) {
    resultEl.src = data.image;
    resultEl.hidden = false;
    emptyEl.hidden = true;
    const seconds = (data.elapsed_ms / 1000).toFixed(1);
    if (!data.total) {
      countsEl.innerHTML = "";
      setMsg("Nothing found in " + seconds + "s. Lower the confidence and try again.", "ok");
      return;
    }
    const rows = (data.counts || [])
      .map((row) => "<li><strong>" + escapeHtml(row.class) + "</strong> · " + row.count + "</li>")
      .join("");
    countsEl.innerHTML = "<p class=\"storage-status-title\">Found " + data.total + " in " + seconds + "s</p><ul class=\"storage-status-list\">" + rows + "</ul>";
    setMsg("Checked this frame.", "ok");
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  async function runOnce() {
    if (busy) return;
    busy = true;
    checkBtn.disabled = true;
    uploadBtn.disabled = true;
    setMsg(model && model.loaded ? "Checking this frame…" : "Loading the model, then checking this frame. The first time can take a few minutes.", "");
    try {
      const response = await fetch("/api/model/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threshold: Number(thresholdEl.value),
          speed: speedEl ? speedEl.value : "fast",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        if (response.status === 409 && live) {
          setMsg(data.error || "Still working…", "");
          return;
        }
        throw new Error(data.error || "The model could not check this frame.");
      }
      if (model) model.loaded = true;
      renderResult(data);
    } catch (err) {
      setMsg(err.message || String(err), "err");
      live = false;
      liveBtn.setAttribute("aria-pressed", "false");
      liveBtn.textContent = "Keep checking";
    } finally {
      busy = false;
      uploadBtn.disabled = false;
      renderStatus();
    }
  }

  thresholdEl.addEventListener("input", () => {
    thresholdValue.textContent = Math.round(Number(thresholdEl.value) * 100) + "%";
  });

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!fileEl.files || !fileEl.files[0]) {
      setMsg("Choose a .pt or .pth weights file first.", "err");
      return;
    }
    const body = new FormData();
    body.append("file", fileEl.files[0]);
    body.append("class_names", namesEl.value);
    uploadBtn.disabled = true;
    setMsg("Uploading model…", "");
    try {
      const response = await fetch("/api/model/upload", { method: "POST", body });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Upload failed.");
      model = data;
      fileEl.value = "";
      setMsg("Model uploaded.", "ok");
      renderStatus();
    } catch (err) {
      setMsg(err.message || String(err), "err");
    } finally {
      uploadBtn.disabled = false;
      renderStatus();
    }
  });

  namesBtn.addEventListener("click", async () => {
    namesBtn.disabled = true;
    try {
      const response = await fetch("/api/model/names", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ class_names: namesEl.value }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not save names.");
      model = data;
      setMsg("Class names saved.", "ok");
      renderStatus();
    } catch (err) {
      setMsg(err.message || String(err), "err");
    } finally {
      namesBtn.disabled = false;
    }
  });

  checkBtn.addEventListener("click", () => {
    runOnce();
  });

  liveBtn.addEventListener("click", async () => {
    live = !live;
    liveBtn.setAttribute("aria-pressed", live ? "true" : "false");
    liveBtn.textContent = live ? "Stop checking" : "Keep checking";
    while (live) {
      if (!busy) await runOnce();
      if (!live) break;
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  });

  refreshStatus().catch((err) => {
    statusEl.textContent = err.message || String(err);
    statusEl.className = "msg err";
  });
})();
