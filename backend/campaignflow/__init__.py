"""CampaignFlow: campaign operations platform for marketing agencies."""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from . import db, scheduler
from .util import ApiError


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DATABASE=os.environ.get("CF_DATABASE", os.path.join(os.getcwd(), "campaignflow.db")),
        SESSION_DAYS=int(os.environ.get("CF_SESSION_DAYS", "7")),
        ALLOW_SIGNUP=os.environ.get("CF_ALLOW_SIGNUP", "1") == "1",
        RUN_WORKER=os.environ.get("CF_WORKER", "1") == "1",
        SIMULATE=os.environ.get("CF_SIMULATE", "0") == "1",
        SIM_INTERVAL=int(os.environ.get("CF_SIM_INTERVAL", "60")),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if config:
        app.config.update(config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    db.init_db(app.config["DATABASE"])
    with db.connect(app.config["DATABASE"]) as conn:
        with db.transaction(conn):
            scheduler.ensure_system_tasks(conn, app.config["SIMULATE"], app.config["SIM_INTERVAL"])

    app.teardown_appcontext(db.close_db)

    from .api import bp
    app.register_blueprint(bp)

    # the UI lives in the sibling ../frontend folder (override with CF_FRONTEND_DIR)
    static_dir = os.path.abspath(os.environ.get("CF_FRONTEND_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend"))

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(static_dir, name, max_age=0)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if resp.mimetype == "text/html":
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ApiError)
    def on_api_error(e: ApiError):
        return jsonify(error={"code": e.code, "message": e.message, "details": e.details}), e.status

    @app.errorhandler(HTTPException)
    def on_http_error(e: HTTPException):
        return jsonify(error={"code": e.name.lower().replace(" ", "_"), "message": e.description, "details": {}}), e.code

    @app.errorhandler(Exception)
    def on_unexpected(e):
        app.logger.exception("unhandled error")
        return jsonify(error={"code": "internal_error", "message": "Something went wrong", "details": {}}), 500

    # the worker thread is started by run.py / the dev server, not at import time (tests drive run_once directly)
    app.start_worker = lambda: _start_worker(app)
    return app


def _start_worker(app):
    if not app.config["RUN_WORKER"]:
        return None
    w = scheduler.Worker(app.config["DATABASE"])
    w.start()
    app.worker = w
    return w
