"""Send Claude's drafts (state/drafts.json) to the admin chat for moderation.

Each draft = two messages: a service line and the post with
«✅ В канал» / «❌ Мимо» buttons. After a successful send, every candidate of this
run is marked seen, draft topics go to recent_topics.json, and candidates/drafts
are cleared. If drafts.json is missing or invalid, the script fails and nothing is
marked seen, so the next run retries.
"""
import html
import sys

from common import (
    MAX_DRAFTS_PER_RUN, TelegramError, admin_chat_id, html_to_text, iso, load_recent_topics,
    load_seen, load_state, log, now_utc, sanitize_html, save_state, tg,
)

SERVICE_MARK = "🗂"
KEYBOARD = {"inline_keyboard": [[
    {"text": "✅ В канал", "callback_data": "pub"},
    {"text": "❌ Мимо", "callback_data": "skip"},
]]}
MIN_LEN, MAX_LEN = 300, 1100  # soft bounds around the 400–900 target; outside → warn in service line


def visible_len(post_html):
    return len(html_to_text(post_html).strip())


def send_post(chat_id, post_html):
    try:
        return tg("sendMessage", chat_id=chat_id, text=post_html, parse_mode="HTML",
                  reply_markup=KEYBOARD, link_preview_options={"is_disabled": False})
    except TelegramError as e:
        if "parse" not in e.description.lower() and "entit" not in e.description.lower():
            raise
        log(f"broken HTML, sending as text: {e.description}")
        return tg("sendMessage", chat_id=chat_id, text=html_to_text(post_html),
                  reply_markup=KEYBOARD)


def main():
    chat = admin_chat_id()
    if chat is None:
        sys.exit("ADMIN_CHAT_ID is not set: send /start to the bot and run the approvals workflow")

    data = load_state("drafts.json", None)
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), list):
        sys.exit("state/drafts.json is missing or invalid; candidates stay unseen for the next run")

    drafts = [d for d in data["drafts"] if isinstance(d, dict) and d.get("post_html")]
    drafts.sort(key=lambda d: -int(d.get("score") or 0))
    drafts = drafts[:MAX_DRAFTS_PER_RUN]

    topics = load_recent_topics()
    sent = 0
    for d in drafts:
        post = sanitize_html(d["post_html"].strip())
        n = visible_len(post)
        warn = f"\n⚠️ длина {n} знаков" if not MIN_LEN <= n <= MAX_LEN else ""
        service = (
            f"{SERVICE_MARK} <b>{html.escape(str(d.get('category', '?')))}</b> · score {html.escape(str(d.get('score', '?')))}"
            f" · {html.escape(str(d.get('source_name', '')))}\n"
            f"{html.escape(str(d.get('why', '')))}"
            + (f"\n<a href=\"{html.escape(d['source_url'], quote=True)}\">первоисточник</a>" if d.get("source_url") else "")
            + warn
        )
        try:
            tg("sendMessage", chat_id=chat, text=service, parse_mode="HTML",
               link_preview_options={"is_disabled": True})
            send_post(chat, post)
        except TelegramError as e:
            log(f"failed to send draft {d.get('topic')!r}: {e}")
            continue
        sent += 1
        topics["topics"].append({"topic": d.get("topic") or d.get("title", ""),
                                 "url": d.get("source_url", ""), "ts": iso(now_utc())})

    if drafts and not sent:
        sys.exit("no draft could be sent; candidates stay unseen for the next run")

    # Everything Claude looked at this run is now seen, taken or not.
    seen = load_seen()
    for c in load_state("candidates.json", {}).get("candidates", []):
        seen["urls"][c["canonical_url"]] = iso(now_utc())
    save_state("seen.json", seen)
    save_state("recent_topics.json", topics)
    save_state("candidates.json", {"collected_at": None, "candidates": []})
    save_state("drafts.json", {})  # no "drafts" key: the next run must write a fresh file
    log(f"drafts sent: {sent}/{len(drafts)}")


if __name__ == "__main__":
    main()
