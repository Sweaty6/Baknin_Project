"""Browser/Python rule parity; all review examples here are invented test fixtures."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from baknin.nlp import (
    ASPECT_LABELS,
    Analyzer,
    RULES_VERSION,
    _ASPECT_REGEX,
    _NEGATIVE,
    _POSITIVE,
    _STOPWORDS,
)


ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "version": RULES_VERSION,
    "aspect_labels": ASPECT_LABELS,
    "aspect_patterns": {name: pattern.pattern for name, pattern in _ASPECT_REGEX.items()},
    "positive_pattern": _POSITIVE.pattern,
    "negative_pattern": _NEGATIVE.pattern,
    "stopwords": sorted(_STOPWORDS),
}


def browser_analyze(texts):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the GitHub Pages browser analyzer")
    script = """
require(process.argv[1]);
const payload = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const result = payload.texts.map(text => {
  try { return { result: globalThis.BakninPagesNLP.analyze(text, payload.rules) }; }
  catch (error) { return { error: error.message }; }
});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(ROOT / "static" / "pages-nlp.js")],
        input=json.dumps({"texts": texts, "rules": RULES}, ensure_ascii=False),
        text=True, capture_output=True, check=True, timeout=15,
    )
    return json.loads(completed.stdout)


FIXTURES = [
    "Преподаватели хорошие",
    "Преподаватели не хорошие",
    "Общежитие плохое",
    "Общежитие не плохое",
    "Преподаватели не очень хорошие",
    "Преподаватели не грубые и добрые",
    "Преподаватели не хорошие и не добрые",
    "Преподаватели не только хорошие, но и отзывчивые",
    "Преподаватели не не хорошие",
    "Нет хороших преподавателей",
    "Без плохих преподавателей",
    "Не очень хорошие преподаватели и плохое общежитие",
    "Отличные преподаватели, но общежитие плохое.",
    "Хорошие преподаватели и плохое общежитие",
    "Отличные\u00a0преподаватели, но общежитие плохое.",
    "Хорошие лекции и практика",
    "Хорошие лекции и ужасная практика",
    "Практика и лекции хорошие",
    "Лекции интересные. Практика полезная. Общежитие чистое. Библиотека современная. "
    "Деканат отзывчивый. Студсовет активный. Стоимость дорогая.",
    "Профессиональные преподаватели работают хорошо",
    "— Хорошие преподаватели –; однако общежитие грязное! Зато столовая чистая?",
    "но лекции интересные однако общежитие грязное зато цена доступная",
    "Лекции отличные\nЛекции отличные\nЛекции ужасные\nЛекции скучные\nЛекции хорошие\nЛекции прекрасные",
    "Преподователь хорошый, практика хорошая",  # Typos stay unknown, as in Python.
    "Квазибублик шурумбурум",
    "Ёлки ёжики и не плохая стипендия",
    "WiFi современный, компьютеры сломанные, столовая чистая",
    "Не слишком плохое общежитие и не очень дорогая стоимость",
    "Учитель поддерживает студентов, работодатель предлагает стажировку",
    "Оплата дешёвая, денег нет, финансовые проблемы",
    "Ректор хороший, спортзал удобный, клуб интересный",
    "İstanbul ıstanbul прекрасные преподаватели",
    "\u00a0Отличные преподаватели\u00a0",
    "\u0085Отличные преподаватели\u0085",
    "Хорошие преподаватели\u0085но\u0085общежитие плохое",
]


def test_browser_rules_match_python_analyzer_for_russian_fixtures():
    browser_results = browser_analyze(FIXTURES)
    for text, entry in zip(FIXTURES, browser_results, strict=True):
        assert "error" not in entry, (text, entry)
        result = entry["result"]
        python_result = Analyzer().analyze(text.strip())
        for key in ("sentiment", "mixed", "aspects", "keywords"):
            assert result[key] == python_result[key], (text, key, result[key], python_result[key])
        assert result["confidence"] is None
        assert result["confidence_threshold"] is None
        assert result["needs_review"] is True
        assert result["engine"] == "browser_rules"
        assert result["model_version"] == RULES_VERSION + "-browser"
        assert "локально в браузере" in result["explanation"]
        assert "Обученная модель не подключена" in result["explanation"]
        assert all(quote in text for row in result["aspects"] for quote in row["evidence"])


@pytest.mark.parametrize("text,message", [
    (None, "Поле text должно быть строкой"),
    (42, "Поле text должно быть строкой"),
    ({"text": "Отличный"}, "Поле text должно быть строкой"),
    ("", "Введите от 1 до 5000 символов"),
    ("  \u00a0\t\n", "Введите от 1 до 5000 символов"),
    ("я" * 5001, "Введите от 1 до 5000 символов"),
    ("...", "Введите отзыв на русском языке"),
    ("Excellent university", "Введите отзыв на русском языке"),
])
def test_browser_predict_validation_matches_api_errors(text, message):
    assert browser_analyze([text]) == [{"error": message}]


def test_browser_length_limit_counts_unicode_codepoints_like_python():
    text = "я" + "😀" * 4999
    assert "result" in browser_analyze([text])[0]
    assert browser_analyze([text + "😀"])[0]["error"] == "Введите от 1 до 5000 символов"
