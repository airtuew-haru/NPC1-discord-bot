from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class AIPromptModal(discord.ui.Modal, title="AI Personality Setup"):
    prompt = discord.ui.TextInput(
        label="System prompt / personality",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the assistant's role, tone, rules, and preferred language...",
        max_length=3000,
    )

    def __init__(self, current_prompt: str) -> None:
        super().__init__()
        self.prompt.default = current_prompt[:3000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This form can only be used in a server.", ephemeral=True)
            return
        prompt_text = str(self.prompt.value).strip()
        if not prompt_text:
            await interaction.response.send_message("The system prompt cannot be empty.", ephemeral=True)
            return
        await interaction.client.db.update_guild_settings(
            interaction.guild.id, ai_prompt=prompt_text
        )
        await interaction.response.send_message("AI personality saved.", ephemeral=True)


class AIChat(commands.Cog):
    ai = app_commands.Group(name="ai", description="Configure the AI auto-reply assistant")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @ai.command(name="set-channel", description="Set the channel where the AI replies to every human message")
    @app_commands.describe(channel="The text channel monitored by the AI")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self.bot.db.update_guild_settings(
            interaction.guild.id, ai_channel_id=channel.id
        )
        await interaction.response.send_message(
            f"AI auto-replies are now configured for {channel.mention}.", ephemeral=True
        )

    @ai.command(name="disable", description="Disable AI auto-replies in this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def disable(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_guild_settings(interaction.guild.id, ai_channel_id=None)
        await interaction.response.send_message("AI auto-replies are disabled.", ephemeral=True)

    @ai.command(name="prompt", description="Set the AI assistant's personality and instructions")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def prompt(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        await interaction.response.send_modal(AIPromptModal(settings["ai_prompt"]))

    @ai.command(name="status", description="Show the current AI configuration")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        channel = interaction.guild.get_channel(settings["ai_channel_id"]) if settings["ai_channel_id"] else None
        prompt_preview = settings["ai_prompt"].replace("\n", " ")[:160]
        key_status = "configured" if self.bot.ai_client else "missing"
        embed = discord.Embed(title="AI Configuration", color=discord.Color.blurple())
        embed.add_field(name="AI Channel", value=channel.mention if channel else "Not configured", inline=False)
        embed.add_field(name="API Key", value=key_status, inline=True)
        embed.add_field(name="Model", value=self.bot.ai_model, inline=True)
        embed.add_field(name="Personality", value=prompt_preview or "Not configured", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ai.command(name="clear-history", description="Clear saved AI conversation history for this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def clear_history(self, interaction: discord.Interaction) -> None:
        await self.bot.db.clear_ai_history(interaction.guild.id, interaction.channel_id)
        await interaction.response.send_message("AI conversation history cleared for this channel.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content.strip():
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        settings = await self.bot.db.get_guild_settings(message.guild.id)
        if not settings["ai_channel_id"] or message.channel.id != settings["ai_channel_id"]:
            return
        if self.bot.ai_client is None:
            log.error("AI channel is enabled but AI_API_KEY is not configured.")
            return

        async with self.channel_locks[message.channel.id]:
            history = await self.bot.db.get_ai_history(
                message.guild.id, message.channel.id, limit=self.bot.ai_history_limit
            )
            messages = [{"role": "system", "content": settings["ai_prompt"]}]
            messages.extend(history)
            messages.append({"role": "user", "content": message.content[:6000]})

            try:
                async with message.channel.typing():
                    completion = await self.bot.ai_client.chat.completions.create(
                        model=self.bot.ai_model,
                        messages=messages,
                        max_tokens=self.bot.ai_max_tokens,
                    )
                answer = (completion.choices[0].message.content or "").strip()
                if not answer:
                    answer = "I could not generate a response. Please try again."
                await self.bot.db.add_ai_history(
                    message.guild.id, message.channel.id, "user", message.content[:6000]
                )
                await self.bot.db.add_ai_history(
                    message.guild.id, message.channel.id, "assistant", answer[:6000]
                )
                # Discord message content is limited to 2,000 characters.
                # Split long answers into safe chunks instead of silently truncating them.
                chunks = [answer[index:index + 1900] for index in range(0, len(answer), 1900)]
                if not chunks:
                    chunks = ["I could not generate a response. Please try again."]
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
            except Exception as exc:
                log.exception("AI response failed: %s", exc)
                try:
                    await message.reply(
                        "I'm temporarily unable to respond. Please try again later.",
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
