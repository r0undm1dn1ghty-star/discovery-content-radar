#!/usr/bin/env python3
"""
Find outlier YouTube videos WITHOUT TubeLab: pure free stack.

Strategy (honest approximation of TubeLab's zScore):
1. Take reference channels (your niche) from CLI args or a keywords file.
2. For each channel, fetch its recent long-form videos via yt-dlp (get_channel_videos).
3. Compute a per-channel outlier score ourselves:
     z = (video_views - mean_views_of_channel) / std_views_of_channel
     score = z * recency_boost  (5%/day decay, floor 0.3)   <- same formula as original
   Because we only have each channel's OWN recent history (not TubeLab's
   full-channel lifetime baseline), the z-scores are noisier. Keep >=20 recent
   videos per channel for a usable baseline.
4. Optionally grab transcripts for the top outliers via youtube-transcript-api (free).

Usage:
    python find_outliers.py --channels @handle1 UCxxx ... [--per-channel 30] [--min-z 2.0] \
        [--transcripts] [--output-dir {folder}]

API keys needed: NONE. YouTube Data API is NOT required (yt-dlp scrapes).
"""

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from get_channel_videos import fetch_channel_videos  # noqa: E402


def score_video(views: float, channel_views: list[float], upload_date: str) -> float:
    """zScore against the channel's recent history, with recency boost (5%/day decay)."""
    if len(channel_views) < 3:
        return 0.0
    mean = statistics.mean(channel_views)
    std = statistics.pstdev(channel_views)
    if mean <= 0 or std <= 0:
        return 0.0
    z = (views - mean) / std

    recency_boost = 1.0
    if upload_date:
        try:
            pub = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - pub).days
            recency_boost = max(0.3, 1.0 - (days_old * 0.05))
        except ValueError:
            pass
    return round(z * recency_boost, 3)


def fetch_transcript(video_id: str, output_dir: Path) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        # new-style API (1.x): instantiate, then fetch
        try:
            fetched = YouTubeTranscriptApi().fetch(video_id)
            segments = [{"start": s.start, "text": s.text} for s in fetched]
        except TypeError:
            fetched = YouTubeTranscriptApi.fetch(video_id)
            segments = [{"start": s.start, "text": s.text} for s in fetched]
        except AttributeError:
            raw = YouTubeTranscriptApi.get_transcript(video_id)
            segments = raw
    except Exception:
        return None

    out = output_dir / f"{video_id}.transcript.txt"
    lines = []
    for seg in segments:
        start = float(seg.get("start", 0))
        minutes, seconds = divmod(int(start), 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg.get('text', '')}")
    out.write_text("\n".join(lines), encoding='utf-8')
    return str(out)


def download_thumbnail(video_id: str, output_dir: Path) -> str | None:
    for quality in ["maxresdefault", "hqdefault", "mqdefault"]:
        url = f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=15) as r:
                data = r.read()
            if len(data) < 2000:  # placeholder image
                continue
            out = output_dir / f"{video_id}.jpg"
            out.write_bytes(data)
            return str(out)
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description='Find YouTube outliers without TubeLab (free)')
    parser.add_argument('--channels', '-c', nargs='+', required=True,
                        help='Reference channels: @handles or channel IDs')
    parser.add_argument('--per-channel', type=int, default=30,
                        help='Recent videos per channel to fetch for baseline (default 30)')
    parser.add_argument('--min-z', type=float, default=2.0,
                        help='Outlier threshold: z-score multiplier (default 2.0)')
    parser.add_argument('--transcripts', action='store_true', help='Fetch transcripts for outliers')
    parser.add_argument('--output-dir', '-o', default='.')
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_videos = []
    for channel in args.channels:
        print(f"Channel: {channel}")
        videos = fetch_channel_videos(channel, args.per_channel)
        channel_views = [v["viewCount"] for v in videos if v["viewCount"] > 0]
        print(f"  fetched {len(videos)} videos (mean views: "
              f"{int(statistics.mean(channel_views)) if channel_views else 0:,})")
        for v in videos:
            v["outlierScore"] = score_video(v["viewCount"], channel_views, v["uploadDate"])
            v["channel"] = v.get("channel") or channel
        all_videos.extend(videos)

    outliers = [v for v in all_videos if v["outlierScore"] >= args.min_z]
    outliers.sort(key=lambda x: x["outlierScore"], reverse=True)

    for v in outliers[:10]:
        thumb = download_thumbnail(v["id"], out_dir)
        if thumb:
            v["thumbnailPath"] = thumb
        if args.transcripts:
            tp = fetch_transcript(v["id"], out_dir)
            if tp:
                v["transcriptPath"] = tp
        print(f"  OUTLIER z={v['outlierScore']:>6}  {v['viewCount']:>10,} views  {v['title'][:70]}")

    raw_path = out_dir / "raw.json"
    raw_path.write_text(json.dumps(all_videos, indent=2, ensure_ascii=False), encoding='utf-8')
    out_path = out_dir / "outliers.json"
    out_path.write_text(json.dumps(outliers, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nSaved {len(all_videos)} videos -> {raw_path}")
    print(f"Saved {len(outliers)} outliers (z >= {args.min_z}) -> {out_path}")


if __name__ == '__main__':
    main()
