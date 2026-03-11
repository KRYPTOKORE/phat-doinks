"""SQLite state store for tracking progress and supporting undo."""

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class StateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        # Init schema on the creating thread
        conn = self._get_conn()
        self._init_schema_on(conn)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_schema_on(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS moves (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source_path TEXT NOT NULL,
                dest_path TEXT NOT NULL,
                category TEXT NOT NULL,
                is_meme INTEGER NOT NULL,
                undone INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS processing_state (
                file_path TEXT PRIMARY KEY,
                category TEXT,
                is_meme INTEGER,
                model TEXT,
                processed_at TEXT,
                run_id TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_processed INTEGER DEFAULT 0,
                total_moved INTEGER DEFAULT 0,
                total_errors INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_moves_run_id ON moves(run_id);
            CREATE INDEX IF NOT EXISTS idx_processing_run_id ON processing_state(run_id);
        """)
        conn.commit()

    def new_run(self, mode: str) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO runs (run_id, mode, started_at) VALUES (?, ?, ?)",
                (run_id, mode, now),
            )
            conn.commit()
        return run_id

    def finish_run(self, run_id: str, processed: int, moved: int, errors: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """UPDATE runs SET finished_at=?, total_processed=?, total_moved=?, total_errors=?
                   WHERE run_id=?""",
                (now, processed, moved, errors, run_id),
            )
            conn.commit()

    def is_processed(self, file_path: str, run_id: str | None = None) -> bool:
        conn = self._get_conn()
        if run_id:
            row = conn.execute(
                "SELECT 1 FROM processing_state WHERE file_path=? AND run_id=?",
                (file_path, run_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM processing_state WHERE file_path=?",
                (file_path,),
            ).fetchone()
        return row is not None

    def mark_processed(
        self,
        file_path: str,
        category: str,
        is_meme: bool,
        model: str,
        run_id: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO processing_state
                   (file_path, category, is_meme, model, processed_at, run_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (file_path, category, int(is_meme), model, now, run_id),
            )
            conn.commit()

    def record_move(
        self,
        run_id: str,
        source_path: str,
        dest_path: str,
        category: str,
        is_meme: bool,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO moves (run_id, timestamp, source_path, dest_path, category, is_meme)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, now, source_path, dest_path, category, int(is_meme)),
            )
            conn.commit()

    def get_undo_batch(self, run_id: str | None = None) -> list[dict]:
        """Get moves to undo. If no run_id, gets the latest run."""
        conn = self._get_conn()
        if not run_id:
            row = conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return []
            run_id = row["run_id"]

        rows = conn.execute(
            """SELECT id, source_path, dest_path, category
               FROM moves WHERE run_id=? AND undone=0
               ORDER BY id DESC""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_undone(self, move_ids: list[int]):
        if not move_ids:
            return
        placeholders = ",".join("?" * len(move_ids))
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                f"UPDATE moves SET undone=1 WHERE id IN ({placeholders})",
                move_ids,
            )
            conn.commit()

    def get_stats(self) -> dict[str, int]:
        """Get file counts per category from processing_state."""
        rows = self._get_conn().execute(
            "SELECT category, COUNT(*) as count FROM processing_state GROUP BY category"
        ).fetchall()
        return {r["category"]: r["count"] for r in rows}

    def get_run_history(self, limit: int = 10) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_processed_paths(self, run_id: str) -> set[str]:
        rows = self._get_conn().execute(
            "SELECT file_path FROM processing_state WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return {r["file_path"] for r in rows}

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None
