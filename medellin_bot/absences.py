"""Ausências: painel, registro e finalização."""

from __future__ import annotations

from datetime import datetime

import discord

from .bot import bot
from .config import ABSENCE_CHANNEL_ID, MEDELLIN_RED, logger
from .stores.extra_store import extra_store


def build_absence_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📅 AUSÊNCIAS • MEDELLÍN",
        description=(
            "Use os botões abaixo para registrar ou finalizar sua ausência.\n\n"
            "Ao registrar, informe o **motivo**, a **data de início** e a **data prevista de retorno**."
        ),
        color=MEDELLIN_RED,
    )
    embed.set_footer(text="Sistema disponível para todos os membros")
    return embed


def build_absence_record_embed(row, ended: bool = False) -> discord.Embed:
    active = not ended and str(row["status"]) == "active"
    embed = discord.Embed(
        title="🟠 AUSÊNCIA REGISTRADA" if active else "🟢 AUSÊNCIA FINALIZADA",
        color=discord.Color.orange() if active else discord.Color.green(),
    )
    embed.add_field(name="👤 Membro", value=f"<@{int(row['user_id'])}>", inline=False)
    embed.add_field(name="📅 Período", value=f"{row['start_date']} → {row['return_date']}", inline=False)
    embed.add_field(name="📝 Motivo", value=str(row["reason"])[:1024], inline=False)
    embed.add_field(name="📌 Status", value="AUSENTE" if active else "RETORNOU", inline=False)
    return embed


class AbsenceModal(discord.ui.Modal, title="📅 Registrar ausência"):
    reason = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, max_length=500, required=True)
    start_date = discord.ui.TextInput(label="Data de início", placeholder="Ex.: 27/08/2026", max_length=10, required=True)
    return_date = discord.ui.TextInput(label="Data prevista de retorno", placeholder="Ex.: 30/08/2026", max_length=10, required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
            return
        if extra_store.get_active_absence(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("Você já possui uma ausência ativa. Finalize a atual antes de criar outra.", ephemeral=True)
            return
        def parse_date(value: str) -> str:
            value = value.strip()
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    return datetime.strptime(value, fmt).strftime("%d/%m/%Y")
                except ValueError:
                    pass
            raise ValueError
        try:
            start = parse_date(str(self.start_date.value))
            ret = parse_date(str(self.return_date.value))
        except ValueError:
            await interaction.response.send_message("Use datas no formato **DD/MM/AAAA**.", ephemeral=True)
            return
        absence_id = extra_store.create_absence(
            interaction.guild.id, interaction.user.id, str(interaction.user), str(self.reason.value).strip(), start, ret
        )
        row = extra_store.get_active_absence(interaction.guild.id, interaction.user.id)
        channel = interaction.guild.get_channel(ABSENCE_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("O canal de ausência configurado não foi encontrado.", ephemeral=True)
            return
        message = await channel.send(embed=build_absence_record_embed(row))
        extra_store.set_absence_message(absence_id, message.id)
        await interaction.response.send_message("✅ Sua ausência foi registrada.", ephemeral=True)


class AbsencePanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrar ausência", emoji="📅", style=discord.ButtonStyle.primary, custom_id="absence:register")
    async def register_absence(self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]) -> None:
        await interaction.response.send_modal(AbsenceModal())

    @discord.ui.button(label="Finalizar ausência", emoji="✅", style=discord.ButtonStyle.success, custom_id="absence:end")
    async def end_absence(self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
            return
        row = extra_store.end_absence(interaction.guild.id, interaction.user.id)
        if row is None:
            await interaction.response.send_message("Você não possui nenhuma ausência ativa.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(ABSENCE_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel) and row["message_id"]:
            try:
                msg = await channel.fetch_message(int(row["message_id"]))
                updated = dict(row)
                updated["status"] = "ended"
                await msg.edit(embed=build_absence_record_embed(updated, ended=True))
            except discord.DiscordException:
                pass
        await interaction.response.send_message("✅ Ausência finalizada. Seu status foi marcado como **RETORNOU**.", ephemeral=True)

    @discord.ui.button(label="Ausências ativas", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="absence:list")
    async def list_absences(self, interaction: discord.Interaction, button: discord.ui.Button[discord.ui.View]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
            return
        rows = extra_store.list_active_absences(interaction.guild.id)
        if not rows:
            text = "Nenhuma ausência ativa no momento."
        else:
            text = "\n".join(f"• <@{int(r['user_id'])}> — até **{r['return_date']}**" for r in rows[:25])
        await interaction.response.send_message(embed=discord.Embed(title="📋 AUSÊNCIAS ATIVAS", description=text, color=MEDELLIN_RED), ephemeral=True)


async def ensure_absence_panel(guild: discord.Guild) -> None:
    channel = guild.get_channel(ABSENCE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        logger.warning("Canal de ausência %s não encontrado.", ABSENCE_CHANNEL_ID)
        return
    existing = None
    async for message in channel.history(limit=50):
        if message.author == bot.user and message.embeds and message.embeds[0].title == "📅 AUSÊNCIAS • MEDELLÍN":
            existing = message
            break
    if existing:
        await existing.edit(embed=build_absence_panel_embed(), view=AbsencePanelView())
    else:
        await channel.send(embed=build_absence_panel_embed(), view=AbsencePanelView())
