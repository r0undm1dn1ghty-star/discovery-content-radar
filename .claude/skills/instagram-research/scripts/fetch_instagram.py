#!/usr/bin/env python3
"""
Fetch Instagram posts/reels from specified accounts using instaloader
(no Apify). Uses YOUR OWN Instagram account via a session file.

Setup (one-time):
    python -m instaloader --login YOUR_IG_USERNAME
    -> creates YOUR_IG_USERNAME.session in cwd; point IG_SESSION_DIR at that folder
    (the password is asked interactively and is NOT stored by these scripts)

Caveats (honest):
- Instagram aggressively rate-limits. Anonymous access breaks often; a logged-in
  session of a real account is the practical minimum.
- Keep max items per account modest (<= 30) and run frequency low (<= 1-2x/week).
- If IG blocks you, the paid fallback is Apify instagram-scraper (~$2.70/1000).
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


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


def get_session():
    try:
        import instaloader
    except ImportError:
        print("Error: instaloader not installed. Run: pip install instaloader", file=sys.stderr)
        sys.exit(1)

    L = instaloader.Instaloader(
        download_videos=False,
        download_comments=False,
        download_geotags=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    session_dir = os.environ.get("IG_SESSION_DIR", ".")
    username = os.environ.get("IG_SESSION_USER")
    session_file = Path(session_dir) / f"{username}.session" if username else None

    if username and session_file and session_file.exists():
        L.load_session_from_file(username, str(session_file))
        print(f"Loaded IG session for '{username}' from {session_file}")
    else:
        print("WARNING: no IG session loaded - anonymous mode. Instagram will likely "
              "rate-limit or block anonymous profile scrapes.", file=sys.stderr)
    return L


def fetch_instagram(usernames: list[str], results_type: str = "posts", results_limit: int = 50,
                    days_back: int = 30, output_path: str = None) -> list[dict]:
    import instaloader

    L = get_session()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    print(f"Fetching {results_type} from {len(usernames)} accounts via instaloader...")
    print(f"Limit per account: {results_limit}, newer than {days_back} days back")

    items = []
    for username in usernames:
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            followers = profile.followers
            kept = 0
            node_iter = profile.get_posts()
            if results_type == "reels":
                # reels are a subset of posts; filter by is_video + product_type when available
                node_iter = (p for p in node_iter if p.is_video)
            for post in node_iter:
                post_date = post.date_utc
                if post_date.tzinfo is None:
                    post_date = post_date.replace(tzinfo=timezone.utc)
                if post_date < cutoff:
                    break
                items.append({
                    "id": post.shortcode,
                    "shortcode": post.shortcode,
                    "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "caption": post.caption or "",
                    "hashtags": list(post.caption_hashtags or []),
                    "ownerUsername": post.owner_username,
                    "ownerFullName": profile.full_name,
                    "ownerFollowersCount": followers,
                    "likesCount": post.likes or 0,
                    "commentsCount": post.comments or 0,
                    "videoViewCount": (post.video_view_count or 0) if post.is_video else 0,
                    "isVideo": bool(post.is_video),
                    "timestamp": post_date.isoformat(),
                    "typeName": post.typename,
                    "_raw": {"shortcode": post.shortcode},
                })
                kept += 1
                if kept >= results_limit:
                    break
            print(f"  @{username}: {kept} posts (followers: {followers})")
        except Exception as e:
            print(f"  @{username}: FAILED ({e})", file=sys.stderr)
        time.sleep(3)

    print(f"Fetched {len(items)} items total")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, default=str)
        print(f"Saved raw data to: {output_path}")

    return items


def main():
    parser = argparse.ArgumentParser(description='Fetch Instagram posts/reels (instaloader, no Apify)')
    parser.add_argument('--accounts-file', '-a', default='.claude/context/instagram-accounts.md')
    parser.add_argument('--usernames', '-u', nargs='+')
    parser.add_argument('--type', '-t', choices=['posts', 'reels'], default='posts')
    parser.add_argument('--limit', '-l', type=int, default=50)
    parser.add_argument('--days', '-d', type=int, default=30)
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
    fetch_instagram(usernames=usernames, results_type=args.type, results_limit=args.limit,
                    days_back=args.days, output_path=args.output)


if __name__ == '__main__':
    main()
