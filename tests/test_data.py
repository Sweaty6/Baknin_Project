"""Ephemeral test fixtures only: no review corpus is shipped or seeded."""
import json
import sqlite3
import hashlib

import pytest

from baknin.db import connect, initialize, seed_universities
from baknin.nlp import Analyzer
from baknin.service import (
    import_reviews, list_reviews, methodology, moderate_review, ranking,
    reclassify_reviews, university_detail,
)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize(path)
    seed_universities(path)
    return path


def record(text="Отличные преподаватели", university_id="timiryazev", **changes):
    item = {"university_id": university_id, "text": text,
            "source": {"platform": "Test fixture", "url": "https://example.org/",
                       "terms_checked_at": "2025-03-01"},
            "source_url": f"https://example.org/reviews/{university_id}/{hashlib.sha256(text.encode()).hexdigest()[:12]}",
            "published_at": "2025-01-15",
            "collected_at": "2025-03-01", "verified": True,
            "display_allowed": True, "personal_data_removed": True}
    item.update(changes)
    return item


def write_jsonl(tmp_path, records):
    file_path = tmp_path / "reviews.jsonl"
    file_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")
    return file_path


def import_records(database, tmp_path, records):
    return import_reviews(database, write_jsonl(tmp_path, records), Analyzer())


def test_seed_only_agrarian_metadata(database):
    with connect(database) as connection:
        items = connection.execute("SELECT * FROM universities").fetchall()
        assert len(items) == 5
        assert all("аграрный" in item["full_name"] for item in items)
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert seed_universities(database) == 0
    assert all(item["rank"] is None and item["smoothed_score"] is None and item["status"] == "no_data"
               for item in ranking(database))


def test_context_manager_closes_connection(database):
    with connect(database) as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_import_deduplicates_normalization_and_keeps_provenance(database, tmp_path):
    records = [record("Преподаватели хорошие!"), record("  ПРЕПОДАВАТЕЛИ   хорошие.  "),
               record("Преподаватели хорошие", "kubsau")]
    stats = import_records(database, tmp_path, records)
    assert stats == {"imported": 2, "duplicates": 1, "pending_moderation": 2}
    review = list_reviews(database, {"university_id": "timiryazev"})["items"][0]
    assert review["text"] == "Преподаватели хорошие!"
    assert review["source_url"] == records[0]["source_url"]
    assert review["source"]["platform"] == "Test fixture"
    assert review["confidence"] is None and review["needs_review"] is True


def test_external_id_deduplicates_edited_source_post(database, tmp_path):
    stats = import_records(database, tmp_path, [
        record("Хорошие преподаватели", source_review_id="external-123"),
        record("Хорошие преподаватели, обновлённый текст", source_review_id="external-123"),
    ])
    assert stats["imported"] == 1 and stats["duplicates"] == 1
    assert list_reviews(database)["items"][0]["source_review_id"] == "external-123"


def test_external_id_identity_ignores_renamed_platform_display_label(database, tmp_path):
    import_records(database, tmp_path, [record("Оригинальный отзыв о преподавателях", source_review_id="post-123")])
    renamed_source = {"platform": "Тестовая площадка", "url": "https://example.org/",
                      "terms_checked_at": "2025-03-01"}
    stats = import_records(database, tmp_path, [
        record("Обновлённый отзыв о преподавателях", source_review_id="post-123", source=renamed_source)])
    assert stats["duplicates"] == 1 and stats["imported"] == 0
    original = list_reviews(database)["items"][0]
    assert original["text"] == "Оригинальный отзыв о преподавателях"
    assert original["source"]["platform"] == "Test fixture"
    with pytest.raises(ValueError, match="другому вузу"):
        import_records(database, tmp_path, [
            record("Текст с неверной привязкой", "kubsau", source_review_id="post-123", source=renamed_source)])
    assert list_reviews(database)["total"] == 1


