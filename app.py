# -*- coding: utf-8 -*-
"""
地震発生確率マップ - ETAS + TEC電離層撹乱 統合版

3種類のマップをタブ切り替えで表示:
  [1] ETAS マップ    : 直近地震履歴からの余震確率（Ogata 1998）
  [2] TEC 撹乱マップ : 電離層TEC Zスコア（参考指標）
  [3] 統合リスクマップ: (1) + w*(2) の加重合算

TEC データ取得戦略（優先順）:
  1. GEONET（国土地理院）RINEX  - 日本全国約1300点の電子基準点GNSS生データから
                                   直接TEC計算。最も日本に特化した高密度データ。
                                   ソース: ftp://terras.gsi.go.jp/data/GPS_products/
  2. IGS IONEX ファイル          - JPL/CODE/ESA等のグローバルマップ（2.5°×5.0°）
  3. NOAA SWPC フォールバック    - Kp/Dst指数からの簡易推定

  - 撹乱指標: 過去7日間の同時刻帯の平均・標準偏差に対するZスコア
              Z = (TEC_now - mean_7d) / std_7d
"""

from flask import Flask, render_template_string
import requests
import csv
import os
import math
import re
import gzip
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import folium
from folium import Element
import numpy as np

app = Flask(__name__)

# ── 定数 ──────────────────────────────────────────────
DATA_FILE          = "data/quakes.csv"
IONEX_CACHE_DIR    = "data/ionex"
GRID_SIZE          = 0.1          # ETAS格子間隔（度）
FETCH_INTERVAL_SEC = 600          # バックグラウンド更新間隔（秒）
TEC_HISTORY_DAYS   = 7            # Zスコア計算に使う過去日数
TEC_WEIGHT         = 0.4          # 統合マップでのTEC寄与率（0〜1）

# ── グローバルキャッシュ ──────────────────────────────
_cache_lock   = threading.Lock()
_cached_maps  = None   # {"etas": html, "tec": html, "combined": html}
_last_update  = 0.0
_ready_phase  = 0      # 0=未準備, 1=地震データのみ準備完了, 2=TEC含む全データ準備完了


# ══════════════════════════════════════════════════════
# ETAS パラメータ（Ogata 1998, 日本カタログ推定値）
# ══════════════════════════════════════════════════════
class ETASParams:
    MU          = 0.05
    K           = 0.020
    C           = 0.010
    P           = 1.11
    ALPHA       = 2.30
    M0          = 1.0
    D           = 0.015
    GAMMA       = 0.50
    Q           = 1.58
    DEPTH_SCALE = 80.0
    SPACE_RADIUS = 8

EP = ETASParams()


# ══════════════════════════════════════════════════════
# 地震データ取得
# ══════════════════════════════════════════════════════
def fetch_quakes_p2p():
    """P2P地震情報API: 無感地震履歴取得用（有感判定なし・座標のみ）"""
    url = "https://api.p2pquake.net/v2/history?codes=551&limit=100"
    try:
        res = requests.get(url, timeout=10,
                           headers={"User-Agent": "EarthquakeApp/4.0"})
        data = res.json()
    except Exception as e:
        print(f"[P2P] 取得エラー: {e}")
        return []
    quakes = []
    for item in data:
        if "earthquake" not in item:
            continue
        eq   = item["earthquake"]
        hypo = eq.get("hypocenter", {})
        try:
            lat = float(hypo["latitude"])
            lon = float(hypo["longitude"])
            if lat == -200 or lon == -200:
                continue
            mag   = float(hypo["magnitude"])
            depth = abs(float(hypo.get("depth", 0)))
            raw_time = eq.get("time", "")
            try:
                now_y  = datetime.now().year
                dt_jst = datetime.strptime(f"{now_y}/{raw_time}", "%Y/%m/%d %H:%M")
                dt_utc = dt_jst.replace(tzinfo=timezone(timedelta(hours=9))) \
                               .astimezone(timezone.utc)
                time_str = dt_utc.isoformat()
            except Exception:
                time_str = raw_time
            quakes.append({
                "time":   time_str,
                "lat":    lat,
                "lon":    lon,
                "mag":    mag,
                "depth":  depth,
                "source": "p2p",
            })
        except Exception:
            continue
    print(f"[P2P] {len(quakes)} 件取得")
    return quakes


# ══════════════════════════════════════════════════════
# 有感地震取得（気象庁 bosai JSON API）
# ══════════════════════════════════════════════════════
JMA_LIST_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"

def _parse_jma_cod(cod_str):
    """ISO6709形式 '+35.6+139.7-50000/' -> (lat, lon, depth_km)"""
    m = re.match(r'([+-][0-9.]+)([+-][0-9.]+)([+-][0-9.]+)?/?', cod_str.strip())
    if not m:
        raise ValueError(f"座標解析失敗: {cod_str}")
    lat   = float(m.group(1))
    lon   = float(m.group(2))
    depth = abs(float(m.group(3))) / 1000.0 if m.group(3) else 0.0
    return lat, lon, depth

def fetch_quakes_jma_bosai():
    """
    気象庁 bosai JSON API から有感地震（震度1以上）を取得。
    https://www.jma.go.jp/bosai/quake/data/list.json
    震源・震度情報（ttl=='震源・震度情報'）かつ maxi が存在する地震のみ。
    """
    try:
        res  = requests.get(JMA_LIST_URL, timeout=10,
                            headers={"User-Agent": "EarthquakeApp/4.0"})
        data = res.json()
    except Exception as e:
        print(f"[JMA-bosai] 取得エラー: {e}")
        return []

    quakes = []
    for item in data:
        # 震源・震度情報のみ（震度速報・震源のみは除外）
        if item.get("ttl") != "震源・震度情報":
            continue
        # 訂正・取消は除外
        if item.get("ift") in ("訂正", "取消"):
            continue
        # 最大震度なし（maxi未定義 or 空）は除外
        maxi = item.get("maxi", "")
        if not maxi:
            continue
        try:
            lat, lon, depth = _parse_jma_cod(item["cod"])
        except Exception:
            continue
        mag_raw = item.get("mag", "")
        try:
            mag = float(mag_raw)
        except Exception:
            mag = 0.0
        # 発生時刻: at フィールド (ISO8601 JST)
        at_str = item.get("at", item.get("rdt", ""))
        try:
            dt = datetime.fromisoformat(at_str)
            time_str = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            time_str = at_str
        place = item.get("anm", "不明")
        quakes.append({
            "time":    time_str,
            "lat":     lat,
            "lon":     lon,
            "mag":     mag,
            "depth":   depth,
            "source":  "jma_bosai",
            "place":   place,
            "max_int": maxi,
        })
    print(f"[JMA-bosai] {len(quakes)} 件取得")
    return quakes


def fetch_quakes_usgs():
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=geojson&starttime={start}"
        "&minlatitude=24&maxlatitude=46"
        "&minlongitude=122&maxlongitude=146"
        "&minmagnitude=1.0&orderby=time&limit=500"
    )
    try:
        res  = requests.get(url, timeout=15)
        data = res.json()
    except Exception as e:
        print(f"[USGS] 取得エラー: {e}")
        return []
    quakes = []
    for feat in data.get("features", []):
        try:
            props  = feat["properties"]
            coords = feat["geometry"]["coordinates"]
            t = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
            quakes.append({
                "time":   t.isoformat(),
                "lat":    float(coords[1]),
                "lon":    float(coords[0]),
                "mag":    float(props["mag"]),
                "depth":  float(coords[2]),
                "source": "usgs",
            })
        except Exception:
            continue
    print(f"[USGS] {len(quakes)} 件取得")
    return quakes

