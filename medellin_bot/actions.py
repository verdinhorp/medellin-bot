"""Registro de ações (GG/RED): painel, modais, seletores e finalização."""

from __future__ import annotations

import re
from typing import Optional

import discord

from .bot import bot
from .config import (
    ACTION_CHANNEL_ID,
    ACTION_HISTORY_CHANNEL_ID,
    FARM_CATEGORY_ID,
    MEDELLIN_RED,
    logger,
)
from .farm_channels import farm_channel_member, fetch_farm_channel_member
from .pvp import refresh_all_pvp_farm_panels, sync_current_pvp_week_from_actions


# =========================
# PAINEL DE REGISTRO DE AÇÕES
# =========================

def build_action_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎯 REGISTRO DE AÇÕES",
        description=(
            "Use o botão abaixo para registrar uma ação.\n"
            "Qualquer membro pode registrar e também confirmar **GG** ou **RED**. "
            "Ao finalizar, a ação vai para o histórico e para o FARM privado dos participantes."
        ),
        color=MEDELLIN_RED,
    )
    embed.set_footer(text="Clique em Registrar ação para preencher os dados.")
    return embed


def can_confirm_action(interaction: discord.Interaction) -> bool:
    """Qualquer membro do servidor pode finalizar uma ação como GG ou RED."""
    return interaction.guild is not None and isinstance(interaction.user, discord.Member)


# IDs das mensagens que já estão sendo/finalizadas. Isso impede dois cliques
# simultâneos (ou de usuários diferentes) de finalizarem a mesma ação duas vezes.
_ACTION_FINALIZATION_LOCKS: set[int] = set()


