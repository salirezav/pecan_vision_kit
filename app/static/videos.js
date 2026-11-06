(() => {
  const listEl = document.getElementById("video-list");
  const statusEl = document.getElementById("videos-status");
  const saveLabel = document.getElementById("save-dir-label");
  const player = document.getElementById("player");
  const playerLabel = document.getElementById("player-label");
  const usbBanner = document.getElementById("usb-banner");
  const storageBox = document.getElementById("storage-status");
  const bootEl = document.getElementById("videos-boot");

  let saveDir = "";
  let videos = [];
  let selected = null;
  let pollTimer = null;

  async function refreshAnalysis(path) {
    if (!window.PecanStorage || !storageBox) return;
    storageBox.hidden = false;
    storageBox.className = "storage-status";
    storageBox.textContent = "Testing write speed (a few seconds)…";
    try {
      const analysis = await window.PecanStorage.analyze({ path: path || saveDir });
      window.PecanStorage.renderAnalysis(storageBox, analysis);
    } catch (err) {
      storageBox.className = "storage-status fail";
      storageBox.textContent = err.message || String(err);
    }
  }

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.classList.toggle("err", Boolean(isError));
    statusEl.classList.toggle("ok", Boolean(text) && !isError);
  }

  function renderUsb(volumes) {
    const usb = (volumes || []).filter((v) => v.kind === "usb");
    if (!usb.length) {
      usbBanner.hidden = true;
      usbBanner.innerHTML = "";
      return;
    }
    usbBanner.hidden = false;
    usbBanner.innerHTML =
      "<strong>USB storage detected.</strong> " +
      usb.map((v) => `${v.label}${v.free_gb != null ? ` (${v.free_gb} GB free)` : ""}`).join(" · ") +
      ' <button type="button" class="btn small" id="btn-usb-browse">Browse USB</button>';
    const btn = document.getElementById("btn-usb-browse");
    if (btn) {
      btn.addEventListener("click", () => {
        window.PecanBrowse.open(usb[0].path, applySaveDir);
      });
    }
  }

  function ensurePoll() {
    const busy = videos.some((v) => v.status === "converting");
    if (busy && !pollTimer) {
      pollTimer = setInterval(() => refresh({ quiet: true }), 2000);
    } else if (!busy && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function renderList() {
    listEl.innerHTML = "";
    if (!videos.length) {
      listEl.innerHTML =
        '<p class="hint">No videos in this folder yet. Record one from the Record page.</p>';
      return;
    }
    for (const video of videos) {
      const converting = video.status === "converting";
      const row = document.createElement("article");
      row.className =
        "video-row" +
        (selected === video.name ? " active" : "") +
        (converting ? " converting" : "");

      const main = document.createElement("button");
      main.type = "button";
      main.className = "video-main";
      main.disabled = converting;
      const statusBit = converting
        ? " · Converting to MP4…"
        : ` · ${video.size_mb} MB`;
      main.innerHTML =
        `<strong>${escapeHtml(video.name)}</strong>` +
        `<span>${escapeHtml(video.mtime_text || "")}${statusBit}</span>` +
        (converting
          ? `<span class="converting-note">${escapeHtml(
              video.message || "Converting — watchable when finished."
            )}</span>`
          : "");
      main.addEventListener("click", () => {
        if (!converting) playVideo(video.name);
      });

      const actions = document.createElement("div");
      actions.className = "video-actions";

      const downloadBtn = document.createElement("a");
      downloadBtn.className = "btn small";
      downloadBtn.textContent = "Download";
      if (converting || video.downloadable === false) {
        downloadBtn.classList.add("disabled");
        downloadBtn.href = "#";
        downloadBtn.addEventListener("click", (ev) => {
          ev.preventDefault();
          setStatus("Download available after conversion finishes.", true);
        });
      } else {
        downloadBtn.href = "/videos/download/" + encodeURIComponent(video.name);
        downloadBtn.setAttribute("download", video.name);
      }

      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.className = "btn small";
      renameBtn.textContent = "Rename";
      renameBtn.disabled = converting;
      renameBtn.addEventListener("click", () => renameVideo(video.name));

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn small danger";
      delBtn.textContent = "Delete";
      delBtn.addEventListener("click", () => deleteVideo(video.name));

      actions.appendChild(downloadBtn);
      actions.appendChild(renameBtn);
      actions.appendChild(delBtn);
      row.appendChild(main);
      row.appendChild(actions);
      listEl.appendChild(row);
    }
    ensurePoll();
  }

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function playVideo(name) {
    const item = videos.find((v) => v.name === name);
    if (item && item.status === "converting") {
      setStatus("Still converting — it will be watchable when finished.", true);
      return;
    }
    selected = name;
    renderList();
    playerLabel.textContent = "Loading…";
    setStatus("");
    const url = "/videos/watch/" + encodeURIComponent(name) + "?t=" + Date.now();
    player.removeAttribute("src");
    player.load();
    player.src = url;
    const onReady = () => {
      playerLabel.textContent = "Playing: " + name;
      player.play().catch(() => setStatus("Tap the play button on the video.", true));
    };
    const onError = () => {
      playerLabel.textContent = "Could not play: " + name;
      setStatus(
        "Playback failed. If it just finished recording, wait for conversion and tap again.",
        true
      );
    };
    player.addEventListener("loadeddata", onReady, { once: true });
    player.addEventListener("error", onError, { once: true });
  }

  async function renameVideo(oldName) {
    const suggested = oldName;
    const next = window.prompt("New file name (keep the extension):", suggested);
    if (!next || next === oldName) return;
    setStatus("Renaming…");
    try {
      const res = await fetch("/api/videos/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_name: oldName, new_name: next }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Rename failed");
      videos = data.videos || [];
      if (selected === oldName) {
        selected = next;
        player.src = "/videos/watch/" + encodeURIComponent(next);
        playerLabel.textContent = "Playing: " + next;
      }
      renderList();
      setStatus("Renamed.");
    } catch (err) {
      setStatus(err.message || String(err), true);
    }
  }

  async function deleteVideo(name) {
    if (!window.confirm(`Delete ${name}? This cannot be undone.`)) return;
    setStatus("Deleting…");
    try {
      const res = await fetch("/api/videos/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Delete failed");
      videos = data.videos || [];
      if (selected === name) {
        selected = null;
        player.removeAttribute("src");
        player.load();
        playerLabel.textContent = "Select a video to play";
      }
      renderList();
      setStatus("Deleted.");
    } catch (err) {
      setStatus(err.message || String(err), true);
    }
  }

  async function applySaveDir(path) {
    const res = await fetch("/api/save-dir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not set folder");
    saveDir = data.save_dir;
    videos = data.videos || [];
    saveLabel.textContent = saveDir;
    renderUsb(data.volumes || []);
    renderList();
    if (data.analysis) {
      window.PecanStorage.renderAnalysis(storageBox, data.analysis);
    } else {
      await refreshAnalysis(saveDir);
    }
    setStatus("Save folder updated.");
  }

  async function refresh(opts) {
    const quiet = opts && opts.quiet;
    if (!quiet) setStatus("Refreshing…");
    try {
      const [videosRes, storageRes] = await Promise.all([
        fetch("/api/videos"),
        fetch("/api/storage"),
      ]);
      const videosData = await videosRes.json();
      const storageData = await storageRes.json();
      if (!videosData.ok) throw new Error(videosData.error || "Could not load videos");
      videos = videosData.videos || [];
      saveDir = videosData.save_dir || saveDir;
      saveLabel.textContent = saveDir;
      renderUsb(storageData.volumes || []);
      renderList();
      if (!quiet) setStatus("");
    } catch (err) {
      if (!quiet) setStatus(err.message || String(err), true);
    }
  }

  let boot = { videos: [], save_dir: "", volumes: [] };
  try {
    boot = JSON.parse(bootEl.textContent || "{}");
  } catch (_) {
    /* ignore */
  }
  videos = boot.videos || [];
  saveDir = boot.save_dir || "";
  saveLabel.textContent = saveDir;
  renderUsb(boot.volumes || []);
  renderList();
  refreshAnalysis(saveDir);

  document.getElementById("btn-refresh").addEventListener("click", () => refresh());
  document.getElementById("btn-browse-save").addEventListener("click", () => {
    window.PecanBrowse.open(saveDir || "/mnt/recordings/videos", applySaveDir);
  });
})();
