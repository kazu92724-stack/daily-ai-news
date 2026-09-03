```python
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from google import genai


# ==========================================
# 0. 設定
# ==========================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 使用するモデル
# APIで利用可能なモデルに変更してください
MODEL_NAME = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash-lite"
)

# 記事取得数
AI_LIMIT = 7
MEDICAL_LIMIT = 10
LOCAL_LIMIT = 10

REQUEST_TIMEOUT = 15

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY が設定されていません。")

if not DISCORD_WEBHOOK_URL:
    raise RuntimeError("DISCORD_WEBHOOK_URL が設定されていません。")

client = genai.Client(api_key=GEMINI_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DailyNewsBot/1.0)"
}


# ==========================================
# 1. 指定サイト
# ==========================================

AI_SITES = {
    "AI Watch": "https://ai.watch.impress.co.jp/",
    "ITmedia AI+": "https://www.itmedia.co.jp/aiplus/",
}

MEDICAL_SITES = {
    "G-MED": "https://gemmed.ghc-j.com/?p=54090",
    "日経メディカル": "https://medical.nikkeibp.co.jp/leaf/all/cancernavi/news/202506/589235.html",
}

LOCAL_SITES = {
    "WBS 医療": "https://news.wbs.co.jp/category/medical",
    "AGARA 医療": "https://www.agara.co.jp/live/medical",
}


# ==========================================
# 2. 共通関数
# ==========================================

def clean_text(text):
    """HTMLタグ・余分な空白を除去"""
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_valid_url(url):
    return bool(url and url.startswith("http"))


def normalize_url(url):
    """URL末尾の不要なパラメータなどを整理"""
    if not url:
        return ""

    return url.split("#")[0].strip()


def is_blood_donation_article(title, description=""):
    """献血関連の記事を除外"""
    text = f"{title} {description}".lower()

    keywords = [
        "献血",
        "血液センター",
        "献血ルーム",
        "献血バス",
        "blood donation",
    ]

    return any(keyword.lower() in text for keyword in keywords)


def deduplicate_articles(articles):
    """URLまたはタイトルで重複除去"""
    seen_urls = set()
    seen_titles = set()
    result = []

    for article in articles:
        url = normalize_url(article.get("link", ""))
        title = clean_text(article.get("title", ""))

        if not title:
            continue

        title_key = title.lower()

        if url and url in seen_urls:
            continue

        if title_key in seen_titles:
            continue

        seen_urls.add(url)
        seen_titles.add(title_key)

        article["title"] = title
        article["link"] = url
        article["description"] = clean_text(
            article.get("description", "")
        )

        result.append(article)

    return result


def fetch_html(url):
    """HTML取得"""
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return response.text

    except Exception as e:
        print(f"HTML取得エラー: {url} / {e}")
        return ""


# ==========================================
# 3. RSS取得
# ==========================================

def fetch_rss(url, source_name, limit=10):
    """RSSから記事を取得"""
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        feed = feedparser.parse(response.content)

        articles = []

        for entry in feed.entries[:limit]:
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")

            if not title or not is_valid_url(link):
                continue

            articles.append({
                "source": source_name,
                "title": title,
                "link": normalize_url(link),
                "description": clean_text(
                    entry.get("summary", "")
                ),
            })

        return articles

    except Exception as e:
        print(f"RSS取得エラー: {url} / {e}")
        return []


# ==========================================
# 4. HTML記事取得
# ==========================================

def fetch_html_articles(url, source_name, limit=10):
    """
    指定ページから記事リンクを取得。

    サイトごとにHTML構造が異なるため、
    まずは一般的な article / h2 / h3 / a を対象にする。
    """

    html = fetch_html(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    # 記事らしいリンクを優先
    selectors = [
        "article a",
        "h2 a",
        "h3 a",
        ".article a",
        ".entry-title a",
        ".post-title a",
        "a",
    ]

    for selector in selectors:
        for a in soup.select(selector):

            title = clean_text(a.get_text(" ", strip=True))
            href = a.get("href", "")

            if not title or not href:
                continue

            link = normalize_url(urljoin(url, href))

            if not is_valid_url(link):
                continue

            # 短すぎるリンク文字列は除外
            if len(title) < 8:
                continue

            # ナビゲーション・広告などを除外
            if any(word in title for word in [
                "ログイン",
                "会員登録",
                "メニュー",
                "検索",
                "お問い合わせ",
                "サイトマップ",
            ]):
                continue

            key = (title.lower(), link)

            if key in seen:
                continue

            seen.add(key)

            articles.append({
                "source": source_name,
                "title": title,
                "link": link,
                "description": "",
            })

            if len(articles) >= limit:
                return articles

    return articles


# ==========================================
# 5. AIニュース取得
# ==========================================

def fetch_ai_news():
    """
    AI Watch と ITmedia AI+ のみ。
    各サイトから記事を取得し、重複除去。
    """

    articles = []

    for source_name, url in AI_SITES.items():
        articles.extend(
            fetch_html_articles(
                url,
                source_name,
                limit=10
            )
        )

    articles = deduplicate_articles(articles)

    return articles[:AI_LIMIT]


# ==========================================
# 6. 医療・ゲノムニュース取得
# ==========================================

def fetch_medical_news():
    """
    指定された2サイトのみ。
    """

    articles = []

    for source_name, url in MEDICAL_SITES.items():

        # 指定URLがRSSの場合はRSS、
        # それ以外はHTMLとして取得
        if "rss" in url.lower() or "feed" in url.lower():
            articles.extend(
                fetch_rss(
                    url,
                    source_name,
                    limit=10
                )
            )
        else:
            html_articles = fetch_html_articles(
                url,
                source_name,
                limit=10
            )

            # 指定ページ自体を1記事として扱う
            if not html_articles:
                articles.append({
                    "source": source_name,
                    "title": source_name,
                    "link": url,
                    "description": "",
                })
            else:
                articles.extend(html_articles)

    return deduplicate_articles(articles)[:MEDICAL_LIMIT]


# ==========================================
# 7. 地域医療ニュース取得
# ==========================================

def fetch_local_news():
    """
    WBS・AGARAの医療カテゴリのみ。
    献血関連の記事は除外。
    """

    articles = []

    for source_name, url in LOCAL_SITES.items():
        articles.extend(
            fetch_html_articles(
                url,
                source_name,
                limit=15
            )
        )

    articles = deduplicate_articles(articles)

    # 献血記事を除外
    articles = [
        article
        for article in articles
        if not is_blood_donation_article(
            article["title"],
            article["description"]
        )
    ]

    return articles[:LOCAL_LIMIT]


# ==========================================
# 8. Gemini用テキスト作成
# ==========================================

def format_articles(articles):
    if not articles:
        return "該当記事なし"

    lines = []

    for i, article in enumerate(articles, 1):
        lines.append(
            f"{i}. {article['title']}\n"
            f"   URL: {article['link']}\n"
            f"   概要: {article['description']}"
        )

    return "\n".join(lines)


# ==========================================
# 9. Gemini要約
# ==========================================

def generate_summary(ai_articles, medical_articles, local_articles):

    system_instruction = """
あなたは日本語のニュース編集者です。

以下のニュース一覧をもとに、Discord向けの短いニュース要約を作成してください。

【重要ルール】

- 提供された記事以外のニュースを追加しない。
- URLを変更しない。
- 記事の内容を推測しない。
- 記事が少ないカテゴリは無理に水増ししない。
- 前置き・挨拶・結論・重複タイトルは禁止。
- 1文字目から本文を開始する。
- Markdown形式で出力する。
- 各記事は箇条書きにする。
- 各記事の最後に必ず元記事URLを付ける。

【カテゴリ】

## 🤖 AI最新トレンド
AI Watch・ITmedia AI+の記事から重要なものを7件程度。
7件に満たない場合は存在する記事だけ。

## 🧬 医療・ゲノム
G-MED・日経メディカルの記事を要約。
検査・病理・ゲノム・がん関連を優先。

## 🗾 地域医療
WBS・AGARAの医療カテゴリの記事を要約。
和歌山県・大阪府南部に関係する話題を優先。
献血関連の記事は除外。

【出力例】

## 🤖 AI最新トレンド

- **記事タイトル**
  要約本文。
  URL: https://example.com/article

## 🧬 医療・ゲノム

- **記事タイトル**
  要約本文。
  URL: https://example.com/article

## 🗾 地域医療

- **記事タイトル**
  要約本文。
  URL: https://example.com/article
"""

    prompt = f"""
【AIニュース】
{format_articles(ai_articles)}

【医療・ゲノムニュース】
{format_articles(medical_articles)}

【地域医療ニュース】
{format_articles(local_articles)}
"""

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=f"{system_instruction}\n\n{prompt}",
        )

        if not response.text:
            raise RuntimeError("Geminiの応答が空です。")

        return response.text.strip()

    except Exception as e:
        print(f"Gemini APIエラー: {e}")
        return None


# ==========================================
# 10. APIエラー時の代替本文
# ==========================================

def create_fallback_summary(ai_articles, medical_articles, local_articles):

    lines = [
        "⚠️ Gemini APIエラーのため、記事リンク一覧を送信します。\n"
    ]

    categories = [
        ("🤖 AI最新トレンド", ai_articles),
        ("🧬 医療・ゲノム", medical_articles),
        ("🗾 地域医療", local_articles),
    ]

    for category_name, articles in categories:

        lines.append(f"## {category_name}")

        if not articles:
            lines.append("- 該当記事なし")
            continue

        for article in articles:
            lines.append(
                f"- [{article['title']}]({article['link']})"
            )

    return "\n".join(lines)


# ==========================================
# 11. Discord送信
# ==========================================

def send_to_discord(title_name, summary_text):

    if not summary_text:
        return

    # DiscordのEmbed description上限
    summary_text = summary_text[:4000]

    payload = {
        "embeds": [
            {
                "title": f"📰 {title_name}",
                "description": summary_text,
                "color": 3447003,
                "footer": {
                    "text": "Daily AI & Medical News • 自動配信"
                },
            }
        ]
    }

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code in [200, 204]:
            print("Discord送信成功")
        else:
            print(
                f"Discord送信失敗: "
                f"{response.status_code} {response.text[:200]}"
            )

    except Exception as e:
        print(f"Discord送信エラー: {e}")


# ==========================================
# 12. RSS生成
# ==========================================

def generate_rss_xml(summary_text, output_path="feed.xml"):

    now = datetime.now(timezone.utc)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = (
        "Daily Medical & AI News"
    )

    ET.SubElement(channel, "link").text = (
        "https://raw.githubusercontent.com/"
        "kazu92724-stack/daily-ai-news/main/feed.xml"
    )

    ET.SubElement(channel, "description").text = (
        "AI・医療・地域ニュースの自動要約フィード"
    )

    item = ET.SubElement(channel, "item")

    ET.SubElement(item, "title").text = (
        f"Daily News {now.strftime('%Y-%m-%d %H:%M')}"
    )

    ET.SubElement(item, "description").text = summary_text

    ET.SubElement(item, "link").text = (
        "https://raw.githubusercontent.com/"
        "kazu92724-stack/daily-ai-news/main/feed.xml"
    )

    ET.SubElement(
        item,
        "guid",
        isPermaLink="false"
    ).text = f"daily-news-{int(now.timestamp())}"

    ET.SubElement(item, "pubDate").text = (
        now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    )

    tree = ET.ElementTree(rss)

    ET.indent(tree, space="  ")

    tree.write(
        output_path,
        encoding="utf-8",
        xml_declaration=True
    )

    print(f"{output_path} の生成完了")


# ==========================================
# 13. メイン処理
# ==========================================

def main():

    print("=== ニュース収集開始 ===")

    ai_articles = fetch_ai_news()
    medical_articles = fetch_medical_news()
    local_articles = fetch_local_news()

    print(f"AIニュース: {len(ai_articles)}件")
    print(f"医療ニュース: {len(medical_articles)}件")
    print(f"地域医療: {len(local_articles)}件")

    summary_text = generate_summary(
        ai_articles,
        medical_articles,
        local_articles
    )

    if not summary_text:
        summary_text = create_fallback_summary(
            ai_articles,
            medical_articles,
            local_articles
        )

    send_to_discord(
        "Daily AI & Medical News",
        summary_text
    )

    generate_rss_xml(summary_text)


if __name__ == "__main__":
    main()
```
