# -*- coding: utf-8 -*-
"""
地震研究統合プラットフォーム v5.0

タブ構成:
  1. 地震履歴     - 有感・無感統合 (JMA / P2P / USGS)
  2. ETASマップ   - 地震発生確率 + ETAS残差（研究用）
  3. b値マップ    - グリッドごとのGutenberg-Richter b値
  4. TEC          - 電離圏全電子数 (NICT SCIDAS リンク)
  5. GNSS         - 地殻変動 (GEONET リンク + 変位プレースホルダー)
  6. 海面気圧     - アメダス海面気圧マップ
"""

from flask import Flask, Response
import requests, csv, os, math, re, json, threading, time
from datetime import datetime, timezone, timedelta
import numpy as np

app = Flask(__name__)

# ── 定数 ────────────────────────────────────────────
DATA_FILE          = "data/quakes.csv"
GRID_SIZE          = 0.1
FETCH_INTERVAL_SEC = 600

# ── スナップショット（解析結果の時系列ログ）────────────
SNAPSHOT_DIR          = "data/snapshots"
SNAPSHOT_INTERVAL_SEC = 3600     # 1時間ごと
SNAPSHOT_KEEP_DAYS    = 30       # 古いスナップショットの保持期間

# ── グローバルキャッシュ ──────────────────────────────
_cache_lock       = threading.Lock()
_cached_data      = None
_last_update      = 0.0
_ready_phase      = 0
_last_snapshot_ts = 0.0

_amedas_cache  = {"data": None, "ts": 0.0}
AMEDAS_CACHE_SEC = 300


# ══════════════════════════════════════════════════════
# ETAS パラメータ (Ogata 1998)
# ══════════════════════════════════════════════════════
class ETASParams:
    MU=0.05; K=0.020; C=0.010; P=1.11; ALPHA=2.30; M0=1.0
    D=0.015; GAMMA=0.50; Q=1.58; DEPTH_SCALE=80.0; SPACE_RADIUS=8
EP = ETASParams()

ETAS_COLOR = {5:"#1a0033", 4:"#8000ff", 3:"#ff0000", 2:"#ff8800", 1:"#66ccff"}

LEAFLET_CDN = """
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>"""

DARK_TILE = "L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy;CartoDB',subdomains:'abcd',maxZoom:18}).addTo(map);"

GEOJSON_JS = """
    fetch('https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson')
      .then(r=>r.json())
      .then(d=>L.geoJSON(d,{style:{fillOpacity:0,color:'#555',weight:1}}).addTo(map));"""


# ══════════════════════════════════════════════════════
# データ取得
# ══════════════════════════════════════════════════════
def _parse_p2p_item(item):
    """P2P history API (code=551) の1件をパースして dict を返す。失敗時は None。"""
    if "earthquake" not in item:
        return None
    eq = item["earthquake"]; hypo = eq.get("hypocenter", {})
    try:
        lat = float(hypo.get("latitude", -200))
        lon = float(hypo.get("longitude", -200))
        if lat == -200 or lon == -200:
            return None
        mag = float(hypo.get("magnitude", -1))
        if mag < 0:
            return None
        depth = abs(float(hypo.get("depth", 0)))
        raw_time = eq.get("time", "")
        time_str = raw_time
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                dt_jst   = datetime.strptime(raw_time, fmt)
                time_str = dt_jst.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc).isoformat()
                break
            except ValueError:
                continue
        scale_map = {10:"1",20:"2",30:"3",40:"4",45:"5-",50:"5+",55:"6-",60:"6+",70:"7"}
        # maxScale フィールド（-1 = 無感）
        max_scale = eq.get("maxScale", -1)
        if max_scale == -1:
            max_int = ""          # 無感
        else:
            max_int = scale_map.get(int(max_scale), str(max_scale))
        return {"time":time_str,"lat":lat,"lon":lon,"mag":mag,"depth":depth,
                "source":"p2p","place":hypo.get("name","不明"),"max_int":max_int}
    except Exception:
        return None

def fetch_quakes_p2p():
    """
    P2P地震情報 /history?codes=551 を複数ページ取得し、
    有感・無感を問わず震源情報のある地震をすべて収集する。
    最大 500 件 (5ページ × 100件) を取得して直近 30 日分を返す。
    """
    BASE_URL = "https://api.p2pquake.net/v2/history"
    HEADERS  = {"User-Agent": "SeismoApp/5.0"}
    PAGES    = 5        # 1ページ100件 → 最大500件
    cutoff   = datetime.now(timezone.utc) - timedelta(days=30)

    quakes = []
    seen_ids = set()
    stop_early = False

    for page in range(PAGES):
        if stop_early:
            break
        params = {"codes": 551, "limit": 100, "offset": page * 100}
        try:
            resp = requests.get(BASE_URL, params=params, timeout=10, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[P2P] page={page} エラー: {e}")
            break

        if not data:
            break  # これ以上データなし

        for item in data:
            # 重複排除（同一IDが来ることがある）
            item_id = item.get("id", "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)

            q = _parse_p2p_item(item)
            if q is None:
                continue

            # 30日カットオフ — それより古いデータが来たらページング終了
            try:
                t = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
                if t < cutoff:
                    stop_early = True
                    break
            except Exception:
                pass

            quakes.append(q)

    print(f"[P2P] {len(quakes)}件（有感+無感）")
    return quakes


def fetch_quakes_p2p_jma():
    """
    P2P地震情報の /jma/quake API から無感地震を含む全地震を取得する。
    /history?codes=551 では直近1週間程度しか取得できないため、
    こちらのエンドポイントで過去30日分を補完する。
    quake_type=Destination (震源のみ情報、無感地震を多く含む) および
    ScaleAndDestination (震度+震源) を対象とする。

    ★ Bug fix: /jma エンドポイントは 10 リクエスト/分 のレート制限がある
    (P2P地震情報 API仕様書 v2.3.0 より)。元コードはページング時にリクエスト間隔を
    空けずに連続でAPIを呼んでいたため、30日分（数百件規模）を取得しようとすると
    すぐ429 (Too Many Requests) となり、resp.raise_for_status() で例外 → breakして
    そのqtypeの取得が早期終了し、無感地震（Destination）がほとんど取れなくなっていた。
    さらに fetch_all_quakes() 側の thread.join(timeout=30) によって、ページングが
    終わる前にスレッドがタイムアウトし、結果が一切resultsに書き込まれず空リストに
    なるケースもあった（このタイムアウト自体も併せて修正している）。
    対策: リクエスト毎に十分なスリープを入れ、レート制限内に収める。
    """
    BASE_URL = "https://api.p2pquake.net/v2/jma/quake"
    HEADERS  = {"User-Agent": "SeismoApp/5.0"}
    scale_map = {10:"1",20:"2",30:"3",40:"4",45:"5-",50:"5+",55:"6-",60:"6+",70:"7"}
    cutoff   = datetime.now(timezone.utc) - timedelta(days=30)
    since    = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%d")

    # /jma エンドポイントは 10 リクエスト/分。安全マージンを取って 6.5 秒間隔にする
    # (= 1分あたり約9リクエストでレート制限に抵触しないようにする)
    REQUEST_INTERVAL_SEC = 6.5
    MAX_PAGES_PER_TYPE    = 8   # 1タイプあたり最大8ページ(800件)で打ち切り、暴走を防ぐ

    quakes = []
    seen_ids = set()
    request_count = 0

    # Destination = 震源のみ（無感）、ScaleAndDestination = 震度+震源（有感）
    for qtype in ("Destination", "ScaleAndDestination"):
        offset = 0
        for page in range(MAX_PAGES_PER_TYPE):
            if request_count > 0:
                time.sleep(REQUEST_INTERVAL_SEC)

            params = {
                "limit": 100, "offset": offset, "order": -1,
                "quake_type": qtype, "since_date": since,
            }
            try:
                resp = requests.get(BASE_URL, params=params, timeout=15, headers=HEADERS)
                request_count += 1
                if resp.status_code == 429:
                    # レート制限に達した場合は少し長めに待ってリトライ
                    print(f"[P2P/jma] {qtype} offset={offset} レート制限(429)。待機して再試行")
                    time.sleep(10)
                    resp = requests.get(BASE_URL, params=params, timeout=15, headers=HEADERS)
                    request_count += 1
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[P2P/jma] {qtype} offset={offset} エラー: {e}")
                break

            if not data:
                break

            stop_early = False
            for item in data:
                item_id = item.get("id", "")
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)

                eq   = item.get("earthquake", {})
                hypo = eq.get("hypocenter", {})
                try:
                    lat = float(hypo.get("latitude", -200))
                    lon = float(hypo.get("longitude", -200))
                    if lat == -200 or lon == -200:
                        continue
                    mag = float(hypo.get("magnitude", -1))
                    if mag < 0:
                        continue
                    depth = abs(float(hypo.get("depth", 0)))

                    raw_time = eq.get("time", "")
                    time_str = raw_time
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                        try:
                            dt_jst   = datetime.strptime(raw_time, fmt)
                            time_str = dt_jst.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc).isoformat()
                            break
                        except ValueError:
                            continue

                    try:
                        t = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                        if t < cutoff:
                            stop_early = True
                            break
                    except Exception:
                        pass

                    max_scale = eq.get("maxScale", -1)
                    max_int   = "" if max_scale == -1 else scale_map.get(int(max_scale), str(max_scale))

                    quakes.append({
                        "time": time_str, "lat": lat, "lon": lon,
                        "mag": mag, "depth": depth,
                        "source": "p2p_jma",
                        "place": hypo.get("name", "不明"),
                        "max_int": max_int,
                    })
                except Exception:
                    continue

            if stop_early or len(data) < 100:
                break
            offset += 100

    print(f"[P2P/jma] {len(quakes)}件（Destination+ScaleAndDestination） リクエスト数={request_count}")
    return quakes

JMA_LIST_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"

def _parse_jma_cod(cod_str):
    # 例: +35.6+139.7-070000/ または +35.6+139.7+0700/
    # 緯度・経度は小数点あり、深さは整数のこともある
    m = re.match(r'([+-]\d+\.?\d*)([+-]\d+\.?\d*)([+-]\d+\.?\d*)?', cod_str.strip())
    if not m: raise ValueError(cod_str)
    lat = float(m.group(1))
    lon = float(m.group(2))
    depth = 0.0
    if m.group(3):
        raw_d = float(m.group(3))
        # 深さの値が大きい場合はメートル表記 → km変換
        depth = abs(raw_d) / 1000.0 if abs(raw_d) >= 1000 else abs(raw_d)
    return lat, lon, depth

