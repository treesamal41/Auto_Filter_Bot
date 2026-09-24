import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from pyrogram import Client, filters

from info import TMDB_API_KEY, OTT_CHANNEL, ADMINS
from database.users_chats_db import db

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"

# Matched (case-insensitive) against TMDB's provider list for India
WANTED_PROVIDERS = [
    "netflix",
    "prime video",
    "hotstar",
    "disney",
    "jiocinema",
    "jio cinema",
    "zee5",
    "zee 5",
    "sony",
    "aha",
    "sun nxt",
    "sunnxt",
]

MAX_POSTS_PER_RUN = 8
LOOKBACK_DAYS = 14


async def tmdb_get(session, path, **params):
    params["api_key"] = TMDB_API_KEY
    try:
        async with session.get(f"{TMDB_BASE}{path}", params=params, timeout=30) as resp:
            if resp.status != 200:
                logger.warning(f"TMDB {path} -> HTTP {resp.status}")
                return None
            return await resp.json()
    except Exception as e:
        logger.warning(f"TMDB {path} failed: {e}")
        return None


async def get_in_providers(session):
    """TMDB provider IDs available in India, filtered to the ones we want."""
    data = await tmdb_get(session, "/watch/providers/movie", watch_region="IN", language="en-US")
    if not data:
        return {}
    out = {}
    for p in data.get("results", []):
        name = (p.get("provider_name") or "").lower()
        if any(w in name for w in WANTED_PROVIDERS):
            out[p["provider_id"]] = p.get("provider_name")
    logger.info(f"OTT providers (IN): {list(out.values())}")
    return out


async def get_platforms(session, media_type, tmdb_id, provider_map):
    data = await tmdb_get(session, f"/{media_type}/{tmdb_id}/watch/providers")
    if not data:
        return []
    try:
        provs = data["results"]["IN"]["flatrate"]
    except (KeyError, TypeError):
        return []
    return [provider_map.get(p["provider_id"], p["provider_name"])
            for p in provs if p["provider_id"] in provider_map]


def build_caption(item, media_type, platforms):
    title = item.get("title") or item.get("name") or "Unknown"
    date = item.get("release_date") or item.get("first_air_date") or ""
    year = f" ({date[:4]})" if date else ""
    rating = item.get("vote_average") or 0
    overview = (item.get("overview") or "").strip()
    if len(overview) > 220:
        overview = overview[:220].rsplit(" ", 1)[0] + "..."
    kind = "Series" if media_type == "tv" else "Movie"
    plat = " | ".join(platforms) if platforms else "OTT"
    tags = " ".join("#" + p.replace("+", "Plus").replace(" ", "") for p in platforms[:3])

    caption = f"🎬 <b>{title}</b>{year}\n"
    caption += f"📺 {plat}\n"
    if rating:
        caption += f"⭐ {rating:.1f}/10\n"
    caption += f"🎭 {kind}\n"
    if overview:
        caption += f"\n{overview}\n"
    if tags:
        caption += f"\n{tags} #NewOnOTT"
    return caption


async def post_item(client, session, item, media_type, provider_map):
    tmdb_id = item["id"]
    key = f"ott_{media_type}_{tmdb_id}"
    if await db.movie_updates.find_one({"_id": key}):
        return False
    platforms = await get_platforms(session, media_type, tmdb_id, provider_map)
    if not platforms:
        return False
    caption = build_caption(item, media_type, platforms)
    poster = item.get("poster_path")
    try:
        if poster:
            await client.send_photo(OTT_CHANNEL, IMG_BASE + poster, caption=caption)
        else:
            await client.send_message(OTT_CHANNEL, caption)
        await db.movie_updates.insert_one({"_id": key})
        logger.info(f"OTT posted: {item.get('title') or item.get('name')} [{', '.join(platforms)}]")
        return True
    except Exception as e:
        logger.warning(f"OTT post failed for {tmdb_id}: {e}")
        return False


async def run_ott_update(client):
    """Fetch new OTT releases and post them. Returns number posted."""
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set, skipping OTT update")
        return 0
    posted = 0
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    async with aiohttp.ClientSession() as session:
        provider_map = await get_in_providers(session)
        if not provider_map:
            return 0
        ids = "|".join(str(i) for i in provider_map)

        targets = [
            ("movie", "/discover/movie", {"primary_release_date.gte": since,
                                          "sort_by": "primary_release_date.desc"}),
            ("tv", "/discover/tv", {"first_air_date.gte": since,
                                    "sort_by": "first_air_date.desc"}),
        ]
        for media_type, path, extra in targets:
            params = {"watch_region": "IN", "with_watch_providers": ids,
                      "language": "en-US", "page": 1, "vote_count.gte": 5}
            params.update(extra)
            data = await tmdb_get(session, path, **params)
            if not data:
                continue
            for item in sorted(data.get("results", []),
                               key=lambda x: x.get("popularity", 0), reverse=True):
                if posted >= MAX_POSTS_PER_RUN:
                    break
                if await post_item(client, session, item, media_type, provider_map):
                    posted += 1
                    await asyncio.sleep(2)
            if posted >= MAX_POSTS_PER_RUN:
                break
    logger.info(f"OTT update done, posted {posted}")
    return posted


async def ott_updates_poster(client):
    await asyncio.sleep(90)  # let the bot finish starting
    while True:
        try:
            await run_ott_update(client)
        except Exception:
            logger.exception("OTT updater crashed")
        await asyncio.sleep(24 * 3600)


@Client.on_message(filters.command("ottnow") & filters.user(ADMINS))
async def ott_now(client, message):
    status = await message.reply("Checking new OTT releases...")
    count = await run_ott_update(client)
    await status.edit(f"Done. {count} new OTT post(s).")
