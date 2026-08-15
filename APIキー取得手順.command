#!/bin/bash
# APIキー取得手順のガイドをブラウザで開く（ダブルクリックで実行）
printf '\033]0;不動産時価評価システム — APIキー取得手順\007'
cd "$(dirname "$0")" || exit 1
open "README_APIキー取得手順.html"