def fetch_quakes_jma_bosai():
    try:
        data = requests.get(JMA_LIST_URL, timeout=10, headers={"User-Agent":"SeismoApp/5.0"}).json()
    except Exception as e:
        print(f"[JMA] {e}"); return []
    quakes = []
    for item in data:
        if item.get("ttl") != "震源・震度情報": continue
        if item.get("ift") in ("訂正","取消"): continue
        try: lat, lon, depth = _parse_jma_cod(item["cod"])
        except Exception: continue
        try: mag = float(item.get("mag","0"))
        except Exception: mag = 0.0
        at_str = item.get("at", item.get("rdt",""))
        try:
            dt = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
            time_str = dt.astimezone(timezone.utc).isoformat()
        except Exception: time_str = at_str
        maxi = item.get("maxi","")
        quakes.append({"time":time_str,"lat":lat,"lon":lon,"mag":mag,"depth":depth,
                       "source":"jma_bosai","place":item.get("anm","不明"),"max_int":maxi})
    print(f"[JMA] {len(quakes)}件"); return quakes

def fetch_quakes_usgs():
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    url = (f"https://earthquake.usgs.gov/fdsnws/event/1/query"
           f"?format=geojson&starttime={start}&minlatitude=24&maxlatitude=46"
           f"&minlongitude=122&maxlongitude=146&minmagnitude=0.0&orderby=time&limit=2000")
    try: data = requests.get(url, timeout=12).json()
    except Exception as e:
        print(f"[USGS] {e}"); return []
    quakes = []
    for feat in data.get("features",[]):
        try:
            props = feat["properties"]; coords = feat["geometry"]["coordinates"]
            t = datetime.fromtimestamp(props["time"]/1000, tz=timezone.utc)
            quakes.append({"time":t.isoformat(),"lat":float(coords[1]),"lon":float(coords[0]),
                           "mag":float(props["mag"]),"depth":float(coords[2]),"source":"usgs",
                           "place":props.get("place",""),"max_int":""})
        except Exception: continue
    print(f"[USGS] {len(quakes)}件"); return quakes

def fetch_all_quakes():
    results = {}
    def _run(name, fn):
        try: results[name] = fn()
        except Exception as e: print(f"[fetch_all] {name} {e}"); results[name] = []
    threads = [threading.Thread(target=_run, args=(n,f), daemon=True) for n,f in
               [("p2p",     fetch_quakes_p2p),
                ("p2p_jma", fetch_quakes_p2p_jma),
                ("usgs",    fetch_quakes_usgs),
                ("jma",     fetch_quakes_jma_bosai)]]
    for t in threads: t.start()
    # ★ Bug fix: p2p_jma は /jma のレート制限(10req/分)に対応するため
    # リクエスト間に約6.5秒のスリープを挟んでいる。Destination/ScaleAndDestination
    # 各最大8ページなので、最悪ケースで約2分ほどかかる。
    # 旧コードは timeout=30 で join していたため、p2p_jma が時間内に完了せず
    # results["p2p_jma"] が一切セットされない（=空扱いになる）ことが多発し、
    # 無感地震が取得できていなかった。十分なタイムアウトに変更する。
    for t in threads: t.join(timeout=150)
    for name in ("p2p", "p2p_jma", "usgs", "jma"):
        if name not in results:
            print(f"[fetch_all] {name} タイムアウトで未完了のためスキップ")
    all_q = (results.get("jma",[]) + results.get("p2p",[]) +
             results.get("p2p_jma",[]) + results.get("usgs",[]))
    return _deduplicate(all_q)

def _deduplicate(quakes, time_tol_min=5, dist_tol_deg=0.3):
    # 優先度: jma_bosai > p2p > p2p_jma > usgs
    # p2p_jmaはJMAデータの再配信なのでjma_bosaiと重複しやすい -> 低優先度
    prio = {"jma_bosai":0,"p2p":1,"p2p_jma":2,"usgs":3}
    sorted_q = sorted(quakes, key=lambda q: prio.get(q["source"],9))
    kept = []
    for q in sorted_q:
        try: t_q = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
        except Exception: t_q = None
        dup = False
        for k in kept:
            try:
                t_k = datetime.fromisoformat(k["time"].replace("Z","+00:00"))
                dt = abs((t_q-t_k).total_seconds())/60 if t_q and t_k else 999
            except Exception: dt = 999
            if dt < time_tol_min and math.sqrt((q["lat"]-k["lat"])**2+(q["lon"]-k["lon"])**2) < dist_tol_deg:
                dup = True; break
        if not dup: kept.append(q)
    print(f"[重複排除] {len(quakes)}->{len(kept)}件"); return kept

def save_quakes(quakes):
    os.makedirs("data", exist_ok=True)
    existing = set()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row)>=3: existing.add((row[0],row[1],row[2]))
    new_count = 0
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for q in quakes:
            key = (q["time"],str(q["lat"]),str(q["lon"]))
            if key not in existing:
                w.writerow([q["time"],q["lat"],q["lon"],q["mag"],q["depth"],
                            q.get("source",""),q.get("place",""),q.get("max_int","")])
                existing.add(key); new_count += 1
    print(f"[保存] {new_count}件追加")
    _cleanup_old_quakes()

def _cleanup_old_quakes(keep_days=65):
    if not os.path.exists(DATA_FILE): return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    kept = []; removed = 0
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                t = datetime.fromisoformat(row[0].replace("Z","+00:00"))
                if t >= cutoff: kept.append(row)
                else: removed += 1
            except Exception: kept.append(row)
    if removed > 0:
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(kept)
        print(f"[CSV整理] {removed}件削除、{len(kept)}件保持")

def load_quakes(days=60):
    if not os.path.exists(DATA_FILE): return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    data = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                t = datetime.fromisoformat(row[0].replace("Z","+00:00"))
                if t < cutoff: continue
                data.append({"time":row[0],"lat":float(row[1]),"lon":float(row[2]),
                             "mag":float(row[3]),"depth":float(row[4]),
                             "source":row[5] if len(row)>5 else "",
                             "place":row[6] if len(row)>6 else "",
                             "max_int":row[7] if len(row)>7 else ""})
            except Exception: continue
    return data


