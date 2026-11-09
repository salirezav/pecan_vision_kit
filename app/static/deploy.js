(() => {
  const statusEl = document.getElementById("model-status");
  const msgEl = document.getElementById("msg");
  const startBtn = document.getElementById("btn-start");
  const stopBtn = document.getElementById("btn-stop");
  const resetBtn = document.getElementById("btn-reset-run");
  const thresholdEl = document.getElementById("threshold");
  const thresholdValue = document.getElementById("threshold-value");
  const confidenceRow = document.getElementById("confidence-row");
  const confidenceLabel = document.getElementById("confidence-label");
  const speedEl = document.getElementById("speed");
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

  const HISTORY = 90;
  const COLORS = ["#c4a35a", "#7dcea0", "#5dade2", "#f5b041", "#af7ac5", "#e74c3c", "#48c9b0", "#f4d03f"];

  let model = null;
  let metrics = [];
  let live = false;
  let busy = false;
  let runFrames = 0;
  let runSums = {};
  let histories = {};
  let frameValues = {};

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

  function metricIsStale(metric) {
    const known = new Set(classNames());
    return [].concat(metric.numerator || [], metric.denominator || []).some((name) => !known.has(name));
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

  function sumLabels(source, labels, measure) {
    let total = 0;
    for (const name of labels) {
      const row = source && source[name];
      if (!row) continue;
      total += Number(row[measure] || 0);
    }
    return total;
  }

  function ratioParts(metric, source) {
    if (!source || metricIsStale(metric)) return null;
    const numerator = sumLabels(source, metric.numerator || [], metric.measure);
    const denominator = sumLabels(source, metric.denominator || [], metric.measure);
    return { numerator, denominator };
  }

  function formatPercent(numerator, denominator) {
    if (!denominator) return "—";
    return ((numerator / denominator) * 100).toFixed(1) + "%";
  }

  function pushHistory(id, percent) {
    if (!histories[id]) histories[id] = [];
    histories[id].push(percent);
    if (histories[id].length > HISTORY) histories[id].shift();
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
    const min = Math.min(0, ...usable);
    const max = Math.max(100, ...usable);
    const span = Math.max(max - min, 1);
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

  function renderBoard() {
    metaFrames.textContent = String(runFrames);
    metaMetrics.textContent = String(metrics.length);
    metaLive.textContent = live ? "Live" : (runFrames ? "Stopped" : "Idle");

    if (!metrics.length) {
      noteEl.textContent = "No metrics yet. Open Model, save a formula, then come back here.";
      gridEl.innerHTML = '<div class="grafana-empty">Metrics you save on the Model page appear here as live charts.</div>';
      return;
    }
    noteEl.textContent = live
      ? "Updating on each inference frame."
      : (runFrames ? "Last run is still on the charts. Start again to begin a new series." : "Press Start live run to fill the charts.");

    gridEl.innerHTML = metrics.map((metric, index) => {
      const stale = metricIsStale(metric);
      const color = COLORS[index % COLORS.length];
      const formula = formatFormula(metric.measure, metric.numerator || [], metric.denominator || []);
      const frame = frameValues[metric.id];
      const run = ratioParts(metric, runFrames ? runSums : null);
      const frameText = stale ? "—" : (frame && frame.denominator ? formatPercent(frame.numerator, frame.denominator) : "—");
      const runText = stale ? "—" : (run && run.denominator ? formatPercent(run.numerator, run.denominator) : "—");
      const detail = stale
        ? "Labels missing from this model"
        : (run && run.denominator
          ? run.numerator.toLocaleString() + " / " + run.denominator.toLocaleString() + (metric.measure === "area" ? " px" : "")
          : "Waiting for labels in the run");
      const series = histories[metric.id] || [];
      return (
        '<article class="grafana-panel' + (stale ? " stale" : "") + '">' +
        '<div class="grafana-panel-head">' +
        '<div><p class="grafana-title">' + escapeHtml(metric.name) + "</p>" +
        '<p class="grafana-formula">' + escapeHtml(formula) + "</p></div>" +
        '<p class="grafana-big" style="color:' + color + '">' + escapeHtml(runText) + "</p>" +
        "</div>" +
        chartSvg(series, color) +
        '<div class="grafana-foot">' +
        "<span>This frame <strong>" + escapeHtml(frameText) + "</strong></span>" +
        "<span>" + escapeHtml(detail) + "</span>" +
        "</div></article>"
      );
    }).join("");
  }

  function resetRun() {
    runFrames = 0;
    runSums = {};
    histories = {};
    frameValues = {};
    renderBoard();
  }

  function addFrame(rows) {
    const map = measurementsMap(rows);
    Object.keys(map).forEach((name) => {
      if (!runSums[name]) runSums[name] = { count: 0, area: 0 };
      runSums[name].count += map[name].count;
      runSums[name].area += map[name].area;
    });
    runFrames += 1;
    metrics.forEach((metric) => {
      const frame = ratioParts(metric, map);
      frameValues[metric.id] = frame;
      if (!frame || !frame.denominator) {
        pushHistory(metric.id, null);
        return;
      }
      pushHistory(metric.id, (frame.numerator / frame.denominator) * 100);
    });
  }

  function modelReady() {
    return Boolean(model && model.installed && model.library_ready);
  }

  function syncLiveButtons() {
    const ready = modelReady();
    startBtn.disabled = live || !ready;
    stopBtn.disabled = !live;
    resetBtn.disabled = live;
  }

  function setLive(on) {
    live = on;
    syncLiveButtons();
    renderBoard();
  }

  function showResult(imageUrl) {
    resultEl.src = imageUrl;
    resultEl.hidden = false;
    emptyEl.hidden = true;
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
    confidenceRow.hidden = installed && model.uses_confidence === false;
    if (installed) {
      statusEl.textContent = "Using " + (model.filename || "model") + ".";
      statusEl.className = model.library_ready ? "msg ok" : "msg";
      kindEl.textContent = model.kind || "Model";
      doesEl.textContent = model.does || "";
      chipsEl.innerHTML = (model.class_names || []).map((name, index) => {
        const color = (model.class_colors || [])[index] || "#c4a35a";
        return "<li><span class=\"swatch\" style=\"background:" + color + "\"></span>" + escapeHtml(name) + "</li>";
      }).join("");
      confidenceLabel.textContent = model.confidence_label || "Confidence";
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

  async function refreshAll() {
    const [modelRes, metricsRes] = await Promise.all([
      fetch("/api/model"),
      fetch("/api/model/metrics"),
    ]);
    const modelData = await modelRes.json();
    const metricsData = await metricsRes.json();
    if (!modelData.ok) throw new Error(modelData.error || "Could not read the model.");
    if (!metricsData.ok) throw new Error(metricsData.error || "Could not read metrics.");
    model = modelData;
    metrics = metricsData.metrics || [];
    renderStatus();
    renderBoard();
  }

  async function runOnce() {
    if (busy) return;
    busy = true;
    setMsg(model && model.loaded ? "Checking…" : "Loading the model, then checking. The first time can take a few minutes.", "");
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
      showResult(data.image);
      metaElapsed.textContent = (data.elapsed_ms / 1000).toFixed(1) + "s";
      addFrame(data.measurements || []);
      renderBoard();
      if (live) setMsg("Live inference running.", "ok");
    } catch (err) {
      setMsg(err.message || String(err), "err");
      setLive(false);
    } finally {
      busy = false;
      syncLiveButtons();
    }
  }

  thresholdEl.addEventListener("input", () => {
    thresholdValue.textContent = Math.round(Number(thresholdEl.value) * 100) + "%";
  });

  startBtn.addEventListener("click", async () => {
    if (live || !modelReady()) return;
    resetRun();
    setLive(true);
    setMsg("Live inference running.", "ok");
    while (live) {
      if (!busy) await runOnce();
      if (!live) break;
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  });

  stopBtn.addEventListener("click", () => {
    if (!live) return;
    setLive(false);
    setMsg("Live run stopped.", "ok");
  });

  resetBtn.addEventListener("click", () => {
    if (live) return;
    resetRun();
    setMsg("Charts cleared.", "ok");
  });

  refreshAll().catch((err) => {
    statusEl.textContent = err.message || String(err);
    statusEl.className = "msg err";
  });
})();
