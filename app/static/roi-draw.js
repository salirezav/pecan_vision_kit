/**
 * Drag-to-draw rectangle on an image inside a .preview-wrap.
 * Maps CSS pixels to natural image pixels (width:100%; height:auto).
 */
(function (global) {
  "use strict";

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function RoiDrawer(options) {
    this.wrap = options.wrap;
    this.img = options.img;
    this.readOnly = Boolean(options.readOnly);
    this.onChange = typeof options.onChange === "function" ? options.onChange : null;
    this.onCommit = typeof options.onCommit === "function" ? options.onCommit : null;
    this.onClear = typeof options.onClear === "function" ? options.onClear : null;
    this.roi = null;
    this._drawing = false;
    this._redrawMode = false;
    this._start = null;
    this._overlay = document.createElement("div");
    this._overlay.className = "roi-overlay";
    this._rect = document.createElement("div");
    this._rect.className = "roi-rect";
    this._rect.hidden = true;
    this._overlay.appendChild(this._rect);
    this.wrap.appendChild(this._overlay);
    this._boundPointerDown = this._onPointerDown.bind(this);
    this._boundPointerMove = this._onPointerMove.bind(this);
    this._boundPointerUp = this._onPointerUp.bind(this);
    if (!this.readOnly) {
      this._overlay.addEventListener("pointerdown", this._boundPointerDown);
      this._overlay.addEventListener("pointermove", this._boundPointerMove);
      this._overlay.addEventListener("pointerup", this._boundPointerUp);
      this._overlay.addEventListener("pointercancel", this._boundPointerUp);
    } else {
      this._overlay.classList.add("roi-overlay-readonly");
    }
  }

  RoiDrawer.prototype._layoutOverlay = function () {
    const wrapRect = this.wrap.getBoundingClientRect();
    const imgRect = this.img.getBoundingClientRect();
    this._overlay.style.left = Math.max(0, imgRect.left - wrapRect.left) + "px";
    this._overlay.style.top = Math.max(0, imgRect.top - wrapRect.top) + "px";
    this._overlay.style.width = imgRect.width + "px";
    this._overlay.style.height = imgRect.height + "px";
    this._overlay.style.right = "auto";
    this._overlay.style.bottom = "auto";
  };

  RoiDrawer.prototype._metrics = function () {
    this._layoutOverlay();
    const naturalW = this.img.naturalWidth || 0;
    const naturalH = this.img.naturalHeight || 0;
    const displayW = this.img.clientWidth || 0;
    const displayH = this.img.clientHeight || 0;
    if (!naturalW || !naturalH || !displayW || !displayH) {
      return null;
    }
    return {
      naturalW: naturalW,
      naturalH: naturalH,
      displayW: displayW,
      displayH: displayH,
      scaleX: naturalW / displayW,
      scaleY: naturalH / displayH,
    };
  };

  RoiDrawer.prototype._eventPoint = function (event) {
    const bounds = this.img.getBoundingClientRect();
    return {
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    };
  };

  RoiDrawer.prototype._cssToImage = function (cssRect, metrics) {
    const x = Math.round(cssRect.x * metrics.scaleX);
    const y = Math.round(cssRect.y * metrics.scaleY);
    const w = Math.round(cssRect.w * metrics.scaleX);
    const h = Math.round(cssRect.h * metrics.scaleY);
    return {
      x: clamp(x, 0, metrics.naturalW - 1),
      y: clamp(y, 0, metrics.naturalH - 1),
      w: clamp(w, 1, metrics.naturalW),
      h: clamp(h, 1, metrics.naturalH),
    };
  };

  RoiDrawer.prototype._imageToCss = function (roi, metrics) {
    return {
      x: roi.x / metrics.scaleX,
      y: roi.y / metrics.scaleY,
      w: roi.w / metrics.scaleX,
      h: roi.h / metrics.scaleY,
    };
  };

  RoiDrawer.prototype._paint = function (cssRect) {
    if (!cssRect || cssRect.w < 1 || cssRect.h < 1) {
      this._rect.hidden = true;
      return;
    }
    this._rect.hidden = false;
    this._rect.style.left = cssRect.x + "px";
    this._rect.style.top = cssRect.y + "px";
    this._rect.style.width = cssRect.w + "px";
    this._rect.style.height = cssRect.h + "px";
  };

  RoiDrawer.prototype.setRoi = function (roi) {
    this.roi = roi ? { x: roi.x, y: roi.y, w: roi.w, h: roi.h } : null;
    const metrics = this._metrics();
    if (!this.roi || !metrics) {
      this._paint(null);
      return;
    }
    this._paint(this._imageToCss(this.roi, metrics));
  };

  RoiDrawer.prototype.refresh = function () {
    this.setRoi(this.roi);
  };

  RoiDrawer.prototype.clear = function () {
    this.roi = null;
    this._redrawMode = false;
    this._paint(null);
    if (this.onClear) this.onClear();
    if (this.onChange) this.onChange(null);
  };

  RoiDrawer.prototype.redraw = function () {
    this.roi = null;
    this._redrawMode = true;
    this._paint(null);
    if (this.onChange) this.onChange(null);
  };

  RoiDrawer.prototype._onPointerDown = function (event) {
    if (this.readOnly) return;
    if (event.button != null && event.button !== 0) return;
    const metrics = this._metrics();
    if (!metrics) return;
    event.preventDefault();
    this._overlay.setPointerCapture(event.pointerId);
    this._drawing = true;
    const point = this._eventPoint(event);
    this._start = {
      x: clamp(point.x, 0, metrics.displayW),
      y: clamp(point.y, 0, metrics.displayH),
    };
    this._paint({ x: this._start.x, y: this._start.y, w: 0, h: 0 });
  };

  RoiDrawer.prototype._onPointerMove = function (event) {
    if (!this._drawing || !this._start) return;
    const metrics = this._metrics();
    if (!metrics) return;
    const point = this._eventPoint(event);
    const x2 = clamp(point.x, 0, metrics.displayW);
    const y2 = clamp(point.y, 0, metrics.displayH);
    const cssRect = {
      x: Math.min(this._start.x, x2),
      y: Math.min(this._start.y, y2),
      w: Math.abs(x2 - this._start.x),
      h: Math.abs(y2 - this._start.y),
    };
    this._paint(cssRect);
  };

  RoiDrawer.prototype._onPointerUp = function (event) {
    if (!this._drawing || !this._start) return;
    this._drawing = false;
    try {
      this._overlay.releasePointerCapture(event.pointerId);
    } catch (_err) {
      /* ignore */
    }
    const metrics = this._metrics();
    if (!metrics) {
      this._start = null;
      return;
    }
    const point = this._eventPoint(event);
    const x2 = clamp(point.x, 0, metrics.displayW);
    const y2 = clamp(point.y, 0, metrics.displayH);
    const cssRect = {
      x: Math.min(this._start.x, x2),
      y: Math.min(this._start.y, y2),
      w: Math.abs(x2 - this._start.x),
      h: Math.abs(y2 - this._start.y),
    };
    this._start = null;
    if (cssRect.w < 8 || cssRect.h < 8) {
      this.setRoi(this.roi);
      return;
    }
    const imageRoi = this._cssToImage(cssRect, metrics);
    if (imageRoi.w < 32 || imageRoi.h < 32) {
      this.setRoi(this.roi);
      return;
    }
    this._redrawMode = false;
    this.roi = imageRoi;
    this._paint(this._imageToCss(imageRoi, metrics));
    if (this.onChange) this.onChange(this.roi);
    if (this.onCommit) this.onCommit(this.roi);
  };

  global.RoiDrawer = RoiDrawer;
})(window);
