"""Pedidos individuais: painel, modal e controle de entrega/cancelamento."""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from .bot import bot
from .collective_orders import ensure_collective_order_for, refresh_collective_order
from .config import MEDELLIN_RED, ORDERS_CHANNEL_ID
from .stores.order_store import order_store
from .utils import format_timestamp


def can_manage_orders(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    allowed_roles = {"gerente geral", "gerente farm", "gerente ação", "gerente acao"}
    return any(role.name.casefold() in allowed_roles for role in interaction.user.roles)


def build_orders_panel_embed(guild_id: int) -> discord.Embed:
    pending, delivered, cancelled = order_store.counts(guild_id)
    embed = discord.Embed(
        title="📦 CENTRAL DE PEDIDOS",
        description=(
            "Use o botão abaixo para registrar um novo pedido.\n"
            "Cada pedido mostra a **família**, o conteúdo e se já foi entregue."
        ),
        color=MEDELLIN_RED,
    )
    embed.add_field(name="🟡 Em andamento", value=str(pending), inline=True)
    embed.add_field(name="🟢 Entregues", value=str(delivered), inline=True)
    embed.add_field(name="🔴 Cancelados", value=str(cancelled), inline=True)
    embed.set_footer(text="Os gerentes podem marcar pedidos como entregues ou cancelados.")
    return embed


def build_order_embed(order: dict) -> discord.Embed:
    status = str(order["status"])
    if status == "delivered":
        title = "✅ PEDIDO ENTREGUE"
        color = discord.Color.green()
        status_text = "🟢 **ENTREGUE**"
    elif status == "cancelled":
        title = "❌ PEDIDO CANCELADO"
        color = discord.Color.red()
        status_text = "🔴 **CANCELADO**"
    else:
        title = "📦 PEDIDO EM ANDAMENTO"
        color = discord.Color.orange()
        status_text = "🟡 **EM ANDAMENTO**"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="👥 Família", value=str(order["family"]), inline=False)
    embed.add_field(name="📝 Pedido", value=str(order["order_text"]), inline=False)
    embed.add_field(name="📌 Status", value=status_text, inline=True)
    embed.add_field(name="👤 Solicitado por", value=f"<@{order['requester_id']}>", inline=True)
    embed.add_field(name="🕒 Criado em", value=format_timestamp(str(order["created_at"])), inline=False)

    if status == "delivered" and order.get("delivered_by_id"):
        embed.add_field(name="✅ Entregue por", value=f"<@{order['delivered_by_id']}>", inline=True)
        if order.get("delivered_at"):
            embed.add_field(name="🕒 Entregue em", value=format_timestamp(str(order["delivered_at"])), inline=True)
    elif status == "cancelled" and order.get("cancelled_by_id"):
        embed.add_field(name="❌ Cancelado por", value=f"<@{order['cancelled_by_id']}>", inline=True)
        if order.get("cancelled_at"):
            embed.add_field(name="🕒 Cancelado em", value=format_timestamp(str(order["cancelled_at"])), inline=True)

    return embed


async def refresh_orders_panel(guild: discord.Guild) -> None:
    channel = guild.get_channel(ORDERS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(ORDERS_CHANNEL_ID)
        except discord.DiscordException:
            return
    if not isinstance(channel, discord.TextChannel):
        return

    async for message in channel.history(limit=50):
        if (
            message.author == bot.user
            and message.embeds
            and message.embeds[0].title == "📦 CENTRAL DE PEDIDOS"
        ):
            try:
                await message.edit(embed=build_orders_panel_embed(guild.id), view=OrdersPanelView())
            except discord.DiscordException:
                pass
            return


class NewOrderModal(discord.ui.Modal, title="📦 Novo pedido"):
    family = discord.ui.TextInput(
        label="Família",
        placeholder="Ex.: 01, 02, 03...",
        min_length=1,
        max_length=60,
        required=True,
    )
    order_text = discord.ui.TextInput(
        label="Pedido",
        placeholder="Ex.: 500 molas, 10k metal, aço e alumínio",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse botão só funciona no servidor.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(ORDERS_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Não encontrei o canal de pedidos configurado.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        temp_order = {
            "family": str(self.family.value).strip(),
            "order_text": str(self.order_text.value).strip(),
            "requester_id": interaction.user.id,
            "requester_name": str(interaction.user),
            "status": "pending",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        message = await channel.send(embed=build_order_embed(temp_order), view=OrderControlView())
        order_store.create_order(
            message_id=message.id,
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            family=temp_order["family"],
            order_text=temp_order["order_text"],
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
        )
        saved = order_store.get_order(message.id)
        if saved:
            await message.edit(embed=build_order_embed(saved), view=OrderControlView())
            await ensure_collective_order_for(saved, interaction.guild)
        await refresh_orders_panel(interaction.guild)
        await interaction.followup.send(f"✅ Pedido criado em {channel.mention}.", ephemeral=True)


class OrdersPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Novo pedido",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="orders_panel:new",
    )
    async def new_order(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.channel_id != ORDERS_CHANNEL_ID:
            await interaction.response.send_message("Use esse botão no canal de pedidos.", ephemeral=True)
            return
        await interaction.response.send_modal(NewOrderModal())


class OrderControlView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Marcar entregue",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="order_control:delivered",
    )
    async def delivered(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.message is None or interaction.guild is None:
            return
        if not can_manage_orders(interaction):
            await interaction.response.send_message(
                "Somente a dona ou os gerentes podem marcar um pedido como entregue.",
                ephemeral=True,
            )
            return
        order = order_store.get_order(interaction.message.id)
        if not order:
            await interaction.response.send_message("Não encontrei esse pedido no banco.", ephemeral=True)
            return
        if order["status"] != "pending":
            await interaction.response.send_message("Esse pedido já foi finalizado.", ephemeral=True)
            return
        order = order_store.set_status(interaction.message.id, "delivered", interaction.user)
        if order:
            await interaction.response.edit_message(embed=build_order_embed(order), view=OrderControlView())
            await refresh_orders_panel(interaction.guild)
            await refresh_collective_order(order, interaction.guild)

    @discord.ui.button(
        label="Cancelar",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="order_control:cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.message is None or interaction.guild is None:
            return
        if not can_manage_orders(interaction):
            await interaction.response.send_message(
                "Somente a dona ou os gerentes podem cancelar um pedido.",
                ephemeral=True,
            )
            return
        order = order_store.get_order(interaction.message.id)
        if not order:
            await interaction.response.send_message("Não encontrei esse pedido no banco.", ephemeral=True)
            return
        if order["status"] != "pending":
            await interaction.response.send_message("Esse pedido já foi finalizado.", ephemeral=True)
            return
        order = order_store.set_status(interaction.message.id, "cancelled", interaction.user)
        if order:
            await interaction.response.edit_message(embed=build_order_embed(order), view=OrderControlView())
            await refresh_orders_panel(interaction.guild)
            await refresh_collective_order(order, interaction.guild)
