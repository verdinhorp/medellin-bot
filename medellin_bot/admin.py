"""Painel de administração do estoque e DM do dono."""

from __future__ import annotations

import discord
from inventory_store import InventoryError

from .bot import InventoryBot, bot
from .config import FARM_CATEGORY_ID, MEDELLIN_RED, logger
from .stock import all_stock_categories
from .utils import resolve_category_value, validate_item_registration_name


class AdminNameModal(discord.ui.Modal):
    def __init__(self, mode: str, guild_id: int, category: str | None = None) -> None:
        titles = {
            "create_category": "📁 Criar categoria",
            "create_item": "📝 Criar item",
            "zero": "🧹 Zerar estoque",
        }
        super().__init__(title=titles[mode])
        self.mode = mode
        self.guild_id = guild_id
        self.category = category
        if mode == "zero":
            self.value_input = discord.ui.TextInput(
                label="Confirmação", placeholder="Digite ZERAR", min_length=5, max_length=5
            )
        else:
            self.value_input = discord.ui.TextInput(
                label="Nome", placeholder="somente minúsculo e singular", min_length=1, max_length=80
            )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is not None or interaction.user.id != bot.get_guild(self.guild_id).owner_id:
            await interaction.response.send_message("Somente a dona do servidor pode usar este painel.", ephemeral=True)
            return
        value = str(self.value_input.value)
        try:
            if self.mode == "create_category":
                bot.store.add_custom_category(self.guild_id, value)
            elif self.mode == "create_item":
                assert self.category is not None
                item_name = validate_item_registration_name(value)
                bot.store.add_catalog_item(self.guild_id, self.category, item_name)
            else:
                if value.strip() != "ZERAR":
                    await interaction.response.send_message("Cancelado. Digite exatamente `ZERAR` para confirmar.")
                    return
                bot.store.clear_inventory(self.guild_id)
                await bot.refresh_inventory_panel(self.guild_id, 0)
                guild = bot.get_guild(self.guild_id)
                if guild:
                    for channel in guild.text_channels:
                        if channel.category_id == FARM_CATEGORY_ID:
                            await bot.refresh_inventory_panel(self.guild_id, channel.id)
        except InventoryError as error:
            await interaction.followup.send(str(error))
            return
        # Confirma silenciosamente para não criar mensagens/logs na DM.
        await interaction.response.defer()


