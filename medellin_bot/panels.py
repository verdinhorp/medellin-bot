"""Painéis com botões de Estoque e FARM."""

from __future__ import annotations

from typing import Optional

import discord

from .bot import InventoryBot, bot
from .cash import AddMoneyModal
from .farm_channels import fresh_pvp_state, is_pvp_farm_channel, scope_channel_id
from .pvp import build_pvp_history_embed, refresh_all_pvp_farm_panels
from .stock import (
    CategorySelectView,
    build_farm_embed,
    build_farm_embed_fresh,
    build_history_embed,
    build_inventory_embed,
)
from .stores.extra_store import extra_store


class InventoryPanelView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot

    @discord.ui.button(
        label="Adicionar",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="inventory_panel:add",
    )
    async def add_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if is_pvp_farm_channel(interaction.channel):
            await interaction.response.send_message("Membros PVP não usam meta individual de materiais neste painel.", ephemeral=True)
            return
        if interaction.guild_id is not None and interaction.message is not None:
            channel_scope = scope_channel_id(interaction)
            bot.remember_panel(interaction.guild_id, interaction.message, channel_scope)
            if channel_scope:
                bot.store.set_farm_panel(
                    interaction.guild_id,
                    channel_scope,
                    interaction.message.id,
                )
        await interaction.response.send_message(
            "Selecione a categoria:", view=CategorySelectView("add", interaction.guild_id), ephemeral=True
        )

    @discord.ui.button(
        label="Retirar",
        emoji="➖",
        style=discord.ButtonStyle.danger,
        custom_id="inventory_panel:remove",
    )
    async def remove_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if is_pvp_farm_channel(interaction.channel):
            await interaction.response.send_message("Membros PVP não usam meta individual de materiais neste painel.", ephemeral=True)
            return
        if interaction.guild_id is not None and interaction.message is not None:
            channel_scope = scope_channel_id(interaction)
            bot.remember_panel(interaction.guild_id, interaction.message, channel_scope)
            if channel_scope:
                bot.store.set_farm_panel(
                    interaction.guild_id,
                    channel_scope,
                    interaction.message.id,
                )
        await interaction.response.send_message(
            "Selecione a categoria:", view=CategorySelectView("remove", interaction.guild_id), ephemeral=True
        )

    @discord.ui.button(
        label="Histórico",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="inventory_panel:history",
    )
    async def history_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return
        channel_scope = scope_channel_id(interaction)
        if interaction.message is not None:
            bot.remember_panel(interaction.guild_id, interaction.message, channel_scope)
            if channel_scope:
                bot.store.set_farm_panel(
                    interaction.guild_id,
                    channel_scope,
                    interaction.message.id,
                )
        await interaction.response.send_message(
            embed=build_history_embed(
                interaction.guild_id,
                channel_scope=channel_scope,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Atualizar",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="inventory_panel:refresh",
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return
        channel_scope = scope_channel_id(interaction)
        if interaction.message is not None:
            bot.remember_panel(interaction.guild_id, interaction.message, channel_scope)
            if channel_scope:
                bot.store.set_farm_panel(
                    interaction.guild_id,
                    channel_scope,
                    interaction.message.id,
                )

        if channel_scope and isinstance(interaction.channel, discord.TextChannel):
            embed = build_farm_embed(
                interaction.guild_id,
                channel_scope,
                interaction.channel.name,
            )
            view = FarmPanelView(self.inventory_bot, pvp_mode=is_pvp_farm_channel(interaction.channel))
        else:
            embed = build_inventory_embed(interaction.guild_id, 0)
            view = InventoryPanelView(self.inventory_bot)

        await interaction.response.edit_message(embed=embed, view=view)


class FarmPanelView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot, pvp_mode: Optional[bool] = None) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot
        # pvp_mode=None é usado somente no registro persistente para manter todos os custom_id.
        # Nas mensagens reais, mostramos só os controles daquele tipo de farm.
        if pvp_mode is True:
            allowed = {"farm_panel:add", "farm_panel:remove", "farm_panel:pvp_history"}
            for item in list(self.children):
                if getattr(item, "custom_id", None) not in allowed:
                    self.remove_item(item)
        elif pvp_mode is False:
            blocked = {"farm_panel:pvp_gg", "farm_panel:pvp_red"}
            for item in list(self.children):
                if getattr(item, "custom_id", None) in blocked:
                    self.remove_item(item)

    @discord.ui.button(
        label="Adicionar",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="farm_panel:add",
    )
    async def add_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is not None and interaction.message is not None:
            bot.remember_panel(
                interaction.guild_id,
                interaction.message,
                interaction.channel_id,
            )
        await interaction.response.send_message(
            "Selecione a categoria:", view=CategorySelectView("add", interaction.guild_id), ephemeral=True
        )

    @discord.ui.button(
        label="Retirar",
        emoji="➖",
        style=discord.ButtonStyle.danger,
        custom_id="farm_panel:remove",
    )
    async def remove_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is not None and interaction.message is not None:
            bot.remember_panel(
                interaction.guild_id,
                interaction.message,
                interaction.channel_id,
            )
        await interaction.response.send_message(
            "Selecione a categoria:", view=CategorySelectView("remove", interaction.guild_id), ephemeral=True
        )

    async def _set_pvp_status(self, interaction: discord.Interaction, status: str) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use este botão dentro de um canal de FARM.", ephemeral=True)
            return
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Somente a dona do servidor pode definir GG ou RED.", ephemeral=True)
            return
        pvp_mode, member = await fresh_pvp_state(interaction.channel)
        if member is None or not pvp_mode:
            await interaction.response.send_message(
                "Este canal não pertence a um membro com o cargo PVP.",
                ephemeral=True,
            )
            return
        extra_store.set_pvp_weekly_status(interaction.guild.id, status, interaction.user.id)
        fresh_embed, _ = await build_farm_embed_fresh(
            interaction.guild.id,
            interaction.channel.id,
            interaction.channel.name,
        )
        await interaction.response.edit_message(
            embed=fresh_embed,
            view=FarmPanelView(self.inventory_bot, pvp_mode=True),
        )
        # Como o resultado é conjunto, atualiza também os outros painéis PVP.
        await refresh_all_pvp_farm_panels(interaction.guild)

    @discord.ui.button(label="GG", emoji="🟢", style=discord.ButtonStyle.success, custom_id="farm_panel:pvp_gg")
    async def pvp_gg_button(self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]) -> None:
        await self._set_pvp_status(interaction, "GG")

    @discord.ui.button(label="RED", emoji="🔴", style=discord.ButtonStyle.danger, custom_id="farm_panel:pvp_red")
    async def pvp_red_button(self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]) -> None:
        await self._set_pvp_status(interaction, "RED")

    @discord.ui.button(
        label="Histórico PVP",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="farm_panel:pvp_history",
    )
    async def pvp_history_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Use este botão dentro do servidor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_pvp_history_embed(interaction.guild_id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Dinheiro Sujo",
        emoji="💰",
        style=discord.ButtonStyle.success,
        custom_id="farm_panel:dirty_money",
    )
    async def dirty_money_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]
    ) -> None:
        await interaction.response.send_modal(AddMoneyModal())

    @discord.ui.button(
        label="Histórico",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="farm_panel:history",
    )
    async def history_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_history_embed(
                interaction.guild_id,
                channel_scope=interaction.channel_id,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Atualizar",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="farm_panel:refresh",
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if (
            interaction.guild_id is None
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um canal de FARM.",
                ephemeral=True,
            )
            return

        if interaction.message is not None:
            bot.remember_panel(
                interaction.guild_id,
                interaction.message,
                interaction.channel_id,
            )
        await interaction.response.defer(ephemeral=True)
        fresh_embed, fresh_pvp = await build_farm_embed_fresh(
            interaction.guild_id,
            interaction.channel_id,
            interaction.channel.name,
        )
        if interaction.message is not None:
            await interaction.message.edit(
                embed=fresh_embed,
                view=FarmPanelView(self.inventory_bot, pvp_mode=fresh_pvp),
            )
        await interaction.followup.send(
            "✅ Painel atualizado com os cargos atuais do membro.",
            ephemeral=True,
        )
