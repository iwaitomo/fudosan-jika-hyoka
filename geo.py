# -*- coding: utf-8 -*-
"""
地理計算のユーティリティ。
- 緯度経度 ⇔ 地図タイル座標(XYZ方式)の変換
- 2地点間の距離(メートル)計算
外部ライブラリ不要（標準ライブラリのみ）。
"""
import math


def deg2tile(lat: float, lon: float, z: int):
    """緯度経度から、その点を含む地図タイルの (x, y) を返す。"""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    # 端の丸め対策
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def surrounding_tiles(lat: float, lon: float, z: int, ring: int = 1):
    """中心タイルと、その周囲 ring 枚ぶんのタイル一覧を返す。ring=1 なら 3x3=9枚。"""
    cx, cy = deg2tile(lat, lon, z)
    n = 2 ** z
    tiles = []
    for dx in range(-ring, ring + 1):
        for dy in range(-ring, ring + 1):
            x = cx + dx
            y = cy + dy
            if 0 <= x < n and 0 <= y < n:
                tiles.append((x, y))
    return tiles


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2地点間の距離をメートルで返す（地球を球とみなす近似）。"""
    R = 6371000.0  # 地球半径(m)
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))