def fetch_all_quakes():
    results = {}
    def _run(name, fn):
        results[name] = fn()
    threads = [
        threading.Thread(target=_run, args=("p2p",      fetch_quakes_p2p)),
        threading.Thread(target=_run, args=("usgs",     fetch_quakes_usgs)),
        threading.Thread(target=_run, args=("jma_bosai", fetch_quakes_jma_bosai)),
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    all_q = (results.get("p2p",[])
             + results.get("usgs",[])
             + results.get("jma_bosai",[]))
    return deduplicate(all_q)

def deduplicate(quakes, time_tol_min=5, dist_tol_deg=0.3):
    priority = {"jma_bosai": 0, "p2p": 1, "usgs": 2}
    quakes_sorted = sorted(quakes, key=lambda q: priority.get(q["source"], 9))
    kept = []
    for q in quakes_sorted:
        try:
            t_q = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
        except Exception:
            t_q = None
        dup = False
        for k in kept:
            try:
                t_k = datetime.fromisoformat(k["time"].replace("Z", "+00:00"))
                dt  = abs((t_q - t_k).total_seconds()) / 60 if t_q and t_k else 999
            except Exception:
                dt = 999
            dist = math.sqrt((q["lat"]-k["lat"])**2 + (q["lon"]-k["lon"])**2)
            if dt < time_tol_min and dist < dist_tol_deg:
                dup = True
                break
        if not dup:
            kept.append(q)
    print(f"[重複排除] {len(quakes)} -> {len(kept)} 件")
    return kept

def save_quakes(quakes):
    os.makedirs("data", exist_ok=True)
    existing = set()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 3:
                    existing.add((row[0], row[1], row[2]))
    new_count = 0
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for q in quakes:
            key = (q["time"], str(q["lat"]), str(q["lon"]))
            if key not in existing:
                writer.writerow([
                    q["time"], q["lat"], q["lon"],
                    q["mag"], q["depth"],
                    q.get("source", "unknown"),
                    q.get("place", ""),
                    q.get("max_int", ""),
                ])
                existing.add(key)
                new_count += 1
    print(f"[保存] {new_count} 件追加")

def load_quakes():
    if not os.path.exists(DATA_FILE):
        return []
    data = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                q = {
                    "time":   row[0],
                    "lat":    float(row[1]),
                    "lon":    float(row[2]),
                    "mag":    float(row[3]),
                    "depth":  float(row[4]),
                    "source": row[5] if len(row) > 5 else "unknown",
                    "place":  row[6] if len(row) > 6 else "",
                    "max_int": row[7] if len(row) > 7 else "",
                }
                data.append(q)
            except Exception:
                continue
    return data



# ══════════════════════════════════════════════════════
# TEC 取得・解析
# ══════════════════════════════════════════════════════
# 取得戦略（優先順）:
#   1. GEONET RINEX（terras.gsi.go.jp）     - 日本特化・高密度（約1300点）
#   2. IONEXファイル（IGS各ミラー）         - グローバルマップ（2.5°×5.0°）
#   3. NOAA Space Weather GOES-TEC JSON API  - フォールバック（全球値のみ）
# ══════════════════════════════════════════════════════

# GEONET代表観測局（日本各地をカバーする常設局）
# terras.gsi.go.jp の /data/GPS_products/ 以下に4文字局名でアクセス可能
GEONET_STATIONS = [
    # 4文字局名, 緯度, 経度
    ("0132", 43.06, 141.35),  # 札幌
    ("0272", 40.82, 141.32),  # 青森
    ("0481", 39.70, 141.14),  # 盛岡
    ("0561", 38.27, 140.87),  # 仙台
    ("0721", 37.42, 140.36),  # 郡山
    ("0891", 36.38, 140.47),  # 水戸
    ("0931", 36.41, 139.74),  # 宇都宮
    ("1021", 36.55, 139.11),  # 前橋
    ("1101", 35.69, 139.75),  # 東京
    ("1211", 35.18, 136.90),  # 名古屋
    ("1301", 35.01, 135.73),  # 京都
    ("1361", 34.69, 135.50),  # 大阪
    ("1501", 34.39, 132.46),  # 広島
    ("1601", 33.59, 130.42),  # 福岡
    ("1701", 31.60, 130.56),  # 鹿児島
    ("1801", 26.33, 127.81),  # 那覇
    ("0601", 37.91, 139.06),  # 新潟
    ("0811", 36.70, 137.21),  # 富山
    ("1141", 34.35, 134.05),  # 高松
    ("1461", 33.55, 133.53),  # 高知
]
GEONET_CACHE_DIR = "data/geonet_rinex"


# ──────────────────────────────────────────────────────
# GEONET RINEX → TEC 計算
# ──────────────────────────────────────────────────────

def _geonet_rinex_url(station: str, dt: datetime) -> list:
    """
    GEONET電子基準点のRINEXファイルURLリストを返す。
    terras.gsi.go.jp の公開FTPをHTTPSで取得する。
    ファイル命名: {STATION}{DOY}0.{YY}o.gz  (RINEX 2.11)
    ディレクトリ: /data/GPS_products/{YYYY}/{DOY:03d}/
    """
    doy  = dt.timetuple().tm_yday
    yy   = dt.strftime("%y")
    yyyy = dt.strftime("%Y")
    base = "https://terras.gsi.go.jp/data/GPS_products"
    fname_obs = f"{station}{doy:03d}0.{yy}o.gz"
    fname_nav = f"brdc{doy:03d}0.{yy}n.gz"   # 放送暦（共通）
    url_obs = f"{base}/{yyyy}/{doy:03d}/{fname_obs}"
    url_nav = f"{base}/{yyyy}/{doy:03d}/{fname_nav}"
    return url_obs, url_nav, fname_obs, fname_nav


def _download_geonet_file(url: str, cache_path: str) -> bytes | None:
    """単一ファイルをキャッシュ付きでダウンロード。生バイト列を返す。"""
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 200:
        with open(cache_path, "rb") as f:
            return f.read()
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "GEONETClient/1.0 (research)"})
        if r.status_code != 200:
            return None
        data = r.content
        with open(cache_path, "wb") as f:
            f.write(data)
        return data
    except Exception as e:
        print(f"[GEONET] DL失敗 {url}: {e}")
        return None


def _decompress_gz(data: bytes) -> str | None:
    """gzip圧縮バイト列を文字列に展開。"""
    try:
        if data[:2] == b"\x1f\x8b":
            with gzip.open(io.BytesIO(data), "rt",
                           encoding="ascii", errors="ignore") as gz:
                return gz.read()
        return data.decode("ascii", errors="ignore")
    except Exception:
        return None


def _parse_rinex2_obs_tec(obs_text: str) -> list:
    """
    RINEX 2.11 観測ファイルから擬似距離P1/P2を読み取り、
    局ごとのスラント TEC 時系列を返す。

    TEC[TECU] = (P2 - P1) / (40.3 * (1/f1^2 - 1/f2^2))
    f1 = 1575.42 MHz, f2 = 1227.60 MHz (GPS L1/L2)
    戻り値: list of {"epoch": datetime(UTC), "stec": float[TECU]}
    """
    F1 = 1575.42e6   # GPS L1 [Hz]
    F2 = 1227.60e6   # GPS L2 [Hz]
    K  = 40.3        # [m・TECU / electron・m^-2]
    # TEC factor: STEC = (P2-P1) * f1^2*f2^2 / (K*(f1^2-f2^2)) [TECU]
    SCALE = F1**2 * F2**2 / (K * (F1**2 - F2**2)) / 1e16

    lines = obs_text.splitlines()
    i = 0
    obs_types = []
    p1_idx = p2_idx = -1

    # ヘッダ解析
    while i < len(lines):
        line = lines[i]
        if "# / TYPES OF OBSERV" in line:
            parts = line.split()
            try:
                n = int(parts[0])
                types = parts[1:1+n]
                # 次行に続く場合
                j = i + 1
                while len(types) < n and j < len(lines):
                    if "# / TYPES OF OBSERV" in lines[j]:
                        types += lines[j].split()[:-3]
                    j += 1
                obs_types = types
                if "P1" in obs_types: p1_idx = obs_types.index("P1")
                if "P2" in obs_types: p2_idx = obs_types.index("P2")
                if "C1" in obs_types and p1_idx < 0: p1_idx = obs_types.index("C1")
            except Exception:
                pass
        if "END OF HEADER" in line:
            i += 1
            break
        i += 1

    if p1_idx < 0 or p2_idx < 0:
        return []

    results = []
    while i < len(lines):
        line = lines[i]
        if len(line) < 26:
            i += 1
            continue
        # エポックヘッダ: yy mm dd hh mm ss.sss  n_sat  ...
        try:
            yy2 = int(line[1:3]);   mo = int(line[4:6]);   dy = int(line[7:9])
            hr  = int(line[10:12]); mi = int(line[13:15]); sc = float(line[15:26])
            year = 2000 + yy2 if yy2 < 80 else 1900 + yy2
            epoch = datetime(year, mo, dy, hr, mi, int(sc), tzinfo=timezone.utc)
            n_sv  = int(line[29:32])
        except Exception:
            i += 1
            continue

        # 衛星リスト（1行29字+12衛星まで）
        sv_line = line[32:68]
        extra_lines = math.ceil(n_sv / 12) - 1
        i += 1
        for _ in range(extra_lines):
            if i < len(lines):
                sv_line += lines[i][32:68]
                i += 1

        stec_list = []
        n_obs = len(obs_types)
        for sv_i in range(n_sv):
            # 各衛星: ceil(n_obs/5) 行
            obs_vals = []
            for row in range(math.ceil(n_obs / 5)):
                if i < len(lines):
                    obs_line = lines[i].ljust(80)
                    i += 1
                    for col in range(5):
                        start = col * 16
                        val_str = obs_line[start:start+14].strip()
                        try:
                            obs_vals.append(float(val_str))
                        except Exception:
                            obs_vals.append(float("nan"))
                else:
                    obs_vals.extend([float("nan")] * 5)

            try:
                p1 = obs_vals[p1_idx]
                p2 = obs_vals[p2_idx]
                if not (math.isnan(p1) or math.isnan(p2) or p1 == 0 or p2 == 0):
                    stec = abs(p2 - p1) * abs(SCALE)
                    # 合理範囲チェック (1-300 TECU)
                    if 1.0 < stec < 300.0:
                        stec_list.append(stec)
            except IndexError:
                pass

        if stec_list:
            results.append({
                "epoch": epoch,
                "stec":  float(np.median(stec_list)),  # 複数衛星の中央値
            })

    return results


