import os
import re
import time
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

# --- Gemini要約関数（503エラー時の自動再試行つき） ---
def generate_summary(prompt_text, retries=3):
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}
    for attempt in range(retries):
        try:
            res = requests.post(API_URL, json=payload, timeout=60)
            res.raise_for_status()
            return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except requests.exceptions.HTTPError as e:
            if attempt < retries - 1:
                print(f"API呼び出し一時エラー (試行 {attempt + 1}/{retries}): {e}. 10秒後に再試行します...")
                time.sleep(10)
            else:
                raise e

# --- 各カテゴリの要約生成 ---
def get_all_summaries():
    summaries = []
    
    # 1. AIトレンド
    print("Generating AI summary...")
    ai_data = fetch_rss_news("AI 人工知能 (開発 OR ロボット OR 画像生成 OR エージェント)", max_items=10)
    ai_prompt = (
        "以下は最新のAIニュース一覧（タイトルとURL）です。\n"
        "前置きは一切不要です。開発動向・ビジネス事例・ガバナンスに分けて要約してください。\n\n"
        "【重要】出力は必ずリッチなHTML形式で記述してください。\n"
        "- 見出しには <h3> や <h4> を使用する\n"
        "- 箇条書きには <ul> と <li> を使用する\n"
        "- 強調したいキーワードは <strong> を使用する\n"
        "- URLは必ず <a href=\"URL\" target=\"_blank\">記事を読む</a> のようなクリッカブルリンクにする\n"
        "- Markdown記法（**太字** や [リンク](URL) など）は絶対に含めないこと\n\n"
        f"{ai_data}"
    )
    summaries.append(("🤖 AI最新トレンド", generate_summary(ai_prompt)))
    time.sleep(5)

    # 2. 医療・ゲノム・病理
    print("Generating Medical summary...")
    med_data = fetch_rss_news("医療 ゲノム 病理 検査", max_items=10)
    med_prompt = (
        "以下は最新の医療・ゲノム・病理ニュース一覧です。\n"
        "前置きは不要です。「対象疾患・検査手技」「臨床的変化」「医局・現場への提案」を含めて詳細に要約してください。\n\n"
        "【重要】出力は必ずリッチなHTML形式で記述してください。\n"
        "- 見出しには <h3> や <h4> を使用する\n"
        "- 箇条書きには <ul> と <li> を使用する\n"
        "- 強調したいキーワードは <strong> を使用する\n"
        "- URLは必ず <a href=\"URL\" target=\"_blank\">記事を読む</a> のようなクリッカブルリンクにする\n"
        "- Markdown記法は絶対に使用しないこと\n\n"
        f"{med_data}"
    )
    summaries.append(("🏥 医療・ゲノム・病理ニュース", generate_summary(med_prompt)))
    time.sleep(5)

    # 3. 地域医療（和歌山県・大阪府泉州地域/阪南・泉南・田尻・熊取・泉佐野・岸和田・貝塚）
    print("Generating Local Medical summary...")
    local_query = (
        "医療 OR 病院 OR クリニック OR 開業 "
        "(和歌山 OR 阪南市 OR 泉南市 OR 泉南郡 OR 田尻町 OR 熊取町 OR 泉佐野市 OR 岸和田市 OR 貝塚市)"
    )
    local_data = fetch_rss_news(local_query, max_items=12)
    local_prompt = (
        "以下は和歌山県および大阪府南部地域（阪南市、泉南市、泉南郡、泉佐野市、岸和田市、貝塚市）の医療関連ニュース一覧です。\n"
        "前置きは不要です。対象自治体や医療機関・施設の名称、新規開業・医療連携・地域医療の動向などのポイントを簡潔にまとめてください。\n\n"
        "【重要】出力は必ずリッチなHTML形式で記述してください。\n"
        "- 見出しには <h3> や <h4> を使用する\n"
        "- 箇条書きには <ul> と <li> を使用する\n"
        "- 強調したいキーワード（市町村名や病院名）は <strong> を使用する\n"
        "- URLは必ず <a href=\"URL\" target=\"_blank\">記事を読む</a> のようなクリッカブルリンクにする\n"
        "- Markdown記法は絶対に使用しないこと\n\n"
        f"{local_data}"
    )
    summaries.append(("📍 地域医療ニュース（和歌山県／大阪府南部地域）", generate_summary(local_prompt)))

    return summaries

# --- RSS (feed.xml) の生成関数 ---
def generate_rss_xml(summaries):
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    now_time_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = "Daily AI & Medical News Summary"
    ET.SubElement(channel, "link").text = "https://kazu92724-stack.github.io/daily-ai-news/"
    ET.SubElement(channel, "description").text = "Geminiによる毎日の医療・AI・地域ニュース要約フィード"
    ET.SubElement(channel, "language").text = "ja"

    for title, content in summaries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{title} ({today_str})"
        ET.SubElement(item, "description").text = content
        ET.SubElement(item, "guid").text = f"{title}-{now_time_str}"
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
