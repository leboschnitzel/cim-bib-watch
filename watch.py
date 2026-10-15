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

# Explicit seller statements. Seekers often say "transfer", "first", "can complete
# the transfer", so only phrases a seller would use about their own bib count.
OFFER = re.compile(
    r"\bi (have|got) (a|an|one|my|1|an extra|a cim|a full)\b[^.!?]{0,40}\b(bib|entry)|"
    r"\bmy (cim |marathon |full marathon |race )?(bib|entry|spot|registration)\b|"
    r"\btransfer(ring)? my\b|\b(giving (up|away)|selling|offering) my\b|"
    r"\b(can'?t|cannot|won'?t be able to|unable to) (run|race|make it)\b|"
    r"\bbib (is )?(available|up for (grabs|transfer))\b|\bup for grabs\b|\bfirst come\b",
    re.I,
)
WEAK_OFFER = re.compile(r"\binjur(y|ed)\b|\bhave to (drop|withdraw|pull out)\b", re.I)
SEEK = re.compile(
    r"\blooking (for|to)\b|\bseeking\b|\bsearch(ing)?\b|\biso\b|\bneed(ing)?\b|\bwould love\b|"
    r"\bhoping\b|\b(i'?m|i am) (very |still |definitely |also )?interested\b|\bwant(ing)?\b|"
    r"\bif (you|anyone|someone) (has|have|is)\b|\bsorry (about|to hear)\b|\$\d+|\bextra (\$|money|cash)\b|"
    r"\b(can|could|will|happy to|able to|ready to) (complete|accept|do|take|finalize)\b[^.!?]{0,30}\btransfer|"
    r"\baccept (the |a |your )?transfer\b|\bmy first (marathon|one|full|cim)\b|\bstill (available|up for grabs)\b|"
    r"\b(i'?d|i would) (love|be happy) to (take|get|have)\b|\bplease (consider|pick) me\b",
    re.I,
)
# Seller statements clear enough to count even when seeker-ish words also appear
# ("I have a bib for you if you're still interested").
STRONG = re.compile(
    r"\bi (have|got) (a|an|one|my|1|an extra|a cim|a full)\b[^.!?]{0,40}\b(bib|entry)|"
    r"\bi (can'?t|cannot|am unable to|won'?t be able to) (run|race|use)\b",
    re.I,
)
# Seekers, thank-you notes, and offers that are already gone.
NEVER = re.compile(
    r"\blooking (for|to)\b|\bhop(e|ing) to (get|find|snag|land|receive)\b|\bif you can'?t\b|"
    r"\bmy first (marathon|one|full|cim)\b|\$\d+|\btransferred (you )?my\b|"
    r"\bcan'?t be transferred\b|\bgrateful\b|\bthank(s| you)( so much)? for\b|\bsorry (you|about|to hear)\b|"
    r"\bdo you (still )?have\b|\bit'?s (sold|gone|taken)\b|\btoo late\b|\bairbnb\b",
    re.I,
)
# In a reply, these mean the seller already picked someone.
CLAIMED = re.compile(
    r"\bmy bib to you\b|\bmove forward\b|\b(just )?sent you\b|\bi'?ll (request|start|initiate) the transfer\b|"
    r"\bplease accept\b|\bgo ahead and transfer\b",
    re.I,
)


def fetch(pages: int) -> list:
    comments = []
    for page in range(1, pages + 1):
        url = (
            f"{API}?post={POST_ID}&per_page=100&page={page}"
            "&_fields=id,parent,author_name,date,link,content"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "cim-bib-watch/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
        try:
            comments.extend(json.loads(body))
        except ValueError:
            # The site sometimes answers with an HTML error/challenge page.
            raise RuntimeError(f"board returned non-JSON: {body[:120]!r}") from None
    return comments


def text_of(comment: dict) -> str:
    raw = re.sub(r"<[^>]+>", " ", comment["content"]["rendered"])
    text = html.unescape(raw).replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", text).strip()


def is_offer(comment: dict) -> bool:
    text = text_of(comment)
    if NEVER.search(text):
        return False
    if comment["parent"] != 0 and CLAIMED.search(text):
        return False
    if SEEK.search(text):
        return bool(STRONG.search(text))
    if OFFER.search(text):
        return True
    # "I got injured" alone is only a seller signal in a new top-level post.
    return comment["parent"] == 0 and bool(WEAK_OFFER.search(text))


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
