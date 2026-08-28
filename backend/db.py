"""SQLite persistence — stdlib sqlite3, no ORM. One short-lived connection
per call (WAL mode) so it's safe across the orchestrator's worker threads."""
import sqlite3
import time

from . import config

VIDEO_STATUSES = (
    "QUEUED", "DOWNLOADING", "DOWNLOADED", "CAPTION_GENERATING", "CAPTION_READY",
    "EDITING", "EDITED", "QA", "QA_FAILED", "QA_PASSED", "WAITING_APPROVAL",
    "APPROVED", "PUBLISHING", "PUBLISHED", "FAILED",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    resolution TEXT NOT NULL,
    watermark_enabled INTEGER NOT NULL,
    watermark_text TEXT NOT NULL DEFAULT '',
    hashtags TEXT NOT NULL DEFAULT '',
    caption_examples_json TEXT NOT NULL DEFAULT '[]',
    style_instructions TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    ig_post_id TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    youtube_url TEXT NOT NULL,
    start_time_seconds REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    title TEXT,
    downloaded_path TEXT,
    duration REAL,
    caption_candidates_json TEXT,
    caption_text TEXT,
    caption_reason TEXT,
    description TEXT NOT NULL DEFAULT '',
    copied_from_idx INTEGER,
    caption_bold INTEGER NOT NULL DEFAULT 0,
    video_filter TEXT NOT NULL DEFAULT 'none',
    resolution TEXT NOT NULL DEFAULT '',
    font_family TEXT NOT NULL DEFAULT 'poppins',
    caption_position TEXT NOT NULL DEFAULT 'top',
    font_color TEXT NOT NULL DEFAULT '',
    aspect_ratio TEXT NOT NULL DEFAULT '1:1',
    caption_style TEXT NOT NULL DEFAULT 'band',
    skip_caption INTEGER NOT NULL DEFAULT 0,
    ig_container_id TEXT,
    final_path TEXT,
    qa_report_json TEXT,
    qa_retry_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    video_id INTEGER,
    agent TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def get_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn, table, column, coldef):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS won't add columns to a table that
        # already existed on disk before these were introduced.
        _ensure_column(conn, "videos", "description", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "videos", "copied_from_idx", "INTEGER")
        _ensure_column(conn, "videos", "caption_bold", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "videos", "ig_container_id", "TEXT")
        _ensure_column(conn, "videos", "video_filter", "TEXT NOT NULL DEFAULT 'none'")
        _ensure_column(conn, "videos", "resolution", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "videos", "font_family", "TEXT NOT NULL DEFAULT 'poppins'")
        _ensure_column(conn, "videos", "caption_position", "TEXT NOT NULL DEFAULT 'top'")
        _ensure_column(conn, "videos", "font_color", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "videos", "aspect_ratio", "TEXT NOT NULL DEFAULT '1:1'")
        _ensure_column(conn, "videos", "caption_style", "TEXT NOT NULL DEFAULT 'band'")
        _ensure_column(conn, "videos", "skip_caption", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "batches", "stop_requested", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "batches", "hashtags", "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def _row(row):
    return dict(row) if row is not None else None


def create_batch(resolution, watermark_enabled, watermark_text, hashtags=""):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO batches (created_at, resolution, watermark_enabled, watermark_text, hashtags, status) "
            "VALUES (?, ?, ?, ?, ?, 'QUEUED')",
            (time.time(), resolution, int(watermark_enabled), watermark_text, hashtags),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def add_video(batch_id, idx, youtube_url, start_time_seconds, caption_text=None, description="",
              copied_from_idx=None, video_filter="none", resolution="",
              font_family="poppins", caption_position="top", font_color="", aspect_ratio="1:1",
              caption_style="band", skip_caption=False):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO videos (batch_id, idx, youtube_url, start_time_seconds, status, caption_text, "
            "description, copied_from_idx, caption_bold, video_filter, resolution, font_family, "
            "caption_position, font_color, aspect_ratio, caption_style, skip_caption, updated_at) "
            "VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, idx, youtube_url, start_time_seconds, caption_text, description, copied_from_idx,
             video_filter, resolution, font_family, caption_position, font_color, aspect_ratio,
             caption_style, int(bool(skip_caption)), time.time()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_batch(batch_id):
    conn = get_conn()
    try:
        return _row(conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone())
    finally:
        conn.close()


def get_video(video_id):
    conn = get_conn()
    try:
        return _row(conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone())
    finally:
        conn.close()


def list_videos(batch_id=None):
    conn = get_conn()
    try:
        if batch_id is None:
            rows = conn.execute("SELECT * FROM videos ORDER BY batch_id, idx").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM videos WHERE batch_id = ? ORDER BY idx", (batch_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_batches():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_batches_for_cleanup(max_age_seconds, terminal_statuses):
    """Batches eligible for startup cleanup: older than max_age_seconds AND
    either already in a terminal status (PUBLISHED/REJECTED), or one where
    every one of its videos ended FAILED (dead-ended, will never finish
    on its own). Active/waiting-approval batches are never returned here,
    regardless of age."""
    cutoff = time.time() - max_age_seconds
    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in terminal_statuses)
        terminal = conn.execute(
            f"SELECT * FROM batches WHERE status IN ({placeholders}) AND created_at < ?",
            (*terminal_statuses, cutoff),
        ).fetchall()
        all_failed = conn.execute(
            "SELECT * FROM batches WHERE created_at < ? AND id IN ("
            "  SELECT batch_id FROM videos GROUP BY batch_id "
            "  HAVING SUM(CASE WHEN status != 'FAILED' THEN 1 ELSE 0 END) = 0"
            ")",
            (cutoff,),
        ).fetchall()
        by_id = {row["id"]: dict(row) for row in list(terminal) + list(all_failed)}
        return list(by_id.values())
    finally:
        conn.close()


def delete_batch(batch_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM events WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM videos WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
        conn.commit()
    finally:
        conn.close()


def _update(table, row_id, fields):
    if not fields:
        return
    if table == "videos":
        fields = {**fields, "updated_at": time.time()}
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [row_id]
    conn = get_conn()
    try:
        conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def update_video(video_id, **fields):
    _update("videos", video_id, fields)


def update_batch(batch_id, **fields):
    _update("batches", batch_id, fields)


def add_event(agent, message, batch_id=None, video_id=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO events (batch_id, video_id, agent, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (batch_id, video_id, agent, message, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def list_events(limit=200):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def add_chat_message(role, content):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO chat_messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def list_chat_messages(limit=200):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def full_state():
    return {
        "batches": list_batches(),
        "videos": list_videos(),
        "events": list_events(),
        "chat_messages": list_chat_messages(),
    }


if __name__ == "__main__":
    # ponytail self-check: schema applies cleanly and a round-trip works.
    import os
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    init_db()
    bid = create_batch("720p", True, "@test")
    vid = add_video(bid, 0, "https://youtu.be/x", 12.0)
    vid2 = add_video(bid, 1, "https://youtu.be/y", 5.0, caption_text="manual caption", description="", copied_from_idx=None)
    vid3 = add_video(bid, 2, "https://youtu.be/z", 0.0, description="a description", copied_from_idx=0)
    update_video(vid, status="DOWNLOADING")
    add_event("Alex", "Started downloading Video #1", batch_id=bid, video_id=vid)
    state = full_state()
    assert state["videos"][0]["status"] == "DOWNLOADING"
    assert state["videos"][1]["caption_text"] == "manual caption"
    assert state["videos"][2]["description"] == "a description" and state["videos"][2]["copied_from_idx"] == 0
    assert state["batches"][0]["id"] == bid
    assert len(state["events"]) == 1
    print("db.py self-check OK")
