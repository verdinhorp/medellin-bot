"""Estoque: categorias, embeds de inventário/FARM/histórico, seletores e modal de quantidade."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import discord
from inventory_store import (
    InsufficientStockError,
    InventoryError,
    ItemNotFoundError,
)

from .bot import bot
from .config import (
    DIRTY_MONEY_WEEKLY_TARGET,
    FARM_TZ,
    LOG_ESTOQUE_CHANNEL_ID,
    MATERIAL_LABELS,
    MATERIAL_TARGETS,
    MEDELLIN_RED,
    logger,
)
from .farm_channels import (
    farm_channel_member,
    fresh_pvp_state,
    is_farm_channel,
    is_pvp_farm_channel,
    scope_channel_id,
)
from .utils import (
    category_emoji,
    farm_week_window,
    format_money,
    format_quantity,
    format_timestamp,
    normalize_category_name,
    normalize_material_name,
    parse_modal_quantity,
    resolve_category_value,
)


def all_stock_categories(guild_id: int) -> list[tuple[str, str, str]]:
    """Retorna (valor, rótulo, emoji) para o menu de categorias."""
    rows = [
        ("armas", "ARMAS", "🔫"),
        ("material", "MATERIAL", "🧱"),
        ("droga", "DROGA", "💊"),
        ("municao", "MUNIÇÃO", "📦"),
        ("dinheiro_real", "DINHEIRO SUJO • REAL", "💵"),
        ("dinheiro_dolar", "DINHEIRO SUJO • DÓLAR", "💵"),
        ("utilitario_acao", "UTILITÁRIO DE AÇÃO", "🧰"),
    ]
    existing = {label.casefold() for _, label, _ in rows}
    for category_name in bot.store.list_custom_categories(guild_id):
        if category_name.casefold() not in existing:
            rows.append((f"custom:{category_name}", category_name, "📁"))
    return rows[:25]


async def build_farm_embed_fresh(
    guild_id: int,
    channel_id: int,
    channel_name: str,
) -> tuple[discord.Embed, bool]:
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None

    if isinstance(channel, discord.TextChannel):
        pvp_mode, member = await fresh_pvp_state(channel)
        if pvp_mode and member is not None:
            embed = discord.Embed(
                title=f"🎯 PVP • {channel_name.upper()}",
                description=(
                    "Este membro é **PVP**.\n"
                    "A meta é **conjunta e semanal**."
                ),
                color=MEDELLIN_RED,
            )
            return embed, True

    return build_farm_embed(guild_id, channel_id, channel_name), False


async def enviar_log_estoque(
    interaction: discord.Interaction,
    tipo: str,
    item: str,
    categoria: str,
    quantidade: int,
    saldo_anterior: int,
    saldo_atual: int,
) -> None:
    if interaction.guild is None:
        return

    canal = interaction.guild.get_channel(LOG_ESTOQUE_CHANNEL_ID)
    if canal is None:
        try:
            canal = await interaction.guild.fetch_channel(LOG_ESTOQUE_CHANNEL_ID)
        except Exception:
            logger.exception("Não foi possível encontrar o canal de logs.")
            return

    if not isinstance(canal, (discord.TextChannel, discord.Thread)):
        logger.warning("O canal configurado para logs não aceita mensagens de texto.")
        return

    if tipo == "entrada":
        titulo = "📥 ENTRADA NO ESTOQUE"
        cor = discord.Color.green()
        simbolo = "➕"
    else:
        titulo = "📤 SAÍDA DO ESTOQUE"
        cor = discord.Color.red()
        simbolo = "➖"

    embed = discord.Embed(
        title=titulo,
        color=cor,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="👤 Responsável",
        value=interaction.user.mention,
        inline=False,
    )
    embed.add_field(name="📦 Item", value=item, inline=True)
    embed.add_field(name="📁 Categoria", value=categoria, inline=True)
    embed.add_field(
        name=f"{simbolo} Quantidade",
        value=format_quantity(quantidade),
        inline=True,
    )
    embed.add_field(
        name="📊 Saldo anterior",
        value=format_quantity(saldo_anterior),
        inline=True,
    )
    embed.add_field(
        name="📊 Saldo atual",
        value=format_quantity(saldo_atual),
        inline=True,
    )

    if is_farm_channel(interaction.channel):
        embed.add_field(
            name="🧑‍🌾 Farm",
            value=f"<#{interaction.channel_id}>",
            inline=False,
        )

    try:
        await canal.send(embed=embed)
    except Exception:
        logger.exception("Não foi possível enviar a log de estoque.")


def stock_description(guild_id: int, channel_scope: int) -> str:
    records = bot.store.list_items(guild_id, channel_id=channel_scope)
    if not records:
        return "📦 O estoque está vazio."

    grouped: dict[str, dict[str, object]] = {}
    for record in records:
        hidden_item = " ".join(str(record["name"]).strip().casefold().split())
        if hidden_item in {"fuja", "plásticos", "plasticos"}:
            continue

        canonical = normalize_category_name(str(record["category"]))
        key = canonical.casefold()
        if key not in grouped:
            grouped[key] = {"name": canonical, "items": []}
        grouped[key]["items"].append(record)  # type: ignore[union-attr]

    lines: list[str] = []
    for group in grouped.values():
        category = str(group["name"])
        lines.append(f"**{category_emoji(category)} {category.upper()}**")
        lines.extend(
            f"{record['name']} — **{format_quantity(record['quantity'])}**"
            for record in group["items"]  # type: ignore[union-attr]
        )
        lines.append("")

    result = "\n".join(lines).strip()
    if len(result) > 1800:
        result = result[:1790].rsplit("\n", 1)[0] + "\n…"
    return result


def build_inventory_embed(guild_id: int, channel_scope: int = 0) -> discord.Embed:
    embed = discord.Embed(title="📦 ESTOQUE • MEDELLÍN", color=MEDELLIN_RED)
    embed.description = stock_description(guild_id, channel_scope)
    embed.set_footer(text="Use os botões abaixo para movimentar o estoque")
    return embed


def material_progress(guild_id: int, channel_id: int) -> dict[str, int]:
    _, monday_utc, _ = farm_week_window()
    rows = bot.store.list_additions_since(
        guild_id,
        channel_id,
        monday_utc.replace(microsecond=0).isoformat(),
    )
    progress = {key: 0 for key in MATERIAL_TARGETS}
    for row in rows:
        material = normalize_material_name(str(row["item_name"]))
        if material in progress:
            progress[material] += int(row["quantity"])
    return progress


def build_farm_embed(
    guild_id: int,
    channel_id: int,
    channel_name: str,
) -> discord.Embed:
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    week_key, _, next_monday = farm_week_window()

    if isinstance(channel, discord.TextChannel) and is_pvp_farm_channel(channel):
        member = farm_channel_member(channel)
        embed = discord.Embed(
            title=f"🎯 PVP • {channel_name.upper()}",
            description=(
                "Este membro é **PVP**.\n"
                "A meta é **conjunta e semanal**."
            ),
            color=MEDELLIN_RED,
        )
        return embed

    now = datetime.now(FARM_TZ)
    today = now.date().isoformat()
    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()

    dirty_money = bot.store.get_weekly_money(guild_id, channel_id, week_start)

    money_percent = min(round((dirty_money / DIRTY_MONEY_WEEKLY_TARGET) * 100), 100)
    money_status = "✅" if dirty_money >= DIRTY_MONEY_WEEKLY_TARGET else "▫️"

    embed = discord.Embed(title=f"📦 FARM • {channel_name.upper()}", color=MEDELLIN_RED)

    embed.add_field(
        name="💰 META DE DINHEIRO",
        value=(
            f"{money_status} **Dinheiro Sujo:** {format_money(dirty_money)} / "
            f"{format_money(DIRTY_MONEY_WEEKLY_TARGET)} ({money_percent}%)"
        ),
        inline=False,
    )

    embed.set_footer(text="Dinheiro reinicia na segunda-feira")
    return embed


def build_history_embed(
    guild_id: int,
    item_name: Optional[str] = None,
    limit: int = 10,
    channel_scope: int = 0,
) -> discord.Embed:
    records = bot.store.list_history(
        guild_id,
        item_name=item_name,
        limit=limit,
        channel_id=channel_scope,
    )
    title = "📋 HISTÓRICO • FARM" if channel_scope else "📋 HISTÓRICO • MEDELLÍN"
    embed = discord.Embed(title=title, color=MEDELLIN_RED)
    embed.set_footer(text="Últimas movimentações")

    if not records:
        embed.description = "Nenhuma movimentação foi registrada ainda."
        return embed

    lines = []
    for record in records:
        action = "Entrada" if record["action"] == "add" else "Saída"
        sign = "+" if record["action"] == "add" else "-"
        lines.append(
            f"**{action}** · **{record['category']}** · **{record['item_name']}** · "
            f"{sign}{format_quantity(record['quantity'])} unidade(s)\n"
            f"Usuário: {record['user_name']} · "
            f"{format_timestamp(record['created_at'])}"
        )
    embed.description = "\n\n".join(lines)
    return embed


class StockQuantityModal(discord.ui.Modal):
    quantity = discord.ui.TextInput(
        label="Quantidade",
        placeholder="ex.: 10",
        min_length=1,
        max_length=10,
        required=True,
    )

    def __init__(self, action: str, category: str, item_name: str) -> None:
        title = "➕ Adicionar ao estoque" if action == "add" else "➖ Retirar do estoque"
        super().__init__(title=title)
        self.action = action
        self.category = category
        self.item_name = item_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel_scope = scope_channel_id(interaction)
        try:
            quantity = parse_modal_quantity(str(self.quantity.value))
            if self.action == "add":
                record = bot.store.add_stock(
                    guild_id=interaction.guild_id,
                    channel_id=channel_scope,
                    item_name=self.item_name,
                    quantity=quantity,
                    user_id=interaction.user.id,
                    user_name=str(interaction.user),
                    category=self.category,
                )
                tipo = "entrada"
                saldo_anterior = record["quantity"] - quantity
            else:
                record = bot.store.remove_stock(
                    guild_id=interaction.guild_id,
                    channel_id=channel_scope,
                    item_name=self.item_name,
                    quantity=quantity,
                    user_id=interaction.user.id,
                    user_name=str(interaction.user),
                )
                tipo = "saida"
                saldo_anterior = record["quantity"] + quantity
        except (InsufficientStockError, ItemNotFoundError, InventoryError) as error:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Não foi possível movimentar o estoque",
                    description=str(error),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        verbo = "adicionadas" if self.action == "add" else "retiradas"
        embed = discord.Embed(title="Estoque atualizado", color=discord.Color.green())
        embed.description = (
            f"Foram {verbo} **{format_quantity(quantity)}** unidade(s) de "
            f"**{record['name']}** em **{record['category']}**.\n"
            f"Saldo atual: **{format_quantity(record['quantity'])}** unidade(s)."
        )
        material = normalize_material_name(record["name"])
        if self.action == "add" and channel_scope and material:
            target = MATERIAL_TARGETS.get(material)
            current = material_progress(interaction.guild_id, channel_scope)[material]
            if target is None:
                embed.description += (
                    f"\n📦 {MATERIAL_LABELS[material]} nesta semana: **{format_quantity(current)}** (sem meta definida)."
                )
            else:
                embed.description += (
                    f"\n🎯 Meta semanal de {MATERIAL_LABELS[material]}: "
                    f"**{format_quantity(current)} / {format_quantity(target)}**."
                )

        await interaction.followup.send(embed=embed, ephemeral=True)
        await enviar_log_estoque(
            interaction, tipo, record["name"], record["category"], quantity,
            saldo_anterior, record["quantity"]
        )
        await bot.refresh_inventory_panel(interaction.guild_id, channel_scope)
        if channel_scope:
            await bot.refresh_inventory_panel(interaction.guild_id, 0)


class ItemSelect(discord.ui.Select):
    def __init__(self, action: str, guild_id: int, category: str) -> None:
        self.action = action
        self.category = category
        item_names = bot.store.list_catalog_items(guild_id, category)
        options = [
            discord.SelectOption(label=name, value=name, emoji="📦")
            for name in item_names[:25]
        ]
        super().__init__(
            placeholder="selecione o item", min_values=1, max_values=1, options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            StockQuantityModal(self.action, self.category, self.values[0])
        )


class ItemSelectView(discord.ui.View):
    def __init__(self, action: str, guild_id: int, category: str) -> None:
        super().__init__(timeout=120)
        self.add_item(ItemSelect(action, guild_id, category))


class CategorySelect(discord.ui.Select):
    def __init__(self, action: str, guild_id: int) -> None:
        self.action = action
        options = [
            discord.SelectOption(label=label.lower(), value=value, emoji=emoji)
            for value, label, emoji in all_stock_categories(guild_id)
        ]
        super().__init__(
            placeholder="selecione a categoria", min_values=1, max_values=1, options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.", ephemeral=True
            )
            return
        category = resolve_category_value(self.values[0])
        items = bot.store.list_catalog_items(interaction.guild_id, category)
        if not items:
            await interaction.response.send_message(
                f"Ainda não existe nenhum item cadastrado em **{category}**. "
                "A dona do servidor precisa cadastrar os itens primeiro.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content=f"Categoria: **{category}**\nAgora selecione o item:",
            view=ItemSelectView(self.action, interaction.guild_id, category),
        )


class CategorySelectView(discord.ui.View):
    def __init__(self, action: str, guild_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(CategorySelect(action, guild_id))
