import json
import os
import re
import time
from datetime import datetime, timezone
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
# 1. ニュース収集関数（直近2時間: when:2h）
# ==========================================
def fetch_google_news(query):
    """Google News RSSから直近2時間以内の記事を取得し、URLを解凍する"""
    encoded_query = requests.utils.quote(f"({query}) when:2h")
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        res = requests.get(rss_url, headers=headers, timeout=10)
        feed = feedparser.parse(res.content)
        articles = []

        for entry in feed.entries[:15]:
            raw_link = entry.link
            final_link = raw_link

            try:
                response = requests.head(
                    raw_link, headers=headers, allow_redirects=True, timeout=5
                )
                final_link = response.url
            except Exception:
                final_link = raw_link

            articles.append({"title": entry.title, "link": final_link})

        return articles
    except Exception as e:
        print(f"Google News取得エラー ({query}): {e}")
        return []


# ==========================================
# 2. Discord送信関数
# ==========================================
def send_to_discord(category_name, summary_text):
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
                "title": f"🚨 {category_name}",
                "description": discord_text[:4000],
                "color": 15158332,
                "footer": {"text": "Traffic Information • 毎時自動更新"},
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
# 3. メイン処理
# ==========================================
def main():
    # エリア：和歌山県全域（南部含む）〜大阪府南部（岸和田、貝塚、泉佐野、阪南、泉南、岬町、熊取など）
    query = "(運転見合わせ OR 遅延 OR 運行遅延 OR 通行止め OR 交通規制 OR 事故通行止め OR 交通取締 OR 取り締まり) AND (和歌山 OR 田辺 OR 新宮 OR 紀南 OR 岸和田 OR 貝塚 OR 泉佐野 OR 阪南 OR 泉南 OR 岬町 OR 熊取 OR 阪和道 OR 阪神高速 OR 京奈和 OR 湯浅御坊道路 OR JR OR 南海)"

    system_instruction = """前置き、挨拶、要約文章、本文解説は一切出力禁止。

【対象エリア】
和歌山県全域（紀南・和歌山南部を含む）〜 大阪府南部（岸和田市、貝塚市、泉佐野市、泉南市、阪南市、熊取町、岬町など）および該当エリアを通る高速道路・主要道路・鉄道路線。

【収集ルール】
1. **鉄道・公共交通（JR、南海等）**: 「運転見合わせ」および「遅延（大幅遅延・ダイヤ乱れ含む）」に関する情報のみを抽出。（※駅のイベントやその他の一般的なニュースは除外）
2. **道路情報（高速道路・一般道）**: 阪和自動車道、阪神高速、京奈和自動車道、湯浅御坊道路、主要国道の「通行止め」「事故による交通規制」「通行規制」に関する情報。
3. **交通取締**: 警察による交通取締・検挙・速度違反監視などの情報。

【出力形式】
該当する重要度の高いニュースを選び、タイトルに <a href='URL' target='_blank'>タイトル</a> のHTMLハイパーリンクを埋め込んだ箇条書きリストのみを出力してください。"""

    print("=== 道路・交通情報の処理開始 ===")
    articles = fetch_google_news(query)

    if not articles:
        print("直近2時間以内に該当する交通ニュースはありませんでした。送信をスキップします。")
        return

    context = "\n".join([f"- タイトル: {a['title']} / URL: {a['link']}" for a in articles])
    prompt = f"以下のニュース記事リストから対象を選び、指定ルールに従ってリンク一覧を作成してください。\n\n【記事リスト】\n{context}"

    summary_text = None
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[gemini-3.6-flash] API呼び出し中 (試行 {attempt}/{max_retries}) ...")
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=f"{system_instruction}\n\n{prompt}",
            )
            summary_text = response.text
            print("[gemini-3.6-flash] 生成完了！")
            break
        except Exception as e:
            print(f"[gemini-3.6-flash] エラー: {e}")
            if attempt < max_retries:
                time.sleep(10)

    if summary_text:
        send_to_discord("交通・事故・遅延・取締情報", summary_text)


if __name__ == "__main__":
    main()