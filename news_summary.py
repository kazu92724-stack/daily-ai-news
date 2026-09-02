import os
import sys
import time
import html
import re
import urllib.parse
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
import feedparser
import requests
from bs4 import BeautifulSoup

from google import genai

# ==========================================
# 1. 定数・設定
# ==========================================
MODEL_NAME = "gemini-3.6-flash"
JST = timezone(timedelta(hours=9))

# 429対策
MAX_RETRIES = 5
BASE_WAIT_SECONDS = 30
WAIT_STEP_SECONDS = 15
CATEGORY_INTERVAL_SECONDS = 15

# HTTP通信用ヘッダー（スクレイピング時のブロック防止）
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# フィルタリング定義
TARGET_COMPANIES = ["BML", "SRL", "エスアールエル", "HUグループ", "H.U.グループ", "LSIメディエンス", "ファルコ", "メディック", "日本臨床"]
EXCLUDE_WORDS_MEDICAL = ["製薬", "新薬", "薬価", "処方薬", "添付文書", "ワクチン", "治験", "江東微生物"]
KANSAI_SOUTH_AREAS = ["和歌山", "阪南", "泉南", "田尻", "熊取", "泉佐野", "岸和田", "貝塚"]
EXCLUDE_AREAS = ["大阪市", "堺市", "北摂"]

CATEGORIES = [
    {
        "id": "ai",
        "name": "🤖 AI最新トレンド",
        "query": "生成AI OR LLM OR 医療AI OR ロボット",
    },
    {
        "id": "medical_admin",
        "name": "📋 医療行政・医療DX動向",
        "query": "診療報酬改定 OR 厚労省 OR 電子カルテ OR マイナ保険証",
    },
    {
        "id": "lab_testing",
        "name": "🏥 医療・ゲノム・病理・検体検査",
        "query": "病理検査 OR 細胞診 OR がんゲノム OR BML OR SRL OR エスアールエル OR HUグループ OR LSIメディエンス OR ファルコ OR メディック OR 日本臨床",
    },
    {
        "id": "local_medical",
        "name": "📍 地域医療ニュース",
        "query": "和歌山 医療 OR 和歌山 病院 OR 泉佐野 病院 OR 岸和田 医療 OR 泉州 医療",
    }
]

# 記事データ簡易オブジェクト
class SimpleEntry:
    def __init__(self, title, link):
        self.title = title
        self.link = link

# ==========================================
# 2. 公式HPダイレクト・スクレイピング機能
# ==========================================
def fetch_official_company_news():
    """指定された公式HPのお知らせ・最新情報をダイレクトに取得"""
    official_entries = []
    now_year = datetime.now(JST).year

    # 1. SRL公式 (https://www.srl-group.co.jp/)
    try:
        url = "https://www.srl-group.co.jp/"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if len(text) >= 12 and any(k in href or k in text for k in ["news", "topics", "release", "お知らせ", "案内"]):
                    full_url = urllib.parse.urljoin(url, href)
                    official_entries.append(SimpleEntry(f"【SRL公式】{text}", full_url))
    except Exception as e:
        print(f"SRL scraping warning: {e}")

    # 2. BML公式 (https://www.bml.co.jp/news/2026/ 等)
    try:
        url = f"https://www.bml.co.jp/news/{now_year}/"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if len(text) >= 10:
                    full_url = urllib.parse.urljoin(url, href)
                    official_entries.append(SimpleEntry(f"【BML公式】{text}", full_url))
    except Exception as e:
        print(f"BML scraping warning: {e}")

    # 3. LSIメディエンス公式 (https://www.medience.co.jp/clinical/)
    try:
        url = "https://www.medience.co.jp/clinical/"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if len(text) >= 10:
                    full_url = urllib.parse.urljoin(url, href)
                    official_entries.append(SimpleEntry(f"【LSIメディエンス公式】{text}", full_url))
    except Exception as e:
        print(f"LSI Medience scraping warning: {e}")

    # 重複URLと極端に短いエントリーの排除（最大5件）
    unique_entries = []
    seen_links = set()
    for entry in official_entries:
        if entry.link not in seen_links and len(entry.title) > 12:
            seen_links.add(entry.link)
            unique_entries.append(entry)
            if len(unique_entries) >= 5:
                break

    print(f"  Scraped {len(unique_entries)} official company news entries.")
    return unique_entries

# ==========================================
# 3. 事前フィルタリング
# ==========================================
def pre_filter_entry(entry, category_id):
    title = entry.get("title", "") if isinstance(entry, dict) or hasattr(entry, "get") else getattr(entry, "title", "")

    if category_id == "lab_testing":
        if any(w in title for w in EXCLUDE_WORDS_MEDICAL):
            return False
        has_company = any(c in title for c in TARGET_COMPANIES)
        has_kw = any(k in title for k in ["病理", "細胞診", "ゲノム", "検査", "ラボ", "臨床"])
        return has_company or has_kw

    if category_id == "local_medical":
        if any(a in title for a in EXCLUDE_AREAS):
            return False
        return any(a in title for a in KANSAI_SOUTH_AREAS)

    return True

