<<<<<<< HEAD
(() => {
  const hostId = "bundled-models-host";

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function joinNames(names) {
    if (names.length <= 1) return names[0] || "";
    if (names.length === 2) return names[0] + " and " + names[1];
    return names.slice(0, -1).join(", ") + ", and " + names[names.length - 1];
  }

  function host() {
    let node = document.getElementById(hostId);
    if (node) return node;
    node = document.createElement("div");
    node.id = hostId;
    const updateHost = document.getElementById("update-banner-host");
    const main = document.querySelector("main");
    if (updateHost && updateHost.parentNode) {
      updateHost.parentNode.insertBefore(node, updateHost.nextSibling);
    } else if (main && main.parentNode) {
      main.parentNode.insertBefore(node, main);
    } else {
      document.body.prepend(node);
    }
    return node;
  }

  function clearBanner() {
    document.getElementById(hostId)?.remove();
  }

  function show(html) {
    host().innerHTML = html;
  }

  async function load() {
    let data;
    try {
      const response = await fetch("/api/model/bundled");
      data = await response.json();
      if (!response.ok || !data.ok) return;
    } catch (_err) {
      return;
    }
    const models = Array.isArray(data.models) ? data.models : [];
    if (!models.length) {
      clearBanner();
      return;
    }
    const names = joinNames(models.map((row) => row.filename || row.id));
    show(`<div class="update-banner" role="status">
      <div class="update-banner-text">
        New models are available to use: <strong>${escapeHtml(names)}</strong>.
        Add them to the model library? Models already saved on this kit are left as they are.
      </div>
      <div class="update-banner-actions">
        <button type="button" class="btn primary" id="btn-install-bundled">Add models</button>
        <button type="button" class="btn" id="btn-dismiss-bundled">Not now</button>
      </div>
    </div>`);
    document.getElementById("btn-install-bundled")?.addEventListener("click", () => install(models));
    document.getElementById("btn-dismiss-bundled")?.addEventListener("click", () => dismiss(models));
  }

  async function install(models) {
    const button = document.getElementById("btn-install-bundled");
    if (button) button.disabled = true;
    show(`<div class="update-banner update-banner-busy" role="status">
      <div class="update-banner-text">Copying models into the library…</div>
    </div>`);
    try {
      const response = await fetch("/api/model/install-bundled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: models.map((row) => row.filename) }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not copy the models.");
      }
      const added = (data.bundled_installed || []).map((row) => row.filename);
      const failed = data.bundled_errors || [];
      const addedText = added.length
        ? "Added " + joinNames(added) + " to the model library."
        : "Those models are already in the library.";
      const failText = failed.length
        ? " " + failed.map((row) => row.filename + ": " + row.error).join(" ")
        : "";
      show(`<div class="update-banner ${failed.length ? "update-banner-err" : "update-banner-ok"}" role="status">
        <div class="update-banner-text">${escapeHtml(addedText + failText)}</div>
        <div class="update-banner-actions">
          <button type="button" class="btn" id="btn-bundled-done">Dismiss</button>
        </div>
      </div>`);
      document.getElementById("btn-bundled-done")?.addEventListener("click", clearBanner);
      window.dispatchEvent(new Event("pecan-models-changed"));
    } catch (err) {
      show(`<div class="update-banner update-banner-err" role="alert">
        <div class="update-banner-text">${escapeHtml(err.message || String(err))}</div>
        <div class="update-banner-actions">
          <button type="button" class="btn" id="btn-bundled-retry">Try again</button>
        </div>
      </div>`);
      document.getElementById("btn-bundled-retry")?.addEventListener("click", () => load());
    }
  }

  async function dismiss(models) {
    try {
      await fetch("/api/model/dismiss-bundled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: models.map((row) => row.filename) }),
      });
    } catch (_err) {
      // Hiding the banner still matches the choice. The next page load can ask again.
    }
    clearBanner();
  }

  load();
})();
=======
(() => {
  const hostId = "bundled-models-host";

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function joinNames(names) {
    if (names.length <= 1) return names[0] || "";
    if (names.length === 2) return names[0] + " and " + names[1];
    return names.slice(0, -1).join(", ") + ", and " + names[names.length - 1];
  }

  function host() {
    let node = document.getElementById(hostId);
    if (node) return node;
    node = document.createElement("div");
    node.id = hostId;
    const updateHost = document.getElementById("update-banner-host");
    const main = document.querySelector("main");
    if (updateHost && updateHost.parentNode) {
      updateHost.parentNode.insertBefore(node, updateHost.nextSibling);
    } else if (main && main.parentNode) {
      main.parentNode.insertBefore(node, main);
    } else {
      document.body.prepend(node);
    }
    return node;
  }

  function clearBanner() {
    document.getElementById(hostId)?.remove();
  }

  function show(html) {
    host().innerHTML = html;
  }

  async function load() {
    let data;
    try {
      const response = await fetch("/api/model/bundled");
      data = await response.json();
      if (!response.ok || !data.ok) return;
    } catch (_err) {
      return;
    }
    const models = Array.isArray(data.models) ? data.models : [];
    if (!models.length) {
      clearBanner();
      return;
    }
    const names = joinNames(models.map((row) => row.filename || row.id));
    show(`<div class="update-banner" role="status">
      <div class="update-banner-text">
        New models are available to use: <strong>${escapeHtml(names)}</strong>.
        Add them to the model library? Models already saved on this kit are left as they are.
      </div>
      <div class="update-banner-actions">
        <button type="button" class="btn primary" id="btn-install-bundled">Add models</button>
        <button type="button" class="btn" id="btn-dismiss-bundled">Not now</button>
      </div>
    </div>`);
    document.getElementById("btn-install-bundled")?.addEventListener("click", () => install(models));
    document.getElementById("btn-dismiss-bundled")?.addEventListener("click", () => dismiss(models));
  }

  async function install(models) {
    const button = document.getElementById("btn-install-bundled");
    if (button) button.disabled = true;
    show(`<div class="update-banner update-banner-busy" role="status">
      <div class="update-banner-text">Copying models into the library…</div>
    </div>`);
    try {
      const response = await fetch("/api/model/install-bundled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: models.map((row) => row.filename) }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not copy the models.");
      }
      const added = (data.bundled_installed || []).map((row) => row.filename);
      const failed = data.bundled_errors || [];
      const addedText = added.length
        ? "Added " + joinNames(added) + " to the model library."
        : "Those models are already in the library.";
      const failText = failed.length
        ? " " + failed.map((row) => row.filename + ": " + row.error).join(" ")
        : "";
      show(`<div class="update-banner ${failed.length ? "update-banner-err" : "update-banner-ok"}" role="status">
        <div class="update-banner-text">${escapeHtml(addedText + failText)}</div>
        <div class="update-banner-actions">
          <button type="button" class="btn" id="btn-bundled-done">Dismiss</button>
        </div>
      </div>`);
      document.getElementById("btn-bundled-done")?.addEventListener("click", clearBanner);
      window.dispatchEvent(new Event("pecan-models-changed"));
    } catch (err) {
      show(`<div class="update-banner update-banner-err" role="alert">
        <div class="update-banner-text">${escapeHtml(err.message || String(err))}</div>
        <div class="update-banner-actions">
          <button type="button" class="btn" id="btn-bundled-retry">Try again</button>
        </div>
      </div>`);
      document.getElementById("btn-bundled-retry")?.addEventListener("click", () => load());
    }
  }

  async function dismiss(models) {
    try {
      await fetch("/api/model/dismiss-bundled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: models.map((row) => row.filename) }),
      });
    } catch (_err) {
      // Hiding the banner still matches the choice. The next page load can ask again.
    }
    clearBanner();
  }

  load();
})();
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7
