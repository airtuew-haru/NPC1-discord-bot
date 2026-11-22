
from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

VISION_MODEL = os.getenv(
    "AI_VISION_MODEL",
    "qwen/qwen3.8-27b",
).strip()

MAX_IMAGES = 3
MAX_IMAGE_SIZE = 6 * 1024 * 1024
MAX_TOTAL_IMAGE_SIZE = 12 * 1024 * 1024
MAX_HISTORY_TEXT = 5000

VISION_INSTRUCTIONS = """
You are an image analysis component.

Your task is to describe only what is visibly supported by the image.

Rules:
1. Describe the visible objects, characters, colors, clothing,
   positions, expressions, and background.
2. Do not invent names, identities, relationships, or events.
3. Do not claim that an image is upside down unless there is
   clear visual evidence.
4. If something is unclear, explicitly say that it is uncertain.
5. For anime illustrations, describe visible character features
   rather than guessing the character's identity.
6. Do not roleplay or use a fictional personality.
7. Reply in Traditional Chinese.
8. Keep the description concise but useful.
"""


def detect_image_mime(data: bytes) -> str | None:
    """Detect supported image types from file signatures."""

    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if (
        len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"

    return None


def is_image_attachment(
    attachment: discord.Attachment,
) -> bool:
    content_type = (attachment.content_type or "").lower()
    filename = attachment.filename.lower()

    return (
        content_type.startswith("image/")
        or filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    )


async def prepare_images(
    attachments: list[discord.Attachment],
) -> list[dict]:
    """Download and encode Discord image attachments."""

    if len(attachments) > MAX_IMAGES:
        raise ValueError(
            f"You can send up to {MAX_IMAGES} images at once."
        )

    total_size = 0
    image_parts = []

    for attachment in attachments:
        if attachment.size > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} exceeds the 6 MB image limit."
            )

        total_size += attachment.size

        if total_size > MAX_TOTAL_IMAGE_SIZE:
            raise ValueError(
                "The total image size cannot exceed 12 MB."
            )

        data = await attachment.read()

        if len(data) > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} exceeds the 6 MB image limit."
            )

        mime_type = detect_image_mime(data)

        if mime_type is None:
            raise ValueError(
                f"{attachment.filename} is not a supported image. "
                "Please use JPG, PNG, or WEBP."
            )

        encoded = base64.b64encode(data).decode("ascii")

        image_parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                    "detail": "auto",
                },
            }
        )

    return image_parts


class AIPromptModal(
    discord.ui.Modal,
    title="AI Personality Setup",
):
    prompt = discord.ui.TextInput(
        label="System prompt / personality",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "Describe the assistant's role, tone, rules, "
            "and preferred language..."
        ),
        max_length=3000,
    )

    def __init__(self, current_prompt: str) -> None:
        super().__init__()
        self.prompt.default = current_prompt[:3000]

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

        prompt_text = str(self.prompt.value).strip()

        if not prompt_text:
            await interaction.response.send_message(
                "The system prompt cannot be empty.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.client.db.update_guild_settings(
                interaction.guild.id,
                ai_prompt=prompt_text,
            )

            await interaction.followup.send(
                "AI personality saved.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not save AI personality")

            await interaction.followup.send(
                "Could not save AI personality.",
                ephemeral=True,
            )


