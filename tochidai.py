# -*- coding: utf-8 -*-
"""
土地代データ（tochidai.info）から、対象エリアの平均地価などを取得する。

- 索引は同サイトが AI 向けに公開している llms.txt を使う（robots.txt で許可）。
  これをローカルにキャッシュし、エリア名 → ページURL を引く。
- 各エリアページの <meta name="Description"> に、公示地価・基準地価の平均や
  住宅地/商業地の内訳が構造化された文で入っているので、そこから数値を取り出す。
- 出典表示のうえで参考値として使う（引用は著作権法32条／原典は国土交通省）。

※あくまで「参考・裏取り」用。本システムの算定は国交省APIの生データが主。
"""
import os
import re
import time
import requests

BASE = "https://tochidai.info"
INDEX_URL = BASE + "/llms.txt"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
INDEX_CACHE = os.path.join(DATA_DIR, "tochidai_index.txt")
UA = {"User-Agent": "RealEstateValuationTool/1.0 (personal use; cited per JP Copyright Act art.32)"}
TIMEOUT = 25

# tochidai の予約パス（エリアページではないもの）
_RESERVED = {"mansion", "area", "rail", "map", "shutoken", "tohoku", "kanto",
             "chubu", "kinki", "kansai", "chugoku", "shikoku", "kyushu",
             "hokkaido", "okinawa", "what-is-public-land-price",
             "what-is-base-land-price", "public-price_city-ranking",
             "otoiawase", "company", "sitemap.xml", "llms.txt"}


class TochidaiError(Exception):
    pass


def _man_to_yen(s: str):
    """'54万6972' や '22万2000'、'100万' を円(int)に。'54万6972円' でも可。"""
    if not s:
        return None
    s = s.replace(",", "")
    m = re.match(r"(\d+)万(\d*)", s)
    if m:
        man = int(m.group(1))
        rest = m.group(2)
        return man * 10000 + (int(rest) if rest else 0)
    m2 = re.match(r"(\d+)", s)
    return int(m2.group(1)) if m2 else None


