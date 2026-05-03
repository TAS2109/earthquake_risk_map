# -*- coding: utf-8 -*-
"""
地震発生確率マップ - ETAS + TEC電離層撹乱 統合版

3種類のマップをタブ切り替えで表示:
  [1] ETAS マップ    : 直近地震履歴からの余震確率（Ogata 1998）
  [2] TEC 撹乱マップ : IGS IONEXによる電離層TEC Zスコア（参考指標）
  [3] 統合リスクマップ: (1) + w*(2) の加重合算

TEC データ:
  - ソース  : JPL（Jet Propulsion Laboratory）IONEX ファイル
  - 解像度  : 2.5度×5.0度（全球グリッド）
  - 更新頻度: 2時間ごと
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
JMA_MAX_ENTRIES    = 60
JMA_WORKERS        = 12
TEC_HISTORY_DAYS   = 7            # Zスコア計算に使う過去日数
TEC_WEIGHT         = 0.4          # 統合マップでのTEC寄与率（0〜1）

# ── グローバルキャッシュ ──────────────────────────────
_cache_lock   = threading.Lock()
_cached_maps  = None   # {"etas": html, "tec": html, "combined": html}
_last_update  = 0.0


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
    url = "https://api.p2pquake.net/v2/history?codes=551&limit=100"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
    except Exception as e:
        print(f"[P2P] 取得エラー: {e}")
        return []
    quakes = []
    for q in data:
        if "earthquake" not in q:
            continue
        eq = q["earthquake"]
        try:
            quakes.append({
                "time":   eq["time"],
                "lat":    float(eq["hypocenter"]["latitude"]),
                "lon":    float(eq["hypocenter"]["longitude"]),
                "mag":    float(eq["hypocenter"]["magnitude"]),
                "depth":  float(eq["hypocenter"]["depth"]),
                "source": "p2p",
            })
        except Exception:
            continue
    print(f"[P2P] {len(quakes)} 件取得")
    return quakes


JMA_FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/eqvol_l.xml"

def _fetch_one_jma(url):
    try:
        r = requests.get(url, timeout=8)
        return _parse_jma_xml(r.content)
    except Exception:
        return None

def fetch_quakes_jma():
    NS = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        res  = requests.get(JMA_FEED_URL, timeout=15)
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"[JMA] フィード取得エラー: {e}")
        return []
    entries = []
    for entry in root.findall("atom:entry", NS):
        title = entry.findtext("atom:title", "", NS)
        link  = entry.find("atom:link", NS)
        if link is None:
            continue
        href = link.get("href", "")
        if "VXSE53" in href or "震源・震度" in title:
            entries.append(href)
    entries = entries[:JMA_MAX_ENTRIES]
    quakes = []
    with ThreadPoolExecutor(max_workers=JMA_WORKERS) as executor:
        futures = {executor.submit(_fetch_one_jma, u): u for u in entries}
        for future in as_completed(futures):
            q = future.result()
            if q:
                quakes.append(q)
    print(f"[JMA] {len(quakes)} 件取得（並列）")
    return quakes

def _parse_jma_xml(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    time_el = root.find(".//{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}DateTime")
    if time_el is None:
        time_el = root.find(".//{http://xml.kishou.go.jp/jmaxml1/}DateTime")
    time_str = time_el.text.strip() if time_el is not None else None
    if not time_str:
        return None
    hypo = root.find(".//{http://xml.kishou.go.jp/jmaxml1/elementBasis1/}Hypocenter")
    if hypo is None:
        return None
    coord_el = hypo.find(".//{http://xml.kishou.go.jp/jmaxml1/elementBasis1/}Coordinate")
    mag_el   = root.find(".//{http://xml.kishou.go.jp/jmaxml1/elementBasis1/}Magnitude")
    if coord_el is None or mag_el is None:
        return None
    try:
        lat, lon, depth = _parse_iso6709(coord_el.text.strip())
        mag = float(mag_el.text.strip())
    except Exception:
        return None
    return {"time": time_str, "lat": lat, "lon": lon,
            "mag": mag, "depth": depth, "source": "jma"}

def _parse_iso6709(coord_text):
    parts = re.findall(r"[+-][0-9.]+", coord_text.rstrip("/"))
    if len(parts) < 2:
        raise ValueError(f"座標解析失敗: {coord_text}")
    lat   = float(parts[0])
    lon   = float(parts[1])
    depth = abs(float(parts[2])) / 1000.0 if len(parts) >= 3 else 0.0
    return lat, lon, depth

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
        threading.Thread(target=_run, args=("p2p",  fetch_quakes_p2p)),
        threading.Thread(target=_run, args=("jma",  fetch_quakes_jma)),
        threading.Thread(target=_run, args=("usgs", fetch_quakes_usgs)),
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    all_q = results.get("p2p",[]) + results.get("jma",[]) + results.get("usgs",[])
    return deduplicate(all_q)

def deduplicate(quakes, time_tol_min=5, dist_tol_deg=0.3):
    priority = {"jma": 0, "p2p": 1, "usgs": 2}
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
                writer.writerow([q["time"], q["lat"], q["lon"],
                                  q["mag"], q["depth"], q.get("source","unknown")])
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
                data.append({
                    "time":   row[0],
                    "lat":    float(row[1]),
                    "lon":    float(row[2]),
                    "mag":    float(row[3]),
                    "depth":  float(row[4]),
                    "source": row[5] if len(row) > 5 else "unknown",
                })
            except Exception:
                continue
    return data


# ══════════════════════════════════════════════════════
# IONEX TEC 取得・解析
# ══════════════════════════════════════════════════════
def _ionex_url(dt):
    """
    JPL の IONEX ファイル URL を生成する。
    フォーマット: jpld{DOY}0.{YY}i.gz
    例: 2025年1月15日 -> DOY=015, YY=25
    """
    doy = dt.timetuple().tm_yday
    yy  = dt.strftime("%y")
    yyyy = dt.strftime("%Y")
    fname = f"jpld{doy:03d}0.{yy}i.gz"
    return (
        f"https://cddis.nasa.gov/archive/gnss/products/ionex/{yyyy}/{doy:03d}/{fname}",
        fname,
    )

def _download_ionex(dt):
    """
    指定日のIONEXをダウンロードしてキャッシュ、テキストを返す。
    NASAのCDDISはログイン不要(anonymous FTP over HTTPS)。
    失敗時はNoneを返す。
    """
    os.makedirs(IONEX_CACHE_DIR, exist_ok=True)
    url, fname = _ionex_url(dt)
    cache_path = os.path.join(IONEX_CACHE_DIR, fname.replace(".gz", ""))

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="ascii", errors="ignore") as f:
            return f.read()

    # 代替ミラー（CDDISが失敗した場合はIGSミラーを試みる）
    mirrors = [
        url,
        url.replace("cddis.nasa.gov/archive", "igs.ign.fr/pub"),
    ]
    for mirror_url in mirrors:
        try:
            r = requests.get(mirror_url, timeout=30)
            if r.status_code != 200:
                continue
            with gzip.open(io.BytesIO(r.content), "rt",
                           encoding="ascii", errors="ignore") as gz:
                text = gz.read()
            with open(cache_path, "w", encoding="ascii") as f:
                f.write(text)
            print(f"[IONEX] ダウンロード完了: {fname}")
            return text
        except Exception as e:
            print(f"[IONEX] ミラー失敗 {mirror_url}: {e}")
            continue
    return None

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


def compute_tec_zscore():
    """
    TEC Zスコアグリッドを計算する。

    手順:
      1. 今日と過去TEC_HISTORY_DAYS日分のIONEXを取得
      2. 現在時刻に最も近いエポックのTECを「現在値」とする
      3. 同じ時間帯（±1時間）の過去7日分のTECで平均・標準偏差を計算
      4. Z = (TEC_now - mean) / std を各グリッドで算出
      5. 日本周辺（lat 24-46, lon 122-146）に絞り込んで返す

    戻り値:
      {
        "zscore": np.ndarray (n_lat_jp, n_lon_jp),
        "lat_arr": np.ndarray,
        "lon_arr": np.ndarray,
        "tec_now": np.ndarray,
        "epoch":   datetime,
        "status":  str
      }
      失敗時は None
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    # 今日のIONEX取得
    today_dt   = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    today_text = _download_ionex(today_dt)
    if not today_text:
        print("[TEC] 今日のIONEXダウンロード失敗")
        return None

    today_maps = _parse_ionex(today_text)
    if not today_maps:
        print("[TEC] 今日のIONEX解析失敗")
        return None

    # 現在時刻に最も近いエポックを選択
    def epoch_diff(m):
        return abs((m["epoch"] - now).total_seconds())

    current_map = min(today_maps, key=epoch_diff)
    tec_now     = current_map["tec"]
    lat_arr     = current_map["lat_arr"]
    lon_arr     = current_map["lon_arr"]
    current_hour = current_map["epoch"].hour

    # 過去TEC_HISTORY_DAYS日分の同時間帯データを収集
    history_stack = []
    for d in range(1, TEC_HISTORY_DAYS + 1):
        past_dt   = today_dt - timedelta(days=d)
        past_text = _download_ionex(past_dt)
        if not past_text:
            continue
        past_maps = _parse_ionex(past_text)
        for pm in past_maps:
            if abs(pm["epoch"].hour - current_hour) <= 1:
                history_stack.append(pm["tec"])

    if len(history_stack) < 3:
        print(f"[TEC] 過去データ不足（{len(history_stack)}件）、絶対値モードで代替")
        # Zスコア計算不可 -> 絶対値を正規化して使用
        zscore = (tec_now - np.nanmean(tec_now)) / (np.nanstd(tec_now) + 1e-6)
        status = "絶対値モード（過去データ不足）"
    else:
        history_arr = np.stack(history_stack, axis=0)  # (days, lat, lon)
        mean_tec    = np.nanmean(history_arr, axis=0)
        std_tec     = np.nanstd(history_arr,  axis=0)
        std_tec     = np.where(std_tec < 0.5, 0.5, std_tec)  # 最小std=0.5TECU
        zscore      = (tec_now - mean_tec) / std_tec
        status      = f"Zスコードモード（過去{len(history_stack)}エポック使用）"

    # 日本周辺に絞り込む（lat 22-48, lon 120-148）
    lat_mask = (lat_arr >= 22) & (lat_arr <= 48)
    lon_mask = (lon_arr >= 120) & (lon_arr <= 148)

    lat_jp = lat_arr[lat_mask]
    lon_jp = lon_arr[lon_mask]

    # 2Dマスク
    lat_idx = np.where(lat_mask)[0]
    lon_idx = np.where(lon_mask)[0]
    zscore_jp  = zscore[np.ix_(lat_idx, lon_idx)]
    tec_now_jp = tec_now[np.ix_(lat_idx, lon_idx)]

    print(f"[TEC] 計算完了: {status}, epoch={current_map['epoch']}")
    return {
        "zscore":  zscore_jp,
        "lat_arr": lat_jp,
        "lon_arr": lon_jp,
        "tec_now": tec_now_jp,
        "epoch":   current_map["epoch"],
        "status":  status,
    }


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
    log_v = np.log(np.clip(values_arr, 0, None) + 1)
    return (
        np.percentile(log_v, 98),
        np.percentile(log_v, 95),
        np.percentile(log_v, 85),
        np.percentile(log_v, 70),
        np.percentile(log_v, 40),
    )

