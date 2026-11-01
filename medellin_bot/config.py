"""Configuração: variáveis de ambiente, IDs de canais/cargos, metas e constantes."""

from __future__ import annotations

import logging
import os
from datetime import timezone
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Pasta onde fica o main.py (as imagens do registro continuam sendo criadas ao lado dele).

# Ajuste dinâmico de caminho do .env (Funciona local no Windows e no Pterodactyl)
ENV_PATH = os.path.join(BASE_DIR, ".env")
if not os.path.exists(ENV_PATH):
    ENV_PATH = "/home/container/medellin-estoque-bot/.env"

load_dotenv(dotenv_path=ENV_PATH, override=True)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


logger = logging.getLogger("inventory-bot")


TOKEN = os.getenv("DISCORD_TOKEN")


DATABASE_PATH = os.getenv("INVENTORY_DATABASE", "data/inventory.sqlite3")


LOG_ESTOQUE_CHANNEL_ID = 1538553212985020560


FARM_CATEGORY_ID = 1538557607894323241


PVP_ROLE_ID = 1542628992450822236


ABSENCE_CHANNEL_ID = 1542629847451443210


EXTRA_DATABASE_PATH = os.getenv("EXTRA_DATABASE", "data/bot_extra.sqlite3")


CASH_ENTRY_CHANNEL_ID = 1538279975483080714


CASH_EXIT_CHANNEL_ID = 1538280019758022736


CASH_LOG_CHANNEL_ID = 1538658029560528958


ORDERS_CHANNEL_ID = 1539812063231344640


COLLECTIVE_ORDERS_CHANNEL_ID = 1541413072248504320


ORDERS_DATABASE_PATH = os.getenv("ORDERS_DATABASE", "data/orders.sqlite3")


ACTION_CHANNEL_ID = 1539061236095000576


ACTION_HISTORY_CHANNEL_ID = 1554863151575470180


REGISTRATION_CHANNEL_ID = 1538225910665183283


REGISTRATION_REVIEW_CHANNEL_ID = 1538248415245373561


REGISTERED_ROLE_ID = 1538216272523825213


MANAGEMENT_NOTICE_CHANNEL_ID = 1538279637829025992


GENERAL_NOTICE_CHANNEL_ID = 1538283528175747222


NOTICE_ALLOWED_ROLE_IDS = {1538208570280116334,1538209585708671036,1538399341675282512,1538211383059947561,1538212284294242324,1538224450799931562}


REGISTRATION_BANNER_PATH = os.path.join(BASE_DIR, "medellin_registro_banner.png")


REGISTRATION_LOGO_PATH = os.path.join(BASE_DIR, "medellin_registro_logo.png")


MATERIAL_TARGETS = {
    "fajuta": 200,
    "tesoura": 200,
    "mola": 200,
    "plastico": 400,
    "panela": 50,
    "itens_casa": None,
}


MATERIAL_LABELS = {
    "fajuta": "Fajuta",
    "tesoura": "Tesoura",
    "mola": "Mola",
    "plastico": "Plástico",
    "panela": "Panela",
    "itens_casa": "Itens de Casa",
}


DIRTY_MONEY_WEEKLY_TARGET = 400_000


CHEMICAL_ROUTES_DAILY_TARGET = 3


MEDELLIN_RED = discord.Color(0x7A1F1F)


STOCK_CATEGORIES = {
    "armas": "ARMAS",
    "material": "MATERIAL",
    "droga": "DROGA",
    "municao": "MUNIÇÃO",
    "dinheiro_real": "DINHEIRO SUJO • REAL",
    "dinheiro_dolar": "DINHEIRO SUJO • DÓLAR",
    "utilitario_acao": "UTILITÁRIO DE AÇÃO",
}


try:
    FARM_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    FARM_TZ = timezone.utc