def _fetch_geonet_tec(dt: datetime) -> dict | None:
    """
    GEONETの複数代表局からRINEXを取得し、
    日本周辺グリッドのTECマップとZスコアを返す。

    処理フロー:
      1. 各局のRINEX観測ファイル（O型）をterras.gsi.go.jpから取得
      2. P1/P2擬似距離差からスラントTECを計算
      3. 現在エポックに最も近い値を各局から抽出
      4. 空間補間（逆距離加重）でグリッドマップを生成
      5. 過去7日間の同時刻値でZスコアを算出
    """
    os.makedirs(GEONET_CACHE_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)

    def _fetch_station_tec(station_info):
        station, slat, slon = station_info
        url_obs, url_nav, fname_obs, fname_nav = _geonet_rinex_url(station, dt)
        cache_obs = os.path.join(GEONET_CACHE_DIR,
                                 f"{station}_{dt.strftime('%Y%m%d')}.obs.gz")
        raw = _download_geonet_file(url_obs, cache_obs)
        if raw is None:
            return None
        text = _decompress_gz(raw)
        if not text or "RINEX" not in text[:200]:
            return None
        series = _parse_rinex2_obs_tec(text)
        if not series:
            return None
        # 現在時刻に最も近いエポックの値
        best = min(series, key=lambda x: abs((x["epoch"] - now).total_seconds()))
        if abs((best["epoch"] - now).total_seconds()) > 7200:
            return None  # 2時間以上ずれていたら棄却
        return {"station": station, "lat": slat, "lon": slon,
                "stec": best["stec"], "epoch": best["epoch"]}

    # 並列取得（最大8局同時）
    station_data = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_station_tec, s): s for s in GEONET_STATIONS}
        for f in as_completed(futures):
            r = f.result()
            if r:
                station_data.append(r)

    if len(station_data) < 3:
        print(f"[GEONET] 有効局数不足: {len(station_data)}局")
        return None

    print(f"[GEONET] {len(station_data)}局からTEC取得成功")

    # ── グリッドへの逆距離加重補間 (IDW) ──
    lat_arr = np.arange(24.0, 47.5, 1.0)   # 0.1度→1.0度（局数に合わせて粗め）
    lon_arr = np.arange(122.0, 147.0, 1.0)
    tec_grid = np.full((len(lat_arr), len(lon_arr)), np.nan)

    lats  = np.array([d["lat"]  for d in station_data])
    lons  = np.array([d["lon"]  for d in station_data])
    stecs = np.array([d["stec"] for d in station_data])

    for i, lat in enumerate(lat_arr):
        for j, lon in enumerate(lon_arr):
            dists = np.sqrt((lats - lat)**2 + (lons - lon)**2)
            dists = np.maximum(dists, 0.01)
            weights = 1.0 / dists**2
            tec_grid[i, j] = np.sum(weights * stecs) / np.sum(weights)

    # ── 過去7日間の同時刻帯でZスコア計算 ──
    history_stack = []
    current_hour  = now.hour
    for d in range(1, TEC_HISTORY_DAYS + 1):
        past_dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) \
                  - timedelta(days=d)

        def _past_station(s_info):
            station, slat, slon = s_info
            url_obs, _, fname_obs, _ = _geonet_rinex_url(station, past_dt)
            cache_p = os.path.join(GEONET_CACHE_DIR,
                                   f"{station}_{past_dt.strftime('%Y%m%d')}.obs.gz")
            raw = _download_geonet_file(url_obs, cache_p)
            if raw is None: return None
            text = _decompress_gz(raw)
            if not text: return None
            series = _parse_rinex2_obs_tec(text)
            # 当日の同時刻帯（±1時間）のエポックを抽出
            target = past_dt.replace(hour=current_hour)
            close  = [x for x in series
                      if abs((x["epoch"]-target).total_seconds()) <= 3600]
            if not close: return None
            med = float(np.median([c["stec"] for c in close]))
            return {"lat": slat, "lon": slon, "stec": med}

        past_data = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_past_station, s): s for s in GEONET_STATIONS}
            for f in as_completed(futs):
                r = f.result()
                if r: past_data.append(r)

        if len(past_data) < 3:
            continue

        # 同じグリッドに補間
        pg = np.full((len(lat_arr), len(lon_arr)), np.nan)
        pl = np.array([d["lat"]  for d in past_data])
        po = np.array([d["lon"]  for d in past_data])
        ps = np.array([d["stec"] for d in past_data])
        for i, lat in enumerate(lat_arr):
            for j, lon in enumerate(lon_arr):
                dists   = np.maximum(np.sqrt((pl-lat)**2 + (po-lon)**2), 0.01)
                weights = 1.0 / dists**2
                pg[i, j] = np.sum(weights * ps) / np.sum(weights)
        history_stack.append(pg)

    if len(history_stack) >= 3:
        history_arr = np.stack(history_stack, axis=0)
        mean_tec    = np.nanmean(history_arr, axis=0)
        std_tec     = np.maximum(np.nanstd(history_arr, axis=0), 0.5)
        zscore      = (tec_grid - mean_tec) / std_tec
        status      = (f"GEONETモード ({len(station_data)}局, "
                       f"Zスコア 過去{len(history_stack)}日)")
    else:
        mean_g = np.nanmean(tec_grid)
        std_g  = max(np.nanstd(tec_grid), 0.5)
        zscore = (tec_grid - mean_g) / std_g
        status = f"GEONETモード ({len(station_data)}局, 絶対値正規化)"

    epoch = station_data[0]["epoch"]
    print(f"[TEC] {status}")
    return {
        "zscore":  zscore,
        "lat_arr": lat_arr,
        "lon_arr": lon_arr,
        "tec_now": tec_grid,
        "epoch":   epoch,
        "status":  status,
        "source":  "geonet",
    }



def _ionex_candidates(dt):
    """
    日付に対応するIONEXファイルの(URL, ファイル名)リストを返す。
    IGS長名フォーマット（2022年以降）と旧短名フォーマット両方を含む。
    認証不要の公開ミラーのみ。
    """
    doy  = dt.timetuple().tm_yday
    yy   = dt.strftime("%y")
    yyyy = dt.strftime("%Y")

    long_date = f"{yyyy}{doy:03d}0000"

    candidates = []

    # ── IGS長名フォーマット（2022年以降の標準）──
    long_providers = [
        ("JPL0OPSFIN", "02H"),
        ("COD0OPSFIN", "01H"),
        ("ESA0OPSFIN", "02H"),
        ("IGS0OPSFIN", "02H"),
    ]
    long_mirrors = [
        "https://igs.ign.fr/pub/igs/products/ionex/{yyyy}/{doy:03d}/{fname}",
        "https://igs.bkg.bund.de/root_ftp/IGS/products/ionosphere/{yyyy}/{doy:03d}/{fname}",
    ]
    for provider, interval in long_providers:
        fname = f"{provider}_{long_date}_01D_{interval}_GIM.INX.gz"
        for mirror in long_mirrors:
            url = mirror.format(yyyy=yyyy, doy=doy, fname=fname)
            candidates.append((url, fname))

    # ── 旧短名フォーマット（後方互換・一部ミラーで現役）──
    short_providers = ["jplg", "codg", "esag", "igsg", "upcg", "whug"]
    short_mirrors = [
        "https://igs.ign.fr/pub/igs/products/ionex/{yyyy}/{doy:03d}/{fname}",
        "https://igs.bkg.bund.de/root_ftp/IGS/products/ionosphere/{yyyy}/{doy:03d}/{fname}",
        "https://ftp.aiub.unibe.ch/CODE/{yyyy}/{fname}",
        "https://cddis.nasa.gov/archive/gnss/products/ionex/{yyyy}/{doy:03d}/{fname}",
    ]
    for provider in short_providers:
        fname = f"{provider}{doy:03d}0.{yy}i.gz"
        for mirror in short_mirrors:
            url = mirror.format(yyyy=yyyy, doy=doy, fname=fname)
            candidates.append((url, fname))

    return candidates


def _download_ionex(dt):
    """
    IONEXファイルをキャッシュから読むか複数ミラーから取得。
    成功したテキストを返す。全失敗時はNone。
    """
    os.makedirs(IONEX_CACHE_DIR, exist_ok=True)
    date_str   = dt.strftime("%Y%m%d")
    cache_path = os.path.join(IONEX_CACHE_DIR, f"tec_{date_str}.ionex")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="ascii", errors="ignore") as f:
            content = f.read()
        if len(content) > 1000:
            return content

    for url, fname in _ionex_candidates(dt):
        try:
            r = requests.get(url, timeout=25,
                             headers={"User-Agent": "IGSClient/1.0 (research)"})
            if r.status_code != 200:
                continue
            raw = r.content
            # gzip判定（マジックバイト）
            if raw[:2] == b"\x1f\x8b":
                with gzip.open(io.BytesIO(raw), "rt",
                               encoding="ascii", errors="ignore") as gz:
                    text = gz.read()
            elif raw[:2] == b"\x1f\x9d":   # compress (.Z)
                text = raw.decode("ascii", errors="ignore")
            else:
                text = raw.decode("ascii", errors="ignore")

            if "IONEX" not in text[:500]:
                continue

            with open(cache_path, "w", encoding="ascii") as f:
                f.write(text)
            print(f"[IONEX] 取得成功: {fname} <- {url}")
            return text
        except Exception:
            continue

    print(f"[IONEX] {date_str} 全ミラー失敗")
    return None


