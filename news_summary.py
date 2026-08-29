import os
import re
import urllib.parse
import requests
import feedparser
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

# --- RSS取得関数 ---
def fetch_rss_news(query, max_items=10):
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:max_items]:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title)
        items.append(f"- タイトル: {title}\n  URL: {entry.link}")
    return "\n".join(items)

# --- Gemini要約関数 ---
def generate_summary(prompt_text):
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}
    res = requests.post(API_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- 各カテゴリの要約生成 ---
def get_all_summaries():
    summaries = []
    
    # 1. AIトレンド
    ai_data = fetch_rss_news("AI LLM 開発", max_items=10)
    ai_prompt = (
        "以下は最新のAIニュース一覧（タイトルとURL）です。\n"
        "前置きは一切不要です。開発動向・ビジネス事例・ガバナンスに分けて、各トピックの要約と元のURLを明記してわかりやすく解説してください。\n\n"
        f"{ai_data}"
    )
    summaries.append(("🤖 AI最新トレンド", generate_summary(ai_prompt)))

    # 2. 医療・ゲノム・病理
    med_data = fetch_rss_news("医療 ゲノム 病理 検査", max_items=10)
    med_prompt = (
        "以下は最新の医療・ゲノム・病理ニュース一覧です。\n"
        "前置きは不要です。「対象疾患・検査手技」「臨床的変化」「医局・現場への提案」を含め、元URL付きで詳細に要約してください。\n\n"
        f"{med_data}"
    )
    summaries.append(("🏥 医療・ゲノム・病理ニュース", generate_summary(med_prompt)))

    # 3. 地域医療（和歌山・大阪南部）
    local_data = fetch_rss_news("和歌山 医療 OR 大阪 病院 開業", max_items=8)
    local_prompt = (
        "以下は和歌山・大阪南部の医療ニュース一覧です。\n"
        "前置きは不要です。施設名や地域の動き、新規開業等のポイントを元URL付きで簡潔にまとめてください。\n\n"
        f"{local_data}"
    )
    summaries.append(("📍 地域医療ニュース（和歌山/大阪南部）", generate_summary(local_prompt)))

    return summaries

# --- RSS (feed.xml) の生成関数 ---
def generate_rss_xml(summaries):
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = "Daily AI & Medical News Summary"
    ET.SubElement(channel, "link").text = "https://github.com"
    ET.SubElement(channel, "description").text = "Geminiによる毎日の医療・AI・地域ニュース要約フィード"
    ET.SubElement(channel, "language").text = "ja"

    for title, content in summaries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{title} ({today_str})"
        # HTML改行に変換してRSSリーダーで見やすくする
        formatted_content = content.replace("\n", "<br>")
        ET.SubElement(item, "description").text = formatted_content
        # ユニークなID（GUID）を設定
        ET.SubElement(item, "guid").text = f"{title}-{today_str}"
        ET.SubElement(item, "pubDate").text = now_rfc822

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write("feed.xml", encoding="utf-8", xml_declaration=True)
    print("feed.xml generated successfully.")

def main():
    print("Generating summaries...")
    summaries = get_all_summaries()
    generate_rss_xml(summaries)

if __name__ == "__main__":
    main()
