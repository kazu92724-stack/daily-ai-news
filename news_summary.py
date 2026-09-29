import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from google import genai
from google.genai import types
from google.genai.errors import APIError

# ==========================================
# 0. 環境変数 & クライアント初期化
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# モデル名は環境変数で差し替え可能（廃止対策）
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not GEMINI_API_KEY:
    print("エラー: GEMINI_API_KEY が設定されていません。")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

JST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


# ==========================================
# 1. ニュース収集関数
# ==========================================
def fetch_google_news(query, timeframe="2d"):
    """Google News RSSから記事を取得（リンクはRSSのURLをそのまま使用）"""
    encoded_query = requests.utils.quote(f"({query}) when:{timeframe}")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"

    try:
        res = requests.get(rss_url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        feed = feedparser.parse(res.content)
        return [{"title": e.title, "link": e.link} for e in feed.entries[:15]]
    except Exception as e:
        print(f"Google News取得エラー ({query}): {e}")
        return []


def build_fallback_list(articles):
    """Gemini失敗時：LLMなしで記事リンク一覧を作る"""
    lines = [
        f"- <a href='{a['link']}' target='_blank'>{html.escape(a['title'])}</a>"
        for a in articles
    ]
    return "\n".join(lines)


# ==========================================
# 2. Discord送信関数
# ==========================================
def send_to_discord(category_name, summary_text):
    """HTML形式のリンクをDiscord用Markdown形式に変換して送信"""
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
    discord_text = html.unescape(discord_text)

    payload = {
        "embeds": [
            {
                "title": f"📰 {category_name}",
                "description": discord_text[:4000],
                "color": 3447003,
                "footer": {"text": "Daily AI & Regional News • 自動配信"},
            }
        ]
    }
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if res.status_code in (200, 204):
            print(f"[{category_name}] Discord送信成功")
        else:
            print(f"[{category_name}] Discord送信失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Discord送信時例外発生: {e}")


# ==========================================
# 3. feed.xml 生成関数
# ==========================================
def generate_rss_xml(all_summaries, output_path="feed.xml"):
    now = datetime.now(timezone.utc)
    time_str = now.astimezone(JST).strftime("%H:%M")
    epoch_time = int(now.timestamp())

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"Daily News [{time_str} JST]"
    ET.SubElement(channel, "link").text = "https://github.com"
    ET.SubElement(channel, "description").text = "AI・医療・地域ニュースの自動一覧フィード"

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
# 4. Gemini呼び出し（リトライ対象を限定）
# ==========================================
RETRYABLE_CODES = {429, 500, 502, 503, 504}


def call_gemini(prompt, system_instruction, max_retries=5):
    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[{GEMINI_MODEL}] API呼び出し中 (試行 {attempt}/{max_retries}) ...")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.3,
                ),
            )
            if response and response.text:
                print(f"[{GEMINI_MODEL}] 生成完了！")
                return response.text
            print("空のレスポンスでした。")
        except APIError as e:
            code = getattr(e, "code", None)
            print(f"[{GEMINI_MODEL}] APIエラー {code}: {e}")
            if code not in RETRYABLE_CODES:
                # 404(モデル廃止)・400・403などは再試行しても直らない
                return None
        except Exception as e:
            print(f"[{GEMINI_MODEL}] エラー: {e}")

        if attempt < max_retries:
            print(f"{delay}秒後に再試行します...")
            time.sleep(delay)
            delay *= 2
    return None


# ==========================================
# 5. メイン処理
# ==========================================
def main():
    ai_sites = [
        "site:news.yahoo.co.jp",
        "site:itmedia.co.jp",
        "site:ledge.ai",
        "site:cloud.watch.impress.co.jp",
        "site:prtimes.jp",
        "site:jbpress.ismedia.jp",
        "site:gihyo.jp",
        "site:k-tai.watch.impress.co.jp",
    ]
    sites_query = " OR ".join(ai_sites)
    ai_keywords = "生成AI OR LLM OR ChatGPT OR OpenAI OR Claude OR Gemini OR Perplexity OR Grok OR Qwen OR Rikyu OR AI新機能 OR AIアプデ"

    categories = [
        {
            "id": "ai",
            "name": "🤖 AI最新トレンド",
            # 修正: 元は「キーワード OR サイト」で、対象サイトの全記事が混入していた
            "query": f"({ai_keywords}) ({sites_query})",
            "timeframe": "2d",
            "system_instruction": "前置き、挨拶、要約文章、本文解説は一切出力禁止。指定されたソース・キーワードから重要度の高いAI関連記事を選び、タイトルに <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んだ箇条書きリストのみを出力してください。",
        },
        {
            "id": "medical",
            "name": "🏥 医療・ゲノム・病理・検体検査",
            "query": "臨床検査 OR 病理 OR ゲノム検査 OR 遺伝子検査 OR 血液検査 OR ゲノム医療",
            "timeframe": "2d",
            "system_instruction": """前置き、挨拶、要約文章、本文解説は一切出力禁止。
【絶対除外】新薬、薬価、処方薬、添付文書。
重要度の高いニュースを選び、タイトルに <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んだ箇条書きリストのみを出力してください。""",
        },
        {
            "id": "local",
            "name": "🗾 地域ニュース（和歌山・大阪南部）",
            "query": "和歌山 OR 阪南市 OR 泉南市 OR 泉佐野市 OR 岬町 OR 熊取町",
            "timeframe": "1d",
            "system_instruction": """前置き、挨拶、要約文章、本文解説は一切出力禁止。
【対象地域】和歌山県、阪南市、泉南市、泉佐野市、岬町、熊取町に関する地域の話題・ニュース。
重要度の高い地域ニュースを選び、タイトルに <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んだ箇条書きリストのみを出力してください。""",
        },
    ]

    all_summaries = []

    for cat in categories:
        print(f"\n=== {cat['name']} の処理開始 ===")

        articles = fetch_google_news(cat["query"], timeframe=cat.get("timeframe", "2d"))

        if not articles:
            print("該当する記事が0件のため、スキップメッセージを出力します。")
            no_news_text = "直近に該当するニュースはありませんでした。"
            all_summaries.append({"id": cat["id"], "category": cat["name"], "content": no_news_text})
            send_to_discord(cat["name"], no_news_text)
            continue

        context = "\n".join([f"- タイトル: {a['title']} / URL: {a['link']}" for a in articles])
        prompt = f"以下のニュース記事リストから対象を選び、指定ルールに従ってリンク一覧を作成してください。\n\n【記事リスト】\n{context}"

        summary_text = call_gemini(prompt, cat["system_instruction"])

        if not summary_text:
            print("Gemini失敗のため、LLMなしのリンク一覧に切り替えます。")
            summary_text = build_fallback_list(articles)

        all_summaries.append({"id": cat["id"], "category": cat["name"], "content": summary_text})
        send_to_discord(cat["name"], summary_text)

        print("API制限防止のため15秒待機中...")
        time.sleep(15)

    generate_rss_xml(all_summaries)


if __name__ == "__main__":
    main()