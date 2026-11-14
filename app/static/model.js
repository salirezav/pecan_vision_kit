(() => {
  const statusEl = document.getElementById("model-status");
  const msgEl = document.getElementById("msg");
  const namesEl = document.getElementById("class-names");
  const fileEl = document.getElementById("weights-file");
  const formEl = document.getElementById("upload-form");
  const uploadBtn = document.getElementById("btn-upload");
  const namesBtn = document.getElementById("btn-names");
  const checkBtn = document.getElementById("btn-check");
  const startBtn = document.getElementById("btn-start");
  const stopBtn = document.getElementById("btn-stop");
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
  const metricsPanel = document.getElementById("metrics-panel");
  const metricsNote = document.getElementById("metrics-note");
  const metricsList = document.getElementById("metrics-list");
  const metricForm = document.getElementById("metric-form");
  const metricNameEl = document.getElementById("metric-name");
  const metricMeasureRow = document.getElementById("metric-measure-row");
  const metricNumEl = document.getElementById("metric-num");
  const metricDenEl = document.getElementById("metric-den");
  const metricFormulaEl = document.getElementById("metric-formula");
  const metricSaveBtn = document.getElementById("btn-metric-save");
  const metricCancelBtn = document.getElementById("btn-metric-cancel");
  const resetRunBtn = document.getElementById("btn-reset-run");

  let model = null;
  let live = false;
  let busy = false;
  let runAbort = null;
  let overrideNames = false;
  let metrics = [];
  let editingId = null;
  let frameMeasurements = null;
  let runSums = {};
  let runFrames = 0;

  function setMsg(text, kind) {
    msgEl.textContent = text || "";
    msgEl.className = "msg" + (kind ? " " + kind : "");
  }

  function formatBytes(bytes) {
    if (!bytes) return "";
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(0) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function classNames() {
    return (model && model.class_names) || [];
  }

  function taskSupportsMetrics() {
    const task = model && model.task;
    return task === "segment" || task === "detect" || task === "pose" || task === "obb" || task === "semantic";
  }

  function supportsArea() {
    const task = model && model.task;
    return task === "segment" || task === "semantic";
  }

  function supportsCount() {
    const task = model && model.task;
    return task === "segment" || task === "detect" || task === "pose" || task === "obb";
  }

  function selectedMeasure() {
    const checked = metricForm.querySelector('input[name="metric-measure"]:checked');
    return checked ? checked.value : "area";
  }

  function setMeasureOptions() {
    const areaInput = metricForm.querySelector('input[value="area"]');
    const countInput = metricForm.querySelector('input[value="count"]');
    const areaLabel = areaInput.closest("label");
    const countLabel = countInput.closest("label");
    const areaOk = supportsArea();
    const countOk = supportsCount();
    areaLabel.hidden = !areaOk;
    countLabel.hidden = !countOk;
    metricMeasureRow.hidden = !(areaOk || countOk);
    if (areaInput.checked && !areaOk && countOk) countInput.checked = true;
    else if (countInput.checked && !countOk && areaOk) areaInput.checked = true;
    else if (!areaInput.checked && !countInput.checked) {
      if (areaOk) areaInput.checked = true;
      else if (countOk) countInput.checked = true;
    }
  }

  function selectedLabels(listEl) {
    return Array.from(listEl.querySelectorAll("input:checked")).map((input) => input.value);
  }

  function formatTerm(measure, labels) {
    if (!labels.length) return "—";
    if (labels.length === 1) return measure + "(" + labels[0] + ")";
    return labels.map((name) => measure + "(" + name + ")").join(" + ");
  }

  function formatFormula(measure, numerator, denominator) {
    const top = formatTerm(measure, numerator);
    const bottom = formatTerm(measure, denominator);
    if (denominator.length > 1) return top + " / (" + bottom + ")";
    return top + " / " + bottom;
  }

  function updateFormulaPreview() {
    const measure = selectedMeasure();
    const numerator = selectedLabels(metricNumEl);
    const denominator = selectedLabels(metricDenEl);
    if (!numerator.length || !denominator.length) {
      metricFormulaEl.textContent = "Choose at least one label on each side.";
      return;
    }
    metricFormulaEl.textContent = formatFormula(measure, numerator, denominator);
  }

  function renderChipList(listEl, selected) {
    const names = classNames();
    const selectedSet = new Set(selected);
    listEl.innerHTML = names.map((name) => {
      const checked = selectedSet.has(name) ? " checked" : "";
      return (
        "<li><label><input type=\"checkbox\" value=\"" +
        escapeHtml(name) +
        "\"" +
        checked +
        "> " +
        escapeHtml(name) +
        "</label></li>"
      );
    }).join("");
  }

  function resetBuilder(defaults) {
    editingId = null;
    metricNameEl.value = defaults && defaults.name ? defaults.name : "";
    setMeasureOptions();
    const measure = defaults && defaults.measure
      ? defaults.measure
      : (supportsArea() ? "area" : "count");
    const measureInput = metricForm.querySelector('input[name="metric-measure"][value="' + measure + '"]');
    if (measureInput && !measureInput.closest("label").hidden) measureInput.checked = true;
    const names = classNames();
    renderChipList(metricNumEl, (defaults && defaults.numerator) || []);
    renderChipList(metricDenEl, (defaults && defaults.denominator) || names);
    metricCancelBtn.hidden = true;
    metricSaveBtn.textContent = "Save metric";
    updateFormulaPreview();
  }

  function beginEdit(metric) {
    editingId = metric.id;
    metricNameEl.value = metric.name || "";
    setMeasureOptions();
    const measureInput = metricForm.querySelector('input[name="metric-measure"][value="' + metric.measure + '"]');
    if (measureInput && !measureInput.closest("label").hidden) measureInput.checked = true;
    renderChipList(metricNumEl, metric.numerator || []);
    renderChipList(metricDenEl, metric.denominator || []);
    metricCancelBtn.hidden = false;
    metricSaveBtn.textContent = "Update metric";
    updateFormulaPreview();
    metricNameEl.focus();
  }

  function metricIsStale(metric) {
    const known = new Set(classNames());
    const labels = [].concat(metric.numerator || [], metric.denominator || []);
    return labels.some((name) => !known.has(name));
  }

  function sumLabels(source, labels, measure) {
    let total = 0;
    for (const name of labels) {
      const row = source && source[name];
      if (!row) continue;
      total += Number(row[measure] || 0);
    }
    return total;
  }

  function measurementsMap(rows) {
    const map = {};
    (rows || []).forEach((row) => {
      map[row.class] = {
        count: Number(row.count || 0),
        area: Number(row.area || 0),
      };
    });
    return map;
  }

  function formatRatio(numerator, denominator, measure) {
    if (!denominator) {
      return { percent: "—", detail: "No " + measure + " in the chosen labels" };
    }
    const percent = ((numerator / denominator) * 100).toFixed(1) + "%";
    const unit = measure === "area" ? "px" : "";
    const detail = numerator.toLocaleString() + unit + " / " + denominator.toLocaleString() + unit;
    return { percent, detail };
  }

  function evaluateMetric(metric, source) {
    if (metricIsStale(metric)) return null;
    if (!source) return null;
    const numerator = sumLabels(source, metric.numerator || [], metric.measure);
    const denominator = sumLabels(source, metric.denominator || [], metric.measure);
    return formatRatio(numerator, denominator, metric.measure);
  }

  function ensureBuilderChips() {
    if (!taskSupportsMetrics() || !classNames().length) return;
    if (metricNumEl.children.length || metricDenEl.children.length || editingId) return;
    resetBuilder();
  }

  function renderMetrics() {
    if (!metricsPanel) return;
    const show = Boolean(model && model.installed && taskSupportsMetrics());
    metricsPanel.hidden = !show;
    if (!show) return;

    setMeasureOptions();
    ensureBuilderChips();

    if (!classNames().length) {
      metricsNote.textContent = "Save class names on the model before building a metric.";
    } else {
      metricsNote.textContent =
        "Build a ratio from class labels. Keep checking accumulates the chosen labels across frames, then divides those sums.";
    }

    if (!metrics.length) {
      metricsList.innerHTML = "<p class=\"hint\">No metrics yet. Choose labels below and save one.</p>";
    } else {
      metricsList.innerHTML = metrics.map((metric) => {
        const stale = metricIsStale(metric);
        const formula = formatFormula(metric.measure, metric.numerator || [], metric.denominator || []);
        const frame = stale ? null : evaluateMetric(metric, frameMeasurements);
        const run = stale ? null : evaluateMetric(metric, runFrames ? runSums : null);
        const framePercent = frame ? frame.percent : "—";
        const frameDetail = stale
          ? "Labels missing from this model"
          : (frame ? frame.detail : "Check a frame to fill this in");
        const runPercent = run ? run.percent : "—";
        const runDetail = stale
          ? "Labels missing from this model"
          : (runFrames
            ? run.detail + " · " + runFrames + " frame" + (runFrames === 1 ? "" : "s")
            : "Start Keep checking to accumulate a run");
        return (
          "<article class=\"metric-card" + (stale ? " stale" : "") + "\" data-id=\"" + escapeHtml(metric.id) + "\">" +
          "<div class=\"metric-card-head\">" +
          "<div><p class=\"metric-card-name\">" + escapeHtml(metric.name) + "</p>" +
          "<p class=\"metric-card-formula\">" + escapeHtml(formula) + "</p></div>" +
          "<div class=\"metric-card-actions\">" +
          "<button class=\"btn\" type=\"button\" data-action=\"edit\">Edit</button>" +
          "<button class=\"btn\" type=\"button\" data-action=\"delete\">Delete</button>" +
          "</div></div>" +
          "<dl class=\"metric-values\">" +
          "<div class=\"metric-value\"><dt>This frame</dt><dd>" + escapeHtml(framePercent) +
          "<span class=\"metric-detail\">" + escapeHtml(frameDetail) + "</span></dd></div>" +
          "<div class=\"metric-value\"><dt>This run</dt><dd>" + escapeHtml(runPercent) +
          "<span class=\"metric-detail\">" + escapeHtml(runDetail) + "</span></dd></div>" +
          "</dl></article>"
        );
      }).join("");
    }

    resetRunBtn.disabled = runFrames === 0 && !Object.keys(runSums).length;
  }

  function resetRun() {
    runSums = {};
    runFrames = 0;
    renderMetrics();
  }

  function addFrameToRun(rows) {
    const map = measurementsMap(rows);
    Object.keys(map).forEach((name) => {
      if (!runSums[name]) runSums[name] = { count: 0, area: 0 };
      runSums[name].count += map[name].count;
      runSums[name].area += map[name].area;
    });
    runFrames += 1;
  }

  async function refreshMetrics() {
    const response = await fetch("/api/model/metrics");
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || "Could not read metrics.");
    metrics = data.metrics || [];
    renderMetrics();
  }

  async function persistMetrics(next) {
    const response = await fetch("/api/model/metrics", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metrics: next }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not save metrics.");
    metrics = data.metrics || [];
    renderMetrics();
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
    syncActionButtons();
    setMeasureOptions();
    renderMetrics();
  }

  function modelReady() {
    return Boolean(model && model.installed && model.library_ready);
  }

  function syncActionButtons() {
    const ready = modelReady();
    checkBtn.disabled = busy || live || !ready;
    startBtn.disabled = live || !ready;
    stopBtn.disabled = !live;
  }

  function setLive(on) {
    live = on;
    syncActionButtons();
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

  async function runOnce(accumulate) {
    if (busy) return;
    busy = true;
    syncActionButtons();
    uploadBtn.disabled = true;
    setMsg(model && model.loaded ? "Checking this frame…" : "Loading the model, then checking this frame. The first time can take a few minutes.", "");
    const controller = new AbortController();
    runAbort = controller;
    try {
      const response = await fetch("/api/model/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threshold: Number(thresholdEl.value),
          speed: speedEl ? speedEl.value : "fast",
        }),
        signal: controller.signal,
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
      frameMeasurements = measurementsMap(data.measurements || []);
      if (accumulate) addFrameToRun(data.measurements || []);
      renderResult(data);
      renderMetrics();
      if (live) setMsg("Keep checking is running.", "ok");
    } catch (err) {
      if (err && err.name === "AbortError") {
        return;
      }
      setMsg(err.message || String(err), "err");
      setLive(false);
    } finally {
      if (runAbort === controller) runAbort = null;
      busy = false;
      uploadBtn.disabled = false;
      syncActionButtons();
    }
  }

  thresholdEl.addEventListener("input", () => {
    thresholdValue.textContent = Math.round(Number(thresholdEl.value) * 100) + "%";
  });

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!fileEl.files || !fileEl.files[0]) {
      setMsg("Choose a .pt, .pth, or .hef weights file first.", "err");
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
      frameMeasurements = null;
      resetRun();
      resetBuilder();
      setMsg("Model uploaded.", "ok");
      renderStatus();
      await refreshMetrics();
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
      resetBuilder();
      renderStatus();
    } catch (err) {
      setMsg(err.message || String(err), "err");
    } finally {
      namesBtn.disabled = false;
    }
  });

  checkBtn.addEventListener("click", () => {
    if (live || busy) return;
    runOnce(false);
  });

  startBtn.addEventListener("click", async () => {
    if (live || !modelReady()) return;
    resetRun();
    setLive(true);
    setMsg("Keep checking is running.", "ok");
    while (live) {
      if (!busy) await runOnce(true);
      if (!live) break;
      // Yield to the event loop only — FPS should track model time.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  });

  stopBtn.addEventListener("click", () => {
    if (!live) return;
    setLive(false);
    if (runAbort) runAbort.abort();
    setMsg("Stopped checking.", "ok");
  });

  window.addEventListener("pagehide", () => {
    if (!live && !busy) return;
    setLive(false);
    if (runAbort) runAbort.abort();
  });

  resetRunBtn.addEventListener("click", () => {
    resetRun();
  });

  metricForm.addEventListener("change", (event) => {
    if (event.target && (event.target.name === "metric-measure" || event.target.type === "checkbox")) {
      updateFormulaPreview();
    }
  });

  metricCancelBtn.addEventListener("click", () => {
    resetBuilder();
  });

  metricForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = metricNameEl.value.trim();
    const measure = selectedMeasure();
    const numerator = selectedLabels(metricNumEl);
    const denominator = selectedLabels(metricDenEl);
    if (!name) {
      setMsg("Give the metric a name.", "err");
      return;
    }
    if (!numerator.length || !denominator.length) {
      setMsg("Choose at least one numerator and one denominator label.", "err");
      return;
    }
    if (metrics.length >= 8 && !editingId) {
      setMsg("Keep at most 8 metrics.", "err");
      return;
    }
    const entry = {
      id: editingId || undefined,
      name,
      measure,
      numerator,
      denominator,
    };
    const next = editingId
      ? metrics.map((metric) => (metric.id === editingId ? { ...metric, ...entry, id: editingId } : metric))
      : metrics.concat([entry]);
    metricSaveBtn.disabled = true;
    try {
      await persistMetrics(next);
      resetBuilder();
      setMsg("Metric saved.", "ok");
    } catch (err) {
      setMsg(err.message || String(err), "err");
    } finally {
      metricSaveBtn.disabled = false;
    }
  });

  metricsList.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const card = button.closest(".metric-card");
    if (!card) return;
    const id = card.getAttribute("data-id");
    const metric = metrics.find((row) => row.id === id);
    if (!metric) return;
    if (button.getAttribute("data-action") === "edit") {
      beginEdit(metric);
      return;
    }
    if (button.getAttribute("data-action") === "delete") {
      try {
        await persistMetrics(metrics.filter((row) => row.id !== id));
        if (editingId === id) resetBuilder();
        setMsg("Metric deleted.", "ok");
      } catch (err) {
        setMsg(err.message || String(err), "err");
      }
    }
  });

  Promise.all([refreshStatus(), refreshMetrics()]).catch((err) => {
    statusEl.textContent = err.message || String(err);
    statusEl.className = "msg err";
  });
})();
