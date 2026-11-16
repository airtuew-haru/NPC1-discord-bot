from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from openai import AsyncOpenAI

from database import Database

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("sammy_bot")


class SammyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)

        self.db = Database()
        self.ai_model = os.getenv("AI_MODEL", "openai/gpt-oss-120b").strip()
        self.ai_max_tokens = self._positive_int("AI_MAX_TOKENS", 700, maximum=4000)
        self.ai_history_limit = self._positive_int("AI_HISTORY_LIMIT", 12, maximum=50)
        self.ai_client: AsyncOpenAI | None = None
        self.command_guild_id = os.getenv("COMMAND_GUILD_ID", "").strip()
        self.tree.error(self.on_app_command_error)

    @staticmethod
    def _positive_int(name: str, default: int, maximum: int) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError:
            log.warning("%s must be an integer; using %s", name, default)
            return default
        if value < 1:
            return default
        return min(value, maximum)

    async def setup_hook(self) -> None:
        await self.db.initialize()
        log.info("Database initialized using %s", self.db.backend)

        api_key = os.getenv("AI_API_KEY", "").strip()
        if api_key:
            self.ai_client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1").strip(),
                timeout=40.0,
                max_retries=1,
            )
        else:
            log.warning("AI_API_KEY is not set. AI auto-replies are unavailable until it is added.")

        for extension in ("cogs.welcome", "cogs.ai_chat", "cogs.autorole"):
            await self.load_extension(extension)
            log.info("Loaded extension: %s", extension)

        if self.command_guild_id:
            try:
                guild = discord.Object(id=int(self.command_guild_id))
            except ValueError as exc:
                raise RuntimeError("COMMAND_GUILD_ID must be a numeric Discord server ID.") from exc
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s application commands to development guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global application commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "unknown")
        log.info("Connected to %s server(s)", len(self.guilds))

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "I am missing a required permission. Please check my server role and permissions."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You do not have permission to use this command here."
        else:
            log.error("Application command failed: %r", error)
            message = "Something went wrong while running that command. Please check the bot logs."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.exception("Could not send application command error response")

    async def close(self) -> None:
        if self.ai_client is not None:
            await self.ai_client.close()
            self.ai_client = None
        await self.db.close()
        await super().close()


def create_http_app(bot: SammyBot) -> web.Application:
    app = web.Application()

    async def home(_: web.Request) -> web.Response:
        return web.json_response({"name": "Sammy Discord Bot", "status": "running"})

    async def health(_: web.Request) -> web.Response:
        # Health endpoint verifies that the HTTP process is alive. Discord login
        # status is reported separately because Gateway reconnects can be transient.
        return web.json_response(
            {
                "status": "ok",
                "discord_connected": bot.is_ready(),
                "database_backend": bot.db.backend,
            }
        )

    app.router.add_get("/", home)
    app.router.add_get("/healthz", health)
    return app


async def run_bot() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Add it to your .env file or Render environment.")

    bot = SammyBot()
    http_runner = web.AppRunner(create_http_app(bot), access_log=None)
    await http_runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(http_runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%s", port)

    try:
        await bot.start(token, reconnect=True)
    finally:
        await bot.close()
        await http_runner.cleanup()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Shutdown requested")


if __name__ == "__main__":
    main()