class AIChat(commands.Cog):

    ai = app_commands.Group(
        name="ai",
        description="Configure the AI auto-reply assistant",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Prevent simultaneous requests from mixing channel history.
        self.channel_locks: defaultdict[int, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    @ai.command(
        name="set-channel",
        description="Set the channel where the AI replies to every human message",
    )
    @app_commands.describe(
        channel="The text channel monitored by the AI"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                ai_channel_id=channel.id,
            )

            await interaction.followup.send(
                f"AI auto-replies are now configured for {channel.mention}.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not set AI channel")

            await interaction.followup.send(
                "Could not save the AI channel.",
                ephemeral=True,
            )

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

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                ai_channel_id=None,
            )

            await interaction.followup.send(
                "AI auto-replies are disabled.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not disable AI")

            await interaction.followup.send(
                "Could not disable AI auto-replies.",
                ephemeral=True,
            )

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

            # A modal must be the initial interaction response.
            await interaction.response.send_modal(
                AIPromptModal(settings.get("ai_prompt") or "")
            )

        except Exception:
            log.exception("Could not open AI personality form")

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Could not load AI personality settings.",
                    ephemeral=True,
                )

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

        await interaction.response.defer(ephemeral=True)

        try:
            settings = await self.bot.db.get_guild_settings(
                interaction.guild.id
            )

            channel_id = settings.get("ai_channel_id")

            channel = (
                interaction.guild.get_channel(channel_id)
                if channel_id
                else None
            )

            prompt_preview = (
                settings.get("ai_prompt") or ""
            ).replace("\n", " ")[:160]

            embed = discord.Embed(
                title="AI Configuration",
                color=discord.Color.blurple(),
            )

            embed.add_field(
                name="AI Channel",
                value=channel.mention if channel else "Not configured",
                inline=False,
            )

            embed.add_field(
                name="Text Model",
                value=self.bot.ai_model,
                inline=True,
            )

            embed.add_field(
                name="Vision Model",
                value=VISION_MODEL,
                inline=True,
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
                name="Personality",
                value=prompt_preview or "Not configured",
                inline=False,
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not load AI status")

            await interaction.followup.send(
                "Could not load AI status.",
                ephemeral=True,
            )

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

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.clear_ai_history(
                interaction.guild.id,
                interaction.channel_id,
            )

            await interaction.followup.send(
                "AI conversation history cleared for this channel.",
                ephemeral=True,
            )

        except Exception:
            log.exception("Could not clear AI history")

            await interaction.followup.send(
                "Could not clear AI conversation history.",
                ephemeral=True,
            )

    async def analyze_images(
        self,
        image_parts: list[dict],
    ) -> str:
        """
        Stage 1:
        Qwen analyzes the images without Sammy's personality prompt.
        """

        vision_messages = [
            {
                "role": "system",
                "content": VISION_INSTRUCTIONS,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Please describe the attached image or images. "
                            "Focus on visible details and avoid guessing."
                        ),
                    },
                    *image_parts,
                ],
            },
        ]

        completion = await self.bot.ai_client.chat.completions.create(
            model=VISION_MODEL,
            messages=vision_messages,
            max_completion_tokens=900,
        )

        description = (
            completion.choices[0].message.content or ""
        ).strip()

        if not description:
            raise RuntimeError("Vision model returned an empty description.")

        return description[:5000]

    async def generate_text_reply(
        self,
        settings: dict,
        history: list[dict],
        user_text: str,
    ) -> str:
        """
        Stage 2:
        GPT-OSS receives the image description and conversation history.
        It generates the only final reply shown in Discord.
        """

        system_prompt = (
            settings.get("ai_prompt")
            or "You are a helpful assistant."
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
                "content": user_text[:6000],
            }
        )

        completion = await self.bot.ai_client.chat.completions.create(
            model=self.bot.ai_model,
            messages=messages,
            max_tokens=self.bot.ai_max_tokens,
        )

        answer = (
            completion.choices[0].message.content or ""
        ).strip()

        if not answer:
            return "I could not generate a response. Please try again."

        return answer

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:

        if (
            message.guild is None
            or message.author.bot
            or not isinstance(message.channel, discord.TextChannel)
        ):
            return

        image_attachments = [
            attachment
            for attachment in message.attachments
            if is_image_attachment(attachment)
        ]

        # Ignore messages that contain neither text nor images.
        if not message.content.strip() and not image_attachments:
            return

        try:
            settings = await self.bot.db.get_guild_settings(
                message.guild.id
            )

            ai_channel_id = settings.get("ai_channel_id")

            if (
                not ai_channel_id
                or message.channel.id != ai_channel_id
            ):
                return

            if self.bot.ai_client is None:
                log.error("AI_API_KEY is not configured.")
                return

            async with self.channel_locks[message.channel.id]:

                history = await self.bot.db.get_ai_history(
                    message.guild.id,
                    message.channel.id,
                    limit=self.bot.ai_history_limit,
                )

                user_text = message.content.strip()

                # Stage 1: image analysis, only when images exist.
                if image_attachments:

                    try:
                        image_parts = await prepare_images(
                            image_attachments
                        )

                        async with message.channel.typing():
                            image_description = await self.analyze_images(
                                image_parts
                            )

                    except ValueError as exc:
                        await message.reply(
                            str(exc),
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    except Exception:
                        log.exception("Vision image analysis failed")

                        await message.reply(
                            "I could not analyze that image. Please try again later.",
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    if not user_text:
                        user_text = (
                            "Please describe the image I sent."
                        )

                    # The image itself is not saved as Base64.
                    # Its textual analysis is saved in conversation history.
                    combined_user_text = (
                        f"{user_text}\n\n"
                        "[Image analysis from Vision model]\n"
                        f"{image_description}\n"
                        "[End of image analysis]"
                    )

                else:
                    combined_user_text = user_text

                # Stage 2: GPT-OSS generates the final response.
                try:
                    async with message.channel.typing():
                        answer = await self.generate_text_reply(
                            settings,
                            history,
                            combined_user_text,
                        )

                    # Save the user's text plus image description.
                    # Future text-only messages can remember the image.
                    await self.bot.db.add_ai_history(
                        message.guild.id,
                        message.channel.id,
                        "user",
                        combined_user_text[:MAX_HISTORY_TEXT],
                    )

                    await self.bot.db.add_ai_history(
                        message.guild.id,
                        message.channel.id,
                        "assistant",
                        answer[:MAX_HISTORY_TEXT],
                    )

                    await message.reply(
                        answer[:1900],
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

                except Exception:
                    log.exception("GPT-OSS response failed")

                    await message.reply(
                        "I'm temporarily unable to respond. Please try again later.",
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

        except Exception:
            log.exception(
                "AI message handler failed in guild %s",
                message.guild.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
