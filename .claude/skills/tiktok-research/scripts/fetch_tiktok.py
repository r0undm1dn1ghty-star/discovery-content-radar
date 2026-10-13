#!/usr/bin/env python3
"""
Fetch TikTok videos from specified accounts using yt-dlp (no Apify, no key, free).

Returns the SAME normalized schema as the original clockworks/tiktok-scraper fetcher,
so the original analyze_posts.py works unchanged.

Caveats (honest):
- yt-dlp scrapes TikTok's public web; if TikTok changes their site, update yt-dlp (`pip install -U yt-dlp`).
- No share/collect counts on some videos (TikTok web doesn't always expose them) -> 0.
- Rate-limit friendly: yt-dlp handles one profile request per account (~2-5s).
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _ytdlp_cmd() -> list:
    """Find a working yt-dlp: binary on PATH first, else python -m yt_dlp."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def parse_accounts_file(accounts_path: str) -> list[str]:
    usernames = []
    with open(accounts_path, 'r') as f:
        in_table = False
        for line in f:
            line = line.strip()
            if line.startswith('| Username') or line.startswith('| Handle'):
                in_table = True
                continue
            if line.startswith('|---'):
                continue
            if in_table and line.startswith('|'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    username = parts[1]
                    if username.startswith('@') and not username.startswith('@example'):
                        usernames.append(username.lstrip('@'))
    return usernames


FIELDS = {
    "id": "%(id)s",
    "title": "%(title)s",
    "timestamp": "%(timestamp)s",
    "view_count": "%(view_count)s",
    "like_count": "%(like_count)s",
    "comment_count": "%(comment_count)s",
    "repost_count": "%(repost_count)s",
    "duration": "%(duration)s",
    "width": "%(width)s",
    "height": "%(height)s",
    "channel": "%(channel)s",
    "channel_follower_count": "%(channel_follower_count)s",
    "uploader": "%(uploader)s",
}


def fetch_profile_videos(username: str, limit: int) -> list[dict]:
    url = f"https://www.tiktok.com/@{username}"
    cmd = _ytdlp_cmd() + [
        "--flat-playlist", "--no-warnings", "--quiet",
        "--playlist-end", str(limit),
        "--print", json.dumps(FIELDS),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=300)
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError((result.stderr or "").strip()[-300:])
    videos = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            videos.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return videos


def normalize(v: dict, username: str) -> dict:
    text = v.get("title", "") or ""
    hashtags = []
    for part in text.split():
        if part.startswith("#") and len(part) > 1:
            hashtags.append({"name": part.lstrip("#"), "title": part.lstrip("#")})
    ts = v.get("timestamp") or 0
    create_time = int(ts) if ts else 0
    return {
        "id": v.get("id"),
        "text": text,
        "createTime": create_time,
        "createTimeISO": datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat() if create_time else "",
        "webVideoUrl": f"https://www.tiktok.com/@{username}/video/{v.get('id', '')}",
        "diggCount": v.get("like_count") or 0,
        "shareCount": v.get("repost_count") or 0,
        "playCount": v.get("view_count") or 0,
        "commentCount": v.get("comment_count") or 0,
        "collectCount": 0,  # not exposed by TikTok web
        "authorUsername": username,
        "authorNickname": v.get("channel") or v.get("uploader") or username,
        "authorFollowers": v.get("channel_follower_count") or 0,
        "authorFollowing": 0,
        "authorHearts": 0,
        "authorVerified": False,
        "videoDuration": v.get("duration") or 0,
        "videoHeight": v.get("height"),
        "videoWidth": v.get("width"),
        "coverUrl": f"https://www.tiktok.com/@{username}/video/{v.get('id', '')}",
        "hashtags": hashtags,
        "mentions": [],
        "isPinned": False,
        "isAd": False,
        "musicName": None,
        "musicAuthor": None,
        "musicOriginal": None,
        "_raw": v,
    }


def fetch_tiktok(usernames: list[str], results_limit: int = 50, days_back: int = 30,
                 sorting: str = "latest", output_path: str = None) -> list[dict]:
    if sorting != "latest":
        print("note: yt-dlp backend returns latest-first; 'popular' handled at analysis stage")

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp()

    print(f"Fetching videos from {len(usernames)} accounts via yt-dlp (free)...")
    print(f"Results limit per account: {results_limit}, newer than {days_back} days back")

    items = []
    for username in usernames:
        try:
            videos = fetch_profile_videos(username, results_limit)
        except Exception as e:
            print(f"  @{username}: FAILED ({e})", file=sys.stderr)
            continue
        kept = 0
        for v in videos:
            ts = v.get("timestamp") or 0
            if ts and int(ts) < cutoff_ts:
                continue
            items.append(normalize(v, username))
            kept += 1
        followers = next((i["authorFollowers"] for i in items[-kept:] if i["authorFollowers"]), 0) if kept else 0
        print(f"  @{username}: {kept} videos (followers: {followers})")

    print(f"Fetched {len(items)} videos total")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, default=str)
        print(f"Saved raw data to: {output_path}")

    return items


def main():
    parser = argparse.ArgumentParser(description='Fetch TikTok videos from accounts (yt-dlp, free)')
    parser.add_argument('--accounts-file', '-a', default='.claude/context/tiktok-accounts.md')
    parser.add_argument('--usernames', '-u', nargs='+')
    parser.add_argument('--limit', '-l', type=int, default=50)
    parser.add_argument('--days', '-d', type=int, default=30)
    parser.add_argument('--sorting', '-s', choices=['latest', 'popular', 'oldest'], default='latest')
    parser.add_argument('--output', '-o')
    args = parser.parse_args()

    if args.usernames:
        usernames = [u.lstrip('@') for u in args.usernames]
    else:
        if not os.path.exists(args.accounts_file):
            print(f"Error: Accounts file not found: {args.accounts_file}")
            sys.exit(1)
        usernames = parse_accounts_file(args.accounts_file)

    if not usernames:
        print("Error: No valid usernames found")
        sys.exit(1)

    print(f"Usernames to fetch: {', '.join(usernames)}")
    fetch_tiktok(usernames=usernames, results_limit=args.limit, days_back=args.days,
                 sorting=args.sorting, output_path=args.output)


if __name__ == '__main__':
    main()
