"""File store (batch links) ported in the classic Unlimited/FileStore style.

Admin flow (bot PM only):
  /batch  -> bot asks for the FIRST message link of the source channel
          -> then asks for the LAST message link (same channel)
          -> bot collects every media post between them and returns ONE
             shareable link: https://t.me/<bot>?start=batch_<code>
  /link   -> same, but a single message link (one file batch)
  /cancel -> abort a running /batch or /link session

Anyone opening the batch link receives all the stored files in bot PM,
copied fresh from the source channel (bot must be admin / member there).

Links accepted: https://t.me/<username>/<msg_id>  and
                https://t.me/c/<internal_id>/<msg_id>  (private channels)

Data is stored in Mongo (collection: filestore_batches) as
{_id: code, chat_id, msg_ids: [...], by, ts}.
"""
import logging
import re
import secrets
import string
from datetime import datetime

import motor.motor_asyncio
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from info import ADMINS, DATABASE_URI, DATABASE_NAME, PROTECT_CONTENT
from utils import temp

logger = logging.getLogger(__name__)

_mongo = motor.motor_asyncio.AsyncIOMotorClient(DATABASE_URI)
_batches = _mongo[DATABASE_NAME]["filestore_batches"]

MAX_RANGE = 1000  # max message ids scanned between first and last link
_sessions = {}  # user_id -> {"step": "first"/"last", "mode": "batch"/"link", "first": (chat, msg_id)}

LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/(\d+)|([A-Za-z0-9_]+))/(\d+)")


def _parse_link(text: str):
    """Return (chat_id_or_username, msg_id) or None."""
    m = LINK_RE.search(text or "")
    if not m:
        return None
    internal, username, msg_id = m.groups()
    if internal:
        return int(f"-100{internal}"), int(msg_id)
    return username, int(msg_id)


def _new_code() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


async def _fetch_media_ids(client, chat_id, first_id, last_id):
    """Collect ids of media messages in [first_id, last_id] from chat_id."""
    ids = list(range(first_id, last_id + 1))
    found = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            msgs = await client.get_messages(chat_id, chunk)
        except Exception as e:
            logger.error("filestore get_messages failed: %s", e)
            return None
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            if not msg or getattr(msg, "empty", False):
                continue
            if any([msg.document, msg.video, msg.audio, msg.photo,
                    msg.animation, msg.voice, msg.video_note, msg.sticker]):
                found.append(msg.id)
    return found


async def _save_batch(code, chat_id, msg_ids, user_id):
    await _batches.update_one(
        {"_id": code},
        {"$set": {
            "chat_id": chat_id if isinstance(chat_id, int) else str(chat_id),
            "msg_ids": msg_ids,
            "by": user_id,
            "ts": datetime.utcnow(),
        }},
        upsert=True,
    )


async def _make_batch(client, message, chat_id, first_id, last_id):
    status = await message.reply_text("<b>⏳ Files collect cheyyunnu...</b>")
    found = await _fetch_media_ids(client, chat_id, first_id, last_id)
    if found is None:
        return await status.edit_text(
            "<b>❌ Aa channel il ninnu files edukkan pattiyilla.\n"
            "Bot aa channel il member/admin aanennu check cheyyu.</b>")
    if not found:
        return await status.edit_text("<b>❌ Aa range il media files onnum kandilla.</b>")
    code = _new_code()
    await _save_batch(code, chat_id, found, message.from_user.id)
    link = f"https://t.me/{temp.U_NAME}?start=batch_{code}"
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Batch Link", url=link)]])
    await status.edit_text(
        f"<b>✅ Batch ready!</b>\n\n"
        f"📦 Files: <b>{len(found)}</b>\n"
        f"🔗 Link: <code>{link}</code>\n\n"
        f"Ee link aarelum open cheythal files ellam avarude PM il kittum.",
        reply_markup=btn)


@Client.on_message(filters.command("batch") & filters.private & filters.user(ADMINS))
async def batch_cmd(client, message):
    _sessions[message.from_user.id] = {"step": "first", "mode": "batch"}
    await message.reply_text(
        "<b>📦 Batch create cheyyam.</b>\n\n"
        "Source channel ile <b>FIRST</b> post ee chat ilekku <b>forward</b> cheyyuka "
        "(allenkil post nte link ayakkuka).\n\n"
        "Cancel cheyyan: /cancel")


