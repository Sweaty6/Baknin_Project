"""Russian review analysis with an honest rules fallback and optional local model.

Joblib contains pickle data. Only configure artifacts produced locally by a trusted
trainer; a checksum detects corruption, it does not establish who made a file.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import pickle
import re
import unicodedata


SENTIMENTS = ("positive", "negative", "neutral")
RULES_VERSION = "ru-aspect-rules-1"
ARTIFACT_FORMAT = "baknin-tfidf-logistic-v1"
ASPECT_LABELS = {
    "teaching": "Преподавание и учебный процесс",
    "career": "Практика и трудоустройство",
    "dormitory": "Общежитие",
    "infrastructure": "Инфраструктура",
    "administration": "Администрация",
    "student_life": "Студенческая жизнь",
    "finance": "Стоимость и финансы",
}

# Prefixes cover ordinary Russian inflections without changing negation tokens.
_ASPECT_PATTERNS = {
    "teaching": r"преподавател\w*|педагог\w*|учител\w*|профессор\w*|лекци\w*|семинар\w*|учеб\w*|обучен\w*|образован\w*|заняти\w*|экзамен\w*|зачет\w*|предмет\w*|дисциплин\w*|курсов\w*|диплом\w*",
    "career": r"практик\w*|стажиров\w*|трудоустрой\w*|карьер\w*|работодател\w*|ваканси\w*|работ(?:а|у|ы|е|ой|ами|ах|ам)|професси[яиюйе]\w*",
    "dormitory": r"общежити\w*|общаг\w*|заселен\w*|комендант\w*",
    "infrastructure": r"корпус\w*|аудитори\w*|библиотек\w*|лаборатори\w*|оборудован\w*|компьютер\w*|интернет\w*|wifi|wi\s?fi|спортзал\w*|столов\w*|кампус\w*|здани\w*|инфраструктур\w*|ремонт\w*",
    "administration": r"администраци\w*|декан\w*|ректо[рв]\w*|канцеляри\w*|бюрократ\w*|документ\w*|справк\w*|приемн\w*|расписани\w*|руководств\w*",
    "student_life": r"студсовет\w*|студенческ\w*|клуб\w*|круж[окк]\w*|мероприяти\w*|концерт\w*|фестивал\w*|волонтер\w*|секци\w*|спорт\w*|досуг\w*",
    "finance": r"стоимост\w*|оплат\w*|платн\w*|цен[ауы]\w*|цене|ценой|стипенди\w*|бюджет\w*|денег|деньг\w*|финанс\w*|дорог\w*|дешев\w*",
}
_ASPECT_REGEX = {
    name: re.compile(r"^(?:" + pattern + r")$", re.IGNORECASE)
    for name, pattern in _ASPECT_PATTERNS.items()
}
_POSITIVE = re.compile(
    r"^(?:отличн\w*|прекрасн\w*|замечательн\w*|хорош\w*|добр\w*|"
    r"грамотн\w*|талантлив\w*|интересн\w*|удобн\w*|качествен\w*|"
    r"чист\w*|современн\w*|просторн\w*|уютн\w*|полезн\w*|доступн\w*|"
    r"лучш\w*|довол\w*|успеш\w*|дружелюбн\w*|отзывчив\w*|комфортн\w*|"
    r"профессиональн\w*|нравит\w*|нравят\w*|понрав\w*|рекоменд\w*|"
    r"помога\w*|поддержива\w*|дешев\w*)$"
)
_NEGATIVE = re.compile(
    r"^(?:плох\w*|ужас\w*|отврат\w*|кошмар\w*|груб\w*|хам\w*|"
    r"невеж\w*|некомпетент\w*|некачествен\w*|скуч\w*|гряз\w*|"
    r"тесн\w*|аварийн\w*|сломан\w*|холодн\w*|дорог\w*|завыш\w*|"
    r"бюрократ\w*|проблем\w*|разочар\w*|непонят\w*|слаб\w*|"
    r"переполн\w*|неработа\w*|коррупц\w*|ненави\w*|недовол\w*)$"
)
_TOKEN = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_CLAUSES = re.compile(r"[.!?;,\n]+|\s+\b(?:но|однако|зато)\b\s+", re.IGNORECASE)
_STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "было", "в", "во", "все", "всего",
    "для", "до", "его", "ее", "если", "есть", "еще", "же", "за", "и", "из",
    "или", "им", "их", "к", "как", "ко", "когда", "который", "ли", "мне",
    "мой", "мы", "на", "над", "наш", "не", "него", "ней", "нет", "ни", "но",
    "ну", "о", "об", "однако", "он", "она", "они", "от", "очень", "по", "под",
    "при", "про", "с", "со", "так", "там", "то", "только", "тут", "у", "уже",
    "что", "чтобы", "это", "этот", "я",
}


def normalize_text(text: str) -> str:
    """The identical, deterministic normalization used in training and inference.

    Negations (не, ни, нет, без) are retained. No aggressive stop-word filtering
    or stemming is applied to the trainable model.
    """
    if not isinstance(text, str):
        raise TypeError("Текст должен быть строкой")
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return " ".join(_TOKEN.findall(text))


def _polarity(tokens: list[str]) -> list[tuple[int, int]]:
    result = []
    for index, token in enumerate(tokens):
        value = 1 if _POSITIVE.fullmatch(token) else -1 if _NEGATIVE.fullmatch(token) else 0
        if not value:
            continue
        window = tokens[max(0, index - 3):index]
        for position, word in enumerate(window):
            if word in {"и", "а", "но", "или", "однако", "зато"}:
                window = window[position + 1:]
                break
        # "не только хорошие" is not a negation of the adjective.
        negators = sum(
            word in {"не", "ни", "нет", "без"}
            and not (word == "не" and position + 1 < len(window) and window[position + 1] == "только")
            for position, word in enumerate(window)
        )
        if negators % 2:
            value *= -1
        result.append((index, value))
    return result


def _sentiment(values: list[int]) -> tuple[str, bool]:
    positive = any(value > 0 for value in values)
    negative = any(value < 0 for value in values)
    if positive and negative:
        return "neutral", True
    return ("positive" if positive else "negative" if negative else "neutral"), False


def _rule_analysis(text: str) -> dict:
    aspect_data = {}
    global_polarity = []
    detected_keywords = {}
    # Evidence must remain an exact substring of the submitted review. Unicode
    # normalization is applied to features below, never to quoted source text.
    for raw_clause in _CLAUSES.split(text):
        evidence = raw_clause.strip(" \t\r—–-")
        evidence = re.sub(r"^(?:но|однако|зато)\s+", "", evidence, flags=re.IGNORECASE)
        if not evidence:
            continue
        tokens = normalize_text(evidence).split()
        polarities = _polarity(tokens)
        global_polarity.extend(value for _, value in polarities)
        mentions = {
            aspect: [index for index, token in enumerate(tokens) if pattern.fullmatch(token)]
            for aspect, pattern in _ASPECT_REGEX.items()
        }
        mentions = {aspect: positions for aspect, positions in mentions.items() if positions}
        for aspect in mentions:
            row = aspect_data.setdefault(aspect, {"values": [], "evidence": []})
            if evidence not in row["evidence"]:
                row["evidence"].append(evidence)
        # Assign adjectives to their nearest aspect in a clause. Distances tied
        # between aspects are shared, e.g. "хорошие лекции и практика".
        for position, value in polarities:
            distances = {
                aspect: min(abs(position - mention) for mention in positions)
                for aspect, positions in mentions.items()
            }
            if distances:
                nearest = min(distances.values())
                for aspect, distance in distances.items():
                    if distance == nearest:
                        aspect_data[aspect]["values"].append(value)
        important = set(index for positions in mentions.values() for index in positions)
        important.update(index for index, _ in polarities)
        for index, token in enumerate(tokens):
            if len(token) >= 3 and token not in _STOPWORDS:
                detected_keywords[token] = detected_keywords.get(token, 0) + (3 if index in important else 1)
    sentiment, mixed = _sentiment(global_polarity)
    aspects = []
    for aspect, label in ASPECT_LABELS.items():
        if aspect in aspect_data:
            row = aspect_data[aspect]
            local_sentiment, _ = _sentiment(row["values"])
            aspects.append({
                "id": aspect,
                "label": label,
                "sentiment": local_sentiment,
                "confidence": None,
                "evidence": row["evidence"][:5],
            })
    return {
        "sentiment": sentiment,
        "mixed": mixed,
        "has_polarity": bool(global_polarity),
        "aspects": aspects,
        "keywords": sorted(detected_keywords, key=lambda word: (-detected_keywords[word], word))[:10],
    }


class Analyzer:
    """Analyze reviews; no model is searched for or downloaded automatically.

    ``confidence`` is None in the rules fallback. With a trained model it is the
    largest logistic-regression predict_proba score, not a calibrated guarantee.
    Aspect extraction remains rule based and never receives fabricated scores.
    """

    def __init__(self, model_path=None, confidence_threshold=0.65):
        if not isinstance(confidence_threshold, (int, float)) or not 0 <= confidence_threshold <= 1:
            raise ValueError("Порог уверенности должен быть от 0 до 1")
        self.confidence_threshold = float(confidence_threshold)
        self.model = None
        self.metadata = {}
        self.load_error = None
        if model_path is not None:
            try:
                self._load_model(model_path)
            except (OSError, ValueError, ImportError, KeyError, TypeError, AttributeError, EOFError, pickle.UnpicklingError) as error:
                self.load_error = str(error)

    def _load_model(self, model_path):
        raw_path = str(model_path)
        if "://" in raw_path or raw_path.startswith(("//", "\\\\")):
            raise ValueError("Модель должна быть явно указанным доверенным локальным файлом")
        path = Path(raw_path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix != ".joblib":
            raise ValueError("Ожидается локальный файл модели с расширением .joblib")
        manifest_path = Path(str(path) + ".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("format") != ARTIFACT_FORMAT:
            raise ValueError("Неподдерживаемый формат метаданных модели")
        if not isinstance(manifest.get("model_version"), str) or not manifest["model_version"]:
            raise ValueError("В метаданных отсутствует версия модели")
        if path.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("Артефакт модели превышает допустимый размер")
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get("artifact_sha256"):
            raise ValueError("Контрольная сумма модели не совпадает с метаданными")
        import joblib
        import sklearn
        if manifest.get("sklearn_version") != sklearn.__version__:
            raise ValueError("Версия scikit-learn модели отличается от установленной; переобучите модель")
        artifact = joblib.load(path)
        if not isinstance(artifact, dict) or artifact.get("format") != ARTIFACT_FORMAT:
            raise ValueError("Неподдерживаемый формат модели")
        if artifact.get("metadata") != {key: value for key, value in manifest.items() if key != "artifact_sha256"}:
            raise ValueError("Метаданные модели и контрольного файла различаются")
        model = artifact["pipeline"]
        if set(model.classes_) != set(SENTIMENTS):
            raise ValueError("Модель должна поддерживать три класса тональности")
        threshold = manifest.get("confidence_threshold", self.confidence_threshold)
        if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("Некорректный порог уверенности в модели")
        self.confidence_threshold = max(self.confidence_threshold, float(threshold))
        self.model = model
        self.metadata = manifest

    def analyze(self, text: str) -> dict:
        normalized = normalize_text(text)
        rule = _rule_analysis(text)
        result = {key: rule[key] for key in ("sentiment", "mixed", "aspects", "keywords")}
        result.update(
            confidence=None, confidence_threshold=self.confidence_threshold if self.model is not None else None,
            needs_review=True, model_version=RULES_VERSION, engine="rules",
        )
        if not normalized:
            result["explanation"] = "Текст пустой или не содержит слов: требуется проверка."
            return result
        if self.model is not None:
            scores = self.model.predict_proba([normalized])[0]
            index = int(scores.argmax())
            result["sentiment"] = str(self.model.classes_[index])
            result["confidence"] = round(float(scores[index]), 6)
            # Entirely unseen vocabulary cannot support a learned prediction.
            vectorizer = self.model.named_steps["tfidf"]
            unknown = vectorizer.transform([normalized]).nnz == 0
            if unknown:
                result["sentiment"] = "neutral"
                result["confidence"] = None
            result["needs_review"] = bool(
                unknown or rule["mixed"] or self.metadata.get("review_all")
                or scores[index] < self.confidence_threshold
            )
            result["model_version"] = self.metadata["model_version"]
            result["engine"] = "tfidf_logistic"
            result["explanation"] = (
                "Тональность определена TF-IDF и логистической регрессией. Уверенность — оценка модели, "
                "а не калиброванная вероятность. Аспекты и цитаты выделены словарными правилами."
            )
            if unknown:
                result["explanation"] += " В тексте нет знакомых модели признаков: требуется проверка."
        else:
            # Unvalidated heuristics must never automatically feed a ranking.
            result["needs_review"] = True
            result["explanation"] = (
                "Использованы словарные правила с учетом отрицаний; обученная модель не подключена. "
                "Правила не имеют измеренной вероятности, поэтому уверенность не указана."
            )
            if not rule["has_polarity"]:
                result["explanation"] += " Оценочные слова не найдены: нейтральная метка требует проверки."
            if self.load_error:
                result["needs_review"] = True
                result["explanation"] += " Указанный артефакт модели не удалось проверить или загрузить."
        if rule["mixed"]:
            result["explanation"] += " Найдены положительные и отрицательные оценки: требуется проверка."
        return result