def _fetch_noaa_tec_fallback():
    """
    フォールバック: NOAA SWPC の公開 JSON から
    宇宙天気指数（Kp・Dst）を取得し、
    日本周辺のTEC代替グリッドを簡易モデルで構築する。

    Kp: 地磁気擾乱指数（0-9）  高い -> 電離層撹乱が強い
    Dst: 磁気嵐指数（nT）       負に大きい -> 磁気嵐

    簡易モデル: TEC_anomaly(lat, lon) = Kp_factor * lat_sensitivity(lat)
    これはあくまで「全球的な撹乱強度」の空間分配であり、
    局所的な精度はIONEXより低い。
    """
    try:
        # Kp指数（直近3時間値）
        r_kp = requests.get(
            "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
            timeout=10)
        kp_data = r_kp.json()
        # 最新値
        kp_val = float(kp_data[-1]["kp_index"]) if kp_data else 2.0
    except Exception:
        kp_val = 2.0

    try:
        # Dst指数（磁気嵐）
        r_dst = requests.get(
            "https://services.swpc.noaa.gov/json/geospace/dst_1_hour.json",
            timeout=10)
        dst_data = r_dst.json()
        dst_val = float(dst_data[-1]["dst"]) if dst_data else 0.0
    except Exception:
        dst_val = 0.0

    # 日本周辺グリッド（2.5x5度、IONEXと同解像度）
    lat_arr = np.arange(22.5, 47.6, 2.5)
    lon_arr = np.arange(120.0, 146.1, 5.0)
    n_lat, n_lon = len(lat_arr), len(lon_arr)

    # 簡易電離層モデル:
    # - Kpが高いほど中高緯度でTEC撹乱が増大
    # - 磁気嵐(Dst < -30nT)で追加ブースト
    # - 日本は中緯度(30-45度)なので中程度の感度
    tec_grid = np.zeros((n_lat, n_lon))
    storm_boost = max(0, -dst_val / 50.0)  # Dst=-50nT -> +1.0

    for i, lat in enumerate(lat_arr):
        # 中高緯度感度: 緯度30-50度でピーク
        lat_factor = np.exp(-((lat - 40.0) ** 2) / (2 * 15.0 ** 2))
        for j, lon in enumerate(lon_arr):
            tec_grid[i, j] = (kp_val / 4.0) * lat_factor + storm_boost * lat_factor

    # Zスコア代替（平均0, 標準偏差1 に正規化）
    mean = np.mean(tec_grid)
    std  = np.std(tec_grid) + 1e-6
    zscore = (tec_grid - mean) / std

    status = (f"NOAA SWPC 代替モード "
              f"(Kp={kp_val:.1f}, Dst={dst_val:.0f}nT)")
    print(f"[TEC] {status}")
    return {
        "zscore":  zscore,
        "lat_arr": lat_arr,
        "lon_arr": lon_arr,
        "tec_now": tec_grid,
        "epoch":   datetime.now(timezone.utc),
        "status":  status,
        "source":  "noaa_fallback",
    }


def compute_tec_zscore():
    """
    TEC Zスコアを計算して返す。
    優先順: GEONET（日本特化）-> IGS IONEX（全球）-> NOAA SWPC（代替）
    """
    now      = datetime.now(timezone.utc)
    today_dt = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    # ── 1. GEONET RINEX から計算（最優先）──
    geonet_result = _fetch_geonet_tec(today_dt)
    if geonet_result:
        return geonet_result

    print("[TEC] GEONET失敗 -> IGS IONEXを試みる")

    # ── 2. IGS IONEX フォールバック ──
    today_text = _download_ionex(today_dt)
    if today_text:
        today_maps = _parse_ionex(today_text)
        if today_maps:
            current_map  = min(today_maps,
                               key=lambda m: abs((m["epoch"]-now).total_seconds()))
            tec_now      = current_map["tec"]
            lat_arr      = current_map["lat_arr"]
            lon_arr      = current_map["lon_arr"]
            current_hour = current_map["epoch"].hour

            history_stack = []
            for d in range(1, TEC_HISTORY_DAYS + 1):
                past_text = _download_ionex(today_dt - timedelta(days=d))
                if not past_text:
                    continue
                for pm in _parse_ionex(past_text):
                    if abs(pm["epoch"].hour - current_hour) <= 1:
                        history_stack.append(pm["tec"])

            if len(history_stack) >= 3:
                history_arr = np.stack(history_stack, axis=0)
                mean_tec    = np.nanmean(history_arr, axis=0)
                std_tec     = np.maximum(np.nanstd(history_arr, axis=0), 0.5)
                zscore      = (tec_now - mean_tec) / std_tec
                status      = f"IGS IONEXモード (Zスコア, 過去{len(history_stack)}エポック)"
                source      = "ionex"
            else:
                mean_g = np.nanmean(tec_now)
                std_g  = max(np.nanstd(tec_now), 0.5)
                zscore = (tec_now - mean_g) / std_g
                status = "IGS IONEXモード (絶対値正規化, 過去データ不足)"
                source = "ionex"

            # 日本周辺に絞り込む
            lat_mask = (lat_arr >= 22) & (lat_arr <= 48)
            lon_mask = (lon_arr >= 120) & (lon_arr <= 148)
            lat_jp   = lat_arr[lat_mask]
            lon_jp   = lon_arr[lon_mask]
            li       = np.where(lat_mask)[0]
            lj       = np.where(lon_mask)[0]

            print(f"[TEC] {status}")
            return {
                "zscore":  zscore[np.ix_(li, lj)],
                "lat_arr": lat_jp,
                "lon_arr": lon_jp,
                "tec_now": tec_now[np.ix_(li, lj)],
                "epoch":   current_map["epoch"],
                "status":  status,
                "source":  source,
            }

    # ── 3. NOAA SWPCフォールバック ──
    print("[TEC] IONEXすべて失敗 -> NOAA SWPCで代替")
    return _fetch_noaa_tec_fallback()

def _parse_ionex(text):
    """
    IONEXテキストから全TECマップを解析する。
    戻り値: list of dict { "epoch": datetime, "tec": np.ndarray(lat, lon) }
    グリッド: lat 87.5 -> -87.5 (2.5度刻み), lon -180 -> 180 (5.0度刻み)
    """
    maps = []
    lines = text.splitlines()
    i = 0
    lat_arr = None
    lon_arr = None

    while i < len(lines):
        line = lines[i]

        # グリッド定義を読み込む（最初の1回）
        if "LAT1 / LAT2 / DLAT" in line:
            parts = line.split()
            lat1, lat2, dlat = float(parts[0]), float(parts[1]), float(parts[2])
            if lat_arr is None:
                lat_arr = np.arange(lat1, lat2 + dlat * 0.5, dlat)
                if dlat < 0:
                    lat_arr = np.arange(lat1, lat2 + dlat * 0.5, dlat)

        if "LON1 / LON2 / DLON" in line:
            parts = line.split()
            lon1, lon2, dlon = float(parts[0]), float(parts[1]), float(parts[2])
            if lon_arr is None:
                lon_arr = np.arange(lon1, lon2 + dlon * 0.5, dlon)

        # TECマップの開始
        if "START OF TEC MAP" in line:
            i += 1
            # エポック行
            epoch_line = lines[i]
            parts = epoch_line.split()
            try:
                yr, mo, dy, hr, mi, sc = int(parts[0]), int(parts[1]), int(parts[2]), \
                                          int(parts[3]), int(parts[4]), int(float(parts[5]))
                epoch = datetime(yr, mo, dy, hr, mi, sc, tzinfo=timezone.utc)
            except Exception:
                i += 1
                continue

            if lat_arr is None or lon_arr is None:
                i += 1
                continue

            n_lat = len(lat_arr)
            n_lon = len(lon_arr)
            tec_map = np.full((n_lat, n_lon), np.nan)
            row_idx = 0

            i += 1
            while i < len(lines) and "END OF TEC MAP" not in lines[i]:
                if "LAT/LON1/LON2/DLON/H" in lines[i]:
                    # 緯度ヘッダ行
                    i += 1
                    # TECデータ行（1行に16値まで）
                    col_idx = 0
                    while i < len(lines) and "LAT/LON1/LON2/DLON/H" not in lines[i] \
                          and "END OF TEC MAP" not in lines[i]:
                        vals = lines[i].split()
                        for v in vals:
                            if col_idx < n_lon:
                                try:
                                    tec_map[row_idx, col_idx] = float(v) * 0.1  # 0.1 TECU単位
                                except ValueError:
                                    pass
                                col_idx += 1
                        i += 1
                    row_idx += 1
                else:
                    i += 1

            maps.append({"epoch": epoch, "tec": tec_map,
                         "lat_arr": lat_arr, "lon_arr": lon_arr})
            continue

        i += 1

    return maps




