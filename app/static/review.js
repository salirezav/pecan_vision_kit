(() => {
  "use strict";

  const bootEl = document.getElementById("review-boot");
  let boot = {};
  try {
    boot = bootEl ? JSON.parse(bootEl.textContent || "{}") : {};
  } catch (_) {
    boot = {};
  }

  let model = boot.model || {};
  let videos = Array.isArray(boot.videos) ? boot.videos : [];
  let review = { status: "ready", running: false };
  let roi = null;
  let maxFramesPerSec = 60;
  let pollTimer = null;
  let playPoll = null;
  let selecting = false;
  let roiDrawer = null;
  let calibrationUi = null;

  const setupEl = document.getElementById("review-setup");
  const sessionEl = document.getElementById("review-session");
  const reviewStatusEl = document.getElementById("review-status");
  const videoSelect = document.getElementById("video-select");
  const videoFileEl = document.getElementById("video-file");
  const btnUploadVideo = document.getElementById("btn-upload-video");
  const player = document.getElementById("review-player");
  const playerLabel = document.getElementById("player-label");
  const framesPerSecEl = document.getElementById("frames-per-sec");
  const windowSecEl = document.getElementById("window-sec");
  const fpsHintEl = document.getElementById("fps-hint");
  const modelListEl = document.getElementById("model-list");
  const modelListEmptyEl = document.getElementById("model-list-empty");
  const modelStatusEl = document.getElementById("model-status");
  const setupMsgEl = document.getElementById("setup-msg");
  const sessionMsgEl = document.getElementById("session-msg");
  const stillPreview = document.getElementById("still-preview");
  const stillEmpty = document.getElementById("still-empty");
  const stillWrap = document.getElementById("still-wrap");
  const roiStatusEl = document.getElementById("roi-status");
  const roiWarningEl = document.getElementById("roi-warning");
  const resultEl = document.getElementById("result");
  const resultEmpty = document.getElementById("result-empty");
  const progressEl = document.getElementById("progress");
  const positionLabel = document.getElementById("position-label");
  const halfyieldKpis = document.getElementById("halfyield-kpis");
  const crackKpis = document.getElementById("crack-kpis");
  const pecanCards = document.getElementById("pecan-cards");
  const customCards = document.getElementById("custom-cards");
  const finalKpis = document.getElementById("final-kpis");
  const finalBody = document.getElementById("final-body");
  const reportLink = document.getElementById("report-link");
  const runsList = document.getElementById("runs-list");
  const trendEl = document.getElementById("trend");
  const halfShareReading = document.getElementById("half-share-reading");
  const halfShareEl = document.getElementById("half-share");
  const pieceShareEl = document.getElementById("piece-share");
  const massShareEl = document.getElementById("mass-share");
  const btnPause = document.getElementById("btn-pause");
  const btnSlow = document.getElementById("btn-slow");

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatPct(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return (Number(value) * 100).toFixed(1) + "%";
  }

  function formatNum(value, digits) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return Number(value).toFixed(digits == null ? 3 : digits);
  }

  function formatTime(ms) {
    const total = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function setMsg(el, text, isError) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("err", Boolean(isError && text));
  }

  function isHalfyield() {
    return Boolean(model && model.halfyield);
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
    return parts.join(" · ");
  }

  function renderModelList() {
    if (!modelListEl) return;
    const rows = (model && model.models) || [];
    if (modelListEmptyEl) modelListEmptyEl.hidden = rows.length > 0;
    const locked = Boolean(review.running) || selecting;
    modelListEl.innerHTML = rows
      .map((row) => {
        const detail = modelDetail(row);
        const label = row.filename || row.id;
        return (
          '<li class="model-row"><button type="button" class="model-pick' +
          (row.active ? " active" : "") +
          '" data-model-id="' +
          escapeHtml(row.id) +
          '" aria-pressed="' +
          (row.active ? "true" : "false") +
          '"' +
          (locked ? " disabled" : "") +
          '><span class="model-pick-name">' +
          escapeHtml(label) +
          "</span>" +
          (detail
            ? '<span class="model-pick-meta">' + escapeHtml(detail) + "</span>"
            : "") +
          "</button></li>"
        );
      })
      .join("");
  }

  function renderModelStatus() {
    if (!modelStatusEl) return;
    if (!model || !model.installed) {
      modelStatusEl.textContent = "Select a model before starting review.";
      return;
    }
    const name = model.filename || model.active_id || "model";
    modelStatusEl.textContent =
      "Using " +
      name +
      (isHalfyield() ? " · half-yield" : " · Crack Analyst / custom metrics") +
      ".";
  }

  function updateRoiStatus() {
    if (!roiStatusEl) return;
    if (!roi) {
      roiStatusEl.textContent = "Using the full frame.";
      return;
    }
    roiStatusEl.textContent =
      "Region " + roi.w + "×" + roi.h + " at (" + roi.x + ", " + roi.y + ").";
  }

  async function selectModel(id) {
    if (!id || selecting || review.running) return;
    selecting = true;
    renderModelList();
    try {
      const response = await fetch("/api/model/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not select model.");
      model = data;
      if (calibrationUi) calibrationUi.render(model, model.calibration);
      renderModelList();
      renderModelStatus();
      renderKpiPanels();
      setMsg(setupMsgEl, "Selected " + (model.filename || id) + ".");
    } catch (err) {
      setMsg(setupMsgEl, err.message || String(err), true);
    } finally {
      selecting = false;
      renderModelList();
    }
  }

  function stopPlayPoll() {
    if (playPoll) {
      clearTimeout(playPoll);
      playPoll = null;
    }
  }

  function stopPlayback() {
    stopPlayPoll();
    if (!player) return;
    try {
      player.pause();
    } catch (_) {
      /* ignore */
    }
    player.removeAttribute("src");
    player.load();
  }

  async function playVideo(name) {
    if (!player) return;
    if (!name) {
      stopPlayback();
      if (playerLabel) playerLabel.textContent = "Select a video to play it here.";
      return;
    }
    const item = videos.find((row) => row.name === name);
    if (item && item.status === "converting") {
      if (playerLabel) playerLabel.textContent = "Still converting — try again when finished.";
      setMsg(setupMsgEl, "Still converting — it will be watchable when finished.", true);
      return;
    }
    if (playerLabel) playerLabel.textContent = "Loading…";
    stopPlayPoll();
    const url = "/videos/watch/" + encodeURIComponent(name) + "?t=" + Date.now();
    try {
      const probe = await fetch(url, { method: "GET", headers: { Accept: "application/json" } });
      const contentType = (probe.headers.get("content-type") || "").toLowerCase();
      if (contentType.includes("application/json")) {
        const data = await probe.json();
        if (probe.status === 409 || data.converting) {
          if (playerLabel) playerLabel.textContent = "Preparing preview…";
          setMsg(
            setupMsgEl,
            data.error || "Still preparing a playable preview — try again in a moment.",
            true
          );
          playPoll = setTimeout(() => playVideo(name), 2000);
          return;
        }
        throw new Error(data.error || "Could not play this video.");
      }
      if (!probe.ok) throw new Error("Could not play this video.");
      try {
        if (probe.body && probe.body.cancel) await probe.body.cancel();
      } catch (_) {
        /* ignore */
      }
    } catch (err) {
      if (playerLabel) playerLabel.textContent = "Could not play: " + name;
      setMsg(setupMsgEl, err.message || String(err), true);
      return;
    }
    player.removeAttribute("src");
    player.load();
    player.src = url;
    const onReady = () => {
      if (playerLabel) playerLabel.textContent = "Playing: " + name;
      player.play().catch(() => setMsg(setupMsgEl, "Tap the play button on the video.", true));
    };
    const onError = () => {
      if (playerLabel) playerLabel.textContent = "Could not play: " + name;
      setMsg(
        setupMsgEl,
        "Playback failed. If it just finished recording, wait for conversion and choose it again.",
        true
      );
    };
    player.addEventListener("loadeddata", onReady, { once: true });
    player.addEventListener("error", onError, { once: true });
  }

  async function loadStill(name) {
    if (!name) {
      if (stillPreview) {
        stillPreview.hidden = true;
        stillPreview.removeAttribute("src");
      }
      if (stillEmpty) stillEmpty.hidden = false;
      roi = null;
      if (roiDrawer) roiDrawer.setRoi(null);
      updateRoiStatus();
      if (roiWarningEl) {
        roiWarningEl.hidden = true;
        roiWarningEl.textContent = "";
      }
      return;
    }
    setMsg(setupMsgEl, "Loading still…");
    try {
      const response = await fetch("/api/review/still", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video: name }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not load still.");
      maxFramesPerSec = Number(data.max_frames_per_sec) || Number(data.fps) || 60;
      if (framesPerSecEl) {
        framesPerSecEl.max = String(maxFramesPerSec);
        if (Number(framesPerSecEl.value) > maxFramesPerSec) {
          framesPerSecEl.value = String(maxFramesPerSec);
        }
      }
      if (fpsHintEl) {
        fpsHintEl.textContent =
          "Video is " +
          (data.width || "?") +
          "×" +
          (data.height || "?") +
          " at " +
          (data.fps ? Number(data.fps).toFixed(2) : "?") +
          " fps. Max sample rate: " +
          Number(maxFramesPerSec).toFixed(2) +
          " frames/sec.";
      }
      if (stillPreview && data.image) {
        stillPreview.src = data.image;
        stillPreview.hidden = false;
        if (stillEmpty) stillEmpty.hidden = true;
      }
      roi = data.default_roi || null;
      if (roiDrawer) {
        const applyRoi = () => {
          roiDrawer.refresh();
          roiDrawer.setRoi(roi);
          updateRoiStatus();
        };
        if (stillPreview.complete && stillPreview.naturalWidth) applyRoi();
        else stillPreview.addEventListener("load", applyRoi, { once: true });
      } else {
        updateRoiStatus();
      }
      if (roiWarningEl) {
        if (data.roi_warning) {
          roiWarningEl.hidden = false;
          roiWarningEl.textContent = data.roi_warning;
        } else {
          roiWarningEl.hidden = true;
          roiWarningEl.textContent = "";
        }
      }
      setMsg(setupMsgEl, "Video ready. Play it above, then adjust the region if needed.");
    } catch (err) {
      setMsg(setupMsgEl, err.message || String(err), true);
    }
  }

  function reviewReadyVideos(rows) {
    return (Array.isArray(rows) ? rows : []).filter(
      (row) => row && row.status === "ready" && row.kind !== "image"
    );
  }

  function fillVideoSelect(selectedName) {
    if (!videoSelect) return;
    const current = selectedName != null ? selectedName : videoSelect.value;
    const options = ['<option value="">Choose a video…</option>'].concat(
      videos.map((row) => {
        const label =
          escapeHtml(row.name) +
          " (" +
          escapeHtml(String(row.size_mb != null ? row.size_mb : "?")) +
          " MB)";
        const selected = row.name === current ? " selected" : "";
        return (
          '<option value="' +
          escapeHtml(row.name) +
          '"' +
          selected +
          ">" +
          label +
          "</option>"
        );
      })
    );
    videoSelect.innerHTML = options.join("");
    if (current && videos.some((row) => row.name === current)) {
      videoSelect.value = current;
    }
  }

  async function refreshVideos(preferName) {
    const response = await fetch("/api/videos");
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error((data && data.error) || "Could not refresh the video list.");
    }
    videos = reviewReadyVideos(data.videos);
    fillVideoSelect(preferName || "");
  }

  async function uploadVideo() {
    if (!videoFileEl || !videoFileEl.files || !videoFileEl.files.length) {
      setMsg(setupMsgEl, "Choose a video file first.", true);
      return;
    }
    if (review.running) {
      setMsg(setupMsgEl, "Finish the current review before uploading.", true);
      return;
    }
    const file = videoFileEl.files[0];
    const body = new FormData();
    body.append("file", file, file.name);
    if (btnUploadVideo) btnUploadVideo.disabled = true;
    setMsg(setupMsgEl, "Uploading " + file.name + "…");
    try {
      const response = await fetch("/api/videos/upload", { method: "POST", body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error((data && data.error) || "Upload failed.");
      }
      videos = reviewReadyVideos(data.review_videos || data.videos);
      if (!videos.some((row) => row.name === data.name)) {
        await refreshVideos(data.name);
      } else {
        fillVideoSelect(data.name);
      }
      videoFileEl.value = "";
      setMsg(setupMsgEl, "Uploaded " + (data.name || file.name) + ". Ready to review.");
      selectVideo(data.name || "");
    } catch (err) {
      setMsg(setupMsgEl, err.message || String(err), true);
    } finally {
      if (btnUploadVideo) btnUploadVideo.disabled = false;
    }
  }

  function selectVideo(name) {
    if (videoSelect && name && videoSelect.value !== name) {
      videoSelect.value = name;
    }
    playVideo(name);
    loadStill(name);
  }

  function renderKpiPanels() {
    const half = isHalfyield() || Boolean(review.halfyield);
    if (halfyieldKpis) halfyieldKpis.hidden = !half;
    if (crackKpis) crackKpis.hidden = half;
  }

  function renderTrend(points) {
    if (!trendEl) return;
    const rows = Array.isArray(points) ? points : [];
    if (rows.length < 2) {
      trendEl.setAttribute("points", "");
      return;
    }
    const values = rows.map((p) => Number(p.value));
    const times = rows.map((p) => Number(p.t));
    const t0 = times[0];
    const t1 = times[times.length - 1];
    const span = Math.max(1, t1 - t0);
    const coords = rows
      .map((p) => {
        const x = 40 + ((Number(p.t) - t0) / span) * 270;
        const y = 110 - Math.max(0, Math.min(1, Number(p.value))) * 100;
        return x.toFixed(1) + "," + y.toFixed(1);
      })
      .join(" ");
    trendEl.setAttribute("points", coords);
    void values;
  }

  function renderHalfyield(state) {
    if (halfShareReading) halfShareReading.textContent = formatPct(state.half_share);
    if (halfShareEl) halfShareEl.textContent = formatPct(state.half_share);
    if (pieceShareEl) pieceShareEl.textContent = formatPct(state.piece_share);
    if (massShareEl) massShareEl.textContent = formatPct(state.mass_share_frame ?? state.mass_share);
    renderTrend(state.trend);
  }

  function cardHtml(title, value, detail) {
    return (
      '<div class="metric"><span>' +
      escapeHtml(title) +
      "</span><strong>" +
      escapeHtml(value) +
      "</strong></div>" +
      (detail ? '<p class="hint">' + escapeHtml(detail) + "</p>" : "")
    );
  }

  function renderCrack(state) {
    if (pecanCards) {
      const builtins = state.pecan_builtins || {};
      const ids = Object.keys(builtins);
      pecanCards.innerHTML = ids
        .map((id) => {
          const card = builtins[id] || {};
          const value =
            card.value == null
              ? card.message || "—"
              : formatNum(card.value, 4) + (card.ready ? "" : " (preview)");
          return cardHtml(card.name || id, value, card.detail || null);
        })
        .join("");
    }
    if (customCards) {
      const metrics = state.metrics || [];
      const values = state.custom_values || {};
      customCards.innerHTML = metrics
        .map((metric) => {
          const mid = metric.id;
          return cardHtml(metric.name || mid, formatNum(values[mid], 4), metric.formula || "");
        })
        .join("");
    }
  }

  function renderFinal(state) {
    if (!finalKpis || !finalBody) return;
    const final = state.final_kpis;
    if (!final || state.running) {
      finalKpis.hidden = true;
      return;
    }
    finalKpis.hidden = false;
    const lines = [];
    if (state.halfyield || isHalfyield()) {
      lines.push(cardHtml("Half yield", formatPct(final.net_average)));
      lines.push(
        cardHtml("Est. half mass share", formatPct(final.estimated_half_mass_share))
      );
      lines.push(cardHtml("Duration (s)", formatNum(final.duration_sec, 2)));
    } else {
      const pecan = final.pecan_builtins || {};
      Object.keys(pecan).forEach((id) => {
        const card = pecan[id] || {};
        lines.push(
          cardHtml(
            card.name || id,
            card.value == null ? card.message || "—" : formatNum(card.value, 4),
            card.ready ? null : card.message || "Not ready"
          )
        );
      });
      if (final.pecan_n != null) lines.push(cardHtml("Pecan count", String(final.pecan_n)));
      const custom = final.custom_values || {};
      const metrics = state.metrics || [];
      const named = {};
      metrics.forEach((metric) => {
        if (metric && metric.id) named[metric.id] = metric.name || metric.id;
      });
      Object.keys(custom).forEach((id) => {
        lines.push(cardHtml(named[id] || id, formatNum(custom[id], 4)));
      });
    }
    finalBody.innerHTML = lines.join("") || "<p class='hint'>No KPI samples in this segment.</p>";
    if (reportLink) {
      if (state.run_id) {
        reportLink.hidden = false;
        reportLink.innerHTML =
          '<a href="/api/review/runs/' +
          encodeURIComponent(state.run_id) +
          '/csv">Download CSV</a>';
      } else {
        reportLink.hidden = true;
        reportLink.textContent = "";
      }
    }
  }

  function applyReviewState(state) {
    review = state || review;
    const running = Boolean(review.running);
    if (setupEl) setupEl.hidden = running;
    if (sessionEl) sessionEl.hidden = !running && review.status !== "finished" && review.status !== "error";
    if (running && sessionEl) sessionEl.hidden = false;
    if (reviewStatusEl) {
      if (review.error) reviewStatusEl.textContent = "Error";
      else if (running && review.paused) reviewStatusEl.textContent = "Paused";
      else if (running) reviewStatusEl.textContent = "Reviewing";
      else if (review.status === "finished") reviewStatusEl.textContent = "Finished";
      else reviewStatusEl.textContent = "Ready";
    }
    if (btnPause) btnPause.textContent = review.paused ? "Resume" : "Pause";
    if (btnSlow) btnSlow.textContent = review.slow ? "Slow: on" : "Slow: off";

    if (review.image && resultEl) {
      resultEl.src = review.image;
      resultEl.hidden = false;
      if (resultEmpty) resultEmpty.hidden = true;
    }

    const duration = Number(review.duration_ms) || 0;
    const position = Number(review.position_ms) || 0;
    const finished = !running && review.status === "finished";
    if (progressEl) {
      progressEl.max = 100;
      if (finished) {
        progressEl.value = 100;
      } else if (duration > 0) {
        progressEl.value = Math.min(100, (position / duration) * 100);
      } else {
        progressEl.value = 0;
      }
    }
    if (positionLabel) {
      if (finished && duration > 0) {
        positionLabel.textContent = formatTime(duration) + " / " + formatTime(duration);
      } else {
        positionLabel.textContent = formatTime(position) + " / " + formatTime(duration);
      }
    }

    renderKpiPanels();
    if (review.halfyield) renderHalfyield(review);
    else renderCrack(review);
    renderFinal(review);

    if (review.error) setMsg(sessionMsgEl, review.error, true);
    else if (finished) {
      setMsg(sessionMsgEl, "Scoring finished. Review saved — download the CSV below.");
    }

    renderModelList();
  }

  async function poll() {
    try {
      const response = await fetch("/api/review");
      const data = await response.json();
      if (!response.ok || !data.ok) return;
      if (data.model) {
        model = data.model;
        if (calibrationUi && !review.running) {
          calibrationUi.render(model, model.calibration);
        }
        renderModelStatus();
      }
      applyReviewState(data);
      if (data.running) {
        if (!pollTimer) pollTimer = setInterval(poll, 500);
      } else if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (_) {
      /* keep last state */
    }
  }

  async function startReview() {
    const video = videoSelect ? videoSelect.value : "";
    if (!video) {
      setMsg(setupMsgEl, "Choose a video.", true);
      return;
    }
    if (!model || !model.installed) {
      setMsg(setupMsgEl, "Select a model first.", true);
      return;
    }
    let framesPerSec = Number(framesPerSecEl && framesPerSecEl.value);
    if (!Number.isFinite(framesPerSec) || framesPerSec <= 0) framesPerSec = 1;
    if (framesPerSec > maxFramesPerSec) framesPerSec = maxFramesPerSec;
    let windowSec = Number(windowSecEl && windowSecEl.value);
    if (!Number.isFinite(windowSec) || windowSec <= 0) windowSec = 60;
    setMsg(setupMsgEl, "Starting review…");
    stopPlayback();
    if (playerLabel) playerLabel.textContent = "Paused while scoring: " + video;
    try {
      const response = await fetch("/api/review/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video,
          frames_per_sec: framesPerSec,
          window_sec: windowSec,
          roi: roi,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not start review.");
      applyReviewState(data);
      setMsg(setupMsgEl, "");
      setMsg(sessionMsgEl, "Review running.");
      if (!pollTimer) pollTimer = setInterval(poll, 500);
    } catch (err) {
      setMsg(setupMsgEl, err.message || String(err), true);
      playVideo(video);
    }
  }

  async function sendAction(action) {
    try {
      const response = await fetch("/api/review/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Action failed.");
      applyReviewState(data);
      if (action === "finish") {
        loadRuns();
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        // Keep session visible so final KPIs and download stay on screen.
        if (sessionEl) sessionEl.hidden = false;
        if (setupEl) setupEl.hidden = false;
        const selected = videoSelect ? videoSelect.value : "";
        if (selected) playVideo(selected);
      }
    } catch (err) {
      setMsg(sessionMsgEl, err.message || String(err), true);
    }
  }

  async function loadRuns() {
    if (!runsList) return;
    try {
      const response = await fetch("/api/review/runs");
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not list runs.");
      const rows = data.runs || [];
      if (!rows.length) {
        runsList.innerHTML = '<p class="hint">No saved reviews yet.</p>';
        return;
      }
      runsList.innerHTML = rows
        .map((row) => {
          return (
            '<div class="run-row"><div><strong>' +
            escapeHtml(row.video_name || row.id) +
            "</strong><div class=\"hint\">" +
            escapeHtml(row.model_filename || "") +
            " · " +
            escapeHtml(row.started_at || "") +
            (row.halfyield ? " · half-yield" : "") +
            "</div></div>" +
            '<a class="btn" href="/api/review/runs/' +
            encodeURIComponent(row.id) +
            '/csv">CSV</a></div>'
          );
        })
        .join("");
    } catch (err) {
      runsList.innerHTML = '<p class="msg err">' + escapeHtml(err.message || String(err)) + "</p>";
    }
  }

  if (modelListEl) {
    modelListEl.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-model-id]");
      if (!btn) return;
      selectModel(btn.getAttribute("data-model-id"));
    });
  }

  if (videoSelect) {
    videoSelect.addEventListener("change", () => selectVideo(videoSelect.value));
  }
  if (btnUploadVideo) {
    btnUploadVideo.addEventListener("click", uploadVideo);
  }

  window.addEventListener("pagehide", stopPlayback);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") stopPlayPoll();
  });

  const btnStart = document.getElementById("btn-start");
  if (btnStart) btnStart.addEventListener("click", startReview);
  if (btnPause) btnPause.addEventListener("click", () => sendAction("toggle_pause"));
  const btnBack = document.getElementById("btn-back");
  if (btnBack) btnBack.addEventListener("click", () => sendAction("seek_back"));
  const btnAhead = document.getElementById("btn-ahead");
  if (btnAhead) btnAhead.addEventListener("click", () => sendAction("seek_forward"));
  const btnPrev = document.getElementById("btn-prev");
  if (btnPrev) btnPrev.addEventListener("click", () => sendAction("prev_frame"));
  const btnNext = document.getElementById("btn-next");
  if (btnNext) btnNext.addEventListener("click", () => sendAction("next_frame"));
  if (btnSlow) btnSlow.addEventListener("click", () => sendAction("toggle_slow"));
  const btnFinish = document.getElementById("btn-finish");
  if (btnFinish) btnFinish.addEventListener("click", () => sendAction("finish"));
  const btnRefreshRuns = document.getElementById("btn-refresh-runs");
  if (btnRefreshRuns) btnRefreshRuns.addEventListener("click", loadRuns);
  const btnRoiRedraw = document.getElementById("btn-roi-redraw");
  if (btnRoiRedraw) {
    btnRoiRedraw.addEventListener("click", () => {
      if (roiDrawer) roiDrawer.redraw();
      roi = null;
      updateRoiStatus();
      setMsg(setupMsgEl, "Drag on the still to draw a new region.");
    });
  }
  const btnRoiClear = document.getElementById("btn-roi-clear");
  if (btnRoiClear) {
    btnRoiClear.addEventListener("click", () => {
      roi = null;
      if (roiDrawer) roiDrawer.clear();
      else updateRoiStatus();
      setMsg(setupMsgEl, "Region cleared. Scoring will use the full frame.", false);
    });
  }

  if (stillWrap && stillPreview && window.RoiDrawer) {
    roiDrawer = new window.RoiDrawer({
      wrap: stillWrap,
      img: stillPreview,
      onChange: (next) => {
        roi = next;
        updateRoiStatus();
      },
      onCommit: (next) => {
        roi = next;
        updateRoiStatus();
      },
      onClear: () => {
        roi = null;
        updateRoiStatus();
      },
    });
    stillPreview.addEventListener("load", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
    window.addEventListener("resize", () => {
      if (roiDrawer) roiDrawer.refresh();
    });
  }

  if (window.CalibrationUi) {
    calibrationUi = window.CalibrationUi.bind({
      root: document.getElementById("calibration"),
      sliders: document.getElementById("calibration-sliders"),
      hint: document.getElementById("calibration-hint"),
      onChange: () => {
        if (review.running) {
          setMsg(
            sessionMsgEl,
            "Calibration updated — new report segment starts on the next sample."
          );
        }
      },
      onError: (err) => setMsg(setupMsgEl, err.message || String(err), true),
    });
    calibrationUi.render(model, model.calibration);
  }

  renderModelList();
  renderModelStatus();
  renderKpiPanels();
  updateRoiStatus();
  loadRuns();
  poll();
})();
