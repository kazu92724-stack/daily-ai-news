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

# 共通ノイズ除外（株価・プレスリリース・新薬・製薬・無関係な求人を排除）
NEGATIVE_WORDS = (
    "-株価 -PRTIMES -IR -決算 -PR -看護師募集 -薬剤師求人 -医師求人 -派遣 "
    "-新薬 -製薬 -処方 -薬価 -添付文書 -自主回収"
)

# --- RSS取得関数 ---
def fetch_rss_news(query, site_list=None, max_items=20):
    site_query = ""
    if site_list:
        site_query = "(" + " OR ".join([f"site:{s}" for s in site_list]) + ")"
    
    full_query = f"{query} {site_query} {NEGATIVE_WORDS} when:2d".strip()
    encoded_query = urllib.parse.quote(full_query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    
    feed = feedparser.parse(url)
    items = []
    seen_titles = set()
    
    for entry in feed.entries:
        title = re.sub(r"\s-\s[^-]+$", "", entry.title).strip()
        
        # タイトル先頭15文字による簡易重複排除
        title_key = re.sub(r"[^\w]", "", title)[:15]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        
        pub_date = getattr(entry, "published", "日時不明")
        items.append(f"- タイトル: {title}\n  公開日時: {pub_date}\n  URL: {entry.link}")
        
        if len(items) >= max_items:
            break
            
    return "\n".join(items) if items else "※直近2日以内の該当ニュースは見つかりませんでした。"

# --- Gemini要約関数 ---
def generate_summary(prompt_text, retries=3):
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}
    for attempt in range(retries):
        try:
            res = requests.post(API_URL, json=payload, timeout=120)
            res.raise_for_status()
            return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (requests.exceptions.RequestException, requests.exceptions.HTTPError) as e:
            if attempt < retries - 1:
                print(f"API呼び出し一時エラー・タイムアウト (試行 {attempt + 1}/{retries}): {e}. 10秒後に再試行します...")
                time.sleep(10)
            else:
                raise e

