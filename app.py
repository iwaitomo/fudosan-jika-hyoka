# -*- coding: utf-8 -*-
"""
不動産（土地）簡易時価評価システム — メイン画面
国土交通省「不動産情報ライブラリ」API を使い、
対象土地の周辺の 地価公示・地価調査・取引価格 を集めて、参考価格を試算する。

起動: 同じフォルダの「起動.command」をダブルクリック
（内部では streamlit run app.py が実行される）
"""
import re
import math
import pandas as pd
import streamlit as st

import apikey_store
import demo_data
import tochidai
import auth
from prefectures import PREFECTURES, NAME_TO_CODE
from reinfolib import Reinfolib, ReinfolibError, geocode_gsi, geocode_best
import calc
import db

try:
    import folium
    from streamlit_folium import st_folium
    HAS_MAP = True
except Exception:
    HAS_MAP = False

st.set_page_config(page_title="不動産 簡易時価評価システム", page_icon="🏠", layout="wide")

# アクセス制限（公開時は合言葉ログイン必須。事務所PCのローカル起動は素通り）
current_user = auth.require_login()

THIS_YEAR = 2026  # 取引の「◯年より前」を判定する基準年

# 表の列（この順が既定。ユーザーは並び替え・表示/非表示を選べる）
COLS4_ALL = ["種別", "所在", "㎡単価(円)", "距離(m)", "用途地域",
             "建蔽率", "容積率", "最寄駅", "駅距離", "前年比"]
# STEP5 は「採用」列を常に先頭固定にするので、選択対象からは外す
COLS5_ALL = ["除外理由", "種類", "地区", "取引時期", "面積(㎡)", "取引総額(円)",
             "㎡単価(円)", "坪単価(円)", "用途地域"]


def _sanitize_range(key, lo, hi):
    """スライダーの保存値が現在の範囲外なら消して初期化させる（再取得で範囲が変わる対策）。"""
    if key in st.session_state:
        v = st.session_state[key]
        try:
            a, b = v
            if a < lo or b > hi or a > b:
                del st.session_state[key]
        except Exception:
            del st.session_state[key]


def city_options_with_ward(cities):
    """
    APIの市区町村一覧 [(code, name)] を、区には親の政令市名を補って表示できる形にする。
    例: ('14102','神奈川区') -> 表示・住所名 '横浜市神奈川区'。
    区コードの直下にある「◯◯市」を親とみなす（政令市の区は必ず親市の直後の連番のため）。
    戻り値: [(表示名, code)] のリスト（元の並び順を維持）。
    """
    shi = [(c, n) for c, n in cities if str(n).endswith("市")]

    def parent_name(ward_code):
        cand = [(c, n) for c, n in shi if int(c) <= int(ward_code)]
        return max(cand, key=lambda x: int(x[0]))[1] if cand else ""

    out = []
    for c, n in cities:
        disp = (parent_name(c) + n) if str(n).endswith("区") else n
        out.append((disp, c))
    return out


