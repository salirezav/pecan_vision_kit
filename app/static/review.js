(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let ending = false;
  let calibrationLoaded = false;
  let lastImage = "";
  let videoRoi = null;
  let roiDrawer = null;

  const fields = [
    ["half_confidence", "Half confidence", false],
    ["piece_confidence", "Piece confidence", false],
    ["obscured_confidence", "Obscured confidence", false],
    ["shell_confidence", "Shell confidence", false],
    ["half_mass_per_area_g_per_model_pixel2", "Half mass / area", true],
    ["piece_mass_per_area_g_per_model_pixel2", "Piece mass / area", true],
  ];

  for (const [name, title, mass] of fields) {
    const label = document.createElement("label");
    label.textContent = title;
    const output = document.createElement("output");
    const input = document.createElement("input");
    input.type = "range";
    input.id = name;
    input.min = mass ? -6 : 0;
    input.max = mass ? 0 : 1;
    input.step = 0.01;
    input.value = mass ? -3 : 0.3;
    input.addEventListener("input", () => {
      output.textContent = mass
        ? `${(10 ** Number(input.value)).toPrecision(4)} g/model px²`
        : `${Math.round(Number(input.value) * 100)}%`;
    });
    label.append(output, input);
    $("sliders").append(label);
    input.dispatchEvent(new Event("input"));
  }

  async function post(route, data) {
    const response = await fetch(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The request failed.");
    return result;
  }

  function errorMessage(error) {
    $("message").textContent = error.message || String(error);
  }

  function fillVideos(videos) {
    const select = $("video-select");
    const current = select.value;
    select.innerHTML = "";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Select a video from kit storage…";
    select.append(blank);
    for (const row of videos || []) {
      if (!row || !row.path) continue;
      const option = document.createElement("option");
      option.value = row.path;
      option.textContent = row.name || row.path;
      select.append(option);
    }
    if (current) select.value = current;
  }

  function showModel(halfyield) {
    const line = $("model-line");
    if (!halfyield || !halfyield.installed) {
      line.textContent =
        halfyield && halfyield.error
          ? halfyield.error
          : "Place halfyield-model.pt under weights/ on this kit.";
      line.className = "msg err";
      return;
    }
    const names = (halfyield.class_names || []).join(", ") || "classes from file";
    line.textContent = `${halfyield.filename} · ${halfyield.architecture || halfyield.family || "YOLO"} · ${names}`;
    line.className = "msg ok";
  }

  function syncStartEnabled() {
    $("start").disabled = !videoRoi;
  }

  function setStillStatus(text, kind) {
    const el = $("roi-still-status");
    if (!el) return;
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }

  function clearVideoRoi() {
    videoRoi = null;
    if (roiDrawer) roiDrawer.setRoi(null);
    syncStartEnabled();
    setStillStatus("Draw a rectangle on this video’s still frame.", "");
  }

  async function loadStillFrame() {
    let source = $("source").value.trim();
    if (!source) source = $("video-select").value.trim();
    if (!source) throw new Error("Choose a video file or enter its local path.");
    setStillStatus("Loading still frame…", "");
    const data = await post("/api/review/preview-frame", { source });
    const wrap = $("roi-still-wrap");
    const img = $("roi-still");
    wrap.hidden = false;
    img.src = data.image;
    videoRoi = null;
    syncStartEnabled();
    if (!roiDrawer && window.RoiDrawer) {
      roiDrawer = new window.RoiDrawer({
        wrap: wrap,
        img: img,
        onCommit: (roi) => {
          videoRoi = roi;
          syncStartEnabled();
          setStillStatus(
            `This video’s region ${roi.w}×${roi.h} at (${roi.x}, ${roi.y}).`,
            "ok"
          );
        },
        onClear: () => {
          videoRoi = null;
          syncStartEnabled();
        },
      });
      img.addEventListener("load", () => {
        if (roiDrawer) roiDrawer.refresh();
      });
      window.addEventListener("resize", () => {
        if (roiDrawer) roiDrawer.refresh();
      });
    } else if (roiDrawer) {
      roiDrawer.setRoi(null);
      roiDrawer.refresh();
    }
    setStillStatus(
      `Still ${data.width}×${data.height}. Drag to draw this video’s measurement region.`,
      "ok"
    );
  }

  $("video-select").addEventListener("change", () => {
    if ($("video-select").value) $("source").value = $("video-select").value;
    clearVideoRoi();
    loadStillFrame().catch((error) => {
      setStillStatus(error.message || String(error), "err");
      errorMessage(error);
    });
  });

  $("btn-load-still").addEventListener("click", () => {
    loadStillFrame().catch((error) => {
      setStillStatus(error.message || String(error), "err");
      errorMessage(error);
    });
  });

  $("btn-roi-redraw").addEventListener("click", () => {
    if (roiDrawer) roiDrawer.redraw();
    videoRoi = null;
    syncStartEnabled();
    setStillStatus("Drag on the still to draw a new region.", "");
  });

  $("btn-roi-clear").addEventListener("click", () => {
    clearVideoRoi();
  });

  $("start-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("start").disabled = true;
    $("message").textContent = "";
    try {
      let source = $("source").value.trim();
      if (!source) source = $("video-select").value.trim();
      if (!source) throw new Error("Choose a video file or enter its local path.");
      if (!videoRoi) throw new Error("Draw a measurement region on the still frame first.");
      const analyzeEl = $("analyze-interval");
      await post("/api/review/start", {
        source,
        report: $("report").value,
        window: Number($("window").value),
        analyze_interval_sec: analyzeEl ? Number(analyzeEl.value) : 0,
        roi: videoRoi,
      });
      $("status").textContent = "Loading model and video…";
      $("setup").hidden = true;
      calibrationLoaded = false;
      ending = false;
    } catch (error) {
      errorMessage(error);
      syncStartEnabled();
      $("status").textContent = "Choose a video";
    }
  });

  async function action(value) {
    if (ending) return;
    try {
      await post("/api/review/action", { action: value });
      if (value === "quit") {
        ending = true;
        $("status").textContent = "Saving report…";
      }
    } catch (error) {
      errorMessage(error);
    }
  }

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => action(button.dataset.action));
  });

  $("calibration-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = {};
    fields.forEach(([name, , mass]) => {
      values[name] = mass ? 10 ** Number($(name).value) : Number($(name).value);
    });
    try {
      await post("/api/review/calibration", values);
    } catch (error) {
      errorMessage(error);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (
      ["INPUT", "SELECT", "TEXTAREA", "BUTTON", "SUMMARY"].includes(event.target.tagName) ||
      $("review").hidden
    ) {
      return;
    }
    const map = {
      " ": "toggle_pause",
      ArrowLeft: "left",
      ArrowRight: "right",
      ",": "prev_frame",
      ".": "next_frame",
      s: "toggle_slow",
      q: "quit",
      Escape: "quit",
    };
    const value = map[event.key] || map[event.key.toLowerCase()];
    if (value) {
      event.preventDefault();
      action(value);
    }
  });

  function percent(value) {
    return value == null || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(1)}%`;
  }

  function clock(value) {
    const seconds = Math.floor(value || 0);
    const minutes = Math.floor(seconds / 60);
    const rem = seconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
  }

  function trend(series) {
    const valid = (series || []).filter(
      ([, ratio]) => ratio != null && Number.isFinite(ratio)
    );
    if (valid.length < 2) {
      $("trend").setAttribute("d", "");
      return;
    }
    const start = valid[0][0];
    const span = valid[valid.length - 1][0] - start;
    if (span <= 0) return;
    let path = "";
    let gap = true;
    for (const [time, ratio] of series) {
      if (ratio == null || !Number.isFinite(ratio)) {
        gap = true;
        continue;
      }
      path += `${gap ? "M" : "L"}${(((time - start) / span) * 350).toFixed(2)} ${(
        140 - Math.max(0, Math.min(1, ratio)) * 130
      ).toFixed(2)} `;
      gap = false;
    }
    $("trend").setAttribute("d", path);
  }

  async function poll() {
    try {
      const response = await fetch("/api/review");
      if (!response.ok) throw new Error("The review service is unavailable.");
      const state = await response.json();
      showModel(state.halfyield);
      fillVideos(state.videos);

      if (state.status === "error") {
        $("status").textContent = "Review failed";
        $("message").textContent = state.error || "Review failed.";
        ending = true;
        syncStartEnabled();
        setTimeout(poll, 1000);
        return;
      }
      if (state.status === "finished") {
        $("status").textContent = "Review finished";
        $("notice").textContent =
          state.notice ||
          (state.report
            ? `Report saved: ${state.report}`
            : "Review finished.");
        ending = true;
        document.querySelectorAll("#review button").forEach((button) => {
          button.disabled = true;
        });
        syncStartEnabled();
        setTimeout(poll, 1500);
        return;
      }
      if (state.status === "loading" || state.status === "saving") {
        $("setup").hidden = true;
        $("status").textContent =
          state.status === "saving" ? "Saving report…" : "Loading model and video…";
      }
      if (state.status === "reviewing" || state.running) {
        $("setup").hidden = true;
        $("review").hidden = false;
        if (!ending) {
          $("status").textContent = state.complete
            ? "Review complete"
            : state.paused
              ? "Paused"
              : "Analyzing";
        }
        $("filename").textContent = state.source || "";
        $("half").textContent = percent(state.ratio);
        $("piece").textContent = percent(
          state.ratio == null ? null : 1 - state.ratio
        );
        $("mass").textContent = percent(state.mass_share);
        $("window-label").textContent = `Rolling ${state.window_seconds}-second window`;
        $("time").textContent = `${clock(state.position)} / ${clock(state.duration)}`;
        $("progress").value =
          state.duration > 0 ? state.position / state.duration : 0;
        $("pause").textContent = state.paused ? "Resume" : "Pause";
        $("pause").disabled = !!state.complete;
        $("slow").textContent = `Slow: ${state.slow ? "on" : "off"}`;
        const back = document.querySelector('[data-action="seek_back"]');
        const ahead = document.querySelector('[data-action="seek_forward"]');
        if (back) back.textContent = `Back ${state.skip_seconds}s`;
        if (ahead) ahead.textContent = `Ahead ${state.skip_seconds}s`;
        $("notice").textContent = state.notice || state.fit_warning || "";
        trend(state.series || []);
        if (!calibrationLoaded && state.calibration) {
          fields.forEach(([name, , mass]) => {
            const value = state.calibration[name];
            if (value == null) return;
            $(name).value = mass ? Math.log10(value) : value;
            $(name).dispatchEvent(new Event("input"));
          });
          calibrationLoaded = true;
        }
        if (state.image && state.image !== lastImage) {
          lastImage = state.image;
          $("frame").src = state.image;
        }
      }
    } catch (error) {
      if (!ending) {
        $("message").textContent =
          "Connection to the review service was lost. Check Admin / journal.";
      }
    }
    setTimeout(poll, 200);
  }

  window.addEventListener("pagehide", () => {
    if (!ending) {
      navigator.sendBeacon("/api/review/stop", "{}");
    }
  });

  poll();
})();
