#!/usr/bin/env python3
"""Sync talks from a YouTube channel's RSS feed into data/talks.json.

Standard-library only (urllib + xml.etree) so it adds no new dependencies and
runs anywhere the app runs. All persistence goes through the app's save_json so
it works on local JSON and Heroku Postgres (kv_store) alike.
"""

import urllib.request
import xml.etree.ElementTree as ET

from app import get_talks, save_json, TALKS_FILE

CHANNEL_ID = 'UCl3aV8OUXRroWvvAiU66DOw'
FEED_URL = f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}'

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'yt': 'http://www.youtube.com/xml/schemas/2015',
    'media': 'http://search.yahoo.com/mrss/',
}


def fetch_feed(url=FEED_URL):
    """Fetch the raw RSS/Atom feed XML from YouTube."""
    req = urllib.request.Request(url, headers={'User-Agent': 'marlene-ai-blog/talks-sync'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_entries(xml_bytes):
    """Parse atom:entry elements into talk dicts."""
    root = ET.fromstring(xml_bytes)
    talks = []
    for entry in root.findall('atom:entry', NS):
        title_el = entry.find('atom:title', NS)
        vid_el = entry.find('yt:videoId', NS)
        published_el = entry.find('atom:published', NS)
        desc_el = entry.find('media:group/media:description', NS)

        video_id = vid_el.text.strip() if vid_el is not None and vid_el.text else ''
        if not video_id:
            continue

        title = title_el.text.strip() if title_el is not None and title_el.text else ''
        date = published_el.text[:10] if published_el is not None and published_el.text else ''
        description = desc_el.text.strip() if desc_el is not None and desc_el.text else ''

        talks.append({
            'title': title,
            'video_id': video_id,
            'event': '',
            'date': date,
            'description': description,
        })
    return talks


def sync():
    existing = get_talks()
    existing_ids = {t.get('video_id') for t in existing}

    fetched = parse_entries(fetch_feed())

    new_talks = [t for t in fetched if t['video_id'] not in existing_ids]

    combined = existing + new_talks
    combined.sort(key=lambda t: t.get('date', ''), reverse=True)

    save_json(TALKS_FILE, combined)

    print(f"\u2705 Synced {len(new_talks)} new talk(s); {len(combined)} total.")
    return new_talks, combined


if __name__ == '__main__':
    sync()