# ------------------------------------------------------------------ 表示ヘルパ
def yen_man(x):
    """円 → 「7,120万円」の文字列に。"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "―"
    man = x / 10000.0
    return f"{man:,.0f}万円"


def yen(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "―"
    return f"{x:,.0f}円"


def num(x, unit=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "―"
    return f"{x:,.0f}{unit}"


# ------------------------------------------------------------------ 状態初期化
def ss_default(key, value):
    if key not in st.session_state:
        st.session_state[key] = value


ss_default("points", None)        # 地価公示・調査ポイント
ss_default("trades", None)        # 取引事例（絞り込み済み・採用フラグ付き）
ss_default("clicked", None)       # 地図クリック地点 (lat, lon)
ss_default("center", None)        # 地図の中心
ss_default("corrections", [])     # 手動補正 [{理由,率}]
ss_default("cities", None)        # 市区町村一覧
ss_default("loaded_case", None)
ss_default("cols4", list(COLS4_ALL))   # STEP4 の表示列と並び順
ss_default("cols5", list(COLS5_ALL))   # STEP5 の表示列と並び順
ss_default("editor_ver", 0)            # STEP5 の表を作り直すための版番号


def column_picker(all_cols, state_key, label):
    """列の表示/非表示＋並び順を選ぶ小さなUI。選んだ順に左から並ぶ。"""
    with st.expander("🔧 表の列を並び替え・表示切替", expanded=False):
        chosen = st.multiselect(
            label, all_cols,
            key=state_key,
            help="選んだ順に、左から並びます。外すとその列は非表示になります。",
        )
        def _reset(k=state_key, cols=list(all_cols)):
            st.session_state[k] = cols   # コールバック内なら再生成前に書き換えできる
        cc = st.columns(2)
        cc[0].button("既定の順に戻す", key=f"reset_{state_key}",
                     use_container_width=True, on_click=_reset)
    return chosen or list(all_cols)

# ------------------------------------------------------------------ サイドバー
st.sidebar.title("🏠 設定")
auth.logout_button()
st.sidebar.markdown("---")

# APIキーは Secrets（共有）優先。無ければローカル保存値。
resolved_key = auth.resolve_api_key(apikey_store.load_key())
demo_mode = st.sidebar.toggle("デモモード（APIキー不要でお試し）", value=(resolved_key == ""))

st.sidebar.subheader("APIキー")
if demo_mode:
    st.sidebar.info("デモモード中はサンプルデータで動きます。\n本番データを使うにはデモをOFFにしてください。")
elif auth.api_key_is_shared():
    st.sidebar.success("APIキーは管理者が設定済みです（共有）。")
else:
    key_input = st.sidebar.text_input("不動産情報ライブラリのAPIキー", value=resolved_key, type="password")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("保存", use_container_width=True):
        apikey_store.save_key(key_input)
        st.sidebar.success("保存しました")
        st.rerun()
    if c2.button("消去", use_container_width=True):
        apikey_store.clear_key()
        st.sidebar.success("消去しました")
        st.rerun()
    st.sidebar.caption("キーの取り方は「APIキー取得手順.command」を参照。")

api = None
if not demo_mode:
    try:
        api = Reinfolib(resolved_key)
    except ReinfolibError:
        api = None
        st.sidebar.warning("APIキーが未設定です。キーを保存するか、デモモードをONにしてください。")

st.sidebar.markdown("---")
st.sidebar.subheader("💾 保存した案件")
_case_ok = auth.case_save_enabled()
if not _case_ok:
    st.sidebar.info("公開版では、依頼者情報保護のため案件の保存・読込は無効です。"
                    "保存が必要なときは事務所PCのローカル版をお使いください。")
kw = st.sidebar.text_input("検索（案件名・所在）", "") if _case_ok else ""
for row in (db.list_cases(kw) if _case_ok else []):
    label = f"#{row['id']} {row['案件名'] or '(無題)'} — {row['所在'] or ''}"
    if st.sidebar.button(label, key=f"load_{row['id']}", use_container_width=True):
        loaded = db.load_case(row["id"])
        st.session_state["loaded_case"] = loaded
        p = loaded["payload"]
        st.session_state["points"] = p.get("points")
        st.session_state["trades"] = p.get("trades")
        st.session_state["corrections"] = p.get("corrections", [])
        st.session_state["clicked"] = tuple(p["clicked"]) if p.get("clicked") else None
        st.session_state["center"] = st.session_state["clicked"]
        for k in ("in_pref", "in_city_name", "in_town", "in_chiban", "in_area", "in_chimoku", "in_casename"):
            if k in p:
                st.session_state[k] = p[k]
        # 列の並び順
        if p.get("cols4"):
            st.session_state["cols4"] = p["cols4"]
        if p.get("cols5"):
            st.session_state["cols5"] = p["cols5"]
        # 土地代データ
        st.session_state["tochidai"] = p.get("tochidai")
        st.session_state["tochidai_station"] = p.get("tochidai_station")
        # STEP5 の絞り込み条件
        f = p.get("filters") or {}
        if f.get("area") is not None:
            st.session_state["trade_f_area"] = f["area"]
        if f.get("type") is not None:
            st.session_state["trade_f_type"] = f["type"]
        if f.get("zone") is not None:
            st.session_state["trade_f_zone"] = f["zone"]
        if f.get("arange"):
            st.session_state["trade_f_arange"] = tuple(f["arange"])
        if f.get("yrange"):
            st.session_state["trade_f_yrange"] = tuple(f["yrange"])
        st.session_state["editor_ver"] += 1   # 表を作り直して復元状態を反映
        st.rerun()

# ================================================================== メイン
st.title("不動産（土地）簡易時価評価システム")
st.caption("国土交通省「不動産情報ライブラリ」の公開データにもとづく参考値です。正式な鑑定評価ではありません。")

# ---- STEP1 対象土地の入力 -------------------------------------------------
st.header("STEP 1　対象の土地を入力")
col = st.columns(3)
pref_names = [n for _, n in PREFECTURES]
# 既定は神奈川県。値は session_state（key=in_pref）で管理し、読込時もそこへ復元する
if st.session_state.get("in_pref") not in pref_names:
    st.session_state["in_pref"] = "神奈川県"
in_pref = col[0].selectbox("都道府県", pref_names, key="in_pref")

# 市区町村
with col[1]:
    if demo_mode:
        in_city_name = st.text_input("市区町村", st.session_state.get("in_city_name", "横浜市神奈川区"), key="in_city_name")
        city_code = None
    else:
        if st.button("市区町村一覧を取得", use_container_width=True) and api:
            try:
                st.session_state["cities"] = api.get_cities(NAME_TO_CODE[in_pref])
            except ReinfolibError as e:
                st.error(str(e))
        cities = st.session_state.get("cities") or []
        if cities:
            opts = city_options_with_ward(cities)   # [(表示名, code)]
            labels = [d for d, _ in opts]
            sel = st.selectbox("市区町村", labels, key="in_city_sel",
                               help="政令指定都市は「区」まで選んでください（例：横浜市神奈川区）。"
                                    "区を選ばないと地図や取引データがずれることがあります。")
            in_city_name = sel   # 区は「横浜市神奈川区」の形になっている
            city_code = dict(opts)[sel]
            if sel.endswith("市") and any(d.endswith("区") and d.startswith(sel) for d in labels):
                st.warning(f"「{sel}」は政令市です。より正確にするには「{sel}◯◯区」を選んでください。")
        else:
            in_city_name = st.text_input("市区町村（一覧未取得）", st.session_state.get("in_city_name", ""), key="in_city_name")
            city_code = st.text_input("市区町村コード(5桁)", st.session_state.get("in_city_code", ""), key="in_city_code") or None

in_town = col[2].text_input("町名・丁目", st.session_state.get("in_town", "反町"), key="in_town")

col2 = st.columns(3)
in_chiban = col2[0].text_input("地番", st.session_state.get("in_chiban", ""), key="in_chiban")
in_area = col2[1].number_input("地積（㎡）", min_value=0.0, value=float(st.session_state.get("in_area", 150.0)), step=1.0, key="in_area")
in_chimoku = col2[2].text_input("地目（任意）", st.session_state.get("in_chimoku", "宅地"), key="in_chimoku")

# 地図検索用の住所。地番があれば付けて精度を上げ、失敗時は町名までにフォールバックする
geo_town = f"{in_pref}{in_city_name}{in_town}"
_chiban = (in_chiban or "").strip()
geo_address = geo_town + _chiban


def strip_banchi(addr: str) -> str:
    """住所末尾の番地・号（例: 4-27-13, 4丁目27番13号）を取り除く。"""
    if not addr:
        return ""
    return re.sub(r"[\s　]*[0-9０-９][-‐ー－0-9０-９丁目番地号の\s　]*$", "", addr.strip())

# ---- STEP2 地図で地点を確認 ----------------------------------------------
st.header("STEP 2　地図で対象地点を確認")
cc = st.columns([1, 3])
with cc[0]:
    st.caption(f"検索する住所：{geo_address}")
    manual_addr = st.text_input(
        "うまく出ないとき用：住所を手入力", "",
        key="geo_addr_override",
        help="政令市は区（例：横浜市神奈川区反町）まで入れると正確になります。空欄なら上の住所で検索します。",
    )
    do_search = st.button("住所から地図を表示", use_container_width=True)
    # 実際に検索する住所（手入力があれば優先）
    effective_q = (manual_addr or "").strip() or geo_address
    # 住所が前回と変わったら自動で検索（ボタンを押さなくても反映）
    changed = bool(effective_q) and effective_q != st.session_state.get("geo_last_query")
    if do_search or changed:
        if demo_mode:
            st.session_state["center"] = demo_data.geocode(effective_q)
            st.session_state["geo_used"] = effective_q
            st.session_state["clicked"] = st.session_state["center"]
        else:
            # 候補：手入力 → 手入力の番地なし → 地番付き住所 → 町名まで（フォールバック）
            cands = [manual_addr, strip_banchi(manual_addr),
                     geo_address, geo_town]
            hit = geocode_best(cands)
            if hit:
                st.session_state["center"] = (hit[0], hit[1])
                st.session_state["geo_used"] = hit[2]
                st.session_state["clicked"] = st.session_state["center"]
            elif do_search:
                st.warning("住所から位置を特定できませんでした。地図を動かしてクリックしてください。")
                st.session_state["center"] = (35.681, 139.767)
                st.session_state["geo_used"] = None
        st.session_state["geo_last_query"] = effective_q  # 無限ループ防止
    if st.session_state.get("geo_used"):
        st.caption(f"↳ 実際に使った住所：{st.session_state['geo_used']}")
    st.caption("地図上をクリックすると、その地点が対象地になります。")
    if st.session_state["clicked"]:
        la, lo = st.session_state["clicked"]
        st.success(f"対象地点\n\n緯度 {la:.6f}\n経度 {lo:.6f}")

with cc[1]:
    center = st.session_state.get("center") or (35.681, 139.767)
    if HAS_MAP:
        m = folium.Map(location=center, zoom_start=16, tiles="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
                       attr="国土地理院")
        if st.session_state["clicked"]:
            folium.Marker(st.session_state["clicked"], tooltip="対象地",
                          icon=folium.Icon(color="red", icon="home")).add_to(m)
        # 既存の公示ポイントも出す
        for p in (st.session_state.get("points") or []):
            folium.CircleMarker([p["緯度"], p["経度"]], radius=6,
                                color="blue", fill=True, fill_opacity=0.8,
                                tooltip=f'{p["種別"]} {num(p["単価_円m2"])}円/㎡\n{p["所在"]}').add_to(m)
        ret = st_folium(m, height=460, use_container_width=True)
        if ret and ret.get("last_clicked"):
            st.session_state["clicked"] = (ret["last_clicked"]["lat"], ret["last_clicked"]["lng"])
    else:
        st.error("地図ライブラリ（folium / streamlit-folium）が見つかりません。起動.command で自動インストールされます。")
        la = st.number_input("緯度", value=float(center[0]), format="%.6f")
        lo = st.number_input("経度", value=float(center[1]), format="%.6f")
        st.session_state["clicked"] = (la, lo)

# ---- STEP3 周辺データを取得 ----------------------------------------------
st.header("STEP 3　周辺の公示価格・取引事例を取得")
opt = st.columns(3)
land_year = opt[0].selectbox("地価公示・調査の年", [2024, 2023, 2022], index=0)
years_back = opt[1].slider("取引事例をさかのぼる年数", 1, 5, 3)
n_koji = opt[2].slider("公示価格方式で使う近傍地点数", 1, 5, 3)

if st.button("🔎 周辺データを取得する", type="primary", use_container_width=True):
    if not st.session_state["clicked"]:
        st.error("先に地図で対象地点をクリックしてください。")
    else:
        la, lo = st.session_state["clicked"]
        with st.spinner("データ取得中…"):
            try:
                if demo_mode:
                    points = demo_data.land_price_points(la, lo)
                    raw_trades = demo_data.transactions()
                else:
                    points = api.get_land_price_points(la, lo, year=land_year)
                    yrs = range(THIS_YEAR - years_back, THIS_YEAR + 1)
                    if not city_code:
                        st.warning("市区町村コードが不明なため、取引事例は取得できません。市区町村一覧を取得して選び直してください。")
                        raw_trades = []
                    else:
                        raw_trades = api.get_transactions(city_code, yrs)
                st.session_state["points"] = points
                st.session_state["trades"] = calc.filter_transactions(
                    raw_trades, in_area, THIS_YEAR, years_back=years_back)
                st.success(f"公示・調査 {len(points)}件、取引事例 {len(raw_trades)}件を取得しました。")
            except ReinfolibError as e:
                st.error(str(e))

# ---- STEP4 公示・調査ポイント表示 ----------------------------------------
points = st.session_state.get("points")
if points:
    st.header("STEP 4　周辺の地価公示・地価調査")
    # 表とピンを対応させる通し番号（距離が近い順）
    for i, p in enumerate(points, start=1):
        p["No"] = i
    dfp = pd.DataFrame([{
        "No": p["No"], "種別": p["種別"], "所在": p["所在"],
        "㎡単価(円)": p["単価_円m2"], "距離(m)": round(p["距離m"]),
        "用途地域": p["用途地域"], "建蔽率": p["建蔽率"], "容積率": p["容積率"],
        "最寄駅": p["最寄駅"], "駅距離": p["駅距離"], "前年比": p["前年比"],
    } for p in points])
    cols4 = column_picker(COLS4_ALL, "cols4", "表示する列（選んだ順に左から並びます）")
    st.dataframe(dfp[["No"] + cols4], use_container_width=False, hide_index=True,
                 column_config={
                     "㎡単価(円)": st.column_config.NumberColumn("㎡単価(円)", format="localized"),
                     "距離(m)": st.column_config.NumberColumn("距離(m)", format="localized"),
                 })

    # --- 地点を地図で確認（STEP2とは別の専用マップ） ---
    st.subheader("🗺 地点を地図で確認")
    st.caption("表の No. と地図のピン番号が対応します。ピンをクリックすると所在・単価が出ます。"
               "下の選択で特定の地点を拡大表示できます。")
    if HAS_MAP:
        opts_pt = ["― 全体表示 ―"] + [f'{p["No"]}: {p["所在"]}（{num(p["単価_円m2"])}円/㎡）' for p in points]
        pick = st.selectbox("拡大して見る地点", opts_pt, key="pt_focus")
        # 中心とズーム
        if pick != opts_pt[0]:
            pno = int(pick.split(":")[0])
            fp = next(p for p in points if p["No"] == pno)
            m4_center, m4_zoom = [fp["緯度"], fp["経度"]], 17
        elif st.session_state.get("clicked"):
            m4_center, m4_zoom = list(st.session_state["clicked"]), 15
        else:
            m4_center, m4_zoom = [points[0]["緯度"], points[0]["経度"]], 15

        m4 = folium.Map(location=m4_center, zoom_start=m4_zoom,
                        tiles="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
                        attr="国土地理院")
        if st.session_state.get("clicked"):
            folium.Marker(list(st.session_state["clicked"]), tooltip="対象地",
                          icon=folium.Icon(color="red", icon="home")).add_to(m4)
        for p in points:
            color = "#1f6feb" if p["種別"] == "地価公示" else "#2da44e"
            popup_html = (f'<b>No.{p["No"]} {p["種別"]}</b><br>{p["所在"]}<br>'
                          f'{num(p["単価_円m2"])} 円/㎡<br>対象地から {round(p["距離m"])}m<br>'
                          f'用途: {p["用途地域"]}')
            folium.Marker(
                [p["緯度"], p["経度"]],
                tooltip=f'{p["No"]}: {p["所在"]}',
                popup=folium.Popup(popup_html, max_width=260),
                icon=folium.DivIcon(
                    icon_size=(26, 26), icon_anchor=(13, 13),
                    html=(f'<div style="background:{color};color:#fff;border:2px solid #fff;'
                          f'border-radius:50%;width:24px;height:24px;line-height:24px;'
                          f'text-align:center;font-size:12px;font-weight:700;'
                          f'box-shadow:0 0 3px rgba(0,0,0,.4)">{p["No"]}</div>')),
            ).add_to(m4)
        st_folium(m4, height=460, use_container_width=True, key="map_step4",
                  returned_objects=[])
        st.caption("🔵 地価公示　🟢 地価調査　🏠 対象地")
    else:
        st.info("地図ライブラリが未インストールのため、地図は表示できません。")

# ---- 参考：土地代データ（tochidai.info） ---------------------------------
st.header("参考　土地代データ（エリア平均）")
st.caption("外部サイト『土地代データ』のエリア平均で、算定結果を裏取りします。"
           "数値の原典は国土交通省の公示地価・基準地価です。")
tc_cols = st.columns([1, 2])
with tc_cols[0]:
    if st.button("📊 土地代データを照会", use_container_width=True):
        with st.spinner("土地代データを照会中…"):
            try:
                st.session_state["tochidai"] = tochidai.lookup(
                    in_city_name, in_pref, town=in_town)
                if not st.session_state["tochidai"]:
                    st.warning("このエリアのページが見つかりませんでした。"
                               "市区町村（政令市は区まで）を選び直してください。")
            except tochidai.TochidaiError as e:
                st.error(str(e))
with tc_cols[1]:
    st.caption("市区町村（政令市は区まで）を選んでから押してください。"
               f"　照会対象：{in_pref}{in_city_name}")

tc = st.session_state.get("tochidai")
if tc:
    def _yen_tsubo(v):
        return f"{num(v)} 円/㎡（坪 {num(round(v * calc.TSUBO))} 円）" if v else "―"
    y = tc.get("年") or ""
    m1, m2, m3 = st.columns(3)
    m1.metric(f"住宅地の公示地価 平均（{y}年）",
              f'{num(tc.get("住宅地_円m2"))} 円/㎡' if tc.get("住宅地_円m2") else "―",
              tc.get("住宅地_変動率") + "%" if tc.get("住宅地_変動率") else None)
    m2.metric("公示地価 平均（全用途）",
              f'{num(tc.get("公示_円m2"))} 円/㎡' if tc.get("公示_円m2") else "―",
              tc.get("公示_変動率") + "%" if tc.get("公示_変動率") else None)
    m3.metric("基準地価 平均",
              f'{num(tc.get("基準地価_円m2"))} 円/㎡' if tc.get("基準地価_円m2") else "―")
    detail = []
    detail.append(f'・住宅地（公示）：{_yen_tsubo(tc.get("住宅地_円m2"))}')
    detail.append(f'・商業地（公示）：{_yen_tsubo(tc.get("商業地_円m2"))}')
    detail.append(f'・地価総平均（公示＋基準）：{_yen_tsubo(tc.get("総平均_円m2"))}')
    if tc.get("町名一致"):
        tm = tc["町名一致"]
        detail.append(f'・「{tm["名称近傍"]}」付近の掲載地点：{_yen_tsubo(tm.get("円m2"))}')
    st.markdown("\n".join(detail))

    # --- 駅・町丁目の詳細 ---
    stations = tc.get("駅一覧") or []
    if stations:
        with st.expander("🚉 駅・町丁目の詳細を見る", expanded=bool(tc.get("対象駅"))):
            names = [s["駅"] for s in stations]
            # 対象町名に一致する駅を初期選択
            default_idx = 0
            if tc.get("対象駅"):
                try:
                    default_idx = names.index(tc["対象駅"][0]["駅"])
                except ValueError:
                    default_idx = 0
            sc = st.columns([2, 1])
            pick_st = sc[0].selectbox("駅を選ぶ（対象地の最寄り駅など）", names, index=default_idx,
                                      key="tc_station_pick")
            if sc[1].button("この駅の詳細を取得", use_container_width=True):
                url_st = dict((s["駅"], s["url"]) for s in stations)[pick_st]
                with st.spinner("駅ページを取得中…"):
                    try:
                        st.session_state["tochidai_station"] = tochidai.fetch_station(url_st)
                    except tochidai.TochidaiError as e:
                        st.error(str(e))

            ts = st.session_state.get("tochidai_station")
            if ts:
                sy = ts.get("年") or ""
                s1, s2, s3 = st.columns(3)
                s1.metric(f'{ts["駅名"]}｜公示地価 平均（{sy}年）',
                          f'{num(ts.get("公示_円m2"))} 円/㎡' if ts.get("公示_円m2") else "―",
                          ts.get("公示_変動率") + "%" if ts.get("公示_変動率") else None)
                s2.metric("基準地価 平均",
                          f'{num(ts.get("基準地価_円m2"))} 円/㎡' if ts.get("基準地価_円m2") else "―",
                          ts.get("基準地価_変動率") + "%" if ts.get("基準地価_変動率") else None)
                s3.metric("地価総平均（公示＋基準）",
                          f'{num(ts.get("総平均_円m2"))} 円/㎡' if ts.get("総平均_円m2") else "―")
                st.caption(f'この駅の詳細ページ：[{ts["駅名"]}]({ts["url"]})')

            # 対象町名に一致する個別地点（区ページの上位表にあれば）
            if tc.get("対象地点"):
                st.markdown(f"**「{in_town}」を含む掲載地点**")
                st.dataframe(pd.DataFrame([{
                    "住所": p["住所"], "㎡単価(円)": num(p["円m2"]),
                    "坪単価(円)": num(p["坪単価"]), "変動率": p["変動率"], "最寄り": p["最寄り"],
                } for p in tc["対象地点"]]), use_container_width=False, hide_index=True)
            elif tc.get("対象駅"):
                st.caption(f"※「{in_town}」の個別地点は区の上位地点表には含まれていません。"
                           "上の駅ページ（エリア平均）を参考にしてください。")

            # 参考：区内の駅平均ランキング（上位）
            st.markdown("**区内の駅別 地価平均（上位）**")
            st.dataframe(pd.DataFrame([{
                "駅": s["駅"], "㎡単価(円)": num(s["円m2"]),
                "坪単価(円)": num(s["坪単価"]), "変動率": s["変動率"],
            } for s in stations[:12]]), use_container_width=False, hide_index=True)

    # 本システムの参考単価があれば突き合わせ
    ref_unit = st.session_state.get("ref_unit")
    if ref_unit and tc.get("住宅地_円m2"):
        diff = (ref_unit - tc["住宅地_円m2"]) / tc["住宅地_円m2"] * 100
        st.info(f"本システムの参考単価 {num(round(ref_unit))} 円/㎡ は、"
                f"土地代データの住宅地平均 {num(tc['住宅地_円m2'])} 円/㎡ に対して "
                f"{diff:+.1f}% です。")
    st.caption(f'出典：[土地代データ]({tc["url"]})（株式会社Land Price Japan）／'
               "原典：国土交通省 公示地価・基準地価。引用は著作権法32条に基づく。")

# ---- STEP5 取引事例の採否 -------------------------------------------------
adopted = []
if st.session_state.get("trades") is not None:
    st.header("STEP 5　取引事例（採用するものにチェック）")
    trades = st.session_state["trades"]
    if not trades:
        st.info("取引事例が取得できませんでした。")
    else:
        st.caption("表は左右にスクロールできます。「除外理由」は自動判定した参考です（チェックで手動変更可）。")

        # --- 絞り込み（地区・種類・用途地域・面積・取引時期） ---
        areas = sorted({t["地区"] for t in trades if t.get("地区")})
        types = sorted({t["種類"] for t in trades if t.get("種類")})
        zones = sorted({t["用途地域"] for t in trades
                        if t.get("用途地域") and t["用途地域"] != "―"})
        area_vals = [t["面積_m2"] for t in trades if t.get("面積_m2")]
        year_vals = [y for y in (calc.parse_trade_year(t.get("取引時期")) for t in trades) if y]

        with st.expander("🔍 絞り込み条件", expanded=True):
            # session値を掃除（無効値を除去。空なら空のまま＝下で「全件」として扱う）
            for k, opts in (("trade_f_area", areas), ("trade_f_type", types),
                            ("trade_f_zone", zones)):
                if k not in st.session_state:
                    st.session_state[k] = list(opts)
                else:
                    st.session_state[k] = [v for v in st.session_state[k] if v in opts]

            def _set_all(k, o):
                st.session_state[k] = list(o)

            def _set_none(k):
                st.session_state[k] = []

            fcol = st.columns(3)
            filt_defs = [("地区（例：反町だけ）", "trade_f_area", areas),
                         ("種類（例：宅地(土地)だけ）", "trade_f_type", types),
                         ("用途地域", "trade_f_zone", zones)]
            sels = {}
            for c, (lbl, key, opts) in zip(fcol, filt_defs):
                with c:
                    bb = st.columns(2)
                    bb[0].button("全選択", key=f"all_{key}", use_container_width=True,
                                 on_click=_set_all, args=(key, opts))
                    bb[1].button("全解除", key=f"none_{key}", use_container_width=True,
                                 on_click=_set_none, args=(key,))
                    sels[key] = st.multiselect(lbl, opts, key=key)
            # 空選択は「全件」（＝絞り込みなし）として扱う
            area_sel = sels["trade_f_area"] or areas
            type_sel = sels["trade_f_type"] or types
            zone_sel = sels["trade_f_zone"] or zones

            # スライダー系（面積・年）。範囲が1点しかなければ出さない
            scol = st.columns(2)
            a_range = None
            if area_vals and min(area_vals) < max(area_vals):
                amin, amax = int(math.floor(min(area_vals))), int(math.ceil(max(area_vals)))
                _sanitize_range("trade_f_arange", amin, amax)
                st.session_state.setdefault("trade_f_arange", (amin, amax))
                a_range = scol[0].slider("面積(㎡)の範囲", amin, amax, key="trade_f_arange")
            y_range = None
            if year_vals and min(year_vals) < max(year_vals):
                ymin, ymax = min(year_vals), max(year_vals)
                _sanitize_range("trade_f_yrange", ymin, ymax)
                st.session_state.setdefault("trade_f_yrange", (ymin, ymax))
                y_range = scol[1].slider("取引時期（年）の範囲", ymin, ymax, step=1,
                                         key="trade_f_yrange")

        def _passes(t):
            if t["地区"] not in area_sel or t["種類"] not in type_sel:
                return False
            z = t.get("用途地域")
            if z and z in zones and z not in zone_sel:
                return False
            if a_range and t.get("面積_m2") is not None:
                if not (a_range[0] <= t["面積_m2"] <= a_range[1]):
                    return False
            if y_range:
                y = calc.parse_trade_year(t.get("取引時期"))
                if y and not (y_range[0] <= y <= y_range[1]):
                    return False
            return True

        # 表示対象（絞り込み後）の trades インデックス
        visible_idx = [i for i, t in enumerate(trades) if _passes(t)]

        # --- 一括選択ボタン ---
        bcol = st.columns(4)
        if bcol[0].button("表示中を全選択", use_container_width=True):
            for i in visible_idx:
                trades[i]["採用"] = True
            st.session_state["editor_ver"] += 1
            st.rerun()
        if bcol[1].button("表示中を全解除", use_container_width=True):
            for i in visible_idx:
                trades[i]["採用"] = False
            st.session_state["editor_ver"] += 1
            st.rerun()
        if bcol[2].button("自動候補だけにする", use_container_width=True,
                          help="面積・時期・地目などの初期条件を満たすものだけを採用にします"):
            for i in visible_idx:
                trades[i]["採用"] = (not trades[i]["除外理由"])
            st.session_state["editor_ver"] += 1
            st.rerun()
        if bcol[3].button("表示中だけに絞って採用", use_container_width=True,
                          help="絞り込みで隠れている事例は採用から外します"):
            vis = set(visible_idx)
            for i in range(len(trades)):
                trades[i]["採用"] = (i in vis and not trades[i]["除外理由"])
            st.session_state["editor_ver"] += 1
            st.rerun()

        cols5 = column_picker(COLS5_ALL, "cols5", "表示する列（「採用」は常に先頭。選んだ順に左から並びます）")
        dft = pd.DataFrame([{
            "採用": trades[i]["採用"], "除外理由": trades[i]["除外理由"] or "（採用候補）",
            "種類": trades[i]["種類"], "地区": trades[i]["地区"], "取引時期": trades[i]["取引時期"],
            "面積(㎡)": trades[i]["面積_m2"], "取引総額(円)": trades[i]["取引総額_円"],
            "㎡単価(円)": round(trades[i]["単価_円m2"]) if trades[i]["単価_円m2"] else None,
            "坪単価(円)": round(trades[i]["坪単価_円"]) if trades[i]["坪単価_円"] else None,
            "用途地域": trades[i]["用途地域"],
        } for i in visible_idx])
        if dft.empty:
            st.info("絞り込み条件に合う事例がありません。条件をゆるめてください。")
        else:
            dft = dft[["採用"] + cols5]
            # 絞り込み・一括操作が変わると表を作り直す（チェック状態を正しく反映）
            sig = (f'{st.session_state["editor_ver"]}|{len(visible_idx)}|'
                   f'{",".join(area_sel)}|{",".join(type_sel)}|{",".join(zone_sel)}|'
                   f'{a_range}|{y_range}')
            edited = st.data_editor(
                dft, use_container_width=False, hide_index=True, height=460,
                column_config={
                    "採用": st.column_config.CheckboxColumn("採用", default=True, width="small"),
                    "除外理由": st.column_config.TextColumn("除外理由", width="large"),
                    "用途地域": st.column_config.TextColumn("用途地域", width="medium"),
                    "面積(㎡)": st.column_config.NumberColumn("面積(㎡)", format="localized"),
                    "取引総額(円)": st.column_config.NumberColumn("取引総額(円)", format="localized"),
                    "㎡単価(円)": st.column_config.NumberColumn("㎡単価(円)", format="localized"),
                    "坪単価(円)": st.column_config.NumberColumn("坪単価(円)", format="localized"),
                },
                disabled=[c for c in dft.columns if c != "採用"],
                key=f"trade_editor_{abs(hash(sig))}",
            )
            # 手動チェックを trades に反映
            for j, i in enumerate(visible_idx):
                trades[i]["採用"] = bool(edited.iloc[j]["採用"])

        # 算定には「絞り込み表示中」かつ「採用」の事例だけを使う（表示と結果を一致させる）
        adopted = [trades[i] for i in visible_idx if trades[i]["採用"]]
        total_adopted = sum(1 for t in trades if t["採用"])
        st.success(f"➡ 算定に使う採用事例：**{len(adopted)}件**"
                   f"（表示中 {len(visible_idx)}件 / 全 {len(trades)}件）")
        st.caption("※ STEP7 の取引事例方式は、いま表示（絞り込み）中で「採用」にした事例だけを集計します。"
                   "全件を対象にしたいときは絞り込みを『全選択／全範囲』に戻してください。")

# ---- STEP6 手動補正 -------------------------------------------------------
st.header("STEP 6　手動補正（任意）")
st.caption("角地・不整形・接道・私道など、機械では拾えない増減価を加えます。"
           "下の一覧から項目を選ぶと目安の率が入ります（率は自由に調整できます）。")

OTHER = "その他（自由に入力）"
preset_labels = []
for name, rate in calc.PRESET_CORRECTIONS:
    if rate is None:
        preset_labels.append(name)                       # 見出し行
    else:
        preset_labels.append(f"　{name}（目安 {rate:+.0f}%）")
preset_labels.append(OTHER)

cor_cols = st.columns([3, 1, 1])
sel_label = cor_cols[0].selectbox("補正項目", preset_labels, key="cor_select")

# 選んだ項目 → 理由名と目安率を決める
is_heading = sel_label.startswith("＜") and "目安" not in sel_label
if sel_label == OTHER:
    reason_default, rate_default = "", 0.0
elif is_heading:
    reason_default, rate_default = "", 0.0
else:
    reason_default = sel_label.strip().split("（目安")[0]
    rate_default = calc.PRESET_MAP.get(reason_default, 0.0)

if sel_label == OTHER:
    new_reason = cor_cols[0].text_input("補正理由（自由入力）", key="cor_reason_free")
else:
    new_reason = reason_default
# 選択が変わると率の初期値も切り替わるよう、キーに選択名を含める
new_pct = cor_cols[1].number_input("補正率(%)", value=float(rate_default), step=1.0,
                                   key=f"cor_pct_{sel_label}")
add_disabled = is_heading or (sel_label == OTHER and not new_reason)
if cor_cols[2].button("追加", use_container_width=True, disabled=add_disabled):
    if new_reason:
        st.session_state["corrections"].append({"理由": new_reason, "率": new_pct})
        st.rerun()
if st.session_state["corrections"]:
    for i, c in enumerate(st.session_state["corrections"]):
        cc2 = st.columns([4, 1, 1])
        cc2[0].write(f"・{c['理由']}")
        cc2[1].write(f"{c['率']:+.0f}%")
        if cc2[2].button("削除", key=f"del_cor_{i}"):
            st.session_state["corrections"].pop(i)
            st.rerun()

# ---- STEP7 算定結果 -------------------------------------------------------
st.header("STEP 7　算定結果（参考価格）")
if points or adopted:
    koji_unit, used_pts = calc.weighted_koji_price(points or [], n=n_koji)
    tstats = calc.transaction_stats(adopted)
    result = calc.summarize(in_area, koji_unit, tstats, st.session_state["corrections"])
    st.session_state["ref_unit"] = result.get("参考単価_円m2")  # 土地代データとの突合用

    m1, m2, m3 = st.columns(3)
    m1.metric("参考単価（㎡）", num(result.get("参考単価_円m2"), "円"))
    m2.metric("参考価格", yen_man(result.get("参考価格_円")))
    m3.metric("補正後の価格", yen_man(result.get("補正後価格_円")),
              delta=f'{result.get("補正率_pct", 0):+.0f}%' if result.get("補正率_pct") else None)

    if result.get("レンジ中心_円"):
        st.subheader("価格レンジ（目安）")
        r1, r2, r3 = st.columns(3)
        r1.metric("低位", yen_man(result["レンジ低_円"]))
        r2.metric("中位", yen_man(result["レンジ中心_円"]))
        r3.metric("高位", yen_man(result["レンジ高_円"]))

    st.subheader("比較表")
    nearest = None
    for p in (points or []):
        if p.get("単価_円m2"):
            nearest = p["単価_円m2"]
            break
    comp_rows = [
        {"評価方法": "最寄の公示・調査地点", "㎡単価(円)": num(nearest)},
        {"評価方法": f"公示価格方式（近傍{len(used_pts)}地点の加重平均）", "㎡単価(円)": num(koji_unit)},
        {"評価方法": "取引事例方式（採用の平均）", "㎡単価(円)": num(tstats.get("平均"))},
        {"評価方法": "取引事例方式（採用の中央値）", "㎡単価(円)": num(tstats.get("中央値"))},
        {"評価方法": "システム参考単価（両者平均）", "㎡単価(円)": num(result.get("参考単価_円m2"))},
        {"評価方法": "手動補正後", "㎡単価(円)": num(result.get("補正後単価_円m2"))},
    ]
    _tc = st.session_state.get("tochidai")
    if _tc and _tc.get("住宅地_円m2"):
        comp_rows.append({"評価方法": "参考：土地代データ 住宅地平均（エリア）",
                          "㎡単価(円)": num(_tc["住宅地_円m2"])})
    st.table(pd.DataFrame(comp_rows))
    if _tc and _tc.get("住宅地_円m2"):
        st.caption("最終行は外部サイト『土地代データ』のエリア平均（原典：国土交通省）。参考値です。")

    st.info("※ この価格は公開データにもとづく機械的な試算です。個別の権利関係・建物・市場動向は反映していません。")
else:
    st.caption("STEP 3 で周辺データを取得すると、ここに参考価格が表示されます。")
    result = None

# ---- STEP8 保存 -----------------------------------------------------------
st.header("STEP 8　この案件を保存")
if not auth.case_save_enabled():
    st.info("🔒 公開版では、依頼者情報保護のため案件の保存は無効にしています。"
            "案件として保存したい場合は、事務所PCのローカル版（起動.command）をご利用ください。")
    st.stop()
case_name = st.text_input("案件名", st.session_state.get("in_casename", ""), key="in_casename")
memo = st.text_area("メモ", st.session_state.get("in_memo", ""), key="in_memo")
if st.button("💾 保存する", use_container_width=True):
    payload = {
        "points": points,
        "trades": st.session_state.get("trades"),
        "corrections": st.session_state["corrections"],
        "clicked": list(st.session_state["clicked"]) if st.session_state["clicked"] else None,
        "result": result,
        "in_pref": in_pref, "in_city_name": in_city_name, "in_town": in_town,
        "in_chiban": in_chiban, "in_area": in_area, "in_chimoku": in_chimoku,
        "in_casename": case_name,
        # STEP5 の絞り込み条件（採否は trades の「採用」フラグに保存済み）
        "filters": {
            "area": st.session_state.get("trade_f_area"),
            "type": st.session_state.get("trade_f_type"),
            "zone": st.session_state.get("trade_f_zone"),
            "arange": list(st.session_state["trade_f_arange"]) if st.session_state.get("trade_f_arange") else None,
            "yrange": list(st.session_state["trade_f_yrange"]) if st.session_state.get("trade_f_yrange") else None,
        },
        "cols4": st.session_state.get("cols4"),
        "cols5": st.session_state.get("cols5"),
        # 土地代データの裏取り結果
        "tochidai": st.session_state.get("tochidai"),
        "tochidai_station": st.session_state.get("tochidai_station"),
    }
    cid = db.save_case(case_name, f"{in_pref}{in_city_name}{in_town}", in_chiban, in_area, memo, payload)
    st.success(f"保存しました（案件番号 #{cid}）。左のサイドバーからいつでも呼び出せます。")
