#!/usr/bin/env python3
"""Pecan Vision Kit — web recorder + admin UI."""

from __future__ import annotations

from flask import Flask

from segment import MAX_UPLOAD_BYTES
import runtime as rt
from routes.ui import bp as ui_bp
from routes.camera import bp as camera_bp
from routes.library import bp as library_bp
from routes.model import bp as model_bp
from routes.system import bp as system_bp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.register_blueprint(ui_bp)
app.register_blueprint(camera_bp)
app.register_blueprint(library_bp)
app.register_blueprint(model_bp)
app.register_blueprint(system_bp)


@app.context_processor
def inject_version():
    return rt.version_context()


def create_app() -> Flask:
    rt.init_camera()
    rt.models.start_worker()
    return app


def main() -> None:
    rt.init_camera()
    rt.models.start_worker()
    host = rt.settings.get("host", "0.0.0.0")
    port = int(rt.settings.get("port", 8080))
    # threaded=True so MJPEG stream + API work together
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