@Client.on_message(filters.command("link") & filters.private & filters.user(ADMINS))
async def link_cmd(client, message):
    _sessions[message.from_user.id] = {"step": "last", "mode": "link", "first": None}
    await message.reply_text(
        "<b>🔗 Single file link create cheyyam.</b>\n\n"
        "Source channel ile post ee chat ilekku <b>forward</b> cheyyuka "
        "(allenkil post nte link ayakkuka).\n\n"
        "Cancel cheyyan: /cancel")


@Client.on_message(filters.command("cancel") & filters.private & filters.user(ADMINS))
async def cancel_cmd(client, message):
    if _sessions.pop(message.from_user.id, None):
        await message.reply_text("<b>🚫 Cancel cheythu.</b>")
    else:
        await message.reply_text("<b>Active session onnum illa.</b>")


@Client.on_message(filters.private & filters.user(ADMINS), group=-1)
async def batch_link_input(client, message):
    session = _sessions.get(message.from_user.id)
    if not session:
        return
    if message.text and message.text.startswith("/"):
        return
    # Source ref: forwarded post (simple) or a t.me post link
    chat_ref = msg_id = None
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_id = getattr(message, "forward_from_message_id", None)
    if fwd_chat is not None and fwd_id:
        chat_ref, msg_id = fwd_chat.id, fwd_id
    elif message.text:
        parsed = _parse_link(message.text)
        if parsed:
            chat_ref, msg_id = parsed
    if chat_ref is None:
        await message.reply_text(
            "<b>❌ Post forward cheyyuka, allenkil valid post link ayakkuka.</b>\n"
            "Example: <code>https://t.me/channel/123</code>\n/cancel cheyyan /cancel")
        raise StopPropagation
    if session["mode"] == "link":
        _sessions.pop(message.from_user.id, None)
        await _make_batch(client, message, chat_ref, msg_id, msg_id)
        raise StopPropagation
    if session["step"] == "first":
        session["first"] = (chat_ref, msg_id)
        session["step"] = "last"
        await message.reply_text(
            "<b>👍 First post kitti.</b>\n\nIppo <b>LAST</b> post <b>forward</b> cheyyuka (same channel).")
        raise StopPropagation
    # step == last
    first_chat, first_id = session["first"]
    _sessions.pop(message.from_user.id, None)
    if str(first_chat) != str(chat_ref):
        await message.reply_text(
            "<b>❌ First um last um SAME channel ile post aayirikkanam.</b>\n/batch veendum thudanguka.")
        raise StopPropagation
    if msg_id < first_id:
        first_id, msg_id = msg_id, first_id
    if msg_id - first_id + 1 > MAX_RANGE:
        await message.reply_text(
            f"<b>❌ Range valuthaanu</b> (max {MAX_RANGE} posts). Cheriya batch aayi split cheyyuka.")
        raise StopPropagation
    await _make_batch(client, message, first_chat, first_id, msg_id)
    raise StopPropagation


@Client.on_message(filters.command("start") & filters.private, group=-1)
async def batch_delivery(client, message):
    if len(message.command) != 2 or not message.command[1].startswith("batch_"):
        return
    code = message.command[1][len("batch_"):]
    doc = await _batches.find_one({"_id": code})
    if not doc:
        await message.reply_text("<b>❌ Ee batch link invalid aanu / kaalam kazhinju poyi.</b>")
        raise StopPropagation
    chat_id = doc["chat_id"]
    if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit():
        chat_id = int(chat_id)
    sent = 0
    await message.reply_text(f"<b>📦 Batch files ayakkunnu: {len(doc['msg_ids'])}</b>")
    for msg_id in doc["msg_ids"]:
        try:
            await client.copy_message(
                chat_id=message.from_user.id,
                from_chat_id=chat_id,
                message_id=msg_id,
                protect_content=bool(PROTECT_CONTENT),
            )
            sent += 1
        except FloodWait as e:
            await _sleep(e.value)
            try:
                await client.copy_message(
                    chat_id=message.from_user.id,
                    from_chat_id=chat_id,
                    message_id=msg_id,
                    protect_content=bool(PROTECT_CONTENT),
                )
                sent += 1
            except Exception:
                pass
        except Exception as e:
            logger.error("filestore copy failed (%s/%s): %s", chat_id, msg_id, e)
    await message.reply_text(f"<b>✅ {sent} files ayachu.</b>")
    raise StopPropagation


async def _sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)
