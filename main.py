#!/usr/bin/env python3
"""
Daily Macro & Stock Sentiment Bot untuk IHSG.

Alur:
  1. Kumpulkan headline 24 jam terakhir dari Google News RSS (query pendek, bukan
     frasa panjang) + RSS langsung media ekonomi Indonesia.
  2. Ambil snapshot harga pasar global/komoditas lewat yfinance (angka, bukan berita).
  3. Sintesis jadi laporan sentimen dengan Gemini.
  4. Kirim ke Telegram (HTML, dipotong otomatis, dengan fallback plain text).

Dirancang supaya GAGAL BERISIK: kalau sumber berita kosong, bot tetap mengirim
pesan diagnostik dan keluar dengan exit code 1 sehingga GitHub Actions jadi merah.
"""

from __future__ import annotations

import html
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

# --------------------------------------------------------------------------- #
# Konfigurasi
# --------------------------------------------------------------------------- #

WIB = ZoneInfo("Asia/Jakarta")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

GEMINI_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip(),
    "gemini-flash-latest",
    "gemini-3.8-flash",
]

# Berapa kali tiap model dicoba sebelum pindah ke model berikutnya.
# Error 503 ("high demand") dari Google bersifat sementara, jadi menunggu
# lebih lama jauh lebih berguna daripada langsung menyerah.
GEMINI_ATTEMPTS = int(os.environ.get("GEMINI_ATTEMPTS", "4"))

# --- Groq: penyedia cadangan kalau Gemini sedang tumbang ---------------------
# Gratis, tanpa kartu kredit, format API kompatibel OpenAI.
# Daftar model yang aktif: https://console.groq.com/docs/models
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_ATTEMPTS = int(os.environ.get("GROQ_ATTEMPTS", "3"))

# Urutan percobaan. Ubah jadi "groq,gemini" kalau mau Groq duluan.
LLM_ORDER = [
    name.strip().lower()
    for name in os.environ.get("LLM_ORDER", "gemini,groq").split(",")
    if name.strip()
]

# Berapa jam ke belakang berita dianggap relevan. 30 jam = 24 jam + buffer,
# karena jam publikasi RSS sering tidak presisi.
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "30"))

# Minimal headline yang harus terkumpul. Di bawah ini = ada yang rusak.
MIN_ITEMS = int(os.environ.get("MIN_ITEMS", "8"))

# Maksimal headline yang dikirim ke Gemini (biar prompt tidak membengkak).
MAX_ITEMS_TO_LLM = int(os.environ.get("MAX_ITEMS_TO_LLM", "80"))

DEBUG = os.environ.get("DEBUG", "0") == "1"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    }
)

# --------------------------------------------------------------------------- #
# Query Google News -- PENDEK. Google News memperlakukan setiap kata sebagai AND,
# jadi frasa panjang seperti "Corporate Action Saham Dividen Right Issue"
# praktis tidak akan pernah match artikel apa pun.
# --------------------------------------------------------------------------- #

GOOGLE_NEWS_QUERIES = [
    # Pasar & indeks
    "IHSG",
    "bursa efek indonesia",
    "saham asing net buy",
    # Moneter & makro
    "suku bunga Bank Indonesia",
    "BI rate",
    "kurs rupiah",
    "inflasi Indonesia",
    "neraca perdagangan Indonesia",
    "cadangan devisa",
    "The Fed suku bunga",
    # Kebijakan & regulator
    "OJK pasar modal",
    "kebijakan ekonomi pemerintah",
    # Corporate action (dipecah, bukan digabung!)
    "emiten dividen",
    "right issue emiten",
    "stock split OR buyback saham",
    "IPO saham",
    "akuisisi OR merger emiten",
    "laporan keuangan emiten",
    # Komoditas
    "harga batu bara",
    "harga nikel",
    "harga emas dunia",
    "harga minyak dunia",
    "harga CPO",
]

