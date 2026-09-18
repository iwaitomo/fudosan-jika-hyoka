# -*- coding: utf-8 -*-
"""
資料PDF（登記事項証明書・固定資産評価証明書・名寄帳・公図・測量図など）を
Claude API で読み取り、対象土地の情報を構造化して返す。

- Anthropic の document（base64 PDF）入力を使う。スキャン画像PDFでもAI側で読み取る。
- 追加費用がかかる（Secrets の ANTHROPIC_API_KEY が必要）。
- 返す値は人が確認してから入力欄に反映する前提（推測での誤りは人がチェックできる）。
"""
import base64
import json
import re

# 既定モデル。Secrets の EXTRACT_MODEL で claude-sonnet-5 / claude-haiku-4-5 等に変更可。
DEFAULT_MODEL = "claude-opus-5"

EXTRACT_PROMPT = """あなたは日本の不動産登記・固定資産税・相続の実務専門家です。
添付のPDF資料（登記事項証明書、固定資産評価証明書、名寄帳、公図、地積測量図、
売買契約書、相続関係資料など）を読み取り、対象となる「土地」について
次の項目だけを JSON で返してください。読み取れない項目は null にし、推測で埋めないこと。

出力はこの JSON のみ（前後に説明文やコードフェンスを付けない）:
{
  "prefecture": "都道府県名（例: 神奈川県）",
  "city": "市区町村名。政令指定都市は必ず区まで含める（例: 横浜市神奈川区）",
  "town": "町名・丁目（例: 反町一丁目 もしくは 反町）",
  "chiban": "地番（例: 1番1 / 303番263）",
  "area_m2": 地積を平方メートルの数値で（例: 90.0）。㎡以外の単位なら㎡へ換算。不明ならnull,
  "chimoku": "地目（例: 宅地 / 山林 / 田 / 畑）",
  "front_roseka_sen": 相続税の正面路線価が読み取れれば千円/㎡の数値（例: 135）。無ければnull,
  "doc_type": "読み取った資料の種類（例: 登記事項証明書）",
  "multiple_parcels": 複数の筆が記載されていれば true、単一なら false,
  "notes": "読み取り上の注意（別筆あり／単位換算した等）。無ければ空文字"
}

ルール:
- 面積が坪・畝歩など㎡以外なら㎡へ換算し、notes に「◯坪を㎡換算」等を明記。
- 複数の土地が載っている場合は、面積が最大または筆頭の主たる1筆を対象にし、
  multiple_parcels=true とし notes にその旨を書く。
- 所在は「都道府県／市区町村／町名」に必ず分解する。政令市は city に区まで入れる。
"""


def extract_land_info(pdf_bytes: bytes, api_key: str, model: str = DEFAULT_MODEL) -> dict:
    """PDFのバイト列を Claude に渡し、土地情報の dict を返す。"""
    import anthropic  # 遅延インポート（未インストール環境でアプリ全体が落ちないように）

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        output_config={"effort": "low"},   # 抽出タスクは軽め。費用を抑える
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": b64}},
                {"type": "text", "text": EXTRACT_PROMPT},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text")
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    """モデル出力から JSON を取り出して dict に。コードフェンス等も許容。"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group())
        raise ValueError("AIの応答をJSONとして解釈できませんでした。")
