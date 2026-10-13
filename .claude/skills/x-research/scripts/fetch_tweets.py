#!/usr/bin/env python3
"""
Fetch tweets from specified X/Twitter accounts using twitterapi.io (Apify-free backend).
Requires XAPI_TOKEN environment variable (or in .env file).

Cost: $0.15 per 1,000 tweets returned (15 credits/tweet), $1 free credit on signup.
Docs: https://twitterapi.io/llms.txt
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)

API_BASE = "https://api.twitterapi.io"


def parse_accounts_file(accounts_path: str) -> list[str]:
    """Parse x-accounts.md and extract handles (supports | Handle | and | Username | tables)."""
    handles = []
    with open(accounts_path, 'r') as f:
        in_table = False
        for line in f:
            line = line.strip()
            if line.startswith('| Handle') or line.startswith('| Username'):
                in_table = True
                continue
            if line.startswith('|---'):
                continue
            if in_table and line.startswith('|'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    handle = parts[1]
                    if handle.startswith('@') and not handle.startswith('@example'):
                        handles.append(handle.lstrip('@'))
    return handles


def _parse_created_at(value: str) -> datetime | None:
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def fetch_user_tweets(session: requests.Session, handle: str, max_items: int) -> list[dict]:
    """Fetch latest tweets for one handle, following pagination cursor."""
    tweets = []
    cursor = ""
    while len(tweets) < max_items:
        params = {"userName": handle}
        if cursor:
            params["cursor"] = cursor
        resp = session.get(f"{API_BASE}/twitter/user/last_tweets", params=params, timeout=30)
        if resp.status_code == 401:
            print("Error: invalid XAPI_TOKEN (twitterapi.io)", file=sys.stderr)
            sys.exit(1)
        if resp.status_code == 429:
            import time
            print("Rate limited by twitterapi.io, sleeping 10s...", file=sys.stderr)
            time.sleep(10)
            continue
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("tweets", [])
        if not batch:
            break
        tweets.extend(batch)
        if not data.get("hasMore") or not data.get("cursor"):
            break
        cursor = data["cursor"]
    return tweets[:max_items]


def normalize(raw: dict) -> dict:
    """Normalize twitterapi.io tweet to the schema the original analyze_posts.py expects."""
    author = raw.get("author", {}) or {}
    return {
        "id": raw.get("id"),
        "text": raw.get("text", ""),
        "url": f"https://x.com/{author.get('userName', 'i')}/status/{raw.get('id', '')}",
        "createdAt": raw.get("createdAt", ""),
        "isRetweet": raw.get("isRetweet", False),
        "isQuote": raw.get("isQuote", False),
        "isReply": raw.get("isReply", False),
        "likeCount": raw.get("likeCount", 0) or 0,
        "retweetCount": raw.get("retweetCount", 0) or 0,
        "replyCount": raw.get("replyCount", 0) or 0,
        "quoteCount": raw.get("quoteCount", 0) or 0,
        "bookmarkCount": raw.get("bookmarkCount", 0) or 0,
        "viewCount": raw.get("viewCount", 0) or 0,
        "author": {
            "userName": author.get("userName", ""),
            "name": author.get("name", ""),
            "followers": author.get("followers", author.get("followersCount", 0)) or 0,
        },
        "media": raw.get("media", []) or [],
        "extendedEntities": raw.get("extendedEntities", {}) or {},
        "_raw": raw,
    }


def fetch_tweets(handles: list[str], days_back: int = 30, max_items_per_handle: int = 100,
                 output_path: str = None) -> list[dict]:
    token = os.environ.get('XAPI_TOKEN')
    if not token:
        print("Error: XAPI_TOKEN environment variable not set (twitterapi.io)", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({"X-API-Key": token, "Accept": "application/json"})

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    print(f"Fetching tweets from {len(handles)} accounts via twitterapi.io...")
    print(f"Window: last {days_back} days, max {max_items_per_handle} per handle")
    print(f"Estimated cost: ~${0.00015 * max_items_per_handle * len(handles):.2f}")

    all_tweets = []
    for handle in handles:
        raw_tweets = fetch_user_tweets(session, handle, max_items_per_handle)
        kept = 0
        for raw in raw_tweets:
            created = _parse_created_at(raw.get("createdAt", ""))
            if created and created < cutoff:
                continue
            item = normalize(raw)
            item["sourceHandle"] = handle
            all_tweets.append(item)
            kept += 1
        print(f"  @{handle}: {kept} tweets kept")

    print(f"Fetched {len(all_tweets)} tweets total")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_tweets, f, indent=2, default=str)
        print(f"Saved raw data to: {output_path}")

    return all_tweets


def main():
    parser = argparse.ArgumentParser(description='Fetch tweets from X/Twitter accounts (twitterapi.io)')
    parser.add_argument('--accounts-file', '-a', default='.claude/context/x-accounts.md')
    parser.add_argument('--handles', '-H', nargs='+', help='Specific handles (overrides accounts file)')
    parser.add_argument('--days', '-d', type=int, default=30)
    parser.add_argument('--max-items', '-m', type=int, default=100)
    parser.add_argument('--output', '-o', help='Output path for raw JSON')
    args = parser.parse_args()

    if args.handles:
        handles = [h.lstrip('@') for h in args.handles]
    else:
        if not os.path.exists(args.accounts_file):
            print(f"Error: Accounts file not found: {args.accounts_file}")
            sys.exit(1)
        handles = parse_accounts_file(args.accounts_file)

    if not handles:
        print("Error: No valid handles found")
        sys.exit(1)

    print(f"Handles to fetch: {', '.join(handles)}")
    fetch_tweets(handles=handles, days_back=args.days,
                 max_items_per_handle=args.max_items, output_path=args.output)


if __name__ == '__main__':
    main()
