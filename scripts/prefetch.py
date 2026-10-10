"""Put the source page text into state/write_job.json before the writer runs.

Saves the writer most of its turns: it reads the text from the file instead of
fetching pages itself. A page that fails to load is left for the writer to fetch.
"""
from common import load_state, log, save_state
from extract import extract

MAX_CHARS = 12000


def main():
    job = load_state("write_job.json", {"topics": []})
    for t in job.get("topics", []):
        if t.get("source_text") or not t.get("source_url"):
            continue
        try:
            t["source_text"] = extract(t["source_url"], MAX_CHARS)
        except Exception as e:  # the writer will try WebFetch itself
            log(f"prefetch failed for {t['source_url']}: {type(e).__name__}: {e}")
    save_state("write_job.json", job)


if __name__ == "__main__":
    main()