# ==========================================
# 4. RSS収集 & 公式HP情報の統合
# ==========================================
def fetch_and_filter_rss(category):
    valid_entries = []

    # 「医療・ゲノム・検体検査」カテゴリの場合は、公式HPのダイレクト情報を最初に追加
    if category["id"] == "lab_testing":
        official_news = fetch_official_company_news()
        valid_entries.extend(official_news)

    search_query = f"{category['query']} when:2d"
    encoded_query = urllib.parse.quote(search_query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(rss_url)

    for entry in feed.entries:
        if pre_filter_entry(entry, category["id"]):
            valid_entries.append(entry)
            if len(valid_entries) >= 10:
                break

    return valid_entries


def summarize_with_gemini(client, category, entries):
    if not entries:
        return "<p>直近48時間以内に該当する主要な最新ニュースはありませんでした。</p>"

    articles_text = "\n".join(
        [f"- タイトル: {e.title}\n  URL: {e.link}" for e in entries]
    )

    prompt_text = f"""
以下の記事・お知らせリストをもとに、ニュース要約を作成してください。

【厳格ルール（重複タイトルの禁止）】
1. 全体タイトル・カテゴリ名（「{category['name']}」等）・「〜要約レポート」「〜の動向」などの見出し、挨拶・前置き・後書きは一切出力禁止。
2. 1文字目から、1つ目のニュース項目の記述（例: `<b>1. 記事タイトル</b>` や `<ul>`）から直接書き始めること。
3. 各ニュース項目は HTML タグ（<b>, <ul>, <li>, <a href="..."> 等）で整形すること。
4. 長いURLの直接文字出力は絶対禁止。必ず `<a href="URL" target="_blank">記事タイトル</a>` のハイパーリンク形式で埋め込むこと。
5. 「医療・ゲノム」カテゴリ：製薬会社、新薬、薬価、処方薬、ワクチン関連の話題は要約から完全に排除すること。
6. 「地域医療」カテゴリ：和歌山県全域および大阪府南部8市町以外の話題は除外すること。

記事リスト:
{articles_text}
"""

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt_text
            )

            if getattr(response, "candidates", None):
                finish_reason = response.candidates[0].finish_reason
                print(f"  Gemini response finish_reason: {finish_reason}")

            if response.text:
                return response.text

        except Exception as e:
            err_str = str(e)
            print(f"Gemini API Error ({category['id']}) Attempt {attempt + 1}: {type(e).__name__} - {e}")

            if "429" in err_str:
                wait_time = BASE_WAIT_SECONDS + (attempt * WAIT_STEP_SECONDS)
                print(f"Waiting {wait_time} seconds due to 429 rate limit...")
                time.sleep(wait_time)
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                wait_time = 15 + (attempt * 10)
                print(f"Waiting {wait_time} seconds due to 503 server overload...")
                time.sleep(wait_time)
            else:
                raise e

    raise RuntimeError(
        f"Failed to generate summary for category '{category['id']}' after {MAX_RETRIES} attempts."
    )

# ==========================================
# 5. RSS 2.0 (feed.xml) 出力生成
# ==========================================
def generate_rss_xml(results):
    now = datetime.now(JST)
    time_prefix = now.strftime("[%H:%M]")
    epoch_time = int(now.timestamp())

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = f"Daily AI & Medical News Summary ({now.strftime('%Y-%m-%d')})"
    ET.SubElement(channel, "link").text = "https://github.com/kazu92724-stack/daily-ai-news"
    ET.SubElement(channel, "description").text = "Gemini APIを活用した日次医療・AIニュース自動要約"
    ET.SubElement(channel, "language").text = "ja"
    ET.SubElement(channel, "lastBuildDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0900")

    for cat_id, cat_name, summary in results:
        item = ET.SubElement(channel, "item")
        
        ET.SubElement(item, "title").text = f"{time_prefix} {cat_name} ({now.strftime('%Y-%m-%d')})"

        clean_link = f"https://github.com/kazu92724-stack/daily-ai-news#{cat_id}_{epoch_time}"
        ET.SubElement(item, "link").text = clean_link

        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = f"daily-ai-news-{cat_id}-{epoch_time}"

        ET.SubElement(item, "pubDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0900")

        # 不要な構造・冗長タイトルの削除クレンジング
        clean_summary = summary.replace("```html", "").replace("```", "").strip()
        clean_summary = re.sub(r'^(#+|\b' + re.escape(cat_name) + r'\b.*?$)', '', clean_summary, flags=re.MULTILINE).strip()

        formatted_summary = clean_summary.replace("\n", "<br>")
        description_html = f"<div>{formatted_summary}</div>"
        ET.SubElement(item, "description").text = description_html

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write("feed.xml", encoding="utf-8", xml_declaration=True)
    print("feed.xml generated successfully.")

# ==========================================
# 6. メイン実行フロー
# ==========================================
def main():
    print("Starting news summary workflow...")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("CRITICAL ERROR: GEMINI_API_KEY is missing in environment variables.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    results = []

    for i, cat in enumerate(CATEGORIES):
        print(f"Processing: {cat['name']}...")
        entries = fetch_and_filter_rss(cat)
        print(f"  Found {len(entries)} valid articles after filtering.")

        summary = summarize_with_gemini(client, cat, entries)
        results.append((cat["id"], cat["name"], summary))

        if i < len(CATEGORIES) - 1:
            print(f"  Sleeping {CATEGORY_INTERVAL_SECONDS}s before next category...")
            time.sleep(CATEGORY_INTERVAL_SECONDS)

    generate_rss_xml(results)
    print("Workflow completed successfully.")


if __name__ == "__main__":
    main()