# ══════════════════════════════════════════════════════
# スナップショット（解析結果を1時間ごとにログして後で読み込めるようにする）
# ══════════════════════════════════════════════════════
def save_snapshot(cached_data):
    """
    現在の解析結果（ETAS格子・b値格子など）を1ファイル1スナップショットとして
    data/snapshots/ に JSON 保存する。グリッドのキーは (gi,gj) タプルなので
    JSON化のために "gi_gj" 文字列に変換して保存する。
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    fname = now_utc.strftime("%Y%m%d_%H%M%S") + ".json"
    path = os.path.join(SNAPSHOT_DIR, fname)
    payload = {
        "timestamp_utc": now_utc.isoformat(),
        "updated":       cached_data.get("updated", ""),
        "quake_count":   len(cached_data.get("all", [])),
        "etas":          {f"{k[0]}_{k[1]}": v for k, v in cached_data.get("etas", {}).items()},
        "bvalue":        {f"{k[0]}_{k[1]}": v for k, v in cached_data.get("bvalue", {}).items()},
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"[スナップショット] 保存: {fname} "
              f"(地震:{payload['quake_count']}件 ETAS格子:{len(payload['etas'])} b値格子:{len(payload['bvalue'])})")
    except Exception as e:
        print(f"[スナップショット] 保存失敗: {e}")
    _cleanup_old_snapshots()

def _cleanup_old_snapshots(keep_days=SNAPSHOT_KEEP_DAYS):
    """古いスナップショットファイルを削除してディスク肥大化を防ぐ。"""
    if not os.path.isdir(SNAPSHOT_DIR):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for fname in os.listdir(SNAPSHOT_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            ts = datetime.strptime(fname[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            if ts < cutoff:
                os.remove(os.path.join(SNAPSHOT_DIR, fname))
        except Exception:
            continue

def list_snapshots():
    """保存済みスナップショットのファイル名を新しい順に返す。"""
    if not os.path.isdir(SNAPSHOT_DIR):
        return []
    files = [f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json")]
    return sorted(files, reverse=True)

def load_snapshot(fname):
    """
    指定したスナップショットファイルを読み込む。
    grid("etas"/"bvalue")のキーは "gi_gj" 文字列から (gi,gj) タプルに復元する。
    見つからない場合は None を返す。
    """
    path = os.path.join(SNAPSHOT_DIR, fname)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    def _restore(grid):
        out = {}
        for k, v in grid.items():
            gi, gj = k.split("_")
            out[(int(gi), int(gj))] = v
        return out

    payload["etas"]   = _restore(payload.get("etas", {}))
    payload["bvalue"] = _restore(payload.get("bvalue", {}))
    return payload


# ══════════════════════════════════════════════════════
# ETAS 解析
# ══════════════════════════════════════════════════════
def analyze_etas(quakes):
    if not quakes: return {}
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(days=60)
    valid = []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
            if t < cutoff: continue
            dt_days = (now_utc - t).total_seconds() / 86400
            if dt_days < 0: continue
            depth_factor = math.exp(-q["depth"] / EP.DEPTH_SCALE)
            valid.append((q["lat"], q["lon"], q["mag"], dt_days, depth_factor))
        except Exception: continue
    if not valid: return {}
    lats   = np.array([v[0] for v in valid]); lons   = np.array([v[1] for v in valid])
    mags   = np.array([v[2] for v in valid]); t_days = np.array([v[3] for v in valid])
    depths = np.array([v[4] for v in valid])
    time_decay    = depths / (t_days + EP.C) ** EP.P
    mag_scale     = EP.K * np.exp(EP.ALPHA * (mags - EP.M0))
    spatial_scale = EP.D * np.exp(EP.GAMMA * mags)
    contributions = time_decay * mag_scale
    R = EP.SPACE_RADIUS
    gi = np.round(lats / GRID_SIZE).astype(int); gj = np.round(lons / GRID_SIZE).astype(int)
    di_arr = np.arange(-R, R+1); dj_arr = np.arange(-R, R+1)
    DI, DJ = np.meshgrid(di_arr, dj_arr, indexing="ij")
    # ★ Bug fix: dist2 は各グリッドオフセットの距離の2乗（2D）。
    # 元コードは [:,:,None] で3次元にしていたため dist2[:,:,ei] が ei>=1 でエラーになっていた
    dist2 = (DI * GRID_SIZE)**2 + (DJ * GRID_SIZE)**2  # shape: (2R+1, 2R+1)
    from collections import defaultdict
    agg_dict = defaultdict(float)
    for ei in range(len(valid)):
        sc = spatial_scale[ei]; q_val = EP.Q
        weight = contributions[ei] / (dist2 + sc**2) ** q_val  # dist2 は2D、ブロードキャスト
        ni = gi[ei] + DI; nj = gj[ei] + DJ
        mask = (ni>=240)&(ni<=460)&(nj>=1220)&(nj<=1460)
        ni_m = ni[mask]; nj_m = nj[mask]; w_m = weight[mask]
        for k in range(len(ni_m)):
            agg_dict[(int(ni_m[k]), int(nj_m[k]))] += float(w_m[k])
    if not agg_dict: return {}
    grid_scores = {}
    for (gi_k, gj_k), agg_val in agg_dict.items():
        v = agg_val + EP.MU
        if v > EP.MU * 1.01:
            grid_scores[(gi_k, gj_k)] = v
    return grid_scores

def _percentile_thresholds(values_arr):
    log_v = np.log(np.clip(values_arr, 0, None) + 1)
    return (np.percentile(log_v,99.8), np.percentile(log_v,98.5),
            np.percentile(log_v,95.0), np.percentile(log_v,85.0),
            np.percentile(log_v,50.0))


# ══════════════════════════════════════════════════════
# b値解析 (Gutenberg-Richter)
# ══════════════════════════════════════════════════════
def compute_bvalue_grid(quakes, grid_size=1.0, mc=1.0, min_count=5):
    """
    グリッドごとにb値を計算する。
    b = log10(e) / (mean(M) - Mc)  (最尤推定)

    ★ Bug fix: 元のデフォルト (grid_size=0.5°, mc=2.0, min_count=10) では、
    60日分の地震データであっても 0.5°グリッド（約55km四方）の中にM2.0以上の
    地震が10件以上集まることが日本周辺でもほとんど無く、b値マップが常に
    空になっていた。grid_sizeを2.0°に広げ、mc/min_countも実データ量に
    見合う値に緩和した。
    """
    from collections import defaultdict
    bins = defaultdict(list)
    for q in quakes:
        if q["mag"] < mc: continue
        gi = round(q["lat"] / grid_size)
        gj = round(q["lon"] / grid_size)
        bins[(gi, gj)].append(q["mag"])

    def _build(min_count_eff):
        result = {}
        for (gi, gj), mags in bins.items():
            if len(mags) < min_count_eff: continue
            mean_m = np.mean(mags)
            if mean_m <= mc: continue
            b = math.log10(math.e) / (mean_m - mc)
            result[(gi, gj)] = {"b": round(b, 3), "n": len(mags), "mean_m": round(mean_m, 2)}
        return result

    result = _build(min_count)
    # データが少ない期間でもマップが完全に空にならないよう、段階的に条件を緩和する
    for fallback_min_count in (5, 3):
        if result:
            break
        result = _build(fallback_min_count)
        if result:
            print(f"[b値] min_count={min_count}→{fallback_min_count}に緩和して再計算")

    print(f"[b値] {len(result)}グリッド計算完了")
    return result


# ══════════════════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════════════════
INTENSITY_COLOR = {"1":"#4ade80","2":"#a3e635","3":"#facc15","4":"#fb923c",
                   "5-":"#f87171","5+":"#ef4444","6-":"#dc2626","6+":"#b91c1c","7":"#7f1d1d"}
INTENSITY_LABEL = {"1":"震度1","2":"震度2","3":"震度3","4":"震度4",
                   "5-":"震度5弱","5+":"震度5強","6-":"震度6弱","6+":"震度6強","7":"震度7"}

def _int_color(v): return INTENSITY_COLOR.get(v,"#94a3b8")
def _int_label(v): return INTENSITY_LABEL.get(v,"不明")

def _fmt_time_jst(time_str):
    try:
        dt = datetime.fromisoformat(time_str.replace("Z","+00:00"))
        return dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
    except Exception: return time_str

def _mag_color(mag):
    if   mag >= 8.0: return "#ff00ff"
    elif mag >= 7.0: return "#dc2626"
    elif mag >= 6.0: return "#ef4444"
    elif mag >= 5.0: return "#fb923c"
    elif mag >= 4.0: return "#facc15"
    elif mag >= 3.0: return "#4ade80"
    else:            return "#94a3b8"

WIND_DIR_16 = ["北","北北東","北東","東北東","東","東南東","南東","南南東",
               "南","南南西","南西","西南西","西","西北西","北西","北北西"]


# ══════════════════════════════════════════════════════
# TAB 1: 統合地震履歴
# ══════════════════════════════════════════════════════
def render_quake_history(quakes, updated_str):
    # 直近31日
    cutoff = datetime.now(timezone.utc) - timedelta(days=31)
    recent = []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
            if t >= cutoff: recent.append(q)
        except Exception: pass
    recent.sort(key=lambda q: q.get("time",""), reverse=True)

    markers = []
    rows = ""
    for i, q in enumerate(recent):
        mag    = q.get("mag", 0)
        depth  = q.get("depth", 0)
        maxi   = q.get("max_int","").strip()
        src    = q.get("source","?")
        place  = q.get("place","不明")
        t_str  = _fmt_time_jst(q.get("time",""))
        mc     = _mag_color(mag)
        ic     = _int_color(maxi) if maxi not in ("","−","-") else "#475569"
        il     = _int_label(maxi) if maxi not in ("","−","-") else "無感"
        src_badge = {"jma_bosai":"JMA","p2p":"P2P","p2p_jma":"P2P","usgs":"USGS"}.get(src,"?")

        # ★ Bug fix: マップの円はマグニチュードで色分けする。
        # 元コードは「有感→震度色、無感→マグニチュード色」という条件分岐になっていたため、
        # 無感地震の取得漏れ（別のバグ）でデータがほぼ有感のみになり、結果的に
        # 地図上のほぼ全ての円が震度色で表示されていた。テーブルの最大震度バッジ(ic/il)は
        # そのまま残し、地図の円色は常にマグニチュード基準にする。
        marker_color = mc
        markers.append({
            "lat":q["lat"],"lon":q["lon"],"color":marker_color,
            "radius":max(4, mag*2.8),"idx":i,
            "tip":f"M{mag:.1f} / {il} [{src_badge}]",
            "pop":f"<b>{place}</b><br>{t_str} JST<br>M{mag:.1f} / {il}<br>深さ{depth:.0f}km [{src_badge}]"
        })
        rows += (
            f'<tr onclick="focusQ({i},{q["lat"]},{q["lon"]})" style="cursor:pointer" class="qrow" id="qrow_{i}">'
            f'<td class="c1">{place}</td>'
            f'<td class="c2">{t_str}</td>'
            f'<td class="c3" style="color:{mc}">M{mag:.1f}</td>'
            f'<td class="c4"><span style="background:{ic};color:#000;padding:1px 5px;border-radius:3px;font-size:11px;font-weight:700">{il}</span></td>'
            f'<td class="c2">{depth:.0f}km</td>'
            f'<td class="c2"><span style="background:#1e3a5f;padding:1px 5px;border-radius:3px;font-size:10px">{src_badge}</span></td>'
            f'</tr>'
        )

    markers_js = json.dumps(markers)
    total = len(recent)
    felt_n = sum(1 for q in recent if q.get("max_int","").strip() not in ("","−","-"))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;height:100vh;background:#0f172a;color:#fff;font-family:"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#lp{{width:380px;flex-shrink:0;background:#111827;border-right:2px solid #1f2937;display:flex;flex-direction:column;overflow:hidden}}
#lh{{padding:12px 14px 8px;background:#1f2937;border-bottom:1px solid #374151;flex-shrink:0}}
#lh h2{{font-size:14px;color:#f3f4f6;margin-bottom:4px}}
#lh p{{font-size:11px;color:#6b7280}}
#fbar{{display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid #1f2937;flex-shrink:0}}
.fb{{flex:1;padding:5px 2px;text-align:center;cursor:pointer;font-size:11px;font-weight:600;
     color:#9ca3af;border:none;background:#1f2937;border-radius:5px}}
.fb:hover{{color:#f3f4f6;background:#374151}}
.fb.on{{color:#fff;background:linear-gradient(135deg,#2563eb,#7c3aed)}}
#ls{{flex:1;overflow-y:auto}}
table{{width:100%;border-collapse:collapse}}
thead tr{{background:#1f2937;position:sticky;top:0;z-index:10}}
thead th{{padding:6px 5px;font-size:10px;color:#9ca3af;text-align:left;border-bottom:1px solid #374151}}
.qrow:hover{{background:#1e2d40}}
.c1{{padding:5px 7px;font-weight:600;color:#f3f4f6;font-size:12px}}
.c2{{padding:5px 4px;color:#9ca3af;font-size:11px}}
.c3{{padding:5px 4px;text-align:center;font-weight:700;font-size:12px}}
.c4{{padding:5px 4px;text-align:center}}
#mp{{flex:1;overflow:hidden;position:relative}}#map{{width:100%;height:100%}}
#mglg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 13px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.8;color:#f3f4f6}}
#mglg b{{font-size:12px}}
</style></head><body>
<div id="lp">
  <div id="lh">
    <h2>統合地震履歴（直近31日: {total}件 / 有感:{felt_n}件）</h2>
    <p>更新: {updated_str}</p>
  </div>
  <div id="fbar">
    <button class="fb on" onclick="filter('all',this)">すべて</button>
    <button class="fb" onclick="filter('felt',this)">有感のみ</button>
    <button class="fb" onclick="filter('unfelt',this)">無感のみ</button>
    <button class="fb" onclick="filter('jma',this)">JMA</button>
    <button class="fb" onclick="filter('usgs',this)">USGS</button>
  </div>
  <div id="ls"><table>
    <thead><tr><th>震源名</th><th>発生時刻</th><th>M</th><th>最大震度</th><th>深さ</th><th>ソース</th></tr></thead>
    <tbody id="tbody">{rows}</tbody>
  </table></div>
</div>
<div id="mp"><div id="map"></div>
  <div id="mglg">
    <b>マップの円色（マグニチュード）</b><br>
    <span style="color:#ff00ff">●</span> M8.0以上&nbsp;
    <span style="color:#dc2626">●</span> M7.0+&nbsp;
    <span style="color:#ef4444">●</span> M6.0+<br>
    <span style="color:#fb923c">●</span> M5.0+&nbsp;
    <span style="color:#facc15">●</span> M4.0+&nbsp;
    <span style="color:#4ade80">●</span> M3.0+&nbsp;
    <span style="color:#94a3b8">●</span> M3.0未満
  </div>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK={markers_js};
var layers=MK.map(function(d){{
  return L.circleMarker([d.lat,d.lon],{{radius:d.radius,color:d.color,fillColor:d.color,fillOpacity:0.85,weight:1}})
          .bindTooltip(d.tip).bindPopup(d.pop);
}});
var lg=L.layerGroup(layers).addTo(map);

function focusQ(idx,lat,lon){{
  document.querySelectorAll('.qrow').forEach(function(r){{r.style.background=''}});
  var row=document.getElementById('qrow_'+idx); if(row)row.style.background='#1e3a5f';
  map.flyTo([lat,lon],8,{{duration:0.7}});
  if(layers[idx])setTimeout(function(){{layers[idx].openPopup()}},800);
}}

var allRows=Array.from(document.querySelectorAll('.qrow'));
var MODE_MAP={{'all':function(r){{return true}},'felt':function(r){{return r.querySelector('.c4 span').style.background!='rgb(71, 85, 105)'}},'unfelt':function(r){{return r.querySelector('.c4 span').style.background==='rgb(71, 85, 105)'}},'jma':function(r){{return r.querySelector('.c2:last-child span').textContent==='JMA'}},'usgs':function(r){{return r.querySelector('.c2:last-child span').textContent==='USGS'}}}};

function filter(mode,btn){{
  document.querySelectorAll('.fb').forEach(function(b){{b.classList.remove('on')}});
  btn.classList.add('on');
  var fn=MODE_MAP[mode];
  allRows.forEach(function(r){{r.style.display=fn(r)?'':'none'}});
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════
# TAB 2: ETASマップ（研究用強化版）
# ══════════════════════════════════════════════════════
def render_etas(grid_scores, quakes, updated_str):
    src_count = {}
    for q in quakes: src_count[q.get("source","?")] = src_count.get(q.get("source","?"),0)+1

    cells = []
    if grid_scores:
        vals = np.array(list(grid_scores.values()))
        th5,th4,th3,th2,th1 = _percentile_thresholds(vals)
        for (gi,gj), score in grid_scores.items():
            s = math.log(score+1)
            if   s>=th5: lv=5
            elif s>=th4: lv=4
            elif s>=th3: lv=3
            elif s>=th2: lv=2
            elif s>=th1: lv=1
            else: continue
            cells.append({"lat":gi*GRID_SIZE,"lon":gj*GRID_SIZE,
                          "color":ETAS_COLOR[lv],"lv":lv,"score":round(score,4)})

    # 最近72時間の地震をマーカーとしてオーバーレイ
    cutoff72 = datetime.now(timezone.utc) - timedelta(hours=72)
    recent_markers = []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
            if t < cutoff72: continue
            mag = q.get("mag",0)
            recent_markers.append({
                "lat":q["lat"],"lon":q["lon"],"mag":mag,
                "tip":f"M{mag:.1f} {_fmt_time_jst(q['time'])}",
                "r":max(3,mag*2.5)
            })
        except Exception: pass

    cells_js = json.dumps(cells)
    recent_js = json.dumps(recent_markers)
    gs = GRID_SIZE
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#tb{{display:flex;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0;padding:6px 10px;gap:8px;align-items:center}}
#tb span{{font-size:12px;color:#9ca3af}}
.tog{{padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer;border:none;border-radius:5px;background:#1f2937;color:#9ca3af}}
.tog.on{{background:#2563eb;color:#fff}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #8800cc;font-size:12px;line-height:2;color:#f3f4f6}}
#info{{position:fixed;top:60px;right:10px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 14px;border-radius:8px;border:1px solid #374151;font-size:11px;color:#9ca3af;max-width:200px}}
</style></head><body>
<div id="tb">
  <span>表示レイヤー:</span>
  <button class="tog on" id="togEtas" onclick="toggleLayer('etas',this)">ETASグリッド</button>
  <button class="tog on" id="togRecent" onclick="toggleLayer('recent',this)">直近72h地震</button>
  <span style="margin-left:auto;color:#6b7280;font-size:11px">更新: {updated_str}</span>
</div>
<div id="map"></div>
<div id="lg">
  <b>ETAS 地震発生確率</b><br>
  <span style="color:#1a0033;background:#1a0033;padding:0 6px">■</span> Lv5 (上位0.2%)<br>
  <span style="color:#8000ff">■</span> Lv4 (上位1.5%)<br>
  <span style="color:#ff0000">■</span> Lv3 (上位5.0%)<br>
  <span style="color:#ff8800">■</span> Lv2 (上位15%)<br>
  <span style="color:#66ccff">■</span> Lv1 (上位50%)<br>
  <hr style="border-color:#374151;margin:5px 0">
  <small>JMA:{src_count.get('jma_bosai',0)} P2P:{src_count.get('p2p',0)+src_count.get('p2p_jma',0)} USGS:{src_count.get('usgs',0)}<br>計{len(quakes)}件</small>
</div>
<div id="info">
  <b style="color:#60a5fa">研究メモ</b><br>
  ETASモデルは過去60日の地震から算出。<br>
  b値・GNSS比較のベースラインとして使用。<br>
  残差 = 実測 − ETAS予測 (Phase2で実装予定)
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}

var CELLS={cells_js};
var gs={gs};
var etasGroup=L.layerGroup().addTo(map);
CELLS.forEach(function(c){{
  L.rectangle([[c.lat,c.lon],[c.lat+gs,c.lon+gs]],
    {{color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:0.65}})
   .bindTooltip('Level '+c.lv+' / rate='+c.score).addTo(etasGroup);
}});

var RECENT={recent_js};
var recentGroup=L.layerGroup().addTo(map);
RECENT.forEach(function(d){{
  L.circleMarker([d.lat,d.lon],{{radius:d.r,color:'#fff',fillColor:'#fff',fillOpacity:0.9,weight:1.5}})
   .bindTooltip(d.tip).addTo(recentGroup);
}});

var groups={{'etas':etasGroup,'recent':recentGroup}};
function toggleLayer(key,btn){{
  btn.classList.toggle('on');
  var g=groups[key];
  if(map.hasLayer(g))map.removeLayer(g); else g.addTo(map);
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════
# TAB 3: b値マップ
# ══════════════════════════════════════════════════════
def render_bvalue(bvalue_grid, quakes, updated_str):
    # b値カラースケール: 低b値(赤)→高b値(青)
    # 低b値は大地震の前兆として注目される
    def b_color(b):
        # b値の典型的な範囲は 0.5 〜 2.0
        ratio = max(0.0, min(1.0, (b - 0.5) / 1.5))
        r = int(220 * (1 - ratio))
        g = int(60 + 100 * ratio)
        bl = int(220 * ratio)
        return f"#{r:02x}{g:02x}{bl:02x}"

    cells = []
    for (gi, gj), info in bvalue_grid.items():
        b = info["b"]; n = info["n"]; mean_m = info["mean_m"]
        lat = gi * 1.0; lon = gj * 1.0
        if not (24<=lat<=46 and 122<=lon<=146): continue
        cells.append({
            "lat": lat, "lon": lon,
            "color": b_color(b), "b": b, "n": n, "mean_m": mean_m
        })

    cells_js = json.dumps(cells)
    gs = 1.0  # b値計算のグリッドサイズ

    # 統計サマリー
    if bvalue_grid:
        all_b = [v["b"] for v in bvalue_grid.values()]
        b_mean = round(np.mean(all_b), 3)
        b_min  = round(np.min(all_b), 3)
        b_max  = round(np.max(all_b), 3)
        b_std  = round(np.std(all_b), 3)
    else:
        b_mean = b_min = b_max = b_std = "N/A"

    total_used = sum(v["n"] for v in bvalue_grid.values())

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#hdr{{padding:8px 14px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0;
       display:flex;align-items:center;gap:16px;font-size:12px;color:#9ca3af}}
#hdr b{{color:#f3f4f6;font-size:13px}}
.stat{{background:#1f2937;padding:4px 10px;border-radius:5px;font-size:11px;color:#d1d5db}}
.stat span{{color:#60a5fa;font-weight:700}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:2;color:#f3f4f6}}
#info{{position:fixed;top:60px;right:10px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 14px;border-radius:8px;border:1px solid #374151;font-size:11px;color:#9ca3af;max-width:210px}}
</style></head><body>
<div id="hdr">
  <b>b値マップ (Gutenberg-Richter則)</b>
  <div class="stat">グリッド数: <span>{len(cells)}</span></div>
  <div class="stat">使用地震数: <span>{total_used}</span></div>
  <div class="stat">b平均: <span>{b_mean}</span></div>
  <div class="stat">b最小: <span style="color:#ef4444">{b_min}</span></div>
  <div class="stat">b最大: <span style="color:#60a5fa">{b_max}</span></div>
  <div style="margin-left:auto;color:#6b7280">更新: {updated_str}</div>
</div>
<div id="map"></div>
<div id="lg">
  <b>b値カラースケール</b><br>
  <div style="width:130px;height:10px;border-radius:3px;
    background:linear-gradient(to right,#dc3c3c,#60a0dc);margin:5px 0 2px"></div>
  <div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af">
    <span>低 (0.5)</span><span>高 (2.0)</span>
  </div>
  <hr style="border-color:#374151;margin:5px 0">
  <small>低b値地域 = 大地震の可能性<br>Mc = 2.0 / 最小{10}件/グリッド<br>グリッドサイズ: 0.5°</small>
</div>
<div id="info">
  <b style="color:#60a5fa">研究メモ</b><br>
  Gutenberg-Richter則:<br>
  log N = a − bM<br><br>
  最尤推定:<br>
  b = log₁₀(e) / (M̄ − Mc)<br><br>
  ETAS異常地域と比較して<br>
  b値低下の相関を調べる。<br>
  (Phase4)
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var CELLS={cells_js};
var gs={gs};
CELLS.forEach(function(c){{
  L.rectangle([[c.lat,c.lon],[c.lat+gs,c.lon+gs]],
    {{color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:0.75}})
   .bindTooltip('b='+c.b+' / N='+c.n+' / M̄='+c.mean_m)
   .bindPopup('<b>b値: '+c.b+'</b><br>地震数: '+c.n+'<br>平均M: '+c.mean_m)
   .addTo(map);
}});
</script></body></html>"""


