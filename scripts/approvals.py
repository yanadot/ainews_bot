"""Process admin actions from Telegram (getUpdates) without Claude.

- «✅ В канал»  → copy the draft to CHANNEL_ID as is, button → «Опубликовано»
- «❌ Мимо»     → button → «Пропущено»
- reply to a draft with own text ≥150 chars → publish that text with its formatting
- reply shorter than 150 chars → not published, ask for the whole post
- /start while ADMIN_CHAT_ID is unset → answer with the chat_id
Updates from other chat_ids are ignored. The getUpdates offset lives in state/tg_offset.json.
"""
import sys

from common import TelegramError, admin_chat_id, channel_id, load_state, log, save_state, tg

MIN_REPLY_LEN = 150
SERVICE_MARK = "🗂"


def done_markup(label):
    return {"inline_keyboard": [[{"text": label, "callback_data": "done"}]]}


def is_draft(msg):
    """A draft is a bot message with our buttons (not the service line)."""
    if not msg or not (msg.get("from") or {}).get("is_bot"):
        return False
    if (msg.get("text") or "").startswith(SERVICE_MARK):
        return False
    return True


def draft_state(msg):
    rows = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
    data = {b.get("callback_data") for row in rows for b in row}
    if "pub" in data:
        return "open"
    if "done" in data:
        return "closed"
    return "unknown"


def answer(cq_id, text):
    try:
        tg("answerCallbackQuery", callback_query_id=cq_id, text=text)
    except TelegramError as e:  # stale queries (>15 min) can't be answered; harmless
        log(f"answerCallbackQuery: {e}")


def handle_callback(cq, admin, channel):
    msg = cq.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    if (cq.get("from") or {}).get("id") != admin and chat != admin:
        return
    action = cq.get("data")
    if action == "done":
        answer(cq["id"], "Уже обработано")
        return
    if action == "pub":
        if not channel:
            answer(cq["id"], "CHANNEL_ID не задан")
            return
        try:
            tg("copyMessage", chat_id=channel, from_chat_id=chat, message_id=msg["message_id"])
        except TelegramError as e:
            log(f"publish failed: {e}")
            answer(cq["id"], f"Ошибка публикации: {e.description[:150]}")
            return
        tg("editMessageReplyMarkup", chat_id=chat, message_id=msg["message_id"],
           reply_markup=done_markup("✅ Опубликовано"))
        answer(cq["id"], "Опубликовано")
    elif action == "skip":
        tg("editMessageReplyMarkup", chat_id=chat, message_id=msg["message_id"],
           reply_markup=done_markup("❌ Пропущено"))
        answer(cq["id"], "Пропущено")


def handle_message(msg, admin, channel):
    chat = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""

    if text.startswith("/start"):
        if admin is None:
            tg("sendMessage", chat_id=chat,
               text=f"Твой chat_id: <code>{chat}</code>\nДобавь его в GitHub Secrets как ADMIN_CHAT_ID.",
               parse_mode="HTML")
        elif chat == admin:
            tg("sendMessage", chat_id=chat, text="Бот на месте. Черновики приходят сюда.")
        return

    if admin is None or chat != admin:
        return

    original = msg.get("reply_to_message")
    if not is_draft(original):
        return
    if draft_state(original) == "closed":
        tg("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
           text="Этот черновик уже обработан, ничего не публикую.")
        return
    if len(text.strip()) < MIN_REPLY_LEN:
        tg("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
           text=f"Не публикую: ответ короче {MIN_REPLY_LEN} символов. "
                "Если хочешь свою версию, пришли пост целиком ответом на черновик.")
        return
    if not channel:
        tg("sendMessage", chat_id=chat, text="CHANNEL_ID не задан, публиковать некуда.")
        return
    try:
        # copyMessage keeps my own formatting (entities) as is
        tg("copyMessage", chat_id=channel, from_chat_id=chat, message_id=msg["message_id"])
    except TelegramError as e:
        tg("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
           text=f"Ошибка публикации: {e.description[:300]}")
        return
    try:
        tg("editMessageReplyMarkup", chat_id=chat, message_id=original["message_id"],
           reply_markup=done_markup("✏️ Опубликована моя версия"))
    except TelegramError as e:
        log(f"editMessageReplyMarkup: {e}")
    tg("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"], text="Опубликовано ✅")


def main():
    admin = admin_chat_id()
    channel = channel_id()
    state = load_state("tg_offset.json", {"offset": 0})
    offset = state.get("offset", 0)

    while True:
        updates = tg("getUpdates", offset=offset or None, timeout=0, limit=100,
                     allowed_updates=["message", "callback_query"])
        if not updates:
            break
        for u in updates:
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    handle_callback(u["callback_query"], admin, channel)
                elif "message" in u:
                    handle_message(u["message"], admin, channel)
            except TelegramError as e:  # one bad update must not block the queue
                log(f"update {u['update_id']}: {e}")
            # persist after each update so a crash never replays a publish
            save_state("tg_offset.json", {"offset": offset})
        if len(updates) < 100:
            break

    if offset:
        # confirm on Telegram's side too, so a lost state commit can't replay a publish
        tg("getUpdates", offset=offset, timeout=0, limit=1)
    log(f"approvals done, offset={offset}")


if __name__ == "__main__":
    try:
        main()
    except TelegramError as e:
        sys.exit(f"Telegram error: {e}")
