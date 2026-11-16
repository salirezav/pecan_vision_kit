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
  const libraryHintEl = document.getElementById("model-library-hint");
  const modelListEl = document.getElementById("model-list");
  const modelListEmptyEl = document.getElementById("model-list-empty");
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
  const metricFormulaInput = document.getElementById("metric-formula-input");
  const metricFormulaSuggest = document.getElementById("metric-formula-suggest");
  const metricFormulaEl = document.getElementById("metric-formula");
  const metricSaveBtn = document.getElementById("btn-metric-save");
  const metricCancelBtn = document.getElementById("btn-metric-cancel");
  const resetRunBtn = document.getElementById("btn-reset-run");
  const cameraWrap = document.getElementById("camera-wrap");
  const cameraPreview = document.getElementById("camera-preview");
  const roiStatusEl = document.getElementById("roi-status");
  const roiRedrawBtn = document.getElementById("btn-roi-redraw");
  const roiClearBtn = document.getElementById("btn-roi-clear");

  const PECAN_REQUIRED = [
    "pecan_uncracked",
    "pecan_hairline",
    "pecan_open",
    "pecan_crushed",
    "kernel_region",
    "kernel_broken",
  ];
  const MIN_PECANS = 200;

  function emptyPecanRun() {
    return {
      whole_pecan_areas: [],
      broken_kernel_area: 0,
      exposed_kernel_ratios: [],
      n: 0,
      has_masks: false,
    };
  }

  let model = null;
  let live = false;
  let busy = false;
  let selecting = false;
  let runAbort = null;
  let overrideNames = false;
  let metrics = [];
  let editingId = null;
  let frameMeasurements = null;
  let runSums = {};
  let cameraRoi = null;
  let roiDrawer = null;
  let runFrames = 0;
  let pecanRun = emptyPecanRun();
  let suggestIndex = -1;

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
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;");
  }

  function classNames() {
    return (model && model.class_names) || [];
  }

  function taskSupportsMetrics() {
    const task = model && model.task;
    return task === "segment" || task === "detect" || task === "pose" || task === "obb" || task === "semantic";
  }

  function pecanClassesReady() {
    const known = new Set(classNames());
    return PECAN_REQUIRED.every((name) => known.has(name));
  }

  function normalizeMetric(metric) {
    return window.MetricFormula.normalizeMetric(metric);
  }

  function metricFormula(metric) {
    return normalizeMetric(metric).formula || "";
  }

  function resetBuilder(defaults) {
    editingId = null;
    metricNameEl.value = defaults && defaults.name ? defaults.name : "";
    metricFormulaInput.value = defaults && defaults.formula ? defaults.formula : "";
    hideSuggest();
    metricCancelBtn.hidden = true;
    metricSaveBtn.textContent = "Save metric";
    updateFormulaPreview();
  }

  function beginEdit(metric) {
    const normalized = normalizeMetric(metric);
    editingId = metric.id;
    metricNameEl.value = normalized.name || "";
    metricFormulaInput.value = normalized.formula || "";
    hideSuggest();
    metricCancelBtn.hidden = false;
    metricSaveBtn.textContent = "Update metric";
    updateFormulaPreview();
    metricNameEl.focus();
  }

  function metricIsStale(metric) {
    const known = new Set(classNames());
    return window.MetricFormula.formulaLabels(metricFormula(metric)).some((name) => !known.has(name));
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

  function formatMetricValue(value) {
    if (value == null || !Number.isFinite(value)) {
      return { text: "—", detail: "No value for this formula" };
    }
    const abs = Math.abs(value);
    let text;
    if (abs >= 100) text = value.toFixed(1);
    else if (abs >= 1) text = value.toFixed(2);
    else text = value.toFixed(3);
    return { text, detail: String(value) };
  }

  function evaluateMetric(metric, source) {
    if (metricIsStale(metric)) return null;
    if (!source) return null;
    try {
      const value = window.MetricFormula.evaluateFormula(metricFormula(metric), source);
      return formatMetricValue(value);
    } catch (_err) {
      return null;
    }
  }

  function updateFormulaPreview() {
    if (!metricFormulaEl) return;
    const formula = metricFormulaInput.value.trim();
    if (!formula) {
      metricFormulaEl.hidden = false;
      metricFormulaEl.textContent = "Enter a formula using count(Class) or area(Class).";
      return;
    }
    try {
      window.MetricFormula.evaluateFormula(formula, {});
      const unknown = window.MetricFormula.formulaLabels(formula).filter((name) => !classNames().includes(name));
      if (unknown.length) {
        metricFormulaEl.hidden = false;
        metricFormulaEl.textContent = "Unknown class: " + unknown.join(", ");
        return;
      }
      metricFormulaEl.hidden = true;
      metricFormulaEl.textContent = "";
    } catch (err) {
      metricFormulaEl.hidden = false;
      metricFormulaEl.textContent = err.message || String(err);
    }
  }

  function hideSuggest() {
    if (!metricFormulaSuggest) return;
    metricFormulaSuggest.hidden = true;
    metricFormulaSuggest.innerHTML = "";
    suggestIndex = -1;
  }

  function activeSuggestToken() {
    const value = metricFormulaInput.value;
    const caret = metricFormulaInput.selectionStart || 0;
    const before = value.slice(0, caret);
    const match = before.match(/(?:count|area)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)$/i);
    if (!match) return null;
    return {
      start: caret - match[1].length,
      end: caret,
      query: match[1].toLowerCase(),
    };
  }

  function renderSuggest() {
    const token = activeSuggestToken();
    if (!token) {
      hideSuggest();
      return;
    }
    const matches = classNames().filter((name) => name.toLowerCase().startsWith(token.query));
    if (!matches.length) {
      hideSuggest();
      return;
    }
    suggestIndex = Math.min(suggestIndex, matches.length - 1);
    if (suggestIndex < 0) suggestIndex = 0;
    metricFormulaSuggest.innerHTML = matches.map((name, index) => (
      "<li><button type=\"button\" data-name=\"" + escapeHtml(name) + "\" class=\"" +
      (index === suggestIndex ? "active" : "") + "\">" + escapeHtml(name) + "</button></li>"
    )).join("");
    metricFormulaSuggest.hidden = false;
  }

  function insertSuggestion(name) {
    const token = activeSuggestToken();
    if (!token) return;
    const value = metricFormulaInput.value;
    const caret = metricFormulaInput.selectionStart || 0;
    const next = value.slice(0, token.start) + name + value.slice(caret);
    metricFormulaInput.value = next;
    const pos = token.start + name.length;
    metricFormulaInput.setSelectionRange(pos, pos);
    hideSuggest();
    updateFormulaPreview();
    metricFormulaInput.focus();
  }

  function median(values) {
    if (!values.length) return null;
    const ordered = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(ordered.length / 2);
    if (ordered.length % 2) return ordered[mid];
    return (ordered[mid - 1] + ordered[mid]) / 2;
  }

  function summarizePecanRun(run) {
    const n = Number(run.n || 0);
    const ready = n >= MIN_PECANS;
    const gateMessage = ready ? null : "insufficient data (" + n + " / " + MIN_PECANS + ")";
    const areas = (run.whole_pecan_areas || []).map(Number).filter((v) => v > 0);
    const exposed = (run.exposed_kernel_ratios || []).map(Number);
    const hasMasks = Boolean(run.has_masks);
    const cards = {
      pecan_exposed_kernel_mean: {
        id: "pecan_exposed_kernel_mean",
        name: "Exposed-kernel mean",
        ready,
        n,
        message: gateMessage,
        value: null,
        ci95: null,
        detail: null,
      },
      pecan_exposed_kernel_median: {
        id: "pecan_exposed_kernel_median",
        name: "Exposed-kernel median",
        ready,
        n,
        message: gateMessage,
        value: null,
        ci95: null,
        detail: null,
      },
      pecan_broken_kernel_area_per_100: {
        id: "pecan_broken_kernel_area_per_100",
        name: "Broken-kernel area per 100 pecans",
        ready,
        n,
        message: gateMessage,
        value: null,
        ci95: null,
        detail: null,
      },
    };
    if (!hasMasks) {
      cards.pecan_exposed_kernel_mean.message = "Needs segmentation masks";
      cards.pecan_exposed_kernel_mean.ready = false;
      cards.pecan_exposed_kernel_median.message = "Needs segmentation masks";
      cards.pecan_exposed_kernel_median.ready = false;
    } else if (ready && exposed.length) {
      const mean = exposed.reduce((sum, value) => sum + value, 0) / exposed.length;
      cards.pecan_exposed_kernel_mean.value = mean;
      if (exposed.length >= 2) {
        const variance = exposed.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (exposed.length - 1);
        const half = 1.96 * Math.sqrt(variance / exposed.length);
        cards.pecan_exposed_kernel_mean.ci95 = [mean - half, mean + half];
      }
      cards.pecan_exposed_kernel_mean.detail = exposed.length + " open pecans · N=" + n;
      cards.pecan_exposed_kernel_median.value = median(exposed);
      cards.pecan_exposed_kernel_median.detail = exposed.length + " open pecans · N=" + n;
    } else if (ready) {
      cards.pecan_exposed_kernel_mean.message = "No open pecans with kernel overlap";
      cards.pecan_exposed_kernel_mean.ready = false;
      cards.pecan_exposed_kernel_median.message = "No open pecans with kernel overlap";
      cards.pecan_exposed_kernel_median.ready = false;
    }
    if (ready && areas.length && n > 0) {
      const medianArea = median(areas);
      if (medianArea > 0) {
        const broken = Number(run.broken_kernel_area || 0);
        cards.pecan_broken_kernel_area_per_100.value = 100 * (broken / medianArea) / n;
        cards.pecan_broken_kernel_area_per_100.detail =
          "broken area " + Math.round(broken) + "px · median pecan " + Math.round(medianArea) + "px · N=" + n;
      } else {
        cards.pecan_broken_kernel_area_per_100.message = "Median pecan area is zero";
        cards.pecan_broken_kernel_area_per_100.ready = false;
      }
    } else if (ready) {
      cards.pecan_broken_kernel_area_per_100.message = "No whole-pecan areas";
      cards.pecan_broken_kernel_area_per_100.ready = false;
    }
    return cards;
  }

  function mergePecanSamples(frame) {
    if (!frame) return;
    pecanRun.whole_pecan_areas = pecanRun.whole_pecan_areas.concat(frame.whole_pecan_areas || []);
    pecanRun.broken_kernel_area += Number(frame.broken_kernel_area || 0);
    pecanRun.exposed_kernel_ratios = pecanRun.exposed_kernel_ratios.concat(frame.exposed_kernel_ratios || []);
    pecanRun.n = pecanRun.whole_pecan_areas.length;
    pecanRun.has_masks = pecanRun.has_masks || Boolean(frame.has_masks);
  }

  function formatBuiltinCard(card) {
    if (!card.ready || card.value == null) {
      return {
        text: "—",
        detail: card.message || "insufficient data",
      };
    }
    const formatted = formatMetricValue(card.value);
    let detail = card.detail || formatted.detail;
    if (card.ci95) {
      detail += " · 95% CI [" + formatMetricValue(card.ci95[0]).text + ", " + formatMetricValue(card.ci95[1]).text + "]";
    }
    return { text: formatted.text, detail };
  }

  function renderBuiltinCards(htmlParts) {
    if (!pecanClassesReady()) return;
    const cards = summarizePecanRun(pecanRun);
    Object.keys(cards).forEach((id) => {
      const card = cards[id];
      const shown = formatBuiltinCard(card);
      htmlParts.push(
        "<article class=\"metric-card builtin\" data-builtin=\"" + escapeHtml(id) + "\">" +
        "<div class=\"metric-card-head\">" +
        "<div><p class=\"metric-card-name\">" + escapeHtml(card.name) +
        "<span class=\"metric-badge\">built-in</span></p>" +
        "<p class=\"metric-card-formula\">Edge-cut pecans excluded · N ≥ " + MIN_PECANS + "</p></div>" +
        "</div>" +
        "<dl class=\"metric-values\">" +
        "<div class=\"metric-value\"><dt>This run</dt><dd>" + escapeHtml(shown.text) +
        "<span class=\"metric-detail\">" + escapeHtml(shown.detail) + "</span></dd></div>" +
        "<div class=\"metric-value\"><dt>Sample size</dt><dd>" + escapeHtml(String(card.n || 0)) +
        "<span class=\"metric-detail\">whole pecans kept</span></dd></div>" +
        "</dl></article>"
      );
    });
  }

  function renderMetrics() {
    if (!metricsPanel) return;
    const show = Boolean(model && model.installed && taskSupportsMetrics());
    metricsPanel.hidden = !show;
    if (!show) return;

    if (!classNames().length) {
      metricsNote.textContent = "Save class names on the model before building a metric.";
    } else if (pecanClassesReady()) {
      metricsNote.textContent =
        "Write a formula with count(Class) or area(Class). This class set also unlocks three built-in pecan KPIs that need per-pecan geometry.";
    } else {
      metricsNote.textContent =
        "Write a formula with count(Class) or area(Class). Keep checking accumulates class totals across frames, then evaluates the formula on those sums.";
    }

    const parts = [];
    renderBuiltinCards(parts);
    metrics.forEach((metric) => {
      const normalized = normalizeMetric(metric);
      const stale = metricIsStale(normalized);
      const formula = normalized.formula || "";
      const frame = stale ? null : evaluateMetric(normalized, frameMeasurements);
      const run = stale ? null : evaluateMetric(normalized, runFrames ? runSums : null);
      const frameText = frame ? frame.text : "—";
      const frameDetail = stale
        ? "Labels missing from this model"
        : (frame ? frame.detail : "Check a frame to fill this in");
      const runText = run ? run.text : "—";
      const runDetail = stale
        ? "Labels missing from this model"
        : (runFrames
          ? runDetailWithFrames(run)
          : "Start Keep checking to accumulate a run");
      parts.push(
        "<article class=\"metric-card" + (stale ? " stale" : "") + "\" data-id=\"" + escapeHtml(metric.id) + "\">" +
        "<div class=\"metric-card-head\">" +
        "<div><p class=\"metric-card-name\">" + escapeHtml(normalized.name) + "</p>" +
        "<p class=\"metric-card-formula\">" + escapeHtml(formula) + "</p></div>" +
        "<div class=\"metric-card-actions\">" +
        "<button class=\"btn\" type=\"button\" data-action=\"edit\">Edit</button>" +
        "<button class=\"btn\" type=\"button\" data-action=\"delete\">Delete</button>" +
        "</div></div>" +
        "<dl class=\"metric-values\">" +
        "<div class=\"metric-value\"><dt>This frame</dt><dd>" + escapeHtml(frameText) +
        "<span class=\"metric-detail\">" + escapeHtml(frameDetail) + "</span></dd></div>" +
        "<div class=\"metric-value\"><dt>This run</dt><dd>" + escapeHtml(runText) +
        "<span class=\"metric-detail\">" + escapeHtml(runDetail) + "</span></dd></div>" +
        "</dl></article>"
      );
    });
    if (!parts.length) {
      metricsList.innerHTML = "<p class=\"hint\">No metrics yet. Enter a formula below and save one.</p>";
    } else {
      metricsList.innerHTML = parts.join("");
    }

    resetRunBtn.disabled = runFrames === 0 && !Object.keys(runSums).length && !(pecanRun.n > 0);
  }

  function runDetailWithFrames(run) {
    const base = run ? run.detail : "—";
    return base + " · " + runFrames + " frame" + (runFrames === 1 ? "" : "s");
  }

  function resetRun() {
    runSums = {};
    runFrames = 0;
    pecanRun = emptyPecanRun();
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
      const problems = [model.library_error, model.inspect_error].filter(Boolean);
      libraryEl.hidden = !problems.length;
      libraryEl.textContent = problems.join(" ");
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
    renderMetrics();
    renderModelList();
  }

  function modelDetail(row) {
    const parts = [];
    if (row.kind) parts.push(row.kind);
    if (row.architecture && row.architecture !== row.kind) parts.push(row.architecture);
    if (row.resolution) parts.push(row.resolution + " px");
    const classCount = Array.isArray(row.class_names) && row.class_names.length
      ? row.class_names.length
      : row.num_classes;
    if (classCount) parts.push(classCount + (Number(classCount) === 1 ? " class" : " classes"));
    const size = formatBytes(row.bytes);
    if (size) parts.push(size);
    if (row.inspect_error) parts.push("Could not read this file");
    return parts.join(" · ");
  }

  function renderModelList() {
    if (!modelListEl) return;
    const rows = (model && model.models) || [];
    if (libraryHintEl && model && model.library_path) {
      libraryHintEl.textContent = "Saved in " + model.library_path + ". Uploading the same file name replaces that model.";
    }
    if (modelListEmptyEl) modelListEmptyEl.hidden = rows.length > 0;
    modelListEl.innerHTML = rows.map((row) => {
      const detail = modelDetail(row);
      return "<li><button type=\"button\" class=\"model-pick" + (row.active ? " active" : "") + "\" data-model-id=\"" + escapeHtml(row.id) + "\" aria-pressed=\"" + (row.active ? "true" : "false") + "\"" + (busy || selecting ? " disabled" : "") + "><span class=\"model-pick-name\">" + escapeHtml(row.filename || row.id) + "</span>" + (detail ? "<span class=\"model-pick-meta\">" + escapeHtml(detail) + "</span>" : "") + "</button></li>";
    }).join("");
  }

  function clearResult() {
    resultEl.hidden = true;
    resultEl.removeAttribute("src");
    emptyEl.hidden = false;
    countsEl.innerHTML = "";
    if (model && model.empty_label) emptyEl.textContent = model.empty_label;
  }

  async function selectModel(id) {
    if (!id || selecting || busy) return;
    if (model && model.active_id === id) return;
    if (live) {
      setLive(false);
      if (runAbort) runAbort.abort();
    }
    selecting = true;
    renderModelList();
    syncActionButtons();
    setMsg("Switching model…", "");
    try {
      const response = await fetch("/api/model/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: id }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not switch models.");
      model = data;
      overrideNames = false;
      frameMeasurements = null;
      resetRun();
      resetBuilder();
      clearResult();
      renderStatus();
      if (!model.error) setMsg("Now using " + (model.filename || "the selected model") + ".", "ok");
    } catch (err) {
      setMsg(err.message || String(err), "err");
      renderModelList();
    } finally {
      selecting = false;
      renderModelList();
      syncActionButtons();
    }
  }

  function modelReady() {
    return Boolean(model && model.installed && model.library_ready);
  }

  function syncActionButtons() {
    const ready = modelReady();
    checkBtn.disabled = busy || live || selecting || !ready;
    startBtn.disabled = live || selecting || !ready;
    stopBtn.disabled = !live;
    renderModelList();
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
      const payload = {
        threshold: Number(thresholdEl.value),
        speed: speedEl ? speedEl.value : "fast",
      };
      if (cameraRoi) payload.roi = cameraRoi;
      else payload.roi = null;
      const response = await fetch("/api/model/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
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
      if (accumulate) {
        addFrameToRun(data.measurements || []);
        if (pecanClassesReady()) mergePecanSamples(data.pecan_samples);
      }
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

  if (modelListEl) {
    modelListEl.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-model-id]");
      if (!button || button.disabled) return;
      selectModel(button.getAttribute("data-model-id"));
    });
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
      clearResult();
      renderStatus();
      if (!model.error) setMsg("Model saved on this kit.", "ok");
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

  metricFormulaInput.addEventListener("input", () => {
    updateFormulaPreview();
    renderSuggest();
  });
  metricFormulaInput.addEventListener("keydown", (event) => {
    if (metricFormulaSuggest.hidden) return;
    const buttons = Array.from(metricFormulaSuggest.querySelectorAll("button"));
    if (!buttons.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      suggestIndex = (suggestIndex + 1) % buttons.length;
      renderSuggest();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      suggestIndex = (suggestIndex - 1 + buttons.length) % buttons.length;
      renderSuggest();
    } else if (event.key === "Enter" || event.key === "Tab") {
      const active = buttons[suggestIndex] || buttons[0];
      if (active) {
        event.preventDefault();
        insertSuggestion(active.getAttribute("data-name"));
      }
    } else if (event.key === "Escape") {
      hideSuggest();
    }
  });
  metricFormulaInput.addEventListener("blur", () => {
    setTimeout(hideSuggest, 120);
  });
  metricFormulaSuggest.addEventListener("mousedown", (event) => {
    const button = event.target.closest("button[data-name]");
    if (!button) return;
    event.preventDefault();
    insertSuggestion(button.getAttribute("data-name"));
  });

  metricCancelBtn.addEventListener("click", () => {
    resetBuilder();
  });

  metricForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = metricNameEl.value.trim();
    const formula = metricFormulaInput.value.trim();
    if (!name) {
      setMsg("Give the metric a name.", "err");
      return;
    }
    if (!formula) {
      setMsg("Enter a formula.", "err");
      return;
    }
    try {
      window.MetricFormula.evaluateFormula(formula, {});
    } catch (err) {
      setMsg(err.message || String(err), "err");
      return;
    }
    const unknown = window.MetricFormula.formulaLabels(formula).filter((label) => !classNames().includes(label));
    if (unknown.length) {
      setMsg("Unknown class: " + unknown.join(", "), "err");
      return;
    }
    if (metrics.length >= 8 && !editingId) {
      setMsg("Keep at most 8 metrics.", "err");
      return;
    }
    const entry = {
      id: editingId || undefined,
      name,
      formula,
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

  function setRoiStatus(roi) {
    if (!roiStatusEl) return;
    if (!roi) {
      roiStatusEl.textContent = "Using the full frame.";
      roiStatusEl.className = "msg";
      return;
    }
    roiStatusEl.textContent =
      "Region " + roi.w + "×" + roi.h + " at (" + roi.x + ", " + roi.y + ").";
    roiStatusEl.className = "msg ok";
  }

  async function persistRoi(roi) {
    const response = await fetch("/api/model/roi", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roi: roi }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not save the region.");
    cameraRoi = data.roi || roi;
    setRoiStatus(cameraRoi);
    if (roiDrawer) roiDrawer.setRoi(cameraRoi);
  }

  async function clearPersistedRoi() {
    const response = await fetch("/api/model/roi", { method: "DELETE" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not clear the region.");
    cameraRoi = null;
    setRoiStatus(null);
    if (roiDrawer) roiDrawer.clear();
  }

  async function refreshRoi() {
    const response = await fetch("/api/model/roi");
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not load the region.");
    cameraRoi = data.roi || null;
    setRoiStatus(cameraRoi);
    if (roiDrawer) roiDrawer.setRoi(cameraRoi);
  }

  if (cameraWrap && cameraPreview && window.RoiDrawer) {
    roiDrawer = new window.RoiDrawer({
      wrap: cameraWrap,
      img: cameraPreview,
      onCommit: (roi) => {
        persistRoi(roi).catch((err) => setMsg(err.message || String(err), "err"));
      },
      onClear: () => {
        cameraRoi = null;
        setRoiStatus(null);
      },
    });
    cameraPreview.addEventListener("load", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
    window.addEventListener("resize", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
  }

  if (roiRedrawBtn) {
    roiRedrawBtn.addEventListener("click", () => {
      if (roiDrawer) roiDrawer.redraw();
      setMsg("Drag on the live camera to draw a new region.", "");
    });
  }

  if (roiClearBtn) {
    roiClearBtn.addEventListener("click", () => {
      clearPersistedRoi()
        .then(() => setMsg("Region cleared. Using the full frame.", "ok"))
        .catch((err) => setMsg(err.message || String(err), "err"));
    });
  }

  Promise.all([refreshStatus(), refreshMetrics(), refreshRoi()]).catch((err) => {
    statusEl.textContent = err.message || String(err);
    statusEl.className = "msg err";
  });
})();
