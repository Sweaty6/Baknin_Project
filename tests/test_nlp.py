"""Invented test fixtures ONLY: none of these examples is a production corpus."""

import json

import pytest

from baknin.nlp import ASPECT_LABELS, Analyzer, normalize_text
from model_training import load_dataset, select_confidence_threshold, train


def test_normalization_retains_negations_and_is_deterministic():
    assert normalize_text("  НЕ хорошее! Ни плохое. Без ёлок. ") == "не хорошее ни плохое без елок"
    assert normalize_text(normalize_text("Преподаватели НЕ хорошие")) == "преподаватели не хорошие"


@pytest.mark.parametrize("text,sentiment", [
    ("Преподаватели хорошие", "positive"),
    ("Преподаватели не хорошие", "negative"),
    ("Общежитие плохое", "negative"),
    ("Общежитие не плохое", "positive"),
    ("Преподаватели не очень хорошие", "negative"),
    ("Преподаватели не грубые и добрые", "positive"),
    ("Преподаватели не хорошие и не добрые", "negative"),
    ("Ничего знакомого: квазибублик шурумбурум", "neutral"),
])
def test_transparent_rules_and_negation_scope(text, sentiment):
    result = Analyzer().analyze(text)
    assert result["sentiment"] == sentiment
    assert result["engine"] == "rules"
    assert result["confidence"] is None
    assert result["needs_review"] is True
    assert all(aspect["confidence"] is None for aspect in result["aspects"])


def test_contrast_preserves_both_aspects_and_evidence():
    result = Analyzer().analyze("Отличные преподаватели, но общежитие плохое.")
    aspects = {aspect["id"]: aspect for aspect in result["aspects"]}
    assert result["mixed"] is True
    assert result["needs_review"] is True
    assert aspects["teaching"]["sentiment"] == "positive"
    assert aspects["dormitory"]["sentiment"] == "negative"
    assert aspects["teaching"]["evidence"] == ["Отличные преподаватели"]
    assert aspects["dormitory"]["evidence"] == ["общежитие плохое"]


def test_aspect_polarity_uses_local_adjacent_words():
    result = Analyzer().analyze("Хорошие преподаватели и плохое общежитие")
    aspects = {aspect["id"]: aspect["sentiment"] for aspect in result["aspects"]}
    assert aspects == {"teaching": "positive", "dormitory": "negative"}


def test_evidence_preserves_original_unicode_source_text():
    text = "Отличные\u00a0преподаватели, но общежитие плохое."
    result = Analyzer().analyze(text)
    for aspect in result["aspects"]:
        assert all(quote in text for quote in aspect["evidence"])
    assert result["aspects"][0]["evidence"] == ["Отличные\u00a0преподаватели"]


def test_all_seven_aspects_can_coexist():
    result = Analyzer().analyze(
        "Лекции интересные. Практика полезная. Общежитие чистое. "
        "Библиотека современная. Деканат отзывчивый. Студсовет активный. Стоимость дорогая."
    )
    assert {aspect["id"] for aspect in result["aspects"]} == set(ASPECT_LABELS)


def test_professional_teacher_is_not_a_career_mention():
    result = Analyzer().analyze("Профессиональные преподаватели работают хорошо")
    assert {aspect["id"] for aspect in result["aspects"]} == {"teaching"}


def test_empty_and_unknown_text_need_review():
    for text in ("", "...", "квазибублик шурумбурум"):
        result = Analyzer().analyze(text)
        assert result["sentiment"] == "neutral"
        assert result["needs_review"] is True
        assert result["confidence"] is None


def test_artifact_load_is_explicit_and_local():
    analyzer = Analyzer("https://example.org/arbitrary-model.joblib")
    assert analyzer.model is None
    assert "локальным" in analyzer.load_error
    assert analyzer.analyze("Отличные преподаватели")["engine"] == "rules"


def fixture_dataset(tmp_path):
    """Small invented examples exercise plumbing, NEVER substantiate quality."""
    fixtures = {
        "train": [
            ("Хорошие преподаватели помогают студентам", "positive"),
            ("Хорошее общежитие нравится студентам", "positive"),
            ("Отличные лекции и полезная практика", "positive"),
            ("Плохие преподаватели грубые", "negative"),
            ("Плохое общежитие грязное", "negative"),
            ("Ужасные лекции скучные", "negative"),
            ("Лекции начинаются в девять", "neutral"),
            ("Общежитие находится на улице мира", "neutral"),
            ("Преподаватели ведут два предмета", "neutral"),
        ],
        "validation": [
            ("Хорошие преподаватели помогают поступающим", "positive"),
            ("Плохие преподаватели грубые с поступающими", "negative"),
            ("Лекции начинаются в восемь", "neutral"),
        ],
        "test": [
            ("Хорошие преподаватели помогают выпускникам", "positive"),
            ("Плохое общежитие очень грязное", "negative"),
            ("Лекции начинаются в десять", "neutral"),
        ],
    }
    rows = []
    for split, samples in fixtures.items():
        for index, (text, sentiment) in enumerate(samples):
            row = {
                "text": text, "sentiment": sentiment, "split": split,
                "source_url": f"https://example.org/test-fixtures/{split}/{index}",
            }
            if split == "test":
                row["aspects"] = [{"id": "dormitory" if "общежитие" in text else "teaching", "sentiment": sentiment}]
            rows.append(row)
    path = tmp_path / "test-fixtures.jsonl"
    write_dataset(path, rows)
    return path, rows


