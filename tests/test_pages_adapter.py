"""Public snapshot behavior matches the live API; fixture reviews are invented."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from baknin import create_app
from baknin.nlp import Analyzer, ASPECT_LABELS, RULES_VERSION, _ASPECT_REGEX, _NEGATIVE, _POSITIVE, _STOPWORDS
from baknin.service import import_reviews

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def published(tmp_path):
    database = tmp_path / "fixture.sqlite3"
    app = create_app({"TESTING": True, "DATABASE": str(database), "MODEL_PATH": None})
    client = app.test_client()
    records = []
    for university, sentiment, context in [
        ("timiryazev", "positive", "Преподаватели хорошие"),
        ("dongau", "positive", "Преподаватели хорошие"),
        ("kubsau", "negative", "Преподаватели плохие"),
    ]:
        for index in range(10):
            records.append({
                "university_id": university, "text": f"{context} фикстура {index}",
                "sentiment": sentiment, "display_allowed": index != 0,
                "aspects": [{"aspect": "teaching", "sentiment": sentiment, "context": context}],
            })
    records += [
        {"university_id": "kazgau", "text": "Обычные занятия тест", "sentiment": "neutral", "display_allowed": True},
        {"university_id": "timiryazev", "text": "Отличная практика непроверенный тест", "display_allowed": True},
    ]
    for index, record in enumerate(records):
        record.update({
            "source": {"platform": "Invented fixtures", "url": "https://example.org/", "terms_checked_at": "2025-03-01"},
            "source_url": f"https://example.org/fixtures/{index}",
            "published_at": "2025-01-01" if index % 2 else "2025-02-02",
            "collected_at": "2025-03-01", "verified": True, "personal_data_removed": True,
        })
    source = tmp_path / "fixtures.jsonl"
    source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    import_reviews(database, source, Analyzer())
    universities = client.get("/api/universities").json
    manifest = {
        "format": 1, "exported_at": "2026-10-13T10:00:00Z", "base_path": "/Baknin_Project/",
        "universities": universities,
        "reviews": client.get("/api/reviews?per_page=100").json["items"],
        "details": {item["id"]: client.get(f"/api/universities/{item['id']}").json for item in universities["items"]},
        "methodology": client.get("/api/methodology").json, "health": client.get("/health").json,
        "rules": {
            "version": RULES_VERSION, "aspect_labels": ASPECT_LABELS,
            "aspect_patterns": {name: pattern.pattern for name, pattern in _ASPECT_REGEX.items()},
            "positive_pattern": _POSITIVE.pattern, "negative_pattern": _NEGATIVE.pattern,
            "stopwords": sorted(_STOPWORDS),
        },
    }
    return client, manifest


def browser_requests(manifest, requests, failed_fetches=0):
    if not shutil.which("node"):
        pytest.skip("Node.js required for the Pages snapshot API")
    script = """
