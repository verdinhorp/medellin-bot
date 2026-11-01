"""Caixa: logs, embeds, modais e botões de entrada/saída de dinheiro."""

from __future__ import annotations

from datetime import datetime, timedelta

import discord
from inventory_store import (
    InsufficientStockError,
    InventoryError,
)

from .bot import InventoryBot, bot
from .config import (
    CASH_LOG_CHANNEL_ID,
    CHEMICAL_ROUTES_DAILY_TARGET,
    DIRTY_MONEY_WEEKLY_TARGET,
    FARM_TZ,
    MEDELLIN_RED,
    logger,
)
from .farm_channels import is_farm_channel
from .utils import (
    currency_label,
    format_currency,
    format_money,
    format_timestamp,
    parse_modal_quantity,
)


async def enviar_log_caixa(
    interaction: discord.Interaction,
    tipo: str,
    amount: int,
    reason: str,
    balance_before: int,
    balance_after: int,
    currency: str,
) -> None:
    if interaction.guild is None:
        return

    canal = interaction.guild.get_channel(CASH_LOG_CHANNEL_ID)
    if canal is None:
        try:
            canal = await interaction.guild.fetch_channel(CASH_LOG_CHANNEL_ID)
        except Exception:
            logger.exception("Não foi possível encontrar o canal de logs do caixa.")
            return

    if not isinstance(canal, (discord.TextChannel, discord.Thread)):
        logger.warning("O canal configurado para logs do caixa não aceita mensagens de texto.")
        return

    entrada = tipo == "entrada"
    embed = discord.Embed(
        title="📥 ENTRADA NO CAIXA" if entrada else "📤 SAÍDA DO CAIXA",
        color=discord.Color.green() if entrada else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="👤 Responsável", value=interaction.user.mention, inline=False)
    embed.add_field(name="💱 Moeda", value=currency_label(currency), inline=False)
    embed.add_field(name="💵 Valor", value=format_currency(amount, currency), inline=True)
    embed.add_field(name="📝 Motivo", value=reason, inline=False)
    embed.add_field(name="📊 Saldo anterior", value=format_currency(balance_before, currency), inline=True)
    embed.add_field(name="📊 Saldo atual", value=format_currency(balance_after, currency), inline=True)

    if interaction.channel_id:
        embed.add_field(name="📍 Canal", value=f"<#{interaction.channel_id}>", inline=False)

    try:
        await canal.send(embed=embed)
    except Exception:
        logger.exception("Não foi possível enviar a log do caixa.")


def build_cash_panel_embed(guild_id: int, mode: str) -> discord.Embed:
    brl = bot.store.get_cash_balance(guild_id, "BRL")
    usd = bot.store.get_cash_balance(guild_id, "USD")
    entrada = mode == "entry"
    embed = discord.Embed(
        title="💰 CAIXA • MEDELLÍN",
        description=(
            "**Controle financeiro da organização**\n"
            + ("Registre entradas e acompanhe os saldos abaixo." if entrada else "Registre retiradas e acompanhe os saldos abaixo.")
        ),
        color=discord.Color.green() if entrada else discord.Color.red(),
    )
    embed.add_field(name="🇧🇷 REAL", value=f"💵 Saldo atual: **{format_currency(brl, 'BRL')}**", inline=False)
    embed.add_field(name="🇺🇸 DÓLAR", value=f"💵 Saldo atual: **{format_currency(usd, 'USD')}**", inline=False)
    embed.set_footer(text="MEDELLÍN • Sistema Financeiro")
    return embed


