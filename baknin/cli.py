"""Command line administration. No harvesting or fabricated corpus generation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .db import initialize, seed_universities
from .nlp import Analyzer
from .service import import_reviews, list_reviews, moderate_review, reclassify_reviews


DEFAULT_DB = Path(__file__).resolve().parent.parent / "instance" / "baknin.sqlite3"


def parser():
    result = argparse.ArgumentParser(description="Импорт и проверка реальных отзывов об аграрных вузах")
    result.add_argument("--db", default=os.environ.get("BAKNIN_DB", str(DEFAULT_DB)), help="Путь к SQLite базе")
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Создать схему и добавить только метаданные пяти вузов")
    importer = commands.add_parser("import", help="Атомарно импортировать проверенные отзывы JSONL")
    importer.add_argument("input_path", nargs="?", help="Файл JSONL")
    importer.add_argument("--input", dest="input_option", help="Файл JSONL (альтернатива позиционному аргументу)")
    importer.add_argument("--model", "--model-path", dest="model_path", help="Путь доверенной локальной модели")
    classifier = commands.add_parser("reclassify", help="Обновить прогнозы, сохранив ручные проверки")
    classifier.add_argument("--model", "--model-path", dest="model_path", help="Путь доверенной локальной модели")
    audit = commands.add_parser("audit", help="Показать очередь или подтвердить тональность отзыва")
    audit.add_argument("review_id", nargs="?", type=int)
    audit.add_argument("--sentiment", choices=["positive", "neutral", "negative"])
    audit.add_argument("--aspects", help="JSON файл проверенных аспектов [{aspect,sentiment,context}]")
    audit.add_argument("--actor", default="reviewer", help="Имя/идентификатор проверяющего")
    audit.add_argument("--note", default="", help="Обоснование ручной оценки")
    audit.add_argument("--page", type=int, default=1, help="Страница очереди ручной проверки")
    for command in (init, importer, classifier, audit):
        command.add_argument("--db", default=argparse.SUPPRESS, help="Путь к SQLite базе")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        initialize(args.db)
        seed_universities(args.db)
        if args.command == "init":
            result = {"database": str(Path(args.db).resolve()), "universities": 5, "reviews_seeded": 0}
        elif args.command == "import":
            input_path = args.input_option or args.input_path
            if not input_path:
                raise ValueError("Укажите JSONL файл позиционно или через --input")
            if args.input_option and args.input_path:
                raise ValueError("Укажите только один входной JSONL файл")
            result = import_reviews(args.db, input_path, Analyzer(model_path=args.model_path or os.environ.get("BAKNIN_MODEL")))
        elif args.command == "reclassify":
            result = reclassify_reviews(args.db, Analyzer(model_path=args.model_path or os.environ.get("BAKNIN_MODEL")))
        elif args.review_id is None:
            if args.sentiment or args.aspects:
                raise ValueError("Для ручной оценки нужен review_id")
            result = list_reviews(args.db, {"needs_review": True}, page=args.page)
        else:
            if not args.sentiment:
                raise ValueError("Для проверки нужен --sentiment")
            aspects = json.loads(Path(args.aspects).read_text(encoding="utf-8")) if args.aspects else None
            result = moderate_review(args.db, args.review_id, args.sentiment, aspects, args.note, args.actor)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