# ══════════════════════════════════════════════════════
# ETAS 解析（NumPy 行列演算）
# ══════════════════════════════════════════════════════
def analyze_etas(quakes):
    if not quakes:
        return {}

    now = datetime.now(timezone.utc)
    valid = []
    for q in quakes:
        if q["mag"] < EP.M0:
            continue
        try:
            t  = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
            dt = max((now - t).total_seconds() / 86400, 1e-3)
        except Exception:
            dt = 1.0
        valid.append((q["lat"], q["lon"], q["mag"], dt, q.get("depth", 10.0)))

    if not valid:
        return {}

    lats   = np.array([v[0] for v in valid])
    lons   = np.array([v[1] for v in valid])
    mags   = np.array([v[2] for v in valid])
    t_days = np.array([v[3] for v in valid])
    depths = np.array([v[4] for v in valid])
    N = len(valid)

    time_kernel   = 1.0 / (t_days + EP.C) ** EP.P
    depth_factor  = 1.0 / (1.0 + (depths / EP.DEPTH_SCALE) ** 2)
    mag_scale     = EP.K * np.exp(EP.ALPHA * (mags - EP.M0))
    contrib       = mag_scale * time_kernel * depth_factor
    spatial_scale = EP.D * np.exp(EP.GAMMA * mags)

    R = EP.SPACE_RADIUS
    gi = np.round(lats / GRID_SIZE).astype(int)
    gj = np.round(lons / GRID_SIZE).astype(int)

    di_arr = np.arange(-R, R+1)
    dj_arr = np.arange(-R, R+1)
    DI, DJ = np.meshgrid(di_arr, dj_arr, indexing="ij")
    cos_lat = math.cos(math.radians(36))
    r2_grid = (DI * GRID_SIZE * 111.0) ** 2 + (DJ * GRID_SIZE * 111.0 * cos_lat) ** 2

    G = (2*R+1)**2
    r2_flat     = r2_grid.reshape(1, G)
    ss_col      = spatial_scale.reshape(N, 1)
    contrib_col = contrib.reshape(N, 1)
    space_kernel = 1.0 / (r2_flat + ss_col) ** EP.Q
    influence    = contrib_col * space_kernel

    gi_col  = gi.reshape(N, 1)
    gj_col  = gj.reshape(N, 1)
    DI_flat = DI.reshape(1, G)
    DJ_flat = DJ.reshape(1, G)
    grid_gi = (gi_col + DI_flat).reshape(-1)
    grid_gj = (gj_col + DJ_flat).reshape(-1)
    vals    = influence.reshape(-1)

    mask    = vals > 1e-12
    grid_gi = grid_gi[mask]
    grid_gj = grid_gj[mask]
    vals    = vals[mask]

    OFFSET_I = 1000
    OFFSET_J = 100
    keys_int = (grid_gi + OFFSET_I) * 10000 + (grid_gj + OFFSET_J)
    unique_keys, inverse = np.unique(keys_int, return_inverse=True)
    agg_vals = np.zeros(len(unique_keys))
    np.add.at(agg_vals, inverse, vals)
    agg_vals += EP.MU

    grid_scores = {}
    for k, v in zip(unique_keys, agg_vals):
        gi_k = int(k // 10000) - OFFSET_I
        gj_k = int(k  % 10000) - OFFSET_J
        grid_scores[(gi_k, gj_k)] = float(v)

    return grid_scores


# ══════════════════════════════════════════════════════
# マップ生成ヘルパー
# ══════════════════════════════════════════════════════
def _percentile_thresholds(values_arr):
    """
    レベル閾値（パーセンタイルベース）
      Level 5: 上位 0.2% （M6相当以上）
      Level 4: 上位 1.5% （M5.5相当以上）
      Level 3: 上位 5.0% （M5前後相当）
      Level 2: 上位 15%  （現状維持）
      Level 1: 上位 50%  （現状維持）
    """
    log_v = np.log(np.clip(values_arr, 0, None) + 1)
    return (
        np.percentile(log_v, 99.8),   # Level 5
        np.percentile(log_v, 98.5),   # Level 4
        np.percentile(log_v, 95.0),   # Level 3
        np.percentile(log_v, 85.0),   # Level 2
        np.percentile(log_v, 50.0),   # Level 1
    )

ETAS_COLOR = {5: "#1a0033", 4: "#8000ff", 3: "red", 2: "orange", 1: "#66ccff"}
TEC_COLOR  = {5: "#4b0000", 4: "#cc0000", 3: "#ff6600", 2: "#ffcc00", 1: "#ffffcc"}
COMB_COLOR = {5: "#0d001a", 4: "#660099", 3: "#cc0033", 2: "#ff6600", 1: "#ffff99"}


def _base_map():
    m = folium.Map(
        location=[36, 138],
        zoom_start=5,
        tiles="CartoDB dark_matter",
        prefer_canvas=True
    )

    # 都道府県境のみ表示
    folium.GeoJson(
        "https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson",
        style_function=lambda x: {
            "fillOpacity": 0,
            "color": "#666666",
            "weight": 1.2,
        }
    ).add_to(m)

    return m


# ── ① ETASマップ ──────────────────────────────────────
def create_etas_map(grid_scores, quakes, updated_str):
    m = _base_map()

    src_count = {}
    for q in quakes:
        src_count[q.get("source","?")] = src_count.get(q.get("source","?"), 0) + 1

    if grid_scores:
        vals = np.array(list(grid_scores.values()))
        th5, th4, th3, th2, th1 = _percentile_thresholds(vals)
        for (gi, gj), score in grid_scores.items():
            s = math.log(score + 1)
            if   s >= th5: lv = 5
            elif s >= th4: lv = 4
            elif s >= th3: lv = 3
            elif s >= th2: lv = 2
            elif s >= th1: lv = 1
            else: continue
            lat = gi * GRID_SIZE
            lon = gj * GRID_SIZE
            folium.Rectangle(
                bounds=[[lat, lon], [lat+GRID_SIZE, lon+GRID_SIZE]],
                color=None, fill=True,
                fill_color=ETAS_COLOR[lv], fill_opacity=0.65,
                tooltip=f"ETAS Level {lv} | rate={score:.4f}",
            ).add_to(m)

    legend = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px;border-radius:8px;
                border:2px solid #8800cc;font-size:13px;line-height:2.0;">
      <b>&#9312; ETAS 地震発生確率</b><br>
      <span style="color:#1a0033;">&#9632;</span> Level 5（上位0.2%）<br>
      <span style="color:#8000ff;">&#9632;</span> Level 4（上位1.5%）<br>
      <span style="color:red;">&#9632;</span> Level 3（上位5.0%）<br>
      <span style="color:orange;">&#9632;</span> Level 2（上位15%）<br>
      <span style="color:#66ccff;">&#9632;</span> Level 1（上位50%）<br>
      <hr style="margin:4px 0;">
      <small>空間: べき乗則(q={EP.Q}) / 時間: Omori-Utsu(p={EP.P})<br>
      深さ補正あり / 背景活動率={EP.MU}<br>
      JMA:{src_count.get('jma_bosai',0)} P2P:{src_count.get('p2p',0)} USGS:{src_count.get('usgs',0)}<br>
      計{len(quakes)}件 | {updated_str}</small>
    </div>"""
    m.get_root().html.add_child(Element(legend))
    return m


# ── ② TEC撹乱マップ ───────────────────────────────────
def create_tec_map(tec_result, updated_str):
    m = _base_map()

    if tec_result is None:
        note = """
        <div style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
                    z-index:1000;background:white;padding:20px;border-radius:8px;
                    border:2px solid red;font-size:14px;">
          TEC データの取得に失敗しました。<br>
          ネットワーク接続またはCDDISサーバーを確認してください。
        </div>"""
        m.get_root().html.add_child(Element(note))
        return m

    zscore  = tec_result["zscore"]    # (n_lat, n_lon)
    lat_arr = tec_result["lat_arr"]   # 降順（87.5 -> -87.5）
    lon_arr = tec_result["lon_arr"]
    epoch   = tec_result["epoch"]
    status  = tec_result["status"]

    # グリッドセルを描画（IONEXは2.5x5度なので大きめ矩形）
    dlat = abs(lat_arr[1] - lat_arr[0]) if len(lat_arr) > 1 else 2.5
    dlon = abs(lon_arr[1] - lon_arr[0]) if len(lon_arr) > 1 else 5.0

    # Zスコアを0~1に正規化してレベル分類
    z_flat = zscore.flatten()
    z_flat = z_flat[~np.isnan(z_flat)]
    if len(z_flat) == 0:
        return m

    # レベル閾値: Zスコアの絶対値で分類（撹乱の大きさ）
    z_abs = np.abs(zscore)

    for i, lat in enumerate(lat_arr):
        for j, lon in enumerate(lon_arr):
            z = z_abs[i, j]
            if np.isnan(z):
                continue
            # Zスコア閾値でレベル分類
            if   z >= 3.0: lv = 5
            elif z >= 2.0: lv = 4
            elif z >= 1.5: lv = 3
            elif z >= 1.0: lv = 2
            elif z >= 0.5: lv = 1
            else: continue

            # lat_arrが降順の場合、矩形の南端を調整
            lat_s = min(lat, lat - dlat) if dlat > 0 else lat + dlat
            lat_n = max(lat, lat + dlat) if dlat > 0 else lat
            lon_w = lon
            lon_e = lon + dlon

            folium.Rectangle(
                bounds=[[lat_s, lon_w], [lat_n, lon_e]],
                color=None, fill=True,
                fill_color=TEC_COLOR[lv], fill_opacity=0.6,
                tooltip=f"TEC Level {lv} | Z={zscore[i,j]:.2f} TECU",
            ).add_to(m)

    epoch_str = epoch.strftime("%Y-%m-%d %H:%M UTC")
    src_label = {
        "geonet":        "GEONET 電子基準点 RINEX（国土地理院）",
        "ionex":         "IGS IONEX（JPL/CODE/ESA GNSS網）",
        "noaa_fallback": "NOAA SWPC（Kp/Dst指数代替モデル）",
    }.get(tec_result.get("source", ""), "不明")
    resolution = {
        "geonet": "1.0°×1.0°（IDW補間）",
        "ionex":  "2.5°×5.0°",
    }.get(tec_result.get("source", ""), "-")
    legend = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px;border-radius:8px;
                border:2px solid #cc0000;font-size:13px;line-height:2.0;">
      <b>&#9313; TEC 電離層撹乱（参考）</b><br>
      <span style="color:#4b0000;">&#9632;</span> Level 5（|Z|&ge;3.0）<br>
      <span style="color:#cc0000;">&#9632;</span> Level 4（|Z|&ge;2.0）<br>
      <span style="color:#ff6600;">&#9632;</span> Level 3（|Z|&ge;1.5）<br>
      <span style="color:#ffcc00;">&#9632;</span> Level 2（|Z|&ge;1.0）<br>
      <span style="color:#ffffcc;">&#9632;</span> Level 1（|Z|&ge;0.5）<br>
      <hr style="margin:4px 0;">
      <small>ソース: {src_label}<br>
      指標: {status}<br>
      解像度: {resolution} | Epoch: {epoch_str}<br>
      &#x26A0; 参考指標（地震との因果関係未確定）<br>
      {updated_str}</small>
    </div>"""
    m.get_root().html.add_child(Element(legend))
    return m


# ── ③ 統合リスクマップ ────────────────────────────────
def create_combined_map(grid_scores, tec_result, quakes, updated_str):
    """
    ETAS スコアと TEC Zスコアをグリッドレベルでブレンドしてリスクを算出。

    統合スコア = (1 - w) * etas_norm + w * tec_norm
    ただし w = TEC_WEIGHT (デフォルト0.4)
    両スコアは [0, 1] に正規化してから合算。
    """
    m = _base_map()

    # ── ETAS を正規化 ──
    etas_norm = {}
    if grid_scores:
        vals = np.array(list(grid_scores.values()))
        v_log = np.log(vals + 1)
        v_min, v_max = v_log.min(), v_log.max()
        denom = v_max - v_min if v_max > v_min else 1.0
        for k, v in grid_scores.items():
            etas_norm[k] = (math.log(v + 1) - v_min) / denom

    # ── TEC Zスコアを 0.1度グリッドに補間して正規化 ──
    tec_norm_grid = {}
    has_tec = False
    if tec_result is not None:
        lat_arr = tec_result["lat_arr"]
        lon_arr = tec_result["lon_arr"]
        z_abs   = np.abs(tec_result["zscore"])

        # Zスコアの最大値で正規化（最大3.0を1.0に対応）
        z_max = max(np.nanmax(z_abs), 3.0)

        for i, lat in enumerate(lat_arr):
            for j, lon in enumerate(lon_arr):
                z = z_abs[i, j]
                if np.isnan(z):
                    continue
                z_n = min(z / z_max, 1.0)
                # 2.5x5度セルを0.1度グリッドに展開
                dlat_half = 1.25
                dlon_half = 2.5
                for sub_lat in np.arange(lat - dlat_half, lat + dlat_half, GRID_SIZE):
                    for sub_lon in np.arange(lon, lon + dlon_half * 2, GRID_SIZE):
                        gi = int(round(sub_lat / GRID_SIZE))
                        gj = int(round(sub_lon / GRID_SIZE))
                        tec_norm_grid[(gi, gj)] = z_n
        has_tec = True

    # ── 統合スコアを計算 ──
    all_keys = set(etas_norm.keys()) | set(tec_norm_grid.keys())
    combined = {}
    w = TEC_WEIGHT
    for k in all_keys:
        e = etas_norm.get(k, 0.0)
        t = tec_norm_grid.get(k, 0.0)
        combined[k] = (1.0 - w) * e + w * t

    if not combined:
        return m

    # ── 描画 ──
    vals = np.array(list(combined.values()))
    v_pct = [np.percentile(vals, p) for p in [98, 95, 85, 70, 40]]

    for (gi, gj), score in combined.items():
        if   score >= v_pct[0]: lv = 5
        elif score >= v_pct[1]: lv = 4
        elif score >= v_pct[2]: lv = 3
        elif score >= v_pct[3]: lv = 2
        elif score >= v_pct[4]: lv = 1
        else: continue
        lat = gi * GRID_SIZE
        lon = gj * GRID_SIZE
        folium.Rectangle(
            bounds=[[lat, lon], [lat+GRID_SIZE, lon+GRID_SIZE]],
            color=None, fill=True,
            fill_color=COMB_COLOR[lv], fill_opacity=0.65,
            tooltip=f"Combined Level {lv} | score={score:.3f}",
        ).add_to(m)

    tec_note = f"TEC寄与: {int(w*100)}%" if has_tec else "TEC: データなし（ETAS単独）"
    src_count = {}
    for q in quakes:
        src_count[q.get("source","?")] = src_count.get(q.get("source","?"), 0) + 1

    legend = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px;border-radius:8px;
                border:2px solid #660099;font-size:13px;line-height:2.0;">
      <b>&#9314; 統合リスクマップ</b><br>
      <span style="color:#0d001a;">&#9632;</span> Level 5（上位0.2%）<br>
      <span style="color:#660099;">&#9632;</span> Level 4（上位0.5%）<br>
      <span style="color:#cc0033;">&#9632;</span> Level 3（上位1%）<br>
      <span style="color:#ff6600;">&#9632;</span> Level 2（上位30%）<br>
      <span style="color:#ffff99;">&#9632;</span> Level 1（上位60%）<br>
      <hr style="margin:4px 0;">
      <small>ETAS {int((1-w)*100)}% + {tec_note}<br>
      計{len(quakes)}件 | {updated_str}<br>
      &#x26A0; 参考目的のみ・防災利用不可</small>
    </div>"""
    m.get_root().html.add_child(Element(legend))
    return m






# ══════════════════════════════════════════════════════
# 有感地震履歴タブ（JMA）
# ══════════════════════════════════════════════════════

# 震度→色
INTENSITY_COLOR = {
    "1": "#4ade80", "2": "#a3e635", "3": "#facc15",
    "4": "#fb923c", "5-": "#f87171", "5+": "#ef4444",
    "6-": "#dc2626", "6+": "#b91c1c", "7": "#7f1d1d",
}
INTENSITY_LABEL = {
    "1":"震度1","2":"震度2","3":"震度3","4":"震度4",
    "5-":"震度5弱","5+":"震度5強",
    "6-":"震度6弱","6+":"震度6強","7":"震度7","-":"不明",
}

def _int_color(max_int):
    return INTENSITY_COLOR.get(max_int, "#94a3b8")

def _int_label(max_int):
    return INTENSITY_LABEL.get(max_int, f"震度{max_int}" if max_int != "-" else "不明")

def _fmt_time_jst(time_str):
    """ISO8601文字列をJST表示に変換。"""
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%m/%d %H:%M")
    except Exception:
        return time_str[:16]


def create_felt_quake_page(jma_quakes, updated_str):
    """
    有感地震履歴: 左リスト + 右foliumマップ の分割レイアウトHTMLを返す。
    iframeを使わず単一HTMLページとして生成。
    """
    # 時刻降順でソート
    sorted_q = sorted(
        jma_quakes,
        key=lambda q: q.get("time", ""),
        reverse=True
    )[:60]  # 最大60件

    # ── foliumマップ生成 ──
    m = _base_map()
    for q in sorted_q:
        ci = _int_color(q.get("max_int", "-"))
        mi = q.get("max_int", "-")
        place = q.get("place", "不明")
        mag   = q.get("mag", 0)
        depth = q.get("depth", 0)
        t_str = _fmt_time_jst(q.get("time", ""))
        radius = max(5, mag * 3)
        folium.CircleMarker(
            location=[q["lat"], q["lon"]],
            radius=radius,
            color=ci, fill=True, fill_color=ci, fill_opacity=0.8,
            tooltip=f"{place} M{mag:.1f} {_int_label(mi)} {t_str}",
            popup=folium.Popup(
                f"<b>{place}</b><br>"
                f"発生: {t_str} JST<br>"
                f"M{mag:.1f} / {_int_label(mi)}<br>"
                f"深さ {depth:.0f}km",
                max_width=220
            ),
        ).add_to(m)

    map_html = m._repr_html_()

    # ── リストHTML生成 ──
    rows = ""
    for i, q in enumerate(sorted_q):
        ci    = _int_color(q.get("max_int", "-"))
        mi    = _int_label(q.get("max_int", "-"))
        place = q.get("place", "不明")
        mag   = q.get("mag", 0)
        depth = q.get("depth", 0)
        t_str = _fmt_time_jst(q.get("time", ""))
        mag_s = f"M{mag:.1f}"
        depth_s = f"{depth:.0f}km"
        rows += f"""
        <tr onclick="focusQuake({q['lat']},{q['lon']},'{place}','{mi}','{mag_s}','{depth_s}','{t_str}')"
            style="cursor:pointer;" class="qrow">
          <td style="padding:7px 8px;font-weight:600;color:#f3f4f6;">{place}</td>
          <td style="padding:7px 4px;color:#9ca3af;font-size:12px;">{t_str}</td>
          <td style="padding:7px 4px;text-align:center;font-weight:700;color:#60a5fa;">{mag_s}</td>
          <td style="padding:7px 4px;text-align:center;">
            <span style="background:{ci};color:#000;padding:2px 6px;border-radius:4px;font-size:12px;font-weight:700;">
              {mi}
            </span>
          </td>
          <td style="padding:7px 4px;text-align:center;color:#9ca3af;font-size:12px;">{depth_s}</td>
        </tr>"""

    page = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ display: flex; height: 100vh; background: #0f172a; color: white;
            font-family: "Helvetica Neue", Arial, sans-serif; overflow: hidden; }}
    #list-panel {{
      width: 380px; flex-shrink: 0;
      background: #111827;
      border-right: 2px solid #1f2937;
      display: flex; flex-direction: column;
      overflow: hidden;
    }}
    #list-header {{
      padding: 14px 16px 10px;
      background: #1f2937;
      border-bottom: 1px solid #374151;
      flex-shrink: 0;
    }}
    #list-header h2 {{ font-size: 16px; color: #f3f4f6; margin-bottom: 4px; }}
    #list-header p  {{ font-size: 11px; color: #6b7280; }}
    #list-scroll {{ flex: 1; overflow-y: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead tr {{ background: #1f2937; position: sticky; top: 0; z-index: 10; }}
    thead th {{ padding: 8px 6px; font-size: 11px; color: #9ca3af;
                text-align: left; border-bottom: 1px solid #374151; }}
    .qrow:hover {{ background: #1f2937; }}
    .qrow:nth-child(even) {{ background: #0d1117; }}
    #map-panel {{ flex: 1; }}
    #map-panel iframe {{ width: 100%; height: 100%; border: none; }}
    #detail-bar {{
      position: fixed; bottom: 0; left: 380px; right: 0;
      background: rgba(17,24,39,0.95);
      border-top: 1px solid #374151;
      padding: 8px 16px;
      font-size: 13px; color: #d1d5db;
      display: none; z-index: 999;
    }}
  </style>
</head>
<body>
  <div id="list-panel">
    <div id="list-header">
      <h2>&#127981; 有感地震履歴（JMA）</h2>
      <p>直近{len(sorted_q)}件 / {updated_str}</p>
    </div>
    <div id="list-scroll">
      <table>
        <thead>
          <tr>
            <th>震源名</th>
            <th>発生時刻(JST)</th>
            <th>M</th>
            <th>最大震度</th>
            <th>深さ</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>

  <div id="map-panel">
    <iframe id="mapframe" srcdoc="{map_html.replace(chr(34), '&quot;')}"></iframe>
  </div>

  <div id="detail-bar" id="detail-bar">
    <span id="detail-text"></span>
  </div>

  <script>
    function focusQuake(lat, lon, place, intens, mag, depth, t) {{
      var bar  = document.getElementById('detail-bar');
      var text = document.getElementById('detail-text');
      text.innerHTML = '&#128205; <b>' + place + '</b> &nbsp; '
        + t + ' JST &nbsp; '
        + mag + ' &nbsp; ' + intens + ' &nbsp; 深さ ' + depth;
      bar.style.display = 'block';
    }}
  </script>
</body>
</html>"""
    return page


# ══════════════════════════════════════════════════════
# 無感地震履歴タブ（P2P + USGS）
# ══════════════════════════════════════════════════════

def create_unfelt_quake_map(unfelt_quakes, updated_str):
    """
    無感地震履歴: P2P・USGSの地震をfoliumマップに表示。
    マグニチュードで色分け・サイズ変更。
    """
    m = _base_map()

    sorted_q = sorted(unfelt_quakes, key=lambda q: q.get("time",""), reverse=True)

    # M別色
    def _mag_color(mag):
        if   mag >= 5.0: return "#ef4444"
        elif mag >= 4.0: return "#fb923c"
        elif mag >= 3.0: return "#facc15"
        elif mag >= 2.0: return "#4ade80"
        else:            return "#94a3b8"

    src_count = {}
    for q in sorted_q:
        src = q.get("source", "?")
        src_count[src] = src_count.get(src, 0) + 1
        mag   = q.get("mag", 0)
        depth = q.get("depth", 0)
        t_str = _fmt_time_jst(q.get("time",""))
        ci    = _mag_color(mag)
        folium.CircleMarker(
            location=[q["lat"], q["lon"]],
            radius=max(3, mag * 2.5),
            color=ci, fill=True, fill_color=ci, fill_opacity=0.7,
            tooltip=f"M{mag:.1f} / {depth:.0f}km / {t_str} [{src}]",
            popup=folium.Popup(
                f"M{mag:.1f}<br>深さ {depth:.0f}km<br>{t_str} JST<br>ソース: {src}",
                max_width=180
            ),
        ).add_to(m)

    legend = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:rgba(17,24,39,0.92);padding:12px 16px;border-radius:8px;
                border:1px solid #374151;font-size:13px;line-height:2.1;color:#f3f4f6;">
      <b>&#127774; 無感地震履歴</b><br>
      <span style="color:#ef4444;">&#9679;</span> M5.0以上<br>
      <span style="color:#fb923c;">&#9679;</span> M4.0〜4.9<br>
      <span style="color:#facc15;">&#9679;</span> M3.0〜3.9<br>
      <span style="color:#4ade80;">&#9679;</span> M2.0〜2.9<br>
      <span style="color:#94a3b8;">&#9679;</span> M2.0未満<br>
      <hr style="border-color:#374151;margin:6px 0;">
      <small>P2P:{src_count.get("p2p",0)}件 USGS:{src_count.get("usgs",0)}件<br>
      計{len(sorted_q)}件 | {updated_str}</small>
    </div>"""
    m.get_root().html.add_child(Element(legend))
    return m._repr_html_()

def create_placeholder_map(title, subtitle):
    m = _base_map()

    html = f"""
    <div style="
        position: fixed;
        top: 20px;
        left: 20px;
        z-index: 9999;
        background: white;
        padding: 18px;
        border-radius: 12px;
        border: 2px solid #2563eb;
        width: 340px;
        font-family: sans-serif;
    ">
      <h3 style="margin-bottom:10px;color:#111827;">{title}</h3>
      <p style="font-size:14px;color:#374151;line-height:1.7;">
        {subtitle}
      </p>
    </div>
    """

    m.get_root().html.add_child(Element(html))
    return m


# ══════════════════════════════════════════════════════
# タブ切り替えページを生成
# ══════════════════════════════════════════════════════
TAB_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>地震・気象統合情報システム</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: "Helvetica Neue", Arial, sans-serif;
      background: #101827;
      color: white;
      display: flex;
      height: 100vh;
      overflow: hidden;
    }

    #sidebar {
      width: 300px;
      background: #111827;
      border-right: 3px solid #1f2937;
      overflow-y: auto;
      padding: 18px 14px;
      flex-shrink: 0;
    }

    .sidebar-title {
      font-size: 22px;
      font-weight: bold;
      margin-bottom: 18px;
      color: #f3f4f6;
    }

    .group-title {
      margin-top: 18px;
      margin-bottom: 10px;
      padding-left: 8px;
      font-size: 15px;
      font-weight: bold;
      color: #93c5fd;
      border-left: 4px solid #3b82f6;
    }

    .tab-btn {
      width: 100%;
      text-align: left;
      border: none;
      margin-bottom: 8px;
      padding: 12px 14px;
      border-radius: 10px;
      background: #1f2937;
      color: #d1d5db;
      cursor: pointer;
      transition: 0.2s;
      font-size: 14px;
      font-weight: 600;
    }

    .tab-btn:hover {
      background: #374151;
      color: white;
    }

    .tab-btn.active {
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      color: white;
    }

    .sub-item {
      margin-left: 12px;
    }

    #content {
      flex: 1;
      height: 100vh;
    }

    iframe {
      width: 100%;
      height: 100%;
      border: none;
    }

    .tab-panel {
      display: none;
      width: 100%;
      height: 100%;
    }

    .tab-panel.active {
      display: block;
    }

    .version {
      margin-top: 20px;
      font-size: 12px;
      color: #9ca3af;
      text-align: center;
    }
  </style>
</head>
<body>

  <div id="sidebar">
    <div class="sidebar-title">地震・気象情報</div>

    <div class="group-title">地震</div>

    <button class="tab-btn active" onclick="switchTab(0)">
      有感地震履歴
    </button>

    <button class="tab-btn" onclick="switchTab(1)">
      無感地震履歴
    </button>

    <div class="group-title">気象</div>

    <button class="tab-btn" onclick="switchTab(2)">
      アメダス観測値
    </button>

    <button class="tab-btn" onclick="switchTab(3)">
      雨雲レーダー
    </button>

    <button class="tab-btn" onclick="switchTab(4)">
      警報・注意報
    </button>

    <div class="group-title">地震リスクマップ</div>

    <button class="tab-btn" onclick="switchTab(5)">
      ETASマップ
    </button>

    <button class="tab-btn" onclick="switchTab(6)">
      電離層（TEC）マップ
    </button>

    <button class="tab-btn" onclick="switchTab(7)">
      統合マップ
    </button>

    <div class="version">β4.0.0</div>
  </div>

  <div id="content">

    <div class="tab-panel active">
      <iframe srcdoc="{{ felt_quake_map|e }}" style="width:100%;height:100%;border:none;"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ unfelt_quake_map|e }}" style="width:100%;height:100%;border:none;"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ amedas_map|e }}"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ radar_map|e }}"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ warning_map|e }}"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ etas_map|e }}"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ tec_map|e }}"></iframe>
    </div>

    <div class="tab-panel">
      <iframe srcdoc="{{ combined_map|e }}"></iframe>
    </div>

  </div>

  <script>
    function switchTab(idx) {
      document.querySelectorAll('.tab-btn').forEach((b, i) => {
        b.classList.toggle('active', i === idx);
      });

      document.querySelectorAll('.tab-panel').forEach((p, i) => {
        p.classList.toggle('active', i === idx);
      });
    }
  </script>

</body>
</html>"""

LOADING_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>地震リスクマップ - 起動中</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body { background:#1a1a2e; color:white; display:flex;
           align-items:center; justify-content:center; height:100vh;
           font-family:sans-serif; flex-direction:column; gap:16px; }
    .spinner { width:48px; height:48px; border:5px solid #0f3460;
               border-top-color:#e94560; border-radius:50%;
               animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="spinner"></div>
  <p>データを準備中です...</p>
  <p style="font-size:12px;color:#aaa;">IONEXファイルのダウンロードに30〜60秒かかります。自動リロードします。</p>
</body>
</html>"""


# ══════════════════════════════════════════════════════
# バックグラウンド更新（段階的ロード）
# ══════════════════════════════════════════════════════
def _make_maps(quakes, tec_result, updated_str):
    """地震データとTEC結果からマップ辞書を生成して返す。"""
    grid_scores = analyze_etas(quakes)
    m1 = create_etas_map(grid_scores, quakes, updated_str)
    m2 = create_tec_map(tec_result, updated_str)
    m3 = create_combined_map(grid_scores, tec_result, quakes, updated_str)

    # 有感地震: JMAデータのみ（place/max_int フィールドあり）
    # 有感地震: 気象庁bosai JSONデータ（震度1以上・place/max_int フィールドあり）
    jma_quakes   = [q for q in quakes if q.get("source") == "jma_bosai"]
    # 無感地震: P2P・USGSデータ（有感情報なし）
    # 無感地震: P2P・USGSデータ（有感フラグなし・位置情報のみ）
    unfelt_quakes = [q for q in quakes if q.get("source") in ("p2p", "usgs")]

    return {
        "felt_quake":  create_felt_quake_page(jma_quakes, updated_str),
        "unfelt_quake": create_unfelt_quake_map(unfelt_quakes, updated_str),
        "amedas": create_placeholder_map(
            "アメダス観測値",
            "気温・降水量・風速・積雪・気圧の統合表示を実装予定です。"
        )._repr_html_(),
        "radar": create_placeholder_map(
            "雨雲レーダー",
            "JMA雨雲レーダー統合を実装予定です。"
        )._repr_html_(),
        "warning": create_placeholder_map(
            "警報・注意報",
            "気象警報・注意報表示を実装予定です。"
        )._repr_html_(),
        "etas": m1._repr_html_(),
        "tec": m2._repr_html_(),
        "combined": m3._repr_html_(),
    }


def _background_updater():
    global _cached_maps, _last_update, _ready_phase
    first_run = True
    while True:
        try:
            print("[BG] 更新開始")
            updated_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            if first_run:
                # ── フェーズ1: 地震データのみ（高速・数秒）──
                # USGSはキャッシュ不要で即取得できるので単独で先行
                print("[BG] フェーズ1: 地震データ取得中...")
                new_q = fetch_all_quakes()
                save_quakes(new_q)
                quakes = load_quakes()
                maps_phase1 = _make_maps(quakes, None, updated_str + " ※TEC読込中")
                with _cache_lock:
                    _cached_maps = maps_phase1
                    _last_update = time.time()
                    _ready_phase = 1
                print(f"[BG] フェーズ1完了（地震:{len(quakes)}件）→ 表示可能")

                # ── フェーズ2: TEC計算（低速・数十秒〜数分）──
                print("[BG] フェーズ2: TEC計算中（バックグラウンド）...")
                tec_result = compute_tec_zscore()
                maps_phase2 = _make_maps(quakes, tec_result, updated_str)
                with _cache_lock:
                    _cached_maps = maps_phase2
                    _last_update = time.time()
                    _ready_phase = 2
                print(f"[BG] フェーズ2完了（TEC:{'OK' if tec_result else 'NG'}）")
                first_run = False

            else:
                # 2回目以降: 地震とTECを並列取得
                results = {}
                def _fetch_quakes():
                    new_q = fetch_all_quakes()
                    save_quakes(new_q)
                    results["quakes"] = load_quakes()
                def _fetch_tec():
                    results["tec"] = compute_tec_zscore()

                t1 = threading.Thread(target=_fetch_quakes)
                t2 = threading.Thread(target=_fetch_tec)
                t1.start(); t2.start()
                t1.join();  t2.join()

                quakes     = results.get("quakes", [])
                tec_result = results.get("tec")
                maps = _make_maps(quakes, tec_result, updated_str)
                with _cache_lock:
                    _cached_maps = maps
                    _last_update = time.time()
                    _ready_phase = 2
                print(f"[BG] 更新完了（地震:{len(quakes)} TEC:{'OK' if tec_result else 'NG'}）")

        except Exception as e:
            import traceback
            print(f"[BG] エラー: {e}")
            traceback.print_exc()

        time.sleep(FETCH_INTERVAL_SEC)



# ══════════════════════════════════════════════════════
# Web ルーティング
# ══════════════════════════════════════════════════════
@app.route("/")
def index():
    with _cache_lock:
        maps  = _cached_maps
        phase = _ready_phase
    if maps is None:
        return LOADING_TEMPLATE

    # フェーズ1（地震のみ）の場合: 表示しつつTEC完了後に自動リロード
    extra_head = ""
    if phase == 1:
        extra_head = '<meta http-equiv="refresh" content="30">'

    html = render_template_string(
        TAB_TEMPLATE,
        felt_quake_map   = maps["felt_quake"],
        unfelt_quake_map = maps["unfelt_quake"],
        amedas_map       = maps["amedas"],
        radar_map        = maps["radar"],
        warning_map      = maps["warning"],
        etas_map         = maps["etas"],
        tec_map          = maps["tec"],
        combined_map     = maps["combined"],
    )
    # フェーズ1のときだけ自動リロードを挿入
    if phase == 1:
        html = html.replace("</head>", f"{extra_head}</head>", 1)
    return html


@app.route("/status")
def status():
    with _cache_lock:
        phase = _ready_phase
        lu    = _last_update
    return {"phase": phase, "last_update": lu}


if __name__ == "__main__":
    updater = threading.Thread(target=_background_updater, daemon=True)
    updater.start()
    app.run(debug=False, host="0.0.0.0", port=5000)