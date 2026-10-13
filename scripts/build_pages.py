"""Export public views for GitHub Pages without publishing a SQLite database."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baknin import create_app
from baknin.nlp import ASPECT_LABELS, RULES_VERSION, _ASPECT_REGEX, _NEGATIVE, _POSITIVE, _STOPWORDS


def normalize_base(value):
    if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]*", value):
        raise ValueError("base-path должен быть URL-путём вида /Baknin_Project/")
    parts = [part for part in value.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("base-path не должен содержать . или ..")
    return "/" + "/".join(parts) + ("/" if parts else "")


class PrefixLinks(HTMLParser):
    """Rewrite only actual URL attributes; leave text and SVG intact."""
    def __init__(self, base):
        super().__init__(convert_charrefs=False)
        self.base = base
        self.parts = []

    def handle_starttag(self, tag, attrs):
        def replace(match):
            url = match.group(2)
            return match.group(1) + (self.base + url[1:] if not url.startswith("//") else url) + match.group(3)
        self.parts.append(re.sub(r"(\b(?:href|src|action)\s*=\s*[\"'])(/[^\"']*)([\"'])",
                                 replace, self.get_starttag_text(), flags=re.IGNORECASE))

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag): self.parts.append(f"</{tag}>")
    def handle_data(self, data): self.parts.append(data)
    def handle_decl(self, decl): self.parts.append(f"<!{decl}>")
    def handle_entityref(self, name): self.parts.append(f"&{name};")
    def handle_charref(self, name): self.parts.append(f"&#{name};")
    def handle_comment(self, data): self.parts.append(f"<!--{data}-->")


def export_site(output, base_path="/Baknin_Project/", database=None):
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Папка результата не должна быть символической ссылкой")
    output = output.resolve()
    base = normalize_base(base_path)
    protected = [ROOT / name for name in ("static", "templates", "data", "docs", "baknin", "scripts", "tests", ".git", ".github")]
    if output == ROOT or output in ROOT.parents or any(output == path or path in output.parents for path in protected):
        raise ValueError("Нельзя записывать сборку поверх исходников")
    if output.exists() and not output.is_dir():
        raise ValueError("output должен указывать на папку")
    source_path = Path(database).resolve(strict=True) if database is not None else None
    if source_path is not None and (source_path == output or output in source_path.parents):
        raise ValueError("Входная database не должна находиться внутри папки сборки")
    marker = output / ".baknin-pages-build"
    if output.exists() and any(output.iterdir()) and not (marker.is_file() and marker.read_text() == "baknin-pages-v1\n"):
        raise ValueError("Папка результата не пуста и не является предыдущей сборкой Pages")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="baknin-pages-db-") as temporary_db, \
         tempfile.TemporaryDirectory(prefix=".baknin-pages-stage-", dir=output.parent) as temporary_site:
        db_path = Path(temporary_db) / "snapshot.sqlite3"
        if database is not None:
            if not source_path.is_file():
                raise ValueError("database должен указывать на существующий файл SQLite")
            source = sqlite3.connect("file:" + quote(str(source_path), safe="/") + "?mode=ro", uri=True)
            target = sqlite3.connect(db_path)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        stage = Path(temporary_site)
        app = create_app({"TESTING": True, "DATABASE": str(db_path), "MODEL_PATH": None,
                          "METRICS_PATH": str(Path(temporary_db) / "no-metrics.json"),
                          "PAGE_MODE": "pages", "PAGE_BASE_PATH": base})
        client = app.test_client()

        def api(path):
            response = client.get(path)
            if response.status_code != 200:
                raise ValueError(f"Не удалось экспортировать {path}: HTTP {response.status_code}")
            return response.get_json()

        universities = api("/api/universities")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", str(item["id"])) for item in universities["items"]):
            raise ValueError("ID вуза не подходит для безопасного статического URL")
        details = {item["id"]: api(f"/api/universities/{item['id']}") for item in universities["items"]}
        reviews = []
        page = 1
        while True:
            result = api(f"/api/reviews?per_page=100&page={page}")
            reviews.extend(result["items"])
            if page >= result["pages"]:
                break
            page += 1
        methodology = api("/api/methodology")
        methodology.update(engine="browser_rules", model_version=RULES_VERSION + "-browser",
                           metrics_available=False, metrics=None,
                           analysis_location="browser", server_api_available=False)
        snapshot = {
            "format": 1, "base_path": base, "exported_at": datetime.now(timezone.utc).isoformat(),
            "universities": universities, "details": details, "reviews": reviews,
            "methodology": methodology, "health": api("/health"),
            "rules": {"version": RULES_VERSION, "aspect_labels": ASPECT_LABELS,
                      "aspect_patterns": {key: value.pattern for key, value in _ASPECT_REGEX.items()},
                      "positive_pattern": _POSITIVE.pattern, "negative_pattern": _NEGATIVE.pattern,
                      "stopwords": sorted(_STOPWORDS)},
        }
        (stage / "data").mkdir()
        (stage / "data" / "site.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        pages = {"/": "index.html", "/reviews": "reviews/index.html", "/analysis": "analysis/index.html",
                 "/methodology": "methodology/index.html", "/launch": "launch/index.html"}
        pages.update({f"/universities/{key}": f"universities/{key}/index.html" for key in details})
        for route, filename in pages.items():
            response = client.get(route)
            if response.status_code != 200:
                raise ValueError(f"Не удалось экспортировать страницу {route}")
            parser = PrefixLinks(base)
            parser.feed(response.get_data(as_text=True))
            target = stage / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(parser.parts), encoding="utf-8")
        with app.test_request_context("/404"):
            from flask import render_template
            parser = PrefixLinks(base)
            parser.feed(render_template("error.html", message="Страница не найдена", status=404))
            (stage / "404.html").write_text("".join(parser.parts), encoding="utf-8")
        shutil.copytree(ROOT / "static", stage / "static")
        (stage / ".nojekyll").write_text("", encoding="utf-8")
        (stage / ".baknin-pages-build").write_text("baknin-pages-v1\n", encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix=".baknin-pages-backup-", dir=output.parent) as backup_dir:
            previous = Path(backup_dir) / "previous"
            if output.exists():
                output.rename(previous)
            try:
                stage.rename(output)
            except OSError:
                if previous.exists():
                    previous.rename(output)
                raise
    return {"output": str(output), "base_path": base, "universities": len(details), "reviews": len(reviews)}


def main():
    parser = argparse.ArgumentParser(description="Сборка открытого снимка для GitHub Pages")
    parser.add_argument("--output", default="_site", type=Path)
    parser.add_argument("--base-path", default="/Baknin_Project/")
    parser.add_argument("--database", type=Path,
                        help="Явно выбранная SQLite-база; по умолчанию только справочник без отзывов")
    args = parser.parse_args()
    try:
        result = export_site(args.output, args.base_path, args.database)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Ошибка сборки: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