def test_edited_direct_permalink_does_not_inflate_rating(database, tmp_path):
    source_url = "https://example.org/reviews/original-post"
    stats = import_records(database, tmp_path, [
        record("Хорошие преподаватели", source_url=source_url, sentiment="positive"),
        record("Хорошие преподаватели, обновлённый отзыв", source_url=source_url, sentiment="positive"),
    ])
    assert stats["imported"] == 1 and stats["duplicates"] == 1
    original = list_reviews(database)["items"][0]
    assert original["text"] == "Хорошие преподаватели"
    item = ranking(database, {"university_id": "timiryazev"}, min_reviews=2)[0]
    assert item["eligible_reviews"] == 1 and item["rank"] is None
    assert item["smoothed_score"] is None


@pytest.mark.parametrize("external_id", [None, "physical-123"])
def test_physical_post_conflicting_university_rolls_back_entire_import(database, tmp_path, external_id):
    source_url = "https://example.org/reviews/physical-post"
    import_records(database, tmp_path, [record("Хорошие преподаватели", source_url=source_url,
                                             source_review_id=external_id)])
    new_url = source_url if external_id is None else "https://example.org/changed-permalink"
    new_source = {"platform": "A new fixture source", "url": "https://example.org/",
                  "terms_checked_at": "2025-03-01"}
    with pytest.raises(ValueError, match="Строка 2.*другому вузу"):
        import_records(database, tmp_path, [
            record("Другая публикация для другого вуза", "omgau", source=new_source),
            record("Изменённый текст чужой публикации", "kubsau", source_url=new_url,
                   source_review_id=external_id),
        ])
    with connect(database) as connection:
        for table in ["reviews", "sources", "predictions", "review_audit"]:
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_physical_conflict_is_not_hidden_by_local_text_duplicate(database, tmp_path):
    import_records(database, tmp_path, [
        record("Оригинал для первого вуза", source_review_id="physical-123"),
        record("Локальный текст второго вуза", "kubsau", source_review_id="other-456"),
    ])
    with pytest.raises(ValueError, match="другому вузу"):
        import_records(database, tmp_path, [
            record("Локальный текст второго вуза", "kubsau", source_review_id="physical-123")])
    assert list_reviews(database)["total"] == 2


def test_shared_listing_requires_distinct_explicit_post_ids(database, tmp_path):
    shared_url = "https://example.org/reviews/listing"
    stats = import_records(database, tmp_path, [
        record("Первый отзыв о преподавателях", source_url=shared_url, source_review_id="post-a"),
        record("Второй отзыв об общежитии", source_url=shared_url, source_review_id="post-b"),
        record("Отзыв для другого вуза", "kubsau", source_url=shared_url, source_review_id="post-c"),
    ])
    assert stats["imported"] == 3
    with pytest.raises(ValueError):
        import_records(database, tmp_path, [record("Отзыв без идентификатора", source_url=shared_url)])
    assert list_reviews(database)["total"] == 3


def test_listing_with_two_known_posts_cannot_import_without_id(database, tmp_path):
    shared_url = "https://example.org/reviews/one-university-listing"
    import_records(database, tmp_path, [
        record("Первый отзыв", source_url=shared_url, source_review_id="post-a"),
        record("Второй отзыв", source_url=shared_url, source_review_id="post-b"),
    ])
    with pytest.raises(ValueError, match="требуется source_review_id"):
        import_records(database, tmp_path, [record("Неизвестный третий отзыв", source_url=shared_url)])


def test_explicit_id_cannot_turn_existing_direct_post_into_another_review(database, tmp_path):
    source_url = "https://example.org/reviews/original-post"
    import_records(database, tmp_path, [record("Оригинальный отзыв", source_url=source_url)])
    stats = import_records(database, tmp_path, [record("Переиздание оригинального отзыва", source_url=source_url,
                                                     source_review_id="newly-added-id")])
    assert stats["duplicates"] == 1 and stats["imported"] == 0
    assert list_reviews(database)["items"][0]["text"] == "Оригинальный отзыв"