# ══════════════════════════════════════════════════════
# TAB 4: TEC（電離圏全電子数）
# ══════════════════════════════════════════════════════
def render_tec(updated_str):
    # NICT SCIDASの最新TECマップをiframe埋め込み
    # 注: 直接埋め込めない場合はリンクとサムネイル表示に切り替え
    nict_url = "https://aer-nc-web.nict.go.jp/iono/GEONET/latest_map.png"
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    date_str = now_jst.strftime("%Y年%m月%d日 %H:%M JST")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#f3f4f6;font-family:"Helvetica Neue",Arial,sans-serif;height:100vh;display:flex;flex-direction:column}}
#hdr{{padding:12px 20px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0}}
#hdr h2{{font-size:15px;font-weight:700;color:#f3f4f6;margin-bottom:4px}}
#hdr p{{font-size:11px;color:#6b7280}}
#body{{flex:1;display:flex;gap:0;overflow:hidden}}
#left{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px;overflow-y:auto}}
#right{{width:280px;flex-shrink:0;background:#111827;border-left:2px solid #1f2937;padding:16px;overflow-y:auto}}
.card{{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:16px;margin-bottom:12px;width:100%;max-width:700px}}
.card h3{{font-size:13px;font-weight:700;color:#60a5fa;margin-bottom:8px}}
.card p{{font-size:12px;color:#9ca3af;line-height:1.7}}
.link-btn{{display:block;padding:10px 16px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
           color:#fff;font-weight:700;font-size:13px;text-decoration:none;border-radius:8px;
           text-align:center;margin-top:8px;transition:opacity 0.2s}}
.link-btn:hover{{opacity:0.85}}
.tec-img{{width:100%;border-radius:6px;border:1px solid #374151;margin-top:8px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;
        background:#1e3a5f;color:#93c5fd;margin-right:4px;margin-bottom:4px}}
#right h3{{font-size:13px;font-weight:700;color:#f3f4f6;margin-bottom:10px;border-bottom:1px solid #374151;padding-bottom:6px}}
.note{{font-size:12px;color:#9ca3af;line-height:1.8;margin-bottom:12px}}
.phase{{background:#1f2937;border-left:3px solid #7c3aed;padding:8px 10px;border-radius:0 6px 6px 0;margin-bottom:8px;font-size:11px;color:#d1d5db}}
</style></head><body>
<div id="hdr">
  <h2>TEC（電離圏全電子数）モニタリング</h2>
  <p>NICT GEONET TEC / {date_str} / 更新: {updated_str}</p>
</div>
<div id="body">
  <div id="left">
    <div class="card">
      <h3>NICT 最新 TEC マップ（GEONET）</h3>
      <p>NICTが提供するGEONETベースのTECマップです。地震前後の電離圏擾乱をモニタリングします。</p>
      <img src="{nict_url}" class="tec-img" alt="NICT TEC Map"
           onerror="this.style.display='none';document.getElementById('img-err').style.display='block'">
      <div id="img-err" style="display:none;padding:12px;background:#1f2937;border-radius:6px;margin-top:8px;font-size:12px;color:#9ca3af">
        ⚠ 画像の直接読み込みができません（CORSポリシー）。<br>下のリンクから直接確認してください。
      </div>
      <a href="https://aer-nc-web.nict.go.jp/iono/GEONET/" target="_blank" class="link-btn">
        🌐 NICT GEONET TECページを開く
      </a>
    </div>
    <div class="card">
      <h3>その他のデータソース</h3>
      <p>TECデータを提供する主要な機関・ツール</p>
      <div style="margin-top:8px">
        <a href="https://scidas.nict.go.jp/" target="_blank" class="link-btn" style="margin-bottom:6px">
          📡 NICT SCIDAS（宇宙天気情報）
        </a>
        <a href="https://www.gsi.go.jp/denshi/denshi.html" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#065f46,#047857);margin-bottom:6px">
          🛰 国土地理院 電子基準点 TEC
        </a>
        <a href="https://ionex.jpl.nasa.gov/" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#7f1d1d,#b91c1c)">
          🚀 JPL Global Ionosphere Maps (GIM)
        </a>
      </div>
    </div>
    <div class="card">
      <h3>現在の取得状況</h3>
      <p style="margin-bottom:8px">
        <span class="badge">状態</span>
        <span style="color:#fbbf24;font-weight:700">⚠ 準備中</span> — NICT APIへの直接アクセスは今後実装予定。
      </p>
      <p>TECの時系列データ自動取得については、NICTのIONEX形式データ（ftp://ftp.nict.go.jp/）からの自動ダウンロードを予定しています。</p>
    </div>
  </div>
  <div id="right">
    <h3>TECとは</h3>
    <div class="note">
      電離圏の全電子数 (Total Electron Content)。<br>
      大地震の数時間〜数日前に異常増減する<br>
      という報告があるが、<br>
      <b style="color:#fbbf24">学術的には未確立</b>。<br>
      1 TECU = 10¹⁶ el/m²
    </div>
  </div>
</div>
</body></html>"""


# ══════════════════════════════════════════════════════
# TAB 5: GNSS（地殻変動）
# ══════════════════════════════════════════════════════
def _make_gnss_vectors():
    """
    GEONETの代表的な電子基準点に対して、
    日本列島のプレート運動を模した擬似変位ベクトルを生成する。
    実データ取得（F5ソリューション）は Phase 5 で実装予定。
    ベクトル: [lat, lon, dE_mm/yr, dN_mm/yr] (東方向・北方向変位速度)
    """
    # 日本列島の主要なプレート運動パターンを反映した代表点
    # 参考: 国土地理院 GEONET F3解 基準速度場（Honshu fixed）
    stations = [
        # 北海道（オホーツクプレート、北西方向）
        [43.1, 141.3, -10, -5],  [42.9, 143.2, -8, -6],
        [41.8, 140.7, -12, -4],
        # 東北（太平洋プレート沈み込み、西方向成分）
        [40.8, 140.7, -22, -8],  [39.7, 141.1, -25, -7],
        [38.3, 140.9, -28, -6],  [37.7, 140.5, -30, -5],
        [37.0, 140.4, -28, -5],
        # 関東（複合プレート境界、南西方向）
        [36.6, 140.9, -20, -8],  [36.4, 140.5, -22, -9],
        [36.1, 140.1, -24, -10], [35.9, 139.6, -20, -12],
        [35.7, 139.7, -18, -13], [35.5, 139.6, -16, -14],
        # 中部（ユーラシアプレート）
        [36.7, 137.2, -5,  -8],  [36.6, 136.6, -4, -9],
        [36.1, 136.2, -3, -10],  [35.7, 138.6, -10, -12],
        [35.2, 136.9, -4,  -9],
        # 近畿・中国・四国
        [35.0, 135.8, -2, -10],  [34.7, 135.5, -1, -11],
        [34.4, 132.5,  5, -12],  [33.8, 132.8,  6, -11],
        [33.6, 133.5,  4, -10],
        # 九州（フィリピン海プレート）
        [33.3, 131.6, 12, -10],  [33.2, 130.3, 14, -9],
        [32.8, 130.7, 15, -8],   [31.9, 131.4, 18, -6],
        [31.6, 130.6, 16, -7],
        # 沖縄・南西諸島（琉球弧）
        [26.2, 127.7, 30, -5],   [24.3, 124.2, 35, -3],
    ]
    return stations

def render_gnss(updated_str):
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    date_str = now_jst.strftime("%Y年%m月%d日")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;color:#f3f4f6;font-family:"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#hdr{{padding:10px 16px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0;display:flex;align-items:center;gap:12px}}
#hdr h2{{font-size:14px;font-weight:700}}
#hdr p{{font-size:11px;color:#6b7280}}
.hbadge{{padding:3px 10px;border-radius:5px;font-size:11px;font-weight:700}}
#content{{flex:1;display:flex;overflow:hidden}}
#map{{flex:1}}
#panel{{width:260px;flex-shrink:0;background:#111827;border-left:2px solid #1f2937;overflow-y:auto;padding:14px}}
.sec{{margin-bottom:14px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid #1f2937;padding-bottom:4px}}
.sec p{{font-size:11px;color:#9ca3af;line-height:1.7}}
.link-btn{{display:block;padding:8px 12px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
           color:#fff;font-weight:700;font-size:12px;text-decoration:none;border-radius:6px;
           text-align:center;margin-bottom:6px;transition:opacity 0.2s}}
.link-btn:hover{{opacity:0.85}}
.phase{{background:#1f2937;border-left:3px solid #7c3aed;padding:6px 8px;border-radius:0 5px 5px 0;
        margin-bottom:6px;font-size:11px;color:#d1d5db}}
</style></head><body>
<div id="hdr">
  <div>
    <h2>GNSS 地殻変動モニタリング（GEONET）</h2>
    <p>国土地理院 電子基準点ネットワーク / {date_str} / 更新: {updated_str}</p>
  </div>
  <span class="hbadge" style="background:#1e3a5f;color:#93c5fd;margin-left:auto">Phase 5 実装予定</span>
</div>
<div id="content">
  <div id="map"></div>
  <div id="panel">
    <div class="sec">
      <h3>データソース</h3>
      <a href="https://terras.gsi.go.jp/" target="_blank" class="link-btn">
        🛰 国土地理院 TERRAS
      </a>
      <a href="https://www.gsi.go.jp/kanshi/gnss_crust.html" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#065f46,#047857)">
        📊 GEONET 地殻変動情報
      </a>
      <a href="https://mekira.gsi.go.jp/" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#78350f,#b45309)">
        📡 MEKIRA（地殻変動モニタ）
      </a>
    </div>
    <div class="sec">
      <h3>実装予定の内容</h3>
      <p>
        ① GEONET F5座標の自動DL<br>
        ② 各基準点の変位ベクトル計算<br>
        ③ ETAS残差マップとの重ね合わせ<br>
        ④ 統計的相関検定 (Spearman ρ)
      </p>
    </div>
    <div class="sec">
      <h3>現在の地図</h3>
      <p>下の地図は GEONET 電子基準点の<br>配置を示すプレースホルダーです。<br>実データは Phase 5 で実装。</p>
    </div>
  </div>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}

// GEONET 電子基準点の代表的な配置をプレースホルダーとして表示
// 実際のGEONET座標は約1300点存在する
var placeholders=[
  [43.1,141.3],[42.9,143.2],[41.8,140.7],[40.8,140.7],[39.7,141.1],[38.3,140.9],
  [37.7,140.5],[37.0,140.4],[36.6,140.9],[36.4,140.5],[36.1,140.1],[35.9,139.6],
  [35.7,139.7],[35.5,139.6],[35.2,136.9],[35.0,135.8],[34.7,135.5],[34.4,132.5],
  [33.8,132.8],[33.6,133.5],[33.3,131.6],[33.2,130.3],[32.8,130.7],[31.9,131.4],
  [31.6,130.6],[26.2,127.7],[24.3,124.2],
  [36.7,137.2],[36.6,136.6],[36.1,136.2],[35.7,138.6],[35.4,133.9],[35.5,134.2]
];

placeholders.forEach(function(p){{
  L.circleMarker(p,{{radius:4,color:'#34d399',fillColor:'#34d399',fillOpacity:0.7,weight:1}})
   .bindTooltip('GEONET電子基準点（プレースホルダー）').addTo(map);
}});

// 凡例
var legend=L.control({{position:'bottomleft'}});
legend.onAdd=function(){{
  var d=L.DomUtil.create('div');
  d.style.cssText='background:rgba(17,24,39,.92);padding:10px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;color:#f3f4f6;line-height:2';
  d.innerHTML='<b>GNSS 電子基準点</b><br><span style="color:#34d399">●</span> GEONET基準点（仮）<br><hr style="border-color:#374151;margin:4px 0"><small>変位ベクトル表示は Phase 5 で実装</small>';
  return d;
}};
legend.addTo(map);
</script></body></html>"""


# ══════════════════════════════════════════════════════
# TAB 6: 海面気圧（アメダス）
# ══════════════════════════════════════════════════════
def _fetch_amedas_table():
    url = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
    try:
        raw = requests.get(url, timeout=10, headers={"User-Agent":"SeismoApp/5.0"}).json()
        table = {}
        for sid, info in raw.items():
            lr = info.get("lat",[0,0]); lo = info.get("lon",[0,0])
            table[sid] = {"name":info.get("kjName",sid),
                          "lat":lr[0]+lr[1]/60.0, "lon":lo[0]+lo[1]/60.0}
        return table
    except Exception as e:
        print(f"[AMEDAS table] {e}"); return {}

def _fetch_amedas_latest():
    try:
        t_text = requests.get("https://www.jma.go.jp/bosai/amedas/data/latest_time.txt",
                              timeout=8, headers={"User-Agent":"SeismoApp/5.0"}).text.strip()
        dt = datetime.fromisoformat(t_text)
        ts = dt.strftime("%Y%m%d%H%M%S")
        data = requests.get(f"https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json",
                            timeout=10, headers={"User-Agent":"SeismoApp/5.0"}).json()
        time_label = dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M JST")
        return data, time_label
    except Exception as e:
        print(f"[AMEDAS obs] {e}"); return {}, "取得失敗"

def _pres_color(val, vmin, vmax):
    ratio = max(0.0, min(1.0, (val - vmin) / max(vmax - vmin, 0.01)))
    # 低気圧(赤/紫) → 高気圧(青/白)
    stops = [(0,(180,0,180)),(0.25,(160,60,240)),(0.50,(100,160,255)),(0.75,(200,220,255)),(1.0,(255,255,255))]
    r,g,b = stops[-1][1]
    for k in range(len(stops)-1):
        lo,hi = stops[k][0], stops[k+1][0]
        if lo <= ratio <= hi:
            t = (ratio-lo)/(hi-lo)
            r0,g0,b0 = stops[k][1]; r1,g1,b1 = stops[k+1][1]
            r,g,b = int(r0+(r1-r0)*t),int(g0+(g1-g0)*t),int(b0+(b1-b0)*t)
            break
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"

def render_pressure(updated_str):
    global _amedas_cache
    now = time.time()
    if _amedas_cache["data"] and now - _amedas_cache["ts"] < AMEDAS_CACHE_SEC:
        cached = _amedas_cache["data"]
        table, obs_data, time_label = cached["table"], cached["obs"], cached["label"]
    else:
        table = _fetch_amedas_table(); obs_data, time_label = _fetch_amedas_latest()
        _amedas_cache = {"data":{"table":table,"obs":obs_data,"label":time_label},"ts":now}

    def _gv(obs, key):
        raw = obs.get(key)
        return raw[0] if isinstance(raw,list) and len(raw)>0 and raw[0] is not None else None

    pres_vals_all = []
    markers = []
    for sid, obs in obs_data.items():
        info = table.get(sid)
        if not info: continue
        lat,lon = info["lat"],info["lon"]
        if not (24<=lat<=46 and 122<=lon<=146): continue
        pres = _gv(obs,"normalPressure")
        if pres is None: continue
        pres_vals_all.append(pres)

    pr_min = min(pres_vals_all) if pres_vals_all else 980
    pr_max = max(pres_vals_all) if pres_vals_all else 1030
    pr_mean = round(sum(pres_vals_all)/len(pres_vals_all), 1) if pres_vals_all else 0

    for sid, obs in obs_data.items():
        info = table.get(sid)
        if not info: continue
        lat,lon = info["lat"],info["lon"]
        if not (24<=lat<=46 and 122<=lon<=146): continue
        pres = _gv(obs,"normalPressure")
        if pres is None: continue
        name = info["name"]
        markers.append({
            "lat":lat,"lon":lon,
            "color":_pres_color(pres, pr_min, pr_max),
            "pres":pres,
            "tip":f"{name} {pres}hPa",
            "pop":f"<b>{name}</b><br>海面気圧: <b>{pres}hPa</b>"
        })

    markers_js = json.dumps(markers)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#hdr{{padding:8px 16px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0;
       display:flex;align-items:center;gap:14px;font-size:12px;color:#9ca3af}}
#hdr b{{color:#f3f4f6;font-size:14px}}
.stat{{background:#1f2937;padding:4px 10px;border-radius:5px;font-size:11px;color:#d1d5db}}
.stat span{{color:#60a5fa;font-weight:700}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:2;color:#f3f4f6}}
</style></head><body>
<div id="hdr">
  <b>海面平均気圧マップ</b>
  <div class="stat">観測点数: <span>{len(markers)}</span></div>
  <div class="stat">最低: <span style="color:#c084fc">{pr_min:.1f}hPa</span></div>
  <div class="stat">最高: <span style="color:#93c5fd">{pr_max:.1f}hPa</span></div>
  <div class="stat">平均: <span>{pr_mean}hPa</span></div>
  <div style="margin-left:auto;color:#6b7280">{time_label} / 更新: {updated_str}</div>
</div>
<div id="map"></div>
<div id="lg">
  <b>海面気圧スケール</b><br>
  <div style="width:130px;height:10px;border-radius:3px;
    background:linear-gradient(to right,#b400b4,#a03cf0,#64a0ff,#c8dcff,#ffffff);margin:5px 0 2px"></div>
  <div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af">
    <span>低({pr_min:.0f})</span><span>高({pr_max:.0f})hPa</span>
  </div>
  <hr style="border-color:#374151;margin:5px 0">
  <small>出典: 気象庁アメダス<br>正規圧力（海面気圧）<br>{updated_str}</small>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK={markers_js};
MK.forEach(function(d){{
  L.circleMarker([d.lat,d.lon],{{radius:4,color:d.color,fillColor:d.color,fillOpacity:1.0,weight:0.5}})
   .bindTooltip(d.tip).bindPopup(d.pop).addTo(map);
}});
</script></body></html>"""


# ══════════════════════════════════════════════════════
# メインページ（タブシェル）
# ══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════
# TAB 7: スナップショット（1時間ごとの解析結果ログ）
# ══════════════════════════════════════════════════════
def render_snapshots(updated_str):
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
__LEAFLET_CDN__
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{display:flex;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif;color:#f3f4f6}
#list{width:250px;flex-shrink:0;background:#111827;border-right:2px solid #1f2937;overflow-y:auto}
#hdr{padding:10px 14px;border-bottom:2px solid #1f2937;font-size:12px;color:#9ca3af;position:sticky;top:0;background:#111827}
#hdr b{color:#f3f4f6;font-size:13px;display:block;margin-bottom:2px}
.snap-item{padding:9px 14px;cursor:pointer;border-bottom:1px solid #1f2937;font-size:12px;color:#d1d5db}
.snap-item:hover{background:#161b22}
.snap-item.active{background:#162032;border-left:3px solid #3b82f6;color:#fff}
.snap-time{font-weight:600;color:#60a5fa;font-size:12px}
.snap-meta{font-size:10px;color:#6b7280;margin-top:2px}
#detail{flex:1;overflow-y:auto;display:flex;flex-direction:column}
#detailTop{padding:14px 18px 10px;flex-shrink:0}
#detailTop h2{font-size:15px;color:#f3f4f6;margin-bottom:4px}
#detailTop .sub{font-size:11px;color:#6b7280;margin-bottom:12px}
.stat-row{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.stat{background:#1f2937;padding:7px 12px;border-radius:6px;font-size:11px;color:#d1d5db}
.stat span{display:block;color:#60a5fa;font-weight:700;font-size:15px;margin-top:2px}
.tog{padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer;border:none;border-radius:5px;background:#1f2937;color:#9ca3af;margin-right:6px}
.tog.on{background:#2563eb;color:#fff}
#mapWrap{position:relative;height:440px;flex-shrink:0;border-top:1px solid #1f2937;border-bottom:1px solid #1f2937}
#map{width:100%;height:100%}
#lg{position:absolute;bottom:14px;left:14px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 12px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.9;color:#f3f4f6}
#tablesWrap{padding:16px 18px 26px}
table{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:22px}
th{text-align:left;padding:6px 8px;color:#6b7280;border-bottom:1px solid #374151;font-weight:600}
td{padding:5px 8px;border-bottom:1px solid #1f2937;color:#d1d5db}
tr:hover td{background:#161b22}
.section-title{font-size:12px;color:#9ca3af;font-weight:700;margin:6px 0 8px;text-transform:uppercase;letter-spacing:0.5px}
.empty{color:#4b5563;font-size:12px;padding:30px;text-align:center}
#loading{padding:30px;text-align:center;color:#6b7280;font-size:12px}
</style></head><body>
<div id="list">
  <div id="hdr"><b>スナップショット一覧</b>1時間ごとの解析結果ログ</div>
  <div id="items"><div id="loading">読込中...</div></div>
</div>
<div id="detail"><div class="empty" style="margin:auto">左のリストからスナップショットを選択してください</div></div>
<script>
var GRID_SIZE = __GRID_SIZE__;
var ETAS_COLOR = {5:'#1a0033',4:'#8000ff',3:'#ff0000',2:'#ff8800',1:'#66ccff'};
var snapshots = [];
var map = null;
var curGroups = {};

function fmtTime(fname){
  var m = fname.match(/(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})/);
  if(!m) return fname;
  // ファイル名はUTC基準のタイムスタンプなのでJST(+9h)に変換して表示する
  var utcMs = Date.UTC(parseInt(m[1]), parseInt(m[2])-1, parseInt(m[3]), parseInt(m[4]), parseInt(m[5]), parseInt(m[6]));
  var jst = new Date(utcMs + 9*3600*1000);
  function pad(n){return n<10?'0'+n:''+n}
  return jst.getUTCFullYear()+'-'+pad(jst.getUTCMonth()+1)+'-'+pad(jst.getUTCDate())+' '+
         pad(jst.getUTCHours())+':'+pad(jst.getUTCMinutes())+':'+pad(jst.getUTCSeconds())+' JST';
}

function bColor(b){
  var ratio = Math.max(0, Math.min(1, (b-0.5)/1.5));
  var r = Math.round(220*(1-ratio)), g = Math.round(60+100*ratio), bl = Math.round(220*ratio);
  function h(v){var s=v.toString(16); return s.length<2?'0'+s:s}
  return '#'+h(r)+h(g)+h(bl);
}

function percentileThresholds(sortedAsc, p){
  if(sortedAsc.length===0) return 0;
  var idx = Math.min(sortedAsc.length-1, Math.max(0, Math.round(p/100*(sortedAsc.length-1))));
  return sortedAsc[idx];
}

function buildEtasCells(etasObj){
  var entries = Object.entries(etasObj);
  if(entries.length===0) return [];
  var logVals = entries.map(function(kv){return Math.log(kv[1]+1)}).sort(function(a,b){return a-b});
  var th5=percentileThresholds(logVals,99.8), th4=percentileThresholds(logVals,98.5),
      th3=percentileThresholds(logVals,95.0), th2=percentileThresholds(logVals,85.0),
      th1=percentileThresholds(logVals,50.0);
  var cells = [];
  entries.forEach(function(kv){
    var parts = kv[0].split('_');
    var score = kv[1];
    var s = Math.log(score+1);
    var lv;
    if(s>=th5) lv=5; else if(s>=th4) lv=4; else if(s>=th3) lv=3;
    else if(s>=th2) lv=2; else if(s>=th1) lv=1; else return;
    cells.push({lat:parseInt(parts[0])*GRID_SIZE, lon:parseInt(parts[1])*GRID_SIZE,
                color:ETAS_COLOR[lv], lv:lv, score:score});
  });
  return cells;
}

function buildBvalueCells(bvObj){
  return Object.entries(bvObj).map(function(kv){
    var parts = kv[0].split('_'); var info = kv[1];
    return {lat:parseInt(parts[0])*1.0, lon:parseInt(parts[1])*1.0,
            color:bColor(info.b), b:info.b, n:info.n, mean_m:info.mean_m};
  });
}

fetch('/snapshots').then(function(r){return r.json()}).then(function(d){
  snapshots = d.snapshots || [];
  var wrap = document.getElementById('items');
  if(snapshots.length===0){
    wrap.innerHTML = '<div class="empty">まだスナップショットがありません<br>(起動後1時間ほどで作成されます)</div>';
    return;
  }
  wrap.innerHTML = snapshots.map(function(f,i){
    return '<div class="snap-item" data-i="'+i+'" onclick="selectSnap('+i+')">'+
      '<div class="snap-time">'+fmtTime(f)+'</div>'+
      '<div class="snap-meta">'+f+'</div></div>';
  }).join('');
  selectSnap(0);
}).catch(function(e){
  document.getElementById('items').innerHTML = '<div class="empty">読込失敗</div>';
});

function selectSnap(i){
  document.querySelectorAll('.snap-item').forEach(function(el){el.classList.toggle('active', el.dataset.i==i)});
  var fname = snapshots[i];
  document.getElementById('detail').innerHTML = '<div id="loading">読込中...</div>';
  fetch('/snapshots/'+fname).then(function(r){return r.json()}).then(function(d){
    renderDetail(fname, d);
  }).catch(function(e){
    document.getElementById('detail').innerHTML = '<div class="empty">読込失敗</div>';
  });
}

function renderDetail(fname, d){
  var etasCount = Object.keys(d.etas||{}).length;
  var bvCount = Object.keys(d.bvalue||{}).length;

  var etasEntries = Object.entries(d.etas||{}).map(function(kv){
    var parts = kv[0].split('_');
    return {lat:(parseInt(parts[0])*GRID_SIZE).toFixed(2), lon:(parseInt(parts[1])*GRID_SIZE).toFixed(2), score:kv[1]};
  }).sort(function(a,b){return b.score-a.score}).slice(0,15);

  var bvEntries = Object.entries(d.bvalue||{}).map(function(kv){
    var parts = kv[0].split('_');
    return {lat:(parseInt(parts[0])*1.0).toFixed(1), lon:(parseInt(parts[1])*1.0).toFixed(1),
             b:kv[1].b, n:kv[1].n, mean_m:kv[1].mean_m};
  }).sort(function(a,b){return b.n-a.n}).slice(0,15);

  var html =
    '<div id="detailTop">'+
      '<h2>'+fmtTime(fname)+'</h2>'+
      '<div class="sub">'+fname+' ／ 保存時点の更新表示: '+(d.updated||'-')+'</div>'+
      '<div class="stat-row">'+
        '<div class="stat">地震件数<span>'+d.quake_count+'</span></div>'+
        '<div class="stat">ETAS格子数<span>'+etasCount+'</span></div>'+
        '<div class="stat">b値格子数<span>'+bvCount+'</span></div>'+
      '</div>'+
      '<div>'+
        '<button class="tog on" id="togEtas" onclick="toggleLayer(\\'etas\\',this)">ETASグリッド</button>'+
        '<button class="tog on" id="togBv" onclick="toggleLayer(\\'bvalue\\',this)">b値グリッド</button>'+
      '</div>'+
    '</div>'+
    '<div id="mapWrap"><div id="map"></div>'+
      '<div id="lg">'+
        '<b>ETAS</b><br>'+
        '<span style="color:#1a0033">■</span> Lv5&nbsp; <span style="color:#8000ff">■</span> Lv4&nbsp; '+
        '<span style="color:#ff0000">■</span> Lv3&nbsp; <span style="color:#ff8800">■</span> Lv2&nbsp; '+
        '<span style="color:#66ccff">■</span> Lv1<br>'+
        '<hr style="border-color:#374151;margin:5px 0">'+
        '<b>b値</b><br>'+
        '<div style="width:110px;height:8px;border-radius:3px;background:linear-gradient(to right,#dc3c3c,#60a0dc);margin:4px 0 2px"></div>'+
        '<div style="display:flex;justify-content:space-between;width:110px;font-size:9px;color:#9ca3af">'+
          '<span>低(0.5)</span><span>高(2.0)</span></div>'+
      '</div>'+
    '</div>'+
    '<div id="tablesWrap">'+
      '<div class="section-title">ETASスコア上位グリッド (上位15件)</div>'+
      (etasEntries.length? ('<table><tr><th>緯度</th><th>経度</th><th>ETASスコア</th></tr>'+
        etasEntries.map(function(e){return '<tr><td>'+e.lat+'</td><td>'+e.lon+'</td><td>'+e.score.toFixed(4)+'</td></tr>'}).join('')
        +'</table>') : '<div class="empty">データなし</div>')+
      '<div class="section-title">b値グリッド 地震数上位15件</div>'+
      (bvEntries.length? ('<table><tr><th>緯度</th><th>経度</th><th>b値</th><th>地震数</th><th>平均M</th></tr>'+
        bvEntries.map(function(e){return '<tr><td>'+e.lat+'</td><td>'+e.lon+'</td><td>'+e.b+'</td><td>'+e.n+'</td><td>'+e.mean_m+'</td></tr>'}).join('')
        +'</table>') : '<div class="empty">データなし</div>')+
    '</div>';

  document.getElementById('detail').innerHTML = html;

  if(map){ map.remove(); map = null; }
  map = L.map('map',{center:[36,138],zoom:4,preferCanvas:true});
  __DARK_TILE__
  __GEOJSON_JS__

  var etasGroup = L.layerGroup().addTo(map);
  buildEtasCells(d.etas||{}).forEach(function(c){
    L.rectangle([[c.lat,c.lon],[c.lat+GRID_SIZE,c.lon+GRID_SIZE]],
      {color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:0.65})
     .bindTooltip('Level '+c.lv+' / score='+c.score.toFixed(4)).addTo(etasGroup);
  });

  var bvGroup = L.layerGroup().addTo(map);
  buildBvalueCells(d.bvalue||{}).forEach(function(c){
    L.rectangle([[c.lat,c.lon],[c.lat+1.0,c.lon+1.0]],
      {color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:0.6})
     .bindTooltip('b='+c.b+' / N='+c.n+' / M̄='+c.mean_m).addTo(bvGroup);
  });

  curGroups = {etas:etasGroup, bvalue:bvGroup};
  document.getElementById('togEtas').onclick = function(){toggleLayer('etas', this)};
  document.getElementById('togBv').onclick = function(){toggleLayer('bvalue', this)};
}

function toggleLayer(key, btn){
  btn.classList.toggle('on');
  var g = curGroups[key];
  if(!g || !map) return;
  if(map.hasLayer(g)) map.removeLayer(g); else g.addTo(map);
}
</script></body></html>"""
    html = html.replace("__GRID_SIZE__", str(GRID_SIZE))
    html = html.replace("__LEAFLET_CDN__", LEAFLET_CDN)
    html = html.replace("__DARK_TILE__", DARK_TILE)
    html = html.replace("__GEOJSON_JS__", GEOJSON_JS)
    return html

SHELL_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>地震研究統合プラットフォーム v5.0</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    html,body{height:100%;overflow:hidden;background:#0f172a;font-family:"Helvetica Neue",Arial,sans-serif}
    #sidebar{
      position:fixed;top:0;left:0;width:188px;height:100%;
      background:#0d1117;border-right:2px solid #1f2937;
      display:flex;flex-direction:column;padding-top:0;z-index:100;
    }
    .app-title{
      padding:14px 14px 12px;
      background:linear-gradient(135deg,#1e3a5f,#1a1a2e);
      border-bottom:2px solid #1f2937;
    }
    .app-title div:first-child{font-size:12px;font-weight:700;color:#60a5fa;letter-spacing:0.5px}
    .app-title div:last-child{font-size:10px;color:#4b5563;margin-top:2px}
    .group-title{
      padding:10px 14px 4px;font-size:10px;font-weight:700;
      color:#374151;text-transform:uppercase;letter-spacing:1px;
    }
    .tab-btn{
      width:100%;text-align:left;padding:10px 14px 10px 16px;cursor:pointer;
      font-size:13px;font-weight:500;color:#6b7280;
      border:none;background:none;transition:0.15s;display:flex;align-items:center;gap:8px;
    }
    .tab-btn:hover{color:#f3f4f6;background:#161b22}
    .tab-btn.active{
      color:#fff;background:linear-gradient(90deg,#162032,#0d1117);
      border-left:3px solid #3b82f6;padding-left:13px;
    }
    .tab-btn .icon{font-size:14px;flex-shrink:0}
    .tab-btn .label{flex:1}
    .tab-btn .badge{font-size:9px;padding:1px 5px;border-radius:3px;background:#1e3a5f;color:#93c5fd;flex-shrink:0}
    .tab-btn.active .badge{background:#2563eb}
    .sep{height:1px;background:#1f2937;margin:6px 12px}
    .version{margin-top:auto;padding:10px 14px;font-size:10px;color:#374151;border-top:1px solid #1f2937}
    #main{margin-left:188px;height:100vh;overflow:hidden}
    iframe{width:100%;height:100%;border:none;display:none}
    iframe.active{display:block}
  </style>
</head>
<body>
  <div id="sidebar">
    <div class="app-title">
      <div>地震研究統合プラットフォーム</div>
      <div>v5.0 / 研究用</div>
    </div>

    <div class="group-title">地震データ</div>
    <button class="tab-btn active" onclick="sw(0)">
      <span class="icon">🗺</span><span class="label">地震履歴</span>
      <span class="badge">有感+無感</span>
    </button>
    <button class="tab-btn" onclick="sw(1)">
      <span class="icon">📊</span><span class="label">ETASマップ</span>
      <span class="badge">P1</span>
    </button>
    <button class="tab-btn" onclick="sw(2)">
      <span class="icon">📉</span><span class="label">b値マップ</span>
      <span class="badge">P4</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">地球物理データ</div>
    <button class="tab-btn" onclick="sw(3)">
      <span class="icon">🌐</span><span class="label">TEC</span>
      <span class="badge">P5+</span>
    </button>
    <button class="tab-btn" onclick="sw(4)">
      <span class="icon">🛰</span><span class="label">GNSS変位</span>
      <span class="badge">P5</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">気象</div>
    <button class="tab-btn" onclick="sw(5)">
      <span class="icon">🌀</span><span class="label">海面気圧</span>
      <span class="badge">AMeDAS</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">ログ</div>
    <button class="tab-btn" onclick="sw(6)">
      <span class="icon">🗂</span><span class="label">スナップショット</span>
      <span class="badge">1h</span>
    </button>

    <div class="version">ETAS残差研究プロジェクト</div>
  </div>
  <div id="main">
    <iframe id="f0" class="active" src="/tab/history"></iframe>
    <iframe id="f1" src=""></iframe>
    <iframe id="f2" src=""></iframe>
    <iframe id="f3" src=""></iframe>
    <iframe id="f4" src=""></iframe>
    <iframe id="f5" src=""></iframe>
    <iframe id="f6" src=""></iframe>
  </div>
  <script>
    var URLS=['history','etas','bvalue','tec','gnss','pressure','snapshots'];
    var loaded=[true,false,false,false,false,false,false];
    function sw(idx){
      document.querySelectorAll('.tab-btn').forEach(function(b,i){b.classList.toggle('active',i===idx)});
      document.querySelectorAll('iframe').forEach(function(f,i){f.classList.toggle('active',i===idx)});
      if(!loaded[idx]){
        document.getElementById('f'+idx).src='/tab/'+URLS[idx];
        loaded[idx]=true;
      }
    }
  </script>
</body>
</html>"""

LOADING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<style>body{background:#0d1117;color:white;display:flex;align-items:center;justify-content:center;
height:100vh;font-family:sans-serif;flex-direction:column;gap:16px}
.sp{width:48px;height:48px;border:5px solid #1f2937;border-top-color:#3b82f6;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="sp"></div><p>データを準備中...</p>
<p style="font-size:12px;color:#4b5563">地震データ取得 → ETAS解析 → b値計算...</p></body></html>"""


# ══════════════════════════════════════════════════════
# バックグラウンド更新
# ══════════════════════════════════════════════════════
def _update_data():
    global _cached_data, _last_update, _ready_phase, _last_snapshot_ts
    first_run = True  # ★ Bug fix: whileループの外に移動（ループ内にあると毎回Trueにリセットされていた）
    while True:
        try:
            print("[BG] 更新開始")
            updated_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST")

            if first_run:
                existing = load_quakes()
                if existing:
                    grid_scores = analyze_etas(existing)
                    bvalue_grid = compute_bvalue_grid(existing)
                    with _cache_lock:
                        _cached_data = {"all":existing,"etas":grid_scores,"bvalue":bvalue_grid,
                                        "updated":updated_str+"(キャッシュ)"}
                        _last_update = time.time(); _ready_phase = 2
                    print(f"[BG] フェーズ1完了: {len(existing)}件")

            new_q = fetch_all_quakes()
            save_quakes(new_q)
            quakes = load_quakes()
            grid_scores = analyze_etas(quakes)
            bvalue_grid = compute_bvalue_grid(quakes)
            with _cache_lock:
                _cached_data = {"all":quakes,"etas":grid_scores,"bvalue":bvalue_grid,"updated":updated_str}
                _last_update = time.time(); _ready_phase = 2
            print(f"[BG] 完了 地震:{len(quakes)}件 ETAS格子:{len(grid_scores)} b値格子:{len(bvalue_grid)}")
            first_run = False

            # ★ 1時間ごとに解析結果のスナップショットをログ保存する
            now_ts = time.time()
            if now_ts - _last_snapshot_ts >= SNAPSHOT_INTERVAL_SEC:
                save_snapshot(_cached_data)
                _last_snapshot_ts = now_ts

        except Exception as e:
            import traceback; print(f"[BG] エラー: {e}"); traceback.print_exc()
            with _cache_lock:
                # ★ Bug fix: _cached_data is None の条件を削除。
                # 既存CSVロードで_cached_dataがセットされたがAPIが失敗した場合も
                # _ready_phase=2 にしてローディング画面から抜け出せるようにする
                if _ready_phase < 2:
                    if _cached_data is None:
                        _cached_data = {"all":[],"etas":{},"bvalue":{},"updated":"取得失敗"}
                    _ready_phase = 2
            first_run = False

        time.sleep(FETCH_INTERVAL_SEC)


# ══════════════════════════════════════════════════════
# Flask ルーティング
# ══════════════════════════════════════════════════════
@app.route("/")
def index():
    with _cache_lock: phase = _ready_phase
    if phase < 2: return Response(LOADING_HTML, mimetype="text/html")
    return Response(SHELL_HTML, mimetype="text/html")

@app.route("/tab/<name>")
def tab(name):
    with _cache_lock: data = _cached_data
    if data is None:
        return Response("<html><body style='background:#0d1117;color:white;padding:20px'>ロード中...</body></html>", mimetype="text/html")
    upd = data["updated"]
    if   name == "history":  html = render_quake_history(data["all"], upd)
    elif name == "etas":     html = render_etas(data["etas"], data["all"], upd)
    elif name == "bvalue":   html = render_bvalue(data["bvalue"], data["all"], upd)
    elif name == "tec":      html = render_tec(upd)
    elif name == "gnss":     html = render_gnss(upd)
    elif name == "pressure": html = render_pressure(upd)
    elif name == "snapshots": html = render_snapshots(upd)
    else: return Response("Not found", status=404)
    return Response(html, mimetype="text/html")

@app.route("/status")
def status():
    with _cache_lock:
        return {"phase":_ready_phase,"last_update":_last_update,
                "quakes":len(_cached_data["all"]) if _cached_data else 0}

@app.route("/snapshots")
def snapshots():
    """保存済みスナップショットのファイル名一覧（新しい順）をJSONで返す。"""
    return {"snapshots": list_snapshots()}

@app.route("/snapshots/<fname>")
def snapshot_detail(fname):
    """指定したスナップショットの内容をJSONで返す（gi_gj文字列キーのまま）。"""
    data = load_snapshot(fname)
    if data is None:
        return Response("Not found", status=404)
    # タプルキーはJSON化できないため "gi_gj" 文字列のまま返す
    out = dict(data)
    out["etas"]   = {f"{k[0]}_{k[1]}": v for k, v in data["etas"].items()}
    out["bvalue"] = {f"{k[0]}_{k[1]}": v for k, v in data["bvalue"].items()}
    return out

if __name__ == "__main__":
    threading.Thread(target=_update_data, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
