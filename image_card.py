from __future__ import annotations

import asyncio
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlsplit

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageOps

MAX_BACKGROUND_BYTES = 8 * 1024 * 1024
CARD_SIZE = (1100, 500)


async def _validate_public_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a direct, public HTTPS image URL.")

    loop = asyncio.get_running_loop()
    try:
        addresses = await loop.getaddrinfo(
            parsed.hostname,
            parsed.port or 443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError("The image host could not be reached.") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Private or local image hosts are not allowed.")


async def _download_background(url: str) -> Image.Image:
    await _validate_public_https_url(url)
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f"Image URL returned HTTP {response.status}.")
            content_type = response.headers.get("Content-Type", "").lower()
            if not content_type.startswith("image/"):
                raise ValueError("The URL must point directly to an image file.")
            if response.content_length and response.content_length > MAX_BACKGROUND_BYTES:
                raise ValueError("The image is larger than 8 MB.")
            data = await response.content.read(MAX_BACKGROUND_BYTES + 1)
            if len(data) > MAX_BACKGROUND_BYTES:
                raise ValueError("The image is larger than 8 MB.")

    try:
        image = Image.open(BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise ValueError("The URL did not contain a supported image.") from exc
    return image


async def create_welcome_card(
    member: discord.Member, background_url: str, position: str = "center"
) -> BytesIO:
    background = await _download_background(background_url)
    background = ImageOps.fit(background, CARD_SIZE, method=Image.Resampling.LANCZOS)

    avatar_bytes = await member.display_avatar.replace(size=256, static_format="png").read()
    avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")

    width, height = CARD_SIZE
    diameter = 250
    border_width = 8
    avatar = ImageOps.fit(
        avatar,
        (diameter, diameter),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    avatar.putalpha(mask)

    position_map = {
        "left": int(width * 0.18),
        "center": width // 2,
        "right": int(width * 0.82),
    }
    center_x = position_map.get(position, position_map["center"])
    center_y = int(height * 0.39)
    left = center_x - diameter // 2
    top = center_y - diameter // 2

    draw = ImageDraw.Draw(background)
    draw.ellipse(
        (
            left - border_width,
            top - border_width,
            left + diameter + border_width,
            top + diameter + border_width,
        ),
        fill=(255, 112, 122),
    )
    background_rgba = background.convert("RGBA")
    background_rgba.alpha_composite(avatar, (left, top))

    output = BytesIO()
    background_rgba.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
