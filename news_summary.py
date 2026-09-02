import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import bs4
import feedparser
from google import genai
import requests

# ==========================================
# 0. 環境変数 & クライアント初期化
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

MODEL_NAME = "gemini-3.5-flash-lite"

if not GEMINI_API_KEY:
    print("エラー: GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=GEMINI_API_KEY)

# フィルタリング用の定数（Python側で事前に絞り込む）
TARGET_COMPANIES = ["BML", "SRL", "HUグループ", "LSIメディエンス", "ファルコ", "メディック", "日本臨床"]
EXCLUDE_WORDS_MEDICAL = ["製薬", "新薬", "薬価", "処方薬", "添付文書", "ワクチン", "治験"]
LAB_KEYWORDS = ["病理検査", "細胞診", "がんゲノム", "臨床検査", "検体検査", "ゲノム医療"]

KANSAI_SOUTH_AREAS = ["和歌山", "阪南", "泉南", "田尻", "熊取", "泉佐野", "岸和田", "貝塚"]
EXCLUDE_AREAS = ["大阪市", "堺市", "北摂"]


# ==========================================
# 1. ニュース収集関数
# ==========================================
def fetch_google_news(query, pre_filter=None):
    """Google News RSSから直近2日限定(when:2d)の記事を取得し、必要ならPython側で事前フィルタリング"""
    encoded_query = requests.utils.quote(f"{query} when:2d")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        res = requests.get(rss_url, timeout=10)
        feed = feedparser.parse(res.content)
        articles = []
        for entry in feed.entries[:15]:
            title = entry.title
            if pre_filter and not pre_filter(title):
                continue
            articles.append({"title": title, "link": entry.link})
            if len(articles) >= 10:
                break
        return articles
    except Exception as e:
        print(f"Google News取得エラー ({query}): {e}")
        return []


def filter_medical(title):
    if any(w in title for w in EXCLUDE_WORDS_MEDICAL):
        return False
    has_company = any(c in title for c in TARGET_COMPANIES)
    has_kw = any(k in title for k in LAB_KEYWORDS)
    return has_company or has_kw


def filter_local(title):
    if any(a in title for a in EXCLUDE_AREAS):
        return False
    return any(a in title for a in KANSAI_SOUTH_AREAS)


def fetch_official_company_news():
    """大手臨床検査会社公式HPの直撃スクレイピング"""
    companies = [
        {"name": "SRL", "url": "https://www.srl-group.co.jp/"},
        {"name": "BML", "url": "https://www.bml.co.jp/"},
        {"name": "LSIメディエンス", "url": "https://www.medience.co.jp/"},
    ]
    official_articles = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for comp in companies:
        try:
            print(f"[{comp['name']}] 公式HP取得中...")
            res = requests.get(comp["url"], headers=headers, timeout=(3, 5))
            soup = bs4.BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True)[:3]:
                title = a.get_text(strip=True)
                if len(title) > 10:
                    href = a["href"]
                    if not href.startswith("http"):
                        href = comp["url"].rstrip("/") + "/" + href.lstrip("/")
                    official_articles.append(
                        {"title": f"[{comp['name']}] {title}", "link": href}
                    )
        except Exception as e:
            print(f"[{comp['name']}] スキップ (タイムアウト/接続失敗): {e}")
    return official_articles