def write_dataset(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_dataset_rejects_missing_provenance(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    rows[0].pop("source_url")
    write_dataset(path, rows)
    with pytest.raises(ValueError, match="source_url"):
        load_dataset(path)


def test_dataset_rejects_missing_explicit_split(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    rows[0].pop("split")
    write_dataset(path, rows)
    with pytest.raises(ValueError, match="split"):
        load_dataset(path)


def test_normalized_duplicate_cannot_leak_into_test(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    rows[-1]["text"] = rows[0]["text"].upper() + "!!!"
    write_dataset(path, rows)
    with pytest.raises(ValueError, match="дубликат между выборками"):
        load_dataset(path)


def test_training_requires_three_test_classes(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    write_dataset(path, [row for row in rows if not (row["split"] == "test" and row["sentiment"] == "neutral")])
    with pytest.raises(ValueError, match="test отсутствуют классы"):
        load_dataset(path)


def test_training_rejects_non_russian_corpus(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    rows[0]["text"] = "This movie is excellent"
    write_dataset(path, rows)
    with pytest.raises(ValueError, match="кириллицей"):
        load_dataset(path)


def test_same_review_source_cannot_leak_across_splits(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    rows[-1]["source_url"] = rows[0]["source_url"]
    write_dataset(path, rows)
    with pytest.raises(ValueError, match="один источник"):
        load_dataset(path)


def test_different_review_ids_can_share_an_aggregate_source_page(tmp_path):
    path, rows = fixture_dataset(tmp_path)
    for index, row in enumerate(rows):
        row["source_url"] = "https://example.org/test-fixtures/reviews"
        row["source_review_id"] = f"fixture-{index}"
    write_dataset(path, rows)
    splits, _ = load_dataset(path)
    assert len(splits["test"]) == 3


def test_bad_validation_abstains_instead_of_claiming_confidence():
    selected = select_confidence_threshold(["positive", "negative"], ["negative", "positive"], [0.92, 0.99])
    assert selected["threshold"] == 1.0
    assert selected["review_all"] is True


def test_training_reports_real_split_metrics_and_verifies_artifact(tmp_path):
    dataset, _ = fixture_dataset(tmp_path)
    model = tmp_path / "model.joblib"
    report_path = tmp_path / "metrics.json"
    report = train(dataset, model, report_path)
    assert report["dataset"]["train"]["size"] == 9
    assert report["test"]["confusion_matrix_labels"] == ["positive", "negative", "neutral"]
    assert len(report["test"]["confusion_matrix"]) == 3
    assert "macro_f1" in report["dummy_baseline_test"]
    assert report["aspects_test"]["evaluated"] is True
    assert 0 <= report["aspects_test"]["extraction_micro_f1"] <= 1
    assert report["warnings"]
    analyzer = Analyzer(model)
    assert analyzer.load_error is None
    result = analyzer.analyze("Хорошие преподаватели помогают друзьям")
    assert result["engine"] == "tfidf_logistic"
    assert isinstance(result["confidence"], float)
    assert result["confidence_threshold"] >= 0.65
    assert result["model_version"] == report["metadata"]["model_version"]
    assert analyzer.analyze("шурумбурум квазибублик")["sentiment"] == "neutral"
    assert analyzer.analyze("шурумбурум квазибублик")["needs_review"] is True
    assert analyzer.analyze("шурумбурум квазибублик")["confidence"] is None
    # Integrity is checked before deserialization.
    model.write_bytes(model.read_bytes() + b"corruption")
    corrupt = Analyzer(model)
    assert corrupt.model is None
    assert "Контрольная сумма" in corrupt.load_error
    assert corrupt.analyze("Хорошие преподаватели")["needs_review"] is True


def test_training_cannot_overwrite_its_dataset(tmp_path):
    dataset, _ = fixture_dataset(tmp_path)
    original = dataset.read_bytes()
    with pytest.raises(ValueError):
        train(dataset, dataset, tmp_path / "report.json")
    assert dataset.read_bytes() == original
