"""Static publication checks, including privacy and repository-path URLs."""
import json
from pathlib import Path

import pytest

from baknin.db import initialize, seed_universities
from baknin.nlp import Analyzer
from baknin.service import import_reviews
from scripts.build_pages import ROOT, export_site, normalize_base


def test_clean_export_has_correct_base_paths_and_real_empty_state(tmp_path):
    output = tmp_path / "site"
    result = export_site(output)
    assert result["universities"] == 5
    assert result["reviews"] == 0
    html = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="/Baknin_Project/static/style.css"' in html
    assert 'src="/Baknin_Project/static/pages-api.js"' in html
    assert 'href="/Baknin_Project/analysis"' in html
    assert 'content="/Baknin_Project/"' in html
    assert (output / "universities/timiryazev/index.html").is_file()
    assert (output / "launch/index.html").is_file()
    analysis_html = (output / "analysis/index.html").read_text(encoding="utf-8")
    assert "обрабатывается локально в вашем браузере" in analysis_html
    assert "Текст отправляется на сервер" not in analysis_html
    assert (output / ".nojekyll").is_file()
    assert not list(output.rglob("*.sqlite3"))
    snapshot = json.loads((output / "data/site.json").read_text(encoding="utf-8"))
    assert snapshot["reviews"] == []
    assert snapshot["methodology"]["engine"] == "browser_rules"
    assert snapshot["methodology"]["server_api_available"] is False
    assert all(item["rank"] is None for item in snapshot["universities"]["items"])
    assert set(snapshot["rules"]["aspect_labels"]) == {
        "teaching", "career", "dormitory", "infrastructure", "administration", "student_life", "finance"}
    # Rebuilding a generated folder is supported.
    assert export_site(output)["reviews"] == 0


def test_export_does_not_implicitly_read_private_database(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKNIN_DB", str(tmp_path / "should-not-exist.sqlite3"))
    export_site(tmp_path / "site")
    assert not (tmp_path / "should-not-exist.sqlite3").exists()


def test_public_export_removes_private_text_and_preserves_input_database(tmp_path):
    db = tmp_path / "private.sqlite3"
    initialize(db)
    seed_universities(db)
    text = "СЕКРЕТНАЯ ТЕХНИЧЕСКАЯ ФИКСТУРА: преподаватели хорошие."
    source = tmp_path / "fixtures.jsonl"
    source.write_text(json.dumps({
        "university_id": "timiryazev", "text": text,
        "source": {"platform": "Test fixtures", "url": "https://example.org/fixtures", "terms_checked_at": "2026-10-13"},
        "source_url": "https://example.org/fixtures/1", "published_at": "2026-01-13",
        "collected_at": "2026-10-13", "verified": True, "personal_data_removed": True,
        "display_allowed": False, "sentiment": "positive",
        "aspects": [{"aspect": "teaching", "sentiment": "positive", "context": "преподаватели хорошие"}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    import_reviews(db, source, Analyzer())
    original = db.read_bytes()
    output = tmp_path / "site"
    export_site(output, database=db)
    assert db.read_bytes() == original
    raw = (output / "data/site.json").read_text(encoding="utf-8")
    assert text not in raw and "преподаватели хорошие" not in raw
    snapshot = json.loads(raw)
    assert len(snapshot["reviews"]) == 1
    assert snapshot["reviews"][0]["text"] is None
    assert snapshot["reviews"][0]["aspects"][0]["context"] is None
    assert not list(output.rglob("*.sqlite3"))


def test_build_preserves_unrecognized_existing_files(tmp_path):
    output = tmp_path / "user-files"
    output.mkdir()
    keep = output / "keep.txt"
    keep.write_text("user content")
    with pytest.raises(ValueError, match="не пуста"):
        export_site(output)
    assert keep.read_text() == "user content"


@pytest.mark.parametrize("directory", ["static", "templates", "data", "docs", "baknin", "scripts", "tests", ".git"])
def test_output_cannot_be_inside_source_directories(directory):
    with pytest.raises(ValueError, match="исходников"):
        export_site(ROOT / directory / "generated-pages")
    assert not (ROOT / directory / "generated-pages").exists()


def test_input_database_inside_output_is_preserved(tmp_path):
    output = tmp_path / "site"
    export_site(output)
    db = output / "local.sqlite3"
    initialize(db)
    seed_universities(db)
    original = db.read_bytes()
    with pytest.raises(ValueError, match="Входная database"):
        export_site(output, database=db)
    assert db.read_bytes() == original


@pytest.mark.parametrize("base", ["https://example.org/", "/../x/", "/a/./b/", "/bad?query/"])
def test_invalid_base_path_rejected(base):
    with pytest.raises(ValueError):
        normalize_base(base)


def test_custom_domain_root_and_project_name():
    assert normalize_base("/") == "/"
    assert normalize_base("/Baknin_Project") == "/Baknin_Project/"
