(() => {
  const slot = document.getElementById("activity-slot");
  if (!slot) return;

  function chip(href, label, className) {
    const a = document.createElement("a");
    a.href = href;
    a.className = "activity-chip " + className;
    a.setAttribute("aria-live", "polite");
    a.innerHTML = '<span class="activity-dot" aria-hidden="true"></span>' + label;
    return a;
  }

  function render(state) {
    slot.innerHTML = "";
    if (state.recording) {
      slot.appendChild(chip("/", "Recording", "recording"));
    }
    if (state.deploy_live) {
      slot.appendChild(chip("/deploy", "Live inference", "live"));
    }
  }

  async function refresh() {
    try {
      const response = await fetch("/api/activity");
      const data = await response.json();
      if (!data.ok) return;
      render(data);
    } catch (_) {
      /* keep last chips on transient errors */
    }
  }

  refresh();
  setInterval(refresh, 1000);
})();
