# -*- coding: utf-8 -*-
"""簡易価格算定のロジック（要件定義 8〜13章）。"""
import re
import statistics

TSUBO = 3.305785  # 1坪 = 3.305785 ㎡

# よくある補正項目と、目安の補正率(%)。プラスは増価、マイナスは減価。
# 実務でよく使う土地の画地条件・環境要因をまとめたもの（あくまで目安。個別に調整可）。
PRESET_CORRECTIONS = [
    # --- 増価要因（プラス） ---
    ("＜増価要因＞", None),
    ("角地", 3.0),
    ("準角地", 2.0),
    ("二方路（前後に道路）", 2.0),
    ("三方路・四方路", 4.0),
    ("整形地・間口良好", 2.0),
    ("幹線道路沿い・接道良好", 3.0),
    ("南向き・日照良好", 2.0),
    # --- 減価要因（マイナス） ---
    ("＜減価要因＞", None),
    ("不整形地", -10.0),
    ("間口狭小", -5.0),
    ("奥行長大", -5.0),
    ("袋地・旗竿地", -10.0),
    ("私道負担あり", -10.0),
    ("がけ地・傾斜地", -15.0),
    ("高低差あり（道路との段差）", -8.0),
    ("再建築不可（接道義務未充足）", -30.0),
    ("無道路地", -35.0),
    ("都市計画道路予定地", -10.0),
    ("高圧線下地", -10.0),
    ("騒音・振動・悪臭など環境不良", -5.0),
    ("北向き・日照不良", -3.0),
    ("借地権・底地など権利制限", -20.0),
]

# 見出し（率が None）を除いた {名称: 率}
PRESET_MAP = {name: rate for name, rate in PRESET_CORRECTIONS if rate is not None}


# ------------------------------------------------------------ 取引事例の絞り込み
def parse_trade_year(period: str):
    """'2023年第2四半期' などから西暦年(int)を取り出す。"""
    if not period:
        return None
    m = re.search(r"(\d{4})", str(period))
    return int(m.group(1)) if m else None


def filter_transactions(trades, target_area, this_year,
                        area_min_ratio=0.5, area_max_ratio=2.0,
                        years_back=3, land_only=True):
    """
    要件6章の初期条件で「採用候補」を判定する。
    戻り値は各取引に '採用' (bool) と '除外理由' を付けたリスト。
    ユーザーは後からチェックボックスで変更できる。
    """
    result = []
    for t in trades:
        reasons = []
        # 土地のみ（宅地(土地)）に限定
        if land_only:
            kind = str(t.get("種類") or "")
            if "土地" not in kind or "建物" in kind:
                reasons.append("土地のみでない")
        # 面積レンジ
        area = t.get("面積_m2")
        if target_area and area:
            if not (target_area * area_min_ratio <= area <= target_area * area_max_ratio):
                reasons.append("面積が範囲外")
        # 取引時期
        y = parse_trade_year(t.get("取引時期"))
        if y is not None and (this_year - y) > years_back:
            reasons.append(f"{years_back}年より前")
        # 単価が取れないものは除外
        if not t.get("単価_円m2"):
            reasons.append("単価不明")

        t2 = dict(t)
        t2["採用"] = (len(reasons) == 0)
        t2["除外理由"] = "／".join(reasons)
        result.append(t2)
    return result


# ------------------------------------------------------------ 公示価格方式
def weighted_koji_price(points, n=3):
    """近い n 地点の㎡単価を、距離の逆数で加重平均する。"""
    usable = [p for p in points if p.get("単価_円m2")]
    usable = sorted(usable, key=lambda p: p["距離m"])[:n]
    if not usable:
        return None, []
    num = 0.0
    den = 0.0
    for p in usable:
        d = max(p["距離m"], 1.0)  # 0m 割り算防止
        w = 1.0 / d
        num += w * p["単価_円m2"]
        den += w
    return (num / den if den else None), usable


# ------------------------------------------------------------ 取引事例方式
def transaction_stats(adopted_trades):
    """採用された取引の㎡単価から 平均・中央値 を返す。"""
    prices = [t["単価_円m2"] for t in adopted_trades if t.get("単価_円m2")]
    if not prices:
        return {"件数": 0, "平均": None, "中央値": None}
    return {
        "件数": len(prices),
        "平均": statistics.fmean(prices),
        "中央値": statistics.median(prices),
    }


# ------------------------------------------------------------ 手動補正
def apply_corrections(base_price, corrections):
    """
    corrections: [{"理由": str, "率": float(%)}]
    率を合算して基準価格に適用する（-10 と +5 なら -5%）。
    戻り値: (補正後価格, 合計補正率%)
    """
    total_pct = sum(float(c.get("率") or 0) for c in corrections)
    corrected = base_price * (1 + total_pct / 100.0)
    return corrected, total_pct


# ------------------------------------------------------------ 総合
def summarize(target_area, koji_unit, trade_stats, corrections,
              range_pct=10.0):
    """
    最終参考価格を組み立てる（要件10・11章）。
    koji_unit: 公示価格方式の㎡単価
    trade_stats: transaction_stats の戻り値
    """
    trade_unit = trade_stats.get("平均")

    # 参考単価 = 公示ベースと取引ベースの単純平均（片方しか無ければそれを使う）
    units = [u for u in (koji_unit, trade_unit) if u]
    ref_unit = (sum(units) / len(units)) if units else None

    out = {
        "地積_m2": target_area,
        "公示ベース単価_円m2": koji_unit,
        "取引ベース単価_円m2": trade_unit,
        "取引中央値_円m2": trade_stats.get("中央値"),
        "参考単価_円m2": ref_unit,
    }
    if ref_unit is None or not target_area:
        out["参考価格_円"] = None
        return out

    base_price = ref_unit * target_area
    out["参考価格_円"] = base_price
    out["参考坪単価_円"] = ref_unit * TSUBO

    # 補正
    corrected, total_pct = apply_corrections(base_price, corrections)
    out["補正率_pct"] = total_pct
    out["補正後価格_円"] = corrected
    out["補正後単価_円m2"] = corrected / target_area if target_area else None

    # 価格レンジ（中心 ± range_pct％）。中心は補正後価格。
    center = corrected
    out["レンジ低_円"] = center * (1 - range_pct / 100.0)
    out["レンジ中心_円"] = center
    out["レンジ高_円"] = center * (1 + range_pct / 100.0)
    return out
