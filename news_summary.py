import os
import re
import time
import requests
from datetime import datetime

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

# --- RSS取得ヘルパー関数 ---
def fetch_rss_news(query, max_items=12):
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:max_items]:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title)
        items.append(f"- タイトル: {title}\n  URL: {entry.link}")
    return "\n".join(items)

# --- 1. AIトレンドの要約 ---
def summarize_ai_trends():
    news_data = fetch_rss_news("AI", max_items=12)
    prompt = (
        "以下は最新のAIニュース一覧（タイトルとURL）です。\n"
        "内容を整理し、以下の条件に従ってコンパクトに要約してください。\n\n"
        "【要約対象データ】\n"
        f"{news_data}\n\n"
        "【出力ルール】\n"
        "1. 開発者向けツール/LLM動向 -> ビジネス活用 -> ハード・ガバナンスの優先順位で並べてください。\n"
        "2. 各トピックには必ず該当する参照元URLを記載してください。\n"
        "3. 全体で400〜600字程度に簡潔にまとめてください（前置きは不要）。"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 2. 医療・ゲノム・地域ニュースの要約 ---
def summarize_medical_trends():
    med_data = fetch_rss_news("医療 検査 ゲノム 病理", max_items=12)
    local_data = fetch_rss_news("和歌山 医療 OR 大阪 病院 開業", max_items=8)

    prompt = (
        "以下は最新の医療・ゲノム・病理関連ニュースおよび地域医療ニュースです。\n"
        "「どんな患者・疾患に、どの検査・手技が使われているか」「臨床・学術的な変化」に重点を置いて要約してください。\n\n"
        "【全国ニュースデータ】\n"
        f"{med_data}\n\n"
        "【地域ニュースデータ】\n"
        f"{local_data}\n\n"
        "【出力形式と構成ルール】\n"
        "・ゲノム関連、検査技術、病理、AI活用に関する情報を最優先にして並べてください。\n"
        "・地域医療情報（和歌山・大阪南部）がある場合は、施設名や規模等を明記してください。\n"
        "・各トピックごとに【要点】【対象患者・手技】【医局への提案】を簡潔にまとめてください。\n"
        "・各トピックには必ず元のURLを記載し、全体で500〜700字程度に収めてください（前置き不要）。"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- ntfy.sh 送信関数（文字数オーバー時の添付ファイル化を自動防止） ---
def send_notification(title, text):
    # ntfyの文字数制限（バイト制限）対策のため、約1,200文字を超える場合はカットする
    if len(text) > 1200:
        text = text[:1150] + "\n\n(※文字数制限のため一部省略されました)"

    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": "default",
        },
        timeout=30,
    )

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. AIトピックス送信
    try:
        ai_summary = summarize_ai_trends()
        send_notification(f"🤖 AIトレンド要約 {today}", ai_summary)
        print("AI summary sent successfully.")
    except Exception as e:
        print(f"AI Trends Error: {e}")

    # レート制限回避のため少し待機
    time.sleep(5)

    # 2. 医療・ゲノムトピックス送信
    try:
        med_summary = summarize_medical_trends()
        send_notification(f"🏥 医療・ゲノム・病理ニュース {today}", med_summary)
        print("Medical summary sent successfully.")
    except Exception as e:
        print(f"Medical Trends Error: {e}")

if __name__ == "__main__":
    main()
