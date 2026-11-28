(() => {
  "use strict";

  const MASS_HALF = "half_mass_per_area_g_per_model_pixel2";
  const MASS_PIECE = "piece_mass_per_area_g_per_model_pixel2";

  function titleCase(name) {
    return String(name || "")
      .split(/[_\s]+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function emptyRecord(classNames, halfyield) {
    const defaultConf = halfyield ? 0.3 : 0.5;
    const class_confidence = {};
    (classNames || []).forEach((name) => {
      class_confidence[name] = defaultConf;
    });
    const record = { class_confidence, halfyield: Boolean(halfyield) };
    if (halfyield) {
      record[MASS_HALF] = 0.001;
      record[MASS_PIECE] = 0.001;
    }
    return record;
  }

  function bind(options) {
    const root = options.root;
    const sliders = options.sliders;
    const hint = options.hint;
    if (!root || !sliders) {
      return {
        getRecord() {
          return emptyRecord([], false);
        },
        render() {},
        current() {
          return emptyRecord([], false);
        },
        setDisabled() {},
      };
    }

    let modelId = "";
    let classNames = [];
    let halfyield = false;
    let usesConfidence = true;
    let record = emptyRecord([], false);
    let saveTimer = null;
    let saving = false;
    let inputsDisabled = false;
    let onChange = typeof options.onChange === "function" ? options.onChange : null;
    let onMassInput = typeof options.onMassInput === "function" ? options.onMassInput : null;
    let suppressSave = false;

    function current() {
      const class_confidence = {};
      classNames.forEach((name) => {
        const input = document.getElementById("cal-conf-" + name);
        if (input) class_confidence[name] = Number(input.value);
        else class_confidence[name] = (record.class_confidence || {})[name] ?? (halfyield ? 0.3 : 0.5);
      });
      const next = { class_confidence, halfyield };
      if (halfyield) {
        const halfInput = document.getElementById("cal-mass-half");
        const pieceInput = document.getElementById("cal-mass-piece");
        next[MASS_HALF] = halfInput ? 10 ** Number(halfInput.value) : Number(record[MASS_HALF] || 0.001);
        next[MASS_PIECE] = pieceInput
          ? 10 ** Number(pieceInput.value)
          : Number(record[MASS_PIECE] || 0.001);
      }
      return next;
    }

    async function persist() {
      if (!modelId || !usesConfidence || suppressSave) return;
      const payload = current();
      record = payload;
      saving = true;
      try {
        const response = await fetch("/api/model/calibration", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ calibration: payload }),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "Could not save calibration.");
        if (data.calibration) record = data.calibration;
        if (onChange) onChange(record);
      } catch (err) {
        if (typeof options.onError === "function") options.onError(err);
      } finally {
        saving = false;
      }
    }

    function scheduleSave() {
      if (suppressSave || inputsDisabled) return;
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        saveTimer = null;
        persist();
      }, 250);
    }

    function applyDisabledState() {
      sliders.querySelectorAll("input, select, textarea, button").forEach((el) => {
        el.disabled = inputsDisabled;
      });
    }

    function updateHint() {
      if (!hint) return;
      if (inputsDisabled) {
        hint.textContent =
          "Calibration is locked while a live run is active. Stop the run to edit confidence" +
          (halfyield ? " or mass / area" : "") +
          ".";
      } else if (halfyield) {
        hint.textContent =
          "Per-class confidence plus half/piece mass per area. Changes save automatically. Locked during a live run.";
      } else {
        hint.textContent =
          "Per-class confidence. Changes save automatically. Locked during a live run.";
      }
    }

    function setDisabled(disabled) {
      inputsDisabled = Boolean(disabled);
      applyDisabledState();
      updateHint();
    }

    function addConfidenceSlider(name, value) {
      const label = document.createElement("label");
      label.textContent = titleCase(name) + " confidence";
      const output = document.createElement("output");
      const input = document.createElement("input");
      input.type = "range";
      input.id = "cal-conf-" + name;
      input.min = "0.05";
      input.max = "0.95";
      input.step = "0.05";
      input.value = String(value);
      input.dataset.className = name;
      const sync = () => {
        output.textContent = Math.round(Number(input.value) * 100) + "%";
      };
      input.addEventListener("input", () => {
        sync();
        scheduleSave();
      });
      label.append(output, input);
      sliders.append(label);
      sync();
    }

    function addMassSlider(id, title, value, bucket) {
      const label = document.createElement("label");
      label.textContent = title;
      const output = document.createElement("output");
      const input = document.createElement("input");
      input.type = "range";
      input.id = id;
      input.min = "-6";
      input.max = "0";
      input.step = "0.01";
      const safe = Math.max(1e-6, Math.min(1, Number(value) || 0.001));
      input.value = String(Math.log10(safe));
      const sync = () => {
        output.textContent = (10 ** Number(input.value)).toPrecision(4) + " g/model px²";
      };
      input.addEventListener("input", () => {
        sync();
        if (onMassInput) onMassInput(bucket, current());
        scheduleSave();
      });
      label.append(output, input);
      sliders.append(label);
      sync();
    }

    function render(nextModel, nextCalibration) {
      const installed = Boolean(nextModel && nextModel.installed);
      usesConfidence = Boolean(installed && nextModel.uses_confidence !== false);
      halfyield = Boolean(installed && nextModel.halfyield);
      classNames = installed ? (nextModel.class_names || []).slice() : [];
      modelId = installed ? String(nextModel.active_id || nextModel.filename || "") : "";
      record =
        nextCalibration ||
        nextModel?.calibration ||
        emptyRecord(classNames, halfyield);

      root.hidden = !usesConfidence || !classNames.length;
      updateHint();
      if (!usesConfidence || !classNames.length) {
        sliders.innerHTML = "";
        return;
      }

      suppressSave = true;
      sliders.innerHTML = "";
      const conf = record.class_confidence || {};
      classNames.forEach((name) => {
        const fallback = halfyield ? 0.3 : 0.5;
        addConfidenceSlider(name, conf[name] != null ? conf[name] : fallback);
      });
      if (halfyield) {
        addMassSlider("cal-mass-half", "Half mass / area", record[MASS_HALF] ?? 0.001, "half");
        addMassSlider("cal-mass-piece", "Piece mass / area", record[MASS_PIECE] ?? 0.001, "piece");
      }
      applyDisabledState();
      suppressSave = false;
    }

    return {
      render,
      current,
      getRecord: () => record,
      saving: () => saving,
      persist,
      setDisabled,
    };
  }

  window.CalibrationUi = {
    bind,
    emptyRecord,
    MASS_HALF,
    MASS_PIECE,
  };
})();
