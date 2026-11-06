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
  const confidenceRow = document.getElementById("confidence-row");
  const confidenceLabel = document.getElementById("confidence-label");
  const speedEl = document.getElementById("speed");
  const resultEl = document.getElementById("result");
  const emptyEl = document.getElementById("result-empty");
  const resultCaption = document.getElementById("result-caption");
  const countsEl = document.getElementById("counts");
  const cardEl = document.getElementById("model-card");
  const kindEl = document.getElementById("model-kind");
  const doesEl = document.getElementById("model-does");
  const factsEl = document.getElementById("model-facts");
  const namesNoteEl = document.getElementById("model-names-note");
  const chipsEl = document.getElementById("class-chips");
  const libraryEl = document.getElementById("model-library");
  const namesField = document.getElementById("names-field");

  let model = null;
  let live = false;
  let busy = false;
  let overrideNames = false;

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
    const installed = Boolean(model.installed);
    cardEl.hidden = !installed;
    namesField.hidden = installed && model.names_editable === false;
    namesBtn.hidden = namesField.hidden;
    confidenceRow.hidden = installed && model.uses_confidence === false;
    if (installed) {
      const size = formatBytes(model.bytes);
      statusEl.textContent = "Using " + (model.filename || "model") + (size ? " (" + size + ")" : "") + ".";
      statusEl.className = model.library_ready ? "msg ok" : "msg";
      kindEl.textContent = model.kind || "Model";
      doesEl.textContent = model.does || "";
      namesNoteEl.textContent = model.names_note || "";
      factsEl.innerHTML = (model.facts || []).map((row) =>
        "<div><dt>" + escapeHtml(row.label) + "</dt><dd>" + escapeHtml(row.value) + "</dd></div>"
      ).join("");
      chipsEl.hidden = model.names_editable === false;
      chipsEl.innerHTML = (model.class_names || []).map((name, index) => {
        const color = (model.class_colors || [])[index] || "#c4a35a";
        return "<li><span class=\"swatch\" style=\"background:" + color + "\"></span>" + escapeHtml(name) + "</li>";
      }).join("");
      libraryEl.hidden = !model.library_error;
      libraryEl.textContent = model.library_error || "";
      if (document.activeElement !== namesEl) {
        namesEl.value = (model.class_names || []).join(", ");
      }
      confidenceLabel.textContent = model.confidence_label || "Confidence";
      if (!busy) checkBtn.textContent = model.check_label || "Check this frame";
      resultCaption.textContent = model.result_label || "Model result";
      if (resultEl.hidden) emptyEl.textContent = model.empty_label || "The model result will show up here.";
    } else if (!model.library_ready) {
      statusEl.textContent = "The model software is still installing on this kit. You can upload a file now, and Check will work when installation finishes.";
      statusEl.className = "msg";
    } else {
      statusEl.textContent = "No model yet. Upload a weights file to start.";
      statusEl.className = "msg";
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
    if (data.result_mode === "scores") {
      const rows = (data.items || []).map((row) =>
        "<li><strong>" + escapeHtml(row.class) + "</strong> · " + Math.round(row.confidence * 100) + "%</li>"
      ).join("");
      const title = data.match
        ? escapeHtml(data.match.class) + " · " + Math.round(data.match.confidence * 100) + "%"
        : "Below the minimum score";
      countsEl.innerHTML = "<p class=\"storage-status-title\">" + title + " in " + seconds + "s</p><ul class=\"storage-status-list\">" + rows + "</ul>";
      if (data.match) setMsg("Checked this frame.", "ok");
      else if (data.best) setMsg("Best guess is " + data.best.class + " at " + Math.round(data.best.confidence * 100) + "%.", "ok");
      else setMsg("No class score came back.", "err");
      return;
    }
    if (data.result_mode === "areas") {
      const rows = (data.areas || []).map((row) =>
        "<li><strong>" + escapeHtml(row.class) + "</strong> · " + Math.round(row.share * 100) + "% of the frame</li>"
      ).join("");
      countsEl.innerHTML = "<p class=\"storage-status-title\">Segmented in " + seconds + "s</p><ul class=\"storage-status-list\">" + rows + "</ul>";
      setMsg("Segmented this frame.", "ok");
      return;
    }
    if (data.result_mode === "depth") {
      countsEl.innerHTML = "<p class=\"storage-status-title\">Relative distance in " + seconds + "s</p><p class=\"hint\">Brighter colors are farther in this frame.</p>";
      setMsg("Estimated depth for this frame.", "ok");
      return;
    }
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
    if (overrideNames) body.append("class_names", namesEl.value);
    uploadBtn.disabled = true;
    setMsg("Uploading model…", "");
    try {
      const response = await fetch("/api/model/upload", { method: "POST", body });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Upload failed.");
      model = data;
      fileEl.value = "";
      overrideNames = false;
      setMsg("Model uploaded.", "ok");
      renderStatus();
    } catch (err) {
      setMsg(err.message || String(err), "err");
    } finally {
      uploadBtn.disabled = false;
      renderStatus();
    }
  });

  fileEl.addEventListener("change", () => {
    overrideNames = false;
  });

  namesEl.addEventListener("input", () => {
    overrideNames = true;
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
