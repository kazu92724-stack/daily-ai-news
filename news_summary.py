#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_summary.py — daily-ai-news（日次AIニュース要約システム）

  Google News RSS → Gemini 一括要約 → Discord Webhook 通知 + feed.xml（RSS 2.0）公開

設計ポイント（備忘録の改善事項への対応）
  1. API コスト対策 : カテゴリ別の個別呼び出しを廃止し、全カテゴリを1回の Gemini 呼び出しで一括要約
  2. エラー対策     : 429(レート制限)は即停止、503 等の一時エラーは最大2回・15秒間隔でリトライ
  3. ノイズ除去     : 「単語除外」ではなくプロンプトによる文脈判断(第一段) + 正規表現による後処理(第二段)
  4. 地域フィルタ   : 和歌山県・大阪府南部のみ許可。大阪市・堺市・北摂・その他の県は物理カット
  5. Discord        : HTMLリンク→Markdownリンク変換、2000文字制限対策でカテゴリごとに分割送信
  6. feed.xml       : 長いURL文字列を排除しハイパーリンク化、[HH:MM](JST)表記+エポック基準の
                      ユニーク guid により RSS リーダーの確実な新着検知を実現
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

JST = timezone(timedelta(hours=9), name="JST")


# ============================================================
# カテゴリ定義
# ============================================================
CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "ai",
        "emoji": "🤖",
        "title": "AI最新トレンド",
        "queries": ["生成AI OR 生成系AI", "AIツール OR AIアプリ"],
        "rule": (
            "生成AI・AI関連ツール/アプリケーションのニュースのみ選ぶ。"
            "製品発表・研究・規制・ビジネス動向を含む。"
        ),
    },
    {
        "id": "medical",
        "emoji": "🏥",
        "title": "医療・ゲノム・病理",
        "queries": ["臨床検査 OR 病理検査 OR がんゲノム", "遺伝子検査 OR 診断薬 OR 病理AI"],
        "rule": (
            "臨床検査・病理検査・がんゲノム医療に関わるニュースのみ選ぶ。"
            "製薬企業の販売戦略や処方薬プロモーションがメインの記事は除外する。"
        ),
    },
    {
        "id": "regional",
        "emoji": "🗾",
        "title": "地域医療（和歌山・大阪南部）",
        "queries": [
            "和歌山 医療 OR 病院 OR クリニック",
            "泉佐野 OR 岸和田 OR 貝塚 OR 泉南 医療 OR 病院",
        ],
        "rule": (
            "和歌山県 または 大阪府南部（泉佐野・岸和田・貝塚・泉南・阪南など）の医療情報のみ選ぶ。"
            "大阪市・堺市・北摂・その他の都道府県の記事、および地域医療と無関係な記事は除外する。"
            "キーワードの単語一致ではなく、記事の文脈全体を読んで判断すること。"
        ),
    },
]

# ---- 地域フィルタ第二段（安全網）用キーワード ----
REGIONAL_INCLUDE = [
    "和歌山", "泉佐野", "岸和田", "貝塚", "泉南", "阪南", 
     "熊取", "田尻", "岬町", "和泉",
]
REGIONAL_EXCLUDE = [
    "大阪市", "堺市", "北摂", "豊中", "吹田", "高槻", "茨木", "箕面",
    "門真", "守口", "枚方", "寝屋川", "大東", "八尾", "東大阪",
    "東京都", "神奈川県", "千葉県", "埼玉県", "愛知県", "京都府", "兵庫県",
    "奈良県", "滋賀県", "三重県", "北海道", "福岡県", "広島県", "宮城県",
]

# ---- クレンジング用パターン ----
FLUFF_PATTERNS = [
    re.compile(r"^(専門アナリストとして|アナリストとして|以下、|本日の|おはようございます|こんにちは|どうも|それでは|最後に|まとめると|皆さま|みなさま).{0,40}$"),
]
DOUBLE_TITLE_PATTERN = re.compile(r"(【[^】]+】)\s*\1")
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
TAG_PATTERN = re.compile(r"<[^>]+>")
HTML_A_PATTERN = re.compile(r"<a\s+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')} JST] {msg}", flush=True)


