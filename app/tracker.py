"""Persistent price-tracking with background polling and email alerts.

Storage: SQLite at data/tracks.db.
Scheduler: a single background thread (daemon) that wakes every CHECK_INTERVAL,
selects active tracks whose last_check_at is older than POLL_INTERVAL, queries
Google Flights via search.cheapest_for, persists, and notifies on drops.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .notifier import send_alert_email
from .search import cheapest_for

log = logging.getLogger("fcf.tracker")

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracks.db"
POLL_INTERVAL = int(os.environ.get("FCF_POLL_INTERVAL_S", str(6 * 3600)))  # 6h default
CHECK_INTERVAL = 60  # how often the loop wakes to look at the queue
_LOCK = threading.Lock()
_STARTED = False


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_conn()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            depart_date TEXT NOT NULL,
            return_date TEXT,
            adults INTEGER NOT NULL DEFAULT 1,
            children INTEGER NOT NULL DEFAULT 0,
            infants INTEGER NOT NULL DEFAULT 0,
            max_stops INTEGER,
            email TEXT,
            threshold_usd REAL,
            best_seen_usd REAL,
            last_check_at TEXT,
            last_price_usd REAL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            price_usd REAL,
            FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_history_track ON history(track_id);
        """)
        c.commit()


@dataclass
class Track:
    id: int
    origin: str
    destination: str
    depart_date: str
    return_date: Optional[str]
    adults: int
    children: int
    infants: int
    max_stops: Optional[int]
    email: Optional[str]
    threshold_usd: Optional[float]
    best_seen_usd: Optional[float]
    last_check_at: Optional[str]
    last_price_usd: Optional[float]
    created_at: str
    active: bool


def _row_to_track(r: sqlite3.Row) -> Track:
    return Track(
        id=r["id"], origin=r["origin"], destination=r["destination"],
        depart_date=r["depart_date"], return_date=r["return_date"],
        adults=r["adults"], children=r["children"], infants=r["infants"],
        max_stops=r["max_stops"], email=r["email"],
        threshold_usd=r["threshold_usd"], best_seen_usd=r["best_seen_usd"],
        last_check_at=r["last_check_at"], last_price_usd=r["last_price_usd"],
        created_at=r["created_at"], active=bool(r["active"]),
    )


def create_track(*, origin: str, destination: str, depart_date: str,
                 return_date: Optional[str], adults: int, children: int,
                 infants: int, max_stops: Optional[int],
                 email: Optional[str], threshold_usd: Optional[float]) -> int:
    _init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_conn()) as c:
        cur = c.execute(
            """INSERT INTO tracks (origin, destination, depart_date, return_date,
                                    adults, children, infants, max_stops,
                                    email, threshold_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (origin, destination, depart_date, return_date, adults, children,
             infants, max_stops, email, threshold_usd, now),
        )
        c.commit()
        return cur.lastrowid


def list_tracks() -> list[dict]:
    _init_db()
    with closing(_conn()) as c:
        rows = c.execute("SELECT * FROM tracks ORDER BY active DESC, created_at DESC").fetchall()
        out = []
        for r in rows:
            t = asdict(_row_to_track(r))
            hist = c.execute(
                "SELECT ts, price_usd FROM history WHERE track_id=? ORDER BY ts DESC LIMIT 20",
                (r["id"],),
            ).fetchall()
            t["history"] = [{"ts": h["ts"], "price_usd": h["price_usd"]} for h in hist]
            out.append(t)
        return out


def delete_track(track_id: int) -> bool:
    _init_db()
    with closing(_conn()) as c:
        cur = c.execute("DELETE FROM tracks WHERE id=?", (track_id,))
        c.commit()
        return cur.rowcount > 0


def _check_track(t: Track) -> None:
    log.info(f"[track {t.id}] checking {t.origin}->{t.destination} {t.depart_date}")
    price = cheapest_for(
        t.origin, t.destination, t.depart_date, t.return_date,
        t.adults, t.children, t.infants, t.max_stops,
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with closing(_conn()) as c:
        c.execute(
            "INSERT INTO history (track_id, ts, price_usd) VALUES (?,?,?)",
            (t.id, now, price),
        )
        new_best = t.best_seen_usd
        if price is not None and (new_best is None or price < new_best):
            new_best = price
        c.execute(
            "UPDATE tracks SET last_check_at=?, last_price_usd=?, best_seen_usd=? WHERE id=?",
            (now, price, new_best, t.id),
        )
        c.commit()

    if price is None:
        return

    # Notify conditions
    notify_reason: Optional[str] = None
    if t.best_seen_usd is not None and price < t.best_seen_usd:
        notify_reason = f"nuevo mínimo histórico (antes ${t.best_seen_usd:.2f})"
    if t.threshold_usd is not None and price <= t.threshold_usd:
        notify_reason = (
            f"precio bajó al/por debajo de tu objetivo de ${t.threshold_usd:.2f}"
        )
    if notify_reason and t.email:
        try:
            send_alert_email(t.email, t, price, notify_reason)
            log.info(f"[track {t.id}] notified {t.email}: {notify_reason}")
        except Exception:
            log.exception(f"[track {t.id}] failed sending notification")


def _due(now_iso: str, last_check_at: Optional[str]) -> bool:
    if last_check_at is None:
        return True
    try:
        last = datetime.fromisoformat(last_check_at)
        now = datetime.fromisoformat(now_iso)
        return (now - last).total_seconds() >= POLL_INTERVAL
    except ValueError:
        return True


def _worker_loop() -> None:
    log.info(f"tracker worker started (poll interval = {POLL_INTERVAL}s)")
    while True:
        try:
            _init_db()
            with closing(_conn()) as c:
                rows = c.execute("SELECT * FROM tracks WHERE active=1").fetchall()
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r in rows:
                t = _row_to_track(r)
                if _due(now_iso, t.last_check_at):
                    try:
                        _check_track(t)
                    except Exception:
                        log.exception(f"[track {t.id}] check failed")
                    # Small spacing between calls so we don't burst Google.
                    time.sleep(2)
        except Exception:
            log.exception("tracker loop iteration failed")
        time.sleep(CHECK_INTERVAL)


def start_worker() -> None:
    """Idempotent: start the background thread exactly once."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        t = threading.Thread(target=_worker_loop, name="fcf-tracker", daemon=True)
        t.start()
