"""SQLite storage for bikes, service log and sound clips (MVP; swap for Postgres later)."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS bikes (
    id TEXT PRIMARY KEY,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER,
    odometer_km REAL DEFAULT 0,
    usage TEXT DEFAULT 'commuter',          -- commuter | gig | diy
    created_at REAL,
    token_hash TEXT                         -- sha256 of the bike's access key; the key itself is never stored
);
CREATE TABLE IF NOT EXISTS service_log (
    id TEXT PRIMARY KEY,
    bike_id TEXT NOT NULL REFERENCES bikes(id),
    item TEXT NOT NULL,                     -- e.g. engine_oil, gear_oil, cvt_cleaning
    odometer_km REAL NOT NULL,
    cost_php REAL,
    notes TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS sound_clips (
    id TEXT PRIMARY KEY,
    bike_id TEXT NOT NULL REFERENCES bikes(id),
    is_baseline INTEGER NOT NULL,
    embedding TEXT NOT NULL,                -- JSON list of floats
    score REAL,
    label TEXT,                             -- mechanic-confirmed finding, for training later
    created_at REAL
);
"""


class Store:
    def __init__(self, path: str = "motorcycle_ai.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(bikes)")}
        if "token_hash" not in cols:  # databases created before access keys existed
            self._conn.execute("ALTER TABLE bikes ADD COLUMN token_hash TEXT")
            self._conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return [dict(r) for r in cur.fetchall()]

    # Bikes
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def add_bike(self, make: str, model: str, year: int | None, odometer_km: float, usage: str) -> dict:
        """Create a bike. Returns it with `token`: the access key, shown only this once."""
        bid = uuid.uuid4().hex[:12]
        token = secrets.token_urlsafe(24)
        self._exec(
            "INSERT INTO bikes (id, make, model, year, odometer_km, usage, created_at, token_hash) VALUES (?,?,?,?,?,?,?,?)",
            (bid, make, model, year, odometer_km, usage, time.time(), self._hash(token)),
        )
        return {**self.get_bike(bid), "token": token}

    def get_bike(self, bike_id: str) -> dict | None:
        rows = self._exec(
            "SELECT id, make, model, year, odometer_km, usage, created_at FROM bikes WHERE id=?", (bike_id,)
        )
        return rows[0] if rows else None

    def check_token(self, bike_id: str, token: str | None) -> bool:
        if not token:
            return False
        rows = self._exec("SELECT token_hash FROM bikes WHERE id=?", (bike_id,))
        return bool(rows) and rows[0]["token_hash"] is not None and secrets.compare_digest(rows[0]["token_hash"], self._hash(token))

    def any_valid_token(self, token: str | None) -> bool:
        if not token:
            return False
        return bool(self._exec("SELECT 1 FROM bikes WHERE token_hash=?", (self._hash(token),)))

    def delete_bike(self, bike_id: str) -> None:
        """Erase a bike and everything linked to it (privacy: right to erasure)."""
        with self._lock:
            for table, col in (("sound_clips", "bike_id"), ("service_log", "bike_id"), ("bikes", "id")):
                self._conn.execute(f"DELETE FROM {table} WHERE {col}=?", (bike_id,))
            self._conn.commit()

    def clip_bike(self, clip_id: str) -> str | None:
        rows = self._exec("SELECT bike_id FROM sound_clips WHERE id=?", (clip_id,))
        return rows[0]["bike_id"] if rows else None

    def update_odometer(self, bike_id: str, km: float) -> None:
        self._exec("UPDATE bikes SET odometer_km=MAX(odometer_km, ?) WHERE id=?", (km, bike_id))

    # Service log
    def add_service(self, bike_id: str, item: str, odometer_km: float, cost_php: float | None, notes: str | None) -> dict:
        sid = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO service_log VALUES (?,?,?,?,?,?,?)",
            (sid, bike_id, item, odometer_km, cost_php, notes, time.time()),
        )
        self.update_odometer(bike_id, odometer_km)
        return {"id": sid, "bike_id": bike_id, "item": item, "odometer_km": odometer_km}

    def service_log(self, bike_id: str) -> list[dict]:
        return self._exec("SELECT * FROM service_log WHERE bike_id=? ORDER BY odometer_km DESC", (bike_id,))

    # Sound
    def add_clip(self, bike_id: str, embedding: np.ndarray, is_baseline: bool, score: float | None) -> str:
        cid = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO sound_clips VALUES (?,?,?,?,?,?,?)",
            (cid, bike_id, int(is_baseline), json.dumps(embedding.tolist()), score, None, time.time()),
        )
        return cid

    def baseline(self, bike_id: str) -> list[np.ndarray]:
        rows = self._exec("SELECT embedding FROM sound_clips WHERE bike_id=? AND is_baseline=1", (bike_id,))
        return [np.array(json.loads(r["embedding"]), dtype=np.float32) for r in rows]

    def recent_clips(self, bike_id: str, limit: int = 5) -> list[dict]:
        return self._exec(
            "SELECT id, is_baseline, score, label, created_at FROM sound_clips WHERE bike_id=? ORDER BY created_at DESC LIMIT ?",
            (bike_id, limit),
        )

    def label_clip(self, clip_id: str, label: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE sound_clips SET label=? WHERE id=?", (label, clip_id))
            self._conn.commit()
            return cur.rowcount > 0
