"""Avisos gerais e da gerência."""

from __future__ import annotations

import discord

from .bot import bot
from .config import (
    GENERAL_NOTICE_CHANNEL_ID,
    MANAGEMENT_NOTICE_CHANNEL_ID,
    NOTICE_ALLOWED_ROLE_IDS,
    logger,
)


# ---------------------------------------------------------------------------
# Sistema de avisos
# ---------------------------------------------------------------------------
def can_send_notice(member: discord.Member) -> bool:
    return member.bot or any(r.id in NOTICE_ALLOWED_ROLE_IDS for r in member.roles)


class NoticeModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title="Aviso Geral" if kind=="general" else "Aviso Gerência", timeout=300)
        self.kind=kind
        self.subject=discord.ui.TextInput(label="Título do aviso", placeholder="Ex.: Reunião / Comunicado", max_length=100)
        self.message=discord.ui.TextInput(label="Mensagem", placeholder="Digite o aviso...", style=discord.TextStyle.paragraph, max_length=1800)
        self.add_item(self.subject); self.add_item(self.message)
    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not can_send_notice(interaction.user):
            return await interaction.response.send_message("❌ Você não tem permissão para enviar avisos.", ephemeral=True)
        cid=GENERAL_NOTICE_CHANNEL_ID if self.kind=="general" else MANAGEMENT_NOTICE_CHANNEL_ID
        ch=interaction.guild.get_channel(cid) if interaction.guild else None
        if ch is None:
            try: ch=await bot.fetch_channel(cid)
            except discord.DiscordException: ch=None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("❌ Não consegui acessar o canal do aviso.", ephemeral=True)
        general=self.kind=="general"
        e=discord.Embed(title="📢 AVISO GERAL" if general else "👑 AVISO GERÊNCIA",
                        description=str(self.message.value), color=discord.Color.blue() if general else discord.Color.gold(),
                        timestamp=discord.utils.utcnow())
        e.add_field(name="📌 Assunto", value=str(self.subject.value), inline=False)
        e.add_field(name="👤 Enviado por", value=interaction.user.mention, inline=True)
        e.set_footer(text="MEDELLÍN • Sistema de Avisos")
        await ch.send(embed=e)
        await interaction.response.send_message(f"✅ Aviso enviado em {ch.mention}.", ephemeral=True)


class GeneralNoticePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if isinstance(interaction.user, discord.Member) and can_send_notice(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar o sistema de avisos.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Enviar Aviso Geral",
        emoji="📢",
        style=discord.ButtonStyle.primary,
        custom_id="medellin:notice:general",
    )
    async def general(self, interaction, button):
        await interaction.response.send_modal(NoticeModal("general"))


class ManagementNoticePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if isinstance(interaction.user, discord.Member) and can_send_notice(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar o sistema de avisos.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Enviar Aviso Gerência",
        emoji="👑",
        style=discord.ButtonStyle.secondary,
        custom_id="medellin:notice:management",
    )
    async def management(self, interaction, button):
        await interaction.response.send_modal(NoticeModal("management"))


def general_notice_panel_embed():
    e = discord.Embed(
        title="📢 AVISOS GERAIS • MEDELLÍN",
        description=(
            "Utilize o botão abaixo para publicar um aviso para **todo o servidor**.\n\n"
            "🔒 Apenas cargos autorizados podem enviar comunicados."
        ),
        color=discord.Color.blue(),
    )
    e.set_footer(text="MEDELLÍN • Avisos Gerais")
    return e


def management_notice_panel_embed():
    e = discord.Embed(
        title="👑 AVISOS DA GERÊNCIA • MEDELLÍN",
        description=(
            "Utilize o botão abaixo para publicar um aviso exclusivo para a **gerência**.\n\n"
            "🔒 Apenas cargos autorizados podem enviar comunicados."
        ),
        color=discord.Color.gold(),
    )
    e.set_footer(text="MEDELLÍN • Avisos da Gerência")
    return e


async def _ensure_one_notice_panel(bot_instance, channel_id, title, embed, view):
    ch = bot_instance.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot_instance.fetch_channel(channel_id)
        except discord.DiscordException:
            ch = None

    if not isinstance(ch, discord.TextChannel):
        logger.error("Canal de avisos não encontrado: %s", channel_id)
        return

    # Remove o painel antigo combinado, caso ele ainda esteja no canal.
    try:
        async for m in ch.history(limit=100):
            if m.author.id != bot_instance.user.id or not m.embeds:
                continue
            old_title = m.embeds[0].title or ""
            if old_title == "📣 CENTRAL DE AVISOS • MEDELLÍN":
                try:
                    await m.delete()
                except discord.DiscordException:
                    pass

        # Atualiza um painel separado já existente, evitando duplicatas.
        async for m in ch.history(limit=100):
            if (
                m.author.id == bot_instance.user.id
                and m.embeds
                and (m.embeds[0].title or "") == title
            ):
                await m.edit(embed=embed, view=view)
                return

        await ch.send(embed=embed, view=view)
    except discord.DiscordException:
        logger.exception("Não foi possível criar o painel de avisos no canal %s.", channel_id)


async def ensure_notice_panel(bot_instance):
    await _ensure_one_notice_panel(
        bot_instance,
        GENERAL_NOTICE_CHANNEL_ID,
        "📢 AVISOS GERAIS • MEDELLÍN",
        general_notice_panel_embed(),
        GeneralNoticePanelView(),
    )
    await _ensure_one_notice_panel(
        bot_instance,
        MANAGEMENT_NOTICE_CHANNEL_ID,
        "👑 AVISOS DA GERÊNCIA • MEDELLÍN",
        management_notice_panel_embed(),
        ManagementNoticePanelView(),
    )