# ------------------------------------------------------------------ 索引
def load_index(max_age_days: int = 7):
    """llms.txt を取得（キャッシュ）し、[(タイトル, URL)] を返す。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    text = None
    if os.path.exists(INDEX_CACHE):
        age = time.time() - os.path.getmtime(INDEX_CACHE)
        if age < max_age_days * 86400:
            with open(INDEX_CACHE, encoding="utf-8") as f:
                text = f.read()
    if text is None:
        try:
            r = requests.get(INDEX_URL, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            text = r.text
            with open(INDEX_CACHE, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            # 取れなければ古いキャッシュでも使う
            if os.path.exists(INDEX_CACHE):
                with open(INDEX_CACHE, encoding="utf-8") as f:
                    text = f.read()
            else:
                raise TochidaiError(f"土地代データの索引を取得できませんでした: {e}")
    # 形式: "- 名称:[URL](URL)"  （名称はブラケットの外にある）
    entries = re.findall(
        r"-\s*(.+?):\[https?://[^\]]+\]\((https://tochidai\.info/[^)]+)\)", text)
    # 名称がURLそのものの行（見出し用）は除外
    return [(name.strip(), url) for name, url in entries if not name.startswith("http")]


def find_city_url(index, city_name: str, pref_name: str):
    """
    市区町村ページ（例: 横浜市神奈川区 → /kanagawa/yokohama-kanagawa/）のURLを返す。
    タイトルが city_name で始まり、県名を含むものを優先。マンション等は除外。
    """
    city_name = (city_name or "").strip()
    if not city_name:   # 市区町村が未指定なら県ページ等に誤マッチさせない
        return None
    prefix = city_name + "の"

    def is_city_level(url):
        # 県ページ(/kanagawa/)は1階層。市区町村ページは2階層(/kanagawa/yokohama-kanagawa/)
        segs = [s for s in url.replace(BASE, "").split("/") if s]
        return len(segs) >= 2 and segs[0] not in _RESERVED

    # 1) タイトルが「◯◯市△△区の…」で始まり、県名を含む（マンション除く）
    for title, url in index:
        if "/mansion/" in url or not is_city_level(url):
            continue
        if title.startswith(prefix) and (pref_name in title):
            return url
    # 2) ゆるめ：city_name と 県名を含む（市区町村階層・マンション除く）
    for title, url in index:
        if "/mansion/" in url or not is_city_level(url):
            continue
        if city_name in title and pref_name in title:
            return url
    return None


# ------------------------------------------------------------------ ページ解析
def _fetch_html(url: str):
    try:
        return requests.get(url, headers=UA, timeout=TIMEOUT).text
    except Exception as e:
        raise TochidaiError(f"土地代データのページを取得できませんでした: {e}")


def _desc_fields(html: str):
    """meta Description から公示・住宅地・商業地・基準地価などの主要数値を取り出す。"""
    m = re.search(r'<meta name="Description" content="(.*?)"\s*/?>', html, re.S)
    desc = m.group(1) if m else ""

    def num(pat):
        mm = re.search(pat, desc)
        return _man_to_yen(mm.group(1)) if mm else None

    def rate(pat):
        mm = re.search(pat, desc)
        return mm.group(1) if mm else None

    my = (re.search(r"(\d{4})年[^。]*?公示地価の平均", desc)
          or re.search(r"公示地価の平均.*?(\d{4})年", desc)
          or re.search(r"(\d{4})年［令和", desc))
    return {
        "公示_円m2": num(r"公示地価の平均は([\d万]+)円/m2"),
        "公示_変動率": rate(r"公示地価の前年からの変動率は([+\-][\d.]+)％"),
        "住宅地_円m2": num(r"住宅地の[^。]*?公示地価[^。]*?平均は([\d万]+)円/m2"),
        "住宅地_変動率": rate(r"住宅地の[^。]*?公示地価[^。]*?変動率は([+\-][\d.]+)％"),
        "商業地_円m2": num(r"商業地の[^。]*?公示地価[^。]*?平均は([\d万]+)円/m2"),
        "基準地価_円m2": num(r"基準地価の平均は([\d万]+)円/m2"),
        "基準地価_変動率": rate(r"基準地価の前年からの変動率は([+\-][\d.]+)％"),
        "総平均_円m2": num(r"地価の総平均（公示地価[、と]*基準地価[のを平均]*）は([\d万]+)円/m2"),
        "年": (my.group(1) if my else None),
    }


def parse_point_rows(html: str):
    """区ページの『地点別ランキング表』（class="address"）から個別地点を取り出す。"""
    rows = []
    for tr in re.split(r"<tr[ >]", html):
        if 'class="address"' not in tr:
            continue
        a = re.search(r'class="address">([^<]+)<', tr)
        p = re.search(r'class="land-price">\s*<b[^>]*>([\d万]+)</b>', tr)
        if not (a and p):
            continue
        st = re.search(r'class="station">([^<]*)<', tr)
        tb = re.search(r'class="tsubo-price[^"]*">\s*<b>([\d万]+)</b>', tr)
        ch = re.search(r'class="change[^"]*">\s*<span>([+\-][\d.]+)</span>', tr)
        rows.append({
            "住所": a.group(1).strip(),
            "最寄り": (st.group(1).strip() if st else ""),
            "円m2": _man_to_yen(p.group(1)),
            "坪単価": _man_to_yen(tb.group(1)) if tb else None,
            "変動率": (ch.group(1) if ch else None),
        })
    return rows


def parse_station_rows(html: str):
    """区ページの『駅別ランキング表』から、駅名・平均・駅ページURLを取り出す。"""
    rows = []
    for tr in re.split(r"<tr[ >]", html):
        a = re.search(r'class="station">\s*<a href="(/area/[^"]+)">([^<]+)</a>', tr)
        p = re.search(r'class="land-price">\s*<span[^>]*>([\d万]+)</span>', tr)
        if not (a and p):
            continue
        tb = re.search(r'class="tsubo-price[^"]*">\s*<span>([\d万]+)</span>', tr)
        ch = re.search(r'class="change[^"]*">\s*<span>([+\-][\d.]+)</span>', tr)
        rows.append({
            "駅": a.group(2).strip(),
            "url": BASE + a.group(1),
            "円m2": _man_to_yen(p.group(1)),
            "坪単価": _man_to_yen(tb.group(1)) if tb else None,
            "変動率": (ch.group(1) if ch else None),
        })
    return rows


def fetch_area_summary(url: str, town: str = ""):
    """区・市ページの主要数値＋駅一覧＋（対象町名に一致する）地点を返す。"""
    html = _fetch_html(url)
    out = {"url": url}
    out.update(_desc_fields(html))

    stations = parse_station_rows(html)
    points = parse_point_rows(html)
    out["駅一覧"] = stations
    out["地点上位"] = points

    # 対象町名に一致する駅・地点（例: town='反町' → '反町駅'、'反町1丁目…'）
    t = (town or "").strip()
    out["対象駅"] = [s for s in stations if t and t in s["駅"]] if t else []
    out["対象地点"] = [p for p in points if t and t in p["住所"]] if t else []
    # 後方互換：単一の町名一致（駅を優先）
    if out["対象駅"]:
        out["町名一致"] = {"名称近傍": out["対象駅"][0]["駅"], "円m2": out["対象駅"][0]["円m2"]}
    elif out["対象地点"]:
        out["町名一致"] = {"名称近傍": out["対象地点"][0]["住所"], "円m2": out["対象地点"][0]["円m2"]}
    else:
        out["町名一致"] = None
    return out


def fetch_station(url: str):
    """駅ページ（/area/…）の主要数値を返す。"""
    html = _fetch_html(url)
    title = re.search(r"<title>\s*(.+?駅)", html)
    out = {"url": url, "駅名": (title.group(1).strip() if title else url)}
    out.update(_desc_fields(html))
    return out


def lookup(city_name: str, pref_name: str, town: str = ""):
    """市区町村名から、土地代データの主要数値をまとめて返す。見つからなければ None。"""
    index = load_index()
    url = find_city_url(index, city_name, pref_name)
    if not url:
        return None
    return fetch_area_summary(url, town=town)