ETAS_COLOR = {5: "#1a0033", 4: "#8000ff", 3: "red", 2: "orange", 1: "#66ccff"}
TEC_COLOR  = {5: "#4b0000", 4: "#cc0000", 3: "#ff6600", 2: "#ffcc00", 1: "#ffffcc"}
COMB_COLOR = {5: "#0d001a", 4: "#660099", 3: "#cc0033", 2: "#ff6600", 1: "#ffff99"}


def _base_map():
    return folium.Map(location=[36, 138], zoom_start=5, tiles="CartoDB positron")


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
      <span style="color:#1a0033;">&#9632;</span> Level 5（上位2%）<br>
      <span style="color:#8000ff;">&#9632;</span> Level 4（上位5%）<br>
      <span style="color:red;">&#9632;</span> Level 3（上位15%）<br>
      <span style="color:orange;">&#9632;</span> Level 2（上位30%）<br>
      <span style="color:#66ccff;">&#9632;</span> Level 1（上位60%）<br>
      <hr style="margin:4px 0;">
      <small>空間: べき乗則(q={EP.Q}) / 時間: Omori-Utsu(p={EP.P})<br>
      深さ補正あり / 背景活動率={EP.MU}<br>
      JMA:{src_count.get('jma',0)} P2P:{src_count.get('p2p',0)} USGS:{src_count.get('usgs',0)}<br>
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
      <small>ソース: JPL IONEX (IGS GNSS網)<br>
      指標: {status}<br>
      解像度: 2.5°&times;5.0° | Epoch: {epoch_str}<br>
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
      <span style="color:#0d001a;">&#9632;</span> Level 5（上位2%）<br>
      <span style="color:#660099;">&#9632;</span> Level 4（上位5%）<br>
      <span style="color:#cc0033;">&#9632;</span> Level 3（上位15%）<br>
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
# タブ切り替えページを生成
# ══════════════════════════════════════════════════════
TAB_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>地震リスクマップ</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Helvetica Neue", Arial, sans-serif; background: #1a1a2e; }
    #tab-bar {
      display: flex; background: #16213e; padding: 8px 12px 0;
      border-bottom: 3px solid #0f3460;
    }
    .tab-btn {
      padding: 10px 24px; cursor: pointer; border: none;
      border-radius: 8px 8px 0 0; font-size: 14px; font-weight: bold;
      background: #0f3460; color: #aac; transition: all 0.2s;
      margin-right: 4px;
    }
    .tab-btn:hover { background: #1a5276; color: white; }
    .tab-btn.active { background: #e94560; color: white; }
    .tab-btn span.num {
      display: inline-block; width: 22px; height: 22px;
      background: rgba(255,255,255,0.2); border-radius: 50%;
      text-align: center; line-height: 22px; margin-right: 6px; font-size: 12px;
    }
    #map-container { width: 100%; height: calc(100vh - 51px); }
    iframe { width: 100%; height: 100%; border: none; }
    .tab-panel { display: none; width: 100%; height: 100%; }
    .tab-panel.active { display: block; }
  </style>
</head>
<body>
  <div id="tab-bar">
    <button class="tab-btn active" onclick="switchTab(0)">
      <span class="num">&#9312;</span>ETAS（地震履歴）
    </button>
    <button class="tab-btn" onclick="switchTab(1)">
      <span class="num">&#9313;</span>TEC（電離層）
    </button>
    <button class="tab-btn" onclick="switchTab(2)">
      <span class="num">&#9314;</span>統合リスク
    </button>
  </div>
  <div id="map-container">
    <div class="tab-panel active" id="panel-0">
      <iframe srcdoc="{{ etas_map|e }}"></iframe>
    </div>
    <div class="tab-panel" id="panel-1">
      <iframe srcdoc="{{ tec_map|e }}"></iframe>
    </div>
    <div class="tab-panel" id="panel-2">
      <iframe srcdoc="{{ combined_map|e }}"></iframe>
    </div>
  </div>
  <script>
    function switchTab(idx) {
      document.querySelectorAll('.tab-btn').forEach((b,i) => {
        b.classList.toggle('active', i === idx);
      });
      document.querySelectorAll('.tab-panel').forEach((p,i) => {
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
  <meta http-equiv="refresh" content="15">
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
# バックグラウンド更新
# ══════════════════════════════════════════════════════
def _background_updater():
    global _cached_maps, _last_update
    while True:
        try:
            print("[BG] 更新開始")
            updated_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            # 地震データ & TEC を並列取得
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

            # ETAS計算
            grid_scores = analyze_etas(quakes)

            # 3マップ生成
            m1 = create_etas_map(grid_scores, quakes, updated_str)
            m2 = create_tec_map(tec_result, updated_str)
            m3 = create_combined_map(grid_scores, tec_result, quakes, updated_str)

            maps = {
                "etas":     m1._repr_html_(),
                "tec":      m2._repr_html_(),
                "combined": m3._repr_html_(),
            }
            with _cache_lock:
                _cached_maps = maps
                _last_update = time.time()
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
        maps = _cached_maps
    if maps is None:
        return LOADING_TEMPLATE

    return render_template_string(
        TAB_TEMPLATE,
        etas_map     = maps["etas"],
        tec_map      = maps["tec"],
        combined_map = maps["combined"],
    )


if __name__ == "__main__":
    import os

    updater = threading.Thread(target=_background_updater, daemon=True)
    updater.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)