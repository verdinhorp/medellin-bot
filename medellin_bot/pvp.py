"""PVP semanal: contagem de ações, resultado da semana, notificações e agendador."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord

from .bot import bot
from .config import (
    ACTION_CHANNEL_ID,
    ACTION_HISTORY_CHANNEL_ID,
    FARM_CATEGORY_ID,
    FARM_TZ,
    MEDELLIN_RED,
    PVP_ROLE_ID,
    logger,
)
from .farm_channels import fresh_pvp_state
from .stores.extra_store import extra_store
from .utils import _format_game_money, farm_week_window


def pvp_previous_week_key() -> str:
    now_local = datetime.now(FARM_TZ)
    current_monday = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (current_monday - timedelta(days=7)).date().isoformat()


def build_pvp_history_embed(guild_id: int) -> discord.Embed:
    rows = extra_store.list_pvp_weekly_history(guild_id, limit=12)
    if not rows:
        description = "Ainda não existe histórico semanal do PVP."
    else:
        lines = []
        for row in rows:
            status = str(row["status"])
            emoji = "🟢" if status == "GG" else "🔴"
            week_start = datetime.strptime(str(row["week_start"]), "%Y-%m-%d").date()
            week_end = week_start + timedelta(days=6)
            lines.append(
                f"{emoji} **{status}** • {week_start.strftime('%d/%m/%Y')} a {week_end.strftime('%d/%m/%Y')}"
            )
        description = "\n".join(lines)

    embed = discord.Embed(
        title="📋 HISTÓRICO SEMANAL • PVP",
        description=description,
        color=MEDELLIN_RED,
    )
    embed.set_footer(text="Resultado conjunto do PVP • Medellín")
    return embed


def _action_result_from_embed(embed: discord.Embed) -> Optional[str]:
    """Extrai GG/RED de uma mensagem de ação finalizada."""
    if embed.title != "🎯 AÇÃO FINALIZADA":
        return None
    for field in embed.fields:
        if field.name == "🏆 Resultado":
            value = str(field.value).upper()
            if "GG" in value:
                return "GG"
            if "RED" in value:
                return "RED"
    return None


def _action_currency_from_embed(embed: discord.Embed) -> str:
    """Retorna BRL ou USD. Ações antigas sem moeda explícita são tratadas como BRL."""
    for field in embed.fields:
        if field.name == "💱 Moeda":
            value = str(field.value).casefold()
            if "usd" in value or "dólar" in value or "dolar" in value or "$" in value:
                return "USD"
            return "BRL"

    # Compatibilidade com ações antigas: tenta inferir pelo próprio campo de valor.
    for field in embed.fields:
        if field.name in {"💰 Valor", "💵 Valor"}:
            value = str(field.value).strip().casefold()
            if "$" in value and "r$" not in value:
                return "USD"
            return "BRL"
    return "BRL"


def _action_money_from_embed(embed: discord.Embed) -> Optional[int]:
    """Extrai um valor monetário do campo 💰 Valor da ação.

    Exemplos aceitos: 2.2kk, 2,2kk, 250k, 333.000, 150000.
    Valores de itens (ex.: 278 madeiras ilegais) são ignorados.
    """
    raw = None
    for field in embed.fields:
        if field.name in {"💰 Valor", "💵 Valor"}:
            raw = str(field.value).strip().lower()
            break
    if not raw:
        return None

    text = raw.replace(" ", "").replace("r$", "").replace("$", "")
    # Se houver palavras fora dos sufixos monetários conhecidos, não tratar como dinheiro.
    if re.search(r"[a-zá-ú]", text):
        m = re.fullmatch(r"([0-9][0-9.,]*)\s*(kk|k|m)", text)
        if not m:
            return None
        number_text, suffix = m.groups()
    else:
        m = re.fullmatch(r"([0-9][0-9.,]*)(kk|k|m)?", text)
        if not m:
            return None
        number_text, suffix = m.groups()

    suffix = suffix or ""
    if suffix:
        # Em valores abreviados, ponto/vírgula funciona como separador decimal: 2.2kk = 2,2 milhões.
        normalized = number_text.replace(",", ".")
        # Evita múltiplos separadores decimais inesperados.
        if normalized.count(".") > 1:
            parts = normalized.split(".")
            normalized = "".join(parts[:-1]) + "." + parts[-1]
        try:
            base = float(normalized)
        except ValueError:
            return None
        multiplier = 1_000_000 if suffix in {"kk", "m"} else 1_000
        return int(round(base * multiplier))

    # Sem sufixo, pontos/vírgulas são tratados como separadores de milhar.
    digits = re.sub(r"[.,]", "", number_text)
    if not digits.isdigit():
        return None
    return int(digits)


def _week_utc_bounds(week_start: str) -> tuple[datetime, datetime]:
    start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=FARM_TZ)
    end_local = start_local + timedelta(days=7)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def count_pvp_actions_for_week(guild: discord.Guild, week_start: str) -> tuple[int, int, int, int, int]:
    """Conta GG/RED e soma separadamente os ganhos em BRL e USD das ações GG."""
    channel = guild.get_channel(ACTION_HISTORY_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(ACTION_HISTORY_CHANNEL_ID)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.DiscordException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return 0, 0, 0, 0, 0

    after_utc, before_utc = _week_utc_bounds(week_start)
    gg = 0
    red = 0
    ganhos_brl = 0
    ganhos_usd = 0
    try:
        async for message in channel.history(
            limit=None,
            after=after_utc,
            before=before_utc,
            oldest_first=True,
        ):
            if not message.embeds:
                continue
            result = _action_result_from_embed(message.embeds[0])
            if result == "GG":
                gg += 1
                money = _action_money_from_embed(message.embeds[0])
                if money is not None:
                    currency = _action_currency_from_embed(message.embeds[0])
                    if currency == "USD":
                        ganhos_usd += money
                    else:
                        ganhos_brl += money
            elif result == "RED":
                red += 1
    except discord.DiscordException:
        logger.exception("Não foi possível contar as ações da semana %s.", week_start)

    return gg + red, gg, red, ganhos_brl, ganhos_usd


def pvp_status_from_action_counts(gg: int, red: int) -> str:
    # Vitória semanal quando houver mais GGs do que REDs. Empate permanece RED.
    return "GG" if gg > red else "RED"


async def sync_current_pvp_week_from_actions(guild: discord.Guild) -> tuple[str, int, int, int]:
    week_start, _, _ = farm_week_window()
    total, gg, red, _ganhos_brl, _ganhos_usd = await count_pvp_actions_for_week(guild, week_start)
    status = pvp_status_from_action_counts(gg, red)
    extra_store.set_pvp_weekly_status_for_week(guild.id, week_start, status, 0)
    return status, total, gg, red


def pvp_week_has_member_split(week_start: str) -> bool:
    """Premiação alternada: 05/10/2026 é semana com divisão; depois alterna sim/não."""
    anchor = datetime(2026, 10, 5).date()
    current = datetime.strptime(week_start, "%Y-%m-%d").date()
    weeks = (current - anchor).days // 7
    return weeks % 2 == 0


def build_pvp_weekly_result_embed(
    week_start: str,
    status: str,
    total: int = 0,
    gg: int = 0,
    red: int = 0,
    ganhos_brl: int = 0,
    ganhos_usd: int = 0,
) -> discord.Embed:
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = start + timedelta(days=6)
    if status == "GG":
        title = "🟢 GG • RESULTADO SEMANAL PVP"
        if pvp_week_has_member_split(week_start):
            description = (
                "## DEU GG! 🟢\n"
                "**Vocês fecharam a semana com mais ações ganhas do que perdidas.**\n\n"
                "💰 **Semana de divisão:** 50% do dinheiro de cada ação fica para o caixa da fac "
                "e os outros 50% são divididos entre os membros que participaram daquela ação."
            )
        else:
            description = (
                "## DEU GG! 🟢\n"
                "**Vocês fecharam a semana com mais ações ganhas do que perdidas.**"
            )
        color = discord.Color.green()
    else:
        title = "🔴 RED • RESULTADO SEMANAL PVP"
        description = (
            "## DEU RED! 🔴\n"
            "**Vocês fecharam a semana sem maioria de ações ganhas.**"
        )
        color = discord.Color.red()

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(
        name="📅 Semana",
        value=f"{start.strftime('%d/%m/%Y')} até {end.strftime('%d/%m/%Y')}",
        inline=False,
    )
    if total:
        aproveitamento = (gg / total) * 100
        embed.add_field(name="🎯 Ações", value=str(total), inline=True)
        embed.add_field(name="🟢 GG", value=str(gg), inline=True)
        embed.add_field(name="🔴 RED", value=str(red), inline=True)
        embed.add_field(
            name="📈 Aproveitamento",
            value=f"**{aproveitamento:.1f}%**",
            inline=False,
        )
        if ganhos_brl > 0 or ganhos_usd > 0:
            linhas_ganhos = []
            if ganhos_brl > 0:
                linhas_ganhos.append(f"🇧🇷 **Real:** R$ {_format_game_money(ganhos_brl)}")
            if ganhos_usd > 0:
                linhas_ganhos.append(f"🇺🇸 **Dólar:** $ {_format_game_money(ganhos_usd)}")
            embed.add_field(
                name="💰 Ganho da semana",
                value="\n".join(linhas_ganhos),
                inline=False,
            )
    else:
        embed.add_field(
            name="🎯 Ações",
            value="Nenhuma ação finalizada foi encontrada nesta semana.",
            inline=False,
        )
    embed.set_footer(text="Resultado automático pelas ações finalizadas • Medellín")
    return embed


async def refresh_all_pvp_farm_panels(guild: discord.Guild) -> None:
    for channel in guild.text_channels:
        if channel.category_id != FARM_CATEGORY_ID:
            continue
        try:
            pvp_mode, _ = await fresh_pvp_state(channel)
            if pvp_mode:
                await bot.ensure_farm_panel(channel)
        except discord.DiscordException:
            logger.exception("Não foi possível atualizar painel PVP do canal %s.", channel.id)


async def reconcile_previous_pvp_week_message(guild: discord.Guild) -> bool:
    """Recalcula a semana anterior e corrige a mensagem semanal já enviada pelo bot."""
    week_start = pvp_previous_week_key()
    total, gg, red, ganhos_brl, ganhos_usd = await count_pvp_actions_for_week(guild, week_start)
    status = pvp_status_from_action_counts(gg, red)
    extra_store.set_pvp_weekly_status_for_week(guild.id, week_start, status, 0)

    channel = guild.get_channel(ACTION_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(ACTION_CHANNEL_ID)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.DiscordException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        logger.warning("Canal privado do PVP/Ação %s não encontrado.", ACTION_CHANNEL_ID)
        return False

    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = start + timedelta(days=6)
    expected_week = f"{start.strftime('%d/%m/%Y')} até {end.strftime('%d/%m/%Y')}"
    corrected_embed = build_pvp_weekly_result_embed(
        week_start, status, total, gg, red, ganhos_brl, ganhos_usd
    )

    try:
        async for message in channel.history(limit=250):
            if message.author != bot.user or not message.embeds:
                continue
            embed = message.embeds[0]
            title = embed.title or ""
            if "RESULTADO SEMANAL PVP" not in title:
                continue

            week_matches = False
            for field in embed.fields:
                if field.name == "📅 Semana" and expected_week in str(field.value):
                    week_matches = True
                    break
            if not week_matches:
                continue

            await message.edit(embed=corrected_embed)
            extra_store.mark_pvp_week_notified(guild.id, week_start)
            logger.info(
                "Mensagem semanal do PVP corrigida: semana=%s total=%s gg=%s red=%s status=%s",
                week_start, total, gg, red, status,
            )
            return True
    except discord.DiscordException:
        logger.exception("Não foi possível procurar/editar a mensagem semanal antiga do PVP.")

    return False


async def notify_previous_pvp_week(guild: discord.Guild) -> None:
    week_start = pvp_previous_week_key()
    if extra_store.pvp_week_was_notified(guild.id, week_start):
        return

    # O fechamento agora é calculado automaticamente pelo histórico das ações.
    total, gg, red, ganhos_brl, ganhos_usd = await count_pvp_actions_for_week(guild, week_start)
    status = pvp_status_from_action_counts(gg, red)
    extra_store.set_pvp_weekly_status_for_week(guild.id, week_start, status, 0)

    channel = guild.get_channel(ACTION_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(ACTION_CHANNEL_ID)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.DiscordException:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        logger.warning("Canal privado do PVP/Ação %s não encontrado.", ACTION_CHANNEL_ID)
        return

    role = guild.get_role(PVP_ROLE_ID)
    mention = role.mention if role else "@PVP"
    await channel.send(
        content=mention,
        embed=build_pvp_weekly_result_embed(
            week_start, status, total, gg, red, ganhos_brl, ganhos_usd
        ),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    extra_store.mark_pvp_week_notified(guild.id, week_start)


async def pvp_weekly_scheduler() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        now_local = datetime.now(FARM_TZ)
        next_monday = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=5, second=0, microsecond=0
        )
        if next_monday <= now_local:
            next_monday += timedelta(days=7)

        seconds = max(1, (next_monday - now_local).total_seconds())
        await discord.utils.sleep_until(next_monday.astimezone(timezone.utc))

        for guild in bot.guilds:
            try:
                await notify_previous_pvp_week(guild)
                await refresh_all_pvp_farm_panels(guild)
            except Exception:
                logger.exception("Erro no fechamento semanal do PVP para guild %s.", guild.id)
