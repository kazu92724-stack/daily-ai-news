import os
import re
import time
import requests
import feedparser
from datetime import datetime

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

# --- RSS取得関数 ---
def fetch_rss_titles(query, max_items=8):
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    titles = []
    for entry in feed.entries[:max_items]:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title)
        titles.append(f"- {title}")
    return "\n".join(titles)

# --- 共通のGemini要約関数（厳格な350文字制限） ---
def generate_short_summary(prompt_text):
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- ntfy.sh 送信関数 ---
def send_notification(title, text):
    # 送信前に380文字を超えていたらカット（絶対途中で切れさせない安全網）
    if len(text) > 380:
        text = text[:360] + "\n(以下省略)"

    res = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": "default",
        },
        timeout=30,
    )
    res.raise_for_status()

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. AI：LLM・開発動向（超短文）
    try:
        print("Sending Topic 1...")
        titles = fetch_rss_titles("AI LLM 開発", max_items=6)
        prompt = f"最新のAI開発・LLMニュースの見出しです。\n前置き一切なしで、要点のみを【全角300文字以内】で簡潔に要約してください。\n\n{titles}"
        text = generate_short_summary(prompt)
        send_notification(f"🤖 AI① 開発・LLM動向 {today}", text)
        print("Topic 1 sent.")
    except Exception as e:
        print(f"Topic 1 Error: {e}")

    time.sleep(10)

    # 2. AI：ビジネス活用（超短文）
    try:
        print("Sending Topic 2...")
        titles = fetch_rss_titles("AI ビジネス 活用", max_items=6)
        prompt = f"最新のAIビジネス事例ニュースの見出しです。\n前置き一切なしで、要点のみを【全角300文字以内】で簡潔に要約してください。\n\n{titles}"
        text = generate_short_summary(prompt)
        send_notification(f"🤖 AI② ビジネス事例 {today}", text)
        print("Topic 2 sent.")
    except Exception as e:
        print(f"Topic 2 Error: {e}")

    time.sleep(10)

    # 3. 医療・ゲノム・病理（超短文）
    try:
        print("Sending Topic 3...")
        titles = fetch_rss_titles("医療 ゲノム 病理 検査", max_items=6)
        prompt = f"最新の医療・ゲノム・病理ニュースの見出しです。\n前置き一切なしで、「対象疾患・検査」と「要点」を【全角300文字以内】で要約してください。\n\n{titles}"
        text = generate_short_summary(prompt)
        send_notification(f"🏥 医療ゲノム・病理 {today}", text)
        print("Topic 3 sent.")
    except Exception as e:
        print(f"Topic 3 Error: {e}")

    time.sleep(10)

    # 4. 地域医療：和歌山・大阪南部（超短文）
    try:
        print("Sending Topic 4...")
        titles = fetch_rss_titles("和歌山 医療 OR 大阪 病院 開業", max_items=6)
        prompt = f"最新の和歌山・大阪南部の医療ニュースです。\n前置き一切なしで、施設名や地域医療の動きを【全角300文字以内】で箇条書き要約してください。\n\n{titles}"
        text = generate_short_summary(prompt)
        send_notification(f"📍 地域医療(和歌山/大阪) {today}", text)
        print("Topic 4 sent.")
    except Exception as e:
        print(f"Topic 4 Error: {e}")

if __name__ == "__main__":
    main()
