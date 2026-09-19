import urllib.parse
import feedparser

def get_traffic_news():
    """
    和歌山県全域（阪和道・主要国道・全自治体の事故/通行止め）のニュースを
    最小のリクエスト数で高速・確実に取得する関数
    """
    traffic_articles = []

    # 1. Googleニュース: 和歌山エリア全域の交通障害・事故・通行止めを一括取得
    # location:和歌山県 を使うことで、県内全自治体・全路線が自動的に対象になります
    google_query = 'location:和歌山県 (通行止め OR 事故 OR 交通規制 OR 阪和道 OR 工事)'
    encoded_query = urllib.parse.quote(google_query)
    google_rss_url = f'https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja'

    # 2. Yahoo!ニュース: 和歌山県の地域ニュースRSS（一般道の事故・通行止め対策）
    yahoo_rss_url = 'https://news.yahoo.co.jp/rss/categories/local.xml' # 地域ニュース枠

    rss_urls = [google_rss_url, yahoo_rss_url]

    # 交通・道路関連キーワード（フィルタリング用）
    target_keywords = ["通行止め", "事故", "規制", "工事", "阪和道", "国道", "道路", "見分"]

    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get('title', '')
                
                # タイトルに交通関連キーワードが含まれているか判定
                if any(kw in title for kw in target_keywords):
                    # 重複チェック（同一URLの除外）
                    if not any(a['link'] == entry.link for a in traffic_articles):
                        traffic_articles.append({
                            'title': title,
                            'link': entry.link,
                            'published': entry.get('published', '')
                        })
        except Exception as e:
            # 個別の通信エラーが発生しても処理全体を落とさずにスキップ
            continue

    return traffic_articles

if __name__ == "__main__":
    articles = get_traffic_news()
    print(f"取得件数: {len(articles)}件")
    for article in articles:
        print(f"- {article['title']}\n  {article['link']}")