async def get_action_history_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(ACTION_HISTORY_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        try:
            fetched = await guild.fetch_channel(ACTION_HISTORY_CHANNEL_ID)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.DiscordException:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


def action_participant_ids(embed: discord.Embed) -> list[int]:
    """Extrai os IDs das menções reais salvas no campo Participantes."""
    for field in embed.fields:
        if field.name == "👥 Participantes":
            return list(dict.fromkeys(int(x) for x in re.findall(r"<@!?(\d+)>", str(field.value))))
    return []


async def farm_channel_for_member(guild: discord.Guild, member_id: int) -> Optional[discord.TextChannel]:
    for channel in guild.text_channels:
        if channel.category_id != FARM_CATEGORY_ID:
            continue
        try:
            member = await fetch_farm_channel_member(channel)
        except Exception:
            member = farm_channel_member(channel)
        if member is not None and member.id == member_id:
            return channel
    return None


async def send_action_to_participant_farms(guild: discord.Guild, embed: discord.Embed) -> None:
    """Copia a ação finalizada para o canal FARM privado de cada participante marcado."""
    for member_id in action_participant_ids(embed):
        channel = await farm_channel_for_member(guild, member_id)
        if channel is None:
            logger.warning("FARM privado não encontrado para participante %s.", member_id)
            continue
        try:
            copy = embed.copy()
            copy.title = "🎯 HISTÓRICO DE AÇÃO PVP"
            await channel.send(embed=copy, allowed_mentions=discord.AllowedMentions.none())
        except discord.DiscordException:
            logger.exception("Não foi possível enviar a ação ao FARM privado de %s.", member_id)


def finalized_action_embed(original: discord.Embed, result: str, user: discord.abc.User) -> discord.Embed:
    is_gg = result == "GG"
    embed = discord.Embed(
        title="🎯 AÇÃO FINALIZADA",
        color=discord.Color.green() if is_gg else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    # Mantém os dados preenchidos no registro original.
    for field in original.fields:
        if field.name not in {"📌 Status", "🏆 Resultado", "✅ Confirmado por"}:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)

    embed.insert_field_at(
        0,
        name="📌 Status",
        value="🟢 **FINALIZADA — GG**" if is_gg else "🔴 **FINALIZADA — RED**",
        inline=False,
    )
    embed.add_field(
        name="🏆 Resultado",
        value="🟢 **GG**" if is_gg else "🔴 **RED**",
        inline=True,
    )
    embed.add_field(name="✅ Confirmado por", value=user.mention, inline=True)
    embed.set_footer(text=f"Resultado confirmado por {user} • ID: {user.id}")
    return embed


class ActionResultView(discord.ui.View):
    def __init__(self, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        if disabled:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True

    async def finish_action(self, interaction: discord.Interaction, result: str) -> None:
        if interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message(
                "Não encontrei os dados desta ação.", ephemeral=True
            )
            return

        message_id = interaction.message.id

        # Uma ação só pode ser finalizada uma vez. O lock é colocado ANTES de
        # qualquer operação assíncrona para impedir duas confirmações simultâneas.
        if message_id in _ACTION_FINALIZATION_LOCKS:
            await interaction.response.send_message(
                "⏳ Essa ação já está sendo finalizada por outra pessoa.", ephemeral=True
            )
            return
        _ACTION_FINALIZATION_LOCKS.add(message_id)

        success = False
        try:
            original = interaction.message.embeds[0]
            if original.title == "🎯 AÇÃO FINALIZADA":
                await interaction.response.send_message(
                    "Essa ação já foi finalizada.", ephemeral=True
                )
                success = True
                return

            if not can_confirm_action(interaction):
                await interaction.response.send_message(
                    "Você não pode confirmar esta ação.", ephemeral=True
                )
                return

            embed = finalized_action_embed(original, result, interaction.user)
            await interaction.response.defer(ephemeral=True)

            # Desabilita os botões imediatamente. Mesmo que alguém ainda tenha
            # a mensagem antiga aberta, o lock acima bloqueia a segunda confirmação.
            try:
                await interaction.message.edit(view=ActionResultView(disabled=True))
            except discord.DiscordException:
                logger.warning("Não foi possível desabilitar os botões da ação %s.", message_id)

            if interaction.guild is None:
                await interaction.followup.send("Não encontrei o servidor desta ação.", ephemeral=True)
                return

            history_channel = await get_action_history_channel(interaction.guild)
            if history_channel is None:
                await interaction.followup.send(
                    "❌ Não encontrei o canal de histórico de ações configurado.", ephemeral=True
                )
                return

            try:
                await history_channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                await send_action_to_participant_farms(interaction.guild, embed)
                # Remove a ação finalizada do canal de registro: o histórico fica na aba própria.
                await interaction.message.delete()
            except discord.DiscordException:
                logger.exception("Não foi possível mover a ação finalizada para o histórico.")
                await interaction.followup.send(
                    "❌ Não consegui mover a ação para o histórico. Verifique as permissões do bot.",
                    ephemeral=True,
                )
                return

            try:
                await sync_current_pvp_week_from_actions(interaction.guild)
                await refresh_all_pvp_farm_panels(interaction.guild)
            except Exception:
                logger.exception("Não foi possível recalcular o resultado semanal do PVP após finalizar a ação.")

            success = True
            await interaction.followup.send(
                f"✅ Ação finalizada como **{result}** e enviada para {history_channel.mention}.",
                ephemeral=True,
            )
        finally:
            # Mantém o ID travado quando a ação foi concluída com sucesso.
            # Se houve erro antes da conclusão, libera para permitir uma nova tentativa.
            if not success:
                _ACTION_FINALIZATION_LOCKS.discard(message_id)

    @discord.ui.button(
        label="Confirmar GG",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="action_result:gg",
    )
    async def confirm_gg(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.finish_action(interaction, "GG")

    @discord.ui.button(
        label="Confirmar RED",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="action_result:red",
    )
    async def confirm_red(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.finish_action(interaction, "RED")


class ActionRegisterModal(discord.ui.Modal, title="🎯 Registrar ação"):
    def __init__(self, currency: str) -> None:
        super().__init__()
        self.currency = "USD" if currency == "USD" else "BRL"

    action_name = discord.ui.TextInput(
        label="Ação",
        placeholder="Ex.: ammu north, madeireira...",
        min_length=1,
        max_length=100,
        required=True,
    )
    action_value = discord.ui.TextInput(
        label="Valor",
        placeholder="Ex.: 333.000 ou 278 madeiras ilegais",
        min_length=1,
        max_length=150,
        required=True,
    )
    action_time = discord.ui.TextInput(
        label="Horário",
        placeholder="Ex.: 23:00",
        min_length=1,
        max_length=20,
        required=True,
    )
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Esse painel só funciona dentro do servidor.", ephemeral=True
            )
            return

        data = {
            "currency": self.currency,
            "action_name": str(self.action_name.value).strip(),
            "action_value": str(self.action_value.value).strip(),
            "action_time": str(self.action_time.value).strip(),
            "registered_by": interaction.user.id,
        }
        await interaction.response.send_message(
            "👥 **Agora selecione os membros que participaram da ação:**",
            view=ActionParticipantSelectView(data),
            ephemeral=True,
        )


class ActionParticipantSelect(discord.ui.UserSelect):
    def __init__(self, data: dict) -> None:
        self.data = data
        super().__init__(
            placeholder="Selecione os participantes da ação",
            min_values=1,
            max_values=25,
            custom_id="action_participants:select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Esse seletor só funciona no servidor.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(ACTION_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await interaction.guild.fetch_channel(ACTION_CHANNEL_ID)
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except discord.DiscordException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Não encontrei o canal de registro de ações configurado.", ephemeral=True
            )
            return

        members = [u for u in self.values if not getattr(u, "bot", False)]
        if not members:
            await interaction.response.send_message("Selecione pelo menos um membro.", ephemeral=True)
            return

        mentions = " ".join(u.mention for u in members)
        embed = discord.Embed(
            title="🎯 AÇÃO REGISTRADA",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="📌 Status", value="🟡 **EM ABERTO**", inline=False)
        embed.add_field(name="🎯 Ação", value=self.data["action_name"], inline=False)
        moeda_texto = "🇺🇸 Dólar ($)" if self.data["currency"] == "USD" else "🇧🇷 Real (R$)"
        embed.add_field(name="💱 Moeda", value=moeda_texto, inline=True)
        embed.add_field(name="💰 Valor", value=self.data["action_value"], inline=True)
        embed.add_field(name="🕒 Horário", value=self.data["action_time"], inline=True)
        embed.add_field(name="👥 Participantes", value=mentions, inline=False)
        embed.add_field(name="👤 Registrado por", value=f"<@{self.data['registered_by']}>", inline=False)
        embed.set_footer(text=f"ID do responsável: {self.data['registered_by']}")

        await interaction.response.defer(ephemeral=True)
        try:
            await channel.send(
                embed=embed,
                view=ActionResultView(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.DiscordException:
            logger.exception("Não foi possível publicar o registro de ação.")
            await interaction.followup.send(
                "Não consegui publicar a ação. Verifique as permissões do bot no canal.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ Ação registrada em {channel.mention} com {len(members)} participante(s).", ephemeral=True
        )


class ActionParticipantSelectView(discord.ui.View):
    def __init__(self, data: dict) -> None:
        super().__init__(timeout=300)
        self.add_item(ActionParticipantSelect(data))


class ActionCurrencySelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Real (BRL)",
                value="BRL",
                emoji="🇧🇷",
                description="Registrar o valor da ação em reais",
            ),
            discord.SelectOption(
                label="Dólar (USD)",
                value="USD",
                emoji="🇺🇸",
                description="Registrar o valor da ação em dólares",
            ),
        ]
        super().__init__(
            placeholder="Selecione a moeda da ação",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ActionRegisterModal(self.values[0]))


class ActionCurrencySelectView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.add_item(ActionCurrencySelect())


class ActionPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar ação",
        emoji="🎯",
        style=discord.ButtonStyle.success,
        custom_id="action_panel:register",
    )
    async def register_action(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.channel_id != ACTION_CHANNEL_ID:
            await interaction.response.send_message(
                "Use esse botão no canal de registro de ações.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "💱 **Selecione a moeda desta ação:**",
            view=ActionCurrencySelectView(),
            ephemeral=True,
        )


async def ensure_action_panel(guild: discord.Guild) -> None:
    channel = guild.get_channel(ACTION_CHANNEL_ID)
    if channel is None:
        try:
            channel = await guild.fetch_channel(ACTION_CHANNEL_ID)
        except discord.DiscordException:
            return
    if not isinstance(channel, discord.TextChannel):
        return

    existing = None
    async for message in channel.history(limit=50):
        if (
            message.author == bot.user
            and message.embeds
            and message.embeds[0].title == "🎯 REGISTRO DE AÇÕES"
        ):
            existing = message
            break

    try:
        if existing is None:
            await channel.send(embed=build_action_panel_embed(), view=ActionPanelView())
        else:
            await existing.edit(embed=build_action_panel_embed(), view=ActionPanelView())
    except discord.DiscordException:
        logger.exception("Não foi possível criar/atualizar o painel de ações.")
