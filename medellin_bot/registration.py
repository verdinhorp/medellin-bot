"""Registro de membros: painel, modal e revisão (aprovar/recusar)."""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

import discord

from .assets import ensure_embedded_registration_images
from .bot import bot
from .config import (
    REGISTERED_ROLE_ID,
    REGISTRATION_BANNER_PATH,
    REGISTRATION_CHANNEL_ID,
    REGISTRATION_LOGO_PATH,
    REGISTRATION_REVIEW_CHANNEL_ID,
    logger,
)


# =========================
# REGISTRO MEDELLÍN
# =========================

FARM_CATEGORY_ID = 1538557607894323241

def build_registration_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="📋 REGISTRO — MEDELLÍN",
        description=(
            "Bem-vindo(a) à **Medellín**! Para liberar seu acesso ao servidor, "
            "clique no botão abaixo e realize seu registro.\n\n"
            "Preencha suas informações corretamente e aguarde a análise da liderança."
        ),
        color=discord.Color.blue(),
    )
    # As duas imagens são enviadas como anexos da própria mensagem do bot.
    embed.set_thumbnail(url="attachment://medellin_registro_logo.png")
    embed.set_image(url="attachment://medellin_registro_banner.png")
    embed.set_footer(text="MEDELLÍN • Sistema de Registro")
    return embed


