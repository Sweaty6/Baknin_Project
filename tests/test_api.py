"""HTTP contract checks; each app uses a fresh temporary database."""
import pytest

from baknin import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"),
                      "MODEL_PATH": None, "METRICS_PATH": str(tmp_path / "metrics.json")})
    return app.test_client()


def test_health_identifies_untrained_rules(client):
    result = client.get("/health")
    assert result.status_code == 200
    assert result.json["database"] == "ready"
    assert result.json["engine"] == "rules"
    assert result.json["ml_trained"] is False


def test_explicit_unavailable_model_is_diagnosed(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"),
                      "MODEL_PATH": str(tmp_path / "missing.joblib")})
    result = app.test_client().get("/health")
    assert result.status_code == 503
    assert result.json["status"] == "degraded"
    assert result.json["model"] == "unavailable"
    assert result.json["model_diagnostic"]


@pytest.mark.parametrize("path", ["/", "/reviews", "/analysis", "/methodology", "/universities/timiryazev"])
def test_pages_are_available_and_use_local_assets(client, path):
    result = client.get(path)
    assert result.status_code == 200
    assert 'lang="ru"' in result.get_data(as_text=True)
    assert "script-src 'self'" in result.headers["Content-Security-Policy"]


def test_empty_database_never_fabricates_rating(client):
    result = client.get("/api/universities").json
    assert len(result["items"]) == 5
    assert all(row["rank"] is None and row["positive_percent"] is None for row in result["items"])
    assert all(row["eligible_reviews"] == 0 for row in result["items"])
    assert client.get("/api/reviews").json["total"] == 0
    assert client.get("/api/methodology").json["metrics_available"] is False


def test_search_region_and_university_ids(client):
    result = client.get("/api/universities?q=Тимирязев&region=Москва").json
    assert [row["id"] for row in result["items"]] == ["timiryazev"]
    detail = client.get("/api/universities/timiryazev").json
    assert detail["university"]["id"] == "timiryazev"
    assert client.get("/api/universities/unknown").status_code == 404


def test_live_prediction_distinguishes_aspect_sentiment(client):
    result = client.post("/predict", json={"text": "Отличные преподаватели, но общежитие плохое"})
    assert result.status_code == 200
    assert result.json["mixed"] is True
    assert {a["id"]: a["sentiment"] for a in result.json["aspects"]} == {
        "teaching": "positive", "dormitory": "negative"}
    assert result.json["confidence"] is None
    assert result.json["needs_review"] is True
    assert client.get("/api/reviews").json["total"] == 0


@pytest.mark.parametrize("payload", [{}, [], {"text": 3}, {"text": " "}, {"text": "я" * 5001},
                                      {"text": "This movie is good"}])
def test_invalid_prediction_is_a_controlled_error(client, payload):
    result = client.post("/predict", json=payload)
    assert result.status_code == 400
    assert result.json["error"]
    assert "Traceback" not in result.get_data(as_text=True)


def test_content_type_and_oversize_request(client):
    assert client.post("/predict", data="invalid").status_code == 415
    assert client.post("/predict", data="x" * 40000, content_type="application/json").status_code == 413


@pytest.mark.parametrize("query", ["page=0", "page=no", "per_page=101", "date_from=invalid",
                                     "date_from=2026-10-13&date_to=2020-01-01", "sentiment=unknown",
                                     "source_id=99999999999999999999999999999999",
                                     "page=99999999999999999999999999999999"])
def test_invalid_filters_return_json(client, query):
    result = client.get("/api/reviews?" + query)
    assert result.status_code == 400
    assert result.json["error"]


def test_effective_methodology_configuration(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3"),
                      "MODEL_PATH": None, "MIN_REVIEWS": 17, "SMOOTHING": 4})
    result = app.test_client().get("/api/methodology").json
    assert result["min_reviews"] == 17
    assert result["smoothing"] == 4
    assert "4" in result["score_formula"]
