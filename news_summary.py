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

# クォータ上限に余裕のあるモデル
MODEL_NAME = "gemini-3.5-flash-lite"

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
        for entry in feed.entries[:8]:
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
        print("DISCORD_WEBHOOK_URLが未設定のため送信をスキップします。")
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
    ET.SubElement(channel, "link").text = "https://raw.githubusercontent.com/kazu92724-stack/daily-ai-news/main/feed.xml"
    ET.SubElement(channel, "description").text = "AI・医療・地域ニュースの自動要約フィード"

    for item_data in all_summaries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{item_data['category']} [{time_str}]"
        ET.SubElement(item, "description").text = item_data["content"]
        ET.SubElement(item, "link").text = "https://raw.githubusercontent.com/kazu92724-stack/daily-ai-news/main/feed.xml"
        ET.SubElement(item, "guid", isPermaLink="false").text = f"news-{item_data['id']}-{epoch_time}"
        ET.SubElement(item, "pubDate").text = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"[{output_path}] の生成が完了しました。")


# ==========================================
# 4. メイン処理（9月2日の高品質指示＋Discord一括転送）
# ==========================================
def main():
    print("=== 各種ニュースの収集を開始 ===")

    ai_articles = fetch_google_news("生成AI OR LLM OR 医療AI")
    medical_articles = fetch_google_news("臨床検査 OR 病理検査 OR がんゲノム検査 OR SRL OR BML OR LSIメディエンス")
    company_articles = fetch_official_company_news()
    local_articles = fetch_google_news("地域医療 OR 和歌山 医療 OR 泉佐野 医療 OR 岸和田 医療")

    combined_context = f"""
【AI最新トレンド】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in ai_articles])}

【医療・ゲノム・病理・検体検査】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in medical_articles])}

【臨床検査会社公式】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in company_articles])}

【地域医療（和歌山・大阪南部）】
{chr(10).join([f'- {a["title"]} / URL: {a["link"]}' for a in local_articles])}
"""

    # 9月2日頃のクオリティを再現するシステム指示
    system_instruction = """あなたはプロのニュース編集者です。提供されたニュースリストを基に、カテゴリごとに整理された要約を作成してください。

【各カテゴリの出力ルール】
1. 各カテゴリの見出し（例: ## 🤖 AI最新トレンド）を明記してください。
2. 重要なニュースを選定し、箇条書きで分かりやすく要約してください。
3. 記事タイトル部分には、必ず <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んでください。
4. 前置き、挨拶、二重タイトルは一切出力禁止です。1文字目から本文を開始してください。

【カテゴリ別の注意点】
- 医療・ゲノム・病理：製薬・処方薬メインのニュースではなく、検査・病理・ゲノム関連を優先してください。
- 地域医療：和歌山県および大阪府南部（泉佐野、岸和田など）の話題を重点的に扱ってください。"""

    prompt = f"以下のニュース一覧から要約を作成してください。\n\n{combined_context}"

    print(f"=== Gemini API呼び出し中 ({MODEL_NAME}) ===")
    summary_text = None

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=f"{system_instruction}\n\n{prompt}",
        )
        summary_text = response.text
        print("要約生成完了！")
    except Exception as e:
        print(f"APIエラー: {e}")
        # 万が一の失敗時のフォールバックリンク一覧
        fallback_lines = ["⚠️ API一時エラーのため、抽出記事のリンク一覧を送信します：\n"]
        for cat_name, art_list in [
            ("🤖 AI最新トレンド", ai_articles),
            ("🏥 医療・ゲノム・病理", medical_articles),
            ("🏢 臨床検査会社公式", company_articles),
            ("🗾 地域医療", local_articles),
        ]:
            if art_list:
                fallback_lines.append(f"**{cat_name}**")
                for a in art_list[:3]:
                    fallback_lines.append(f"・<a href='{a['link']}' target='_blank'>{a['title']}</a>")
        summary_text = "\n".join(fallback_lines)

    # Discord送信（Markdownハイパーリンクへ自動変換される）
    send_to_discord("Daily AI & Medical News", summary_text)

    # feed.xml 生成
    all_summaries = [{
        "id": "daily-bundle",
        "category": "総合ニュース要約",
        "content": summary_text,
    }]
    generate_rss_xml(all_summaries)


if __name__ == "__main__":
    main()
