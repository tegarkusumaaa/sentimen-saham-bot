#!/usr/bin/env python3
"""
Cek satu per satu sumber mana yang hidup dan mana yang mati.
Jalankan lokal atau lewat `workflow_dispatch` sebelum menyalahkan Gemini.

    python debug_sources.py
"""

from datetime import datetime, timedelta, timezone

import main as bot

cutoff = datetime.now(timezone.utc) - timedelta(hours=bot.LOOKBACK_HOURS)

print("=" * 70)
print("GOOGLE NEWS")
print("=" * 70)
total_g = 0
for query in bot.GOOGLE_NEWS_QUERIES:
    url = (
        "https://news.google.com/rss/search?q="
        f"{bot.quote_plus(query)}+when:2d&hl=id&gl=ID&ceid=ID:id"
    )
    entries = bot.fetch_feed_entries(url, f"GNews '{query}'", retries=1)
    fresh = [e for e in entries if not bot.entry_datetime(e) or bot.entry_datetime(e) >= cutoff]
    total_g += len(fresh)
    status = "OK " if fresh else "KOSONG"
    print(f"[{status}] {query:<38} total={len(entries):>3} fresh={len(fresh):>3}")
    if fresh:
        print(f"         contoh: {fresh[0].get('title', '')[:90]}")

print()
print("=" * 70)
print("RSS MEDIA")
print("=" * 70)
total_m = 0
for label, url, strict in bot.EXTRA_RSS_FEEDS:
    entries = bot.fetch_feed_entries(url, label, retries=1)
    fresh = [e for e in entries if not bot.entry_datetime(e) or bot.entry_datetime(e) >= cutoff]
    total_m += len(fresh)
    status = "OK " if fresh else "MATI/KOSONG"
    print(f"[{status}] {label:<28} total={len(entries):>3} fresh={len(fresh):>3}")
    if fresh:
        print(f"         contoh: {fresh[0].get('title', '')[:90]}")
    else:
        print(f"         -> hapus atau ganti URL ini: {url}")

print()
print("=" * 70)
print("DATA PASAR (yfinance)")
print("=" * 70)
for row in bot.fetch_market_snapshot():
    print("  " + row)

print()
print("=" * 70)
print(f"RINGKASAN: Google News={total_g}, Media RSS={total_m}")
print("=" * 70)
if total_g == 0:
    print("Google News nol total -> kemungkinan besar IP kena blokir, bukan salah keyword.")
if total_g + total_m < bot.MIN_ITEMS:
    print("Di bawah MIN_ITEMS. Bot akan mengirim pesan diagnostik, bukan laporan.")