def build_cash_history_embed(guild_id: int, limit: int = 10) -> discord.Embed:
    records = bot.store.list_cash_history(guild_id, limit=limit)
    embed = discord.Embed(title="💰 HISTÓRICO DO CAIXA", color=MEDELLIN_RED)

    if not records:
        embed.description = "Nenhuma movimentação foi registrada ainda."
        return embed

    lines = []
    for record in records:
        entrada = record["action"] == "add"
        emoji = "📥" if entrada else "📤"
        tipo = "Entrada" if entrada else "Saída"
        sinal = "+" if entrada else "-"
        lines.append(
            f"{emoji} **{tipo}** · {currency_label(record.get('currency', 'BRL'))} · {sinal}{format_currency(record['amount'], record.get('currency', 'BRL'))}\n"
            f"Motivo: {record['reason']}\n"
            f"Responsável: {record['user_name']} · "
            f"{format_timestamp(record['created_at'])}\n"
            f"Saldo após: **{format_currency(record['balance_after'], record.get('currency', 'BRL'))}**"
        )
    embed.description = "\n\n".join(lines)
    return embed


def build_routes_days_embed(
    guild_id: int,
    channel_id: int,
    days: int = 7,
) -> discord.Embed:
    now = datetime.now(FARM_TZ)
    today = now.date()

    embed = discord.Embed(
        title="🧪 Histórico de Rotas de Químicos",
        description="Últimos 7 dias — ✅ meta feita | ❌ meta não feita",
        color=MEDELLIN_RED,
    )

    lines = []
    completed_days = 0

    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        progress = bot.store.get_daily_farm_progress(
            guild_id,
            channel_id,
            day.isoformat(),
        )
        routes = int(progress["chemical_routes"])
        completed = routes >= CHEMICAL_ROUTES_DAILY_TARGET

        if completed:
            completed_days += 1

        status = "✅" if completed else "❌"
        day_label = day.strftime("%d/%m")
        today_mark = " **(hoje)**" if day == today else ""

        lines.append(
            f"{status} **{day_label}**{today_mark} — "
            f"{routes}/{CHEMICAL_ROUTES_DAILY_TARGET} rotas"
        )

    embed.add_field(
        name="📅 Dias",
        value="\n".join(lines) if lines else "Nenhum registro encontrado.",
        inline=False,
    )
    embed.set_footer(
        text=f"Meta concluída em {completed_days}/{days} dias • "
             f"necessário: {CHEMICAL_ROUTES_DAILY_TARGET} rotas por dia"
    )
    return embed


