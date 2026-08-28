import os
import re
import requests
import feedparser
from datetime import datetime

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

# --- 1. Google News RSSからAIトピックのニュースを取得 ---
RSS_URL = "https://news.google.com/rss/search?q=AI&hl=ja&gl=JP&ceid=JP:ja"

def fetch_headlines(max_items=10):
    feed = feedparser.parse(RSS_URL)
    headlines = []
    for entry in feed.entries[:max_items]:
        title = entry.title
        # サイト名がタイトル末尾に " - サイト名" の形でつくので軽く除去
        title = re.sub(r"\s-\s[^-]+$", "", title)
        headlines.append(title)
    return headlines

# --- 2. Gemini APIで要約 ---
def summarize(headlines):
    joined = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        "以下は本日のAI関連ニュースの見出し一覧です。\n"
        "この内容から、今日のAIトピックスの傾向を日本語で400字程度に要約してください。\n"
        "前置きや見出しの列挙はせず、要約本文のみを出力してください。\n\n"
        f"{joined}"
    )

    # 最新の gemini-3.5-flash-lite に変更
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    res = requests.post(url, json=payload, timeout=60)
    
    # エラーが発生した場合は詳細ログを出力
    if res.status_code != 200:
        print(f"API Error ({res.status_code}): {res.text}")
        res.raise_for_status()

    data = res.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 3. ntfy.sh経由でスマホに通知 ---
def send_notification(summary_text):
    today = datetime.now().strftime("%Y-%m-%d")
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=summary_text.encode("utf-8"),
        headers={
            "Title": f"AIトピックス要約 {today}".encode("utf-8"),
            "Priority": "default",
        },
        timeout=30,
    )

def main():
    headlines = fetch_headlines()
    if not headlines:
        send_notification("本日はニュースを取得できませんでした。")
        return
    summary = summarize(headlines)
    send_notification(summary)

if __name__ == "__main__":
    main()
