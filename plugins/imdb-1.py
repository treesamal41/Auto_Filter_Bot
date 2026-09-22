"""
IMDb search for Carla — /imdb <movie name>.
Shows complete movie details (proIMDb style) with poster,
plus a Subtitles button wired to the subtitle plugin.
"""

import logging
import re

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils import get_poster

try:
    from info import TMDB_API_KEY
except ImportError:
    from os import environ
    TMDB_API_KEY = environ.get("TMDB_API_KEY", "")

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def _clean(value, default="N/A"):
    if not value or str(value).strip() in ("N/A", "None", ""):
        return default
    return str(value).strip()


def _tag(name):
    return "#" + re.sub(r"\s+", "_", str(name).strip())


async def _streaming(session, imdb_id):
    """Streaming providers via TMDB (India, else US). Returns 'A | B' or None."""
    if not TMDB_API_KEY:
        return None
    try:
        async with session.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            timeout=15,
        ) as r:
            if r.status != 200:
                return None
            found = await r.json()
        movie_results = found.get("movie_results") or []
        tv_results = found.get("tv_results") or []
        if movie_results:
            media, mtype = movie_results[0], "movie"
        elif tv_results:
            media, mtype = tv_results[0], "tv"
        else:
            return None
        tmdb_id = media.get("id")
        async with session.get(
            f"https://api.themoviedb.org/3/{mtype}/{tmdb_id}/watch/providers",
            params={"api_key": TMDB_API_KEY},
            timeout=15,
        ) as r:
            if r.status != 200:
                return None
            prov = await r.json()
        results = prov.get("results") or {}
        region = results.get("IN") or results.get("US") or {}
        names = []
        for key in ("flatrate", "buy", "rent"):
            for p in region.get(key) or []:
                n = p.get("provider_name")
                if n and n not in names:
                    names.append(n)
        return " | ".join(names[:6]) if names else None
    except Exception as e:
        logger.exception(e)
        return None


@Client.on_message(filters.command("imdb") & filters.incoming)
async def imdb_search(client, message):
    if len(message.command) < 2:
        return await message.reply_text(
            "🔎 <b>Usage:</b> <code>/imdb movie name</code>\n"
            "Example: <code>/imdb Avatar 2009</code>"
        )
    query = message.text.split(None, 1)[1].strip()
    wait = await message.reply_text("🔎 <b>Searching IMDb...</b>")

    try:
        data = await get_poster(query)
    except Exception as e:
        logger.exception(e)
        data = None
    if not data:
        return await wait.edit_text(
            f"😕 <b>No IMDb results for:</b> <code>{query}</code>"
        )

    title = _clean(data.get("title"))
    year = _clean(data.get("year"))
    rating = _clean(data.get("rating"))
    votes = _clean(data.get("votes"))
    imdb_id = _clean(data.get("imdb_id"))
    kind = _clean(data.get("kind")).title()
    runtime = _clean(data.get("runtime"))
    release = _clean(data.get("release_date"))
    countries = _clean(data.get("countries"))
    langs = _clean(data.get("languages"))
    genres = _clean(data.get("genres"))
    plot = _clean(data.get("plot"), "No storyline available.")
    if len(plot) > 450:
        plot = plot[:450].rsplit(" ", 1)[0] + "..."
    director = _clean(data.get("director"))
    writer = _clean(data.get("writer"))
    cast = _clean(data.get("cast"))
    aka = _clean(data.get("aka"), "")
    cert = _clean(data.get("certificates"), "")
    cert = cert.split(",")[0].strip() if cert != "N/A" else ""
    url = data.get("url") or f"https://www.imdb.com/title/{imdb_id}"

    async with aiohttp.ClientSession() as session:
        streaming = await _streaming(session, imdb_id)

    lang_tags = " ".join(_tag(l) for l in langs.split(",") if l.strip()) if langs != "N/A" else "N/A"
    genre_tags = " ".join(_tag(g) for g in genres.split(",") if g.strip()) if genres != "N/A" else "N/A"
    country_tag = " ".join(_tag(c) for c in countries.split(",")[:1] if c.strip())

    rating_line = f"🏆 <b>USER RATINGS:</b> {rating} / 10"
    if cert:
        rating_line += f" | {cert}"
    rating_line += f"\n({rating} based on {votes} user ratings)"

    text = (
        f"🎪 <b>TITLE:</b> <a href=\"{url}\">{title} ({year})</a>\n"
        + (f"<b>ALSO KNOWN AS</b> {aka}\n" if aka != "N/A" else "")
        + f"{rating_line}\n"
        f"🌠 <b>IMDB ID:</b> <code>{imdb_id}</code>\n"
        f"🪬 <b>TITLE TYPE:</b> {kind}\n"
        f"🕰 <b>DURATION:</b> {runtime}\n"
        + (f"🍿 <b>STREAMING ON:</b> {streaming}\n" if streaming else "")
        + f"<b>RELEASE DATE:</b> {release}"
        + (f" ({countries.split(',')[0].strip()}) {country_tag}" if countries != "N/A" else "")
        + f"\n💬 <b>LANGUAGE:</b> {lang_tags}\n"
        f"📼 <b>GENRE:</b> {genre_tags}\n\n"
        f"📝 <b>STORY LINE:</b> {plot}\n\n"
        f"🎥 <b>DIRECTOR:</b> {director}\n"
        f"🎞 <b>WRITER:</b> {writer}\n"
        f"🚥 <b>ACTORS:</b> {cast}"
    )

    buttons = []
    trailer_url = f"https://www.youtube.com/results?search_query={title} {year} trailer".replace(" ", "+")
    buttons.append([InlineKeyboardButton("🎬 Trailer", url=trailer_url)])
    if data.get("imdb_id"):
        buttons.append(
            [InlineKeyboardButton("📝 sᴜʙᴛɪᴛʟᴇs", callback_data=f"subdl#{data['imdb_id']}")]
        )
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    poster = data.get("poster")
    try:
        if poster:
            await message.reply_photo(
                photo=poster, caption=text, reply_markup=markup
            )
        else:
            await message.reply_text(text, reply_markup=markup)
        await wait.delete()
    except Exception as e:
        logger.exception(e)
        try:
            await wait.edit_text(text, reply_markup=markup)
        except Exception:
            pass