const payload = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const calls = [];
global.document = {querySelector: () => ({content: '/Baknin_Project/'})};
global.fetch = async (url) => {
  calls.push(url);
  if (calls.length <= payload.failed_fetches) throw new Error('Temporary fixture network failure');
  return {ok: true, json: async () => payload.manifest};
};
require(process.argv[1]);
require(process.argv[2]);
(async () => {
  const results = [];
  for (const entry of payload.requests) {
    try {
      const result = await global.BakninPagesAPI.request(entry.path, entry.options || {});
      results.push({result});
      if (entry.mutate) result.items[0].text = 'mutated caller value';
    } catch (error) { results.push({error: error.message}); }
  }
  process.stdout.write(JSON.stringify({results, calls}));
})();
"""
    completed = subprocess.run(
        ["node", "-e", script, str(ROOT / "static/pages-nlp.js"), str(ROOT / "static/pages-api.js")],
        input=json.dumps({"manifest": manifest, "requests": requests, "failed_fetches": failed_fetches}, ensure_ascii=False),
        capture_output=True, text=True, check=True, timeout=15,
    )
    return json.loads(completed.stdout)


def test_ranking_filters_baseline_topics_ties_and_small_samples_match_server(published):
    client, manifest = published
    paths = ["/api/universities" + query for query in [
        "", "?q=Краснодар", "?region=Москва", "?date_from=2025-02-02&date_to=2025-02-02",
        "?sentiment=positive", "?aspect=teaching", "?source_id=1", "?university_id=timiryazev",
    ]]
    browser = browser_requests(manifest, [{"path": path} for path in paths])
    assert browser["calls"] == ["/Baknin_Project/data/site.json"]
    for path, entry in zip(paths, browser["results"], strict=True):
        expected = client.get(path).json
        result = entry["result"]
        assert result["items"] == expected["items"], path
        assert result["regions"] == expected["regions"]
        assert result["meta"] == {**expected["meta"], "exported_at": manifest["exported_at"]}
    # The two equally positive schools tie, while the one-review school is unranked.
    ranked = {item["id"]: item for item in browser["results"][0]["result"]["items"]}
    assert ranked["timiryazev"]["rank"] == ranked["dongau"]["rank"] == 1
    assert ranked["kazgau"]["rank"] is None
    assert ranked["kazgau"]["smoothed_score"] is None


def test_review_filters_order_and_pagination_match_server(published):
    client, manifest = published
    paths = ["/api/reviews" + query for query in [
        "", "?page=2&per_page=4", "?q=ПРЕПОДАВАТЕЛИ", "?q=РГАУ", "?q=ничегонет",
        "?region=Москва", "?university_id=dongau&sentiment=positive&aspect=teaching",
        "?source_id=1&date_from=2025-02-02&date_to=2025-02-02", "?page=100&per_page=100",
    ]]
    browser = browser_requests(manifest, [{"path": path} for path in paths])
    for path, entry in zip(paths, browser["results"], strict=True):
        assert entry["result"] == client.get(path).json, path


def test_methodology_health_detail_and_prediction_describe_browser_rules(published):
    _, manifest = published
    manifest["methodology"].update(engine="tfidf_logistic", metrics_available=True, metrics={"accuracy": 1.0})
    result = browser_requests(manifest, [
        {"path": "/api/methodology"}, {"path": "/health"}, {"path": "/api/universities/timiryazev"},
        {"path": "/predict", "options": {"method": "POST", "body": json.dumps({"text": "Отличные преподаватели, но общежитие плохое"})}},
    ])
    method, health, detail, prediction = [entry["result"] for entry in result["results"]]
    assert method["engine"] == health["engine"] == detail["methodology"]["engine"] == prediction["engine"] == "browser_rules"
    assert method["metrics_available"] is False and method["metrics"] is None
    assert health["ml_trained"] is False
    assert prediction["confidence"] is None and prediction["needs_review"] is True
    assert prediction["mixed"] is True
    assert "локально в браузере" in prediction["explanation"]
    assert result["calls"] == ["/Baknin_Project/data/site.json"]


@pytest.mark.parametrize("query", [
    "page=0", "page=no", "per_page=101", "per_page=0", "page=9999999999999999999999999",
    "source_id=0", "source_id=999999999999999999999999999", "source_id=1.5", "sentiment=uncertain",
    "date_from=2025-02-30", "date_from=invalid", "date_from=2026-01-01&date_to=2025-01-01",
])
def test_invalid_filters_fail_usefully(published, query):
    _, manifest = published
    entry = browser_requests(manifest, [{"path": "/api/reviews?" + query}])["results"][0]
    assert entry.get("error")


def test_failed_snapshot_can_retry_and_callers_cannot_mutate_cache(published):
    _, manifest = published
    result = browser_requests(manifest, [
        {"path": "/api/reviews"}, {"path": "/api/reviews", "mutate": True}, {"path": "/api/reviews"},
    ], failed_fetches=1)
    assert "error" in result["results"][0]
    assert result["results"][1]["result"]["items"][0]["text"] == "mutated caller value"
    assert result["results"][2]["result"]["items"][0]["text"] != "mutated caller value"
    assert result["calls"] == ["/Baknin_Project/data/site.json"] * 2


def test_hidden_text_not_searchable_and_unknown_university_is_controlled(published):
    _, manifest = published
    # Even a malformed snapshot cannot turn a hidden text into a search match.
    hidden = next(review for review in manifest["reviews"] if not review["display_allowed"])
    hidden["text"] = "hiddenuniquefixture"
    result = browser_requests(manifest, [
        {"path": "/api/reviews?q=hiddenuniquefixture"},
        {"path": "/api/universities/unknown"},
        {"path": "/api/reviews?uni_id=dongau"},
    ])["results"]
    assert result[0]["result"]["total"] == 0
    assert result[1]["error"] == "Вуз не найден"
    assert result[2]["result"]["total"] == 10
