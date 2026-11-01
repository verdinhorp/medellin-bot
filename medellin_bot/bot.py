"""Classe do bot (InventoryBot) e a instância única `bot`."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands
from inventory_store import InventoryStore

from .config import (
    CASH_ENTRY_CHANNEL_ID,
    CASH_EXIT_CHANNEL_ID,
    DATABASE_PATH,
    FARM_CATEGORY_ID,
    logger,
)
from .stores.extra_store import extra_store
from .stores.order_store import order_store


class InventoryBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Não exige o Server Members Intent do Developer Portal.
        super().__init__(command_prefix="!", intents=intents)
        self.store = InventoryStore(DATABASE_PATH)
        self.commands_synced = False
        self.farm_panels_initialized = False
        self.panel_messages: dict[tuple[int, int], tuple[int, int]] = {}
        self.pvp_weekly_task = None

    async def setup_hook(self) -> None:
        from .absences import AbsencePanelView
        from .actions import ActionPanelView, ActionResultView
        from .cash import CashEntryView, CashExitView
        from .collective_orders import CollectiveOrderView
        from .notices import GeneralNoticePanelView, ManagementNoticePanelView
        from .orders import OrderControlView, OrdersPanelView
        from .panels import FarmPanelView, InventoryPanelView
        from .registration import RegistrationPanelView, RegistrationReviewView
        self.add_view(InventoryPanelView(self))
        self.add_view(FarmPanelView(self))
        self.add_view(CashEntryView(self))
        self.add_view(CashExitView(self))
        self.add_view(OrdersPanelView())
        self.add_view(OrderControlView())
        self.add_view(CollectiveOrderView())
        self.add_view(ActionPanelView())
        self.add_view(ActionResultView())
        self.add_view(RegistrationPanelView())
        self.add_view(RegistrationReviewView())
        self.add_view(GeneralNoticePanelView())
        self.add_view(ManagementNoticePanelView())
        self.add_view(AbsencePanelView())
        logger.info("Persistent inventory/cash/orders/action/registration/notice/absence panel views registered.")

    async def sync_slash_commands(self) -> None:
        command_definitions = self.tree.get_commands()
        self.tree.clear_commands(guild=None)
        logger.info("Clearing global slash commands.")
        cleared_global_commands = await self.tree.sync()

        for command in command_definitions:
            self.tree.add_command(command)

        guild_command_total = 0
        for guild in self.guilds:
            logger.info("Syncing slash commands to guild %s.", guild.id)
            self.tree.copy_global_to(guild=guild)
            guild_commands = await self.tree.sync(guild=guild)
            guild_command_total += len(guild_commands)

        logger.info(
            "Slash commands synced: global registry cleared (%d remaining); "
            "%d across %d guild(s).",
            len(cleared_global_commands),
            guild_command_total,
            len(self.guilds),
        )

    async def close(self) -> None:
        self.store.close()
        order_store.close()
        extra_store.close()
        await super().close()

    def remember_panel(
        self,
        guild_id: int,
        message: discord.Message,
        channel_scope: int = 0,
    ) -> None:
        self.panel_messages[(guild_id, channel_scope)] = (
            message.channel.id,
            message.id,
        )

    async def refresh_inventory_panel(
        self,
        guild_id: int,
        channel_scope: int = 0,
    ) -> None:
        from .farm_channels import is_pvp_farm_channel
        from .panels import FarmPanelView, InventoryPanelView
        from .stock import build_farm_embed, build_inventory_embed
        panel_reference = self.panel_messages.get((guild_id, channel_scope))
        if panel_reference is None and channel_scope:
            message_id = self.store.get_farm_panel(guild_id, channel_scope)
            if message_id:
                panel_reference = (channel_scope, message_id)

        if panel_reference is None:
            return

        channel_id, message_id = panel_reference
        try:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            message = await channel.fetch_message(message_id)

            if channel_scope:
                await message.edit(
                    embed=build_farm_embed(guild_id, channel_scope, channel.name),
                    view=FarmPanelView(self, pvp_mode=is_pvp_farm_channel(channel)),
                )
            else:
                await message.edit(
                    embed=build_inventory_embed(guild_id, 0),
                    view=InventoryPanelView(self),
                )
        except discord.DiscordException:
            logger.warning(
                "Could not refresh inventory panel for guild %s scope %s.",
                guild_id,
                channel_scope,
                exc_info=True,
            )


    async def refresh_cash_panels(self, guild_id: int) -> None:
        from .cash import CashEntryView, CashExitView, build_cash_panel_embed
        guild = self.get_guild(guild_id)
        if guild is None:
            return
        for channel_id, mode in (
            (CASH_ENTRY_CHANNEL_ID, "entry"),
            (CASH_EXIT_CHANNEL_ID, "exit"),
        ):
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            message_id = self.store.get_cash_panel(guild_id, channel_id)
            if not message_id:
                continue
            try:
                message = await channel.fetch_message(message_id)
                view = CashEntryView(self) if mode == "entry" else CashExitView(self)
                await message.edit(
                    embed=build_cash_panel_embed(guild_id, mode),
                    view=view,
                )
            except discord.DiscordException:
                logger.warning(
                    "Não foi possível atualizar painel do caixa em #%s.",
                    channel.name,
                    exc_info=True,
                )

    async def ensure_cash_panel(
        self,
        guild: discord.Guild,
        channel_id: int,
        mode: str,
    ) -> None:
        from .cash import CashEntryView, CashExitView, build_cash_panel_embed
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Canal do caixa %s não encontrado.", channel_id)
            return

        message: Optional[discord.Message] = None
        saved_message_id = self.store.get_cash_panel(guild.id, channel_id)

        if saved_message_id:
            try:
                message = await channel.fetch_message(saved_message_id)
            except discord.DiscordException:
                message = None

        view = CashEntryView(self) if mode == "entry" else CashExitView(self)

        if message is None:
            try:
                message = await channel.send(
                    embed=build_cash_panel_embed(guild.id, mode),
                    view=view,
                )
                self.store.set_cash_panel(guild.id, channel_id, message.id)
            except discord.DiscordException:
                logger.exception("Erro ao criar painel do caixa em #%s.", channel.name)
                return
        else:
            try:
                await message.edit(
                    embed=build_cash_panel_embed(guild.id, mode),
                    view=view,
                )
            except discord.DiscordException:
                logger.exception("Erro ao atualizar painel do caixa em #%s.", channel.name)

    async def ensure_farm_panel(self, channel: discord.TextChannel) -> None:
        from .panels import FarmPanelView
        from .stock import build_farm_embed_fresh
        if channel.category_id != FARM_CATEGORY_ID:
            return

        message: Optional[discord.Message] = None
        saved_message_id = self.store.get_farm_panel(channel.guild.id, channel.id)

        if saved_message_id:
            try:
                message = await channel.fetch_message(saved_message_id)
                fresh_embed, fresh_pvp = await build_farm_embed_fresh(
                    channel.guild.id, channel.id, channel.name
                )
                await message.edit(
                    embed=fresh_embed,
                    view=FarmPanelView(self, pvp_mode=fresh_pvp),
                )
            except discord.NotFound:
                message = None
            except discord.Forbidden:
                logger.warning("Sem permissão para editar painel em #%s.", channel.name)
                return
            except discord.DiscordException:
                logger.exception("Erro ao recuperar painel da farm em #%s.", channel.name)
                return

        if message is None:
            try:
                fresh_embed, fresh_pvp = await build_farm_embed_fresh(
                    channel.guild.id, channel.id, channel.name
                )
                message = await channel.send(
                    embed=fresh_embed,
                    view=FarmPanelView(self, pvp_mode=fresh_pvp),
                )
            except discord.Forbidden:
                logger.warning("Sem permissão para criar painel em #%s.", channel.name)
                return
            except discord.DiscordException:
                logger.exception("Erro ao criar painel da farm em #%s.", channel.name)
                return

            self.store.set_farm_panel(channel.guild.id, channel.id, message.id)

        self.remember_panel(channel.guild.id, message, channel.id)


bot = InventoryBot()
