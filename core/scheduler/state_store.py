import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import List, Optional, Tuple
from core.scheduler.task import DownloadTask, DownloadStatus
from core.engine.chunk_planner import Chunk

logger = logging.getLogger(__name__)


class StateStore:
    """
    SQLite-backed state persistence for tasks and chunks with WAL mode
    to support seamless download resume across application restarts.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                destination_dir TEXT NOT NULL,
                package_name TEXT,
                filename TEXT,
                status TEXT NOT NULL,
                total_size INTEGER DEFAULT 0,
                downloaded_bytes INTEGER DEFAULT 0,
                etag TEXT,
                error_message TEXT,
                created_at REAL NOT NULL,
                cookies TEXT,
                referer TEXT,
                user_agent TEXT,
                category TEXT,
                task_type TEXT DEFAULT 'HTTP',
                seeds INTEGER DEFAULT 0,
                peers INTEGER DEFAULT 0,
                upload_speed_bps REAL DEFAULT 0.0,
                pieces_total INTEGER DEFAULT 0,
                pieces_completed INTEGER DEFAULT 0,
                info_hash TEXT,
                selected_files TEXT,
                priority INTEGER DEFAULT 4,
                files_status TEXT,
                headers TEXT
            );
            """)

            # Automatic migration for existing databases
            for col, col_def in (
                ("cookies", "TEXT"),
                ("referer", "TEXT"),
                ("user_agent", "TEXT"),
                ("category", "TEXT"),
                ("task_type", "TEXT DEFAULT 'HTTP'"),
                ("seeds", "INTEGER DEFAULT 0"),
                ("peers", "INTEGER DEFAULT 0"),
                ("upload_speed_bps", "REAL DEFAULT 0.0"),
                ("pieces_total", "INTEGER DEFAULT 0"),
                ("pieces_completed", "INTEGER DEFAULT 0"),
                ("info_hash", "TEXT"),
                ("selected_files", "TEXT"),
                ("priority", "INTEGER DEFAULT 4"),
                ("files_status", "TEXT"),
                ("headers", "TEXT"),
            ):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_def};")
                except sqlite3.OperationalError:
                    pass

            conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                task_id TEXT NOT NULL,
                chunk_id INTEGER NOT NULL,
                start_byte INTEGER NOT NULL,
                end_byte INTEGER NOT NULL,
                bytes_downloaded INTEGER DEFAULT 0,
                is_completed BOOLEAN DEFAULT 0,
                PRIMARY KEY (task_id, chunk_id),
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            """)
            conn.commit()

    def save_task(self, task: DownloadTask) -> None:
        # Defensively sanitize numeric and collection parameters for SQLite
        p_total = int(task.pieces_total) if isinstance(task.pieces_total, (int, float)) else 0
        if isinstance(task.pieces_completed, (int, float)):
            p_completed = int(task.pieces_completed)
        elif isinstance(task.pieces_completed, list):
            p_completed = task.pieces_completed.count(True) if (task.pieces_completed and isinstance(task.pieces_completed[0], bool)) else len(task.pieces_completed)
        else:
            p_completed = 0

        seeds = int(task.seeds) if isinstance(task.seeds, (int, float)) else 0
        peers = int(task.peers) if isinstance(task.peers, (int, float)) else 0
        up_speed = float(task.upload_speed_bps) if isinstance(task.upload_speed_bps, (int, float)) else 0.0
        prio = int(getattr(task, "priority", 4)) if isinstance(getattr(task, "priority", 4), (int, float)) else 4

        if task.selected_files is not None:
            if isinstance(task.selected_files, str):
                sel_files_json = task.selected_files
            else:
                try:
                    sel_files_json = json.dumps(list(task.selected_files))
                except Exception:
                    sel_files_json = None
        else:
            sel_files_json = None

        if task.files_status is not None:
            if isinstance(task.files_status, str):
                files_status_json = task.files_status
            else:
                try:
                    files_status_json = json.dumps(task.files_status)
                except Exception:
                    files_status_json = None
        else:
            files_status_json = None

        if task.headers is not None:
            if isinstance(task.headers, str):
                headers_json = task.headers
            else:
                try:
                    headers_json = json.dumps(task.headers)
                except Exception:
                    headers_json = None
        else:
            headers_json = None

        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO tasks (
                task_id, url, destination_dir, package_name, filename,
                status, total_size, downloaded_bytes, etag, error_message, created_at,
                cookies, referer, user_agent, category,
                task_type, seeds, peers, upload_speed_bps, pieces_total, pieces_completed,
                info_hash, selected_files, priority, files_status, headers
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                task.task_id,
                task.url,
                str(task.destination_dir),
                task.package_name,
                task.filename,
                task.status.value,
                int(task.total_size) if isinstance(task.total_size, (int, float)) else 0,
                int(task.downloaded_bytes) if isinstance(task.downloaded_bytes, (int, float)) else 0,
                task.etag,
                task.error_message,
                task.created_at,
                task.cookies,
                task.referer,
                task.user_agent,
                task.category,
                task.task_type or "HTTP",
                seeds,
                peers,
                up_speed,
                p_total,
                p_completed,
                task.info_hash,
                sel_files_json,
                prio,
                files_status_json,
                headers_json,
            ))
            conn.commit()

    def update_task_progress(self, task_id: str, downloaded_bytes: int, status: DownloadStatus) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE tasks SET downloaded_bytes = ?, status = ? WHERE task_id = ?;
            """, (downloaded_bytes, status.value, task_id))
            conn.commit()

    def update_task_status(self, task_id: str, status: DownloadStatus, error: Optional[str] = None) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE tasks SET status = ?, error_message = ? WHERE task_id = ?;
            """, (status.value, error, task_id))
            conn.commit()

    def update_task_url(self, task_id: str, new_url: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE tasks SET url = ? WHERE task_id = ?;
            """, (new_url, task_id))
            conn.commit()

    def save_chunks(self, task_id: str, chunks: List[Chunk]) -> None:
        with self._get_connection() as conn:
            conn.executemany("""
            INSERT OR REPLACE INTO chunks (
                task_id, chunk_id, start_byte, end_byte, bytes_downloaded, is_completed
            ) VALUES (?, ?, ?, ?, ?, ?);
            """, [
                (task_id, c.chunk_id, c.start_byte, c.end_byte, c.bytes_downloaded, 1 if c.is_completed else 0)
                for c in chunks
            ])
            conn.commit()

    def update_chunk_progress(self, task_id: str, chunk_id: int, bytes_downloaded: int, is_completed: bool) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            UPDATE chunks SET bytes_downloaded = ?, is_completed = ?
            WHERE task_id = ? AND chunk_id = ?;
            """, (bytes_downloaded, 1 if is_completed else 0, task_id, chunk_id))
            conn.commit()

    def load_chunks(self, task_id: str) -> List[Chunk]:
        with self._get_connection() as conn:
            cursor = conn.execute("""
            SELECT chunk_id, start_byte, end_byte, bytes_downloaded, is_completed
            FROM chunks WHERE task_id = ? ORDER BY chunk_id ASC;
            """, (task_id,))
            rows = cursor.fetchall()

        return [
            Chunk(
                chunk_id=r[0],
                start_byte=r[1],
                end_byte=r[2],
                bytes_downloaded=r[3],
                is_completed=bool(r[4]),
            )
            for r in rows
        ]

    @staticmethod
    def _row_to_task(r: tuple) -> DownloadTask:
        raw_filename = r[4]
        clean_filename = os.path.basename(str(raw_filename).replace("\\", "/")) if raw_filename else None
        
        # Deserialize selected_files if present
        sel_files_raw = r[22] if len(r) > 22 else None
        selected_files = None
        if sel_files_raw:
            try:
                selected_files = json.loads(sel_files_raw)
            except Exception:
                selected_files = None

        # Deserialize files_status if present
        files_status_raw = r[24] if len(r) > 24 else None
        files_status = None
        if files_status_raw:
            try:
                files_status = json.loads(files_status_raw)
            except Exception:
                files_status = None

        # Deserialize headers if present
        headers_raw = r[25] if len(r) > 25 else None
        headers = None
        if headers_raw:
            try:
                headers = json.loads(headers_raw)
            except Exception:
                headers = None

        return DownloadTask(
            task_id=r[0],
            url=r[1],
            destination_dir=Path(r[2]),
            package_name=r[3],
            filename=clean_filename,
            status=DownloadStatus(r[5]),
            total_size=r[6] or 0,
            downloaded_bytes=r[7] or 0,
            etag=r[8],
            error_message=r[9],
            created_at=r[10],
            cookies=r[11] if len(r) > 11 else None,
            referer=r[12] if len(r) > 12 else None,
            user_agent=r[13] if len(r) > 13 else None,
            headers=headers,
            category=r[14] if len(r) > 14 else None,
            task_type=r[15] if len(r) > 15 and r[15] else "HTTP",
            seeds=r[16] if len(r) > 16 and r[16] is not None else 0,
            peers=r[17] if len(r) > 17 and r[17] is not None else 0,
            upload_speed_bps=r[18] if len(r) > 18 and r[18] is not None else 0.0,
            pieces_total=r[19] if len(r) > 19 and r[19] is not None else 0,
            pieces_completed=r[20] if len(r) > 20 and r[20] is not None else 0,
            info_hash=r[21] if len(r) > 21 else None,
            selected_files=selected_files,
            priority=r[23] if len(r) > 23 and r[23] is not None else 4,
            files_status=files_status,
        )

    _TASK_COLUMNS = """
        task_id, url, destination_dir, package_name, filename,
        status, total_size, downloaded_bytes, etag, error_message, created_at,
        cookies, referer, user_agent, category,
        task_type, seeds, peers, upload_speed_bps, pieces_total, pieces_completed,
        info_hash, selected_files, priority, files_status, headers
    """

    def get_pending_tasks(self) -> List[DownloadTask]:
        with self._get_connection() as conn:
            cursor = conn.execute(f"""
            SELECT {self._TASK_COLUMNS}
            FROM tasks WHERE status IN (?, ?, ?) ORDER BY created_at ASC;
            """, (DownloadStatus.PENDING.value, DownloadStatus.DOWNLOADING.value, DownloadStatus.PAUSED.value))
            rows = cursor.fetchall()

        return [self._row_to_task(r) for r in rows]

    def get_all_tasks(self) -> List[DownloadTask]:
        """Returns all tasks stored in the database ordered by creation date descending."""
        with self._get_connection() as conn:
            cursor = conn.execute(f"""
            SELECT {self._TASK_COLUMNS}
            FROM tasks ORDER BY created_at DESC;
            """)
            rows = cursor.fetchall()

        return [self._row_to_task(r) for r in rows]

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Retrieves a single task by its task_id."""
        with self._get_connection() as conn:
            cursor = conn.execute(f"""
            SELECT {self._TASK_COLUMNS}
            FROM tasks WHERE task_id = ?;
            """, (task_id,))
            r = cursor.fetchone()

        if not r:
            return None

        return self._row_to_task(r)

    def delete_task(self, task_id: str) -> None:
        """Deletes a task and its chunks from the database."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM chunks WHERE task_id = ?;", (task_id,))
            conn.execute("DELETE FROM tasks WHERE task_id = ?;", (task_id,))
            conn.commit()

    def delete_inactive_tasks(self) -> int:
        """
        Deletes tasks that are COMPLETED, FAILED, CANCELLED, or PAUSED with 0 downloaded bytes.
        Returns the count of deleted tasks.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
            DELETE FROM tasks
            WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')
               OR (status = 'PAUSED' AND (downloaded_bytes = 0 OR downloaded_bytes IS NULL));
            """)
            deleted_count = cursor.rowcount
            conn.execute("DELETE FROM chunks WHERE task_id NOT IN (SELECT task_id FROM tasks);")
            conn.commit()
            return deleted_count