# RSS langsung dari media. Ini backup penting: kalau IP GitHub Actions kena
# rate-limit Google News, feed ini biasanya tetap jalan.
# strict=True -> headline disaring dulu pakai RELEVANCE_KEYWORDS (feed umum).
# Sudah dibuang karena membalas HTTP 403 dari runner GitHub (cek 20 Okt 2026):
# CNBC Indonesia, Kontan, Bisnis.com, CNN Indonesia, Tempo. Situs-situs itu
# memblokir IP pusat data lewat Cloudflare. Antara Ekonomi dibuang karena
# feed-nya hidup tapi isinya tidak pernah segar.
EXTRA_RSS_FEEDS = [
    # Terbukti bekerja.
    ("Detik Finance", "https://finance.detik.com/rss", True),
    # Kandidat, belum terverifikasi. Jalankan Debug Sources untuk mengecek,
    # lalu hapus baris yang berstatus MATI.
    ("Detik Bursa & Valas", "https://finance.detik.com/bursa-dan-valas/rss", False),
    ("Okezone Economy", "https://economy.okezone.com/rss", True),
    ("Sindonews Ekbis", "https://ekbis.sindonews.com/rss", True),
    ("Liputan6 Bisnis", "https://feed.liputan6.com/rss/bisnis", True),
    ("Suara Bisnis", "https://www.suara.com/rss/bisnis", True),
    ("Merdeka Uang", "https://www.merdeka.com/feed/uang", True),
    ("Emitennews", "https://www.emitennews.com/feed", False),
]

RELEVANCE_KEYWORDS = [
    "saham", "ihsg", "emiten", "bursa", "idx", "dividen", "right issue",
    "buyback", "ipo", "obligasi", "rupiah", "suku bunga", "bi rate",
    "bank indonesia", "inflasi", "ojk", "the fed", "ekspor", "impor",
    "neraca perdagangan", "batu bara", "batubara", "nikel", "emas", "cpo",
    "minyak", "komoditas", "investor", "pasar modal", "apbn", "pajak",
    "kuartal", "laba", "akuisisi", "merger", "danantara", "tarif",
]

