from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class AutoRole(commands.Cog):
    autorole = app_commands.Group(name="autorole", description="Configure the automatic join role")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @autorole.command(name="set", description="Choose the role assigned to new members")
    @app_commands.describe(role="The role to assign when a member joins")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        bot_member = interaction.guild.me
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "That role cannot be assigned automatically.", ephemeral=True
            )
            return
        if role >= bot_member.top_role:
            await interaction.response.send_message(
                "Move the bot's highest role above the selected role, then try again.",
                ephemeral=True,
            )
            return

        await self.bot.db.update_guild_settings(
            interaction.guild.id, autorole_role_id=role.id
        )
        await interaction.response.send_message(
            f"Automatic join role set to {role.mention}.", ephemeral=True
        )

    @autorole.command(name="enable", description="Enable or disable automatic role assignment")
    @app_commands.describe(enabled="Turn automatic role assignment on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def enable(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, autorole_enabled=int(enabled)
        )
        await interaction.response.send_message(
            f"Automatic role assignment is now {'enabled' if enabled else 'disabled'}.",
            ephemeral=True,
        )

    @autorole.command(name="clear", description="Remove the saved automatic role")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, autorole_enabled=0, autorole_role_id=None
        )
        await interaction.response.send_message("Automatic role settings cleared.", ephemeral=True)

    @autorole.command(name="status", description="Show the automatic role configuration")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        role = interaction.guild.get_role(settings["autorole_role_id"]) if settings["autorole_role_id"] else None
        await interaction.response.send_message(
            f"Enabled: **{'Yes' if settings['autorole_enabled'] else 'No'}**\n"
            f"Role: {role.mention if role else 'Not configured'}",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        settings = await self.bot.db.get_guild_settings(member.guild.id)
        if not settings["autorole_enabled"] or not settings["autorole_role_id"]:
            return

        role = member.guild.get_role(settings["autorole_role_id"])
        if role is None or role.is_default() or role.managed:
            log.warning("Configured auto-role is missing or unavailable in guild %s", member.guild.id)
            return

        bot_member = member.guild.me
        if bot_member is None:
            bot_member = await member.guild.fetch_member(self.bot.user.id)
        if role >= bot_member.top_role:
            log.warning("Bot role is not high enough to assign role %s in guild %s", role.id, member.guild.id)
            return

        try:
            await member.add_roles(role, reason="Automatic join role")
        except discord.Forbidden:
            log.error("Missing Manage Roles permission for guild %s", member.guild.id)
        except discord.HTTPException:
            log.exception("Failed to assign automatic role in guild %s", member.guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRole(bot))
