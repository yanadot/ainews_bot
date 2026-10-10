"""Send the digest (state/digest.json, written by the cheap model) to the admin chat.

One message: up to 10 topics (title, what it is, why it matters for the audience) and
number buttons. Pressing a number queues that topic for a full post (approvals.py).
After a successful send all candidates of this run are marked seen, offered topics go to
recent_topics.json and state/topics.json, and candidates are cleared.
"""
import html
import sys

from common import (
    TelegramError, admin_chat_id, iso, load_recent_topics, load_seen, load_state, log,
    now_utc, parse_iso, save_state, tg,
)

MAX_TOPICS = 10
TOPICS_TTL_H = 72
TG_LIMIT = 4000


def topic_keyboard(digest_id, topics):
    """Number buttons, 5 per row. A topic already taken shows a check mark."""
    buttons = []
    for t in topics:
        label = f"✍️ {t['n']}" if t.get("status", "offered") != "offered" else str(t["n"])
        buttons.append({"text": label, "callback_data": f"t:{digest_id}:{t['n']}"})
    return {"inline_keyboard": [buttons[i:i + 5] for i in range(0, len(buttons), 5)]}


def render(topics, header):
    blocks = [header]
    for t in topics:
        link = (f' <a href="{html.escape(t["source_url"], quote=True)}">→</a>'
                if t.get("source_url") else "")
        blocks.append(
            f"<b>{t['n']}. {html.escape(t['title'])}</b>{link}\n"
            f"{html.escape(t.get('what', ''))}\n"
            f"<i>{html.escape(t.get('why', ''))}</i>"
        )
    return "\n\n".join(blocks)


def prune_digests(store):
    cutoff = now_utc().timestamp() - TOPICS_TTL_H * 3600
    store["digests"] = {
        k: v for k, v in store.get("digests", {}).items()
        if (parse_iso(v.get("created_at", "")) or now_utc()).timestamp() >= cutoff
    }
    return store


def main():
    chat = admin_chat_id()
    if chat is None:
        sys.exit("ADMIN_CHAT_ID is not set")

    data = load_state("digest.json", None)
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        sys.exit("state/digest.json is missing or invalid; candidates stay unseen for the next run")

    topics = [t for t in data["topics"] if isinstance(t, dict) and t.get("title")]
    topics.sort(key=lambda t: -int(t.get("score") or 0))
    topics = topics[:MAX_TOPICS]
    for i, t in enumerate(topics, 1):
        t["n"] = i
        t["status"] = "offered"

    now = now_utc()
    digest_id = now.strftime("%m%d%H%M")
    header = f"🗞 <b>Темы на {'утро' if now.hour < 12 else 'вечер'} · {now.strftime('%d.%m')}</b>\nНажми номер — напишу пост."

    if topics:
        text = render(topics, header)
        while len(text) > TG_LIMIT and topics:  # Telegram message limit: drop the weakest
            topics.pop()
            text = render(topics, header)
        try:
            msg = tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                     reply_markup=topic_keyboard(digest_id, topics),
                     link_preview_options={"is_disabled": True})
        except TelegramError as e:
            sys.exit(f"could not send digest: {e}")
        message_id = msg.get("message_id")
    else:
        tg("sendMessage", chat_id=chat, text="🗞 За эти полдня ничего важного не нашлось.")
        message_id = None

    store = prune_digests(load_state("topics.json", {"digests": {}}))
    if topics:
        store["digests"][digest_id] = {"created_at": iso(now), "message_id": message_id, "topics": topics}
    save_state("topics.json", store)

    recent = load_recent_topics()
    for t in topics:
        recent["topics"].append({"topic": t["title"], "url": t.get("source_url", ""),
                                 "ts": iso(now), "kind": "offered"})
    save_state("recent_topics.json", recent)

    candidates = load_state("candidates.json", {}).get("candidates", [])
    seen = load_seen()
    for c in candidates:
        seen["urls"][c["canonical_url"]] = iso(now)
    save_state("seen.json", seen)
    save_state("triage_log.json", {"run_at": iso(now), "candidates": len(candidates),
                                   "offered": [{"n": t["n"], "title": t["title"], "score": t.get("score"),
                                                "source_url": t.get("source_url")} for t in topics]})
    save_state("candidates.json", {"collected_at": None, "candidates": []})
    save_state("digest.json", {})
    log(f"digest {digest_id}: {len(topics)} topics from {len(candidates)} candidates")


if __name__ == "__main__":
    main()