def test_concurrent_imports_cannot_assign_one_post_to_two_universities(database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    paths = []
    for university_id in ["timiryazev", "kubsau"]:
        path = tmp_path / f"{university_id}.jsonl"
        path.write_text(json.dumps(record(f"Отзыв о {university_id}", university_id,
                                         source_review_id="one-physical-post")), encoding="utf-8")
        paths.append(path)

    def attempt(path):
        barrier.wait(timeout=10)
        try:
            return import_reviews(database, path, Analyzer())
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, paths))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, str) and "другому вузу" in result for result in results) == 1
    assert list_reviews(database)["total"] == 1


def test_invalid_line_rolls_back_reviews_sources_predictions_and_audit(database, tmp_path):
    with pytest.raises(ValueError, match="Строка 2"):
        import_records(database, tmp_path, [record(), record("Другой отзыв", "unknown")])
    with connect(database) as connection:
        for table in ["reviews", "sources", "predictions", "review_aspects", "review_audit"]:
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"verified": False}, {"personal_data_removed": False}, {"display_allowed": None},
    {"source_url": "javascript:alert(1)"}, {"published_at": "no-date"},
    {"text": " "}, {"text": "!"}, {"text": "я" * 5001}, {"original_score": float("nan")},
    {"source_url": "https://someone:password@example.org/review"},
])
def test_import_requires_verified_safe_complete_provenance(database, tmp_path, change):
    with pytest.raises(ValueError):
        import_records(database, tmp_path, [record(**change)])
    assert list_reviews(database)["total"] == 0


def test_filters_use_publication_date_casefold_and_page(database, tmp_path):
    import_records(database, tmp_path, [
        record("Хорошие преподаватели первый", sentiment="positive", published_at="2025-01-01"),
        record("Плохое общежитие второй", "kubsau", sentiment="negative", published_at="2025-02-02"),
        record("Удобная лаборатория третий", published_at="2025-02-02", collected_at="2025-04-01"),
    ])
    assert list_reviews(database, {"date_from": "2025-02-02", "date_to": "2025-02-02"})["total"] == 2
    assert list_reviews(database, {"q": "ПРЕПОДАВАТЕЛИ"})["total"] == 1
    assert list_reviews(database, {"region": "Краснодарский край"})["total"] == 1
    assert list_reviews(database, {"sentiment": "negative"})["total"] == 1
    assert list_reviews(database, {"aspect": "dormitory"})["total"] == 1
    page = list_reviews(database, page=2, per_page=1)
    assert page["total"] == 3 and page["pages"] == 3 and len(page["items"]) == 1
    assert list_reviews(database, {"source_id": page["items"][0]["source"]["id"]})["total"] == 3


def test_filter_boundaries_are_controlled(database):
    with pytest.raises(ValueError):
        list_reviews(database, page=10**30)
    with pytest.raises(ValueError):
        list_reviews(database, {"source_id": 10**30})
    assert list_reviews(database, {"date_to": "9999-12-31"})["total"] == 0
    assert ranking(database, {"q": "Краснодар"})[0]["id"] == "kubsau"


