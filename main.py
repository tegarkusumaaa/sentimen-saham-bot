import os
import requests
import datetime
import xml.etree.ElementTree as ET
from urllib.parse import quote
import yfinance as yf
from google import genai

# 1. Configuration & Env Variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 2. Topik Kunci & Keyword Pencarian Berita Pasar Modal Indonesia
SEARCH_KEYWORDS = [
    "Suku Bunga Bank Indonesia",
    "IHSG Saham Indonesia",
    "Corporate Action Saham Dividen Right Issue",
    "Nikel Emas Batu Bara Batubara Saham",
    "Kebijakan Ekonomi OJK Kemenkeu Saham"
]

# Ticker Global & Komoditas Penting dari Yahoo Finance
GLOBAL_TICKERS = ["GC=F", "CL=F", "^JKSE"]

def fetch_google_news_rss(query):
    """Mengambil berita 24 jam terakhir dari Google News RSS dalam Bahasa Indonesia"""
    encoded_query = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}+when:1d&hl=id&gl=ID&ceid=ID:id"
    
    news_list = []
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for item in root.findall('.//item')[:4]:
                title = item.find('title').text if item.find('title') is not None else ""
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                news_list.append({
                    "source": "Google News (Media Lokal)",
                    "title": title,
                    "pubDate": pub_date
                })
    except Exception as e:
        print(f"Error fetching Google News RSS for {query}: {e}")
        
    return news_list

def fetch_yfinance_global():
    """Mengambil berita komoditas & makro global dari yfinance"""
    news_list = []
    for symbol in GLOBAL_TICKERS:
        try:
            ticker = yf.Ticker(symbol)
            for item in ticker.news[:3]:
                news_list.append({
                    "source": f"Yahoo Finance ({symbol})",
                    "title": item.get("title"),
                    "pubDate": item.get("providerPublishTime")
                })
        except Exception as e:
            print(f"Error fetching yfinance for {symbol}: {e}")
    return news_list

def rewrite_news_with_gemini(raw_news):
    """Menggunakan Gemini untuk sintesis berita makro, sektor, & emiten"""
    if not raw_news:
        return "Tidak ditemukan berita makro atau corporate action signifikan dalam 24 jam terakhir."

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    formatted_news = "\n".join([f"- [{item['source']}] {item['title']}" for item in raw_news])
    
    prompt = f"""
    Kamu adalah seorang Senior Financial Market Analyst & Chief Economist untuk Pasar Modal Indonesia (IHSG).
    Berikut adalah kumpulan berita pasar keuangan, makroekonomi, kebijakan, komoditas, dan corporate action 24 jam terakhir:

    {formatted_news}

    Tugasmu:
    1. **Ekstrak & Rangkum Sentimen Utama**: Analisis berita makro (suku bunga, kebijakan pemerintah, inflasi), berita sektor, dan corporate action penting.
    2. **Pengelompokan Berdasarkan Dampak Sektor & Emiten**:
       - Berikan kategori **Bullish 🟢**, **Bearish 🔴**, atau **Neutral 🟡**.
       - Sebutkan **Sektor Terkait** yang terpengaruh (misal: Perbankan, Properti, Tambang/Komoditas, Consumer Goods).
       - Sebutkan **Contoh Emiten Terkait** di IHSG yang berpotensi kena dampaknya (misal: BBCA, BMRI, MDKA, ANTM, CTRA, BSDE, dll).
    3. **Gaya Bahasa**: Profesional, padat, lugas, mudah dipahami trader/investor (Gunakan format Markdown Telegram yang rapi dengan bullet points & emoji).
    4. **Kesimpulan IHSG**: Berikan pandangan ringkas arah IHSG hari ini berdasarkan sentimen keseluruhan.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    
    return response.text

def send_telegram_message(text):
    """Mengirim pesan ke Telegram Group"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        print("Berhasil mengirim berita ke Telegram Group.")
    else:
        print(f"Gagal mengirim pesan: {res.status_code} - {res.text}")

def main():
    print("Fetching macro & market news...")
    all_news = []
    
    for kw in SEARCH_KEYWORDS:
        all_news.extend(fetch_google_news_rss(kw))
        
    all_news.extend(fetch_yfinance_global())
    
    print(f"Total collected news items: {len(all_news)}. Rewriting with Gemini...")
    summary = rewrite_news_with_gemini(all_news)
    
    today_str = datetime.datetime.now().strftime("%d %B %Y")
    final_message = f"📊 *DAILY MACRO & STOCK SENTIMENT REPORT*\n📅 {today_str}\n\n{summary}"
    
    print("Sending to Telegram...")
    send_telegram_message(final_message)

if __name__ == "__main__":
    main()
