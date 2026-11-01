"""Pedidos coletivos: progresso, contribuições e embed de produção."""

from __future__ import annotations

import discord

from .config import COLLECTIVE_ORDERS_CHANNEL_ID
from .stores.order_store import collective_order_store, order_store
from .utils import _progress_bar


def _all_collective_items_complete(items: list[dict]) -> bool:
    return bool(items) and all(
        int(item.get("goal_amount", 0)) > 0
        and int(item.get("current_amount", 0)) >= int(item.get("goal_amount", 0))
        for item in items
    )


def build_collective_order_embed(order: dict, progress: dict) -> discord.Embed:
    items = collective_order_store.ensure_items(order)
    complete = _all_collective_items_complete(items)
    official_status = str(order.get("status", "pending"))

    if official_status == "delivered":
        color = discord.Color.green()
        title = "✅ PEDIDO ENTREGUE"
    elif official_status == "cancelled":
        color = discord.Color.red()
        title = "❌ PEDIDO CANCELADO"
    else:
        color = discord.Color.green() if complete else discord.Color.orange()
        title = "✅ PEDIDO COMPLETO" if complete else "📦 PEDIDO EM PRODUÇÃO"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="👥 Família", value=str(order["family"]), inline=False)
    embed.add_field(name="📦 Pedido oficial", value=str(order["order_text"]), inline=False)

    if items:
        # Cada item ganha sua própria barra e seu próprio "faltam".
        for item in items[:20]:
            goal = int(item.get("goal_amount", 0))
            current = int(item.get("current_amount", 0))
            remaining = max(goal - current, 0) if goal > 0 else 0
            percent = int(round((current / goal) * 100)) if goal > 0 else 0
            item_name = str(item.get("item_name", "Item"))

            if goal > 0:
                value = (
                    f"**{current:,} / {goal:,}** ({percent}%)\n"
                    f"`{_progress_bar(current, goal)}`\n"
                    f"📉 Faltam: **{remaining:,}**"
                ).replace(",", ".")
            else:
                value = f"**{current:,} adicionados**".replace(",", ".")

            if goal > 0 and current >= goal:
                value += "\n✅ **COMPLETO**"

            embed.add_field(
                name=f"🔧 {item_name}",
                value=value,
                inline=False,
            )
    else:
        embed.add_field(
            name="⚠️ Itens",
            value="Não consegui identificar os itens e quantidades desse pedido.",
            inline=False,
        )

    contributors = collective_order_store.contributors(int(order["message_id"]))
    if contributors:
        lines = []
        for row in contributors:
            total_text = f"{int(row['total']):,}".replace(",", ".")
            item_parts = [
                f"{item['item_name']}: **{int(item['total']):,}**".replace(",", ".")
                for item in row.get("items", [])
                if int(item.get("total", 0)) > 0
            ]
            details = " | ".join(item_parts)
            line = f"<@{row['user_id']}> — **{total_text} itens**"
            if details:
                line += f"\n↳ {details}"
            lines.append(line)

        # Campos de embed aceitam no máximo 1024 caracteres. Mantemos somente
        # as linhas completas que couberem para evitar erro ao editar a mensagem.
        visible_lines = []
        used = 0
        for line in lines:
            extra = len(line) + (1 if visible_lines else 0)
            if used + extra > 1000:
                break
            visible_lines.append(line)
            used += extra
        if len(visible_lines) < len(lines):
            visible_lines.append("… e mais contribuições registradas.")

        embed.add_field(
            name="👥 Quem já adicionou",
            value="\n".join(visible_lines),
            inline=False,
        )

    if official_status == "delivered":
        status_value = "✅ **ENTREGUE NOS PEDIDOS OFICIAIS**"
    elif official_status == "cancelled":
        status_value = "🔴 **CANCELADO**"
    else:
        status_value = "🟢 **PRONTO PARA ENTREGA**" if complete else "🟡 **EM PRODUÇÃO**"

    embed.add_field(name="📌 Status", value=status_value, inline=False)
    embed.set_footer(
        text="Clique em Adicionar, escolha o item e informe a quantidade produzida."
    )
    return embed


async def ensure_collective_order_for(order: dict, guild: discord.Guild) -> None:
    if str(order.get("status")) != "pending":
        return

    channel = guild.get_channel(COLLECTIVE_ORDERS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(COLLECTIVE_ORDERS_CHANNEL_ID)
        except discord.DiscordException:
            return
    if not isinstance(channel, discord.TextChannel):
        return

    progress = collective_order_store.get_by_order(int(order["message_id"]))
    collective_order_store.ensure_items(order)

    if progress and progress.get("progress_message_id"):
        try:
            message = await channel.fetch_message(int(progress["progress_message_id"]))
            await message.edit(
                embed=build_collective_order_embed(order, progress),
                view=CollectiveOrderView(),
            )
            return
        except discord.DiscordException:
            pass

    temp_progress = {"current_amount": 0}
    message = await channel.send(
        embed=build_collective_order_embed(order, temp_progress),
        view=CollectiveOrderView(),
    )
    progress = collective_order_store.create(order, message.id, channel.id)
    await message.edit(
        embed=build_collective_order_embed(order, progress),
        view=CollectiveOrderView(),
    )


async def refresh_collective_order(order: dict, guild: discord.Guild) -> None:
    progress = collective_order_store.get_by_order(int(order["message_id"]))
    if not progress or not progress.get("progress_message_id"):
        return

    channel = guild.get_channel(int(progress["channel_id"]))
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(progress["channel_id"]))
        except discord.DiscordException:
            return
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(progress["progress_message_id"]))
        view = CollectiveOrderView() if str(order.get("status")) == "pending" else None
        await message.edit(
            embed=build_collective_order_embed(order, progress),
            view=view,
        )
    except discord.DiscordException:
        pass