class AddMoneyModal(discord.ui.Modal, title="💰 Adicionar dinheiro sujo"):
    amount = discord.ui.TextInput(
        label="Valor de dinheiro sujo",
        placeholder="Ex.: 25000",
        min_length=1,
        max_length=12,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not is_farm_channel(interaction.channel):
            await interaction.response.send_message(
                "A meta diária só funciona nos canais de FARM.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            amount = parse_modal_quantity(str(self.amount.value))
            now = datetime.now(FARM_TZ)
            week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
            total = bot.store.add_weekly_money(
                interaction.guild_id, interaction.channel_id, week_start, amount
            )
        except InventoryError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        await interaction.followup.send(
            f"💰 Adicionado **{format_money(amount)}** de dinheiro sujo.\n"
            f"Progresso da semana: **{format_money(total)} / {format_money(DIRTY_MONEY_WEEKLY_TARGET)}**.",
            ephemeral=True,
        )
        await bot.refresh_inventory_panel(interaction.guild_id, interaction.channel_id)


class AddChemicalRouteModal(discord.ui.Modal, title="🧪 Registrar rota de químicos"):
    routes = discord.ui.TextInput(
        label="Quantidade de rotas",
        placeholder="Ex.: 1",
        default="1",
        min_length=1,
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not is_farm_channel(interaction.channel):
            await interaction.response.send_message(
                "A meta diária só funciona nos canais de FARM.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            routes = parse_modal_quantity(str(self.routes.value))
            today = datetime.now(FARM_TZ).date().isoformat()
            total = bot.store.add_daily_chemical_route(
                interaction.guild_id, interaction.channel_id, today, routes
            )
        except InventoryError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        await interaction.followup.send(
            f"🧪 Registrada(s) **{routes}** rota(s) de químicos.\n"
            f"Progresso de hoje: **{total} / {CHEMICAL_ROUTES_DAILY_TARGET}**.",
            ephemeral=True,
        )
        await bot.refresh_inventory_panel(interaction.guild_id, interaction.channel_id)


class CashMovementModal(discord.ui.Modal):
    amount = discord.ui.TextInput(label="Valor", placeholder="Ex.: 250000", min_length=1, max_length=12, required=True)
    reason = discord.ui.TextInput(label="Motivo", placeholder="Ex.: Venda de materiais", min_length=1, max_length=200, required=True)

    def __init__(self, action: str, currency: str) -> None:
        self.action = action
        self.currency = currency
        moeda = "Real" if currency == "BRL" else "Dólar"
        titulo = f"📥 Adicionar {moeda}" if action == "add" else f"📤 Retirar {moeda}"
        super().__init__(title=titulo)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Esse painel só funciona dentro do servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            amount = parse_modal_quantity(str(self.amount.value))
            kwargs = dict(guild_id=interaction.guild_id, amount=amount, reason=str(self.reason.value), user_id=interaction.user.id, user_name=str(interaction.user), currency=self.currency)
            record = bot.store.add_cash(**kwargs) if self.action == "add" else bot.store.remove_cash(**kwargs)
        except (InventoryError, InsufficientStockError) as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        entrada = self.action == "add"
        moeda = "REAL" if self.currency == "BRL" else "DÓLAR"
        embed = discord.Embed(title=f"{'📥 ENTRADA' if entrada else '📤 SAÍDA'} • {moeda}", color=discord.Color.green() if entrada else discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="👤 Responsável", value=interaction.user.mention, inline=False)
        embed.add_field(name="💵 Valor", value=f"**{format_currency(record['amount'], record['currency'])}**", inline=True)
        embed.add_field(name="📝 Motivo", value=record["reason"], inline=False)
        embed.add_field(name="📊 Saldo anterior", value=format_currency(record["balance_before"], record["currency"]), inline=True)
        embed.add_field(name="💰 Novo saldo", value=f"**{format_currency(record['balance_after'], record['currency'])}**", inline=True)
        embed.set_footer(text="MEDELLÍN • Sistema Financeiro")
        await interaction.followup.send("Movimentação registrada com sucesso.", ephemeral=True)
        await enviar_log_caixa(interaction, "entrada" if entrada else "saida", record["amount"], record["reason"], record["balance_before"], record["balance_after"], record["currency"])
        await bot.refresh_cash_panels(interaction.guild_id)


class CashEntryView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot

    @discord.ui.button(label="Adicionar Real", emoji="🇧🇷", style=discord.ButtonStyle.success, custom_id="cash_panel:add_brl")
    async def add_brl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CashMovementModal("add", "BRL"))

    @discord.ui.button(label="Adicionar Dólar", emoji="🇺🇸", style=discord.ButtonStyle.success, custom_id="cash_panel:add_usd")
    async def add_usd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CashMovementModal("add", "USD"))

    @discord.ui.button(label="Histórico", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="cash_panel:entry_history_v2")
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild_id is not None:
            await interaction.response.send_message(embed=build_cash_history_embed(interaction.guild_id), ephemeral=True)


class CashExitView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot

    @discord.ui.button(label="Retirar Real", emoji="🇧🇷", style=discord.ButtonStyle.danger, custom_id="cash_panel:remove_brl")
    async def remove_brl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CashMovementModal("remove", "BRL"))

    @discord.ui.button(label="Retirar Dólar", emoji="🇺🇸", style=discord.ButtonStyle.danger, custom_id="cash_panel:remove_usd")
    async def remove_usd(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CashMovementModal("remove", "USD"))

    @discord.ui.button(label="Histórico", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="cash_panel:exit_history_v2")
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild_id is not None:
            await interaction.response.send_message(embed=build_cash_history_embed(interaction.guild_id), ephemeral=True)
