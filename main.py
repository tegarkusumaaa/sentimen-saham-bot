import os
import requests
import datetime
import yfinance as yf
from google import genai

# 1. Configuration & Env Variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Ticker/Komoditas/Emiten Fokus (Bisa disesuaikan)
TICKERS = ["MDKA.JK", "ANTM.JK", "TPIA.JK", "BRPT.JK", "ICBP.JK", "GC=F"] 

def fetch_yfinance_news():
    """Mengambil berita 24 jam terakhir dari yfinance"""
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_time = now - datetime.timedelta(hours=24)
    
    collected_news = []
    
    for symbol in TICKERS:
        try:
            ticker = yf.Ticker(symbol)
            news_items = ticker.news
            for item in news_items:
                pub_time = datetime.datetime.fromtimestamp(item.get('providerPublishTime', 0), tz=datetime.timezone.utc)
                if pub_time >= cutoff_time:
                    collected_news.append({
                        "ticker": symbol,
                        "title": item.get("title"),
                        "publisher": item.get("publisher"),
                        "link": item.get("link")
                    })
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            
    return collected_news

def rewrite_news_with_gemini(raw_news):
    """Menggunakan Gemini API untuk rewriting dan analisis sentimen"""
    if not raw_news:
        return "Tidak ada berita utama terkait saham/pasar finansial Indonesia dalam 24 jam terakhir."

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    news_text = "\n".join([f"- [{item['ticker']}] {item['title']} (Source: {item['publisher']})" for item in raw_news])
    
    prompt = f"""
    Kamu adalah seorang Senior Financial Market Analyst & Trader Pasar Modal Indonesia (IHSG).
    Berikut adalah kumpulan berita pasar keuangan & emiten 24 jam terakhir:

    {news_text}

    Tugasmu:
    1. Buat **Rangkuman Eksekutif Sentimen Saham & Pasar** untuk pagi ini.
    2. Kelompokkan berdasarkan **Sentimen (Bullish 🟢 / Bearish 🔴 / Neutral 🟡)** jika ada dampaknya ke IHSG atau emiten terkait.
    3. Tulis dengan gaya bahasa profesional, lugas, padat, dan mudah dipahami trader/investor (Gunakan format Markdown Telegram yang rapi).
    4. Berikan kesimpulan singkat: **Dampak Sentimen Keseluruhan bagi IHSG Hari Ini**.
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
    print("Fetching news...")
    news = fetch_yfinance_news()
    
    print(f"Found {len(news)} news items. Rewriting with Gemini...")
    summary = rewrite_news_with_gemini(news)
    
    today_str = datetime.datetime.now().strftime("%d %B %Y")
    final_message = f"📊 *DAILY MARKET & STOCK SENTIMENT REPORT*\n📅 {today_str}\n\n{summary}"
    
    print("Sending to Telegram...")
    send_telegram_message(final_message)

if __name__ == "__main__":
    main()