class AddContributionModal(discord.ui.Modal, title="➕ Adicionar ao pedido"):
    amount = discord.ui.TextInput(
        label="Quantidade que você fez",
        placeholder="Ex.: 100",
        min_length=1,
        max_length=12,
        required=True,
    )

    def __init__(self, progress_message_id: int, item_id: int, item_name: str) -> None:
        super().__init__()
        self.progress_message_id = progress_message_id
        self.item_id = item_id
        self.item_name = item_name
        self.amount.label = f"Quantidade de {item_name}"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.amount.value).strip().replace(".", "").replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message(
                "Digite uma quantidade válida maior que zero.",
                ephemeral=True,
            )
            return

        progress = collective_order_store.get_by_progress(self.progress_message_id)
        if not progress:
            await interaction.response.send_message(
                "Não encontrei esse pedido de produção.",
                ephemeral=True,
            )
            return

        order = order_store.get_order(int(progress["order_message_id"]))
        if not order or str(order.get("status")) != "pending":
            await interaction.response.send_message(
                "Esse pedido oficial já foi finalizado.",
                ephemeral=True,
            )
            return

        item = collective_order_store.get_item(self.item_id)
        if not item or int(item["order_message_id"]) != int(progress["order_message_id"]):
            await interaction.response.send_message(
                "Não encontrei esse item no pedido.",
                ephemeral=True,
            )
            return

        goal = int(item.get("goal_amount", 0))
        current = int(item.get("current_amount", 0))
        if goal > 0 and current >= goal:
            await interaction.response.send_message(
                f"✅ **{self.item_name}** já atingiu 100% da meta.",
                ephemeral=True,
            )
            return

        requested = int(raw)
        updated_item = collective_order_store.add_to_item(
            self.item_id,
            interaction.user,
            requested,
        )
        if not updated_item:
            await interaction.response.send_message(
                "Não consegui atualizar o pedido.",
                ephemeral=True,
            )
            return

        added = int(updated_item["current_amount"]) - current

        await interaction.response.send_message(
            f"✅ Você adicionou **{added:,} {self.item_name}** ao pedido.".replace(",", "."),
            ephemeral=True,
        )

        # Atualiza a mensagem pública do pedido coletivo.
        channel = interaction.guild.get_channel(int(progress["channel_id"])) if interaction.guild else None
        if channel is None and interaction.guild:
            try:
                channel = await interaction.guild.fetch_channel(int(progress["channel_id"]))
            except discord.DiscordException:
                channel = None

        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(self.progress_message_id)
                fresh_progress = collective_order_store.get_by_progress(self.progress_message_id) or progress
                await message.edit(
                    embed=build_collective_order_embed(order, fresh_progress),
                    view=CollectiveOrderView(),
                )
            except discord.DiscordException:
                pass


class CollectiveItemSelect(discord.ui.Select):
    def __init__(self, progress_message_id: int, items: list[dict]) -> None:
        options = []
        for item in items[:25]:
            goal = int(item.get("goal_amount", 0))
            current = int(item.get("current_amount", 0))
            remaining = max(goal - current, 0) if goal > 0 else 0
            name = str(item.get("item_name", "Item"))

            if goal > 0:
                description = f"Faltam {remaining:,} de {goal:,}".replace(",", ".")
            else:
                description = f"{current:,} adicionados".replace(",", ".")

            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(item["id"]),
                    description=description[:100],
                    emoji="🔧",
                )
            )

        super().__init__(
            placeholder="Escolha qual item você quer adicionar",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"collective_order:item_select:{progress_message_id}",
        )
        self.progress_message_id = progress_message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        item_id = int(self.values[0])
        item = collective_order_store.get_item(item_id)
        if not item:
            await interaction.response.send_message(
                "Não encontrei esse item.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AddContributionModal(
                self.progress_message_id,
                item_id,
                str(item.get("item_name", "Item")),
            )
        )


class CollectiveItemSelectView(discord.ui.View):
    def __init__(self, progress_message_id: int, items: list[dict]) -> None:
        super().__init__(timeout=120)
        self.add_item(CollectiveItemSelect(progress_message_id, items))


class CollectiveOrderView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Adicionar",
        emoji="➕",
        style=discord.ButtonStyle.primary,
        custom_id="collective_order:add",
    )
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.message is None:
            return

        progress = collective_order_store.get_by_progress(interaction.message.id)
        if not progress:
            await interaction.response.send_message(
                "Não encontrei esse pedido de produção.",
                ephemeral=True,
            )
            return

        order = order_store.get_order(int(progress["order_message_id"]))
        if not order or str(order.get("status")) != "pending":
            await interaction.response.send_message(
                "Esse pedido oficial já foi finalizado.",
                ephemeral=True,
            )
            return

        items = collective_order_store.ensure_items(order)
        available = [
            item for item in items
            if int(item.get("goal_amount", 0)) <= 0
            or int(item.get("current_amount", 0)) < int(item.get("goal_amount", 0))
        ]

        if not available:
            await interaction.response.send_message(
                "✅ Todos os itens desse pedido já atingiram 100% da meta.",
                ephemeral=True,
            )
            return

        # Primeiro escolhe o item; depois o bot abre a caixa para colocar a quantidade.
        await interaction.response.send_message(
            "🔧 **Qual item você quer adicionar?**",
            view=CollectiveItemSelectView(interaction.message.id, available),
            ephemeral=True,
        )
