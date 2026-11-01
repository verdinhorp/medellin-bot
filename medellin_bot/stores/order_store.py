"""Banco de pedidos e pedidos coletivos."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import discord

from ..config import ORDERS_DATABASE_PATH


class OrderStore:
    """Banco simples e separado para manter os pedidos mesmo após reiniciar o bot."""

    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                family TEXT NOT NULL,
                order_text TEXT NOT NULL,
                requester_id INTEGER NOT NULL,
                requester_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                delivered_by_id INTEGER,
                delivered_by_name TEXT,
                delivered_at TEXT,
                cancelled_by_id INTEGER,
                cancelled_by_name TEXT,
                cancelled_at TEXT
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_order(
        self,
        message_id: int,
        guild_id: int,
        channel_id: int,
        family: str,
        order_text: str,
        requester_id: int,
        requester_name: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO orders (
                message_id, guild_id, channel_id, family, order_text,
                requester_id, requester_name, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                message_id,
                guild_id,
                channel_id,
                family,
                order_text,
                requester_id,
                requester_name,
                datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            ),
        )
        self.conn.commit()

    def get_order(self, message_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM orders WHERE message_id = ?", (message_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_status(self, message_id: int, status: str, user: discord.abc.User) -> Optional[dict]:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if status == "delivered":
            self.conn.execute(
                """
                UPDATE orders
                SET status = 'delivered', delivered_by_id = ?, delivered_by_name = ?, delivered_at = ?
                WHERE message_id = ?
                """,
                (user.id, str(user), now, message_id),
            )
        elif status == "cancelled":
            self.conn.execute(
                """
                UPDATE orders
                SET status = 'cancelled', cancelled_by_id = ?, cancelled_by_name = ?, cancelled_at = ?
                WHERE message_id = ?
                """,
                (user.id, str(user), now, message_id),
            )
        self.conn.commit()
        return self.get_order(message_id)

    def counts(self, guild_id: int) -> tuple[int, int, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS total FROM orders WHERE guild_id = ? GROUP BY status",
            (guild_id,),
        ).fetchall()
        totals = {str(row["status"]): int(row["total"]) for row in rows}
        return (
            totals.get("pending", 0),
            totals.get("delivered", 0),
            totals.get("cancelled", 0),
        )


order_store = OrderStore(ORDERS_DATABASE_PATH)


# ---------------------------------------------------------------------------
# Pedidos coletivos / progresso de produção
# ---------------------------------------------------------------------------
def _parse_amount_and_item(line: str) -> tuple[int, str]:
    """Extrai quantidade e nome do item de uma linha, ex.: '1500 Mola' ou '2k Plástico'."""
    raw = str(line).strip()
    match = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(k|mil)?\b", raw)
    if not match:
        return 0, ""

    number = match.group(1)
    suffix = (match.group(2) or "").casefold()
    if suffix in {"k", "mil"}:
        normalized = number.replace(",", ".")
        try:
            amount = int(float(normalized) * 1000)
        except ValueError:
            amount = 0
    else:
        normalized = number.replace(".", "").replace(",", ".")
        try:
            amount = int(float(normalized))
        except ValueError:
            amount = 0

    item = raw[match.end():].strip(" -–—:|,.")
    if not item:
        item = "Item"
    return max(amount, 0), item[:100]


def _parse_items_from_order_text(text: str) -> list[tuple[int, str]]:
    """
    Lê vários itens do pedido.
    Ex.:
      1500 Mola
      2000 Plástico
    """
    raw = str(text or "").strip()
    if not raw:
        return []

    # Linhas são o formato principal; também aceita ; como separador.
    chunks: list[str] = []
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if line:
            chunks.append(line)

    parsed: list[tuple[int, str]] = []
    for chunk in chunks:
        amount, item = _parse_amount_and_item(chunk)
        if amount > 0:
            parsed.append((amount, item))

    # Compatibilidade com pedido antigo de um item em uma linha.
    if not parsed:
        amount, item = _parse_amount_and_item(raw)
        if amount > 0:
            parsed.append((amount, item))

    return parsed


class CollectiveOrderStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

        # Tabela principal: uma mensagem coletiva para cada pedido oficial.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collective_orders (
                order_message_id INTEGER PRIMARY KEY,
                progress_message_id INTEGER UNIQUE,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                goal_amount INTEGER NOT NULL DEFAULT 0,
                item_name TEXT NOT NULL DEFAULT 'Item',
                current_amount INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT
            )
            """
        )

        # Cada pedido pode ter vários itens, cada um com seu próprio progresso.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collective_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_message_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                item_name TEXT NOT NULL,
                goal_amount INTEGER NOT NULL DEFAULT 0,
                current_amount INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                UNIQUE(order_message_id, position)
            )
            """
        )

        # Contribuições novas, ligadas ao item específico.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collective_item_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_message_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        # Mantém a tabela antiga para não quebrar bancos já existentes.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collective_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def get_by_order(self, order_message_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM collective_orders WHERE order_message_id = ?",
            (order_message_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_by_progress(self, progress_message_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM collective_orders WHERE progress_message_id = ?",
            (progress_message_id,),
        ).fetchone()
        return dict(row) if row else None

    def items(self, order_message_id: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM collective_order_items
            WHERE order_message_id = ?
            ORDER BY position ASC, id ASC
            """,
            (order_message_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_item(self, item_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM collective_order_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def _sync_items_from_order_text(self, order: dict) -> None:
        order_message_id = int(order["message_id"])
        parsed = _parse_items_from_order_text(str(order.get("order_text", "")))
        existing = self.items(order_message_id)

        # Se já houver itens, preserva o progresso e apenas garante que novos itens
        # presentes no texto sejam criados.
        if existing:
            for pos, (goal, item_name) in enumerate(parsed):
                if pos < len(existing):
                    row = existing[pos]
                    self.conn.execute(
                        """
                        UPDATE collective_order_items
                        SET item_name = ?, goal_amount = ?
                        WHERE id = ?
                        """,
                        (item_name, goal, int(row["id"])),
                    )
                else:
                    self.conn.execute(
                        """
                        INSERT INTO collective_order_items
                        (order_message_id, position, item_name, goal_amount, current_amount, completed_at)
                        VALUES (?, ?, ?, ?, 0, NULL)
                        """,
                        (order_message_id, pos, item_name, goal),
                    )
            self.conn.commit()
            return

        # Migração suave do formato antigo de um único item.
        legacy = self.get_by_order(order_message_id)
        legacy_current = int(legacy.get("current_amount", 0)) if legacy else 0

        if not parsed:
            old_goal = int(legacy.get("goal_amount", 0)) if legacy else 0
            old_name = str(legacy.get("item_name", "Item")) if legacy else "Item"
            if old_goal > 0:
                parsed = [(old_goal, old_name)]

        for pos, (goal, item_name) in enumerate(parsed):
            current = legacy_current if pos == 0 else 0
            current = min(current, goal) if goal > 0 else current
            completed_at = (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                if goal > 0 and current >= goal else None
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO collective_order_items
                (order_message_id, position, item_name, goal_amount, current_amount, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_message_id, pos, item_name, goal, current, completed_at),
            )
        self.conn.commit()

    def create(self, order: dict, progress_message_id: int, channel_id: int) -> dict:
        parsed = _parse_items_from_order_text(str(order.get("order_text", "")))
        first_goal = parsed[0][0] if parsed else 0
        first_item = parsed[0][1] if parsed else "Item"

        self.conn.execute(
            """
            INSERT OR REPLACE INTO collective_orders (
                order_message_id, progress_message_id, guild_id, channel_id,
                goal_amount, item_name, current_amount, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?,
                      COALESCE((SELECT current_amount FROM collective_orders WHERE order_message_id = ?), 0),
                      (SELECT completed_at FROM collective_orders WHERE order_message_id = ?))
            """,
            (
                int(order["message_id"]), progress_message_id, int(order["guild_id"]), channel_id,
                first_goal, first_item, int(order["message_id"]), int(order["message_id"]),
            ),
        )
        self.conn.commit()
        self._sync_items_from_order_text(order)
        return self.get_by_order(int(order["message_id"])) or {}

    def ensure_items(self, order: dict) -> list[dict]:
        self._sync_items_from_order_text(order)
        return self.items(int(order["message_id"]))

    def add_to_item(self, item_id: int, user: discord.abc.User, amount: int) -> Optional[dict]:
        item = self.get_item(item_id)
        if not item or amount <= 0:
            return item

        goal = int(item["goal_amount"])
        current = int(item["current_amount"])

        if goal > 0:
            amount = min(amount, max(goal - current, 0))
        if amount <= 0:
            return item

        new_current = current + amount
        completed_at = item.get("completed_at")
        if goal > 0 and new_current >= goal and not completed_at:
            completed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        self.conn.execute(
            """
            UPDATE collective_order_items
            SET current_amount = ?, completed_at = ?
            WHERE id = ?
            """,
            (new_current, completed_at, item_id),
        )
        self.conn.execute(
            """
            INSERT INTO collective_item_contributions
            (order_message_id, item_id, user_id, user_name, amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(item["order_message_id"]),
                item_id,
                user.id,
                str(user),
                amount,
                datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            ),
        )
        self.conn.commit()
        return self.get_item(item_id)

    def contributors(self, order_message_id: int, limit: int = 8) -> list[dict]:
        # Mostra o total de cada pessoa e também quanto ela colocou de cada item.
        # O CTE primeiro escolhe os maiores contribuidores; depois buscamos o
        # detalhamento por item somente dessas pessoas.
        rows = self.conn.execute(
            """
            WITH top_users AS (
                SELECT user_id, SUM(amount) AS total
                FROM collective_item_contributions
                WHERE order_message_id = ?
                GROUP BY user_id
                ORDER BY total DESC, user_id ASC
                LIMIT ?
            )
            SELECT
                c.user_id,
                u.total,
                i.position,
                i.item_name,
                SUM(c.amount) AS item_total
            FROM collective_item_contributions AS c
            JOIN top_users AS u ON u.user_id = c.user_id
            JOIN collective_order_items AS i ON i.id = c.item_id
            WHERE c.order_message_id = ?
            GROUP BY c.user_id, u.total, i.id, i.position, i.item_name
            ORDER BY u.total DESC, c.user_id ASC, i.position ASC, i.id ASC
            """,
            (order_message_id, limit, order_message_id),
        ).fetchall()

        contributors: list[dict] = []
        by_user: dict[int, dict] = {}
        for row in rows:
            user_id = int(row["user_id"])
            entry = by_user.get(user_id)
            if entry is None:
                entry = {
                    "user_id": user_id,
                    "total": int(row["total"]),
                    "items": [],
                }
                by_user[user_id] = entry
                contributors.append(entry)
            entry["items"].append({
                "item_name": str(row["item_name"]),
                "total": int(row["item_total"]),
            })
        return contributors


collective_order_store = CollectiveOrderStore(order_store.conn)
