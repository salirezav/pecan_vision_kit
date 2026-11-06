(() => {
  const bannerHostId = "update-banner-host";

  function ensureHost() {
    let host = document.getElementById(bannerHostId);
    if (host) return host;
    host = document.createElement("div");
    host.id = bannerHostId;
    const main = document.querySelector("main");
    if (main && main.parentNode) {
      main.parentNode.insertBefore(host, main);
    } else {
      document.body.prepend(host);
    }
    return host;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderLastStatus(host, last) {
    if (!last || !last.state) return;
    if (last.state === "ok") {
      host.innerHTML = `<div class="update-banner update-banner-ok" role="status">
        Updated to <strong>${escapeHtml(last.target_version)}</strong>.
        ${escapeHtml(last.message || "")}
        <button type="button" class="btn" data-dismiss-update>Dismiss</button>
      </div>`;
    } else if (last.state === "failed") {
      host.innerHTML = `<div class="update-banner update-banner-err" role="alert">
        Update failed${last.previous_version ? ` (rolled back to ${escapeHtml(last.previous_version)})` : ""}.
        ${escapeHtml(last.message || "")}
        <button type="button" class="btn" data-dismiss-update>Dismiss</button>
      </div>`;
    } else if (["starting", "downloading", "extracting", "backing_up", "installing", "deps", "verifying"].includes(last.state)) {
      host.innerHTML = `<div class="update-banner update-banner-busy" role="status">
        Updating to <strong>${escapeHtml(last.target_version || "…")}</strong>…
        ${escapeHtml(last.message || "The kit will reconnect shortly.")}
      </div>`;
      pollUntilBack(last.target_version);
    }
    host.querySelector("[data-dismiss-update]")?.addEventListener("click", () => {
      host.innerHTML = "";
    });
  }

  function renderAvailable(host, data) {
    const notes = (data.release_notes || "").trim();
    const notesHtml = notes
      ? `<details class="update-notes"><summary>Release notes</summary><pre>${escapeHtml(notes)}</pre></details>`
      : "";
    host.innerHTML = `<div class="update-banner" role="status">
      <div class="update-banner-text">
        <strong>Version ${escapeHtml(data.latest_label || data.latest_version)} is available.</strong>
        This kit is on ${escapeHtml(data.current_label || data.current_version)}.
        ${notesHtml}
      </div>
      <div class="update-banner-actions">
        ${data.release_url ? `<a class="btn" href="${escapeHtml(data.release_url)}" target="_blank" rel="noopener">View notes</a>` : ""}
        <button type="button" class="btn primary" id="btn-apply-update">Update</button>
      </div>
    </div>`;
    document.getElementById("btn-apply-update")?.addEventListener("click", () => applyUpdate(host));
  }

  async function applyUpdate(host) {
    if (!window.confirm("Stop the web app briefly, download the new version, install it, and restart? Recordings and settings stay on this kit.")) {
      return;
    }
    host.innerHTML = `<div class="update-banner update-banner-busy" role="status">
      Updating… the kit will reconnect shortly.
    </div>`;
    try {
      const res = await fetch("/api/update/apply", { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        host.innerHTML = `<div class="update-banner update-banner-err" role="alert">
          ${escapeHtml(data.error || "Could not start the update.")}
        </div>`;
        return;
      }
      pollUntilBack(data.target_version);
    } catch (err) {
      // Service likely stopped; keep polling.
      pollUntilBack(null);
    }
  }

  function pollUntilBack(targetVersion) {
    const started = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - started > 180000) {
        clearInterval(timer);
        const host = ensureHost();
        host.innerHTML = `<div class="update-banner update-banner-err" role="alert">
          Update is taking longer than expected. Check Admin → service status, or
          <code>journalctl -u pecan-vision -n 50</code> and
          <code>/mnt/recordings/update-apply.log</code>.
        </div>`;
        return;
      }
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (!data.version) return;
        if (!targetVersion || String(data.version) === String(targetVersion).replace(/^v/, "")) {
          clearInterval(timer);
          location.reload();
        }
      } catch (_) {
        /* still restarting */
      }
    }, 2000);
  }

  async function refreshAdminPanel(data) {
    const ver = document.getElementById("software-version");
    const state = document.getElementById("software-update-state");
    if (ver) ver.textContent = data.current_label || data.current_version || "—";
    if (!state) return;
    if (data.update_available) {
      state.textContent = `Update available: ${data.latest_label || data.latest_version}`;
    } else if (data.error) {
      state.textContent = `Could not check for updates (${data.error}).`;
    } else {
      state.textContent = "Up to date (or offline — no newer release seen).";
    }
  }

  async function init() {
    const host = ensureHost();
    let data;
    try {
      const res = await fetch("/api/update", { cache: "no-store" });
      data = await res.json();
    } catch (_) {
      return;
    }
    refreshAdminPanel(data);
    if (data.last_status && data.last_status.state && data.last_status.state !== "ok") {
      renderLastStatus(host, data.last_status);
      if (data.update_available && !["starting", "downloading", "extracting", "backing_up", "installing", "deps", "verifying"].includes(data.last_status.state)) {
        // keep failure visible; still allow a new update banner below via re-check later
      }
      return;
    }
    if (data.last_status && data.last_status.state === "ok") {
      const key = `pecan-dismiss-update-${data.last_status.target_version}-${data.last_status.updated_at}`;
      if (!sessionStorage.getItem(key)) {
        renderLastStatus(host, data.last_status);
        host.querySelector("[data-dismiss-update]")?.addEventListener("click", () => {
          sessionStorage.setItem(key, "1");
          host.innerHTML = "";
          if (data.update_available) renderAvailable(host, data);
        });
        return;
      }
    }
    if (data.update_available) {
      renderAvailable(host, data);
    }

    document.getElementById("btn-check-update")?.addEventListener("click", async () => {
      const state = document.getElementById("software-update-state");
      if (state) state.textContent = "Checking…";
      try {
        const res = await fetch("/api/update?force=1", { cache: "no-store" });
        const fresh = await res.json();
        refreshAdminPanel(fresh);
        if (fresh.update_available) renderAvailable(ensureHost(), fresh);
      } catch (err) {
        if (state) state.textContent = "Check failed.";
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
