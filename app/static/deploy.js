(() => {
  const statusEl = document.getElementById("model-status");
  const msgEl = document.getElementById("msg");
  const startBtn = document.getElementById("btn-start");
  const stopBtn = document.getElementById("btn-stop");
  const resetBtn = document.getElementById("btn-reset-run");
  const speedEl = document.getElementById("speed");
  const speedRow = document.getElementById("speed-row");
  const resultEl = document.getElementById("result");
  const emptyEl = document.getElementById("result-empty");
  const resultCaption = document.getElementById("result-caption");
  const cardEl = document.getElementById("model-card");
  const kindEl = document.getElementById("model-kind");
  const doesEl = document.getElementById("model-does");
  const chipsEl = document.getElementById("class-chips");
  const gridEl = document.getElementById("grafana-grid");
  const noteEl = document.getElementById("grafana-note");
  const metaFrames = document.getElementById("meta-frames");
  const metaElapsed = document.getElementById("meta-elapsed");
  const metaMetrics = document.getElementById("meta-metrics");
  const metaLive = document.getElementById("meta-live");
  const intervalEl = document.getElementById("interval");
  const rateCapEl = document.getElementById("rate-cap");
  const thisRunEl = document.getElementById("this-run");
  const roiStatusEl = document.getElementById("roi-status");
  const cameraWrap = document.getElementById("camera-wrap");
  const cameraPreview = document.getElementById("camera-preview");
  const runsListEl = document.getElementById("runs-list");
  const runDetailEl = document.getElementById("run-detail");
  const runTableWrap = document.getElementById("run-table-wrap");
  const runCsvEl = document.getElementById("run-csv");
  const refreshRunsBtn = document.getElementById("btn-refresh-runs");
  const closeRunBtn = document.getElementById("btn-close-run");

  const COLORS = ["#c4a35a", "#7dcea0", "#5dade2", "#f5b041", "#af7ac5", "#e74c3c", "#48c9b0", "#f4d03f"];
  const HALFYIELD_METRIC_ID = "halfyield_half_share";
  const HALFYIELD_METRIC_ID_LEGACY = "halfyield_mass_share";

  let model = null;
  let metrics = [];
  let live = false;
  let runFrames = 0;
  let runSums = {};
  let histories = {};
  let frameValues = {};
  let pecanBuiltins = {};
  let pecanReady = false;
  let pollTimer = null;
  let runId = null;
  let roiDrawer = null;
  let halfyield = false;
  let halfShare = null;
  let halfShareFrame = null;
  let builtinEnabled = {
    pecan_exposed_kernel_mean: true,
    pecan_exposed_kernel_median: true,
    pecan_broken_kernel_area_per_100: true,
    halfyield_half_share: true,
  };

  function mergeBuiltinEnabled(raw) {
    if (!raw || typeof raw !== "object") return;
    builtinEnabled = Object.assign({}, builtinEnabled, raw);
    if (
      raw[HALFYIELD_METRIC_ID] === undefined &&
      raw[HALFYIELD_METRIC_ID_LEGACY] !== undefined
    ) {
      builtinEnabled[HALFYIELD_METRIC_ID] = raw[HALFYIELD_METRIC_ID_LEGACY] !== false;
    }
  }

  function isBuiltinOn(id) {
    if (id === HALFYIELD_METRIC_ID && builtinEnabled[id] === undefined) {
      return builtinEnabled[HALFYIELD_METRIC_ID_LEGACY] !== false;
    }
    return builtinEnabled[id] !== false;
  }

  const calibration = window.CalibrationUi
    ? window.CalibrationUi.bind({
        root: document.getElementById("calibration"),
        sliders: document.getElementById("calibration-sliders"),
        hint: document.getElementById("calibration-hint"),
        onError: (err) => setMsg(err.message || String(err), "err"),
        onChange: (record) => {
          if (model) model.calibration = record;
          // Deploy live loop reloads calibration; refresh UI from status soon.
          if (live) refreshLive({ quiet: true });
        },
      })
    : null;

  function isHalfyield() {
    return Boolean(halfyield || (model && model.halfyield));
  }

  function formatPercent(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    return (value * 100).toFixed(1) + "%";
  }

  function setMsg(text, kind) {
    msgEl.textContent = text || "";
    msgEl.className = "msg" + (kind ? " " + kind : "");
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

  function normalizeMetric(metric) {
    return window.MetricFormula.normalizeMetric(metric);
  }

  function metricFormula(metric) {
    return normalizeMetric(metric).formula || "";
  }

  function metricIsStale(metric) {
    const known = new Set(classNames());
    return window.MetricFormula.formulaLabels(metricFormula(metric)).some((name) => !known.has(name));
  }

  function evaluateMetric(metric, source) {
    if (!source || metricIsStale(metric)) return null;
    try {
      return window.MetricFormula.evaluateFormula(metricFormula(metric), source);
    } catch (_err) {
      return null;
    }
  }

  function formatValue(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    const abs = Math.abs(value);
    if (abs >= 100) return value.toFixed(1);
    if (abs >= 1) return value.toFixed(2);
    return value.toFixed(3);
  }

  function chartSvg(points, color) {
    const width = 320;
    const height = 120;
    const padX = 8;
    const padY = 10;
    const usable = points.filter((value) => value != null && Number.isFinite(value));
    if (!usable.length) {
      return (
        '<svg class="grafana-chart" viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="none" aria-hidden="true">' +
        '<line x1="' + padX + '" y1="' + (height / 2) + '" x2="' + (width - padX) + '" y2="' + (height / 2) + '" class="grafana-gridline" />' +
        "</svg>"
      );
    }
    let min = Math.min(...usable);
    let max = Math.max(...usable);
    if (max === min) {
      const pad = Math.max(Math.abs(max) * 0.05, 0.01);
      min -= pad;
      max += pad;
    }
    const span = Math.max(max - min, 0.01);
    const step = points.length > 1 ? (width - padX * 2) / (points.length - 1) : 0;
    const coords = points.map((value, index) => {
      if (value == null || !Number.isFinite(value)) return null;
      const x = padX + index * step;
      const y = height - padY - ((value - min) / span) * (height - padY * 2);
      return [x, y];
    });
    const valid = coords.filter(Boolean);
    const line = valid.map((pair, index) => (index ? "L" : "M") + pair[0].toFixed(1) + " " + pair[1].toFixed(1)).join(" ");
    const first = valid[0];
    const last = valid[valid.length - 1];
    const area =
      "M" + first[0].toFixed(1) + " " + (height - padY).toFixed(1) +
      " L" + valid.map((pair) => pair[0].toFixed(1) + " " + pair[1].toFixed(1)).join(" L") +
      " L" + last[0].toFixed(1) + " " + (height - padY).toFixed(1) + " Z";
    const gridY = [0.25, 0.5, 0.75].map((fraction) => {
      const y = padY + (height - padY * 2) * fraction;
      return '<line x1="' + padX + '" y1="' + y.toFixed(1) + '" x2="' + (width - padX) + '" y2="' + y.toFixed(1) + '" class="grafana-gridline" />';
    }).join("");
    return (
      '<svg class="grafana-chart" viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="none" role="img">' +
      gridY +
      '<path d="' + area + '" fill="' + color + '" fill-opacity="0.18"></path>' +
      '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"></path>' +
      "</svg>"
    );
  }

  function renderBuiltinPanels(parts, colorOffset) {
    if (!pecanReady) return colorOffset;
    const ids = [
      "pecan_exposed_kernel_mean",
      "pecan_exposed_kernel_median",
      "pecan_broken_kernel_area_per_100",
    ].filter((id) => isBuiltinOn(id));
    ids.forEach((id, index) => {
      const card = pecanBuiltins[id] || { id, name: id, ready: false, message: "insufficient data" };
      const color = COLORS[(colorOffset + index) % COLORS.length];
      const series = histories[id] || [];
      const valueText = card.value != null ? formatValue(card.value) : "—";
      let detail = card.detail || card.message || "Waiting for pecan samples";
      if (!card.ready && card.message) {
        detail = card.message + (card.detail ? " · " + card.detail : "");
      } else if (card.ready && card.ci95) {
        detail = (card.detail || "") + " · 95% CI [" + formatValue(card.ci95[0]) + ", " + formatValue(card.ci95[1]) + "]";
      }
      parts.push(
        '<article class="grafana-panel' + (card.ready ? "" : " stale") + '">' +
        '<div class="grafana-panel-head">' +
        '<div><p class="grafana-title">' + escapeHtml(card.name || id) + " <span class=\"metric-badge\">built-in</span></p>" +
        '<p class="grafana-formula">Edge-cut pecans excluded · N ≥ 200</p></div>' +
        '<p class="grafana-big" style="color:' + color + '">' + escapeHtml(valueText) + "</p>" +
        "</div>" +
        chartSvg(series, color) +
        '<div class="grafana-foot">' +
        "<span>N <strong>" + escapeHtml(String(card.n || 0)) + "</strong></span>" +
        "<span>" + escapeHtml(detail) + "</span>" +
        "</div></article>"
      );
    });
    return colorOffset + ids.length;
  }

  function renderBoard() {
    metaFrames.textContent = String(runFrames);
    metaLive.textContent = live ? "Live" : (runFrames ? "Stopped" : "Idle");

    if (isHalfyield()) {
      if (!isBuiltinOn(HALFYIELD_METRIC_ID)) {
        metaMetrics.textContent = "0";
        noteEl.textContent = "Half yield is disabled on the Model page.";
        gridEl.innerHTML = '<div class="grafana-empty">Enable Half yield on the Model page to chart it here.</div>';
        return;
      }
      metaMetrics.textContent = "1";
      const color = COLORS[0];
      const series = histories[HALFYIELD_METRIC_ID] || histories[HALFYIELD_METRIC_ID_LEGACY] || [];
      const runText = formatPercent(halfShare);
      const frame = frameValues[HALFYIELD_METRIC_ID] || frameValues[HALFYIELD_METRIC_ID_LEGACY];
      const frameText = frame && frame.value != null
        ? formatPercent(frame.value)
        : formatPercent(halfShareFrame);
      noteEl.textContent = live
        ? "Half yield updating on each sample."
        : (runFrames
          ? "Last half-yield run is still on the chart. Start again for a new series."
          : "Press Start live run to fill the half-yield chart.");
      gridEl.innerHTML =
        '<article class="grafana-panel">' +
        '<div class="grafana-panel-head">' +
        '<div><p class="grafana-title">Half yield <span class="metric-badge">built-in</span></p>' +
        '<p class="grafana-formula">half / (half + piece)</p></div>' +
        '<p class="grafana-big" style="color:' + color + '">' + escapeHtml(runText) + "</p>" +
        "</div>" +
        chartSvg(series.map((value) => (value == null ? null : value * 100)), color) +
        '<div class="grafana-foot">' +
        "<span>This frame <strong>" + escapeHtml(frameText) + "</strong></span>" +
        "<span>Time-integrated half yield for the run</span>" +
        "</div></article>";
      return;
    }

    const enabledMetrics = metrics.filter((metric) => metric && metric.enabled !== false);
    const pecanCount = pecanReady
      ? ["pecan_exposed_kernel_mean", "pecan_exposed_kernel_median", "pecan_broken_kernel_area_per_100"]
        .filter((id) => isBuiltinOn(id)).length
      : 0;
    const panelCount = enabledMetrics.length + pecanCount;
    metaMetrics.textContent = String(panelCount);
    const parts = [];
    let colorOffset = renderBuiltinPanels(parts, 0);
    enabledMetrics.forEach((metric, index) => {
      const normalized = normalizeMetric(metric);
      const stale = metricIsStale(normalized);
      const color = COLORS[(colorOffset + index) % COLORS.length];
      const formula = normalized.formula || "";
      const frame = frameValues[metric.id];
      const runValue = evaluateMetric(normalized, runFrames ? runSums : null);
      const frameText = stale ? "—" : (frame && frame.value != null ? formatValue(frame.value) : "—");
      const runText = stale ? "—" : formatValue(runValue);
      const detail = stale
        ? "Labels missing from this model"
        : (runValue != null
          ? "Formula result on accumulated class totals"
          : "Waiting for labels in the run");
      const series = histories[metric.id] || [];
      parts.push(
        '<article class="grafana-panel' + (stale ? " stale" : "") + '">' +
        '<div class="grafana-panel-head">' +
        '<div><p class="grafana-title">' + escapeHtml(normalized.name) + "</p>" +
        '<p class="grafana-formula">' + escapeHtml(formula) + "</p></div>" +
        '<p class="grafana-big" style="color:' + color + '">' + escapeHtml(runText) + "</p>" +
        "</div>" +
        chartSvg(series, color) +
        '<div class="grafana-foot">' +
        "<span>This frame <strong>" + escapeHtml(frameText) + "</strong></span>" +
        "<span>" + escapeHtml(detail) + "</span>" +
        "</div></article>"
      );
    });

    if (!parts.length) {
      noteEl.textContent = "No enabled metrics. Open Model, enable a KPI or save a formula, then come back here.";
      gridEl.innerHTML = '<div class="grafana-empty">Enabled metrics from the Model page appear here as live charts.</div>';
      return;
    }
    noteEl.textContent = live
      ? "Updating on each inference frame."
      : (runFrames ? "Last run is still on the charts. Start again to begin a new series." : "Press Start live run to fill the charts.");
    gridEl.innerHTML = parts.join("");
  }

  function modelReady() {
    return Boolean(model && model.installed && model.library_ready);
  }

  function syncLiveButtons() {
    const ready = modelReady();
    startBtn.disabled = live || !ready;
    stopBtn.disabled = !live;
    resetBtn.disabled = live;
    if (speedEl) speedEl.disabled = live;
    if (calibration && typeof calibration.setDisabled === "function") {
      calibration.setDisabled(live);
    }
  }

  function showResult(imageUrl) {
    if (!imageUrl) return;
    resultEl.src = imageUrl;
    resultEl.hidden = false;
    emptyEl.hidden = true;
  }

  function updateRateCap(data) {
    if (!rateCapEl) return;
    const minInterval = data.min_interval_sec;
    const maxHz = data.max_hz;
    const requested = data.requested_interval_sec;
    const actual = data.interval_sec;
    if (minInterval == null || maxHz == null) {
      rateCapEl.hidden = true;
      rateCapEl.textContent = "";
      return;
    }
    rateCapEl.hidden = false;
    let text = "Limited to ~" + Number(maxHz).toFixed(2) + " Hz by current inference speed.";
    if (requested != null && actual != null && Number(actual) > Number(requested) + 0.01) {
      text += " Using " + Number(actual).toFixed(2) + " s between samples.";
    }
    rateCapEl.textContent = text;
  }

  function updateThisRun(data) {
    runId = data.run_id || runId;
    if (!thisRunEl) return;
    if (live && runId) {
      thisRunEl.hidden = false;
      thisRunEl.innerHTML =
        "This run: <a href=\"/api/deploy/runs/" +
        encodeURIComponent(runId) +
        "/csv\">download CSV</a> · " +
        runFrames +
        " sample" +
        (runFrames === 1 ? "" : "s");
    } else if (runId && runFrames) {
      thisRunEl.hidden = false;
      thisRunEl.innerHTML =
        "Last run <code>" +
        escapeHtml(runId) +
        "</code>: <a href=\"/api/deploy/runs/" +
        encodeURIComponent(runId) +
        "/csv\">download CSV</a>";
    } else {
      thisRunEl.hidden = true;
      thisRunEl.textContent = "";
    }
  }

  function updateRoiOverlay(data) {
    const roi = data.roi || null;
    if (roiStatusEl) {
      if (!roi) {
        roiStatusEl.textContent = "Camera region: full frame (set on Model).";
      } else {
        roiStatusEl.textContent =
          "Camera region: " + roi.w + "×" + roi.h + " at (" + roi.x + ", " + roi.y + ").";
      }
    }
    if (roiDrawer) roiDrawer.setRoi(roi);
  }

  function applySnapshot(data) {
    live = Boolean(data.running);
    runFrames = Number(data.frames || 0);
    runSums = data.run_sums || {};
    histories = data.histories || {};
    frameValues = data.frame_values || {};
    pecanBuiltins = data.pecan_builtins || {};
    pecanReady = Boolean(data.pecan_ready);
    metrics = data.metrics || metrics;
    halfyield = Boolean(data.halfyield || (data.model && data.model.halfyield));
    halfShare = data.half_share != null
      ? Number(data.half_share)
      : (data.area_share != null ? Number(data.area_share) : null);
    halfShareFrame = data.half_share_frame != null
      ? Number(data.half_share_frame)
      : (data.area_share != null ? Number(data.area_share) : null);
    mergeBuiltinEnabled(data.builtin_enabled);
    if (data.model) model = data.model;
    if (data.calibration && model) model.calibration = data.calibration;
    if (data.speed && speedEl && document.activeElement !== speedEl) {
      speedEl.value = data.speed;
    }
    if (data.interval_sec != null && intervalEl && document.activeElement !== intervalEl) {
      intervalEl.value = String(data.requested_interval_sec != null ? data.requested_interval_sec : data.interval_sec);
    }
    if (data.elapsed_ms != null) {
      metaElapsed.textContent = (Number(data.elapsed_ms) / 1000).toFixed(1) + "s";
    }
    if (data.image) showResult(data.image);
    else if (!live && !runFrames) {
      resultEl.hidden = true;
      emptyEl.hidden = false;
    }
    updateRateCap(data);
    updateThisRun(data);
    updateRoiOverlay(data);
    if (data.error) setMsg(data.error, "err");
    else if (live) setMsg("Live inference running.", "ok");
    renderStatus();
    renderBoard();
    syncLiveButtons();
    ensurePoll();
  }

  function renderStatus() {
    if (!model) {
      statusEl.textContent = "Looking for a model…";
      statusEl.className = "msg";
      syncLiveButtons();
      return;
    }
    const installed = Boolean(model.installed);
    cardEl.hidden = !installed;
    if (speedRow) speedRow.hidden = installed && isHalfyield();
    if (calibration) calibration.render(model, model.calibration || null);
    if (installed) {
      statusEl.textContent = "Using " + (model.filename || "model") +
        (isHalfyield() ? " · half-yield" : "") + ".";
      statusEl.className = model.library_ready ? "msg ok" : "msg";
      kindEl.textContent = model.kind || "Model";
      doesEl.textContent = model.does || "";
      chipsEl.innerHTML = (model.class_names || []).map((name, index) => {
        const color = (model.class_colors || [])[index] || "#c4a35a";
        return "<li><span class=\"swatch\" style=\"background:" + color + "\"></span>" + escapeHtml(name) + "</li>";
      }).join("");
      resultCaption.textContent = model.result_label || "Model result";
      if (resultEl.hidden) {
        emptyEl.hidden = false;
        emptyEl.textContent = "Start live inference to see the model result here.";
      }
    } else {
      statusEl.textContent = "No model yet. Upload one on the Model page.";
      statusEl.className = "msg";
    }
    syncLiveButtons();
  }

  function ensurePoll() {
    if (live && !pollTimer) {
      pollTimer = setInterval(() => refreshLive({ quiet: true }), 1000);
    } else if (!live && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshLive(opts) {
    const quiet = opts && opts.quiet;
    try {
      const response = await fetch("/api/deploy/live");
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || "Could not read live status.");
      applySnapshot(data);
    } catch (err) {
      if (!quiet) {
        statusEl.textContent = err.message || String(err);
        statusEl.className = "msg err";
      }
    }
  }

  startBtn.addEventListener("click", async () => {
    if (live || !modelReady()) return;
    setMsg("Starting live run…", "");
    resultEl.hidden = true;
    emptyEl.hidden = false;
    emptyEl.textContent = "Waiting for the first sample…";
    try {
      // Persist current calibration so Start and live samples share the same values.
      if (calibration) await calibration.persist();
      const response = await fetch("/api/deploy/live/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          speed: isHalfyield() ? "accurate" : (speedEl ? speedEl.value : "fast"),
          interval_sec: intervalEl ? Number(intervalEl.value) : 1.0,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not start live run.");
      applySnapshot(data);
      setMsg("Live inference running.", "ok");
      refreshRuns();
    } catch (err) {
      emptyEl.textContent = "Start live inference to see the model result here.";
      setMsg(err.message || String(err), "err");
    }
  });

  stopBtn.addEventListener("click", async () => {
    try {
      const response = await fetch("/api/deploy/live/stop", { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not stop live run.");
      applySnapshot(data);
      setMsg("Live run stopped.", "ok");
      refreshRuns();
    } catch (err) {
      setMsg(err.message || String(err), "err");
    }
  });

  resetBtn.addEventListener("click", async () => {
    if (live) return;
    try {
      const response = await fetch("/api/deploy/live/reset", { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not reset charts.");
      applySnapshot(data);
      resultEl.hidden = true;
      emptyEl.hidden = false;
      emptyEl.textContent = "Start live inference to see the model result here.";
      setMsg("Charts cleared.", "ok");
    } catch (err) {
      setMsg(err.message || String(err), "err");
    }
  });

  async function refreshRoi() {
    try {
      const response = await fetch("/api/model/roi");
      const data = await response.json();
      if (!response.ok || !data.ok) return;
      updateRoiOverlay(data);
    } catch (_err) {
      /* ignore */
    }
  }

  async function refreshRuns() {
    if (!runsListEl) return;
    try {
      const response = await fetch("/api/deploy/runs");
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not list runs.");
      const runs = data.runs || [];
      if (!runs.length) {
        runsListEl.innerHTML = "<p class=\"hint\">No saved runs yet. Start and stop a live run to create one.</p>";
        return;
      }
      runsListEl.innerHTML = runs.map((run) => {
        const frames = run.frames == null ? "—" : String(run.frames);
        const when = run.started_at ? escapeHtml(String(run.started_at).replace("T", " ").slice(0, 19)) : escapeHtml(run.id);
        return (
          "<button type=\"button\" class=\"run-row\" data-run-id=\"" +
          escapeHtml(run.id) +
          "\"><strong>" +
          when +
          "</strong><span>" +
          frames +
          " samples · " +
          escapeHtml(run.status || "") +
          (run.model_filename ? " · " + escapeHtml(run.model_filename) : "") +
          "</span></button>"
        );
      }).join("");
    } catch (err) {
      runsListEl.innerHTML = "<p class=\"msg err\">" + escapeHtml(err.message || String(err)) + "</p>";
    }
  }

  async function openRun(id) {
    if (!runDetailEl || !runTableWrap) return;
    try {
      const response = await fetch("/api/deploy/runs/" + encodeURIComponent(id));
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not open that run.");
      const samples = data.samples || [];
      runDetailEl.hidden = false;
      if (runCsvEl) runCsvEl.href = "/api/deploy/runs/" + encodeURIComponent(id) + "/csv";
      if (!samples.length) {
        runTableWrap.innerHTML = "<p class=\"hint\">This run has no sample rows yet.</p>";
        return;
      }
      const keys = Object.keys(samples[0]);
      const head = keys.map((key) => "<th>" + escapeHtml(key) + "</th>").join("");
      const body = samples.slice(0, 200).map((row) => {
        return "<tr>" + keys.map((key) => "<td>" + escapeHtml(row[key] == null ? "" : row[key]) + "</td>").join("") + "</tr>";
      }).join("");
      runTableWrap.innerHTML =
        "<table class=\"run-table\"><thead><tr>" +
        head +
        "</tr></thead><tbody>" +
        body +
        "</tbody></table>" +
        (samples.length > 200 ? "<p class=\"hint\">Showing the first 200 rows. Download the CSV for the full run.</p>" : "");
    } catch (err) {
      setMsg(err.message || String(err), "err");
    }
  }

  if (runsListEl) {
    runsListEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-run-id]");
      if (!button) return;
      openRun(button.getAttribute("data-run-id"));
    });
  }
  if (refreshRunsBtn) refreshRunsBtn.addEventListener("click", () => refreshRuns());
  if (closeRunBtn) {
    closeRunBtn.addEventListener("click", () => {
      if (runDetailEl) runDetailEl.hidden = true;
    });
  }

  if (cameraWrap && cameraPreview && window.RoiDrawer) {
    roiDrawer = new window.RoiDrawer({
      wrap: cameraWrap,
      img: cameraPreview,
      readOnly: true,
    });
    cameraPreview.addEventListener("load", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
    window.addEventListener("resize", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
  }

  Promise.all([refreshLive(), refreshRoi(), refreshRuns()]).catch((err) => {
    statusEl.textContent = err.message || String(err);
    statusEl.className = "msg err";
  });
})();
