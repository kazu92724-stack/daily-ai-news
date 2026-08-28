import os
import requests
from datetime import datetime

# --- GitHub Secretsから読み込む値 ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

# --- 1. AIトレンドの収集 ---
def fetch_ai_trends():
    prompt = (
        "過去24時間以内に公開されたAI（人工知能）関連の最新トレンドやニュースをGoogle検索して要約してください。\n\n"
        "【収集対象カテゴリ】\n"
        "- 最新のLLM動向や注目研究論文\n"
        "- AIビジネス活用事例\n"
        "- AI倫理、ガバナンス、規制の動向\n"
        "- AI開発者向けツール、ライブラリ\n"
        "- AIハードウェア（GPU、NPU等）の進化\n\n"
        "【出力ルール】\n"
        "1. 開発者向けツール/LLM動向 -> ビジネス活用 -> ハード・ガバナンスの優先順位で並べてください。\n"
        "2. 各ニュースには必ず参照元URLを記載してください。\n"
        "3. 全体で600〜800字程度、前置きなしで要約本文から開始してください。"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}]
    }

    res = requests.post(API_URL, json=payload, timeout=90)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# --- 2. 医療・ゲノム・病理・地域ニュースの収集 ---
def fetch_medical_trends():
    prompt = (
        "医療・検査・ゲノム・病理領域に関する全国的な最新トレンド情報、および特定地域の医療機関ニュースをGoogle検索して要約してください。\n"
        "実行時間から過去2日以内に公開された情報を優先し、診療報酬改定そのものよりも「どんな患者・疾患に、どの検査・手技が使われているか」「臨床・学術的な変化」に重点を置いてください。\n\n"
        "【必集カテゴリ（全国）】\n"
        "1. 疾患・患者層のトレンド：がん（消化器・肺・乳・婦人科等）の診療動向・治療戦略、高齢化・感染症、新診断アルゴリズムやガイドライン改訂\n"
        "2. 検査・手技・ゲノム：血液・細菌・病理・細胞診の新規/迅速検査法、ゲノム関連（コンパクトパネル、Oncomine RAS/BRAF/HER2等）の適応・検査フロー・保険適用、検体前処理や報告様式の改善事例\n"
        "3. 臨床研究・学会・論文：主要大学病院・がんセンター等の治験/レジストリ、関連学会（病理・臨床検査・腫瘍・消化器等）の抄録/声明/重要論文\n"
        "4. 現場でのAI・デジタル活用：病理AI診断、画像解析、検査オーダリング支援、検査・病理部門のワークフロー改善や医療DX\n\n"
        "【地域限定情報】\n"
        "- 和歌山県および大阪南部における医療機関ニュース（新規開業・閉院・移転・増床等）のみ収集。施設名、所在地、診療科、規模、時期を可能な限り明記。\n\n"
        "【出力形式と構成ルール】\n"
        "・ゲノム関連、検査技術、病理、AI活用に関する情報を最優先（優先度高め）にして並べてください。\n"
        "・各トピックごとに必ず以下の3点を簡潔にまとめてください：\n"
        "  1.「要点」\n"
        "  2.「対象となる患者・疾患・手技・検査」\n"
        "  3.「医局への提案に使える示唆」\n"
        "・学術情報・ニュースには必ず参照元のURL（[タイトル](URL) または末尾記載）を明記してください。\n"
        "・挨拶や前置きは不要です。"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}]
    }

    res = requests.post(API_URL, json=payload, timeout=90)
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# --- ntfy.sh 送信関数 ---
def send_notification(title, text):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": "default",
        },
        timeout=30,
    )


def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. AIトピックスの取得と通知
    try:
        ai_summary = fetch_ai_trends()
        send_notification(f"🤖 AIトレンド要約 {today}", ai_summary)
    except Exception as e:
        print(f"AI Trends Error: {e}")

    # 2. 医療・ゲノムトピックスの取得と通知
    try:
        med_summary = fetch_medical_trends()
        send_notification(f"🏥 医療・ゲノム・病理ニュース {today}", med_summary)
    except Exception as e:
        print(f"Medical Trends Error: {e}")


if __name__ == "__main__":
    main()
