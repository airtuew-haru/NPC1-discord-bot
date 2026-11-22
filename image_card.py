
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageOps

log = logging.getLogger(__name__)

MAX_BACKGROUND_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
CARD_SIZE = (1100, 500)

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


async def _validate_public_https_url(url: str) -> None:
    parsed = urlsplit(url)

    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use a public HTTPS image URL.")

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("Invalid image URL port.") from exc

    loop = asyncio.get_running_loop()

    try:
        addresses = await loop.getaddrinfo(
            parsed.hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError("The image host could not be reached.") from exc

    if not addresses:
        raise ValueError("The image host has no public address.")

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if not ip.is_global:
            raise ValueError("Private or local image hosts are not allowed.")


async def _download_background(url: str) -> Image.Image:
    timeout = aiohttp.ClientTimeout(
        total=20,
        connect=8,
        sock_read=12,
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SammyWelcomeBot/1.0)",
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*",
    }

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
    ) as session:

        current_url = url

        for _ in range(6):
            await _validate_public_https_url(current_url)

            async with session.get(
                current_url,
                allow_redirects=False,
            ) as response:

                if response.status in REDIRECT_STATUSES:
                    location = response.headers.get("Location")

                    if not location:
                        raise ValueError("Image redirect has no location.")

                    current_url = urljoin(current_url, location)
                    continue

                if response.status != 200:
                    raise ValueError(
                        f"Image URL returned HTTP {response.status}."
                    )

                content_length = response.content_length

                if (
                    content_length is not None
                    and content_length > MAX_BACKGROUND_BYTES
                ):
                    raise ValueError("Image is larger than 8 MB.")

                data = await response.content.read(
                    MAX_BACKGROUND_BYTES + 1
                )

                if len(data) > MAX_BACKGROUND_BYTES:
                    raise ValueError("Image is larger than 8 MB.")

                break
        else:
            raise ValueError("Too many image redirects.")

    return await asyncio.to_thread(_decode_image, data)


def _decode_image(data: bytes) -> Image.Image:
    if not data:
        raise ValueError("Empty image response.")

    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image dimensions are too large.")

        source.seek(0)
        source.load()

        image = ImageOps.exif_transpose(source)
        return image.convert("RGB")


def _create_default_background() -> Image.Image:
    width, height = CARD_SIZE

    image = Image.new("RGB", CARD_SIZE)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(height - 1, 1)

        color = (
            int(90 + ratio * 45),
            int(120 + ratio * 20),
            int(200 + ratio * 35),
        )

        draw.line(
            (0, y, width, y),
            fill=color,
        )

    draw.ellipse(
        (-120, -180, 330, 270),
        fill=(125, 155, 225),
    )

    draw.ellipse(
        (800, 280, 1250, 730),
        fill=(115, 125, 210),
    )

    draw.ellipse(
        (780, -130, 1050, 140),
        fill=(145, 160, 230),
    )

    return image


def _prepare_avatar(avatar_bytes: bytes) -> Image.Image:
    with Image.open(BytesIO(avatar_bytes)) as source:
        source.seek(0)
        source.load()

        avatar = ImageOps.exif_transpose(source).convert("RGBA")

    diameter = 250

    avatar = ImageOps.fit(
        avatar,
        (diameter, diameter),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    mask = Image.new("L", (diameter, diameter), 0)

    ImageDraw.Draw(mask).ellipse(
        (0, 0, diameter - 1, diameter - 1),
        fill=255,
    )

    avatar.putalpha(mask)

    return avatar


def _get_avatar_position(
    position: str | None,
    width: int,
) -> int:

    position = (position or "center").strip().lower()

    position_map = {
        "left": int(width * 0.18),
        "center": width // 2,
        "right": int(width * 0.82),
    }

    return position_map.get(
        position,
        position_map["center"],
    )


async def create_welcome_card(
    member: discord.Member,
    background_url: str | None,
    position: str = "center",
) -> BytesIO:

    try:
        if not background_url:
            raise ValueError("No background image configured.")

        background = await _download_background(background_url)

    except Exception as exc:
        log.warning(
            "Welcome background failed for guild %s: %s",
            member.guild.id,
            exc,
        )

        background = _create_default_background()

    background = ImageOps.fit(
        background,
        CARD_SIZE,
        method=Image.Resampling.LANCZOS,
    )

    width, height = CARD_SIZE
    diameter = 250
    border_width = 8

    try:
        avatar_bytes = await member.display_avatar.replace(
            size=256,
            static_format="png",
        ).read()

        avatar = await asyncio.to_thread(
            _prepare_avatar,
            avatar_bytes,
        )

    except Exception as exc:
        log.warning(
            "Could not load avatar for member %s: %s",
            member.id,
            exc,
        )

        avatar = Image.new(
            "RGBA",
            (diameter, diameter),
            (120, 145, 210, 255),
        )

    center_x = _get_avatar_position(position, width)
    center_y = int(height * 0.39)

    left = center_x - diameter // 2
    top = center_y - diameter // 2

    background_rgba = background.convert("RGBA")
    draw = ImageDraw.Draw(background_rgba)

    draw.ellipse(
        (
            left - border_width,
            top - border_width,
            left + diameter + border_width,
            top + diameter + border_width,
        ),
        fill=(255, 112, 122, 255),
    )

    background_rgba.alpha_composite(
        avatar,
        (left, top),
    )

    output = BytesIO()

    background_rgba.convert("RGB").save(
        output,
        format="PNG",
        optimize=True,
    )

    output.seek(0)

    return output
