
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)

VISION_MODEL = os.getenv(
    "AI_VISION_MODEL",
    "qwen/qwen3.8-27b",
).strip()

MAX_IMAGES = 3
MAX_IMAGE_SIZE = 6 * 1024 * 1024
MAX_TOTAL_IMAGE_SIZE = 12 * 1024 * 1024
MAX_HISTORY_TEXT = 5000

# 每張原圖會額外切成四個區域。
CROP_OVERLAP = 0.12

# 每次 Vision API 請求最多傳入三張圖片。
VISION_BATCH_SIZE = 3

VISION_INSTRUCTIONS = """
You are a neutral image analysis component.

Your task is to analyze the provided original images and image crops.

Rules:
1. Describe only what is visibly supported by the images.
2. Pay close attention to small details, text, objects, clothing,
   colors, expressions, and background elements.
3. Analyze each provided crop carefully.
4. Crops may overlap. Avoid repeating the same observation.
5. Combine observations from different regions when possible.
6. Do not invent names, identities, relationships, or events.
7. Do not claim that an image is upside down without clear evidence.
8. If something is unclear, explicitly say that it is uncertain.
9. For anime illustrations, describe visible character features
   rather than guessing the character's identity.
10. Do not roleplay or use a fictional personality.
11. Reply in Traditional Chinese.
12. Organize the description clearly and include useful details.
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
    """Only accept JPG, PNG, and WEBP attachments."""

    content_type = (attachment.content_type or "").lower()
    filename = attachment.filename.lower()

    return (
        content_type in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }
        or filename.endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        )
    )


def encode_image(
    data: bytes,
    mime_type: str,
    label: str,
) -> dict:
    """Convert image bytes into an OpenAI-compatible image part."""

    encoded = base64.b64encode(data).decode("ascii")

    return {
        "label": label,
        "part": {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{encoded}",
                "detail": "auto",
            },
        },
    }


def encode_crop(
    image: Image.Image,
    box: tuple[int, int, int, int],
    label: str,
    output_format: str,
) -> dict:
    """Crop an image region and encode it."""

    cropped = image.crop(box)
    output = io.BytesIO()

    if output_format == "JPEG":
        if cropped.mode not in ("RGB", "L"):
            cropped = cropped.convert("RGB")

        cropped.save(
            output,
            format="JPEG",
            quality=92,
            optimize=True,
        )
        mime_type = "image/jpeg"

    elif output_format == "WEBP":
        cropped.save(
            output,
            format="WEBP",
            quality=92,
            method=4,
        )
        mime_type = "image/webp"

    else:
        cropped.save(
            output,
            format="PNG",
            optimize=True,
        )
        mime_type = "image/png"

    return encode_image(
        output.getvalue(),
        mime_type,
        label,
    )


def create_image_regions(
    image: Image.Image,
    filename: str,
    mime_type: str,
) -> list[dict]:
    """
    Create one original image and four overlapping crops.

    The crops preserve their original pixel resolution.
    """

    width, height = image.size

    if width < 2 or height < 2:
        raise ValueError(
            f"{filename} has invalid image dimensions."
        )

    output_format = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }[mime_type]

    # Encode the complete original image.
    original_buffer = io.BytesIO()

    if output_format == "JPEG":
        original = image.convert("RGB")
        original.save(
            original_buffer,
            format="JPEG",
            quality=95,
            optimize=True,
        )

    elif output_format == "WEBP":
        image.save(
            original_buffer,
            format="WEBP",
            quality=95,
            method=4,
        )

    else:
        image.save(
            original_buffer,
            format="PNG",
            optimize=True,
        )

    original_part = encode_image(
        original_buffer.getvalue(),
        mime_type,
        f"{filename} - Full image",
    )

    # Calculate overlapping crop boundaries.
    overlap_x = int(width * CROP_OVERLAP)
    overlap_y = int(height * CROP_OVERLAP)

    mid_x = width // 2
    mid_y = height // 2

    regions = [
        (
            "Top-left region",
            (
                0,
                0,
                min(width, mid_x + overlap_x),
                min(height, mid_y + overlap_y),
            ),
        ),
        (
            "Top-right region",
            (
                max(0, mid_x - overlap_x),
                0,
                width,
                min(height, mid_y + overlap_y),
            ),
        ),
        (
            "Bottom-left region",
            (
                0,
                max(0, mid_y - overlap_y),
                min(width, mid_x + overlap_x),
                height,
            ),
        ),
        (
            "Bottom-right region",
            (
                max(0, mid_x - overlap_x),
                max(0, mid_y - overlap_y),
                width,
                height,
            ),
        ),
    ]

    results = [original_part]

    for region_name, box in regions:
        results.append(
            encode_crop(
                image,
                box,
                f"{filename} - {region_name}",
                output_format,
            )
        )

    return results


async def prepare_images(
    attachments: list[discord.Attachment],
) -> list[dict]:
    """
    Download original Discord attachments.

    Return one full image and four crops per attachment.
    """

    if len(attachments) > MAX_IMAGES:
        raise ValueError(
            f"You can send up to {MAX_IMAGES} images at once."
        )

    total_size = 0
    image_entries = []

    for attachment in attachments:
        if attachment.size > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} exceeds the 6 MB image limit."
            )

        total_size += attachment.size

        if total_size > MAX_TOTAL_IMAGE_SIZE:
            raise ValueError(
                "The total original image size cannot exceed 12 MB."
            )

        data = await attachment.read()

        if len(data) > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} exceeds the 6 MB image limit."
            )

        mime_type = detect_image_mime(data)

        if mime_type is None:
            raise ValueError(
                f"{attachment.filename} is not supported. "
                "Please use JPG, PNG, or WEBP."
            )

        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                image.load()

                if image.width * image.height > 40_000_000:
                    raise ValueError(
                        f"{attachment.filename} has too many pixels."
                    )

                # Create independent image data before closing source.
                image = image.copy()

        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError(
                f"Could not read {attachment.filename} as a valid image."
            ) from exc

        image_entries.extend(
            create_image_regions(
                image,
                attachment.filename,
                mime_type,
            )
        )

    return image_entries


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

        self.channel_locks: defaultdict[int, asyncio.Lock] = (
            defaultdict(asyncio.Lock)
        )

    @ai.command(
        name="set-channel",
        description="Set the channel where the AI replies to every human message",
    )
    @app_commands.describe(
        channel="Enter a channel mention or channel ID"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        # Accept both <#CHANNEL_ID> and plain numeric IDs.
        match = re.fullmatch(
            r"(?:<#)?(\d{15,25})>?",
            channel.strip(),
        )

        if not match:
            await interaction.response.send_message(
                "Please enter a valid channel mention or channel ID.",
                ephemeral=True,
            )
            return

        channel_id = int(match.group(1))
        target = interaction.guild.get_channel(channel_id)

        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "The specified channel is not a text channel "
                "in this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                ai_channel_id=target.id,
            )

            await interaction.followup.send(
                f"AI auto-replies are now configured for {target.mention}.",
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

            await interaction.response.send_modal(
                AIPromptModal(
                    settings.get("ai_prompt") or ""
                )
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
        image_entries: list[dict],
    ) -> str:
        """
        Stage 1:
        Qwen analyzes the original images and their crops.

        Each request contains no more than three images.
        """

        descriptions = []

        for batch_start in range(
            0,
            len(image_entries),
            VISION_BATCH_SIZE,
        ):
            batch = image_entries[
                batch_start:batch_start + VISION_BATCH_SIZE
            ]

            content = [
                {
                    "type": "text",
                    "text": (
                        "Analyze the following image material carefully. "
                        "Some images are full images and others are crops "
                        "of the same original image. "
                        "Pay attention to small details."
                    ),
                }
            ]

            for entry in batch:
                content.append(
                    {
                        "type": "text",
                        "text": f"Image region: {entry['label']}",
                    }
                )
                content.append(entry["part"])

            vision_messages = [
                {
                    "role": "system",
                    "content": VISION_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": content,
                },
            ]

            completion = (
                await self.bot.ai_client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=vision_messages,
                    max_completion_tokens=900,
                )
            )

            description = (
                completion.choices[0].message.content or ""
            ).strip()

            if not description:
                raise RuntimeError(
                    "Vision model returned an empty description."
                )

            descriptions.append(
                f"Analysis batch {len(descriptions) + 1}:\n"
                f"{description}"
            )

        return "\n\n".join(descriptions)[:MAX_HISTORY_TEXT]

    async def generate_text_reply(
        self,
        settings: dict,
        history: list[dict],
        user_text: str,
    ) -> str:
        """
        Stage 2:
        GPT-OSS generates the final reply using image analysis
        and the existing conversation history.
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

                # Stage 1: analyze images when attachments exist.
                if image_attachments:

                    try:
                        async with message.channel.typing():
                            image_entries = await prepare_images(
                                image_attachments
                            )

                            image_description = (
                                await self.analyze_images(
                                    image_entries
                                )
                            )

                    except ValueError as exc:
                        await message.reply(
                            str(exc),
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    except Exception:
                        log.exception(
                            "Vision image analysis failed"
                        )

                        await message.reply(
                            "I could not analyze that image. "
                            "Please try again later.",
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    if not user_text:
                        user_text = (
                            "Please describe the image I sent."
                        )

                    combined_user_text = (
                        f"{user_text}\n\n"
                        "[Image analysis from Vision model]\n"
                        f"{image_description}\n"
                        "[End of image analysis]"
                    )

                else:
                    combined_user_text = user_text

                # Stage 2: GPT-OSS generates the final reply.
                try:
                    async with message.channel.typing():
                        answer = await self.generate_text_reply(
                            settings,
                            history,
                            combined_user_text,
                        )

                    # Store the text and image description.
                    # The original image bytes are not stored.
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
                        "I'm temporarily unable to respond. "
                        "Please try again later.",
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
