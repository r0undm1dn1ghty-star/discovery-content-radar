#!/usr/bin/env python3
"""
Fetch recent long-form videos of YouTube channels via yt-dlp (no TubeLab, no API key).

Usage:
    python get_channel_videos.py CHANNEL_ID_OR_URL [--limit 50] [--format summary]
    python get_channel_videos.py --from-context  # reads .claude/context/youtube-channel.md

Output: list of {id, title, viewCount, uploadDate, url, channelId}
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path


def resolve_channel_url(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        return value
    if value.startswith("UC") and len(value) == 24:
        return f"https://www.youtube.com/channel/{value}/videos"
    return f"https://www.youtube.com/@{value.lstrip('@')}/videos"


def parse_channel_from_context(path: str) -> str:
    """Extract channel id/handle from youtube-channel.md ('Channel ID:' or 'Channel Handle:' lines)."""
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if 'Channel ID' in line and ':' in line:
            val = line.split(':', 1)[1].strip().strip('*')
            if val and val != 'UCxxxxxxxxxxxxxxxxxx':
                return val
        if 'Channel Handle' in line and ':' in line:
            val = line.split(':', 1)[1].strip().strip('*')
            if val and val.startswith('@'):
                return val
    return ""


def fetch_channel_videos(channel: str, limit: int = 50) -> list[dict]:
    url = resolve_channel_url(channel)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--no-warnings", "--quiet",
        "--playlist-end", str(limit),
        "--print", json.dumps({
            "id": "%(id)s",
            "title": "%(title)s",
            "viewCount": "%(view_count)s",
            "uploadDate": "%(upload_date)s",
            "duration": "%(duration)s",
            "channelId": "%(channel_id)s",
            "channel": "%(channel)s",
        }),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0 and not result.stdout.strip():
        print(f"yt-dlp error for {url}:", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        sys.exit(1)

    videos = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        def _int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
        videos.append({
            "id": item.get("id"),
            "title": item.get("title", ""),
            "viewCount": _int(item.get("viewCount")),
            "uploadDate": item.get("uploadDate", ""),
            "duration": _int(item.get("duration")),
            "channelId": item.get("channelId", ""),
            "channel": item.get("channel", ""),
            "url": f"https://www.youtube.com/watch?v={item.get('id')}",
        })
    return videos


def main():
    parser = argparse.ArgumentParser(description='Fetch channel videos via yt-dlp (free)')
    parser.add_argument('channel', nargs='?', help='Channel ID, @handle, or URL')
    parser.add_argument('--from-context', action='store_true',
                        help='Read channel from .claude/context/youtube-channel.md')
    parser.add_argument('--limit', '-l', type=int, default=50)
    parser.add_argument('--format', '-f', choices=['summary', 'json'], default='summary')
    args = parser.parse_args()

    channel = args.channel
    if args.from_context or not channel:
        ctx = Path('.claude/context/youtube-channel.md')
        if not ctx.exists():
            print("Error: no channel given and .claude/context/youtube-channel.md not found")
            sys.exit(1)
        channel = parse_channel_from_context(str(ctx))
        if not channel:
            print("Error: could not parse channel from youtube-channel.md")
            sys.exit(1)

    print(f"Fetching up to {args.limit} videos from {channel} via yt-dlp...")
    videos = fetch_channel_videos(channel, args.limit)
    print(f"Fetched {len(videos)} videos")

    if args.format == 'json':
        print(json.dumps(videos, indent=2, ensure_ascii=False))
    else:
        for v in videos[:20]:
            print(f"  {v['viewCount']:>10,}  {v['uploadDate']}  {v['title'][:70]}")


if __name__ == '__main__':
    main()