# ==========================================
# 2. Discord送信関数
# ==========================================
def send_to_discord(category_name, summary_text):
    """HTML形式のリンクをDiscord用Markdown形式に自動変換して送信"""
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URLが未設定のためDiscord送信をスキップします。")
        return

    discord_text = re.sub(
        r"<a\s+[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        r"[\2](\1)",
        summary_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    discord_text = re.sub(r"</p>|<br\s*/?>", "\n", discord_text)
    discord_text = re.sub(r"<p>", "", discord_text)

    payload = {
        "embeds": [
            {
                "title": f"📰 {category_name}",
                "description": discord_text[:4000],
                "color": 3447003,
                "footer": {"text": "Daily AI & Medical News • 自動配信"},
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=10
        )
        if res.status_code in [200, 204]:
            print(f"[{category_name}] Discord送信成功")
        else:
            print(f"[{category_name}] Discord送信失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Discord送信時例外発生: {e}")


# ==========================================
# 3. feed.xml 生成関数
# ==========================================
def generate_rss_xml(all_summaries, output_path="feed.xml"):
    """feed.xmlを出力"""
    now = datetime.now(timezone.utc)
    time_str = now.strftime("%H:%M")
    epoch_time = int(now.timestamp())

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"Daily Medical & AI News [{time_str}]"
    ET.SubElement(channel, "link").text = "https://github.com"
    ET.SubElement(channel, "description").text = "AI・医療・地域ニュースの自動要約フィード"

    for item_data in all_summaries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{item_data['category']} [{time_str}]"
        ET.SubElement(item, "description").text = item_data["content"]
        ET.SubElement(item, "guid", isPermaLink="false").text = f"news-{item_data['id']}-{epoch_time}"
        ET.SubElement(item, "pubDate").text = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"[{output_path}] の生成が完了しました。")


# ==========================================
# 4. メイン処理
# ==========================================
def main():
    categories = [
        {
            "id": "ai",
            "name": "🤖 AI最新トレンド",
            "query": "生成AI OR LLM OR 医療AI",
            "extra_fetch": None,
            "pre_filter": None,
            "system_instruction": "前置き、挨拶、二重タイトルは一切出力禁止。1文字目から本文を開始すること。記事タイトルに <a href='URL' target='_blank'> のHTMLハイパーリンクを埋め込んで要約を作成してください。",
        },
        {
            "id": "medical",
            "name": "🏥 医療・ゲノム・病理・検体検査",
            "query": "臨床検査 OR 病理検査 OR がんゲノム OR ゲノム医療 OR BML OR SRL OR HUグループ",
            "extra_fetch": fetch_official_company_news,
            "pre_filter": filter_medical,
            "system_instruction": """前置き、挨拶、二重タイトルは一切出力禁止。1文字目から本文を開始すること。
【絶対除外】製薬会社、新薬、薬価、処方薬、添付文書、ワクチン、治験。
【限定ターゲット】指定7社（BML, SRL, HU, LSIメディエンス, ファルコ, メディック, 日本臨床）および臨床検査・病理関連に限定。
記事タイトルに <a href='URL' target='_blank'> のHTMLハイパーリンクを埋め込んで要約を作成してください。""",
        },
        {
            "id": "local",
            "name": "🗾 地域医療（和歌山・大阪南部）",
            "query": "和歌山 病院 OR 泉佐野 病院 OR 岸和田 医療 OR 阪南 医療 OR 泉南 医療",
            "extra_fetch": None,
            "pre_filter": filter_local,
            "system_instruction": """前置き、挨拶、二重タイトルは一切出力禁止。1文字目から本文を開始すること。
【対象エリア】和歌山県全域および大阪府南部8市町（阪南、泉南、田尻、熊取、泉佐野、岸和田、貝塚）に限定。
【絶対除外】大阪市内、堺市、北摂地域。
記事タイトルに <a href='URL' target='_blank'> のHTMLハイパーリンクを埋め込んで要約を作成してください。""",
        },
    ]

    all_summaries = []
    max_retries = 3

    for cat in categories:
        print(f"\n=== {cat['name']} の処理開始 ===")

        articles = fetch_google_news(cat["query"], pre_filter=cat["pre_filter"])
        if cat["extra_fetch"]:
            articles.extend(cat["extra_fetch"]())

        print(f"  収集記事数: {len(articles)}件")

        context = "\n".join([f"- タイトル: {a['title']} / URL: {a['link']}" for a in articles])

        if len(context) > 5000:
            print(f"データ量超過 ({len(context)}文字) のため、5000文字に制限します。")
            context = context[:5000] + "\n...（データ量超過のため省略）"

        if not articles:
            summary_text = "本日は該当条件に一致する記事が見つかりませんでした。"
        else:
            prompt = f"以下のニュース記事リストを基に、指定のルールに従って要約を作成してください。\n\n【記事リスト】\n{context}"
            summary_text = None

            for attempt in range(1, max_retries + 1):
                try:
                    print(f"[{MODEL_NAME}] API呼び出し中 (試行 {attempt}/{max_retries}) ...")
                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=f"{cat['system_instruction']}\n\n{prompt}",
                    )
                    summary_text = response.text
                    print(f"[{MODEL_NAME}] 生成完了！")
                    break
                except Exception as e:
                    err_str = str(e)
                    print(f"[{MODEL_NAME}] エラー: {e}")

                    if "PerDay" in err_str or "generate_content_free_tier_requests" in err_str:
                        print("日次クォータ超過を検知。このカテゴリはリトライせずスキップします。")
                        break

                    if attempt < max_retries:
                        wait_time = attempt * 10
                        print(f"サーバー混雑のため、{wait_time}秒後に再試行します...")
                        time.sleep(wait_time)

            if not summary_text:
                summary_text = "APIの混雑またはクォータ超過のため、要約をスキップしました。"

        all_summaries.append({
            "id": cat["id"],
            "category": cat["name"],
            "content": summary_text,
        })

        send_to_discord(cat["name"], summary_text)

        print("API制限防止のため20秒待機中...")
        time.sleep(20)

    generate_rss_xml(all_summaries)


if __name__ == "__main__":
    main()
