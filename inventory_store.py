"""SQLite persistence layer for the Discord inventory bot."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Optional


LEGACY_CATEGORY = "Sem categoria"


class InventoryError(ValueError):
    """Base error for invalid inventory operations."""


class ItemNotFoundError(InventoryError):
    """Raised when an operation references an unknown item."""


class InsufficientStockError(InventoryError):
    """Raised when a withdrawal is larger than the current stock."""


class InventoryStore:
    def __init__(self, database_path: str) -> None:
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.lock = RLock()
        self._create_schema()

    def _table_exists(self, table_name: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _columns(self, table_name: str) -> set[str]:
        if not self._table_exists(table_name):
            return set()
        return {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table_name})")
        }

    def _migrate_channel_scope(self) -> None:
        """Upgrade the old guild-only database to guild + channel scope safely."""
        item_columns = self._columns("inventory_items")
        if not item_columns or "channel_id" in item_columns:
            return

        movement_columns = self._columns("inventory_movements")

        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN")

            self.connection.execute(
                """
                CREATE TABLE inventory_items_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT 'Sem categoria',
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, channel_id, normalized_name)
                )
                """
            )
            self.connection.execute(
                """
                INSERT INTO inventory_items_v2
                    (id, guild_id, channel_id, category, name, normalized_name,
                     quantity, created_at, updated_at)
                SELECT
                    id, guild_id, 0,
                    COALESCE(category, 'Sem categoria'),
                    name, normalized_name, quantity, created_at, updated_at
                FROM inventory_items
                """
            )

            if movement_columns:
                self.connection.execute(
                    """
                    CREATE TABLE inventory_movements_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        item_id INTEGER NOT NULL,
                        category TEXT NOT NULL DEFAULT 'Sem categoria',
                        item_name TEXT NOT NULL,
                        normalized_item_name TEXT NOT NULL,
                        action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                        user_id INTEGER NOT NULL,
                        user_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (item_id) REFERENCES inventory_items_v2 (id) ON DELETE CASCADE
                    )
                    """
                )
                normalized_expr = (
                    "normalized_item_name"
                    if "normalized_item_name" in movement_columns
                    else "lower(trim(item_name))"
                )
                category_expr = (
                    "COALESCE(category, 'Sem categoria')"
                    if "category" in movement_columns
                    else "'Sem categoria'"
                )
                self.connection.execute(
                    f"""
                    INSERT INTO inventory_movements_v2
                        (id, guild_id, channel_id, item_id, category, item_name,
                         normalized_item_name, action, quantity, balance_after,
                         user_id, user_name, created_at)
                    SELECT
                        id, guild_id, 0, item_id, {category_expr}, item_name,
                        {normalized_expr}, action, quantity, balance_after,
                        user_id, user_name, created_at
                    FROM inventory_movements
                    """
                )
                self.connection.execute("DROP TABLE inventory_movements")

            self.connection.execute("DROP TABLE inventory_items")
            self.connection.execute("ALTER TABLE inventory_items_v2 RENAME TO inventory_items")
            if movement_columns:
                self.connection.execute(
                    "ALTER TABLE inventory_movements_v2 RENAME TO inventory_movements"
                )

            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def _create_schema(self) -> None:
        with self.lock:
            self._migrate_channel_scope()

            with self.connection:
                self.connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS inventory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        category TEXT NOT NULL DEFAULT 'Sem categoria',
                        name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (guild_id, channel_id, normalized_name)
                    );

                    CREATE TABLE IF NOT EXISTS inventory_movements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL DEFAULT 0,
                        item_id INTEGER NOT NULL,
                        category TEXT NOT NULL DEFAULT 'Sem categoria',
                        item_name TEXT NOT NULL,
                        normalized_item_name TEXT NOT NULL,
                        action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                        user_id INTEGER NOT NULL,
                        user_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (item_id) REFERENCES inventory_items (id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_items_scope_name
                        ON inventory_items (guild_id, channel_id, normalized_name);

                    CREATE INDEX IF NOT EXISTS idx_movements_scope_created
                        ON inventory_movements (guild_id, channel_id, created_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_movements_scope_item_created
                        ON inventory_movements
                        (guild_id, channel_id, normalized_item_name, created_at DESC);

                    CREATE TABLE IF NOT EXISTS farm_money_progress (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        week_start TEXT NOT NULL,
                        amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, channel_id, week_start)
                    );

                    CREATE TABLE IF NOT EXISTS farm_panels (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, channel_id)
                    );

                    CREATE TABLE IF NOT EXISTS farm_daily_progress (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        progress_date TEXT NOT NULL,
                        dirty_money INTEGER NOT NULL DEFAULT 0 CHECK (dirty_money >= 0),
                        chemical_routes INTEGER NOT NULL DEFAULT 0 CHECK (chemical_routes >= 0),
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, channel_id, progress_date)
                    );

                    CREATE TABLE IF NOT EXISTS cash_movements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
                        amount INTEGER NOT NULL CHECK (amount > 0),
                        reason TEXT NOT NULL,
                        balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                        user_id INTEGER NOT NULL,
                        user_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS cash_panels (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, channel_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_cash_movements_guild_created
                        ON cash_movements (guild_id, created_at DESC);

                    CREATE TABLE IF NOT EXISTS custom_stock_categories (
                        guild_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, normalized_name)
                    );

                    CREATE TABLE IF NOT EXISTS stock_catalog_items (
                        guild_id INTEGER NOT NULL,
                        category_name TEXT NOT NULL,
                        normalized_category TEXT NOT NULL,
                        name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (guild_id, normalized_category, normalized_name)
                    );

                    CREATE INDEX IF NOT EXISTS idx_stock_catalog_guild_category
                        ON stock_catalog_items (guild_id, normalized_category, normalized_name);
                    """
                )

                cash_columns = {
                    row["name"]
                    for row in self.connection.execute("PRAGMA table_info(cash_movements)").fetchall()
                }
                if "currency" not in cash_columns:
                    self.connection.execute(
                        "ALTER TABLE cash_movements ADD COLUMN currency TEXT NOT NULL DEFAULT 'BRL'"
                    )

                # Additive migrations for databases that already had channel scope.
                item_columns = self._columns("inventory_items")
                if "category" not in item_columns:
                    self.connection.execute(
                        "ALTER TABLE inventory_items ADD COLUMN category TEXT NOT NULL DEFAULT 'Sem categoria'"
                    )

                movement_columns = self._columns("inventory_movements")
                if "category" not in movement_columns:
                    self.connection.execute(
                        "ALTER TABLE inventory_movements ADD COLUMN category TEXT NOT NULL DEFAULT 'Sem categoria'"
                    )
                if "normalized_item_name" not in movement_columns:
                    self.connection.execute(
                        "ALTER TABLE inventory_movements ADD COLUMN normalized_item_name TEXT NOT NULL DEFAULT ''"
                    )
                    self.connection.execute(
                        """
                        UPDATE inventory_movements
                        SET normalized_item_name = lower(trim(item_name))
                        WHERE normalized_item_name = ''
                        """
                    )

                # Migração: itens que já existiam no estoque viram itens cadastrados no catálogo.
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO stock_catalog_items
                        (guild_id, category_name, normalized_category, name, normalized_name, created_at)
                    SELECT guild_id, category, lower(trim(category)), name, normalized_name, created_at
                    FROM inventory_items
                    """
                )

    @staticmethod
    def normalize_name(item_name: str) -> str:
        normalized = " ".join(item_name.strip().split()).casefold()
        if not normalized:
            raise InventoryError("O nome do item não pode ficar vazio.")
        if len(normalized) > 100:
            raise InventoryError("O nome do item deve ter no máximo 100 caracteres.")
        return normalized

    @staticmethod
    def display_name(item_name: str) -> str:
        name = " ".join(item_name.strip().split())
        if not name:
            raise InventoryError("O nome do item não pode ficar vazio.")
        if len(name) > 100:
            raise InventoryError("O nome do item deve ter no máximo 100 caracteres.")
        return name

    @staticmethod
    def display_category(category: str) -> str:
        value = " ".join(category.strip().split())
        if not value:
            raise InventoryError("A categoria não pode ficar vazia.")
        if len(value) > 100:
            raise InventoryError("A categoria deve ter no máximo 100 caracteres.")
        return value

    @staticmethod
    def canonical_category(category: str) -> str:
        """Normaliza a categoria sem alterar o nome escolhido no painel."""
        return InventoryStore.display_category(category)

    @staticmethod
    def validate_quantity(quantity: int) -> None:
        if quantity <= 0:
            raise InventoryError("A quantidade deve ser maior que zero.")
        if quantity > 2_147_483_647:
            raise InventoryError("A quantidade informada é grande demais.")

    def get_item(
        self,
        guild_id: int,
        item_name: str,
        channel_id: int = 0,
    ) -> Optional[dict[str, Any]]:
        normalized = self.normalize_name(item_name)
        with self.lock:
            row = self.connection.execute(
                """
                SELECT id, guild_id, channel_id, category, name, quantity, created_at, updated_at
                FROM inventory_items
                WHERE guild_id = ? AND channel_id = ? AND normalized_name = ?
                """,
                (guild_id, channel_id, normalized),
            ).fetchone()
        return dict(row) if row else None

    def list_items(self, guild_id: int, channel_id: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT id, guild_id, channel_id, category, name, quantity, created_at, updated_at
                FROM inventory_items
                WHERE guild_id = ? AND channel_id = ?
                ORDER BY lower(category) ASC, lower(name) ASC
                """,
                (guild_id, channel_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_stock(
        self, guild_id: int, item_name: str, quantity: int, user_id: int,
        user_name: str, category: Optional[str] = None, channel_id: int = 0,
    ) -> dict[str, Any]:
        """Movimenta um único baú do servidor; channel_id registra apenas a origem."""
        self.validate_quantity(quantity)
        name = self.display_name(item_name)
        normalized = self.normalize_name(name)
        requested_category = self.canonical_category(category) if category is not None else None
        timestamp = self._timestamp()
        origin_channel_id = channel_id
        stock_channel_id = 0

        with self.lock, self.connection:
            row = self.connection.execute(
                """SELECT id, category, name, quantity FROM inventory_items
                   WHERE guild_id = ? AND channel_id = ? AND normalized_name = ?""",
                (guild_id, stock_channel_id, normalized),
            ).fetchone()
            if row:
                new_quantity = row["quantity"] + quantity
                stored_category = requested_category or row["category"]
                self.connection.execute(
                    "UPDATE inventory_items SET category = ?, quantity = ?, updated_at = ? WHERE id = ?",
                    (stored_category, new_quantity, timestamp, row["id"]),
                )
                item_id, stored_name = row["id"], row["name"]
            else:
                new_quantity = quantity
                stored_category = requested_category or LEGACY_CATEGORY
                cursor = self.connection.execute(
                    """INSERT INTO inventory_items
                       (guild_id, channel_id, category, name, normalized_name, quantity, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (guild_id, stock_channel_id, stored_category, name, normalized,
                     quantity, timestamp, timestamp),
                )
                item_id, stored_name = cursor.lastrowid, name

            self._record_movement(
                guild_id=guild_id, channel_id=origin_channel_id, item_id=item_id,
                category=stored_category, item_name=stored_name,
                normalized_item_name=normalized, action="add", quantity=quantity,
                balance_after=new_quantity, user_id=user_id, user_name=user_name,
                created_at=timestamp,
            )
        return {"id": item_id, "category": stored_category, "name": stored_name,
                "quantity": new_quantity, "channel_id": origin_channel_id}

    def remove_stock(
        self, guild_id: int, item_name: str, quantity: int, user_id: int,
        user_name: str, channel_id: int = 0,
    ) -> dict[str, Any]:
        """Retira do único baú do servidor; channel_id registra apenas a origem."""
        self.validate_quantity(quantity)
        normalized = self.normalize_name(item_name)
        timestamp = self._timestamp()
        origin_channel_id = channel_id
        stock_channel_id = 0

        with self.lock, self.connection:
            row = self.connection.execute(
                """SELECT id, category, name, quantity FROM inventory_items
                   WHERE guild_id = ? AND channel_id = ? AND normalized_name = ?""",
                (guild_id, stock_channel_id, normalized),
            ).fetchone()
            if row is None:
                raise ItemNotFoundError(f"O item **{item_name.strip()}** não está cadastrado.")
            if row["quantity"] < quantity:
                raise InsufficientStockError(
                    f"Saldo de **{row['name']}**: {row['quantity']} unidade(s). Você tentou retirar {quantity}."
                )
            new_quantity = row["quantity"] - quantity
            self.connection.execute(
                "UPDATE inventory_items SET quantity = ?, updated_at = ? WHERE id = ?",
                (new_quantity, timestamp, row["id"]),
            )
            self._record_movement(
                guild_id=guild_id, channel_id=origin_channel_id, item_id=row["id"],
                category=row["category"], item_name=row["name"],
                normalized_item_name=normalized, action="remove", quantity=quantity,
                balance_after=new_quantity, user_id=user_id, user_name=user_name,
                created_at=timestamp,
            )
        return {"id": row["id"], "category": row["category"], "name": row["name"],
                "quantity": new_quantity, "channel_id": origin_channel_id}

    def list_history(
        self,
        guild_id: int,
        item_name: Optional[str] = None,
        limit: int = 10,
        channel_id: int = 0,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise InventoryError("O limite deve ficar entre 1 e 20.")

        with self.lock:
            if item_name:
                normalized = self.normalize_name(item_name)
                rows = self.connection.execute(
                    """
                    SELECT category, item_name, normalized_item_name, action,
                           quantity, balance_after, user_name, created_at
                    FROM inventory_movements
                    WHERE guild_id = ? AND channel_id = ? AND normalized_item_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (guild_id, channel_id, normalized, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """
                    SELECT category, item_name, normalized_item_name, action,
                           quantity, balance_after, user_name, created_at
                    FROM inventory_movements
                    WHERE guild_id = ? AND channel_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (guild_id, channel_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_additions_since(
        self,
        guild_id: int,
        channel_id: int,
        since_iso: str,
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT item_name, normalized_item_name, quantity, created_at
                FROM inventory_movements
                WHERE guild_id = ?
                  AND channel_id = ?
                  AND action = 'add'
                  AND created_at >= ?
                ORDER BY id ASC
                """,
                (guild_id, channel_id, since_iso),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_weekly_money(
        self,
        guild_id: int,
        channel_id: int,
        week_start: str,
        amount: int,
    ) -> int:
        self.validate_quantity(amount)
        timestamp = self._timestamp()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO farm_money_progress
                    (guild_id, channel_id, week_start, amount, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id, week_start)
                DO UPDATE SET
                    amount = farm_money_progress.amount + excluded.amount,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, week_start, amount, timestamp),
            )
            row = self.connection.execute(
                """
                SELECT amount
                FROM farm_money_progress
                WHERE guild_id = ? AND channel_id = ? AND week_start = ?
                """,
                (guild_id, channel_id, week_start),
            ).fetchone()
        return int(row["amount"])

    def get_weekly_money(
        self,
        guild_id: int,
        channel_id: int,
        week_start: str,
    ) -> int:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT amount
                FROM farm_money_progress
                WHERE guild_id = ? AND channel_id = ? AND week_start = ?
                """,
                (guild_id, channel_id, week_start),
            ).fetchone()
        return int(row["amount"]) if row else 0

    def get_daily_farm_progress(
        self, guild_id: int, channel_id: int, progress_date: str
    ) -> dict[str, int]:
        with self.lock:
            row = self.connection.execute(
                """SELECT dirty_money, chemical_routes
                   FROM farm_daily_progress
                   WHERE guild_id = ? AND channel_id = ? AND progress_date = ?""",
                (guild_id, channel_id, progress_date),
            ).fetchone()
        if row is None:
            return {"dirty_money": 0, "chemical_routes": 0}
        return {
            "dirty_money": int(row["dirty_money"]),
            "chemical_routes": int(row["chemical_routes"]),
        }

    def add_daily_dirty_money(
        self, guild_id: int, channel_id: int, progress_date: str, amount: int
    ) -> int:
        self.validate_quantity(amount)
        timestamp = self._timestamp()
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO farm_daily_progress
                       (guild_id, channel_id, progress_date, dirty_money, chemical_routes, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?)
                   ON CONFLICT(guild_id, channel_id, progress_date)
                   DO UPDATE SET dirty_money = farm_daily_progress.dirty_money + excluded.dirty_money,
                                 updated_at = excluded.updated_at""",
                (guild_id, channel_id, progress_date, amount, timestamp),
            )
        return self.get_daily_farm_progress(guild_id, channel_id, progress_date)["dirty_money"]

    def add_daily_chemical_route(
        self, guild_id: int, channel_id: int, progress_date: str, routes: int = 1
    ) -> int:
        self.validate_quantity(routes)
        timestamp = self._timestamp()
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO farm_daily_progress
                       (guild_id, channel_id, progress_date, dirty_money, chemical_routes, updated_at)
                   VALUES (?, ?, ?, 0, ?, ?)
                   ON CONFLICT(guild_id, channel_id, progress_date)
                   DO UPDATE SET chemical_routes = farm_daily_progress.chemical_routes + excluded.chemical_routes,
                                 updated_at = excluded.updated_at""",
                (guild_id, channel_id, progress_date, routes, timestamp),
            )
        return self.get_daily_farm_progress(guild_id, channel_id, progress_date)["chemical_routes"]

    def set_farm_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        timestamp = self._timestamp()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO farm_panels (guild_id, channel_id, message_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    message_id = excluded.message_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, message_id, timestamp),
            )

    def get_farm_panel(self, guild_id: int, channel_id: int) -> Optional[int]:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT message_id
                FROM farm_panels
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        return int(row["message_id"]) if row else None

    def delete_farm_panel(self, guild_id: int, channel_id: int) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "DELETE FROM farm_panels WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )


    @staticmethod
    def normalize_currency(currency: str) -> str:
        value = currency.strip().upper()
        aliases = {"REAL": "BRL", "R$": "BRL", "BRL": "BRL",
                   "DOLAR": "USD", "DÓLAR": "USD", "US$": "USD", "USD": "USD"}
        if value not in aliases:
            raise InventoryError("Moeda inválida. Use Real ou Dólar.")
        return aliases[value]

    def get_cash_balance(self, guild_id: int, currency: str = "BRL") -> int:
        currency = self.normalize_currency(currency)
        with self.lock:
            row = self.connection.execute(
                """
                SELECT balance_after FROM cash_movements
                WHERE guild_id = ? AND currency = ?
                ORDER BY id DESC LIMIT 1
                """,
                (guild_id, currency),
            ).fetchone()
        return int(row["balance_after"]) if row else 0

    def add_cash(
        self, guild_id: int, amount: int, reason: str, user_id: int,
        user_name: str, currency: str = "BRL",
    ) -> dict[str, Any]:
        self.validate_quantity(amount)
        currency = self.normalize_currency(currency)
        reason_value = " ".join(reason.strip().split())
        if not reason_value:
            raise InventoryError("O motivo não pode ficar vazio.")
        if len(reason_value) > 200:
            raise InventoryError("O motivo deve ter no máximo 200 caracteres.")
        timestamp = self._timestamp()
        with self.lock, self.connection:
            balance_before = self.get_cash_balance(guild_id, currency)
            balance_after = balance_before + amount
            self.connection.execute(
                """INSERT INTO cash_movements
                   (guild_id, action, amount, reason, balance_after,
                    user_id, user_name, created_at, currency)
                   VALUES (?, 'add', ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, amount, reason_value, balance_after,
                 user_id, user_name, timestamp, currency),
            )
        return {"action":"add","amount":amount,"reason":reason_value,
                "balance_before":balance_before,"balance_after":balance_after,
                "created_at":timestamp,"currency":currency}

    def remove_cash(
        self, guild_id: int, amount: int, reason: str, user_id: int,
        user_name: str, currency: str = "BRL",
    ) -> dict[str, Any]:
        self.validate_quantity(amount)
        currency = self.normalize_currency(currency)
        reason_value = " ".join(reason.strip().split())
        if not reason_value:
            raise InventoryError("O motivo não pode ficar vazio.")
        if len(reason_value) > 200:
            raise InventoryError("O motivo deve ter no máximo 200 caracteres.")
        timestamp = self._timestamp()
        with self.lock, self.connection:
            balance_before = self.get_cash_balance(guild_id, currency)
            if amount > balance_before:
                symbol = "R$" if currency == "BRL" else "US$"
                raise InsufficientStockError(
                    f"Saldo atual: {symbol} {balance_before:,}. Você tentou retirar {symbol} {amount:,}."
                )
            balance_after = balance_before - amount
            self.connection.execute(
                """INSERT INTO cash_movements
                   (guild_id, action, amount, reason, balance_after,
                    user_id, user_name, created_at, currency)
                   VALUES (?, 'remove', ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, amount, reason_value, balance_after,
                 user_id, user_name, timestamp, currency),
            )
        return {"action":"remove","amount":amount,"reason":reason_value,
                "balance_before":balance_before,"balance_after":balance_after,
                "created_at":timestamp,"currency":currency}

    def list_cash_history(
        self,
        guild_id: int,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise InventoryError("O limite deve ficar entre 1 e 20.")
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT action, amount, reason, balance_after,
                       user_name, created_at
                FROM cash_movements
                WHERE guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_cash_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        timestamp = self._timestamp()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO cash_panels (guild_id, channel_id, message_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET
                    message_id = excluded.message_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, message_id, timestamp),
            )

    def get_cash_panel(self, guild_id: int, channel_id: int) -> Optional[int]:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT message_id
                FROM cash_panels
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        return int(row["message_id"]) if row else None

    def list_catalog_items(self, guild_id: int, category_name: str) -> list[str]:
        normalized_category = self.normalize_name(category_name)
        with self.lock:
            rows = self.connection.execute(
                """SELECT name FROM stock_catalog_items
                   WHERE guild_id = ? AND normalized_category = ?
                   ORDER BY normalized_name ASC""",
                (guild_id, normalized_category),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def add_catalog_item(self, guild_id: int, category_name: str, item_name: str) -> str:
        category = self.display_category(category_name)
        normalized_category = self.normalize_name(category)
        name = self.display_name(item_name)
        normalized_name = self.normalize_name(name)
        with self.lock, self.connection:
            total = self.connection.execute(
                "SELECT COUNT(*) AS total FROM stock_catalog_items WHERE guild_id = ? AND normalized_category = ?",
                (guild_id, normalized_category),
            ).fetchone()["total"]
            if int(total) >= 25:
                raise InventoryError("Essa categoria já atingiu o limite de 25 itens para o menu do Discord.")

            duplicate_anywhere = self.connection.execute(
                "SELECT category_name FROM stock_catalog_items WHERE guild_id = ? AND normalized_name = ? LIMIT 1",
                (guild_id, normalized_name),
            ).fetchone()
            if duplicate_anywhere:
                raise InventoryError(
                    f"O item **{name}** já está cadastrado em **{duplicate_anywhere['category_name']}**."
                )

            self.connection.execute(
                """INSERT INTO stock_catalog_items
                   (guild_id, category_name, normalized_category, name, normalized_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, category, normalized_category, name, normalized_name, self._timestamp()),
            )
        return name

    def remove_catalog_item(self, guild_id: int, category_name: str, item_name: str) -> str:
        """Exclui definitivamente o item do catálogo, estoque e histórico."""
        normalized_category = self.normalize_name(category_name)
        normalized_name = self.normalize_name(item_name)

        with self.lock, self.connection:
            row = self.connection.execute(
                """SELECT name FROM stock_catalog_items
                   WHERE guild_id = ? AND normalized_category = ? AND normalized_name = ?""",
                (guild_id, normalized_category, normalized_name),
            ).fetchone()
            if row is None:
                raise InventoryError(
                    f"O item **{item_name.strip()}** não está cadastrado nessa categoria."
                )

            stored_name = str(row["name"])

            # Histórico primeiro: funciona também em bancos antigos.
            self.connection.execute(
                """DELETE FROM inventory_movements
                   WHERE guild_id = ? AND normalized_item_name = ?""",
                (guild_id, normalized_name),
            )
            # Estoque geral atual.
            self.connection.execute(
                """DELETE FROM inventory_items
                   WHERE guild_id = ? AND normalized_name = ?""",
                (guild_id, normalized_name),
            )
            # Catálogo usado pelos menus.
            self.connection.execute(
                """DELETE FROM stock_catalog_items
                   WHERE guild_id = ? AND normalized_category = ? AND normalized_name = ?""",
                (guild_id, normalized_category, normalized_name),
            )

        return stored_name

    def list_custom_categories(self, guild_id: int) -> list[str]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT name FROM custom_stock_categories
                   WHERE guild_id = ? ORDER BY normalized_name ASC""",
                (guild_id,),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def add_custom_category(self, guild_id: int, category_name: str) -> str:
        name = " ".join(category_name.strip().split())
        if not name:
            raise InventoryError("O nome da categoria não pode ficar vazio.")
        if name != name.lower():
            raise InventoryError("Escreva a categoria somente em minúsculo. Exemplo: `contrabando`.")
        if len(name) > 80:
            raise InventoryError("O nome da categoria deve ter no máximo 80 caracteres.")
        normalized = self.normalize_name(name)
        with self.lock, self.connection:
            count = self.connection.execute(
                "SELECT COUNT(*) AS total FROM custom_stock_categories WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()["total"]
            if int(count) >= 18:
                raise InventoryError("Você já atingiu o limite de 18 categorias personalizadas.")
            exists = self.connection.execute(
                "SELECT 1 FROM custom_stock_categories WHERE guild_id = ? AND normalized_name = ?",
                (guild_id, normalized),
            ).fetchone()
            if exists:
                raise InventoryError(f"A categoria **{name}** já existe.")
            self.connection.execute(
                "INSERT INTO custom_stock_categories (guild_id, name, normalized_name, created_at) VALUES (?, ?, ?, ?)",
                (guild_id, name, normalized, self._timestamp()),
            )
        return name

    def remove_custom_category(self, guild_id: int, category_name: str) -> str:
        normalized = self.normalize_name(category_name)
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT name FROM custom_stock_categories WHERE guild_id = ? AND normalized_name = ?",
                (guild_id, normalized),
            ).fetchone()
            if row is None:
                raise InventoryError(f"A categoria **{category_name.strip()}** não existe ou é uma categoria padrão.")
            stored_name = str(row["name"])
            catalog_item = self.connection.execute(
                "SELECT 1 FROM stock_catalog_items WHERE guild_id = ? AND normalized_category = ? LIMIT 1",
                (guild_id, normalized),
            ).fetchone()
            if catalog_item:
                raise InventoryError(
                    f"A categoria **{stored_name}** ainda possui item cadastrado. Exclua os itens dela antes de excluir a categoria."
                )
            in_use = self.connection.execute(
                "SELECT 1 FROM inventory_items WHERE guild_id = ? AND lower(trim(category)) = lower(trim(?)) LIMIT 1",
                (guild_id, stored_name),
            ).fetchone()
            if in_use:
                raise InventoryError(
                    f"A categoria **{stored_name}** ainda possui item no estoque. Zere/remova esses itens antes de excluir a categoria."
                )
            self.connection.execute(
                "DELETE FROM custom_stock_categories WHERE guild_id = ? AND normalized_name = ?",
                (guild_id, normalized),
            )
        return stored_name

    def clear_inventory(self, guild_id: int) -> None:
        """Zera somente estoque/histórico de itens; não altera caixa, painéis ou categorias."""
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM inventory_movements WHERE guild_id = ?", (guild_id,))
            self.connection.execute("DELETE FROM inventory_items WHERE guild_id = ?", (guild_id,))
            self.connection.execute("DELETE FROM farm_money_progress WHERE guild_id = ?", (guild_id,))

    def _record_movement(
        self,
        guild_id: int,
        channel_id: int,
        item_id: int,
        category: str,
        item_name: str,
        normalized_item_name: str,
        action: str,
        quantity: int,
        balance_after: int,
        user_id: int,
        user_name: str,
        created_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO inventory_movements
                (guild_id, channel_id, item_id, category, item_name,
                 normalized_item_name, action, quantity, balance_after,
                 user_id, user_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                item_id,
                category,
                item_name,
                normalized_item_name,
                action,
                quantity,
                balance_after,
                user_id,
                user_name,
                created_at,
            ),
        )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def close(self) -> None:
        with self.lock:
            self.connection.close()