def test_calendar_filters_and_months_use_the_displayed_moscow_day(database, tmp_path):
    import_records(database, tmp_path, [
        record("Последняя секунда января по Москве", published_at="2025-01-31T20:59:59Z", sentiment="neutral"),
        record("Первая секунда февраля по Москве", published_at="2025-01-31T21:00:00Z", sentiment="positive"),
        record("Последняя секунда первого февраля", published_at="2025-02-01T20:59:59Z", sentiment="negative"),
        record("Первая секунда второго февраля", published_at="2025-02-01T21:00:00Z", sentiment="neutral"),
        record("Тот же момент с исходной временной зоной", published_at="2025-02-01T00:00:00+03:00", sentiment="positive"),
    ])
    february_first = list_reviews(database, {"date_from": "2025-02-01", "date_to": "2025-02-01"})
    assert february_first["total"] == 3
    assert {item["text"] for item in february_first["items"]} == {
        "Первая секунда февраля по Москве", "Последняя секунда первого февраля",
        "Тот же момент с исходной временной зоной"}
    assert list_reviews(database, {"date_to": "2025-01-31"})["total"] == 1
    assert list_reviews(database, {"date_from": "2025-02-01"})["total"] == 4
    item = ranking(database, {"date_from": "2025-02-01", "date_to": "2025-02-01", "university_id": "timiryazev"}, min_reviews=1)[0]
    assert item["eligible_reviews"] == 3 and item["positive_percent"] == pytest.approx(200 / 3)
    # Explicit timestamps still select exact instants, not local calendar days.
    same_instant = list_reviews(database, {"date_from": "2025-01-31T21:00:00Z", "date_to": "2025-01-31T21:00:00Z"})
    assert same_instant["total"] == 2
    assert all(item["published_at"] == "2025-01-31T21:00:00Z" for item in same_instant["items"])
    detail = university_detail(database, "timiryazev")
    assert [(point["month"], point["total"]) for point in detail["dynamics"]] == [("2025-01", 1), ("2025-02", 4)]
    assert methodology(database)["timezone"] == "Europe/Moscow"


def test_calendar_uses_historical_moscow_offset(database, tmp_path):
    # Moscow used UTC+04 in January 2012: a fixed modern UTC+03 offset is wrong.
    import_records(database, tmp_path, [record("Исторический отзыв в начале февраля", published_at="2012-01-31T20:00:00Z", sentiment="positive")])
    assert list_reviews(database, {"date_from": "2012-02-01", "date_to": "2012-02-01"})["total"] == 1
    assert list_reviews(database, {"date_to": "2012-01-31"})["total"] == 0
    assert university_detail(database, "timiryazev")["dynamics"][0]["month"] == "2012-02"


def test_hidden_text_and_snippets_do_not_leak_even_via_search(database, tmp_path):
    import_records(database, tmp_path, [record("Преподаватели прекрасные секретнаяфраза", sentiment="positive",
                    display_allowed=False,
                    aspects=[{"aspect": "teaching", "sentiment": "positive", "context": "Преподаватели прекрасные"}])])
    review = list_reviews(database)["items"][0]
    assert review["text"] is None and review["aspects"][0]["context"] is None
    assert list_reviews(database, {"q": "секретнаяфраза"})["total"] == 0
    detail = university_detail(database, "timiryazev")
    assert detail["topics"][0]["evidence"][0]["text"] is None


def test_low_confidence_excluded_until_audit_and_original_score_ignored(database, tmp_path):
    import_records(database, tmp_path, [record(original_score=1)])
    item = ranking(database, {"university_id": "timiryazev"}, min_reviews=1)[0]
    assert item["eligible_reviews"] == 0 and item["rank"] is None
    moderate_review(database, 1, "positive", actor="tester", note="Checked original")
    item = ranking(database, {"university_id": "timiryazev"}, min_reviews=1)[0]
    assert item["positive"] == 1 and item["eligible_reviews"] == 1 and item["rank"] == 1
    assert list_reviews(database)["items"][0]["needs_review"] is False
    with connect(database) as connection:
        row = connection.execute("SELECT * FROM review_audit WHERE action='approve'").fetchone()
        assert row["actor"] == "tester" and json.loads(row["after_json"])["approved"] is True


def test_manual_sentiment_does_not_approve_rule_aspects(database, tmp_path):
    import_records(database, tmp_path, [record(f"Хорошие преподаватели {index}", sentiment="positive") for index in range(3)])
    assert university_detail(database, "timiryazev")["topics"] == []
    for index in range(1, 4):
        moderate_review(database, index, "positive", aspects=[
            {"aspect": "teaching", "sentiment": "positive", "context": "Хорошие преподаватели"}])
    item = ranking(database, {"university_id": "timiryazev"}, min_reviews=1)[0]
    assert item["strengths"][0]["count"] == 3
    assert {row["review_id"] for row in item["strengths"][0]["evidence"]} == {1, 2, 3}


