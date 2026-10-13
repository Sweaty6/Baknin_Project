"""SQLite storage; all paths are selected by the caller."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS universities (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    official_url TEXT NOT NULL,
    official_url_live_verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    terms_checked_at TEXT NOT NULL,
    UNIQUE(platform, url)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id TEXT NOT NULL REFERENCES universities(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_url TEXT NOT NULL,
    source_review_id TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    published_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    original_score REAL,
    verified INTEGER NOT NULL CHECK(verified = 1),
    display_allowed INTEGER NOT NULL CHECK(display_allowed IN (0, 1)),
    personal_data_removed INTEGER NOT NULL CHECK(personal_data_removed = 1),
    sentiment TEXT NOT NULL CHECK(sentiment IN ('positive', 'neutral', 'negative', 'uncertain')),
    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(university_id, text_hash),
    UNIQUE(source_id, university_id, source_review_id)
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    sentiment TEXT NOT NULL,
    confidence REAL,
    needs_review INTEGER NOT NULL DEFAULT 1,
    model_version TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS review_aspects (
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    aspect TEXT NOT NULL,
    sentiment TEXT NOT NULL CHECK(sentiment IN ('positive', 'neutral', 'negative', 'uncertain')),
    confidence REAL,
    context TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0, 1)),
    PRIMARY KEY(review_id, aspect)
);
CREATE TABLE IF NOT EXISTS review_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_reviews_university ON reviews(university_id);
CREATE INDEX IF NOT EXISTS idx_reviews_publication ON reviews(published_at);
CREATE INDEX IF NOT EXISTS idx_reviews_source ON reviews(source_id);
CREATE INDEX IF NOT EXISTS idx_predictions_review ON predictions(review_id, id);
CREATE INDEX IF NOT EXISTS idx_aspects_topic ON review_aspects(aspect);
"""


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and close on leaving a with block."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=15, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function("CASEFOLD", 1, lambda value: str(value or "").casefold())
    return connection


def initialize(db_path: str | Path) -> None:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        # Additive migrations for local databases created by earlier releases.
        for table, column, declaration in [
            ("universities", "official_url_live_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("review_aspects", "approved", "INTEGER NOT NULL DEFAULT 0"),
            ("reviews", "source_review_id", "TEXT"),
        ]:
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_review_external_id "
                           "ON reviews(source_id,university_id,source_review_id)")


def seed_universities(db_path: str | Path) -> int:
    """Seed only institutional metadata, never reviews or ratings."""
    file_path = Path(__file__).resolve().parent.parent / "data" / "universities.json"
    universities = json.loads(file_path.read_text(encoding="utf-8"))
    with connect(db_path) as connection:
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO universities "
            "(id, full_name, short_name, city, region, official_url) "
            "VALUES (:id, :full_name, :short_name, :city, :region, :official_url)",
            universities,
        )
        return connection.total_changes - before
