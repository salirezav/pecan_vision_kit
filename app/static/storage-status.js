(() => {
  function renderAnalysis(target, analysis) {
    if (!target) return;
    if (!analysis) {
      target.hidden = true;
      target.innerHTML = "";
      return;
    }
    target.hidden = false;
    const level = analysis.level || (analysis.ok ? "ok" : "fail");
    target.className = "storage-status " + level;
    const hints = (analysis.hints || [])
      .map((h) => `<li>${escapeHtml(h)}</li>`)
      .join("");
    const reasons = (analysis.reasons || [])
      .map((r) => `<li>${escapeHtml(r)}</li>`)
      .join("");
    const measured =
      analysis.measured_mib_s != null
        ? `${analysis.measured_mib_s} MiB/s measured`
        : "speed not measured";
    target.innerHTML =
      `<div class="storage-status-title">${escapeHtml(analysis.message || "")}</div>` +
      `<div class="storage-status-meta">Need ~${analysis.required_mib_s} MiB/s · ${escapeHtml(measured)} · ` +
      `${analysis.free_gb} GB free · ${escapeHtml(analysis.fstype || "?")} / ${escapeHtml(analysis.transport || "?")}</div>` +
      (reasons ? `<ul class="storage-status-list">${reasons}</ul>` : "") +
      (hints
        ? `<p class="storage-status-hints-label">What to change</p><ul class="storage-status-list">${hints}</ul>`
        : "");
  }

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function analyze(opts) {
    const payload = {
      path: opts.path,
      width: opts.width,
      height: opts.height,
      fps: opts.fps,
      benchmark: opts.benchmark === false ? 0 : 1,
    };
    const res = await fetch("/api/storage/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Analysis failed");
    return data.analysis;
  }

  window.PecanStorage = { renderAnalysis, analyze };
})();