# ============================================================
# 1. ニュース取得（Google News RSS）
# ============================================================
def google_news_url(query: str, span: str) -> str:
    q = f"{query} when:{span}"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=ja&gl=JP&ceid=JP:ja"


def parse_rss(xml_content: bytes) -> list[dict[str, Any]]:
    parsed = feedparser.parse(xml_content)
    items = []
    for entry in parsed.entries:
        title = html.unescape(getattr(entry, "title", "") or "").strip()
        link = getattr(entry, "link", "") or ""
        if not title or not link:
            continue
        pub = getattr(entry, "published_parsed", None)
        pub_dt = (
            datetime(*pub[:6], tzinfo=timezone.utc).astimezone(JST)
            if pub
            else datetime.now(JST)
        )
        items.append({"title": title, "url": link, "published": pub_dt})
    return items


def fetch_articles(query: str, span: str, max_items: int = 8) -> list[dict[str, Any]]:
    url = google_news_url(query, span)
    log(f"  RSS取得: {query} (span={span})")
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (daily-ai-news/1.0)"})
        resp.raise_for_status()
        return parse_rss(resp.content)[:max_items]
    except requests.RequestException as e:
        log(f"  ! 取得失敗: {e}")
        return []


def collect_all_news(span: str = "2d", max_per_query: int = 8) -> dict[str, list[dict[str, Any]]]:
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for cat in CATEGORIES:
        dedupe: dict[str, dict[str, Any]] = {}
        for q in cat["queries"]:
            for a in fetch_articles(q, span, max_per_query):
                dedupe.setdefault(a["url"], a)
        by_cat[cat["id"]] = list(dedupe.values())
        log(f"  {cat['emoji']} {cat['title']}: {len(by_cat[cat['id']])} 件")
    return by_cat


# ============================================================
# 2. プロンプト構築（第一段: 文脈判断によるフィルタ）
# ============================================================
def build_prompt(articles_by_cat: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "あなたは日本語ニュース要約エンジンです。次の指示に厳密に従ってください。",
        "",
        "【出力】コードフェンス・説明文なしで、以下のキーを持つJSONのみを出力すること。",
        '{"ai":[{"title":"...","url":"...","summary":"..."}],"medical":[...],"regional":[...]}',
        "",
        "【ルール】",
        "1. summaryは2文以内・60文字程度の簡潔な日本語。数値・日付は正確に保つ。",
        "2. titleは元記事タイトルをそのまま1つだけ使う。書き換え・見出し追加・二重タイトル禁止。",
        "3. 前置き・挨拶・「専門アナリストとして〜」等の飾り文・結びのコメントを一切書かない。",
        "4. 各カテゴリの選定ルールに従い、該当しない記事はJSONに含めない（単語一致ではなく文脈で判断）。",
        "5. 除外した記事のことは一切言及しない。",
        "6. urlは元記事のURLをそのまま入れる。",
        "",
    ]
    for cat in CATEGORIES:
        lines.append(f"## {cat['emoji']} {cat['title']} の選定ルール")
        lines.append(cat["rule"])
        lines.append("対象記事:")
        arts = articles_by_cat.get(cat["id"], [])
        if not arts:
            lines.append("（該当記事なし）")
        for i, a in enumerate(arts, 1):
            lines.append(f"{i}. {a['title']} | {a['url']}")
        lines.append("")
    lines.append("選定した記事のみをJSONで返してください。")
    return "\n".join(lines)


