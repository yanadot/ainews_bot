"""Shared helpers: state files, URL canonicalization, Telegram API, HTML checks."""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
CONFIG_DIR = os.path.join(ROOT, "config")

MAX_DRAFTS_PER_RUN = int(os.environ.get("MAX_DRAFTS_PER_RUN") or 5)
MAX_AGE_H = int(os.environ.get("MAX_AGE_H") or 36)
SEEN_TTL_DAYS = 150
TOPICS_TTL_H = 72

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ainews-bot"
)

ALLOWED_TAGS = {"b", "i", "a", "code", "blockquote"}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# ---------- state ----------

def state_path(name):
    return os.path.join(STATE_DIR, name)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_state(name, default):
    return load_json(state_path(name), default)


def save_state(name, data):
    save_json(state_path(name), data)


def load_seen():
    seen = load_state("seen.json", {})
    seen.setdefault("urls", {})
    seen.setdefault("initialized_sources", [])
    return seen


def prune_seen(seen):
    cutoff = now_utc() - timedelta(days=SEEN_TTL_DAYS)
    seen["urls"] = {
        u: ts for u, ts in seen["urls"].items()
        if (parse_iso(ts) or now_utc()) >= cutoff
    }
    return seen


def load_recent_topics():
    data = load_state("recent_topics.json", {"topics": []})
    cutoff = now_utc() - timedelta(hours=TOPICS_TTL_H)
    data["topics"] = [
        t for t in data.get("topics", [])
        if (parse_iso(t.get("ts", "")) or now_utc()) >= cutoff
    ]
    return data


# ---------- URLs ----------

_TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref", "ref_src", "guccounter"}


def canonical_url(url):
    """Lowercase host, drop www., utm_* and tracking params, fragment and trailing slash."""
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    query = [
        (k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = p.path.rstrip("/") or ""
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(query), ""))


# ---------- HTTP ----------

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(5_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), resp.geturl()


# ---------- Telegram ----------

class TelegramError(Exception):
    def __init__(self, method, description):
        super().__init__(f"{method}: {description}")
        self.description = description or ""


def tg(method, **params):
    """Call a Bot API method. DRY_RUN=1 prints the call instead of sending it."""
    params = {k: v for k, v in params.items() if v is not None}
    if os.environ.get("DRY_RUN") == "1":
        log(f"[dry-run] {method} {json.dumps(params, ensure_ascii=False)[:600]}")
        return {"message_id": int(time.time() * 1000) % 10**9}
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40 + int(params.get("timeout") or 0)) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except (ValueError, OSError):
                body = {"ok": False, "description": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise TelegramError(method, str(e))
            time.sleep(2 * (attempt + 1))
            continue
        if body.get("ok"):
            return body.get("result")
        retry_after = (body.get("parameters") or {}).get("retry_after")
        if retry_after and attempt < 2:
            time.sleep(min(int(retry_after), 30))
            continue
        raise TelegramError(method, body.get("description"))
    raise TelegramError(method, "retries exhausted")


def admin_chat_id():
    v = (os.environ.get("ADMIN_CHAT_ID") or "").strip()
    return int(v) if re.fullmatch(r"-?\d+", v) else None


def channel_id():
    v = (os.environ.get("CHANNEL_ID") or "").strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v and not v.startswith("@"):
        v = "@" + v
    return v or None


# ---------- HTML ----------

_TAG_RE = re.compile(r"</?\s*([a-zA-Z0-9-]+)[^>]*>")


def sanitize_html(text):
    """Keep only the tags Telegram post format allows; drop the rest, keep their text."""
    def repl(m):
        return m.group(0) if m.group(1).lower() in ALLOWED_TAGS else ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = _TAG_RE.sub(repl, text)
    return re.sub(r"\n{3,}", "\n\n", text)


def html_to_text(text):
    """Fallback for broken HTML: strip tags, keep link targets visible."""
    text = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text))
