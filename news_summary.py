import os
import sys
import time
import html
import urllib.parse
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
import feedparser

from google import genai

# ==========================================
# 1. 定数・設定
# ==========================================
# 無料枠で最も制限が緩く安定しているモデル
MODEL_NAME = "gemini-1.5-flash"
JST = timezone(timedelta(hours=9))

# 429/503対策
MAX_RETRIES = 5
BASE_WAIT_SECONDS = 30
WAIT_STEP_SECONDS = 15
CATEGORY_INTERVAL_SECONDS = 10

# フィルタリング定義
TARGET_COMPANIES = ["BML", "SRL", "HUグループ", "LSIメディエンス", "ファルコ", "メディック", "日本臨床"]
EXCLUDE_WORDS_MEDICAL = ["製薬", "新薬", "薬価", "処方薬", "添付文書", "ワクチン", "治験", "江東微生物"]
KANSAI_SOUTH_AREAS = ["和歌山", "阪南", "泉南", "田尻", "熊取", "泉佐野", "岸和田", "貝塚"]
EXCLUDE_AREAS = ["大阪市", "堺市", "北摂"]

CATEGORIES = [
    {
        "id": "ai",
        "name": "🤖 AI最新トレンド",
        "query": "生成AI OR LLM OR 医療AI OR ロボット",
        "prompt": "最新のAI・LLM・医療AIトレンドについて、背景情報や技術的影響を含めてわかりやすく要約・考察してください。"
    },
    {
        "id": "medical_admin",
        "name": "📋 医療行政・医療DX動向",
        "query": "診療報酬改定 OR 厚労省 OR 電子カルテ OR マイナ保険証",
        "prompt": "医療行政、診療報酬改定、マイナ保険証や医療DXの最新政策動向・議論について詳細に要約してください。"
    },
    {
        "id": "lab_testing",
        "name": "🏥 医療・ゲノム・病理・検体検査",
        "query": "病理検査 OR 細胞診 OR がんゲノム OR BML OR SRL OR HUグループ OR LSIメディエンス OR ファルコ OR メディック OR 日本臨床",
        "prompt": "検体検査、病理診、がんゲノム医療、および大手臨床検査ラボ（BML, SRL等）の最新動向を要約してください。"
    },
    {
        "id": "local_medical",
        "name": "📍 地域医療ニュース",
        "query": "和歌山 医療 OR 和歌山 病院 OR 泉佐野 病院 OR 岸和田 医療 OR 泉州 医療",
        "prompt": "和歌山県全域および大阪府南部（泉州地域等）における病院・クリニックの新規開業、移転、医療再編、求人動向などを要約してください。"
    }
]

# ==========================================
# 2. 事前フィルタリング
# ==========================================
def pre_filter_entry(entry, category_id):
    title = entry.get("title", "")

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
# 3. RSS収集 & Gemini処理
# ==========================================
def fetch_and_filter_rss(category):
    # 最新ニュース限定（直近2日）
    search_query = f"{category['query']} when:2d"
    encoded_query = urllib.parse.quote(search_query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(rss_url)

    valid_entries = []
    for entry in feed.entries:
        if pre_filter_entry(entry, category["id"]):
            valid_entries.append(entry)
            if len(valid_entries) >= 5:  # トークン消費を抑えるため上位5件に調整
                break
    return valid_entries


def summarize_with_gemini(client, category, entries):
    if not entries:
        return "直近48時間以内に該当する主要な最新ニュースはありませんでした。"

    articles_text = "\n".join(
        [f"- タイトル: {e.title}\n  URL: {e.link}" for e in entries]
    )

    prompt_text = f"""
あなたは専門のニュースアナリストです。
以下の最新記事リストをもとに、【{category['name']}】に関する高品質な要約レポートを作成してください。

【厳格ルール】
1. 「医療・ゲノム」カテゴリの場合、製薬会社・新薬・薬価・処方薬・ワクチンに関する話題が含まれていた場合は要約から完全に排除してください。
2. 「地域医療」カテゴリの場合、和歌山県全域および大阪府南部8市町（阪南・泉南・田尻・熊取・泉佐野・岸和田・貝塚）以外の話題（大阪市内・堺市・北摂等）は除外してください。
3. 各ニュース項目には、元の記事タイトルと参照URLを明記し、読みやすい構造化フォーマット（箇条書き・見出し）で出力してください。

記事リスト:
{articles_text}
"""

    for attempt in range(MAX_RETRIES):
        try:
            # 429回避のため Grounding なしの軽量リクエストを実行
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
# 4. RSS 2.0 (feed.xml) 出力生成
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

        formatted_summary = html.escape(summary).replace(chr(10), '<br>')
        description_html = f"<div><h3>{html.escape(cat_name)}</h3><div>{formatted_summary}</div></div>"
        ET.SubElement(item, "description").text = description_html

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write("feed.xml", encoding="utf-8", xml_declaration=True)
    print("feed.xml generated successfully.")

# ==========================================
# 5. メイン実行フロー
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
