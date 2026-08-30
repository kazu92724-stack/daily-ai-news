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

# ネガティブワード（共通ノイズ除去）
NEGATIVE_WORDS = "-株価 -PRTIMES -IR -決算 -PR"

# --- RSS取得関数（サイト指定・重複排除・過去2日以内） ---
def fetch_rss_news(query, site_list=None, max_items=20):
    # サイト指定がある場合は OR で結合
    site_query = ""
    if site_list:
        site_query = "(" + " OR ".join([f"site:{s}" for s in site_list]) + ")"
    
    # 検索クエリの組み立て (メインクエリ + サイト指定 + ネガティブワード + 期間制限)
    full_query = f"{query} {site_query} {NEGATIVE_WORDS} when:2d".strip()
    encoded_query = urllib.parse.quote(full_query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    
    feed = feedparser.parse(url)
    items = []
    seen_titles = set()
    
    for entry in feed.entries:
        # メディア名の除去とタイトルの正規化
        title = re.sub(r"\s-\s[^-]+$", "", entry.title).strip()
        
        # --- Python側 Duplicate Detection (タイトルの簡易重複排除) ---
        # タイトル先頭15文字がほぼ同じニュースは重複とみなしてスキップ
        title_key = re.sub(r"[^\w]", "", title)[:15]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        
        pub_date = getattr(entry, "published", "日時不明")
        items.append(f"- タイトル: {title}\n  公開日時: {pub_date}\n  URL: {entry.link}")
        
        if len(items) >= max_items:
            break
            
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
    
    # 共通デザイン・重複統合プロンプト指示
    style_instruction = (
        "【重要：出力スタイルおよび重複排除の指示】\n"
        "回答はすべてシンプルかつ標準的なHTML形式のみで出力してください。\n"
        "- Markdown記法（**太字** や # 見出し、- 箇条書き など）は厳禁です。\n"
        "- style属性（colorやbackground-colorなど）は使用しないでください。\n"
        "- トピックの見出しには <h3> や <h4> を使用してください。\n"
        "- 箇条書きには <ul> と <li> を使用してください。\n"
        "- 重要語句や施設名・メディア名は <strong> タグで太字にしてください。\n"
        "- 記事のリンクは <p>🔗 <a href=\"URL\" target=\"_blank\">元記事を読む</a></p> の形式に統一してください。\n"
        "- 絵文字（📌, 💡, 🏥, 🤖, 📍）を活用して視認性を高めてください。\n"
        "- 【Duplicate Detection】複数の記事が同じ話題・同一発表を扱っている場合は、必ず1つのトピックにまとめて要約し、リンクを併記してください。\n\n"
    )

    # 1. AIトレンド（指定サイト: ITmedia AI+, Ledge.ai, 日経クロステック等 ＋ 一般AI）
    print("Generating AI summary...")
    ai_sites = ["itmedia.co.jp", "ledge.ai", "xtech.nikkei.com", "aipicks.jp"]
    ai_query = "AI 人工知能 (LLM OR 開発 OR ロボット OR 医療AI OR エージェント OR 生成AI)"
    ai_data = fetch_rss_news(ai_query, site_list=ai_sites, max_items=20)
    
    ai_prompt = (
        f"{style_instruction}"
        "以下は指定メディア（ITmedia, Ledge.ai, 日経クロステック, AIPICKS等）から取得した直近2日以内の最新AIニュース一覧です。\n"
        "前置きは一切不要です。「LLM・基盤モデルの動向」「産業・ビジネス応用」「ガバナンス・技術動向」などに分け、主要記事をわかりやすく要約してください。\n\n"
        f"{ai_data}"
    )
    summaries.append(("🤖 AI最新トレンド", generate_summary(ai_prompt)))
    time.sleep(5)

    # 2. 医療・ゲノム・病理（指定サイト: CareNet, 日経メディカル, 日経バイオテク, m3等）
    print("Generating Medical summary...")
    med_sites = ["carenet.com", "medical.nikkeibp.co.jp", "bio.nikkeibp.co.jp", "m3.com"]
    med_query = "医療 OR ゲノム OR 病理 OR 検査 OR がんゲノム OR ガイドライン"
    med_data = fetch_rss_news(med_query, site_list=med_sites, max_items=20)
    
    med_prompt = (
        f"{style_instruction}"
        "以下は専門医療メディア（CareNet, 日経メディカル, 日経バイオテク, m3等）および学会・コンソーシアムの動向を含む最新医療ニュース一覧です。\n"
        "前置きは不要です。「がんゲノム・遺伝子診療」「対象疾患・新検査技術」「臨床・学会ガイドライン動向」などのカテゴリに分けて要約してください。\n\n"
        f"{med_data}"
    )
    summaries.append(("🏥 医療・ゲノム・病理ニュース", generate_summary(med_prompt)))
    time.sleep(5)

    # 3. 地域医療（和歌山県／大阪府南部地域）
    print("Generating Local Medical summary...")
    local_query = (
        "医療 OR 病院 OR クリニック OR 開業 "
        "(和歌山 OR 阪南市 OR 泉南市 OR 泉南郡 OR 田尻町 OR 熊取町 OR 泉佐野市 OR 岸和田市 OR 貝塚市)"
    )
    local_data = fetch_rss_news(local_query, site_list=None, max_items=15)
    local_prompt = (
        f"{style_instruction}"
        "以下は直近2日以内に公開された和歌山県および大阪府南部地域の医療ニュース一覧です。\n"
        "前置きは不要です。対象市町村名や病院・クリニック名を <strong> で強調し、見やすくまとめてください。\n"
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