# Snapshot harga -- ticker ini stabil di Yahoo Finance.
MARKET_TICKERS = [
    ("^JKSE", "IHSG"),
    ("USDIDR=X", "USD/IDR"),
    ("GC=F", "Emas (COMEX)"),
    ("CL=F", "Minyak WTI"),
    ("BZ=F", "Minyak Brent"),
    ("HG=F", "Tembaga"),
    ("DX-Y.NYB", "Dollar Index"),
    ("^TNX", "US Treasury 10Y"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^HSI", "Hang Seng"),
]

# Ticker yang berita per-emitennya masih relevan buat konteks global.
YF_NEWS_TICKERS = ["GC=F", "CL=F", "^GSPC"]


def log(msg: str) -> None:
    print(f"[{datetime.now(WIB):%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Pengambilan feed
# --------------------------------------------------------------------------- #


def fetch_feed_entries(url: str, label: str, retries: int = 3) -> list:
    """Ambil satu feed RSS. Mengembalikan list entry feedparser (bisa kosong)."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=20)
        except Exception as exc:
            log(f"   ! {label}: request gagal ({exc})")
            time.sleep(2 + attempt * 2)
            continue

        if resp.status_code != 200:
            log(f"   ! {label}: HTTP {resp.status_code}")
            time.sleep(3 + attempt * 3)
            continue

        head = resp.content[:4000].lower()
        if b"<rss" not in head and b"<feed" not in head and b"<rdf" not in head:
            # Google kadang membalas 200 tapi isinya halaman consent/captcha.
            log(f"   ! {label}: body bukan XML (kemungkinan diblokir/consent page)")
            time.sleep(3 + attempt * 3)
            continue

        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            log(f"   - {label}: feed valid tapi 0 entry")
        return parsed.entries

    return []


def entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tup = entry.get(key)
        if tup:
            try:
                return datetime(*tup[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def split_google_title(title: str) -> tuple[str, str]:
    """Google News memberi judul dengan format 'Judul - Nama Media'."""
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if head and len(tail) < 45:
            return head.strip(), tail.strip()
    return title.strip(), "Google News"


def is_relevant(title: str) -> bool:
    low = title.lower()
    return any(kw in low for kw in RELEVANCE_KEYWORDS)


def normalize_title(title: str) -> str:
    low = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    low = re.sub(r"\s+", " ", low).strip()
    return low[:70]


def collect_google_news(cutoff: datetime) -> list[dict]:
    items: list[dict] = []
    for query in GOOGLE_NEWS_QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(query)}+when:2d&hl=id&gl=ID&ceid=ID:id"
        )
        entries = fetch_feed_entries(url, f"GNews '{query}'")
        kept = 0
        for entry in entries[:12]:
            raw_title = (entry.get("title") or "").strip()
            if not raw_title:
                continue
            published = entry_datetime(entry)
            # when:2d dipakai supaya Google tidak terlalu agresif memotong,
            # penyaringan 24 jam dilakukan sendiri di bawah ini.
            if published and published < cutoff:
                continue
            title, source = split_google_title(raw_title)
            items.append(
                {
                    "title": title,
                    "source": source,
                    "published": published,
                    "origin": f"GoogleNews:{query}",
                }
            )
            kept += 1
            if kept >= 6:
                break
        log(f"   . GNews '{query}': {kept} headline dalam window")
        time.sleep(random.uniform(0.6, 1.4))  # jeda sopan, kurangi risiko 429
    return items


def collect_media_rss(cutoff: datetime) -> list[dict]:
    items: list[dict] = []
    for label, url, strict in EXTRA_RSS_FEEDS:
        entries = fetch_feed_entries(url, label, retries=2)
        kept = 0
        for entry in entries[:30]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            published = entry_datetime(entry)
            if published and published < cutoff:
                continue
            if strict and not is_relevant(title):
                continue
            items.append(
                {
                    "title": title,
                    "source": label,
                    "published": published,
                    "origin": f"RSS:{label}",
                }
            )
            kept += 1
            if kept >= 10:
                break
        log(f"   . {label}: {kept} headline dalam window")
        time.sleep(0.4)
    return items


# --------------------------------------------------------------------------- #
# yfinance
# --------------------------------------------------------------------------- #


def fetch_market_snapshot() -> list[str]:
    """Snapshot harga penutupan terakhir + perubahan harian."""
    rows: list[str] = []
    try:
        import yfinance as yf
    except Exception as exc:
        log(f"   ! yfinance tidak tersedia: {exc}")
        return rows

    for symbol, name in MARKET_TICKERS:
        try:
            hist = yf.Ticker(symbol).history(period="10d", interval="1d")
            closes = hist["Close"].dropna() if hist is not None and not hist.empty else None
            if closes is None or len(closes) < 2:
                log(f"   ! snapshot {symbol}: data kurang")
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            change = (last - prev) / prev * 100 if prev else 0.0
            rows.append(f"{name} ({symbol}): {last:,.2f} ({change:+.2f}%)")
        except Exception as exc:
            log(f"   ! snapshot {symbol} gagal: {exc}")
    return rows


def _parse_yf_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def collect_yfinance_news(cutoff: datetime) -> list[dict]:
    """
    Skema news yfinance SUDAH BERUBAH: field sekarang bersarang di item['content'].
    Kode lama yang memakai item.get('title') menghasilkan None, bukan error,
    sehingga kegagalan ini tidak terlihat sama sekali di log.
    """
    items: list[dict] = []
    try:
        import yfinance as yf
    except Exception:
        return items

    for symbol in YF_NEWS_TICKERS:
        try:
            raw = yf.Ticker(symbol).news or []
        except Exception as exc:
            log(f"   ! yfinance news {symbol}: {exc}")
            continue

        kept = 0
        for entry in raw[:8]:
            content = entry.get("content") if isinstance(entry, dict) else None
            block = content if isinstance(content, dict) else entry
            title = block.get("title") or entry.get("title")
            if not title:
                continue
            published = _parse_yf_time(
                block.get("pubDate")
                or block.get("displayTime")
                or entry.get("providerPublishTime")
            )
            if published and published < cutoff:
                continue
            items.append(
                {
                    "title": str(title).strip(),
                    "source": f"Yahoo Finance ({symbol})",
                    "published": published,
                    "origin": f"YF:{symbol}",
                }
            )
            kept += 1
        log(f"   . Yahoo Finance {symbol}: {kept} headline")
    return items


# --------------------------------------------------------------------------- #
# Dedup & format
# --------------------------------------------------------------------------- #


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = normalize_title(item["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def format_for_llm(items: list[dict]) -> str:
    lines = []
    for item in items:
        if item["published"]:
            stamp = item["published"].astimezone(WIB).strftime("%d/%m %H:%M")
        else:
            stamp = "waktu n/a"
        lines.append(f"- ({stamp} WIB) [{item['source']}] {item['title']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def build_prompt(news_block: str, market_block: str, today: str) -> str:
    return f"""Kamu adalah Senior Financial Market Analyst & Chief Economist untuk Pasar Modal Indonesia (IHSG).

Tanggal laporan: {today}

=== DATA PASAR (penutupan terakhir & perubahan harian) ===
{market_block or "Data harga tidak tersedia."}

=== HEADLINE 24 JAM TERAKHIR ===
{news_block}

TUGASMU: susun laporan sentimen harian untuk trader/investor IHSG.

Struktur output (ikuti persis, gunakan emoji):

**RINGKASAN SENTIMEN**
2-3 kalimat arah sentimen keseluruhan.

**MAKRO & MONETER**
Bullet. Suku bunga, rupiah, inflasi, kebijakan fiskal/regulator, sinyal global (Fed, DXY, yield UST).

**KOMODITAS**
Bullet. Arah harga + sektor IHSG yang terdampak.

**CORPORATE ACTION & EMITEN**
Bullet. Dividen, right issue, buyback, IPO, akuisisi, laporan keuangan. Tulis "Tidak ada yang signifikan" kalau memang tidak ada.

**PETA DAMPAK SEKTOR**
Untuk tiap sektor relevan, satu baris:
Sektor — 🟢 Bullish / 🔴 Bearish / 🟡 Netral — alasan singkat — emiten: KODE, KODE, KODE

**PANDANGAN IHSG HARI INI**
Bias (bullish/bearish/sideways), level/area yang diperhatikan kalau bisa disimpulkan, dan 1-2 risiko utama.

AKURASI:
- Hanya gunakan informasi dari data di atas. Jangan mengarang berita, angka, atau corporate action.
- Kalau sebuah bagian tidak punya dasar data, tulis satu baris bahwa tidak ada informasi. Jangan diisi spekulasi.
- Kode emiten harus 4 huruf kapital yang benar-benar ada di IDX.
- Jangan mengulang informasi yang sama di dua bagian berbeda.

GAYA PENULISAN (ini yang paling sering dilanggar, patuhi ketat):
- Satu kalimat maksimal 22 kata. Pecah kalimat panjang jadi dua.
- Satu bullet berisi satu gagasan saja. Jangan menumpuk tiga fakta dalam satu bullet.
- Tulis angka apa adanya tanpa penjelasan berlebihan. Contoh baik: "IHSG 5.185 (-11,57%)".
- Pakai bahasa Indonesia. Istilah asing hanya kalau tidak ada padanannya, dan ditulis biasa tanpa penanda apa pun.
- Langsung ke inti. Jangan menulis kalimat pembuka, penutup, atau basa-basi.
- Jangan memakai kata pengisi seperti "kendati demikian", "sementara itu", "perlu dicatat bahwa".

FORMAT (penting, output dikirim ke Telegram):
- Penebalan HANYA dengan **dua bintang**. Contoh: **MAKRO & MONETER**.
- JANGAN PERNAH memakai satu bintang untuk miring. Jangan memakai garis bawah, heading #, tabel, link, atau blok kode.
- Bullet diawali tanda minus di awal baris.
- Beri satu baris kosong antar bagian.
- Total maksimal 2.200 karakter. Ini batas keras supaya muat dalam satu pesan Telegram. Kalau data terlalu banyak, pilih yang paling berdampak dan buang sisanya.
"""


def analyze_with_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY kosong.")

    from google import genai

    try:
        from google.genai import types

        config = types.GenerateContentConfig(temperature=0.35)
    except Exception:
        config = None

    client = genai.Client(api_key=GEMINI_API_KEY)
    last_error: Exception | None = None

    models: list[str] = []
    for name in GEMINI_MODELS:
        if name and name not in models:
            models.append(name)

    for model in models:
        for attempt in range(GEMINI_ATTEMPTS):
            try:
                kwargs = {"model": model, "contents": prompt}
                if config is not None:
                    kwargs["config"] = config
                response = client.models.generate_content(**kwargs)
                text = (getattr(response, "text", None) or "").strip()
                if not text and getattr(response, "candidates", None):
                    parts = response.candidates[0].content.parts or []
                    text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
                if text:
                    log(f"   . Gemini OK via {model}")
                    return text
                last_error = RuntimeError(f"{model} mengembalikan teks kosong")
            except Exception as exc:
                last_error = exc
                log(f"   ! Gemini {model} attempt {attempt + 1}: {exc}")
                if attempt < GEMINI_ATTEMPTS - 1:
                    delay = min(60, 8 * (2 ** attempt))
                    log(f"     menunggu {delay} detik sebelum mencoba lagi...")
                    time.sleep(delay)

    raise RuntimeError(f"Semua percobaan Gemini gagal: {last_error}")


def analyze_with_groq(prompt: str) -> str:
    """Groq memakai format API OpenAI, jadi cukup requests biasa."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY kosong.")

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(GROQ_ATTEMPTS):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=90)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                if text:
                    log(f"   . Groq OK via {GROQ_MODEL}")
                    return text
                last_error = RuntimeError("Groq mengembalikan teks kosong")
            else:
                last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as exc:
            last_error = exc

        log(f"   ! Groq attempt {attempt + 1}: {last_error}")
        if attempt < GROQ_ATTEMPTS - 1:
            delay = min(45, 6 * (2 ** attempt))
            log(f"     menunggu {delay} detik sebelum mencoba lagi...")
            time.sleep(delay)

    raise RuntimeError(f"Semua percobaan Groq gagal: {last_error}")


