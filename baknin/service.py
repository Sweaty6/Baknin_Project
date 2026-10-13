"""Verified review import, provenance, moderation, and transparent aggregates."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .db import connect
from .nlp import ASPECT_LABELS, normalize_text

SENTIMENTS = {"positive", "neutral", "negative", "uncertain"}
MAX_TEXT_LENGTH = 5_000
MAX_SQL_INTEGER = 2**63 - 1


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _url(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: требуется HTTPS URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name}: допустим только HTTPS URL без учётных данных")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError(f"{name}: недопустимые символы в URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{name}: неверный порт") from exc
    return value.strip()


def _timestamp(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name}: требуется дата ISO 8601")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name}: неверная дата ISO 8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name}: дата выходит за поддерживаемый диапазон") from exc


def _text_hash(text):
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _aspects(aspects, text):
    if aspects is None:
        return []
    if isinstance(aspects, dict):
        aspects = [dict(value, aspect=key) if isinstance(value, dict)
                   else {"aspect": key, "sentiment": value} for key, value in aspects.items()]
    if not isinstance(aspects, list):
        raise ValueError("aspects: требуется список объектов")
    result = {}
    for item in aspects:
        if not isinstance(item, dict):
            raise ValueError("aspects: каждый аспект должен быть объектом")
        aspect = item.get("aspect", item.get("id", item.get("name", item.get("category"))))
        sentiment = item.get("sentiment", "uncertain")
        if not isinstance(aspect, str) or not aspect.strip() or len(aspect) > 80:
            raise ValueError("aspects: неверное имя аспекта")
        if aspect not in ASPECT_LABELS:
            raise ValueError(f"aspects: неизвестный аспект {aspect}")
        if sentiment not in SENTIMENTS:
            raise ValueError("aspects: неверная тональность")
        confidence = item.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                       or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("aspects: confidence должен быть от 0 до 1 или null")
        context = item.get("context", item.get("evidence", ""))
        if isinstance(context, list):
            if any(not isinstance(value, str) or value not in text for value in context):
                raise ValueError("aspects: контекст отсутствует в тексте отзыва")
            context = context[0] if context else ""
        if not isinstance(context, str) or len(context) > MAX_TEXT_LENGTH:
            raise ValueError("aspects: неверный контекст")
        # A snippet is evidence only when it is present in the original review.
        if context and context not in text:
            raise ValueError("aspects: контекст отсутствует в тексте отзыва")
        name = aspect.strip()
        entry = {"aspect": name, "sentiment": sentiment, "confidence": confidence, "context": context}
        if name in result:
            previous = result[name]
            if previous["sentiment"] != sentiment:
                previous["sentiment"] = "uncertain"
                previous["confidence"] = None
            continue
        result[name] = entry
    return list(result.values())


def _analyze(analyzer, text):
    output = analyzer.analyze(text) if hasattr(analyzer, "analyze") else analyzer(text)
    if hasattr(output, "to_dict"):
        output = output.to_dict()
    if not isinstance(output, dict):
        raise ValueError("Анализатор должен возвращать объект")
    sentiment = output.get("sentiment", "uncertain")
    if sentiment not in SENTIMENTS:
        raise ValueError("Анализатор вернул неизвестную тональность")
    confidence = output.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Анализатор вернул неверное confidence")
    # Rules with uncalibrated confidence always need manual review.
    needs_review = bool(output.get("needs_review", confidence is None or confidence < 0.65))
    if confidence is None or confidence < 0.65 or sentiment == "uncertain":
        needs_review = True
    return {**output, "sentiment": sentiment, "confidence": confidence,
            "needs_review": needs_review, "aspects": _aspects(output.get("aspects", []), text),
            "model_version": str(output.get("model_version", output.get("model", "unknown")))}


def _record_prediction(connection, review_id, prediction):
    connection.execute(
        "INSERT INTO predictions(review_id,sentiment,confidence,needs_review,model_version,details_json) "
        "VALUES (?,?,?,?,?,?)",
        (review_id, prediction["sentiment"], prediction["confidence"], int(prediction["needs_review"]),
         prediction["model_version"], _json(prediction)),
    )


def _save_aspects(connection, review_id, aspects, approved=False):
    connection.execute("DELETE FROM review_aspects WHERE review_id=?", (review_id,))
    connection.executemany(
        "INSERT INTO review_aspects(review_id,aspect,sentiment,confidence,context,approved) VALUES (?,?,?,?,?,?)",
        [(review_id, item["aspect"], item["sentiment"], item["confidence"], item["context"], int(approved)) for item in aspects],
    )


def _audit(connection, review_id, action, actor, before, after, note):
    connection.execute(
        "INSERT INTO review_audit(review_id,action,actor,before_json,after_json,note) VALUES (?,?,?,?,?,?)",
        (review_id, action, actor, _json(before), _json(after), note),
    )


def _validate_review(record, known_universities):
    if not isinstance(record, dict):
        raise ValueError("Каждая строка JSONL должна быть объектом")
    university_id = record.get("university_id")
    if university_id not in known_universities:
        raise ValueError(f"Неизвестный university_id: {university_id}")
    text = record.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text: требуется непустой текст до {MAX_TEXT_LENGTH} символов")
    if not normalize_text(text):
        raise ValueError("text: требуется текст с буквами или цифрами")
    if record.get("verified", record.get("manual_verified")) is not True:
        raise ValueError("verified:true обязателен после проверки реального источника человеком")
    if record.get("personal_data_removed") is not True:
        raise ValueError("personal_data_removed:true обязателен")
    if type(record.get("display_allowed")) is not bool:
        raise ValueError("display_allowed: требуется явное true или false")
    source = record.get("source")
    if not isinstance(source, dict):
        raise ValueError("source: требуется объект platform, url, terms_checked_at")
    platform = source.get("platform")
    if not isinstance(platform, str) or not platform.strip() or len(platform) > 120:
        raise ValueError("source.platform: требуется название площадки")
    source_url = _url(record.get("source_url"), "source_url")
    platform_url = _url(source.get("url"), "source.url")
    if urlsplit(source_url).hostname != urlsplit(platform_url).hostname:
        raise ValueError("source_url и source.url должны относиться к одной площадке")
    published_at = _timestamp(record.get("published_at"), "published_at")
    collected_at = _timestamp(record.get("collected_at"), "collected_at")
    if collected_at < published_at:
        raise ValueError("collected_at не может быть раньше published_at")
    terms_checked_at = _timestamp(source.get("terms_checked_at"), "source.terms_checked_at")
    original_score = record.get("original_score")
    if original_score is not None and (isinstance(original_score, bool) or not isinstance(original_score, (float, int))
                                       or not math.isfinite(original_score)):
        raise ValueError("original_score: требуется конечное число или null")
    manual_sentiment = record.get("sentiment")
    if manual_sentiment is not None and manual_sentiment not in {"positive", "neutral", "negative"}:
        raise ValueError("Ручная sentiment должна быть positive, neutral или negative")
    manual_aspects = _aspects(record.get("aspects"), text) if "aspects" in record else None
    if manual_aspects is not None and manual_sentiment is None:
        raise ValueError("Ручные аспекты требуют ручной sentiment")
    source_review_id = record.get("source_review_id")
    if source_review_id is not None and (not isinstance(source_review_id, str) or not source_review_id.strip()
                                         or len(source_review_id) > 200):
        raise ValueError("source_review_id: требуется непустая строка до 200 символов или null")
    return {"university_id": university_id, "text": text.strip(), "text_hash": _text_hash(text),
            "source_url": source_url, "platform": platform.strip(), "platform_url": platform_url,
            "terms_checked_at": terms_checked_at, "published_at": published_at, "collected_at": collected_at,
            "original_score": original_score, "display_allowed": record["display_allowed"],
            "manual_sentiment": manual_sentiment, "manual_aspects": manual_aspects,
            "source_review_id": source_review_id.strip() if source_review_id is not None else None}


def import_reviews(db_path, input_path, analyzer):
    """Atomically import verified JSONL. Any invalid line rolls back every line."""
    statistics = {"imported": 0, "duplicates": 0, "pending_moderation": 0}
    with connect(db_path) as connection:
        known = {row["id"] for row in connection.execute("SELECT id FROM universities")}
        with Path(input_path).open(encoding="utf-8-sig") as input_file:
            for line_number, line in enumerate(input_file, 1):
                if not line.strip():
                    continue
                try:
                    item = _validate_review(json.loads(line), known)
                    duplicate = connection.execute(
                        "SELECT id FROM reviews WHERE university_id=? AND text_hash=?",
                        (item["university_id"], item["text_hash"]),
                    ).fetchone()
                    if not duplicate and item["source_review_id"] is not None:
                        duplicate = connection.execute(
                            "SELECT r.id FROM reviews r JOIN sources s ON s.id=r.source_id "
                            "WHERE r.university_id=? AND r.source_review_id=? AND s.platform=? AND s.url=?",
                            (item["university_id"], item["source_review_id"], item["platform"], item["platform_url"]),
                        ).fetchone()
                    if duplicate:
                        statistics["duplicates"] += 1
                        continue
                    prediction = _analyze(analyzer, item["text"])
                    manual = item["manual_sentiment"] is not None
                    approved = manual or not prediction["needs_review"]
                    sentiment = item["manual_sentiment"] or prediction["sentiment"]
                    connection.execute(
                        "INSERT INTO sources(platform,url,terms_checked_at) VALUES (?,?,?) "
                        "ON CONFLICT(platform,url) DO UPDATE SET terms_checked_at=MAX(terms_checked_at,excluded.terms_checked_at)",
                        (item["platform"], item["platform_url"], item["terms_checked_at"]),
                    )
                    source_id = connection.execute(
                        "SELECT id FROM sources WHERE platform=? AND url=?", (item["platform"], item["platform_url"])
                    ).fetchone()["id"]
                    cursor = connection.execute(
                        "INSERT INTO reviews(university_id,source_id,source_url,source_review_id,text,text_hash,published_at,collected_at,"
                        "original_score,verified,display_allowed,personal_data_removed,sentiment,approved) "
                        "VALUES (?,?,?,?,?,?,?,?,?,1,?,1,?,?)",
                        (item["university_id"], source_id, item["source_url"], item["source_review_id"], item["text"], item["text_hash"],
                         item["published_at"], item["collected_at"], item["original_score"], int(item["display_allowed"]),
                         sentiment, int(approved)),
                    )
                    review_id = cursor.lastrowid
                    _record_prediction(connection, review_id, prediction)
                    aspects = item["manual_aspects"] if item["manual_aspects"] is not None else prediction["aspects"]
                    _save_aspects(connection, review_id, aspects, approved=item["manual_aspects"] is not None)
                    _audit(connection, review_id, "import_manual" if manual else "import", "importer", {},
                           {"sentiment": sentiment, "approved": approved, "source_url": item["source_url"],
                            "aspects": aspects, "aspects_approved": item["manual_aspects"] is not None},
                           "Источник, право отображения и удаление персональных данных подтверждены импортёром")
                    statistics["imported"] += 1
                    statistics["pending_moderation"] += int(not approved)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Строка {line_number}: {exc}") from exc
    return statistics


def moderate_review(db_path, review_id, sentiment, aspects=None, note="", actor="reviewer"):
    if sentiment not in {"positive", "neutral", "negative"}:
        raise ValueError("sentiment должна быть positive, neutral или negative")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Нужно указать проверяющего actor")
    with connect(db_path) as connection:
        row = connection.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if row is None:
            raise ValueError("Отзыв не найден")
        before = {"sentiment": row["sentiment"], "approved": bool(row["approved"])}
        before["aspects"] = [dict(item) for item in connection.execute(
            "SELECT aspect,sentiment,confidence,context,approved FROM review_aspects WHERE review_id=? ORDER BY aspect",
            (review_id,),
        )]
        checked_aspects = _aspects(aspects, row["text"]) if aspects is not None else None
        connection.execute("UPDATE reviews SET sentiment=?,approved=1 WHERE id=?", (sentiment, review_id))
        if checked_aspects is not None:
            _save_aspects(connection, review_id, checked_aspects, approved=True)
        after = {"sentiment": sentiment, "approved": True}
        if checked_aspects is not None:
            after["aspects"] = checked_aspects
        _audit(connection, review_id, "approve", actor.strip(), before, after, str(note))
    return {"id": int(review_id), **after}


def reclassify_reviews(db_path, analyzer):
    statistics = {"classified": 0, "manual_preserved": 0, "pending_moderation": 0}
    with connect(db_path) as connection:
        for row in connection.execute("SELECT * FROM reviews ORDER BY id").fetchall():
            prediction = _analyze(analyzer, row["text"])
            _record_prediction(connection, row["id"], prediction)
            manually_checked = connection.execute(
                "SELECT 1 FROM review_audit WHERE review_id=? AND action IN ('approve','import_manual') LIMIT 1",
                (row["id"],),
            ).fetchone()
            if manually_checked:
                statistics["manual_preserved"] += 1
            else:
                connection.execute("UPDATE reviews SET sentiment=?,approved=? WHERE id=?",
                                   (prediction["sentiment"], int(not prediction["needs_review"]), row["id"]))
                _save_aspects(connection, row["id"], prediction["aspects"], approved=False)
                statistics["pending_moderation"] += int(prediction["needs_review"])
            _audit(connection, row["id"], "reclassify", "classifier",
                   {"sentiment": row["sentiment"], "approved": bool(row["approved"])},
                   {"prediction": prediction["sentiment"], "manual_preserved": bool(manually_checked)},
                   prediction["model_version"])
            statistics["classified"] += 1
    return statistics


def _where(filters=None, search_reviews=True):
    filters = filters or {}
    clauses, parameters = [], []
    for key, column in [("university_id", "r.university_id"), ("region", "u.region"),
                        ("sentiment", "r.sentiment"), ("source_id", "r.source_id")]:
        value = filters.get(key)
        if value not in (None, ""):
            if key == "sentiment" and value not in SENTIMENTS:
                raise ValueError("Неизвестная тональность фильтра")
            if key == "source_id":
                try:
                    value = int(value)
                except (ValueError, TypeError) as exc:
                    raise ValueError("source_id должен быть целым числом") from exc
                if not 1 <= value <= MAX_SQL_INTEGER:
                    raise ValueError("source_id выходит за поддерживаемый диапазон")
            clauses.append(f"{column}=?")
            parameters.append(value)
    if filters.get("needs_review") not in (None, ""):
        value = filters["needs_review"]
        pending = value is True or str(value).lower() in {"true", "1"}
        clauses.append("r.approved=?")
        parameters.append(int(not pending))
    aspect = filters.get("aspect")
    if aspect:
        clauses.append("EXISTS(SELECT 1 FROM review_aspects ra WHERE ra.review_id=r.id AND ra.aspect=?)")
        parameters.append(aspect)
    if search_reviews and filters.get("q"):
        clauses.append("((r.display_allowed=1 AND instr(CASEFOLD(r.text),?)>0) OR instr(CASEFOLD(u.full_name),?)>0 "
                       "OR instr(CASEFOLD(u.short_name),?)>0)")
        query = str(filters["q"]).casefold().strip()
        parameters.extend([query, query, query])
    if filters.get("date_from"):
        clauses.append("r.published_at>=?")
        parameters.append(_timestamp(filters["date_from"], "date_from"))
    if filters.get("date_to"):
        end = str(filters["date_to"])
        if len(end) == 10:
            try:
                end_date = date.fromisoformat(end)
            except ValueError as exc:
                raise ValueError("date_to: неверная дата") from exc
            if end_date == date.max:
                clauses.append("r.published_at<=?")
                parameters.append("9999-12-31T23:59:59Z")
            else:
                exclusive = (end_date + timedelta(days=1)).isoformat()
                clauses.append("r.published_at<?")
                parameters.append(_timestamp(exclusive, "date_to"))
        else:
            clauses.append("r.published_at<=?")
            parameters.append(_timestamp(end, "date_to"))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters


REVIEW_SELECT = (
    "SELECT r.*,u.full_name,u.short_name,s.platform,s.url AS platform_url,s.terms_checked_at,"
    "p.confidence,p.model_version,p.sentiment AS predicted_sentiment,p.needs_review AS predicted_needs_review "
    "FROM reviews r JOIN universities u ON u.id=r.university_id JOIN sources s ON s.id=r.source_id "
    "LEFT JOIN predictions p ON p.id=(SELECT MAX(p2.id) FROM predictions p2 WHERE p2.review_id=r.id)"
)


def _review_output(connection, row):
    permitted = bool(row["display_allowed"])
    manual = bool(connection.execute(
        "SELECT 1 FROM review_audit WHERE review_id=? AND action IN ('approve','import_manual') LIMIT 1", (row["id"],)
    ).fetchone())
    aspects = [dict(item) for item in connection.execute(
        "SELECT aspect,sentiment,confidence,context,approved FROM review_aspects WHERE review_id=? ORDER BY aspect", (row["id"],)
    )]
    if not permitted:
        for item in aspects:
            item["context"] = None
    return {"id": row["id"], "university_id": row["university_id"], "full_name": row["full_name"],
            "short_name": row["short_name"], "text": row["text"] if permitted else None,
            "source": {"id": row["source_id"], "platform": row["platform"], "url": row["platform_url"],
                       "terms_checked_at": row["terms_checked_at"]}, "source_url": row["source_url"],
            "source_review_id": row["source_review_id"],
            "published_at": row["published_at"], "collected_at": row["collected_at"],
            "original_score": row["original_score"], "sentiment": row["sentiment"],
            "confidence": None if manual else row["confidence"], "model_version": row["model_version"],
            "manual_reviewed": manual, "label_origin": "manual" if manual else "prediction",
            "prediction": {"sentiment": row["predicted_sentiment"], "confidence": row["confidence"],
                           "model_version": row["model_version"], "needs_review": bool(row["predicted_needs_review"])},
            "needs_review": not bool(row["approved"]), "approved": bool(row["approved"]),
            "verified": True, "display_allowed": permitted, "personal_data_removed": True, "aspects": aspects}


def list_reviews(db_path, filters=None, page=1, per_page=20):
    try:
        page, per_page = int(page), int(per_page)
    except (ValueError, TypeError) as exc:
        raise ValueError("page и per_page должны быть целыми числами") from exc
    if page < 1 or not 1 <= per_page <= 100:
        raise ValueError("page >= 1; per_page должен быть от 1 до 100")
    if (page - 1) * per_page > MAX_SQL_INTEGER:
        raise ValueError("page выходит за поддерживаемый диапазон")
    where, parameters = _where(filters)
    with connect(db_path) as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS n FROM reviews r JOIN universities u ON u.id=r.university_id" + where, parameters
        ).fetchone()["n"]
        rows = connection.execute(REVIEW_SELECT + where + " ORDER BY r.published_at DESC,r.id DESC LIMIT ? OFFSET ?",
                                  [*parameters, per_page, (page - 1) * per_page]).fetchall()
        items = [_review_output(connection, row) for row in rows]
    return {"items": items, "total": total, "page": page, "per_page": per_page,
            "pages": math.ceil(total / per_page) if total else 0}


def _reliability(count, minimum):
    if count < minimum:
        return "insufficient"
    if count < 30:
        return "limited"
    if count < 100:
        return "moderate"
    return "high"


def _topic_stats(connection, review_ids):
    if not review_ids:
        return []
    # Group in Python to avoid SQLite's bound-variable limit on large corpora.
    selected = set(review_ids)
    groups = defaultdict(list)
    for row in connection.execute("SELECT ra.*,r.source_url,r.display_allowed FROM review_aspects ra "
                                  "JOIN reviews r ON r.id=ra.review_id ORDER BY r.published_at DESC,r.id DESC"):
        if row["review_id"] in selected and row["approved"]:
            groups[row["aspect"]].append(row)
    output = []
    for aspect, rows in sorted(groups.items()):
        counts = Counter(row["sentiment"] for row in rows)
        evidence = [{"review_id": row["review_id"], "text": row["context"] if row["display_allowed"] else None,
                     "source_url": row["source_url"], "sentiment": row["sentiment"]} for row in rows[:5]]
        output.append({"aspect": aspect, "count": len(rows), "positive": counts["positive"],
                       "negative": counts["negative"], "neutral": counts["neutral"],
                       "uncertain": counts["uncertain"], "evidence": evidence})
    return output


def ranking(db_path, filters=None, min_reviews=10, smoothing=10):
    if isinstance(min_reviews, bool) or not isinstance(min_reviews, int) or min_reviews < 1:
        raise ValueError("min_reviews должен быть положительным целым числом")
    if isinstance(smoothing, bool) or not isinstance(smoothing, (int, float)) or not math.isfinite(smoothing) or smoothing < 0:
        raise ValueError("smoothing должен быть конечным неотрицательным числом")
    filters = filters or {}
    with connect(db_path) as connection:
        universities = [dict(row) for row in connection.execute("SELECT * FROM universities")]
        if filters.get("university_id"):
            universities = [item for item in universities if item["id"] == filters["university_id"]]
        if filters.get("region"):
            universities = [item for item in universities if item["region"] == filters["region"]]
        if filters.get("q"):
            query = str(filters["q"]).casefold().strip()
            universities = [item for item in universities
                            if query in " ".join([item["full_name"], item["short_name"], item["city"], item["region"]]).casefold()]
        global_counts = connection.execute(
            "SELECT COUNT(*) AS n,COALESCE(SUM(sentiment='positive'),0) AS p FROM reviews "
            "WHERE approved=1 AND sentiment IN ('positive','negative','neutral')"
        ).fetchone()
        baseline = global_counts["p"] / global_counts["n"] if global_counts["n"] else 0
        where, parameters = _where(filters, search_reviews=False)
        reviews = connection.execute("SELECT r.* FROM reviews r JOIN universities u ON u.id=r.university_id" + where,
                                     parameters).fetchall()
        grouped = defaultdict(list)
        for row in reviews:
            grouped[row["university_id"]].append(row)
        results = []
        for university in universities:
            rows = grouped[university["id"]]
            eligible = [row for row in rows if row["approved"] and row["sentiment"] in {"positive", "negative", "neutral"}]
            counts = Counter(row["sentiment"] for row in eligible)
            n = len(eligible)
            small = n < min_reviews
            topics = _topic_stats(connection, [row["id"] for row in eligible])
            strengths = [topic for topic in topics if topic["count"] >= 3 and topic["positive"] > topic["negative"]
                         and topic["positive"] >= 3]
            weaknesses = [topic for topic in topics if topic["count"] >= 3 and topic["negative"] > topic["positive"]
                          and topic["negative"] >= 3]
            score = 100 * (counts["positive"] + smoothing * baseline) / (n + smoothing) if not small else None
            results.append({**university, "reviews_total": len(rows), "eligible_reviews": n,
                            "pending_reviews": len(rows) - n, "positive": counts["positive"],
                            "negative": counts["negative"], "neutral": counts["neutral"],
                            "positive_percent": 100 * counts["positive"] / n if n else None,
                            "smoothed_score": score, "rank": None, "small_sample": small, "eligible": not small,
                            "status": "no_data" if not rows else "insufficient" if small else "ranked",
                            "reliability": _reliability(n, min_reviews), "strengths": strengths,
                            "weaknesses": weaknesses, "baseline_positive_fraction": baseline if global_counts["n"] else None,
                            "period_start": min((row["published_at"] for row in rows), default=None),
                            "period_end": max((row["published_at"] for row in rows), default=None),
                            "last_collected_at": max((row["collected_at"] for row in rows), default=None),
                            "pending_percent": 100 * (len(rows) - n) / len(rows) if rows else None})
    results.sort(key=lambda item: (item["smoothed_score"] is None, -(item["smoothed_score"] or 0), item["full_name"].casefold()))
    last_score, last_rank = None, None
    for position, item in enumerate(results, 1):
        if item["smoothed_score"] is not None:
            if last_score is None or not math.isclose(item["smoothed_score"], last_score, abs_tol=1e-12):
                last_rank = position
            item["rank"] = last_rank
            last_score = item["smoothed_score"]
    return results


def university_detail(db_path, university_id, min_reviews=10, smoothing=10):
    results = [item for item in ranking(db_path, min_reviews=min_reviews, smoothing=smoothing)
               if item["id"] == university_id]
    if not results:
        return None
    with connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM reviews WHERE university_id=? ORDER BY published_at", (university_id,)).fetchall()
        monthly = defaultdict(Counter)
        eligible_ids = []
        for row in rows:
            counts = monthly[row["published_at"][:7]]
            counts["total"] += 1
            if row["approved"] and row["sentiment"] in {"positive", "neutral", "negative"}:
                counts[row["sentiment"]] += 1
                eligible_ids.append(row["id"])
            else:
                counts["needs_review"] += 1
        dynamics = [{"month": month, **{key: counts[key] for key in ("total", "positive", "negative", "neutral", "needs_review")}}
                    for month, counts in sorted(monthly.items())]
        topics = _topic_stats(connection, eligible_ids)
        sources = [dict(row) for row in connection.execute(
            "SELECT s.id,s.platform,s.url,s.terms_checked_at,COUNT(r.id) AS review_count FROM sources s "
            "JOIN reviews r ON r.source_id=s.id WHERE r.university_id=? GROUP BY s.id ORDER BY s.platform", (university_id,)
        )]
    return {"university": results[0], "dynamics": dynamics, "topics": topics, "sources": sources,
            "recent_reviews": list_reviews(db_path, {"university_id": university_id}, per_page=5)["items"],
            "methodology": methodology(db_path, min_reviews=min_reviews, smoothing=smoothing)}


def methodology(db_path, min_reviews=10, smoothing=10):
    with connect(db_path) as connection:
        counts = connection.execute(
            "SELECT COUNT(*) AS total,COALESCE(SUM(approved=1),0) AS approved,COALESCE(SUM(approved=0),0) AS pending FROM reviews"
        ).fetchone()
        universities = connection.execute("SELECT COUNT(*) AS n FROM universities").fetchone()["n"]
        sources = connection.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        source_list = [dict(row) for row in connection.execute(
            "SELECT s.id,s.platform,s.url,s.terms_checked_at,COUNT(r.id) AS review_count FROM sources s "
            "LEFT JOIN reviews r ON r.source_id=s.id GROUP BY s.id ORDER BY s.platform"
        )]
        last_updated = connection.execute("SELECT MAX(collected_at) AS value FROM reviews").fetchone()["value"]
        models = [dict(row) for row in connection.execute(
            "SELECT model_version,COUNT(*) AS predictions FROM predictions GROUP BY model_version ORDER BY model_version"
        )]
    statistics = {"universities": universities, "reviews": counts["total"], "approved": counts["approved"],
                  "pending_moderation": counts["pending"], "sources": sources}
    return {"counts": statistics, "stats": statistics, "sources": source_list, "last_updated": last_updated,
            "requires_corpus": counts["total"] == 0,
            "models": models, "min_reviews": min_reviews, "smoothing": smoothing, "min_topic_reviews": 3,
            "score_formula": f"100 × (P + {smoothing:g} × p₀) / (P + N + U + {smoothing:g})",
            "positive_percent_formula": "100 × P / (P + N + U)",
            "baseline_scope": "Все проверенные отзывы корпуса до применения фильтров",
            "sections": [
                {"title": "Источники", "text": "В базу импортируются только реальные отзывы, проверенные человеком, со ссылкой на публикацию и датами публикации, сбора и проверки условий площадки. Тексты без разрешения на показ скрыты."},
                {"title": "Тональность и проверка", "text": "Правила анализируют русскоязычный текст по предложениям. Их confidence не является вероятностью: без калибровки оно равно null, а результат требует ручной проверки. Непроверенные и неопределённые оценки исключены из рейтинга."},
                {"title": "Рейтинг", "text": f"P — число положительных, N — отрицательных, U — нейтральных проверенных отзывов. p₀ — доля положительных во всём проверенном корпусе до фильтрации. Оценка сглажена к этому среднему с весом {smoothing:g}; при менее чем {min_reviews} отзывах место и оценка не публикуются."},
                {"title": "Аспекты", "text": "Каждый отзыв учитывается не более одного раза по каждому аспекту. Сильная или слабая сторона публикуется только при минимум трёх подтверждениях соответствующей тональности, с контекстом и ссылками на отзывы."},
                {"title": "Ограничения", "text": "Отзывы не являются репрезентативным опросом студентов. Рейтинг отражает только доступный проверенный корпус, а не объективное качество образования. Достоверность по размеру выборки не гарантирует отсутствие систематического смещения."},
            ]}
