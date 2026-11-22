
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from image_card import create_welcome_card

log = logging.getLogger(__name__)


def render_welcome_template(
    template: str,
    member: discord.Member,
) -> str:
    replacements = {
        "<id>": member.display_name,
        "%guild%": member.guild.name,
        "%user%": member.mention,
        "%member_count%": str(
            member.guild.member_count or len(member.guild.members)
        ),
    }

    result = template or "Welcome %user% to %guild%!"

    for token, value in replacements.items():
        result = result.replace(token, value)

    return result[:1900]


def parse_channel_id(value: str) -> Optional[int]:
    match = re.fullmatch(
        r"\s*(?:<#(\d+)>|(\d+))\s*",
        value,
    )

    if not match:
        return None

    return int(match.group(1) or match.group(2))


def get_avatar_position(settings: dict) -> str:
    position = settings.get("avatar_position") or "center"

    if position not in ("left", "center", "right"):
        return "center"

    return position


class WelcomeConfigModal(
    discord.ui.Modal,
    title="Welcome Message Setup",
):
    channel = discord.ui.TextInput(
        label="Welcome channel ID or mention",
        placeholder="Example: <#123456789012345678>",
        max_length=80,
    )

    template = discord.ui.TextInput(
        label="Welcome message template",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder="Welcome <id> to %guild%! You are member #%member_count%.",
    )

    def __init__(
        self,
        current_channel: str = "",
        current_template: str = "",
    ) -> None:
        super().__init__()

        if current_channel:
            self.channel.default = current_channel

        if current_template:
            self.template.default = current_template

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:

        if interaction.guild is None:
            await interaction.response.send_message(
                "This form can only be used in a server.",
                ephemeral=True,
            )
            return

        channel_id = parse_channel_id(str(self.channel.value))

        if channel_id is None:
            await interaction.response.send_message(
                "I could not read that channel. Use a channel mention or numeric ID.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "That ID is not a text channel in this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.client.db.update_guild_settings(
                interaction.guild.id,
                welcome_channel_id=channel.id,
                welcome_template=str(self.template.value).strip(),
            )

            await interaction.followup.send(
                f"Welcome settings saved. Channel: {channel.mention}",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not save welcome settings")

            await interaction.followup.send(
                "Could not save welcome settings. Check bot logs.",
                ephemeral=True,
            )


class WelcomeImageModal(
    discord.ui.Modal,
    title="Welcome Image Setup",
):
    image_url = discord.ui.TextInput(
        label="Direct HTTPS image URL",
        placeholder="https://example.com/welcome.png",
        required=False,
        max_length=1000,
    )

    def __init__(self, current_url: str = "") -> None:
        super().__init__()

        if current_url:
            self.image_url.default = current_url

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:

        if interaction.guild is None:
            await interaction.response.send_message(
                "This form can only be used in a server.",
                ephemeral=True,
            )
            return

        url = str(self.image_url.value).strip() or None

        if url and not url.lower().startswith("https://"):
            await interaction.response.send_message(
                "Please use a direct HTTPS image URL.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.client.db.update_guild_settings(
                interaction.guild.id,
                welcome_image_url=url,
            )

            await interaction.followup.send(
                "Welcome image URL saved." if url
                else "Welcome image URL cleared.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not save welcome image URL")

            await interaction.followup.send(
                "Could not save image settings.",
                ephemeral=True,
            )


class Welcome(commands.Cog):

    welcome = app_commands.Group(
        name="welcome",
        description="Configure welcome messages and images",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @welcome.command(
        name="configure",
        description="Set the welcome channel and message template",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def configure(
        self,
        interaction: discord.Interaction,
    ) -> None:

        # Modal 必須是第一次互動回應，不能先 defer。
        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            channel_id = settings.get("welcome_channel_id")
            template = settings.get("welcome_template") or ""

            await interaction.response.send_modal(
                WelcomeConfigModal(
                    str(channel_id) if channel_id else "",
                    template,
                )
            )

        except Exception:
            log.exception("Welcome configure failed")

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Could not load welcome settings.",
                    ephemeral=True,
                )

    @welcome.command(
        name="enable",
        description="Enable or disable welcome messages",
    )
    @app_commands.describe(enabled="Turn welcome messages on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def enable(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ) -> None:

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                welcome_enabled=int(enabled),
            )

            await interaction.followup.send(
                f"Welcome messages are now {'enabled' if enabled else 'disabled'}.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Welcome enable failed")

            await interaction.followup.send(
                "Could not update welcome settings.",
                ephemeral=True,
            )

    @welcome.command(
        name="image",
        description="Set the welcome background image URL",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def image(
        self,
        interaction: discord.Interaction,
    ) -> None:

        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            await interaction.response.send_modal(
                WelcomeImageModal(
                    settings.get("welcome_image_url") or ""
                )
            )

        except Exception:
            log.exception("Welcome image command failed")

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Could not load image settings.",
                    ephemeral=True,
                )

    @welcome.command(
        name="image-toggle",
        description="Enable or disable welcome images",
    )
    @app_commands.describe(enabled="Turn welcome images on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def image_toggle(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ) -> None:

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                welcome_image_enabled=int(enabled),
            )

            await interaction.followup.send(
                f"Welcome images are now {'enabled' if enabled else 'disabled'}.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Welcome image toggle failed")

            await interaction.followup.send(
                "Could not update image settings.",
                ephemeral=True,
            )

    @welcome.command(
        name="avatar-position",
        description="Choose where the joining member's avatar appears",
    )
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
        self,
        interaction: discord.Interaction,
        position: app_commands.Choice[str],
    ) -> None:

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                avatar_position=position.value,
            )

            await interaction.followup.send(
                f"Avatar position set to **{position.name}**.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Avatar position update failed")

            await interaction.followup.send(
                "Could not update avatar position.",
                ephemeral=True,
            )

    @welcome.command(
        name="test",
        description="Preview the current welcome message and image",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def test(
        self,
        interaction: discord.Interaction,
    ) -> None:

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            sample_member = interaction.user

            if not isinstance(sample_member, discord.Member):
                await interaction.followup.send(
                    "This command must be used in a server.",
                    ephemeral=True,
                )
                return

            content = render_welcome_template(
                settings.get("welcome_template") or "",
                sample_member,
            )

            if (
                settings.get("welcome_image_enabled")
                and settings.get("welcome_image_url")
            ):
                card = await create_welcome_card(
                    sample_member,
                    settings.get("welcome_image_url"),
                    get_avatar_position(settings),
                )

                await interaction.followup.send(
                    content=content,
                    file=discord.File(
                        card,
                        filename="welcome-preview.png",
                    ),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            await interaction.followup.send(
                content or "Welcome message is empty.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Welcome preview failed")

            await interaction.followup.send(
                "Could not create welcome preview. Check Render logs.",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member,
    ) -> None:

        try:
            settings = await self.bot.db.get_guild_settings(
                member.guild.id
            )

            if not settings.get("welcome_enabled"):
                return

            channel_id = settings.get("welcome_channel_id")

            if not channel_id:
                return

            channel = member.guild.get_channel(channel_id)

            if not isinstance(channel, discord.TextChannel):
                log.warning(
                    "Welcome channel %s is missing in guild %s",
                    channel_id,
                    member.guild.id,
                )
                return

            content = render_welcome_template(
                settings.get("welcome_template") or "",
                member,
            )

            allowed_mentions = discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
                replied_user=False,
            )

            if (
                settings.get("welcome_image_enabled")
                and settings.get("welcome_image_url")
            ):
                card = await create_welcome_card(
                    member,
                    settings.get("welcome_image_url"),
                    get_avatar_position(settings),
                )

                await channel.send(
                    content=content,
                    file=discord.File(
                        card,
                        filename="welcome.png",
                    ),
                    allowed_mentions=allowed_mentions,
                )
                return

            await channel.send(
                content=content,
                allowed_mentions=allowed_mentions,
            )

        except Exception:
            log.exception(
                "Welcome event failed for guild %s",
                member.guild.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
