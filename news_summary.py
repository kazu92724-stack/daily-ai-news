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

if not GEMINI_API_KEY:
    print("エラー: GEMINI_API_KEY が設定されていません。")

client = genai.Client(api_key=GEMINI_API_KEY)


# ==========================================
# 1. 各種ニュース収集関数
# ==========================================
def fetch_google_news(query):
    """Google News RSSから直近2日限定(when:2d)の記事を取得"""
    encoded_query = requests.utils.quote(f"{query} when:2d")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        res = requests.get(rss_url, timeout=10)
        feed = feedparser.parse(res.content)
        articles = []
        for entry in feed.entries[:8]:  # 記事数を少し絞って軽量化
            articles.append({"title": entry.title, "link": entry.link})
        return articles
    except Exception as e:
        print(f"Google News取得エラー ({query}): {e}")
        return []


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
            print(f"{comp['name']} スクレイピングスキップ: {e}")
    return official_articles


# ==========================================
# 2. Discord送信関数
# ==========================================
def send_to_discord(title_name, summary_text):
    """HTML形式のリンクをDiscord用Markdown形式に自動変換して送信"""
    if not DISCORD_WEBHOOK_URL:
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
                "title": f"📰 {title_name}",
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
            print(f"[{title_name}] Discord送信成功")
        else:
            print(f"[{title_name}] Discord送信失敗: {res.status_code}")
    except Exception as e:
        print(f"Discord送信時例外発生: {e}")


# ==========================================
# 3. feed.xml 生成関数
# ==========================================
def generate_rss_xml(all_summaries, output_path="feed.xml"):
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
    print("=== 各種ニュースの収集を開始 ===")
    
    ai_articles = fetch_google_news("生成AI LLM 医療AI")
    medical_articles = fetch_google_news("臨床検査 病理 ゲノム医療")
    company_articles = fetch_official_company_news()
    local_articles = fetch_google_news("地域医療 和歌山 泉佐野 岸和田")

    combined_context = f"""
【AI最新トレンド】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in ai_articles])}

【医療・ゲノム・病理】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in medical_articles])}

【臨床検査会社公式】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in company_articles])}

【地域医療（和歌山・大阪南部）】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in local_articles])}
"""

    system_instruction = """あなたはプロのニュース編集者です。提供された4つのカテゴリのニュース記事リストを基に、それぞれのカテゴリごとに要約を作成してください。
出力形式は以下の通りに分割し、それぞれのセクションごとに見出しをつけてください。
各記事の紹介部分では、必ず <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んでください。
前置き、挨拶、二重タイトルは一切出力禁止です。"""

    prompt = f"以下のニュース全体をカテゴリ別に整理して要約してください。\n\n{combined_context}"

    print("=== Gemini APIを呼び出し中（1回のみ） ===")
    summary_text = None

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=f"{system_instruction}\n\n{prompt}",
        )
        summary_text = response.text
        print("API要約の生成に成功しました！")
    except Exception as e:
        print(f"APIエラー (上限超過/混雑): {e}")
        summary_text = "⚠️ 無料枠の制限または混雑のため要約をスキップしました。最新のリンク集をご確認ください。"

    send_to_discord("Daily AI & Medical News", summary_text)

    all_summaries = [{
        "id": "daily-bundle",
        "category": "総合ニュース要約",
        "content": summary_text,
    }]
    generate_rss_xml(all_summaries)


if __name__ == "__main__":
    main()
