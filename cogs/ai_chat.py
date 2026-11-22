
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

# Vision model：只在訊息包含圖片時使用
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"

# 圖片限制
MAX_IMAGE_COUNT = 3
MAX_IMAGE_SIZE = 6 * 1024 * 1024
MAX_TOTAL_IMAGE_SIZE = 12 * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def detect_image_mime(data: bytes) -> str | None:
    """根據檔案內容判斷圖片格式，避免只相信副檔名。"""

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
        or any(filename.endswith(ext) for ext in SUPPORTED_EXTENSIONS)
        or filename.endswith(".gif")
    )


async def build_image_content(
    attachments: list[discord.Attachment],
) -> tuple[list[dict], list[str]]:
    """
    下載 Discord 圖片附件，轉換成 Base64 Data URL，
    供 Vision 模型讀取。
    """

    if len(attachments) > MAX_IMAGE_COUNT:
        raise ValueError(
            f"You can send up to {MAX_IMAGE_COUNT} images at once."
        )

    image_parts = []
    filenames = []
    total_size = 0

    for attachment in attachments:
        if attachment.size > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} is larger than 6 MB."
            )

        total_size += attachment.size

        if total_size > MAX_TOTAL_IMAGE_SIZE:
            raise ValueError(
                "The total image size cannot exceed 12 MB."
            )

        data = await attachment.read()

        if len(data) > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{attachment.filename} is larger than 6 MB."
            )

        mime_type = detect_image_mime(data)

        if mime_type is None:
            raise ValueError(
                f"{attachment.filename} is not a supported image. "
                "Use JPG, PNG, or WEBP."
            )

        encoded = base64.b64encode(data).decode("ascii")

        data_url = f"data:{mime_type};base64,{encoded}"

        image_parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": "auto",
                },
            }
        )

        filenames.append(attachment.filename)

    return image_parts, filenames


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
                "Could not save the AI personality.",
                ephemeral=True,
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

        self.vision_model = os.getenv(
            "AI_VISION_MODEL",
            DEFAULT_VISION_MODEL,
        ).strip()

    @ai.command(
        name="set-channel",
        description="Set the channel where the AI automatically replies",
    )
    @app_commands.describe(
        channel="Enter a text channel mention or channel ID"
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

        channel_value = channel.strip().replace("<#", "").replace(">", "")

        if not channel_value.isdigit():
            await interaction.response.send_message(
                "Enter a valid channel mention or channel ID.",
                ephemeral=True,
            )
            return

        target = interaction.guild.get_channel(int(channel_value))

        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "That channel is not a text channel in this server.",
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
                AIPromptModal(settings.get("ai_prompt") or "")
            )

        except Exception:
            log.exception("Could not open AI personality form")

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Could not load the AI personality settings.",
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

            key_status = (
                "Configured"
                if self.bot.ai_client
                else "Missing"
            )

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
                name="API Key",
                value=key_status,
                inline=True,
            )

            embed.add_field(
                name="Text Model",
                value=self.bot.ai_model,
                inline=True,
            )

            embed.add_field(
                name="Vision Model",
                value=self.vision_model,
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

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:

        # 忽略私訊、機器人訊息及非一般文字頻道
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

        # 文字和圖片都沒有，就不處理
        if not message.content.strip() and not image_attachments:
            return

        try:
            settings = await self.bot.db.get_guild_settings(
                message.guild.id
            )

            ai_channel_id = settings.get("ai_channel_id")

            if not ai_channel_id:
                return

            if message.channel.id != ai_channel_id:
                return

            if self.bot.ai_client is None:
                log.error(
                    "AI channel is enabled but AI_API_KEY is not configured."
                )
                return

            async with self.channel_locks[message.channel.id]:

                history = await self.bot.db.get_ai_history(
                    message.guild.id,
                    message.channel.id,
                    limit=self.bot.ai_history_limit,
                )

                # 保留原本的文字對話記憶
                messages = [
                    {
                        "role": "system",
                        "content": settings.get("ai_prompt")
                        or "You are a helpful assistant.",
                    }
                ]

                messages.extend(history)

                user_text = message.content.strip()

                # 有圖片時，下載附件並建立 Vision 輸入
                if image_attachments:
                    try:
                        image_parts, filenames = await build_image_content(
                            image_attachments
                        )

                    except ValueError as exc:
                        await message.reply(
                            str(exc),
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    except Exception:
                        log.exception("Could not download image attachments")

                        await message.reply(
                            "I could not read the attached image. Please try again.",
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return

                    if not user_text:
                        user_text = "Please analyze the attached image(s) and describe what you can see."

                    # 圖片和文字放在同一個 user message
                    user_content = [
                        {
                            "type": "text",
                            "text": user_text[:6000],
                        }
                    ]

                    user_content.extend(image_parts)

                    messages.append(
                        {
                            "role": "user",
                            "content": user_content,
                        }
                    )

                    selected_model = self.vision_model

                    # 資料庫只儲存文字，不儲存圖片的 Base64 內容
                    history_user_text = (
                        user_text[:5000]
                        + "\n[Attached image(s): "
                        + ", ".join(filenames)
                        + "]"
                    )

                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": message.content[:6000],
                        }
                    )

                    selected_model = self.bot.ai_model
                    history_user_text = message.content[:6000]

                try:
                    async with message.channel.typing():

                        request_options = {
                            "model": selected_model,
                            "messages": messages,
                        }

                        if image_attachments:
                            request_options["max_completion_tokens"] = (
                                self.bot.ai_max_tokens
                            )
                        else:
                            request_options["max_tokens"] = (
                                self.bot.ai_max_tokens
                            )

                        completion = await self.bot.ai_client.chat.completions.create(
                            **request_options
                        )

                    answer = (
                        completion.choices[0].message.content or ""
                    ).strip()

                    if not answer:
                        answer = (
                            "I could not generate a response. Please try again."
                        )

                    # 儲存文字形式的歷史，不儲存圖片資料
                    await self.bot.db.add_ai_history(
                        message.guild.id,
                        message.channel.id,
                        "user",
                        history_user_text[:6000],
                    )

                    await self.bot.db.add_ai_history(
                        message.guild.id,
                        message.channel.id,
                        "assistant",
                        answer[:6000],
                    )

                    await message.reply(
                        answer[:1900],
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

                except Exception:
                    log.exception(
                        "AI response failed in channel %s",
                        message.channel.id,
                    )

                    try:
                        await message.reply(
                            "I'm temporarily unable to respond. Please try again later.",
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        pass

        except Exception:
            log.exception(
                "AI message handler failed in guild %s",
                message.guild.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
