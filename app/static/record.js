(function () {
  const startBtn = document.getElementById("btn-start");
  const stopBtn = document.getElementById("btn-stop");
  const msg = document.getElementById("msg");
  const badge = document.getElementById("rec-badge");
  const metaFile = document.getElementById("meta-file");
  let convertPoll = null;

  function setRecording(on, file) {
    badge.textContent = on ? "RECORDING" : "READY";
    badge.classList.toggle("on", on);
    startBtn.disabled = on;
    stopBtn.disabled = !on;
    if (file) metaFile.textContent = file;
    if (!on && file === null) metaFile.textContent = "—";
  }

  function stopConvertPoll() {
    if (convertPoll) {
      clearInterval(convertPoll);
      convertPoll = null;
    }
  }

  function watchConversion(destPath) {
    stopConvertPoll();
    const name = destPath.split("/").pop();
    convertPoll = setInterval(async () => {
      try {
        const res = await fetch("/api/videos");
        const data = await res.json();
        const item = (data.videos || []).find((v) => v.name === name);
        if (!item) return;
        if (item.status === "converting") {
          msg.textContent =
            item.message ||
            "Converting to H.264 MP4 — it will be watchable when finished.";
          msg.className = "msg";
          metaFile.textContent = name + " (converting…)";
          return;
        }
        stopConvertPoll();
        metaFile.textContent = name;
        msg.textContent = "Ready: " + name + " — open Videos to watch or download.";
        msg.className = "msg ok";
      } catch (_) {
        /* ignore transient errors while polling */
      }
    }, 1500);
  }

  async function refresh() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      setRecording(!!data.camera.recording, data.camera.current_file);
      if (data.camera.last_error) {
        msg.textContent = data.camera.last_error;
        msg.className = "msg err";
      }
    } catch (e) {
      msg.textContent = "Cannot reach recorder service.";
      msg.className = "msg err";
    }
  }

  startBtn.addEventListener("click", async () => {
    stopConvertPoll();
    msg.textContent = "Starting…";
    msg.className = "msg";
    const res = await fetch("/api/record/start", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      setRecording(true, data.file);
      msg.textContent = "Recording started.";
      msg.className = "msg ok";
    } else {
      msg.textContent = data.error || "Could not start.";
      msg.className = "msg err";
    }
  });

  stopBtn.addEventListener("click", async () => {
    msg.textContent = "Stopping…";
    msg.className = "msg";
    const res = await fetch("/api/record/stop", { method: "POST" });
    const data = await res.json();
    setRecording(false, data.file || null);
    if (data.converting) {
      msg.textContent =
        data.message ||
        "Converting to H.264 MP4 — video will be watchable when finished.";
      msg.className = "msg";
      if (data.file) watchConversion(data.file);
    } else {
      msg.textContent = data.file ? "Saved: " + data.file : "Recording stopped.";
      msg.className = "msg ok";
    }
  });

  refresh();
  setInterval(refresh, 2000);
})();
