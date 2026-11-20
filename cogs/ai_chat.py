
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


async def send_interaction_error(
    interaction: discord.Interaction,
    message: str,
) -> None:
    """Safely send an ephemeral interaction error."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
    except discord.HTTPException:
        log.exception("Failed to send interaction error response")


class AIPromptModal(discord.ui.Modal, title="AI Personality Setup"):
    prompt = discord.ui.TextInput(
        label="System prompt / personality",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the assistant's role, tone, rules, and preferred language...",
        max_length=3000,
    )

    def __init__(self, current_prompt: str) -> None:
        super().__init__()
        self.prompt.default = (current_prompt or "")[:3000]

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await send_interaction_error(
                interaction,
                "This form can only be used in a server.",
            )
            return

        prompt_text = str(self.prompt.value).strip()

        if not prompt_text:
            await send_interaction_error(
                interaction,
                "The system prompt cannot be empty.",
            )
            return

        try:
            await interaction.client.db.update_guild_settings(
                interaction.guild.id,
                ai_prompt=prompt_text,
            )

            await interaction.response.send_message(
                "AI personality saved.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Failed to save AI personality")

            await send_interaction_error(
                interaction,
                "Failed to save AI personality. Please check the bot logs.",
            )


class AIChat(commands.Cog):
    ai = app_commands.Group(
        name="ai",
        description="Configure the AI auto-reply assistant",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_locks: defaultdict[int, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    # --------------------------------------------------
    # Set AI channel
    # --------------------------------------------------

    @ai.command(
        name="set-channel",
        description="Set the channel where the AI replies",
    )
    @app_commands.describe(
        channel_id="Paste the channel ID or channel mention, e.g. <#123456789>"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel_id: str,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            await send_interaction_error(
                interaction,
                "This command can only be used in a server.",
            )
            return

        # Extract the numeric ID from either an ID or <#ID>.
        match = re.search(r"\d{15,25}", channel_id)

        if match is None:
            await send_interaction_error(
                interaction,
                "Invalid channel ID. Please copy the channel ID again.",
            )
            return

        target_id = int(match.group())

        try:
            # Fetch directly from Discord instead of relying on cache conversion.
            channel = await guild.fetch_channel(target_id)

            if channel.guild.id != guild.id:
                await send_interaction_error(
                    interaction,
                    "That channel does not belong to this server.",
                )
                return

            if not isinstance(channel, discord.TextChannel):
                await send_interaction_error(
                    interaction,
                    "Please provide a normal text channel.",
                )
                return

            bot_member = guild.me

            if bot_member is None:
                bot_member = await guild.fetch_member(self.bot.user.id)

            permissions = channel.permissions_for(bot_member)

            if not permissions.view_channel:
                await send_interaction_error(
                    interaction,
                    "The bot cannot view that channel. Grant View Channel permission.",
                )
                return

            if not permissions.send_messages:
                await send_interaction_error(
                    interaction,
                    "The bot cannot send messages there. Grant Send Messages permission.",
                )
                return

            await self.bot.db.update_guild_settings(
                guild.id,
                ai_channel_id=channel.id,
            )

            await interaction.response.send_message(
                f"AI auto-replies are now configured for {channel.mention}.",
                ephemeral=True,
            )

            log.info(
                "AI channel configured: guild_id=%s channel_id=%s",
                guild.id,
                channel.id,
            )

        except discord.NotFound:
            await send_interaction_error(
                interaction,
                "Channel not found. Please check the channel ID.",
            )

        except discord.Forbidden:
            await send_interaction_error(
                interaction,
                "The bot cannot access that channel.",
            )

        except Exception:
            log.exception("Failed to set AI channel")

            await send_interaction_error(
                interaction,
                "Failed to configure the AI channel. Please check the bot logs.",
            )

    # --------------------------------------------------
    # Disable AI
    # --------------------------------------------------

    @ai.command(
        name="disable",
        description="Disable AI auto-replies in this server",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def disable(
        self,
        interaction: discord.Interaction,
    ) -> None:
        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                ai_channel_id=None,
            )

            await interaction.response.send_message(
                "AI auto-replies are disabled.",
                ephemeral=True,
            )

            log.info(
                "AI disabled: guild_id=%s",
                interaction.guild.id,
            )

        except Exception:
            log.exception("Failed to disable AI")

            await send_interaction_error(
                interaction,
                "Failed to disable AI. Please check the bot logs.",
            )

    # --------------------------------------------------
    # Configure AI personality
    # --------------------------------------------------

    @ai.command(
        name="prompt",
        description="Set the AI assistant's personality and instructions",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def prompt(
        self,
        interaction: discord.Interaction,
    ) -> None:
        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            await interaction.response.send_modal(
                AIPromptModal(
                    settings.get("ai_prompt") or ""
                )
            )

        except Exception:
            log.exception("Failed to open AI personality modal")

            await send_interaction_error(
                interaction,
                "Failed to open AI personality settings.",
            )

    # --------------------------------------------------
    # Show AI status
    # --------------------------------------------------

    @ai.command(
        name="status",
        description="Show the current AI configuration",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            channel_id = settings.get("ai_channel_id")

            if channel_id:
                channel = interaction.guild.get_channel(channel_id)
                channel_display = (
                    channel.mention
                    if channel is not None
                    else f"<#{channel_id}>"
                )
            else:
                channel_display = "Not configured"

            prompt_preview = (
                (settings.get("ai_prompt") or "")
                .replace("\n", " ")[:160]
            )

            embed = discord.Embed(
                title="AI Configuration",
                color=discord.Color.blurple(),
            )

            embed.add_field(
                name="AI Channel",
                value=channel_display,
                inline=False,
            )

            embed.add_field(
                name="API Key",
                value=(
                    "Configured"
                    if self.bot.ai_client
                    else "Missing"
                ),
                inline=True,
            )

            embed.add_field(
                name="Model",
                value=str(self.bot.ai_model)[:256],
                inline=True,
            )

            embed.add_field(
                name="Personality",
                value=prompt_preview or "Not configured",
                inline=False,
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

        except Exception:
            log.exception("Failed to retrieve AI configuration")

            await send_interaction_error(
                interaction,
                "Failed to retrieve AI configuration.",
            )

    # --------------------------------------------------
    # Clear conversation history
    # --------------------------------------------------

    @ai.command(
        name="clear-history",
        description="Clear saved AI conversation history for this channel",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def clear_history(
        self,
        interaction: discord.Interaction,
    ) -> None:
        try:
            await self.bot.db.clear_ai_history(
                interaction.guild.id,
                interaction.channel_id,
            )

            await interaction.response.send_message(
                "AI conversation history cleared for this channel.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Failed to clear AI conversation history")

            await send_interaction_error(
                interaction,
                "Failed to clear conversation history. Please check the bot logs.",
            )

    # --------------------------------------------------
    # AI automatic replies
    # --------------------------------------------------

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:

        # Ignore DMs, bots and empty messages.
        if (
            message.guild is None
            or message.author.bot
            or not message.content
            or not message.content.strip()
        ):
            return

        # Only process normal text channels.
        if not isinstance(message.channel, discord.TextChannel):
            return

        try:
            settings = await self.bot.db.get_guild_settings(
                message.guild.id
            )

            configured_channel_id = settings.get("ai_channel_id")

            if not configured_channel_id:
                return

            if message.channel.id != configured_channel_id:
                return

            if self.bot.ai_client is None:
                log.error(
                    "AI channel is enabled but AI_API_KEY is missing."
                )
                return

            async with self.channel_locks[message.channel.id]:

                history = await self.bot.db.get_ai_history(
                    message.guild.id,
                    message.channel.id,
                    limit=self.bot.ai_history_limit,
                )

                system_prompt = (
                    settings.get("ai_prompt")
                    or "You are a helpful Discord server assistant. "
                       "Reply clearly and politely."
                )

                messages = [
                    {
                        "role": "system",
                        "content": system_prompt,
                    }
                ]

                messages.extend(history)

                messages.append(
                    {
                        "role": "user",
                        "content": message.content[:6000],
                    }
                )

                async with message.channel.typing():
                    completion = (
                        await self.bot.ai_client.chat.completions.create(
                            model=self.bot.ai_model,
                            messages=messages,
                            max_tokens=self.bot.ai_max_tokens,
                        )
                    )

                answer = (
                    completion.choices[0].message.content or ""
                ).strip()

                if not answer:
                    answer = (
                        "I could not generate a response. Please try again."
                    )

                # Save conversation history.
                await self.bot.db.add_ai_history(
                    message.guild.id,
                    message.channel.id,
                    "user",
                    message.content[:6000],
                )

                await self.bot.db.add_ai_history(
                    message.guild.id,
                    message.channel.id,
                    "assistant",
                    answer[:6000],
                )

                # Discord messages must stay below 2000 characters.
                chunks = [
                    answer[index:index + 1900]
                    for index in range(0, len(answer), 1900)
                ]

                if not chunks:
                    chunks = [
                        "I could not generate a response. Please try again."
                    ]

                await message.reply(
                    chunks[0],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

                for chunk in chunks[1:]:
                    await message.channel.send(
                        chunk,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

        except Exception:
            log.exception(
                "AI response failed: guild_id=%s channel_id=%s",
                message.guild.id,
                message.channel.id,
            )

            try:
                await message.reply(
                    "I'm temporarily unable to respond. Please try again later.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.exception("Failed to send AI error message")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
