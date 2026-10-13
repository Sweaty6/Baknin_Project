"""Train from a real, attributed JSONL corpus; never synthesize training reviews.

Example invocation:
    python model_training.py --input labeled.jsonl --output instance/model.joblib \
        --report instance/metrics.json

Every row requires text, sentiment, source_url and an explicit split:
train / validation / test. Optional aspects is a list of {id, sentiment} objects.
The resulting joblib file is executable pickle data; load only trusted artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import tempfile
from urllib.parse import urlparse

import joblib
import numpy as np
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MultiLabelBinarizer

from baknin.nlp import ARTIFACT_FORMAT, ASPECT_LABELS, Analyzer, RULES_VERSION, SENTIMENTS, normalize_text


SPLITS = ("train", "validation", "test")
TARGET_REVIEW_ACCURACY = 0.85


def load_dataset(path):
    """Reject ambiguous labels, missing provenance and split leakage before fitting."""
    raw = Path(path).read_bytes()
    rows = []
    seen = {}
    seen_sources = {}
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Строка {line_number}: некорректный JSON") from error
        if not isinstance(row, dict):
            raise ValueError(f"Строка {line_number}: ожидается JSON-объект")
        if not isinstance(row.get("text"), str) or not normalize_text(row["text"]):
            raise ValueError(f"Строка {line_number}: требуется непустой text")
        if not re.search(r"[а-яё]", row["text"], re.IGNORECASE):
            raise ValueError(f"Строка {line_number}: требуется русскоязычный текст с кириллицей")
        if row.get("sentiment") not in SENTIMENTS:
            raise ValueError(f"Строка {line_number}: sentiment должен быть positive, negative или neutral")
        if row.get("split") not in SPLITS:
            raise ValueError(f"Строка {line_number}: split должен быть train, validation или test")
        source = row.get("source_url")
        if not isinstance(source, str):
            raise ValueError(f"Строка {line_number}: требуется source_url для происхождения примера")
        parsed = urlparse(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError(f"Строка {line_number}: source_url должен быть публичным HTTP(S) URL без учетных данных")
        source_review_id = row.get("source_review_id", "")
        if not isinstance(source_review_id, str) or len(source_review_id) > 200 or "\n" in source_review_id:
            raise ValueError(f"Строка {line_number}: source_review_id должен быть строкой до 200 символов")
        identity = (source, source_review_id)
        if identity in seen_sources and seen_sources[identity][1] != row["split"]:
            previous_line, _ = seen_sources[identity]
            raise ValueError(
                f"Строки {previous_line} и {line_number}: один источник отзыва попал в разные выборки; "
                "для разных отзывов на одной странице укажите разные source_review_id"
            )
        seen_sources[identity] = (line_number, row["split"])
        normalized = normalize_text(row["text"])
        if normalized in seen:
            previous_line, previous_split = seen[normalized]
            scope = "между выборками" if previous_split != row["split"] else "внутри выборки"
            raise ValueError(f"Строки {previous_line} и {line_number}: нормализованный дубликат {scope}")
        seen[normalized] = (line_number, row["split"])
        if "aspects" in row:
            if not isinstance(row["aspects"], list):
                raise ValueError(f"Строка {line_number}: aspects должен быть списком")
            aspect_ids = set()
            for aspect in row["aspects"]:
                if not isinstance(aspect, dict) or aspect.get("id") not in ASPECT_LABELS or aspect.get("sentiment") not in SENTIMENTS:
                    raise ValueError(f"Строка {line_number}: некорректная разметка аспекта")
                if aspect["id"] in aspect_ids:
                    raise ValueError(f"Строка {line_number}: повторный аспект {aspect['id']}")
                aspect_ids.add(aspect["id"])
        rows.append(row)
    if not rows:
        raise ValueError("Обучающий файл пуст")
    splits = {split: [row for row in rows if row["split"] == split] for split in SPLITS}
    for split, records in splits.items():
        if not records:
            raise ValueError(f"Выборка {split} пуста: автоматическое разбиение не выполняется")
    for split in ("train", "test"):
        absent = set(SENTIMENTS) - {row["sentiment"] for row in splits[split]}
        if absent:
            raise ValueError(f"В выборке {split} отсутствуют классы: {', '.join(sorted(absent))}")
    return splits, hashlib.sha256(raw).hexdigest()


def _metrics(expected, predicted):
    return {
        "accuracy": float(accuracy_score(expected, predicted)),
        "macro_f1": float(f1_score(expected, predicted, labels=list(SENTIMENTS), average="macro", zero_division=0)),
        "per_class": classification_report(expected, predicted, labels=list(SENTIMENTS), output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(expected, predicted, labels=list(SENTIMENTS)).tolist(),
        "confusion_matrix_labels": list(SENTIMENTS),
    }


def select_confidence_threshold(labels, predicted, scores):
    """Select using validation only; no claim of probability calibration.

    Maximize validation coverage subject to observed selective accuracy >= 85%.
    With no acceptable threshold, abstain on all future predictions. Small sample
    sizes are reported, not disguised as a reliable operating guarantee.
    """
    labels = np.asarray(labels)
    predicted = np.asarray(predicted)
    scores = np.asarray(scores)
    candidates = []
    for threshold in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99):
        accepted = scores >= threshold
        count = int(accepted.sum())
        accuracy = float(np.mean(labels[accepted] == predicted[accepted])) if count else None
        candidates.append({
            "threshold": threshold,
            "accepted": count,
            "coverage": count / len(labels),
            "selective_accuracy": accuracy,
        })
    valid = [row for row in candidates if row["accepted"] and row["selective_accuracy"] >= TARGET_REVIEW_ACCURACY]
    best = max(valid, key=lambda row: (row["coverage"], -row["threshold"])) if valid else None
    return {
        "threshold": best["threshold"] if best else 1.0,
        "review_all": best is None,
        "target_selective_accuracy": TARGET_REVIEW_ACCURACY,
        "effective_default_threshold": max(0.65, best["threshold"]) if best else 1.0,
        "validation_size": len(labels),
        "selected": best,
        "candidates": candidates,
        "method": "Максимальное покрытие validation при наблюдаемой точности принятых решений не ниже 0.85; test для выбора порога не используется. При загрузке Analyzer применяет max(порог конфигурации, выбранный порог); стандартный порог конфигурации 0.65.",
        "limitations": "Оценка логистической регрессии не калибрована. Малая validation не доказывает надежность порога на новых отзывах.",
    }


def _aspect_metrics(rows):
    annotated = [row for row in rows if "aspects" in row]
    if not annotated:
        return {
            "evaluated": False,
            "reason": "В test нет разметки aspects; качество аспектных правил не измерено.",
        }
    analyzer = Analyzer()
    expected = []
    predicted = []
    true_pairs = []
    predicted_pairs = []
    for row in annotated:
        actual = analyzer.analyze(row["text"])["aspects"]
        expected.append([aspect["id"] for aspect in row["aspects"]])
        predicted.append([aspect["id"] for aspect in actual])
        true_pairs.append([f"{aspect['id']}:{aspect['sentiment']}" for aspect in row["aspects"]])
        predicted_pairs.append([f"{aspect['id']}:{aspect['sentiment']}" for aspect in actual])
    binarizer = MultiLabelBinarizer(classes=list(ASPECT_LABELS))
    expected_matrix = binarizer.fit_transform(expected)
    predicted_matrix = binarizer.transform(predicted)
    pair_binarizer = MultiLabelBinarizer(classes=[f"{aspect}:{sentiment}" for aspect in ASPECT_LABELS for sentiment in SENTIMENTS])
    expected_pairs = pair_binarizer.fit_transform(true_pairs)
    actual_pairs = pair_binarizer.transform(predicted_pairs)
    return {
        "evaluated": True,
        "examples": len(annotated),
        "engine": "rules",
        "extraction_micro_f1": float(f1_score(expected_matrix, predicted_matrix, average="micro", zero_division=0)),
        "extraction_macro_f1": float(f1_score(expected_matrix, predicted_matrix, average="macro", zero_division=0)),
        "sentiment_micro_f1": float(f1_score(expected_pairs, actual_pairs, average="micro", zero_division=0)),
        "sentiment_macro_f1": float(f1_score(expected_pairs, actual_pairs, average="macro", zero_division=0)),
        "per_aspect": classification_report(expected_matrix, predicted_matrix, target_names=list(ASPECT_LABELS), output_dict=True, zero_division=0),
        "macro_note": "Macro усредняет все 7 аспектов (и все 21 пары аспект/тональность); отсутствующие классы получают F1=0.",
    }


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".baknin-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def train(input_path, output_path, report_path):
    output = Path(output_path)
    report_path = Path(report_path)
    input_path = Path(input_path)
    if output.suffix != ".joblib":
        raise ValueError("Файл модели должен иметь расширение .joblib")
    protected = {input_path.resolve(), output.resolve(), Path(str(output) + ".json").resolve(), report_path.resolve()}
    if len(protected) != 4:
        raise ValueError("Датасет, модель, метаданные и отчет должны иметь разные пути")
    splits, dataset_sha256 = load_dataset(input_path)
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(preprocessor=normalize_text, lowercase=False, ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b\w+\b")),
        ("classifier", LogisticRegression(max_iter=1500, class_weight="balanced", random_state=42)),
    ])
    train_texts = [row["text"] for row in splits["train"]]
    train_labels = [row["sentiment"] for row in splits["train"]]
    pipeline.fit(train_texts, train_labels)
    validation_texts = [row["text"] for row in splits["validation"]]
    validation_labels = [row["sentiment"] for row in splits["validation"]]
    validation_probabilities = pipeline.predict_proba(validation_texts)
    validation_predictions = pipeline.classes_[validation_probabilities.argmax(axis=1)]
    threshold_report = select_confidence_threshold(validation_labels, validation_predictions, validation_probabilities.max(axis=1))
    test_texts = [row["text"] for row in splits["test"]]
    test_labels = [row["sentiment"] for row in splits["test"]]
    test_predictions = pipeline.predict(test_texts)
    baseline = DummyClassifier(strategy="most_frequent", random_state=42)
    baseline.fit(np.zeros((len(train_labels), 1)), train_labels)
    baseline_predictions = baseline.predict(np.zeros((len(test_labels), 1)))
    metadata = {
        "format": ARTIFACT_FORMAT,
        "model_version": f"tfidf-logistic-{dataset_sha256[:12]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": dataset_sha256,
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "joblib_version": joblib.__version__,
        "confidence_threshold": threshold_report["threshold"],
        "review_all": threshold_report["review_all"],
        "classes": list(SENTIMENTS),
        "normalization": "baknin.nlp.normalize_text-v1; negations retained",
        "aspect_rules_version": RULES_VERSION,
    }
    warnings = []
    for split, rows in splits.items():
        if len(rows) < 30:
            warnings.append(f"В {split} только {len(rows)} примеров: метрики недостаточны для вывода о рабочем качестве.")
    if set(validation_labels) != set(SENTIMENTS):
        warnings.append("В validation представлены не все три класса; оценка порога ограничена.")
    report = {
        "metadata": metadata,
        "dataset": {split: {"size": len(rows), "classes": dict(Counter(row["sentiment"] for row in rows))} for split, rows in splits.items()},
        "validation": _metrics(validation_labels, validation_predictions),
        "test": _metrics(test_labels, test_predictions),
        "dummy_baseline_test": _metrics(test_labels, baseline_predictions),
        "confidence_selection": threshold_report,
        "aspects_test": _aspect_metrics(splits["test"]),
        "warnings": warnings,
        "method": "TF-IDF обучается только на train; threshold выбирается только на validation; test используется только для отчета. Источник каждого примера указан в source_url; доступность и права на источник следует проверить при сборе корпуса.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".baknin-model-", suffix=".joblib", delete=False) as handle:
            temporary = Path(handle.name)
        joblib.dump({"format": ARTIFACT_FORMAT, "metadata": metadata, "pipeline": pipeline}, temporary, compress=3)
        manifest = dict(metadata, artifact_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest())
        # A running loader sees a checksum mismatch during replacement rather
        # than accidentally loading an incomplete model. Each file is atomic.
        os.replace(temporary, output)
        _atomic_json(Path(str(output) + ".json"), manifest)
        _atomic_json(report_path, report)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description="Обучить модель на размеченном реальном JSONL-корпусе")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = train(args.input, args.output, args.report)
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(2, f"Ошибка обучения: {error}\n")
    print(f"Модель: {args.output}")
    print(f"Метаданные и SHA-256: {args.output}.json")
    print(f"Отчет: {args.report}; test macro-F1={report['test']['macro_f1']:.4f}")
    for warning in report["warnings"]:
        print(f"Предупреждение: {warning}")


if __name__ == "__main__":
    main()
