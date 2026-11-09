(() => {
  const root = document.getElementById("v4l-controls");
  const statusEl = document.getElementById("v4l-status");
  if (!root) return;

  let debounceTimers = {};

  function setStatus(text, isError) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("err", Boolean(isError));
    statusEl.classList.toggle("ok", Boolean(text) && !isError);
  }

  async function applyControl(name, value) {
    const sent = Number(value);
    setStatus(`Setting ${name}…`);
    try {
      const res = await fetch("/api/v4l/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, value: sent }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Failed");
      // A newer keypress is waiting. Leave the field alone until that one finishes.
      if (debounceTimers[name]) return;
      syncControls(data.controls || [], name, sent);
      setStatus(`Updated ${name}`);
    } catch (err) {
      setStatus(err.message || String(err), true);
    }
  }

  function findRow(name) {
    return [...root.querySelectorAll("[data-name]")].find((el) => el.dataset.name === name) || null;
  }

  function captureFocus() {
    const el = document.activeElement;
    if (!el || !root.contains(el)) return null;
    const row = el.closest("[data-name]");
    if (!row) return null;
    return { name: row.dataset.name, type: el.type || el.tagName };
  }

  function restoreFocus(saved) {
    if (!saved) return;
    const row = findRow(saved.name);
    if (!row) return;
    const el = [...row.querySelectorAll("input, select")].find((node) => (node.type || node.tagName) === saved.type);
    if (el && document.activeElement !== el) el.focus();
  }

  function setLock(row, inactive) {
    row.classList.toggle("inactive", Boolean(inactive));
    const title = row.querySelector(".ctrl-title");
    const lock = row.querySelector(".ctrl-lock");
    if (inactive && !lock && title) {
      const mark = document.createElement("span");
      mark.className = "ctrl-lock";
      mark.textContent = "locked by auto";
      title.appendChild(mark);
    } else if (!inactive && lock) {
      lock.remove();
    }
  }

  function syncControls(controls, touchedName, sentValue) {
    const usable = (controls || []).filter((c) => c && c.name && !c.error);
    const names = usable.map((c) => c.name);
    const existing = [...root.querySelectorAll("[data-name]")].map((el) => el.dataset.name);
    if (!usable.length || names.join("\0") !== existing.join("\0")) {
      const saved = captureFocus();
      renderControls(usable);
      restoreFocus(saved);
      return;
    }
    for (const ctrl of usable) {
      const row = findRow(ctrl.name);
      if (!row) continue;
      setLock(row, ctrl.inactive);
      const slider = row.querySelector('input[type="range"]');
      const number = row.querySelector('input[type="number"]');
      if (slider && number) {
        const focused = document.activeElement === slider || document.activeElement === number;
        const pending = Boolean(debounceTimers[ctrl.name]);
        const keepValue = pending || (focused && ctrl.name === touchedName && Number(slider.value) !== Number(sentValue));
        if (!keepValue) {
          slider.value = String(ctrl.value);
          number.value = String(ctrl.value);
        }
        if (document.activeElement !== slider) slider.disabled = Boolean(ctrl.inactive);
        if (document.activeElement !== number) number.disabled = Boolean(ctrl.inactive);
        continue;
      }
      const box = row.querySelector('input[type="checkbox"]');
      if (box) {
        if (document.activeElement !== box) box.checked = Number(ctrl.value) === 1;
        const text = row.querySelector(".ctrl-bool span");
        if (text) text.textContent = box.checked ? "On" : "Off";
        if (document.activeElement !== box) box.disabled = Boolean(ctrl.inactive);
        continue;
      }
      const select = row.querySelector("select");
      if (select && document.activeElement !== select) {
        select.value = String(ctrl.value);
        select.disabled = Boolean(ctrl.inactive);
      }
    }
  }

  function scheduleApply(name, value) {
    clearTimeout(debounceTimers[name]);
    debounceTimers[name] = setTimeout(() => {
      delete debounceTimers[name];
      applyControl(name, value);
    }, 120);
  }

  function renderControls(controls) {
    const usable = (controls || []).filter((c) => c && c.name && !c.error);
    if (!usable.length) {
      root.innerHTML = "<p>No camera controls available.</p>";
      return;
    }

    root.innerHTML = "";
    for (const ctrl of usable) {
      const row = document.createElement("div");
      row.className = "ctrl-widget" + (ctrl.inactive ? " inactive" : "");
      row.dataset.name = ctrl.name;

      const title = document.createElement("div");
      title.className = "ctrl-title";
      title.textContent = ctrl.label || ctrl.name;
      if (ctrl.inactive) {
        const lock = document.createElement("span");
        lock.className = "ctrl-lock";
        lock.textContent = "locked by auto";
        title.appendChild(lock);
      }
      row.appendChild(title);

      if (ctrl.type === "bool") {
        row.appendChild(buildBool(ctrl));
      } else if (ctrl.type === "menu") {
        row.appendChild(buildMenu(ctrl));
      } else {
        row.appendChild(buildRange(ctrl));
      }
      root.appendChild(row);
    }
  }

  function buildBool(ctrl) {
    const wrap = document.createElement("label");
    wrap.className = "ctrl-bool";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = Number(ctrl.value) === 1;
    box.disabled = Boolean(ctrl.inactive);
    const text = document.createElement("span");
    text.textContent = box.checked ? "On" : "Off";
    box.addEventListener("change", () => {
      text.textContent = box.checked ? "On" : "Off";
      applyControl(ctrl.name, box.checked ? 1 : 0);
    });
    wrap.appendChild(box);
    wrap.appendChild(text);
    return wrap;
  }

  function buildMenu(ctrl) {
    const select = document.createElement("select");
    select.className = "ctrl-menu";
    select.disabled = Boolean(ctrl.inactive);
    const options = ctrl.options && ctrl.options.length
      ? ctrl.options
      : [{ value: ctrl.value, label: String(ctrl.value) }];
    for (const opt of options) {
      const el = document.createElement("option");
      el.value = String(opt.value);
      el.textContent = opt.label;
      if (Number(opt.value) === Number(ctrl.value)) el.selected = true;
      select.appendChild(el);
    }
    select.addEventListener("change", () => applyControl(ctrl.name, select.value));
    return select;
  }

  function buildRange(ctrl) {
    const min = Number(ctrl.min ?? 0);
    const max = Number(ctrl.max ?? 100);
    const step = Number(ctrl.step ?? 1) || 1;
    const value = Number(ctrl.value ?? min);

    const wrap = document.createElement("div");
    wrap.className = "ctrl-range";

    const meta = document.createElement("div");
    meta.className = "ctrl-range-meta";
    meta.textContent = `${min} – ${max}` + (step !== 1 ? ` (step ${step})` : "");

    const row = document.createElement("div");
    row.className = "ctrl-range-row";

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = String(min);
    slider.max = String(max);
    slider.step = String(step);
    slider.value = String(value);
    slider.disabled = Boolean(ctrl.inactive);

    const number = document.createElement("input");
    number.type = "number";
    number.min = String(min);
    number.max = String(max);
    number.step = String(step);
    number.value = String(value);
    number.disabled = Boolean(ctrl.inactive);
    number.className = "ctrl-number";

    const sync = (next, source) => {
      let n = Number(next);
      if (Number.isNaN(n)) n = value;
      // Snap to step relative to min
      n = min + Math.round((n - min) / step) * step;
      n = Math.min(max, Math.max(min, n));
      slider.value = String(n);
      number.value = String(n);
      if (source === "live") scheduleApply(ctrl.name, n);
      else applyControl(ctrl.name, n);
    };

    function snapToStep(n) {
      const snapped = min + Math.round((n - min) / step) * step;
      return Math.min(max, Math.max(min, snapped));
    }

    function nudge(delta) {
      const typed = Number(number.value);
      const current = Number.isFinite(typed) ? typed : Number(slider.value);
      let next = snapToStep(current + delta);
      // A step larger than the keypress (pan/tilt) cannot move by 1. Advance one step.
      if (next === current) next = snapToStep(current + Math.sign(delta) * step);
      if (next === current) return;
      slider.value = String(next);
      number.value = String(next);
      scheduleApply(ctrl.name, next);
    }

    function onStepKey(ev) {
      const direction = ev.key === "ArrowUp" || ev.key === "ArrowRight" ? 1
        : ev.key === "ArrowDown" || ev.key === "ArrowLeft" ? -1
        : 0;
      if (!direction) return;
      ev.preventDefault();
      nudge((ev.shiftKey ? 10 : 1) * direction);
    }

    slider.addEventListener("input", () => {
      number.value = slider.value;
      scheduleApply(ctrl.name, slider.value);
    });
    slider.addEventListener("change", () => applyControl(ctrl.name, slider.value));
    slider.addEventListener("keydown", onStepKey);
    number.addEventListener("change", () => sync(number.value, "commit"));
    number.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        sync(number.value, "commit");
        return;
      }
      onStepKey(ev);
    });

    row.appendChild(slider);
    row.appendChild(number);
    wrap.appendChild(meta);
    wrap.appendChild(row);
    return wrap;
  }

  // Initial controls from server-rendered JSON
  const boot = document.getElementById("v4l-controls-data");
  let initial = [];
  if (boot) {
    try {
      initial = JSON.parse(boot.textContent || "[]");
    } catch (_) {
      initial = [];
    }
  }
  renderControls(initial);
  window.PecanControls = { render: renderControls };
})();
