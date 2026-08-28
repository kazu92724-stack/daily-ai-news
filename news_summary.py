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

# --- RSS取得ヘルパー関数 ---
def fetch_rss_titles(query, max_items=12):
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    titles = []
    for entry in feed.entries[:max_items]:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title)
        titles.append(f"- {title}")
    return "\n".join(titles)

# --- 1. AIトレンドの要約 ---
def summarize_ai_trends():
    news_titles = fetch_rss_titles("AI", max_items=12)
    prompt = (
        "以下は最新のAIニュースの見出し一覧です。\n"
        "【絶対ルール】「〜について要約します」「以下は〜」といった前置き・挨拶・導入文は一切禁止です。いきなり本文から始めてください。\n\n"
        "【対象ニュース】\n"
        f"{news_titles}\n\n"
        "【出力形式】\n"
        "1. 開発者向けツール/LLM動向 -> ビジネス活用 -> ハード・ガバナンスの順に記述。\n"
        "2. URLやリンクは含めない。\n"
        "3. 全体で400〜600字程度で簡潔・具体的にまとめる。"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 2. 医療・ゲノム・病理ニュースの要約 ---
def summarize_medical_trends():
    med_titles = fetch_rss_titles("医療 検査 ゲノム 病理", max_items=12)
    prompt = (
        "以下は最新の医療・ゲノム・病理関連ニュースの見出し一覧です。\n"
        "「どんな患者・疾患に、どの検査・手技が使われているか」「臨床・学術的変化」に重点を置いて要約してください。\n"
        "【絶対ルール】「〜について要約します」「以下は〜」といった前置き・挨拶・導入文は一切禁止です。いきなり本文から始めてください。\n\n"
        "【対象ニュース】\n"
        f"{med_titles}\n\n"
        "【出力形式】\n"
        "・ゲノム関連、検査技術、病理、AI活用に関する情報を最優先。\n"
        "・各トピックごとに【要点】【対象患者・手技】【医局への提案】の形式で記述。\n"
        "・URLやリンクは含めず、全体で500〜700字程度にまとめる。"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 3. 地域医療・ニュースの要約 ---
def summarize_local_trends():
    local_titles = fetch_rss_titles("和歌山 医療 OR 大阪 病院 開業", max_items=10)
    prompt = (
        "以下は最新の和歌山県・大阪南部の地域医療ニュースの見出し一覧です。\n"
        "【絶対ルール】「〜について要約します」「以下は〜」といった前置き・挨拶・導入文は一切禁止です。いきなり本文から始めてください。\n\n"
        "【対象ニュース】\n"
        f"{local_titles}\n\n"
        "【出力形式】\n"
        "・施設名、所在地、診療科、規模、時期などがわかる場合は明記する。\n"
        "・地域の医療提供体制の変化や新規開業・閉院の情報に絞って記述する。\n"
        "・各トピックを簡潔な箇条書きでまとめる。\n"
        "・URLやリンクは含めず、全体で300〜500字程度にまとめる。"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- ntfy.sh 送信関数 ---
def send_notification(title, text):
    # バイト数制限（添付ファイル化）を防止するため、1000文字で安全カット
    if len(text) > 1000:
        text = text[:950] + "\n\n(※文字数制限のため一部省略)"

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

    # 1. AIトピックス送信
    try:
        print("Fetching AI trends...")
        ai_summary = summarize_ai_trends()
        send_notification(f"🤖 AIトレンド要約 {today}", ai_summary)
        print("AI summary sent successfully.")
    except Exception as e:
        print(f"AI Trends Error: {e}")

    time.sleep(15)

    # 2. 医療・ゲノムトピックス送信
    try:
        print("Fetching Medical trends...")
        med_summary = summarize_medical_trends()
        send_notification(f"🏥 医療・ゲノム・病理 {today}", med_summary)
        print("Medical summary sent successfully.")
    except Exception as e:
        print(f"Medical Trends Error: {e}")

    time.sleep(15)

    # 3. 地域医療トピックス送信
    try:
        print("Fetching Local trends...")
        local_summary = summarize_local_trends()
        send_notification(f"📍 地域医療ニュース(和歌山/大阪) {today}", local_summary)
        print("Local summary sent successfully.")
    except Exception as e:
        print(f"Local Trends Error: {e}")

if __name__ == "__main__":
    main()
