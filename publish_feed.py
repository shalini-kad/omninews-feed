#!/usr/bin/env python3
"""Fetch RSS headlines, sign them, and update feed.json.

Standalone version of the MeshNews gateway's internet-feed path — no
Meshtastic/radio dependency, so it can run in a GitHub Action. The private
key comes from MESHNEWS_PRIVATE_KEY (PEM contents) or a .pem file path in
MESHNEWS_PRIVATE_KEY_PATH.

Wire format (one LoRa-packet-sized line per bulletin):
    MN1|<id>|<unix_ts>|<CAT>|<headline>|<base64 ed25519 sig>
"""
import base64
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

import feedparser
from cryptography.hazmat.primitives import serialization

socket.setdefaulttimeout(20)

FEEDS = {
    "https://feeds.bbci.co.uk/news/world/rss.xml": "WRLD",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml": "SCI",
    "https://feeds.bbci.co.uk/news/business/rss.xml": "BIZ",
}

FEED_PATH = Path(__file__).resolve().parent / "feed.json"
CAP = 200
MAX_HEADLINE = 140  # keep bulletins inside a single LoRa text packet


def load_private_key():
    pem = os.environ.get("MESHNEWS_PRIVATE_KEY")
    if pem:
        return serialization.load_pem_private_key(pem.encode(), password=None)
    path = os.environ.get("MESHNEWS_PRIVATE_KEY_PATH")
    if path:
        return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    sys.exit("Set MESHNEWS_PRIVATE_KEY (PEM contents) or MESHNEWS_PRIVATE_KEY_PATH")


def bulletin_id(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:6]


def make_bulletin(private_key, bid: str, ts: int, cat: str, headline: str) -> str:
    # Must match gateway.py exactly: the signature covers the full message
    # INCLUDING the "MN1|" prefix.
    headline = headline.replace("|", "/").strip()
    if len(headline) > MAX_HEADLINE:
        headline = headline[: MAX_HEADLINE - 1] + "…"
    message = f"MN1|{bid}|{ts}|{cat}|{headline}"
    sig = base64.b64encode(private_key.sign(message.encode())).decode()
    return f"{message}|{sig}"


def main() -> None:
    key = load_private_key()
    existing = json.loads(FEED_PATH.read_text()) if FEED_PATH.exists() else []
    seen = {line.split("|")[1] for line in existing if line.count("|") >= 5}

    new = []
    for url, cat in FEEDS.items():
        feed = feedparser.parse(url)
        if getattr(feed, "bozo_exception", None):
            print(f"feed error {url}: {feed.bozo_exception}", file=sys.stderr)
        for entry in feed.entries[:10]:
            bid = bulletin_id(entry.get("link", entry.get("title", "")))
            if bid in seen:
                continue
            ts = int(time.mktime(entry.published_parsed)) if entry.get("published_parsed") else int(time.time())
            new.append(make_bulletin(key, bid, ts, cat, entry.title))
            seen.add(bid)

    merged = (existing + new)[-CAP:]
    FEED_PATH.write_text(json.dumps(merged, indent=0))
    print(f"{len(new)} new bulletins, {len(merged)} total")


if __name__ == "__main__":
    main()
