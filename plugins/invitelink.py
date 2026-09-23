import logging
import os
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from utils import is_check_admin
from database.users_chats_db import db
from info import ADMINS

logger = logging.getLogger(__name__)


@Client.on_message(filters.command("invitelink") & filters.group & filters.incoming)
async def get_invite_link(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    # Only group admins can fetch the invite link
    if not await is_check_admin(client, chat_id, user_id):
        await message.reply_text("Admins mathrame ee command use cheyyan pattu.")
        return

    try:
        invite_link = await client.export_chat_invite_link(chat_id)
    except Exception:
        logger.exception("export_chat_invite_link failed")
        await message.reply_text(
            "Invite link edukkan pattilla. Bot-nu 'Invite Users' permission ullathu urappakkuka."
        )
        return

    await message.reply_text(f"Invite link:\n{invite_link}")


@Client.on_message(filters.command("grouplinks") & filters.private & filters.incoming)
async def get_all_group_links(client, message):
    # Owner only
    if message.from_user.id not in ADMINS:
        return

    status = await message.reply_text("Groups scan cheyyunnu...")
    lines = []
    failed = 0
    chats = await db.get_all_chats()
    async for chat in chats:
        chat_id = chat.get("id")
        title = chat.get("title", "Unknown")
        try:
            link = await client.export_chat_invite_link(chat_id)
            lines.append(f"{title}\n{link}\n")
        except FloodWait as e:
            import asyncio
            await asyncio.sleep(e.value)
        except Exception:
            failed += 1
            continue

    if not lines:
        await status.edit_text("Bot admin aaya group onnum kandilla, allel link edukkan pattilla.")
        return

    text = "\n".join(lines)
    if len(text) < 4000:
        await status.edit_text(f"Bot ulla groups ({len(lines)}):\n\n{text}")
    else:
        path = "group_links.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await message.reply_document(path, caption=f"Bot ulla groups: {len(lines)}")
        os.remove(path)
        await status.delete()
    if failed:
        await message.reply_text(f"{failed} group-il link edukkan pattilla (bot admin alla).")
