#!/bin/bash
# 不動産 簡易時価評価システム — 起動用（ダブルクリックで実行）
printf '\033]0;不動産時価評価システム — 起動\007'

# このスクリプトのあるフォルダへ移動
cd "$(dirname "$0")" || exit 1

echo "================================================"
echo " 不動産（土地）簡易時価評価システム"
echo "================================================"
echo ""

# Python3 の確認
if ! command -v python3 >/dev/null 2>&1; then
  echo "【エラー】Python3 が見つかりません。"
  echo "https://www.python.org/downloads/ からインストールしてください。"
  echo ""
  read -n 1 -s -r -p "何かキーを押すと閉じます"
  exit 1
fi

# 初回だけ仮想環境をつくる
if [ ! -d ".venv" ]; then
  echo "初回準備をしています（数分かかることがあります）…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
  echo "必要な部品をインストール中…"
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "準備が完了しました。"
  echo ""
fi

echo "ブラウザで画面を開きます。少しお待ちください…"
echo "（この黒い画面は、使い終わるまで閉じないでください）"
echo ""

# この端末（事務所PC）は信頼環境とみなし、合言葉なしで使える
export LOCAL_TRUSTED=1

# Streamlit 起動（ブラウザ自動オープン）
./.venv/bin/python -m streamlit run app.py --server.headless false

echo ""
read -n 1 -s -r -p "終了しました。何かキーを押すと閉じます"
