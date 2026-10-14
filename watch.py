"""Watch the 2026 CIM bib transfer board for new bib offers.

Polls the board's WordPress comments API, remembers which comments it has
already seen, and prints one line per new comment that looks like someone
offering a bib. Prints nothing when there is nothing new.

Usage:
    python3 watch.py            # report new offers since last run
    python3 watch.py --test N   # classify the last N comments, no state change

Env vars (used by the GitHub Actions workflow):
    STATE_FILE   where to keep seen comment IDs (default: seen.json next to this file)
    NTFY_TOPIC   if set, also push each offer to https://ntfy.sh/<topic>
"""

import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://shop.runsra.org/wp-json/wp/v2/comments"
POST_ID = 60430
BOARD_URL = "https://shop.runsra.org/2026-cim-bib-transfer/"
STATE = Path(os.environ.get("STATE_FILE") or Path(__file__).with_name("seen.json"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

OFFER = re.compile(
    r"my (bib|entry|spot|registration)|selling|giving (up|away)|can'?t run|cannot run|"
    r"won'?t be able to (run|race|make)|unable to (run|race)|injur|have an? (extra )?(bib|entry)|"
    r"bib (is )?available|transfer(ring)? (my|it)|up for grabs|first (person|one|to)|"
    r"offering (my|a|an|one)|will transfer|sent (the|a) transfer|have to (drop|withdraw|pull out)",
    re.I,
)
SEEK = re.compile(
    r"looking for|seeking|in search of|\biso\b|need(ing)? a bib|would love (a|to (take|get|run))|"
    r"hoping (to|for)|\b(i'?m|i am|am) (very |still |definitely )?interested|want(ing)? (a|to buy)|if (you|anyone) (has|have)|"
    r"sorry about your|still (up for grabs|available)\?|\$\d+ (to|for)|(pay|offer(ing)?) (an )?extra",
    re.I,
)
STRONG = re.compile(r"my (bib|entry|spot|registration)|i have an? (extra )?(bib|entry)|can'?t run|cannot run|unable to (run|race)", re.I)


def fetch(pages: int) -> list:
    comments = []
    for page in range(1, pages + 1):
        url = (
            f"{API}?post={POST_ID}&per_page=100&page={page}"
            "&_fields=id,parent,author_name,date,link,content"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "cim-bib-watch/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            comments.extend(json.load(resp))
    return comments


def text_of(comment: dict) -> str:
    raw = re.sub(r"<[^>]+>", " ", comment["content"]["rendered"])
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def is_offer(comment: dict) -> bool:
    text = text_of(comment)
    if not OFFER.search(text):
        return False
    seeking = SEEK.search(text)
    if comment["parent"] == 0:
        return not seeking or bool(STRONG.search(text) and not re.search(r"looking for", text, re.I))
    # Replies are mostly people claiming someone else's offer; only flag clear sellers.
    return bool(STRONG.search(text)) and not seeking


def line(comment: dict) -> str:
    snippet = text_of(comment)[:160]
    return f"{comment['date'][5:16]} {comment['author_name']}: {snippet} | {comment.get('link', BOARD_URL)}"


def push(comment: dict) -> None:
    body = f"{comment['author_name']}: {text_of(comment)[:300]}".encode()
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body,
        headers={
            "Title": "CIM bib offered - reply fast",
            "Click": comment.get("link", BOARD_URL),
            "Priority": "high",
            "Tags": "runner",
        },
    )
    urllib.request.urlopen(req, timeout=30).close()


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        n = int(sys.argv[2])
        comments = fetch((n + 99) // 100)[:n]
        offers = [c for c in comments if is_offer(c)]
        print(f"{len(offers)} offers in last {len(comments)} comments")
        for c in offers:
            print(line(c))
        return

    seen = set(json.loads(STATE.read_text())) if STATE.exists() else None
    comments = fetch(1)
    ids = {c["id"] for c in comments}
    if seen is None:
        # First run: record the current board without alerting on old posts.
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(ids)))
        return
    new = [c for c in comments if c["id"] not in seen]
    for c in reversed(new):
        if is_offer(c):
            print(line(c))
            if NTFY_TOPIC:
                push(c)
    STATE.write_text(json.dumps(sorted(seen | ids)[-2000:]))


if __name__ == "__main__":
    main()
