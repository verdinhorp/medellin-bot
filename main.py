"""Ponto de entrada do bot Medellín."""

from medellin_bot.config import TOKEN
from medellin_bot.bot import bot
from medellin_bot import events, slash_commands  # noqa: F401

# TESTE: Mostra no terminal o token exato que o Python leu
print(f"DEBUG TOKEN LIDO: >>>{TOKEN}<<<")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN não está configurado.")

bot.run(TOKEN)