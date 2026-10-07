"""Collect fresh entries from config/sources.json into state/candidates.json.

- rss: entries from the last MAX_AGE_H hours;
- page: list page, new links matched by link_pattern; on the first run a page source
  only remembers the links it sees, so the archive does not turn into drafts.

URL dedup happens here against state/seen.json. Entries are marked seen only after
send_drafts.py succeeds, so a failed Claude run retries them next time.
Writes has_candidates=true|false to $GITHUB_OUTPUT.
"""
import html
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from common import (
    CONFIG_DIR, MAX_AGE_H, canonical_url, http_get, iso, load_json, load_recent_topics,
    load_seen, load_state, log, now_utc, parse_iso, prune_seen, save_state,
)

SUMMARY_LEN = 500
MAX_TOTAL_CANDIDATES = 80


def strip_tags(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = parse_iso(s)
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el, *names):
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def parse_feed(xml_text):
    """Return [{title, url, published, summary}] for RSS 2.0, RSS 1.0 and Atom."""
    xml_text = re.sub(r"^[^<]+", "", xml_text)
    root = ET.fromstring(xml_text.encode("utf-8"))
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        link = ""
        for child in el:
            if _local(child.tag) != "link":
                continue
            href = child.get("href")
            rel = child.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
            if not href and (child.text or "").strip():
                link = child.text.strip()
                break
        if not link:
            guid = _child_text(el, "guid", "id")
            if guid.startswith("http"):
                link = guid
        items.append({
            "title": strip_tags(_child_text(el, "title")),
            "url": link,
            "published": parse_date(_child_text(el, "pubdate", "published", "updated", "date", "issued")),
            "summary": strip_tags(_child_text(el, "description", "summary", "content", "encoded"))[:SUMMARY_LEN],
        })
    return items


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


def parse_page(page_html, base_url, pattern):
    parser = _LinkParser()
    parser.feed(page_html)
    rx = re.compile(pattern)
    found = {}
    for href, text in parser.links:
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        if not rx.search(url):
            continue
        # keep the longest anchor text: cards often have an empty image link first
        if len(text) > len(found.get(url, "")):
            found[url] = text
        else:
            found.setdefault(url, text)
    return [{"title": t[:200], "url": u, "published": None, "summary": ""} for u, t in found.items()]


def keyword_ok(source, entry):
    kws = source.get("keywords")
    if not kws:
        return True
    hay = f"{entry['title']} {entry['summary']}".lower()
    return any(re.search(rf"\b{re.escape(k.lower())}\b", hay) for k in kws)


def main():
    sources = load_json(os.path.join(CONFIG_DIR, "sources.json"), {}).get("sources", [])
    seen = prune_seen(load_seen())
    cutoff = now_utc() - timedelta(hours=MAX_AGE_H)

    # Leftovers from a run where Claude failed stay in play.
    previous = load_state("candidates.json", {}).get("candidates", [])
    candidates = {c["canonical_url"]: c for c in previous if c["canonical_url"] not in seen["urls"]}
    errors = []

    for src in sources:
        if not src.get("enabled", True):
            continue
        sid = src["id"]
        try:
            body, final_url = http_get(src["url"])
            if src["type"] == "rss":
                entries = parse_feed(body)
            elif src["type"] == "page":
                entries = parse_page(body, final_url, src["link_pattern"])
                if not entries:
                    raise ValueError("no links matched link_pattern (JS-only page or layout changed?)")
            else:
                raise ValueError(f"unknown source type {src['type']}")
        except Exception as e:  # one broken source must not stop the run
            log(f"[{sid}] ERROR {type(e).__name__}: {e}")
            errors.append({"id": sid, "name": src.get("name", sid), "url": src["url"],
                           "error": f"{type(e).__name__}: {e}"[:300]})
            continue

        first_run = sid not in seen["initialized_sources"]
        new_count = 0
        for e in entries[: src.get("max_items", 30)]:
            if not e["url"]:
                continue
            canon = canonical_url(e["url"])
            if canon in seen["urls"] or canon in candidates:
                continue
            if e["published"] is not None and e["published"] < cutoff:
                continue
            if e["published"] is None and first_run:
                # undated entries on the first run are the archive: remember, don't draft
                seen["urls"][canon] = iso(now_utc())
                continue
            if not keyword_ok(src, e):
                continue
            candidates[canon] = {
                "source_id": sid,
                "source_name": src.get("name", sid),
                "source_role": src.get("role", ""),
                "title": e["title"],
                "url": e["url"],
                "canonical_url": canon,
                "published": iso(e["published"]) if e["published"] else None,
                "summary": e["summary"],
                "found_at": iso(now_utc()),
            }
            new_count += 1
        if first_run:
            seen["initialized_sources"].append(sid)
        log(f"[{sid}] ok: {len(entries)} entries, {new_count} new" + (" (baseline run)" if first_run else ""))

    ordered = sorted(candidates.values(), key=lambda c: c["published"] or c["found_at"], reverse=True)
    ordered = ordered[:MAX_TOTAL_CANDIDATES]

    save_state("seen.json", seen)
    save_state("source_errors.json", {"checked_at": iso(now_utc()), "errors": errors})
    save_state("recent_topics.json", load_recent_topics())
    save_state("candidates.json", {"collected_at": iso(now_utc()), "candidates": ordered})
    save_state("drafts.json", {})  # Claude must write a fresh one this run

    log(f"candidates: {len(ordered)}, source errors: {len(errors)}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"has_candidates={'true' if ordered else 'false'}\n")


if __name__ == "__main__":
    main()