class AdminCategorySelect(discord.ui.Select):
    def __init__(self, guild_id: int, mode: str) -> None:
        self.guild_id = guild_id
        self.mode = mode
        options = [
            discord.SelectOption(label=label.lower(), value=value, emoji=emoji)
            for value, label, emoji in all_stock_categories(guild_id)
        ]
        super().__init__(placeholder="selecione a categoria", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = bot.get_guild(self.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("Somente a dona do servidor pode usar este painel.")
            return
        category = resolve_category_value(self.values[0])
        if self.mode == "create_item":
            # Remove o menu temporário antes de abrir o formulário do item.
            if interaction.message is not None:
                try:
                    await interaction.message.delete()
                except discord.DiscordException:
                    pass
            await interaction.response.send_modal(AdminNameModal("create_item", self.guild_id, category))
            return
        if self.mode == "delete_category":
            if not self.values[0].startswith("custom:"):
                await interaction.response.send_message("As categorias padrão não podem ser excluídas.")
                return
            try:
                removed = bot.store.remove_custom_category(self.guild_id, category)
            except InventoryError as error:
                await interaction.response.send_message(str(error))
                return
            await interaction.response.defer()
            if interaction.message is not None:
                try:
                    await interaction.message.delete()
                except discord.DiscordException:
                    pass
            return
        items = bot.store.list_catalog_items(self.guild_id, category)
        if not items:
            await interaction.response.send_message(f"Não há itens cadastrados em **{category}**.")
            return
        await interaction.response.edit_message(
            content=f"Categoria: **{category}**\nSelecione o item que deseja excluir:",
            view=AdminItemDeleteView(self.guild_id, category),
        )


class AdminCategorySelectView(discord.ui.View):
    def __init__(self, guild_id: int, mode: str) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminCategorySelect(guild_id, mode))


class AdminItemDeleteSelect(discord.ui.Select):
    def __init__(self, guild_id: int, category: str) -> None:
        self.guild_id = guild_id
        self.category = category
        options = [discord.SelectOption(label=name, value=name, emoji="📦") for name in bot.store.list_catalog_items(guild_id, category)[:25]]
        super().__init__(placeholder="selecione o item", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = bot.get_guild(self.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("Somente a dona do servidor pode usar este painel.")
            return
        try:
            removed = bot.store.remove_catalog_item(self.guild_id, self.category, self.values[0])
        except InventoryError as error:
            await interaction.response.send_message(str(error))
            return
        await interaction.response.defer()
        if interaction.message is not None:
            try:
                await interaction.message.delete()
            except discord.DiscordException:
                pass


class AdminItemDeleteView(discord.ui.View):
    def __init__(self, guild_id: int, category: str) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminItemDeleteSelect(guild_id, category))


class AdminItemCategoryDeleteSelect(discord.ui.Select):
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        options = []
        for value, label, emoji in all_stock_categories(guild_id):
            category = resolve_category_value(value)
            if bot.store.list_catalog_items(guild_id, category):
                options.append(discord.SelectOption(
                    label=label.lower(), value=category, emoji=emoji
                ))
        super().__init__(
            placeholder="selecione a categoria do item",
            options=options[:25],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = bot.get_guild(self.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Somente a dona do servidor pode usar este painel."
            )
            return
        category = self.values[0]
        await interaction.response.edit_message(
            content=f"Categoria: **{category}**\nSelecione o item para excluir:",
            view=AdminItemDeleteView(self.guild_id, category),
        )


class AdminItemCategoryDeleteView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminItemCategoryDeleteSelect(guild_id))


class AdminCategoryDeleteSelect(discord.ui.Select):
    """Menu exclusivo para apagar categorias personalizadas."""
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        categories = bot.store.list_custom_categories(guild_id)
        options = [
            discord.SelectOption(label=name.lower(), value=name, emoji="🗂️")
            for name in categories[:25]
        ]
        super().__init__(
            placeholder="selecione a categoria para excluir",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = bot.get_guild(self.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Somente a dona do servidor pode usar este painel."
            )
            return

        category = self.values[0]
        try:
            bot.store.remove_custom_category(self.guild_id, category)
        except InventoryError as error:
            await interaction.response.send_message(str(error))
            return

        await interaction.response.defer()
        if interaction.message is not None:
            try:
                await interaction.message.delete()
            except discord.DiscordException:
                pass


class AdminCategoryDeleteView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminCategoryDeleteSelect(guild_id))


class AdminPanelView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot, guild_id: int) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = self.inventory_bot.get_guild(self.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("Somente a dona do servidor pode usar este painel.")
            return False
        return True

    @discord.ui.button(label="Criar categoria", emoji="📁", style=discord.ButtonStyle.primary, custom_id="admin_stock:create_category")
    async def create_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AdminNameModal("create_category", self.guild_id))

    @discord.ui.button(label="Criar item", emoji="📝", style=discord.ButtonStyle.success, custom_id="admin_stock:create_item")
    async def create_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Selecione a categoria do novo item:", view=AdminCategorySelectView(self.guild_id, "create_item"))

    @discord.ui.button(label="Excluir item", emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="admin_stock:delete_item")
    async def delete_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Selecione a categoria do item:", view=AdminItemCategoryDeleteView(self.guild_id))

    @discord.ui.button(label="Excluir categoria", emoji="🗂️", style=discord.ButtonStyle.secondary, custom_id="admin_stock:delete_category")
    async def delete_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        categories = bot.store.list_custom_categories(self.guild_id)
        if not categories:
            await interaction.response.send_message(
                "Não há categorias personalizadas para excluir.",
                delete_after=5,
            )
            return
        await interaction.response.send_message(
            "Selecione a categoria que deseja excluir:",
            view=AdminCategoryDeleteView(self.guild_id),
        )

    @discord.ui.button(label="Zerar estoque", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="admin_stock:zero")
    async def zero_stock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AdminNameModal("zero", self.guild_id))


async def ensure_owner_admin_dm(guild: discord.Guild) -> None:
    """Mantém um painel administrativo no privado da dona do servidor."""
    try:
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        dm = owner.dm_channel or await owner.create_dm()
        embed = discord.Embed(
            title="⚙️ ADMINISTRAÇÃO DO ESTOQUE",
            description=(
                "Este painel é privado e somente você pode vê-lo e usá-lo.\n\n"
                "Crie categorias e itens aqui. Os membros apenas selecionam **categoria → item** "
                "e digitam a **quantidade**."
            ),
            color=MEDELLIN_RED,
        )
        embed.set_footer(text=f"Servidor: {guild.name}")
        existing = None
        async for message in dm.history(limit=30):
            if message.author == bot.user and message.embeds and message.embeds[0].title == "⚙️ ADMINISTRAÇÃO DO ESTOQUE":
                existing = message
                break
        view = AdminPanelView(bot, guild.id)
        if existing:
            await existing.edit(embed=embed, view=view)
        else:
            await dm.send(embed=embed, view=view)
    except discord.DiscordException:
        logger.exception("Não foi possível enviar o painel administrativo privado para a dona do servidor %s.", guild.id)
