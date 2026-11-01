"""Comandos slash: /estoque e /historico."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from .bot import bot
from .farm_channels import is_pvp_farm_channel, scope_channel_id
from .panels import FarmPanelView, InventoryPanelView
from .stock import build_farm_embed, build_history_embed, build_inventory_embed


def require_guild(interaction: discord.Interaction) -> Optional[discord.Embed]:
    if interaction.guild_id is None:
        return discord.Embed(
            title="Comando indisponível",
            description="Os comandos de inventário só podem ser usados dentro de um servidor.",
            color=discord.Color.red(),
        )
    return None


@bot.tree.command(name="estoque", description="Consulta o inventário deste canal/servidor.")
@app_commands.describe(item="Nome do item específico (opcional)")
async def estoque(
    interaction: discord.Interaction,
    item: Optional[str] = None,
) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    channel_scope = scope_channel_id(interaction)

    if item:
        record = bot.store.get_item(
            interaction.guild_id,
            item,
            channel_id=channel_scope,
        )
        if record is None:
            embed = discord.Embed(
                title="Item não encontrado",
                description=f"Nenhum item chamado **{item.strip()}** está cadastrado aqui.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    if channel_scope and isinstance(interaction.channel, discord.TextChannel):
        embed = build_farm_embed(
            interaction.guild_id,
            channel_scope,
            interaction.channel.name,
        )
        view: discord.ui.View = FarmPanelView(bot, pvp_mode=is_pvp_farm_channel(interaction.channel))
    else:
        embed = build_inventory_embed(interaction.guild_id, 0)
        view = InventoryPanelView(bot)

    await interaction.response.send_message(embed=embed, view=view)
    panel_message = await interaction.original_response()
    bot.remember_panel(interaction.guild_id, panel_message, channel_scope)

    if channel_scope:
        bot.store.set_farm_panel(
            interaction.guild_id,
            channel_scope,
            panel_message.id,
        )


def _is_guild_owner(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.user.id == interaction.guild.owner_id


@bot.tree.command(name="historico", description="Consulta as últimas movimentações do estoque.")
@app_commands.describe(
    item="Filtra pelo nome do item (opcional)",
    limite="Quantidade de registros, de 1 a 20 (padrão: 10)",
)
async def historico(
    interaction: discord.Interaction,
    item: Optional[str] = None,
    limite: app_commands.Range[int, 1, 20] = 10,
) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    channel_scope = scope_channel_id(interaction)
    await interaction.response.send_message(
        embed=build_history_embed(
            interaction.guild_id,
            item_name=item,
            limit=limite,
            channel_scope=channel_scope,
        ),
        ephemeral=True,
    )
