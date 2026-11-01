"""Ajuda para canais FARM: escopo de canal, membro dono do canal e estado PVP."""

from __future__ import annotations

from typing import Optional

import discord

from .config import FARM_CATEGORY_ID, PVP_ROLE_ID
from .utils import _norm_discord_text


def farm_channel_member(channel: discord.TextChannel) -> Optional[discord.Member]:
    """Tenta descobrir o dono do canal de farm pelas permissões diretas ou pelo nome."""
    pvp_role = channel.guild.get_role(PVP_ROLE_ID)
    direct_members = [target for target in channel.overwrites if isinstance(target, discord.Member) and not target.bot]
    if pvp_role is not None:
        pvp_direct = [m for m in direct_members if pvp_role in m.roles]
        if len(pvp_direct) == 1:
            return pvp_direct[0]
    if len(direct_members) == 1:
        return direct_members[0]
    channel_key = _norm_discord_text(channel.name)
    candidates = []
    for member in channel.guild.members:
        if member.bot:
            continue
        if _norm_discord_text(member.display_name) and _norm_discord_text(member.display_name) in channel_key:
            candidates.append(member)
    if pvp_role is not None:
        pvp_candidates = [m for m in candidates if pvp_role in m.roles]
        if len(pvp_candidates) == 1:
            return pvp_candidates[0]
    return candidates[0] if len(candidates) == 1 else None


def is_pvp_farm_channel(channel: object) -> bool:
    if not isinstance(channel, discord.TextChannel) or channel.category_id != FARM_CATEGORY_ID:
        return False
    member = farm_channel_member(channel)
    role = channel.guild.get_role(PVP_ROLE_ID)
    return member is not None and role is not None and role in member.roles


async def fetch_farm_channel_member(channel: discord.TextChannel) -> Optional[discord.Member]:
    """Descobre o dono do FARM e consulta os cargos atuais pela API do Discord."""
    # Primeiro tenta achar membros com overwrite direto no canal.
    candidate_ids: list[int] = []

    try:
        for target in channel.overwrites.keys():
            if isinstance(target, discord.Member) and not target.bot:
                candidate_ids.append(target.id)
    except Exception:
        pass

    # Fallback pelos overwrites internos, útil quando o cache do Discord está incompleto.
    if not candidate_ids:
        for overwrite in getattr(channel, "_overwrites", []):
            if getattr(overwrite, "type", None) == 1:  # member overwrite
                try:
                    candidate_ids.append(int(overwrite.id))
                except Exception:
                    pass

    candidates: list[discord.Member] = []
    seen: set[int] = set()
    for member_id in candidate_ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        try:
            member = await channel.guild.fetch_member(member_id)
        except discord.DiscordException:
            member = channel.guild.get_member(member_id)
        if member is not None and not member.bot:
            candidates.append(member)

    # Se houver só um membro com overwrite direto, ele é o dono do FARM.
    if len(candidates) == 1:
        return candidates[0]

    # Se houver mais de um, tenta casar com o nome do canal.
    channel_key = _norm_discord_text(channel.name)
    for member in candidates:
        display_key = _norm_discord_text(member.display_name)
        user_key = _norm_discord_text(member.name)
        if display_key and display_key in channel_key:
            return member
        if user_key and user_key in channel_key:
            return member

    # Fallback para a lógica antiga; depois força fetch_member para pegar cargos atuais.
    cached = farm_channel_member(channel)
    if cached is not None:
        try:
            return await channel.guild.fetch_member(cached.id)
        except discord.DiscordException:
            return cached

    return candidates[0] if candidates else None


async def fresh_pvp_state(channel: discord.TextChannel) -> tuple[bool, Optional[discord.Member]]:
    member = await fetch_farm_channel_member(channel)
    if member is None:
        return False, None
    return any(role.id == PVP_ROLE_ID for role in member.roles), member


def scope_channel_id_from_channel(channel: object) -> int:
    if isinstance(channel, discord.TextChannel) and channel.category_id == FARM_CATEGORY_ID:
        return channel.id
    return 0


def scope_channel_id(interaction: discord.Interaction) -> int:
    return scope_channel_id_from_channel(interaction.channel)


def is_farm_channel(channel: object) -> bool:
    return scope_channel_id_from_channel(channel) != 0