def test_smoothing_global_baseline_precedes_filters_and_detail_keeps_rank(database, tmp_path):
    records = [record(f"Отличные преподаватели {index}", sentiment="positive") for index in range(10)]
    records += [record(f"Ужасное общежитие {index}", "kubsau", sentiment="negative") for index in range(10)]
    import_records(database, tmp_path, records)
    unfiltered = ranking(database)
    timiryazev = next(item for item in unfiltered if item["id"] == "timiryazev")
    assert timiryazev["positive_percent"] == 100
    assert timiryazev["smoothed_score"] == pytest.approx(75)
    filtered = ranking(database, {"region": "Москва"})[0]
    assert filtered["smoothed_score"] == timiryazev["smoothed_score"]
    assert filtered["baseline_positive_fraction"] == 0.5
    assert university_detail(database, "kubsau")["university"]["rank"] == 2
    assert university_detail(database, "unknown") is None


def test_small_samples_neutral_denominator_and_dynamic_methodology(database, tmp_path):
    import_records(database, tmp_path, [record("Обычные занятия", sentiment="neutral"),
                                      record("Хорошие преподаватели", sentiment="positive")])
    item = ranking(database, {"university_id": "timiryazev"})[0]
    assert item["positive_percent"] == 50
    assert item["smoothed_score"] is None and item["rank"] is None and not item["eligible"]
    method = methodology(database, min_reviews=2, smoothing=5)
    assert method["min_reviews"] == 2 and "5 ×" in method["score_formula"]
    assert method["stats"]["approved"] == 2 and method["sources"][0]["review_count"] == 2
    assert method["last_updated"] == "2025-03-01T00:00:00Z"


def test_reclassifying_preserves_manual_audit_and_aspects(database, tmp_path):
    import_records(database, tmp_path, [record()])
    moderate_review(database, 1, "negative", aspects=[
        {"aspect": "teaching", "sentiment": "negative", "context": "Отличные преподаватели"}], actor="tester")
    stats = reclassify_reviews(database, Analyzer())
    assert stats["manual_preserved"] == 1
    review = list_reviews(database)["items"][0]
    assert review["sentiment"] == "negative" and review["approved"]
    assert review["aspects"][0]["sentiment"] == "negative" and review["aspects"][0]["approved"]
    assert review["label_origin"] == "manual" and review["confidence"] is None
    assert review["prediction"]["sentiment"] == "positive"


def test_cli_import_option_and_moderation_queue(database, tmp_path, capsys):
    from baknin.cli import main
    path = write_jsonl(tmp_path, [record()])
    assert main(["import", "--db", str(database), "--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["pending_moderation"] == 1
    assert main(["--db", str(database), "audit", "1", "--sentiment", "positive", "--actor", "tester"]) == 0
    assert json.loads(capsys.readouterr().out)["approved"] is True


def test_cli_separate_pending_aspect_queue_is_safe_and_actionable(database, tmp_path, capsys):
    from baknin.cli import main

    import_records(database, tmp_path, [record("Хорошие преподаватели", sentiment="positive", display_allowed=False),
                                      record("Плохое общежитие")])
    assert main(["audit", "--db", str(database)]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == 2
    assert main(["audit", "--db", str(database), "--pending-aspects"]) == 0
    queued = json.loads(capsys.readouterr().out)
    assert queued["total"] == 1 and queued["items"][0]["id"] == 1
    assert queued["items"][0]["text"] is None and queued["items"][0]["aspects"][0]["context"] is None
    moderate_review(database, 1, "positive", aspects=[
        {"aspect": "teaching", "sentiment": "positive", "context": "Хорошие преподаватели"}])
    assert main(["audit", "--db", str(database), "--pending-aspects"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
