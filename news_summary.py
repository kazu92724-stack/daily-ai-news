import os
import requests
from datetime import datetime

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

# --- Gemini API（Google検索グラウンディング機能付き）で情報収集と要約 ---
def fetch_and_summarize_ai_trends():
    prompt = (
        "過去24時間以内に公開されたAI（人工知能）関連の最新トレンドやニュースをGoogle検索して要約してください。\n\n"
        "【収集対象のカテゴリ】\n"
        "- 最新のLLM（大規模言語モデル）の動向や注目研究論文\n"
        "- AIビジネス活用事例・導入成功例\n"
        "- AI倫理、ガバナンス、規制・法律の動向\n"
        "- AI開発者向けのツール、ライブラリ、フレームワーク\n"
        "- AIハードウェア（GPU、NPU、チップ）の進化・インフラ\n\n"
        "【出力形式とルール】\n"
        "1. 収集した情報を整理し、関心が高そうな順（1. 開発者向けツール/LLM動向 -> 2. ビジネス活用 -> 3. ハード・ガバナンス）に並べて提示してください。\n"
        "2. 各ニュース・トピックには、必ず参照元のURL（[タイトル](URL) または末尾にURL）を記載してください。\n"
        "3. 全体で800〜1000字程度で、読みやすいよう適宜改行や箇条書きを活用してください。\n"
        "4. 挨拶や前置きは省き、要約本文から開始してください。"
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    )

    # Google Search Grounding ツールを有効化
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}]
    }

    res = requests.post(url, json=payload, timeout=90)
    
    if res.status_code != 200:
        print(f"API Error ({res.status_code}): {res.text}")
        res.raise_for_status()

    data = res.json()
    
    # 生成テキストの取得
    try:
        summary_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return summary_text
    except (KeyError, IndexingError) as e:
        print(f"Response Parsing Error: {e}, Raw Data: {data}")
        raise e

# --- ntfy.sh経由でスマホに通知 ---
def send_notification(text):
    today = datetime.now().strftime("%Y-%m-%d")
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        headers={
            "Title": f"AIトレンド最新要約 {today}".encode("utf-8"),
            "Priority": "default",
        },
        timeout=30,
    )

def main():
    try:
        summary = fetch_and_summarize_ai_trends()
        send_notification(summary)
    except Exception as e:
        send_notification(f"ニュース取得中にエラーが発生しました: {e}")

if __name__ == "__main__":
    main()
