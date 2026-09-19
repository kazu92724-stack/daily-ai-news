import os
import urllib.parse
import feedparser
import requests

def get_traffic_news():
    """
    和歌山県全域 ＋ 大阪府南部（泉佐野・岸和田・関空等）の交通ニュースを
    漏れなく・ノイズなく取得する関数
    """
    traffic_articles = []

    # GoogleニュースRSS（和歌山・南大阪エリア）
    google_query = '(location:和歌山県 OR location:大阪府) (通行止め OR 事故 OR 交通規制 OR 阪和道 OR 工事 OR 火災 OR 国道26号)'
    encoded_query = urllib.parse.quote(google_query)
    google_rss_url = f'https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja'

    # Yahoo!ニュース（地域カテゴリ）
    yahoo_rss_url = 'https://news.yahoo.co.jp/rss/categories/local.xml'

    rss_urls = [google_rss_url, yahoo_rss_url]

    # 【1】カバー対象の地域・主要路線キーワード（和歌山全域 ＋ 南大阪・泉州エリア）
    target_area_keywords = [
        # 和歌山県域
        "和歌山", "海南", "岩出", "橋本", "紀の川", "かつらぎ", "有田", "御坊", "田辺", "白浜", "串本", "すさみ",
        # 大阪府南部（泉州エリア）
        "泉佐野", "岸和田", "貝塚", "泉南", "阪南", "岬町", "熊取", "田尻", "関空", "関西空港",
        # 広域主要道路
        "阪和道", "阪和自動車道", "紀勢道", "湯浅御坊道路", "京奈和", "国道26号", "国道24号", "国道170号", "国道480号"
    ]

    # 【2】交通・障害関連キーワード
    traffic_keywords = ["通行止め", "通行規制", "夜間通行", "事故", "車線規制", "工事", "国道", "バイパス", "見分", "規制", "火災"]

    # 【3】完全除外キーワード（交通に無関係なエンタメ・事件・社会問題等）
    absolute_ignore = [
        "水難", "バギー", "殺人", "逮捕", "刺さ", "米", "アンバサダー", 
        "コスモス", "クマ", "50周年", "平成22年", "クラウドファンディング", 
        "デイサービス", "盛土", "不良工事", "検査", "射撃", "訓練"
    ]

    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get('title', '')

                # 判定①: 完全除外ワードが含まれていたらスキップ
                if any(ig in title for ig in absolute_ignore):
                    continue

                # 判定②: カバー対象エリア（和歌山＋南大阪）または対象路線のキーワードが含まれているか
                if not any(area in title for area in target_area_keywords):
                    continue

                # 判定③: 交通規制・事故・火災等のキーワードが含まれているか
                if any(tk in title for tk in traffic_keywords):
                    # 重複チェック（同一URLの除外）
                    if not any(a['link'] == entry.link for a in traffic_articles):
                        traffic_articles.append({
                            'title': title,
                            'link': entry.link,
                            'published': entry.get('published', '')
                        })
        except Exception as e:
            print(f"RSS取得エラー: {e}")
            continue

    return traffic_articles

def send_to_discord(webhook_url, text):
    """
    Discordの2000文字制限を考慮し、超過した場合は1900文字単位で分割送信する関数
    """
    max_length = 1900
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        payload = {"content": chunk}
        res = requests.post(webhook_url, json=payload)
        if res.status_code not in [200, 204]:
            print(f"Discord送信エラー: {res.status_code}, {res.text}")

def main():
    articles = get_traffic_news()
    
    # 件数が多い場合は直近15件までに絞り込み
    articles = articles[:15]

    if not articles:
        print("配信対象の交通ニュースはありませんでした。")
        return

    # メッセージの組み立て
    message_content = "🚗 **【和歌山・南大阪エリア 交通・道路規制ニュース】**\n\n"
    for a in articles:
        message_content += f"・ [{a['title']}]({a['link']})\n"

    # Discordへの送信処理
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook_url:
        send_to_discord(webhook_url, message_content)
        print("Discordへの送信完了")
    else:
        print("DISCORD_WEBHOOK_URL が設定されていません。")

if __name__ == "__main__":
    main()
