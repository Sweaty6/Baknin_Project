"""Application factory for the Russian university review project."""

from __future__ import annotations

import os
import json
import math
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException


def create_app(test_config=None):
    root = Path(__file__).resolve().parent.parent
    instance = root / "instance"
    app = Flask(__name__, template_folder=str(root / "templates"),
                static_folder=str(root / "static"), instance_path=str(instance))
    app.config.update(
        DATABASE=os.environ.get("BAKNIN_DB", str(instance / "baknin.sqlite3")),
        MODEL_PATH=os.environ.get("BAKNIN_MODEL") or None,
        METRICS_PATH=os.environ.get("BAKNIN_METRICS") or str(instance / "metrics.json"),
        MIN_REVIEWS=int(os.environ.get("BAKNIN_MIN_REVIEWS", "10")),
        SMOOTHING=float(os.environ.get("BAKNIN_SMOOTHING", "10")),
        CONFIDENCE_THRESHOLD=0.65,
        MAX_CONTENT_LENGTH=32768,
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)
    if (app.config["MIN_REVIEWS"] < 1 or not math.isfinite(app.config["SMOOTHING"])
            or app.config["SMOOTHING"] < 0):
        raise ValueError("MIN_REVIEWS must be positive; SMOOTHING must be nonnegative")
    app.json.ensure_ascii = False
    from .db import initialize, seed_universities
    from .nlp import Analyzer, ASPECT_LABELS
    from . import service
    initialize(app.config["DATABASE"])
    seed_universities(app.config["DATABASE"])
    app.extensions["analyzer"] = Analyzer(
        model_path=app.config["MODEL_PATH"],
        confidence_threshold=app.config["CONFIDENCE_THRESHOLD"],
    )

    def options():
        return {"min_reviews": app.config["MIN_REVIEWS"], "smoothing": app.config["SMOOTHING"]}

    def filters():
        result = {}
        for name in ("q", "region", "sentiment", "aspect", "university_id"):
            value = request.args.get(name, "").strip()
            if value:
                if len(value) > 200:
                    raise ValueError("Слишком длинный параметр фильтра")
                result[name] = value
        if "sentiment" in result and result["sentiment"] not in {"positive", "negative", "neutral"}:
            raise ValueError("Неизвестная тональность")
        for name in ("source_id",):
            value = request.args.get(name)
            if value:
                result[name] = int(value)
                if not 1 <= result[name] <= 2**63 - 1:
                    raise ValueError("Идентификатор вне допустимого диапазона")
        for name in ("date_from", "date_to"):
            value = request.args.get(name)
            if value:
                result[name] = date.fromisoformat(value).isoformat()
        if result.get("date_from", "") > result.get("date_to", "9999-12-31"):
            raise ValueError("Начальная дата не может быть позже конечной")
        return result

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/universities/<university_id>")
    def university(university_id):
        detail = service.university_detail(app.config["DATABASE"], university_id, **options())
        if detail is None:
            return render_template("error.html", message="Вуз не найден", status=404), 404
        return render_template("university.html", university_id=university_id)

    @app.get("/reviews")
    def reviews_page():
        return render_template("reviews.html")

    @app.get("/analysis")
    def analysis_page():
        return render_template("analysis.html")

    @app.get("/methodology")
    def methodology_page():
        return render_template("methodology.html")

    @app.get("/health")
    def health():
        from .db import connect
        with connect(app.config["DATABASE"]) as connection:
            connection.execute("SELECT COUNT(*) FROM universities").fetchone()
        analyzer = app.extensions["analyzer"]
        probe = analyzer.analyze("Отличные преподаватели, но общежитие плохое")
        degraded = bool(analyzer.load_error)
        return jsonify(status="degraded" if degraded else "ok", database="ready",
                       model="unavailable" if degraded else "ready",
                       engine=probe["engine"], model_version=probe["model_version"],
                       ml_trained=probe["engine"] == "tfidf_logistic",
                       model_diagnostic="Указанный артефакт не удалось проверить или загрузить." if degraded else None,
                       limitation="Реальные отзывы и оценка качества модели требуют отдельного корпуса."), 503 if degraded else 200

    @app.get("/api/universities")
    def universities_api():
        items = service.ranking(app.config["DATABASE"], filters=filters(), **options())
        all_items = service.ranking(app.config["DATABASE"], **options())
        return jsonify(items=items, regions=sorted({u["region"] for u in all_items}),
                       meta=dict(**options(), total=len(items),
                                 parameter_status="Пилотные параметры; калибровка на корпусе ещё не выполнена."))

    @app.get("/api/universities/<university_id>")
    def university_api(university_id):
        detail = service.university_detail(app.config["DATABASE"], university_id, **options())
        if detail is None:
            return jsonify(error="Вуз не найден"), 404
        return jsonify(detail)

    @app.get("/api/reviews")
    def reviews_api():
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "20"))
        if page < 1 or not 1 <= per_page <= 100:
            raise ValueError("Некорректная страница или размер страницы")
        return jsonify(service.list_reviews(app.config["DATABASE"], filters=filters(),
                                            page=page, per_page=per_page))

    @app.get("/api/methodology")
    def methodology_api():
        result = service.methodology(app.config["DATABASE"], **options())
        result.update(options())
        probe = app.extensions["analyzer"].analyze("Учусь в университете")
        result.update(engine=probe["engine"], model_version=probe["model_version"],
                      parameter_status="Пилотные значения, не подобраны на реальном корпусе.")
        result["aspects"] = [{"id": key, "label": value} for key, value in ASPECT_LABELS.items()]
        result["metrics_available"] = False
        result["metrics"] = None
        metrics_path = Path(app.config["METRICS_PATH"])
        if probe["engine"] == "tfidf_logistic" and metrics_path.is_file():
            try:
                if metrics_path.stat().st_size <= 1_000_000:
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                    if metrics.get("metadata", {}).get("model_version") == probe["model_version"]:
                        result["metrics"] = metrics
                        result["metrics_available"] = True
            except (OSError, ValueError, AttributeError):
                pass
        return jsonify(result)

    @app.post("/predict")
    @app.post("/api/predict")
    def predict():
        if not request.is_json:
            return jsonify(error="Отправьте JSON с полем text"), 415
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            return jsonify(error="Поле text должно быть строкой"), 400
        text = payload["text"].strip()
        if not text or len(text) > 5000:
            return jsonify(error="Введите от 1 до 5000 символов"), 400
        if not any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in text):
            return jsonify(error="Введите отзыв на русском языке"), 400
        return jsonify(app.extensions["analyzer"].analyze(text))

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {413: "Размер запроса превышает допустимый", 404: "Страница не найдена",
                    405: "Этот метод не поддерживается"}
        message = messages.get(error.code, "Некорректный запрос")
        if request.path.startswith("/api/") or request.path in {"/predict", "/health"}:
            return jsonify(error=message), error.code
        return render_template("error.html", message=message, status=error.code), error.code

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    return app