# ============================================================
# 3. Gemini API 呼び出し（1回に一括・429即停止 / 503のみ最大2回×15秒リトライ）
# ============================================================
def call_gemini(prompt: str, api_key: str, model: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("google-generativeai が未インストールです（pip install -r requirements.txt）")
    genai.configure(api_key=api_key)
    model_obj = genai.GenerativeModel(model)

    for attempt in range(3):  # 初回 + リトライ最大2回
        try:
            resp = model_obj.generate_content(prompt)
            return resp.text or ""
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 429 or "429" in str(e) or "ResourceExhausted" in type(e).__name__:
                log("!! 429 レート制限: 無駄打ちを避けるため即停止します")
                raise
            if attempt == 2:
                log(f"!! リトライ上限到達（{type(e).__name__}）: 停止します")
                raise
            log(f"! 一時エラー({type(e).__name__}): 15秒後にリトライ（{attempt + 1}/2）")
            time.sleep(15)
    raise RuntimeError("unreachable")


# ============================================================
# 4. 応答パース＆クレンジング
# ============================================================
def cleanse_text(text: str) -> str:
    if not text:
        return ""
    cleaned = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(p.search(line) for p in FLUFF_PATTERNS):  # ドヤ顔挨拶などを除去
            continue
        line = DOUBLE_TITLE_PATTERN.sub(r"\1", line)      # 二重タイトル除去
        line = TAG_PATTERN.sub("", line)                  # HTMLタグ（アンカー含む）を除去
        line = URL_PATTERN.sub("", line)                  # 生の長いURL文字列を完全排除
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned.append(line)
    return " ".join(cleaned) if cleaned else ""


def parse_gemini_response(text: str) -> dict[str, list[dict[str, Any]]]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Gemini応答からJSONを抽出できませんでした")
    data = json.loads(text[start : end + 1])
    out: dict[str, list[dict[str, Any]]] = {}
    for cat in CATEGORIES:
        items = []
        for row in data.get(cat["id"], []) or []:
            title = cleanse_text(str(row.get("title", "")))
            url = str(row.get("url", "")).strip()
            summary = cleanse_text(str(row.get("summary", "")))
            if not title or not url or not summary:
                continue
            items.append({"title": title, "url": url, "summary": summary})
        out[cat["id"]] = items
    return out


# ============================================================
# 5. 地域フィルタ第二段（後処理・安全網）
# ============================================================
def regional_post_filter(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for it in items:
        t = it["title"]
        has_exclude = any(k in t for k in REGIONAL_EXCLUDE)
        has_include = any(k in t for k in REGIONAL_INCLUDE)
        # 「◯◯県」のうち和歌山県・大阪府以外 → 物理カット
        other_pref = bool(re.search(r"[\u4e00-\u9fff]{1,6}県", t)) and "和歌山県" not in t and "大阪府" not in t
        if other_pref or (has_exclude and not has_include):
            log(f"  !! 地域フィルタで除外: {t}")
            continue
        kept.append(it)
    return kept


# ============================================================
# 6. Discord メッセージ生成＆送信
# ============================================================
def html_to_markdown(text: str) -> str:
    return HTML_A_PATTERN.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", text)


def build_discord_message(parsed: dict[str, list[dict[str, Any]]]) -> list[str]:
    messages: list[str] = []
    for cat in CATEGORIES:
        items = parsed.get(cat["id"], [])
        if not items:
            continue
        parts = [f"{cat['emoji']} **{cat['title']}**（{len(items)}件）"]
        for it in items:
            title = html_to_markdown(it["title"])
            summary = html_to_markdown(it["summary"])
            parts.append(f"• **[{title}]({it['url']})**\n  {summary}")
        messages.extend(split_discord("\n".join(parts)))
    return messages


def split_discord(msg: str, limit: int = 1900) -> list[str]:
    if len(msg) <= limit:
        return [msg]
    chunks, cur = [], ""
    for line in msg.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def post_discord(webhook_url: str, messages: list[str]) -> None:
    for m in messages:
        resp = requests.post(webhook_url, json={"content": m}, timeout=20)
        resp.raise_for_status()
        log(f"  Discord送信: {len(m)} chars")


# ============================================================
# 7. feed.xml 生成
# ============================================================
def attach_published(parsed: dict[str, list[dict[str, Any]]], articles_by_cat: dict[str, list[dict[str, Any]]]) -> None:
    index: dict[str, datetime] = {}
    for arts in articles_by_cat.values():
        for a in arts:
            index[a["url"]] = a["published"]
    for items in parsed.values():
        for it in items:
            it["published"] = index.get(it["url"], datetime.now(JST))


def build_feed_xml(
    parsed: dict[str, list[dict[str, Any]]],
    feed_title: str,
    feed_desc: str,
    feed_url: str,
) -> str:
    now = datetime.now(JST)
    items_xml: list[str] = []
    for cat in CATEGORIES:
        for idx, it in enumerate(parsed.get(cat["id"], [])):
            pub = it["published"]
            hhmm = pub.strftime("%H:%M")
            guid = f"{int(pub.timestamp())}-{cat['id']}-{idx}"  # エポックタイムスタンプ付き
            title = f"[{hhmm}] {it['title']}"
            desc = (
                f"[{hhmm}] {html.escape(it['summary'])}"
                f' — <a href="{html.escape(it["url"])}">記事を読む</a>'
            )
            items_xml.append(
                "<item>\n"
                f"  <title>{html.escape(title)}</title>\n"
                f"  <link>{html.escape(it['url'])}</link>\n"
                f'  <guid isPermaLink="false">{guid}</guid>\n'
                f"  <pubDate>{format_datetime(pub)}</pubDate>\n"
                f"  <description>{desc}</description>\n"
                "</item>"
            )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "<channel>\n"
        f"  <title>{html.escape(feed_title)}</title>\n"
        f"  <link>{html.escape(feed_url)}</link>\n"
        f"  <description>{html.escape(feed_desc)}</description>\n"
        f"  <lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        f'  <atom:link href="{html.escape(feed_url)}" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items_xml)
        + "\n</channel>\n</rss>\n"
    )


# ============================================================
# 8. モック（オフライン動作確認用）
# ============================================================
SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News (mock)</title>
<item><title>和歌山県立医大附属病院が新棟を開設 高齢者医療を拡充</title><link>https://example.com/wakayama</link><pubDate>Wed, 02 Sep 2026 23:30:00 GMT</pubDate></item>
<item><title>岸和田市民病院、外来待ち時間の短縮へ電子カルテ更新</title><link>https://example.com/kishiwada</link><pubDate>Wed, 02 Sep 2026 20:00:00 GMT</pubDate></item>
<item><title>大阪市が新たな医療連携ネットワークを発表</title><link>https://example.com/osaka-city</link><pubDate>Wed, 02 Sep 2026 15:00:00 GMT</pubDate></item>
<item><title>堺市のクリニックで誤診防止AIを導入</title><link>https://example.com/sakai</link><pubDate>Wed, 02 Sep 2026 12:00:00 GMT</pubDate></item>
<item><title>東京都ががんゲノム医療の新制度を開始</title><link>https://example.com/tokyo</link><pubDate>Wed, 02 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>生成AIベンチャーが医療文書支援ツールを正式リリース</title><link>https://example.com/ai-medical</link><pubDate>Wed, 02 Sep 2026 09:00:00 GMT</pubDate></item>
<item><title>Google、新モデルを発表 日本語対応が大幅向上</title><link>https://example.com/ai-google</link><pubDate>Wed, 02 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>病理AIスタートアップが5億円調達 がん検出精度をアピール</title><link>https://example.com/patho</link><pubDate>Wed, 02 Sep 2026 07:00:00 GMT</pubDate></item>
<item><title>新薬メーカーが自社製品の販売キャンペーンを開始</title><link>https://example.com/pharma</link><pubDate>Wed, 02 Sep 2026 06:00:00 GMT</pubDate></item>
<item><title>和歌山県が地域医療構想の改定案を公表</title><link>https://example.com/wakayama2</link><pubDate>Wed, 02 Sep 2026 05:00:00 GMT</pubDate></item>
</channel></rss>"""


def mock_collect_news() -> dict[str, list[dict[str, Any]]]:
    items = parse_rss(SAMPLE_RSS.encode("utf-8"))
    return {
        "ai": [i for i in items if "AI" in i["title"] or "生成AI" in i["title"]],
        "medical": [i for i in items if "病理" in i["title"]],
        "regional": [i for i in items if any(k in i["title"] for k in REGIONAL_INCLUDE) or "大阪市" in i["title"] or "堺市" in i["title"] or "東京都" in i["title"]],
    }


def mock_gemini(prompt: str) -> str:
    # 実運用ではプロンプトが除外するはずの「大阪市/堺市/東京都」をわざと含め、
    # 第二段（後処理）が確実に動くことを検証できるようにした固定応答
    return json.dumps(
        {
            "ai": [
                {
                    "title": "Google、新モデルを発表 日本語対応が大幅向上",
                    "url": "https://example.com/ai-google",
                    "summary": "専門アナリストとして、本日の注目ニュースをお届けします。\nGoogleが新モデルを発表し、日本語対応が大幅に向上した。",
                },
                {
                    "title": "生成AIベンチャーが医療文書支援ツールを正式リリース",
                    "url": "https://example.com/ai-medical",
                    "summary": "生成AIベンチャーが医療文書の作成・要約を支援するツールを正式リリースした。",
                },
            ],
            "medical": [
                {
                    "title": "病理AIスタートアップが5億円調達 がん検出精度をアピール",
                    "url": "https://example.com/patho",
                    "summary": "病理AIスタートアップが5億円を調達。<a href=\"https://example.com/patho-detail\">詳細はこちら</a>",
                },
            ],
            "regional": [
                {"title": "和歌山県立医大附属病院が新棟を開設 高齢者医療を拡充", "url": "https://example.com/wakayama", "summary": "和歌山県立医大附属病院が新棟を開設し、高齢者医療を拡充する。"},
                {"title": "岸和田市民病院、外来待ち時間の短縮へ電子カルテ更新", "url": "https://example.com/kishiwada", "summary": "岸和田市民病院が電子カルテを更新し、外来の待ち時間短縮を目指す。"},
                {"title": "和歌山県が地域医療構想の改定案を公表", "url": "https://example.com/wakayama2", "summary": "和歌山県が地域医療構想の改定案を公表した。"},
                {"title": "大阪市が新たな医療連携ネットワークを発表", "url": "https://example.com/osaka-city", "summary": "大阪市が新しい医療連携ネットワークを発表した。"},
                {"title": "堺市のクリニックで誤診防止AIを導入", "url": "https://example.com/sakai", "summary": "堺市のクリニックが誤診防止AIを導入した。"},
                {"title": "東京都ががんゲノム医療の新制度を開始", "url": "https://example.com/tokyo", "summary": "東京都ががんゲノム医療の新制度を始めた。"},
            ],
        }
    )


# ============================================================
# 9. main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="daily-ai-news ニュース収集・要約")
    ap.add_argument("--mock", action="store_true", help="オフライン動作確認（サンプルRSS+固定Gemini応答）")
    ap.add_argument("--no-discord", action="store_true", help="Discord送信をスキップ")
    ap.add_argument("--out", default="feed.xml", help="feed.xml出力先パス")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    span = os.environ.get("NEWS_SPAN", "2d")
    feed_title = os.environ.get("FEED_TITLE", "Daily AI News Digest（AI・医療・地域医療）")
    feed_desc = os.environ.get("FEED_DESC", "Gemini APIによる日次ニュース要約")
    feed_url = os.environ.get(
        "FEED_URL",
        "https://raw.githubusercontent.com/kazu92724-stack/daily-ai-news/main/feed.xml",
    )

    if args.mock:
        log("MOCKモード: サンプルRSS + 固定Gemini応答でパイプラインを検証します")
        articles = mock_collect_news()
        raw = mock_gemini(build_prompt(articles))
    else:
        if not api_key:
            log("GEMINI_API_KEY 未設定 → 終了")
            return 1
        if not webhook:
            log("DISCORD_WEBHOOK_URL 未設定 → 終了")
            return 1
        articles = collect_all_news(span=span)
        raw = call_gemini(build_prompt(articles), api_key, model)

    parsed = parse_gemini_response(raw)
    parsed["regional"] = regional_post_filter(parsed.get("regional", []))  # 第二段: 後処理
    attach_published(parsed, articles)

    if not args.no_discord and webhook:
        post_discord(webhook, build_discord_message(parsed))
    else:
        log("Discord送信スキップ")

    xml_text = build_feed_xml(parsed, feed_title, feed_desc, feed_url)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(xml_text)
    total = sum(len(v) for v in parsed.values())
    log(f"feed.xml 書き出し: {args.out}（全{total}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