def analyze_news(prompt: str) -> str:
    """
    Coba tiap penyedia berurutan. Satu penyedia tumbang bukan alasan laporan
    harian ikut gagal -- Gemini dan Groq perusahaan dan infrastrukturnya beda.
    """
    providers = {"gemini": analyze_with_gemini, "groq": analyze_with_groq}
    available = [
        name
        for name in LLM_ORDER
        if name in providers
        and (GEMINI_API_KEY if name == "gemini" else GROQ_API_KEY)
    ]

    if not available:
        raise RuntimeError(
            "Tidak ada penyedia AI yang siap. Isi GEMINI_API_KEY atau GROQ_API_KEY."
        )

    log(f"   . Urutan penyedia: {' -> '.join(available)}")
    errors: list[str] = []

    for name in available:
        try:
            return providers[name](prompt)
        except Exception as exc:
            log(f"   ! Penyedia {name} gagal, lanjut ke berikutnya.")
            errors.append(f"{name}: {exc}")

    raise RuntimeError(" | ".join(errors))


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #

TELEGRAM_LIMIT = 4000  # batas keras Telegram 4096, sisakan ruang aman


def markdown_to_telegram_html(text: str) -> str:
    """
    parse_mode='Markdown' (legacy) adalah sumber error diam-diam: Gemini menulis
    **tebal** padahal legacy Markdown memakai *tebal*, dan tanda _ * [ ] yang
    tidak berpasangan bikin Telegram membalas 400 can't parse entities.
    Jadi: escape semuanya dulu, lalu bangun ulang tag HTML yang diizinkan.
    """
    text = html.escape(text, quote=False)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[\*\-\u2022]\s+", "• ", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    # Gemini sering memakai *satu bintang* untuk miring. Tanpa baris ini,
    # bintangnya muncul mentah di pesan Telegram.
    text = re.sub(r"\*([^*\n]{1,120}?)\*", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]{1,120})_(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`\n]{1,200})`", r"<code>\1</code>", text)
    # Sisa bintang yatim (pasangannya hilang) dibuang supaya tidak terlihat.
    text = text.replace("*", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """
    Potong di batas paragraf dulu, baru baris, baru karakter. Tujuannya supaya
    satu bagian laporan tidak pernah terbelah di tengah kalimat.
    """
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue

        flush()
        if len(block) <= limit:
            current = block
            continue

        # Satu paragraf sendirian kepanjangan: pecah per baris.
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
        flush()

    flush()
    return chunks or [text[:limit]]


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("   ! Token atau chat id Telegram kosong, pesan tidak dikirim.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True

    for index, chunk in enumerate(chunk_text(text), start=1):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
        except Exception as exc:
            log(f"   ! Telegram bagian {index} error jaringan: {exc}")
            ok = False
            continue

        if resp.status_code == 200:
            log(f"   . Telegram bagian {index} terkirim.")
            continue

        log(f"   ! Telegram bagian {index} gagal {resp.status_code}: {resp.text[:300]}")
        # Fallback: buang semua tag, kirim sebagai teks polos.
        plain = html.unescape(re.sub(r"<[^>]+>", "", chunk))
        try:
            retry = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": plain,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            if retry.status_code == 200:
                log(f"   . Telegram bagian {index} terkirim via fallback plain text.")
            else:
                log(f"   ! Fallback juga gagal: {retry.status_code} {retry.text[:200]}")
                ok = False
        except Exception as exc:
            log(f"   ! Fallback error: {exc}")
            ok = False
        time.sleep(1)

    return ok


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    now_wib = datetime.now(WIB)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    log(f"Mulai. Window berita: {LOOKBACK_HOURS} jam terakhir (cutoff {cutoff:%Y-%m-%d %H:%M} UTC)")

    log("1/4 Mengambil Google News RSS...")
    items = collect_google_news(cutoff)
    google_count = len(items)

    log("2/4 Mengambil RSS media ekonomi Indonesia...")
    items += collect_media_rss(cutoff)
    media_count = len(items) - google_count

    log("3/4 Mengambil data pasar & berita global...")
    market_rows = fetch_market_snapshot()
    items += collect_yfinance_news(cutoff)

    items = dedupe(items)
    items.sort(key=lambda it: it["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    log(
        f"Terkumpul {len(items)} headline unik "
        f"(Google News: {google_count}, media RSS: {media_count}), "
        f"{len(market_rows)} baris data pasar."
    )

    if DEBUG:
        for item in items:
            log(f"      {item['origin']} | {item['title'][:110]}")

    header = f"📊 <b>DAILY MACRO &amp; STOCK SENTIMENT REPORT</b>\n📅 {now_wib:%d %B %Y} (WIB)\n\n"

    if len(items) < MIN_ITEMS:
        log("Data terlalu sedikit. Mengirim pesan diagnostik dan keluar dengan error.")
        diag = (
            header
            + "⚠️ <b>Pengumpulan data gagal.</b>\n\n"
            + f"Hanya {len(items)} headline terkumpul (minimum {MIN_ITEMS}).\n"
            + f"Google News: {google_count} | Media RSS: {media_count}\n\n"
            + "Kemungkinan penyebab: IP runner diblokir Google News, "
            + "atau URL feed media berubah. Cek log GitHub Actions.\n\n"
            + ("<b>Data pasar masih tersedia:</b>\n" + "\n".join(market_rows) if market_rows else "")
        )
        send_telegram_message(diag)
        return 1

    log("4/4 Sintesis dengan Gemini...")
    prompt = build_prompt(
        format_for_llm(items[:MAX_ITEMS_TO_LLM]),
        "\n".join(market_rows),
        f"{now_wib:%d %B %Y}",
    )

    try:
        summary = analyze_news(prompt)
    except Exception as exc:
        log(f"Semua penyedia AI gagal: {exc}")
        fallback = header + "⚠️ Analisis AI gagal. Berikut headline mentahnya:\n\n" + "\n".join(
            f"• {item['title']} ({item['source']})" for item in items[:30]
        )
        send_telegram_message(fallback)
        return 1

    footer = (
        f"\n\n<i>Sumber: {len(items)} headline 24 jam terakhir "
        f"({google_count} Google News, {media_count} RSS media). "
        f"Bukan rekomendasi beli/jual.</i>"
    )
    message = header + markdown_to_telegram_html(summary) + footer

    log("Mengirim ke Telegram...")
    return 0 if send_telegram_message(message) else 1


if __name__ == "__main__":
    sys.exit(main())
