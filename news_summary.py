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
        "3. 全体で600〜800字程度で簡潔・具体的にまとめる。"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 2. 医療・ゲノム・地域ニュースの要約 ---
def summarize_medical_trends():
    med_titles = fetch_rss_titles("医療 検査 ゲノム 病理", max_items=12)
    local_titles = fetch_rss_titles("和歌山 医療 OR 大阪 病院 開業", max_items=6)

    prompt = (
        "以下は最新の医療・ゲノム・病理および地域医療ニュースの見出し一覧です。\n"
        "「どんな患者・疾患に、どの検査・手技が使われているか」「臨床・学術的変化」に重点を置いて要約してください。\n"
        "【絶対ルール】「〜について要約します」「以下は〜」といった前置き・挨拶・導入文は一切禁止です。いきなり本文から始めてください。\n\n"
        "【全国ニュース】\n"
        f"{med_titles}\n\n"
        "【地域ニュース】\n"
        f"{local_titles}\n\n"
        "【出力形式】\n"
        "・ゲノム関連、検査技術、病理、AI活用に関する情報を最優先。\n"
        "・地域医療情報（和歌山・大阪南部）がある場合は、施設名や規模等を明記。\n"
        "・各トピックごとに【要点】【対象患者・手技】【医局への提案】の形式で記述。\n"
        "・URLやリンクは含めず、全体で600〜800字程度にまとめる。"
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

    # レート制限回避のため15秒待機
    print("Waiting 15 seconds before next request...")
    time.sleep(15)

    # 2. 医療・ゲノムトピックス送信
    try:
        print("Fetching Medical trends...")
        med_summary = summarize_medical_trends()
        send_notification(f"🏥 医療・ゲノム・病理ニュース {today}", med_summary)
        print("Medical summary sent successfully.")
    except Exception as e:
        print(f"Medical Trends Error: {e}")

if __name__ == "__main__":
    main()