class RegistrationModal(discord.ui.Modal, title="📋 Registro Medellín"):
    rp_name = discord.ui.TextInput(
        label="Nome no RP", placeholder="Ex: Milena Dezzotti", min_length=2, max_length=80, required=True
    )
    rp_id = discord.ui.TextInput(
        label="ID", placeholder="Ex: 1148", min_length=1, max_length=20, required=True
    )
    phone = discord.ui.TextInput(
        label="Telefone", placeholder="Ex: (01) 554-063", min_length=3, max_length=40, required=True
    )
    recruiter = discord.ui.TextInput(
        label="Quem recrutou?", placeholder="Ex: Nome do recrutador", min_length=2, max_length=80, required=True
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse registro só funciona dentro do servidor.", ephemeral=True)
            return

        review_channel = interaction.guild.get_channel(REGISTRATION_REVIEW_CHANNEL_ID)
        if review_channel is None:
            try:
                review_channel = await interaction.guild.fetch_channel(REGISTRATION_REVIEW_CHANNEL_ID)
            except discord.DiscordException:
                review_channel = None
        if not isinstance(review_channel, discord.TextChannel):
            await interaction.response.send_message("Não encontrei o canal de registros abertos.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 NOVO REGISTRO", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
        embed.add_field(name="👤 Nome no RP", value=str(self.rp_name.value).strip(), inline=False)
        embed.add_field(name="🪪 ID", value=str(self.rp_id.value).strip(), inline=True)
        embed.add_field(name="📱 Telefone", value=str(self.phone.value).strip(), inline=True)
        embed.add_field(name="🤝 Quem recrutou?", value=str(self.recruiter.value).strip(), inline=False)
        embed.add_field(name="💬 Discord", value=interaction.user.mention, inline=False)
        embed.add_field(name="📌 Status", value="🟡 **AGUARDANDO ANÁLISE**", inline=False)
        embed.set_footer(text=f"registration_user:{interaction.user.id}")

        await interaction.response.defer(ephemeral=True)
        try:
            await review_channel.send(embed=embed, view=RegistrationReviewView())
        except discord.DiscordException:
            logger.exception("Não foi possível enviar o registro para análise.")
            await interaction.followup.send("Não consegui enviar seu registro. Avise a liderança.", ephemeral=True)
            return
        await interaction.followup.send("✅ Registro enviado! Aguarde a análise da liderança.", ephemeral=True)


class RegistrationPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="REALIZAR REGISTRO", emoji="📝", style=discord.ButtonStyle.danger,
        custom_id="registration:open"
    )
    async def open_registration(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.channel_id != REGISTRATION_CHANNEL_ID:
            await interaction.response.send_message("Use esse botão no canal de registro.", ephemeral=True)
            return
        await interaction.response.send_modal(RegistrationModal())


def can_review_registration(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    return interaction.user.guild_permissions.manage_roles or interaction.user.guild_permissions.administrator


async def create_member_farm_channel(
    guild: discord.Guild,
    member: discord.Member,
    rp_name: str,
    rp_id: str = "",
) -> Optional[discord.TextChannel]:
    """Cria um canal privado de FARM para o membro aprovado."""
    category = guild.get_channel(FARM_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(FARM_CATEGORY_ID)
        except discord.DiscordException:
            logger.exception("Não foi possível localizar a categoria de FARM (%s).", FARM_CATEGORY_ID)
            return None

    if not isinstance(category, discord.CategoryChannel):
        logger.error("O ID configurado para FARM não é uma categoria: %s", FARM_CATEGORY_ID)
        return None

    # Evita criar outro canal se o membro já tiver um FARM nessa categoria.
    for channel in category.text_channels:
        if channel.overwrites_for(member).view_channel is True:
            return channel

    # Converte o nome informado no registro em um nome válido para canal do Discord.
    normalized = unicodedata.normalize("NFKD", rp_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", normalized.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug).strip("-")

    # Inclui o ID informado no registro para seguir o padrão: 📦・nome-1468
    normalized_id = unicodedata.normalize("NFKD", str(rp_id)).encode("ascii", "ignore").decode("ascii")
    id_slug = re.sub(r"[^a-zA-Z0-9-]+", "-", normalized_id.lower()).strip("-")
    id_slug = re.sub(r"-+", "-", id_slug).strip("-")

    channel_base = "-".join(part for part in (slug, id_slug) if part)
    channel_base = channel_base[:85].strip("-") or f"membro-{member.id}"
    channel_name = f"📦・{channel_base}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }
    bot_member = guild.me
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_permissions=True,
        )

    try:
        channel = await category.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"Canal de FARM de {rp_name} | Membro: {member.id}",
            reason=f"Canal de FARM criado após aprovação do registro de {member}",
        )
        await channel.send(f"Bem-vindo ao seu canal de FARM, {member.mention}! **Nome no RP:** {rp_name}")
        return channel
    except discord.DiscordException:
        logger.exception("Não foi possível criar o canal de FARM para %s (%s).", member, member.id)
        return None


async def get_registration_member(interaction: discord.Interaction) -> Optional[discord.Member]:
    if interaction.guild is None or interaction.message is None or not interaction.message.embeds:
        return None
    footer = interaction.message.embeds[0].footer.text or ""
    if not footer.startswith("registration_user:"):
        return None
    try:
        user_id = int(footer.split(":", 1)[1])
    except ValueError:
        return None
    member = interaction.guild.get_member(user_id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(user_id)
        except discord.DiscordException:
            return None
    return member


class RegistrationReviewView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="APROVAR", emoji="✅", style=discord.ButtonStyle.success, custom_id="registration:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_review_registration(interaction):
            await interaction.response.send_message("Você não tem permissão para aprovar registros.", ephemeral=True)
            return
        member = await get_registration_member(interaction)
        if member is None or interaction.guild is None or interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message("Não consegui localizar o membro deste registro.", ephemeral=True)
            return
        role = interaction.guild.get_role(REGISTERED_ROLE_ID)
        if role is None:
            await interaction.response.send_message("Não encontrei o cargo configurado para aprovados.", ephemeral=True)
            return

        embed = interaction.message.embeds[0].copy()
        rp_name = next((f.value for f in embed.fields if f.name == "👤 Nome no RP"), member.display_name)
        rp_id = next((f.value for f in embed.fields if f.name == "🪪 ID"), "")
        try:
            await member.add_roles(role, reason=f"Registro aprovado por {interaction.user}")
            nickname = f"{rp_name} | {rp_id}"[:32]
            await member.edit(nick=nickname, reason=f"Registro aprovado por {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Não consegui dar o cargo ou alterar o apelido. Coloque o cargo do bot acima do cargo de membro.", ephemeral=True
            )
            return
        except discord.DiscordException:
            await interaction.response.send_message("O Discord não permitiu concluir a aprovação.", ephemeral=True)
            return

        farm_channel = await create_member_farm_channel(interaction.guild, member, rp_name, rp_id)

        for idx, field in enumerate(embed.fields):
            if field.name == "📌 Status":
                embed.set_field_at(idx, name="📌 Status", value=f"🟢 **APROVADO**\nPor: {interaction.user.mention}", inline=False)
                break
        embed.title = "✅ REGISTRO APROVADO"
        embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=None)
        if farm_channel is None:
            await interaction.followup.send(
                "✅ Registro aprovado e cargo aplicado, mas não consegui criar o canal de FARM. "
                "Confira se a categoria existe e se o bot tem Gerenciar Canais.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Registro aprovado! Canal de FARM criado: {farm_channel.mention}",
                ephemeral=True,
            )

    @discord.ui.button(label="RECUSAR", emoji="❌", style=discord.ButtonStyle.danger, custom_id="registration:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_review_registration(interaction):
            await interaction.response.send_message("Você não tem permissão para recusar registros.", ephemeral=True)
            return
        if interaction.message is None or not interaction.message.embeds:
            return
        embed = interaction.message.embeds[0].copy()
        for idx, field in enumerate(embed.fields):
            if field.name == "📌 Status":
                embed.set_field_at(idx, name="📌 Status", value=f"🔴 **RECUSADO**\nPor: {interaction.user.mention}", inline=False)
                break
        embed.title = "❌ REGISTRO RECUSADO"
        embed.color = discord.Color.red()
        await interaction.response.edit_message(embed=embed, view=None)


async def ensure_registration_panel(guild: discord.Guild) -> None:
    channel = guild.get_channel(REGISTRATION_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(REGISTRATION_CHANNEL_ID)
        except discord.DiscordException:
            return
    if not isinstance(channel, discord.TextChannel):
        return

    existing = None
    async for message in channel.history(limit=50):
        if message.author == bot.user and message.embeds and message.embeds[0].title == "📋 REGISTRO — MEDELLÍN":
            existing = message
            break
    try:
        # Se já existir um painel antigo, apaga para conseguir substituir também os anexos.
        if existing is not None:
            try:
                await existing.delete()
            except discord.DiscordException:
                pass

        # Garante que as imagens existam mesmo quando somente o main.py foi enviado.
        ensure_embedded_registration_images()

        missing = [
            path for path in (REGISTRATION_BANNER_PATH, REGISTRATION_LOGO_PATH)
            if not os.path.isfile(path)
        ]
        if missing:
            logger.error(
                "Imagens do painel de registro não encontradas: %s",
                ", ".join(missing),
            )
            return

        banner_file = discord.File(
            REGISTRATION_BANNER_PATH, filename="medellin_registro_banner.png"
        )
        logo_file = discord.File(
            REGISTRATION_LOGO_PATH, filename="medellin_registro_logo.png"
        )
        await channel.send(
            embed=build_registration_panel_embed(guild),
            view=RegistrationPanelView(),
            files=[banner_file, logo_file],
        )
    except discord.DiscordException:
        logger.exception("Não foi possível criar/atualizar o painel de registro.")
