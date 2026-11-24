(() => {
  const modal = document.getElementById("browse-modal");
  if (!modal) return;

  const pathEl = document.getElementById("browse-path");
  const volsEl = document.getElementById("browse-volumes");
  const listEl = document.getElementById("browse-list");
  const statusEl = document.getElementById("browse-status");
  const upBtn = document.getElementById("browse-up");
  const selectBtn = document.getElementById("browse-select");
  const closeBtns = modal.querySelectorAll("[data-browse-close]");

  let currentPath = "";
  let onSelected = null;

  function setStatus(text, isError) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("err", Boolean(isError));
    statusEl.classList.toggle("ok", Boolean(text) && !isError);
  }

  async function load(path) {
    setStatus("Loading…");
    const url = "/api/browse?path=" + encodeURIComponent(path || "");
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Could not open folder", true);
      return;
    }
    currentPath = data.path;
    pathEl.textContent = data.path;
    upBtn.disabled = !data.parent;
    upBtn.dataset.parent = data.parent || "";
    selectBtn.disabled = !data.writable;
    selectBtn.textContent = data.writable ? "Use this folder" : "Folder not writable";
    renderVolumes(data.volumes || []);
    renderList(data);
    setStatus(data.writable ? "" : "This folder is read-only.");
  }

  function renderVolumes(volumes) {
    volsEl.innerHTML = "";
    if (!volumes.length) {
      volsEl.innerHTML = '<p class="hint">No storage volumes detected yet. You can still browse kit storage.</p>';
      return;
    }
    for (const vol of volumes) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "vol-chip" + (vol.kind === "usb" ? " usb" : "");
      const free = vol.free_gb != null ? `${vol.free_gb} GB free` : "";
      btn.innerHTML = `<strong>${escapeHtml(vol.label)}</strong><span>${escapeHtml(free)}</span>`;
      btn.addEventListener("click", () => load(vol.path));
      volsEl.appendChild(btn);
    }
  }

  function renderList(data) {
    listEl.innerHTML = "";
    if (!data.folders.length && !data.files.length) {
      listEl.innerHTML = '<p class="hint">This folder is empty.</p>';
      return;
    }
    for (const folder of data.folders) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "browse-item folder";
      row.textContent = "📁 " + folder.name;
      row.addEventListener("click", () => load(folder.path));
      listEl.appendChild(row);
    }
    for (const file of data.files) {
      const row = document.createElement("div");
      row.className = "browse-item file";
      row.textContent = `🎬 ${file.name} (${file.size_mb} MB)`;
      listEl.appendChild(row);
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function openBrowser(startPath, callback) {
    onSelected = callback;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    load(startPath || "/mnt/recordings/videos");
  }

  function closeBrowser() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    onSelected = null;
  }

  upBtn.addEventListener("click", () => {
    if (upBtn.dataset.parent) load(upBtn.dataset.parent);
  });

  selectBtn.addEventListener("click", async () => {
    if (!currentPath || selectBtn.disabled) return;
    setStatus("Saving folder…");
    try {
      if (typeof onSelected === "function") {
        await onSelected(currentPath);
      } else {
        const res = await fetch("/api/save-dir", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: currentPath }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "Failed");
      }
      closeBrowser();
    } catch (err) {
      setStatus(err.message || String(err), true);
    }
  });

  closeBtns.forEach((btn) => btn.addEventListener("click", closeBrowser));
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) closeBrowser();
  });

  window.PecanBrowse = { open: openBrowser, close: closeBrowser, load };
})();
