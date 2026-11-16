from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from image_card import create_welcome_card

log = logging.getLogger(__name__)


def render_welcome_template(template: str, member: discord.Member) -> str:
    replacements = {
        "<id>": member.display_name,
        "%guild%": member.guild.name,
        "%user%": member.mention,
        "%member_count%": str(member.guild.member_count or len(member.guild.members)),
    }
    result = template
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result[:1900]


def parse_channel_id(value: str) -> Optional[int]:
    match = re.fullmatch(r"\s*(?:<#(\d+)>|(\d+))\s*", value)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


class WelcomeConfigModal(discord.ui.Modal, title="Welcome Message Setup"):
    channel = discord.ui.TextInput(
        label="Welcome channel ID or mention",
        placeholder="Example: <#123456789012345678> or 123456789012345678",
        max_length=80,
    )
    template = discord.ui.TextInput(
        label="Welcome message template",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder="Welcome <id> to %guild%! You are member #%member_count%.",
    )

    def __init__(self, current_channel: str = "", current_template: str = "") -> None:
        super().__init__()
        if current_channel:
            self.channel.default = current_channel
        if current_template:
            self.template.default = current_template

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This form can only be used in a server.", ephemeral=True)
            return

        channel_id = parse_channel_id(str(self.channel.value))
        if channel_id is None:
            await interaction.response.send_message(
                "I could not read that channel. Use a channel mention or its numeric ID.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "That ID is not a text channel in this server.", ephemeral=True
            )
            return

        await interaction.client.db.update_guild_settings(
            interaction.guild.id,
            welcome_channel_id=channel.id,
            welcome_template=str(self.template.value).strip(),
        )
        await interaction.response.send_message(
            f"Welcome settings saved. Channel: {channel.mention}", ephemeral=True
        )


class WelcomeImageModal(discord.ui.Modal, title="Welcome Image Setup"):
    image_url = discord.ui.TextInput(
        label="Direct HTTPS image URL",
        placeholder="https://example.com/welcome-background.png",
        required=False,
        max_length=1000,
    )

    def __init__(self, current_url: str = "") -> None:
        super().__init__()
        if current_url:
            self.image_url.default = current_url

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This form can only be used in a server.", ephemeral=True)
            return

        url = str(self.image_url.value).strip() or None
        if url and not url.lower().startswith("https://"):
            await interaction.response.send_message(
                "Please use a direct HTTPS image URL.", ephemeral=True
            )
            return

        await interaction.client.db.update_guild_settings(
            interaction.guild.id, welcome_image_url=url
        )
        result = "Welcome image URL saved." if url else "Welcome image URL cleared."
        await interaction.response.send_message(result, ephemeral=True)


class Welcome(commands.Cog):
    welcome = app_commands.Group(name="welcome", description="Configure welcome messages and images")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @welcome.command(name="configure", description="Set the welcome channel and message template")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def configure(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        channel_value = (
            f"{settings['welcome_channel_id']}" if settings["welcome_channel_id"] else ""
        )
        await interaction.response.send_modal(
            WelcomeConfigModal(channel_value, settings["welcome_template"])
        )

    @welcome.command(name="enable", description="Enable or disable welcome messages")
    @app_commands.describe(enabled="Turn welcome messages on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def enable(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, welcome_enabled=int(enabled)
        )
        await interaction.response.send_message(
            f"Welcome messages are now {'enabled' if enabled else 'disabled'}.",
            ephemeral=True,
        )

    @welcome.command(name="image", description="Open a form to set the welcome background image URL")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def image(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        await interaction.response.send_modal(WelcomeImageModal(settings["welcome_image_url"] or ""))

    @welcome.command(name="image-toggle", description="Enable or disable welcome images")
    @app_commands.describe(enabled="Turn welcome images on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def image_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, welcome_image_enabled=int(enabled)
        )
        await interaction.response.send_message(
            f"Welcome images are now {'enabled' if enabled else 'disabled'}.",
            ephemeral=True,
        )

    @welcome.command(name="avatar-position", description="Choose where the joining member's avatar appears")
    @app_commands.choices(
        position=[
            app_commands.Choice(name="Left", value="left"),
            app_commands.Choice(name="Center", value="center"),
            app_commands.Choice(name="Right", value="right"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def avatar_position(
        self, interaction: discord.Interaction, position: app_commands.Choice[str]
    ) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, avatar_position=position.value
        )
        await interaction.response.send_message(
            f"Avatar position set to **{position.name}**.", ephemeral=True
        )

    @welcome.command(name="test", description="Preview the current welcome message and image")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def test(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        sample_member = interaction.user
        if not isinstance(sample_member, discord.Member):
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        content = render_welcome_template(settings["welcome_template"], sample_member)
        if settings["welcome_image_enabled"] and settings["welcome_image_url"]:
            try:
                card = await create_welcome_card(
                    sample_member,
                    settings["welcome_image_url"],
                    settings["avatar_position"],
                )
                await interaction.followup.send(
                    content=content,
                    file=discord.File(card, filename="welcome-preview.png"),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                )
                return
            except Exception as exc:
                log.warning("Welcome image preview failed: %s", exc)
                await interaction.followup.send(
                    f"{content}\n\nImage preview failed: {exc}", ephemeral=True
                )
                return

        await interaction.followup.send(content or "Welcome message is empty.", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        settings = await self.bot.db.get_guild_settings(member.guild.id)
        if not settings["welcome_enabled"] or not settings["welcome_channel_id"]:
            return

        channel = member.guild.get_channel(settings["welcome_channel_id"])
        if not isinstance(channel, discord.TextChannel):
            log.warning("Welcome channel %s is missing in guild %s", settings["welcome_channel_id"], member.guild.id)
            return

        content = render_welcome_template(settings["welcome_template"], member)
        allowed_mentions = discord.AllowedMentions(
            users=True, roles=False, everyone=False, replied_user=False
        )
        if settings["welcome_image_enabled"] and settings["welcome_image_url"]:
            try:
                card = await create_welcome_card(
                    member,
                    settings["welcome_image_url"],
                    settings["avatar_position"],
                )
                await channel.send(
                    content=content,
                    file=discord.File(card, filename="welcome.png"),
                    allowed_mentions=allowed_mentions,
                )
                return
            except Exception:
                log.exception("Could not create welcome card for guild %s", member.guild.id)

        await channel.send(content=content, allowed_mentions=allowed_mentions)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
