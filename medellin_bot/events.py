"""Eventos do bot: on_ready, entrada em servidor, criação/remoção de canais."""

from __future__ import annotations

import asyncio
from datetime import datetime

import discord

from .absences import ensure_absence_panel
from .actions import ensure_action_panel
from .admin import AdminPanelView, ensure_owner_admin_dm
from .bot import bot
from .collective_orders import ensure_collective_order_for
from .config import (
    CASH_ENTRY_CHANNEL_ID,
    CASH_EXIT_CHANNEL_ID,
    FARM_CATEGORY_ID,
    FARM_TZ,
    ORDERS_CHANNEL_ID,
    logger,
)
from .notices import ensure_notice_panel
from .orders import OrdersPanelView, build_orders_panel_embed
from .pvp import (
    notify_previous_pvp_week,
    pvp_weekly_scheduler,
    reconcile_previous_pvp_week_message,
    sync_current_pvp_week_from_actions,
)
from .registration import ensure_registration_panel
from .stores.order_store import order_store


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        logger.info("Connected as %s (%s)", bot.user, bot.user.id)

    if bot.pvp_weekly_task is None or bot.pvp_weekly_task.done():
        import asyncio
        bot.pvp_weekly_task = asyncio.create_task(pvp_weekly_scheduler())

    # Se o bot estava offline na virada da semana, envia o resultado pendente ao voltar.
    now_local = datetime.now(FARM_TZ)
    if now_local.weekday() == 0:
        for guild in bot.guilds:
            try:
                # Corrige a mensagem já enviada mais cedo, mesmo que a semana esteja marcada como notificada.
                corrected = await reconcile_previous_pvp_week_message(guild)
                if not corrected:
                    await notify_previous_pvp_week(guild)
            except Exception:
                logger.exception("Erro ao verificar/corrigir fechamento semanal do PVP.")

    if not bot.commands_synced:
        try:
            await bot.sync_slash_commands()
            bot.commands_synced = True
        except discord.DiscordException:
            logger.exception("Could not sync slash commands during on_ready.")

    # Recalcula a semana atual pelas ações já existentes no Discord.
    for guild in bot.guilds:
        try:
            await sync_current_pvp_week_from_actions(guild)
        except Exception:
            logger.exception("Erro ao recalcular o resultado atual do PVP pelas ações.")

    if not bot.farm_panels_initialized:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.category_id == FARM_CATEGORY_ID:
                    await bot.ensure_farm_panel(channel)
        bot.farm_panels_initialized = True
        logger.info("FARM panels checked/created.")

    for guild in bot.guilds:
        bot.add_view(AdminPanelView(bot, guild.id))
        await ensure_owner_admin_dm(guild)
        await bot.ensure_cash_panel(guild, CASH_ENTRY_CHANNEL_ID, "entry")
        await bot.ensure_cash_panel(guild, CASH_EXIT_CHANNEL_ID, "exit")
        await ensure_action_panel(guild)
        await ensure_registration_panel(guild)
        await ensure_notice_panel(bot)
        await ensure_absence_panel(guild)

        orders_channel = guild.get_channel(ORDERS_CHANNEL_ID)
        if isinstance(orders_channel, discord.TextChannel):
            existing_orders_panel = None
            async for message in orders_channel.history(limit=50):
                if (
                    message.author == bot.user
                    and message.embeds
                    and message.embeds[0].title == "📦 CENTRAL DE PEDIDOS"
                ):
                    existing_orders_panel = message
                    break
            if existing_orders_panel is None:
                await orders_channel.send(
                    embed=build_orders_panel_embed(guild.id),
                    view=OrdersPanelView(),
                )
            else:
                await existing_orders_panel.edit(
                    embed=build_orders_panel_embed(guild.id),
                    view=OrdersPanelView(),
                )


        # Garante que os pedidos oficiais pendentes também apareçam no canal coletivo.
        pending_rows = order_store.conn.execute(
            "SELECT * FROM orders WHERE guild_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (guild.id,),
        ).fetchall()
        for row in pending_rows:
            await ensure_collective_order_for(dict(row), guild)
    logger.info("Cash, orders, action and registration panels checked/created.")


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    try:
        bot.tree.copy_global_to(guild=guild)
        guild_commands = await bot.tree.sync(guild=guild)
        logger.info(
            "Slash commands synced to new guild %s: %d command(s).",
            guild.id,
            len(guild_commands),
        )
    except discord.DiscordException:
        logger.exception("Could not sync slash commands to guild %s.", guild.id)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    if isinstance(channel, discord.TextChannel) and channel.category_id == FARM_CATEGORY_ID:
        await bot.ensure_farm_panel(channel)
        logger.info("New FARM channel initialized: %s (%s)", channel.name, channel.id)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    if isinstance(channel, discord.TextChannel):
        bot.store.delete_farm_panel(channel.guild.id, channel.id)
