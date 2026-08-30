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

# --- RSS取得関数（過去2日以内＆取得件数を拡大） ---
def fetch_rss_news(query, max_items=15):
    time_bounded_query = f"{query} when:2d"
    encoded_query = urllib.parse.quote(time_bounded_query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    
    feed = feedparser.parse(url)
    items = []
    
    for entry in feed.entries[:max_items]:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title)
        pub_date = getattr(entry, "published", "日時不明")
        items.append(f"- タイトル: {title}\n  公開日時: {pub_date}\n  URL: {entry.link}")
        
    return "\n".join(items) if items else "※直近2日以内の該当ニュースは見つかりませんでした。"

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
    
    # 共通デザインプロンプト指示
    style_instruction = (
        "【重要：出力スタイルの完全指定】\n"
        "回答はすべてリッチなHTML形式のみで出力してください。\n"
        "- Markdown記法（**太字** や # 見出し、- 箇条書き など）は厳禁です。\n"
        "- デザインを視覚的（ビジュアライズ）に美しくするため、背景色つきのカード風デザインを適用してください。\n"
        "- 各トピックやニュース枠は以下のようなスタイル付きdivタグで囲んでください：\n"
        "  <div style=\"background-color: #f9f9f9; border-left: 4px solid #007bff; padding: 12px; margin-bottom: 15px; border-radius: 4px;\">\n"
        "  <h3>📌 [トピックタイトル]</h3>\n"
        "  <p>要約文章...</p>\n"
        "  <p><a href=\"URL\" target=\"_blank\" style=\"background-color: #007bff; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; display: inline-block;\">🔗 記事を読む</a></p>\n"
        "  </div>\n"
        "- 適宜 <strong> タグや <ul><li> タグ、絵文字（💡, 🏥, 🤖, 📍 など）を使って見やすく装飾してください。\n\n"
    )

    # 1. AIトレンド（取得数 15件）
    print("Generating AI summary...")
    ai_data = fetch_rss_news("AI 人工知能 (開発 OR ロボット OR 画像生成 OR エージェント)", max_items=15)
    ai_prompt = (
        f"{style_instruction}"
        "以下は直近2日以内に公開された最新のAIニュース一覧です。\n"
        "前置きは一切不要です。「開発・モデル動向」「ビジネス・産業応用」「ガバナンス・社会影響」のセクションに分け、できるだけ多くの主要記事を盛り込んでわかりやすく要約・ビジュアライズしてください。\n\n"
        f"{ai_data}"
    )
    summaries.append(("🤖 AI最新トレンド", generate_summary(ai_prompt)))
    time.sleep(5)

    # 2. 医療・ゲノム・病理（取得数 15件）
    print("Generating Medical summary...")
    med_data = fetch_rss_news("医療 ゲノム 病理 検査", max_items=15)
    med_prompt = (
        f"{style_instruction}"
        "以下は直近2日以内に公開された最新の医療・ゲノム・病理ニュース一覧です。\n"
        "前置きは不要です。「対象疾患・検査技術」「臨床現場の変化」「現場・経営への提案」を含めてカード形式でわかりやすく要約してください。\n\n"
        f"{med_data}"
    )
    summaries.append(("🏥 医療・ゲノム・病理ニュース", generate_summary(med_prompt)))
    time.sleep(5)

    # 3. 地域医療（和歌山・大阪南部 / 取得数 15件）
    print("Generating Local Medical summary...")
    local_query = (
        "医療 OR 病院 OR クリニック OR 開業 "
        "(和歌山 OR 阪南市 OR 泉南市 OR 泉南郡 OR 田尻町 OR 熊取町 OR 泉佐野市 OR 岸和田市 OR 贝塚市)"
    )
    local_data = fetch_rss_news(local_query, max_items=15)
    local_prompt = (
        f"{style_instruction}"
        "以下は直近2日以内に公開された和歌山県および大阪府南部地域（阪南市、泉南市、泉南郡、泉佐野市、岸和田市、貝塚市）の医療ニュース一覧です。\n"
        "前置きは不要です。対象市町村名や病院・クリニック名を強調（<strong>）し、カード形式で読みやすくまとめてください。\n"
        "※該当する最新ニュースがない場合は『※直近2日以内の地域医療トピックはありません』と1行で記載してください。\n\n"
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
