"""Funções utilitárias puras (formatação, normalização, barras de progresso)."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from inventory_store import InventoryError

from .config import FARM_TZ, STOCK_CATEGORIES


def resolve_category_value(value: str) -> str:
    if value.startswith("custom:"):
        return value.split(":", 1)[1]
    return STOCK_CATEGORIES[value]


def validate_item_registration_name(value: str) -> str:
    """Exige cadastro padronizado: minúsculo e singular."""
    name = " ".join(value.strip().split())
    if not name:
        raise InventoryError("O nome do item não pode ficar vazio.")
    if name != name.lower():
        raise InventoryError(
            "Escreva o nome do item somente em **minúsculo**. Exemplo: `tesoura`."
        )
    # Regra do servidor: nomes cadastrados não podem ser informados no plural.
    # Para os itens usados no estoque, plural normalmente termina em "s".
    if name.endswith("s"):
        raise InventoryError(
            "Escreva o nome do item no **singular**. Exemplo: `tesoura`, não `tesouras`."
        )
    return name


def format_quantity(quantity: int) -> str:
    return f"{quantity:,}".replace(",", ".")


def format_money(amount: int) -> str:
    return f"R$ {format_quantity(amount)}"


def format_currency(amount: int, currency: str) -> str:
    return f"{'R$' if currency == 'BRL' else 'US$'} {format_quantity(amount)}"


def currency_label(currency: str) -> str:
    return "🇧🇷 Real (R$)" if currency == "BRL" else "🇺🇸 Dólar (US$)"


def format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return discord.utils.format_dt(parsed, style="f")
    except ValueError:
        return value


def normalize_material_name(name: str) -> Optional[str]:
    cleaned = unicodedata.normalize("NFKD", name.casefold())
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    cleaned = " ".join(cleaned.strip().split())

    aliases = {
        "fajuta": "fajuta",
        "fajutas": "fajuta",
        "tesoura": "tesoura",
        "tesouras": "tesoura",
        "mola": "mola",
        "molas": "mola",
        "plastico": "plastico",
        "plasticos": "plastico",
        "panela": "panela",
        "panela velha": "panela",
        "panelas": "panela",
        "itens de casa": "itens_casa",
        "item de casa": "itens_casa",
        "itens casa": "itens_casa",
    }
    return aliases.get(cleaned)


def _norm_discord_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value)


def _format_game_money(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def farm_week_window() -> tuple[str, datetime, datetime]:
    now_local = datetime.now(FARM_TZ)
    monday_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_monday_local = monday_local + timedelta(days=7)
    monday_utc = monday_local.astimezone(timezone.utc)
    return (
        monday_local.date().isoformat(),
        monday_utc,
        next_monday_local,
    )


def progress_bar(value: int, target: int, blocks: int = 10) -> str:
    ratio = min(max(value / target, 0), 1) if target else 0
    filled = round(ratio * blocks)
    return "█" * filled + "░" * (blocks - filled)


def _progress_bar(current: int, goal: int, width: int = 14) -> str:
    if goal <= 0:
        return "░" * width
    ratio = min(max(current / goal, 0.0), 1.0)
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def normalize_category_name(category: str) -> str:
    cleaned = unicodedata.normalize("NFKD", category.casefold())
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    # Remove emojis/symbols/punctuation so "📁 MATERIAL", "Material" and
    # "Materiais" always become the same category.
    cleaned = " ".join(
        "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in cleaned).split()
    )

    if "material" in cleaned:
        return "MATERIAL"
    if cleaned in {"arma", "armas"} or "arma" in cleaned:
        return "ARMAS"
    if cleaned in {"municao", "municoes"} or "municao" in cleaned:
        return "MUNIÇÃO"
    if cleaned in {"droga", "drogas"} or "droga" in cleaned:
        return "DROGA"
    if "dinheiro" in cleaned and "real" in cleaned:
        return "DINHEIRO SUJO • REAL"
    if "dinheiro" in cleaned and ("dolar" in cleaned or "usd" in cleaned):
        return "DINHEIRO SUJO • DÓLAR"
    if "utilitario" in cleaned and "acao" in cleaned:
        return "UTILITÁRIO DE AÇÃO"

    return cleaned.upper() if cleaned else "OUTROS"


def category_emoji(category: str) -> str:
    normalized = normalize_category_name(category)
    if normalized == "ARMAS":
        return "🔫"
    if normalized == "MUNIÇÃO":
        return "📦"
    if normalized == "DROGA":
        return "💊"
    if normalized == "MATERIAL":
        return "🧱"
    if normalized.startswith("DINHEIRO SUJO"):
        return "💵"
    if normalized == "UTILITÁRIO DE AÇÃO":
        return "🧰"
    return "📁"


def parse_modal_quantity(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise InventoryError("A quantidade deve ser um número inteiro.") from error