# --- 各カテゴリの要約生成 ---
def get_all_summaries():
    summaries = []
    
    style_instruction = (
        "【重要：出力形式および要約ルール】\n"
        "回答はすべてシンプルかつ標準的なHTML形式のみで出力してください。\n"
        "- Markdown記法（**太字** や # 見出し、- 箇条書き など）は厳禁です。\n"
        "- style属性（colorやbackground-colorなど）は使用しないでください。\n"
        "- トピックの見出しには <h3> や <h4> を使用してください。\n"
        "- 箇条書きには <ul> と <li> を使用してください。\n"
        "- 重要語句や施設名・企業名・制度名は <strong> タグで太字にしてください。\n"
        "- 記事のリンクは <p>🔗 <a href=\"URL\" target=\"_blank\">元記事を読む</a></p> の形式に統一してください。\n"
        "- 【Duplicate Detection】複数記事が同一トピックを扱っている場合は1つにまとめて要約し、リンクを併記してください。\n"
        "- 指定条件に合致しないノイズ記事や無関係な地域の情報は出力から完全に排除してください。\n\n"
        "【構成指定】\n"
        "冒頭に必ず以下の構成で『今日の3行まとめ』と『重要度スコア』を出力してください。\n"
        "<p>⚡ <strong>今日の3行まとめ</strong><br>\n"
        "・（1点目の重要ポイント）<br>\n"
        "・（2点目の重要ポイント）<br>\n"
        "・（3点目の重要ポイント）</p>\n"
        "各トピック（<h3>）の横には、業界への影響度に応じた重要度スコア（例: 【重要度: ★★★★☆】）を付与してください。\n\n"
    )

    # 1. AIトレンド
    print("Generating AI summary...")
    ai_sites = ["itmedia.co.jp", "ledge.ai", "xtech.nikkei.com", "aipicks.jp"]
    ai_query = "AI 人工知能 (LLM OR 開発 OR ロボット OR 医療AI OR エージェント OR 生成AI)"
    ai_data = fetch_rss_news(ai_query, site_list=ai_sites, max_items=20)
    
    ai_prompt = (
        f"{style_instruction}"
        "以下は指定メディアからの最新AIニュース一覧です。\n"
        "前置きは不要です。指示通り冒頭に『今日の3行まとめ』を配置し、「LLM・基盤モデルの動向」「産業・ビジネス応用」「ガバナンス・技術動向」などに分類して要約してください。\n\n"
        f"{ai_data}"
    )
    summaries.append(("🤖 AI最新トレンド", generate_summary(ai_prompt)))
    time.sleep(5)

    # 2. 医療行政・医療DX動向
    print("Generating Medical Administration & DX summary...")
    dx_sites = ["medical.nikkeibp.co.jp", "m3.com", "carenet.com", "xtech.nikkei.com"]
    dx_query = "診療報酬 OR 厚生労働省 OR ガイドライン OR 医療DX OR 電子カルテ OR マイナ保険証 OR オンライン資格確認"
    dx_data = fetch_rss_news(dx_query, site_list=dx_sites, max_items=20)
    
    dx_prompt = (
        f"{style_instruction}"
        "以下は医療行政・診療報酬改定・医療DXに関する最新ニュース一覧です。\n"
        "前置きは不要です。指示通り冒頭に『今日の3行まとめ』を配置し、「電子カルテ・医療DX推進」「診療報酬・制度改正」「厚労省ガイドライン・通知」などに分類して要約してください。\n\n"
        f"{dx_data}"
    )
    summaries.append(("📋 医療行政・医療DX動向", generate_summary(dx_prompt)))
    time.sleep(5)

    # 3. 医療・ゲノム・病理・検体検査（7社・公式HP＋病理・細胞診・ゲノム限定）
    print("Generating Medical & Clinical Lab summary...")
    lab_sites = [
        "carenet.com", "medical.nikkeibp.co.jp", "bio.nikkeibp.co.jp", "m3.com",
        "bml.co.jp", "srl-group.co.jp", "hugp.com", "medience.co.jp",
        "falco-hd.co.jp", "falco.co.jp", "medic-grp.co.jp", "jcl.co.jp"
    ]
    lab_query = (
        "(病理検査 OR 細胞診 OR がんゲノム OR ゲノム医療 OR "
        "BML OR SRL OR HUグループ OR LSIメディエンス OR ファルコ OR メディック OR 日本臨床)"
    )
    med_data = fetch_rss_news(lab_query, site_list=lab_sites, max_items=20)
    
    med_prompt = (
        f"{style_instruction}"
        "以下は指定メディアおよび主要臨床検査会社公式サイトからの最新情報一覧です。\n"
        "【厳格ルール】\n"
        "- 対象企業: BML、SRL、HUグループ、LSIメディエンス、ファルコ、メディック、日本臨床（日本医学臨床検査研究所）\n"
        "- 対象分野: 「上記企業の経営・事業動向」「病理検査」「細胞診」「がんゲノム」のみ\n"
        "- 製薬会社、新薬、薬価、一般の処方薬関連ニュースは【完全に排除】してください。\n"
        "前置きは不要です。冒頭に『今日の3行まとめ』を配置して要約してください。\n\n"
        f"{med_data}"
    )
    summaries.append(("🏥 医療・ゲノム・病理・検体検査", generate_summary(med_prompt)))
    time.sleep(5)

    # 4. 地域医療（指定エリア限定：和歌山県／大阪府南部[阪南市・泉南市・田尻町・熊取町・泉佐野市・岸和田市・貝塚市]）
    print("Generating Local Medical summary...")
    local_query = (
        "(和歌山 OR 阪南市 OR 泉南市 OR 泉南郡 OR 田尻町 OR 熊取町 OR 泉佐野市 OR 岸和田市 OR 貝塚市) "
        "(クリニック OR 診療所 OR 医院 OR 病院) "
        "(開業 OR 開設 OR 新設 OR 移転 OR 臨床検査技師)"
    )
    local_data = fetch_rss_news(local_query, site_list=None, max_items=15)
    local_prompt = (
        f"{style_instruction}"
        "以下は直近2日以内に公開された地域医療関連ニュース一覧です。\n"
        "【絶対厳守ルール】\n"
        "1. 対象エリアは『和歌山県全域』および『大阪府（阪南市・泉南市・泉南郡・田尻町・熊取町・泉佐野市・岸和田市・貝塚市）』のみです。\n"
        "2. 上記以外の地域（大阪市内、堺市、他府県など）のニュースや求人は【絶対に掲載しないでください】。\n"
        "3. 対象エリア内における『診療所・クリニックの新規開業・開設・移転』および『臨床検査技師の募集・動向』に限定して掲載してください。\n"
        "前置きは不要です。対象自治体名や施設名を <strong> で強調してください。\n"
        "※条件に適合する最新ニュースがない場合は『※直近2日以内の対象地域（和歌山・大阪府南部指定市町）のトピックはありません』と1行で記載してください。\n\n"
        f"{local_data}"
    )
    summaries.append(("📍 地域医療ニュース（和歌山県／大阪府南部指定地域）", generate_summary(local_prompt)))

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
    ET.SubElement(channel, "description").text = "Geminiによる毎日の医療行政・電子カルテ・AI・地域ニュース要約フィード"
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
