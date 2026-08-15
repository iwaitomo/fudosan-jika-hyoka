# -*- coding: utf-8 -*-
"""
国土交通省「不動産情報ライブラリ」API クライアント。

- XIT002: 都道府県内の市区町村一覧
- XPT002: 地価公示・地価調査のポイント（点）データ（地図タイル方式・GeoJSON）
- XIT001: 不動産価格（取引価格・成約価格）情報

すべて Ocp-Apim-Subscription-Key ヘッダに APIキーを付けて呼び出す。
戻り値は、このアプリで扱いやすい日本語キーの辞書に正規化して返す。
"""
import re
import requests

from geo import surrounding_tiles, haversine_m

BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external/"
TIMEOUT = 30


class ReinfolibError(Exception):
    pass


def _to_num(value):
    """'1,230円/㎡' のような文字列から数値(float)を取り出す。取れなければ None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


class Reinfolib:
    def __init__(self, api_key: str):
        if not api_key:
            raise ReinfolibError("APIキーが設定されていません。")
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({"Ocp-Apim-Subscription-Key": self.api_key})

    def _get(self, endpoint: str, params: dict) -> dict:
        url = BASE + endpoint
        r = self.session.get(url, params=params, timeout=TIMEOUT)
        if r.status_code == 401 or r.status_code == 403:
            raise ReinfolibError("APIキーが正しくないか、権限がありません（認証エラー）。")
        if r.status_code == 429:
            raise ReinfolibError("アクセスが集中しています。少し待って再実行してください。")
        if not r.ok:
            raise ReinfolibError(f"通信エラー（HTTP {r.status_code}）: {url}")
        try:
            return r.json()
        except ValueError:
            raise ReinfolibError("APIの応答を解釈できませんでした。")

    # ---------------------------------------------------------------- 市区町村一覧
    def get_cities(self, pref_code: str):
        """[(city_code, city_name), ...] を返す。"""
        data = self._get("XIT002", {"area": pref_code})
        out = []
        for row in data.get("data", []):
            code = row.get("id") or row.get("city_code")
            name = row.get("name") or row.get("city_name")
            if code and name:
                out.append((str(code), str(name)))
        return out

    # ---------------------------------------------------- 地価公示・地価調査ポイント
    def get_land_price_points(self, lat: float, lon: float, year: int,
                              z: int = 13, ring: int = 1, max_points: int = 30):
        """
        対象地点(lat, lon)の周辺の地価公示・地価調査ポイントを取得し、
        対象地からの距離が近い順に並べて返す。
        """
        tiles = surrounding_tiles(lat, lon, z, ring=ring)
        seen = set()
        points = []
        for (x, y) in tiles:
            params = {
                "response_format": "geojson",
                "z": z, "x": x, "y": y,
                "year": year,
            }
            data = self._get("XPT002", params)
            for feat in data.get("features", []):
                p = feat.get("properties", {}) or {}
                geom = feat.get("geometry", {}) or {}
                coords = geom.get("coordinates") or [None, None]
                plon, plat = coords[0], coords[1]
                pid = p.get("point_id") or (plat, plon)
                if pid in seen:
                    continue
                seen.add(pid)
                if plat is None or plon is None:
                    continue
                dist = haversine_m(lat, lon, float(plat), float(plon))
                points.append(self._normalize_point(p, float(plat), float(plon), dist))
        points.sort(key=lambda d: d["距離m"])
        return points[:max_points]

    @staticmethod
    def _normalize_point(p: dict, plat: float, plon: float, dist: float):
        loc = "".join([
            str(p.get("city_county_name_ja") or ""),
            str(p.get("ward_town_village_name_ja") or ""),
            str(p.get("place_name_ja") or ""),
            str(p.get("standard_lot_number_ja") or ""),
        ])
        ltype = str(p.get("land_price_type") or "")
        if ltype in ("0", "00"):
            種別 = "地価公示"
        elif ltype in ("1", "01"):
            種別 = "地価調査"
        elif "公示" in ltype:
            種別 = "地価公示"
        elif "調査" in ltype:
            種別 = "地価調査"
        else:
            種別 = ltype or "―"
        return {
            "種別": 種別,
            "所在": loc or "―",
            "単価_円m2": _to_num(p.get("u_current_years_price_ja")),
            "地積_m2": _to_num(p.get("u_cadastral_ja")),
            "用途地域": p.get("regulations_use_category_name_ja") or "―",
            "建蔽率": p.get("regulations_building_coverage_ratio_ja"),
            "容積率": p.get("regulations_floor_area_ratio_ja"),
            "最寄駅": p.get("nearest_station_name_ja") or "―",
            "駅距離": p.get("u_road_distance_to_nearest_station_name_ja") or "―",
            "前面道路幅員": _to_num(p.get("front_road_width")),
            "前年比": p.get("year_on_year_change_rate"),
            "緯度": plat,
            "経度": plon,
            "距離m": dist,
        }

    # ---------------------------------------------------------------- 取引価格情報
    def get_transactions(self, city_code: str, years, quarters=(1, 2, 3, 4)):
        """
        指定した市区町村コードの取引価格情報を、複数年ぶんまとめて取得。
        years: 取得したい年の反復可能オブジェクト（例: range(2022, 2025)）
        """
        out = []
        for year in years:
            for q in quarters:
                params = {"city": city_code, "year": int(year), "quarter": int(q)}
                try:
                    data = self._get("XIT001", params)
                except ReinfolibError:
                    continue
                for row in data.get("data", []):
                    out.append(self._normalize_trade(row))
        return out

    @staticmethod
    def _normalize_trade(row: dict):
        area = _to_num(row.get("Area"))
        trade = _to_num(row.get("TradePrice"))
        unit_m2 = _to_num(row.get("UnitPrice"))  # ㎡単価（あれば）
        if unit_m2 is None and trade and area:
            unit_m2 = trade / area
        tsubo = _to_num(row.get("PricePerUnit"))  # 坪単価（あれば）
        if tsubo is None and unit_m2:
            tsubo = unit_m2 * 3.305785
        return {
            "種類": row.get("Type") or "",
            "地区": row.get("DistrictName") or "",
            "取引時期": row.get("Period") or "",
            "面積_m2": area,
            "取引総額_円": trade,
            "単価_円m2": unit_m2,
            "坪単価_円": tsubo,
            "用途地域": row.get("CityPlanning") or "―",
            "建蔽率": row.get("CoverageRatio"),
            "容積率": row.get("FloorAreaRatio"),
            "前面道路": row.get("Breadth") or row.get("Classification") or "―",
            "最寄駅": row.get("Region") or row.get("DistrictName") or "―",
            "駅距離": row.get("MinTimeToNearestStation") or "―",
            "利用状況": row.get("Use") or "",
        }


# ---------------------------------------------------------------- ジオコーディング
def geocode_gsi(address: str):
    """
    国土地理院の住所検索API（APIキー不要）で住所→緯度経度。
    見つかれば (lat, lon)、なければ None。
    """
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    try:
        r = requests.get(url, params={"q": address}, timeout=15)
        r.raise_for_status()
        js = r.json()
    except Exception:
        return None
    if not js:
        return None
    coords = js[0].get("geometry", {}).get("coordinates")
    if not coords:
        return None
    lon, lat = coords[0], coords[1]
    return float(lat), float(lon)


def geocode_best(candidates):
    """
    住所の候補を順に試し、最初に見つかった (lat, lon) を返す。
    政令市で区が抜けている場合などに備え、いくつかの言い回しを試す。
    戻り値: (lat, lon, 使った住所) / 見つからなければ None。
    """
    for q in candidates:
        q = (q or "").strip()
        if not q:
            continue
        r = geocode_gsi(q)
        if r:
            return r[0], r[1], q
    return None
