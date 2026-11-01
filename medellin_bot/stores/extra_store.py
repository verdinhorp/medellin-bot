"""Banco extra: PVP semanal, status PVP e ausências."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import EXTRA_DATABASE_PATH, FARM_TZ
from ..utils import farm_week_window


class ExtraStore:
    """Dados extras do PVP e das ausências, sem depender do inventory_store.py."""
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pvp_status (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('GG','RED')),
                updated_by INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pvp_weekly_result (
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('GG','RED')),
                updated_by INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                notified_at TEXT,
                PRIMARY KEY (guild_id, week_start)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS absences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                reason TEXT NOT NULL,
                start_date TEXT NOT NULL,
                return_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                message_id INTEGER,
                created_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_pvp_status(self, guild_id: int, user_id: int) -> str:
        row = self.conn.execute(
            "SELECT status, updated_at FROM pvp_status WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        if not row:
            return "RED"

        # O status PVP vale somente para a semana atual.
        # Toda segunda-feira, à 00:00 no horário de São Paulo, ele volta
        # automaticamente para RED até a dona definir GG novamente.
        now_local = datetime.now(FARM_TZ)
        monday_local = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        try:
            updated_at = datetime.fromisoformat(str(row["updated_at"]))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            updated_local = updated_at.astimezone(FARM_TZ)
        except (TypeError, ValueError):
            return "RED"

        if updated_local < monday_local:
            return "RED"

        return str(row["status"])

    def set_pvp_status(self, guild_id: int, user_id: int, status: str, updated_by: int) -> None:
        self.conn.execute(
            """INSERT INTO pvp_status(guild_id,user_id,status,updated_by,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(guild_id,user_id) DO UPDATE SET
                 status=excluded.status, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (guild_id, user_id, status, updated_by, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_pvp_weekly_status(self, guild_id: int, week_start: Optional[str] = None) -> str:
        if week_start is None:
            week_start, _, _ = farm_week_window()
        row = self.conn.execute(
            "SELECT status FROM pvp_weekly_result WHERE guild_id=? AND week_start=?",
            (guild_id, week_start),
        ).fetchone()
        # A semana começa em RED e só vira GG quando a dona definir.
        return str(row["status"]) if row else "RED"

    def set_pvp_weekly_status(self, guild_id: int, status: str, updated_by: int) -> None:
        week_start, _, _ = farm_week_window()
        self.conn.execute(
            """INSERT INTO pvp_weekly_result(guild_id,week_start,status,updated_by,updated_at,notified_at)
               VALUES(?,?,?,?,?,NULL)
               ON CONFLICT(guild_id,week_start) DO UPDATE SET
                 status=excluded.status,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (guild_id, week_start, status, updated_by, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def set_pvp_weekly_status_for_week(
        self, guild_id: int, week_start: str, status: str, updated_by: int = 0
    ) -> None:
        """Salva o resultado calculado de uma semana específica."""
        self.conn.execute(
            """INSERT INTO pvp_weekly_result(guild_id,week_start,status,updated_by,updated_at,notified_at)
               VALUES(?,?,?,?,?,NULL)
               ON CONFLICT(guild_id,week_start) DO UPDATE SET
                 status=excluded.status,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (guild_id, week_start, status, updated_by, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def list_pvp_weekly_history(self, guild_id: int, limit: int = 12):
        return self.conn.execute(
            """SELECT * FROM pvp_weekly_result
               WHERE guild_id=?
               ORDER BY week_start DESC
               LIMIT ?""",
            (guild_id, limit),
        ).fetchall()

    def get_pvp_week_result(self, guild_id: int, week_start: str):
        return self.conn.execute(
            "SELECT * FROM pvp_weekly_result WHERE guild_id=? AND week_start=?",
            (guild_id, week_start),
        ).fetchone()

    def ensure_pvp_week_result(self, guild_id: int, week_start: str, status: str = "RED") -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO pvp_weekly_result
               (guild_id,week_start,status,updated_by,updated_at,notified_at)
               VALUES(?,?,?,?,?,NULL)""",
            (guild_id, week_start, status, 0, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def mark_pvp_week_notified(self, guild_id: int, week_start: str) -> None:
        self.conn.execute(
            """UPDATE pvp_weekly_result SET notified_at=?
               WHERE guild_id=? AND week_start=?""",
            (datetime.now(timezone.utc).isoformat(), guild_id, week_start),
        )
        self.conn.commit()

    def pvp_week_was_notified(self, guild_id: int, week_start: str) -> bool:
        row = self.conn.execute(
            "SELECT notified_at FROM pvp_weekly_result WHERE guild_id=? AND week_start=?",
            (guild_id, week_start),
        ).fetchone()
        return bool(row and row["notified_at"])

    def create_absence(self, guild_id: int, user_id: int, user_name: str, reason: str, start_date: str, return_date: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO absences(guild_id,user_id,user_name,reason,start_date,return_date,status,created_at)
               VALUES(?,?,?,?,?,?, 'active', ?)""",
            (guild_id, user_id, user_name, reason, start_date, return_date, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_absence_message(self, absence_id: int, message_id: int) -> None:
        self.conn.execute("UPDATE absences SET message_id=? WHERE id=?", (message_id, absence_id))
        self.conn.commit()

    def get_active_absence(self, guild_id: int, user_id: int):
        return self.conn.execute(
            "SELECT * FROM absences WHERE guild_id=? AND user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (guild_id, user_id),
        ).fetchone()

    def end_absence(self, guild_id: int, user_id: int):
        row = self.get_active_absence(guild_id, user_id)
        if row:
            self.conn.execute(
                "UPDATE absences SET status='ended', ended_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), int(row["id"])),
            )
            self.conn.commit()
        return row

    def list_active_absences(self, guild_id: int):
        return self.conn.execute(
            "SELECT * FROM absences WHERE guild_id=? AND status='active' ORDER BY return_date ASC, id ASC",
            (guild_id,),
        ).fetchall()


extra_store = ExtraStore(EXTRA_DATABASE_PATH)
