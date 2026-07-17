# -*- coding: utf-8 -*-
"""
地震研究統合プラットフォーム v5.0

タブ構成:
  1. 地震履歴     - 有感・無感統合 (JMA / P2P / USGS)
  2. ETASマップ   - 地震発生確率 + ETAS残差（研究用）
  3. b値マップ    - グリッドごとのGutenberg-Richter b値
  4. 活断層・プレート境界 - 都市圏活断層図(GSI) + プレート境界(PB2002)
  5. 統合リスクマップ(β) - ETAS/b値/活断層/プレート境界/気圧を統合した相対リスク指数
  6. TEC          - 電離圏全電子数 (NICT SCIDAS リンク)
  7. GNSS         - 地殻変動 (GEONET リンク + 変位プレースホルダー)
  8. 海面気圧     - アメダス海面気圧マップ
  9. スナップショット - 1時間ごとの解析結果ログ
"""

from flask import Flask, Response, send_file, request
import requests, csv, os, math, re, json, threading, time, zipfile, io, bisect
from datetime import datetime, timezone, timedelta
import numpy as np

app = Flask(__name__)

# ── 定数 ────────────────────────────────────────────
DATA_FILE          = "data/quakes.csv"
GRID_SIZE          = 0.1
FETCH_INTERVAL_SEC = 600
BVALUE_GRID_SIZE   = 1.5   # b値マップのグリッドサイズ（旧1.0°より少し大きく）

# ── ETAS/b値の計算対象範囲（逆L字型）────────────────────
# 単純な緯度経度の矩形では「先島諸島〜台湾」と「千島海溝」の両方を含めつつ
# 「中国大陸・日本海北部」を除外することができないため、3つの矩形の
# 論理和(OR)でL字型の範囲を作る。
#   (lat_min, lat_max, lon_min, lon_max)
ETAS_REGION_BOXES = [
    (22.0, 27.0, 121.0, 129.0),   # 先島諸島・沖縄・台湾近海
    (27.0, 38.0, 129.0, 148.0),   # 九州〜本州中部（太平洋側・日本海側とも）
    (38.0, 51.0, 136.0, 156.0),   # 東北北部〜北海道〜千島海溝・北方領土
]
# 外部APIへの問い合わせ用（矩形1回で済ませるための外接矩形。実際の絞り込みは
# in_etas_region() で行う）
ETAS_FETCH_BBOX = (22.0, 51.0, 121.0, 156.0)  # (lat_min, lat_max, lon_min, lon_max)

def in_etas_region(lat, lon):
    """指定した緯度経度がETAS計算対象のL字型範囲内かどうかを返す。"""
    for lat_min, lat_max, lon_min, lon_max in ETAS_REGION_BOXES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return True
    return False

# ── スナップショット（解析結果の時系列ログ）────────────
SNAPSHOT_DIR          = "data/snapshots"
SNAPSHOT_KEEP_DAYS    = 30       # 古いスナップショットの保持期間
JST                   = timezone(timedelta(hours=9))

# ── グローバルキャッシュ ──────────────────────────────
_cache_lock        = threading.Lock()
_cached_data       = None
_last_update       = 0.0
_ready_phase       = 0
_last_snapshot_key = None   # 直前に保存を試みたJST時間帯キー("YYYYMMDD_HH")

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
      .then(d=>L.geoJSON(d,{interactive:false,style:{fillOpacity:0,color:'#555',weight:1}}).addTo(map));"""


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
    最大 1000 件 (10ページ × 100件) を取得して直近 30 日分を返す。
    """
    BASE_URL = "https://api.p2pquake.net/v2/history"
    HEADERS  = {"User-Agent": "SeismoApp/5.0"}
    PAGES    = 10       # 1ページ100件 → 最大1000件（増量）
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
    MAX_PAGES_PER_TYPE    = 15  # 1タイプあたり最大15ページ(1500件)に増量

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
    lat_min, lat_max, lon_min, lon_max = ETAS_FETCH_BBOX
    url = (f"https://earthquake.usgs.gov/fdsnws/event/1/query"
           f"?format=geojson&starttime={start}&minlatitude={lat_min}&maxlatitude={lat_max}"
           f"&minlongitude={lon_min}&maxlongitude={lon_max}&minmagnitude=0.0&orderby=time&limit=5000")
    try: data = requests.get(url, timeout=12).json()
    except Exception as e:
        print(f"[USGS] {e}"); return []
    quakes = []
    for feat in data.get("features",[]):
        try:
            props = feat["properties"]; coords = feat["geometry"]["coordinates"]
            lat, lon = float(coords[1]), float(coords[0])
            if not in_etas_region(lat, lon): continue
            t = datetime.fromtimestamp(props["time"]/1000, tz=timezone.utc)
            quakes.append({"time":t.isoformat(),"lat":lat,"lon":lon,
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
    # 各最大15ページなので、最悪ケースで約2〜3分ほどかかる。
    # 旧コードは timeout=30 で join していたため、p2p_jma が時間内に完了せず
    # results["p2p_jma"] が一切セットされない（=空扱いになる）ことが多発し、
    # 無感地震が取得できていなかった。十分なタイムアウトに変更する。
    for t in threads: t.join(timeout=240)
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
def _snapshot_key(now_jst=None):
    """現在時刻が属するJST時間帯のキー("YYYYMMDD_HH")を返す。"""
    now_jst = now_jst or datetime.now(JST)
    return now_jst.strftime("%Y%m%d_%H")

def _snapshot_path(key):
    return os.path.join(SNAPSHOT_DIR, key + ".json")

def snapshot_exists(key):
    """指定したJST時間帯のスナップショットが既にディスクに存在するか。
    （メモリ上のカウンタに頼らず、プロセス再起動をまたいでも重複保存を防ぐため）"""
    return os.path.exists(_snapshot_path(key))

def save_snapshot(cached_data, key=None):
    """
    現在の解析結果（ETAS格子・b値格子など）を1ファイル1スナップショットとして
    data/snapshots/ に JSON 保存する。グリッドのキーは (gi,gj) タプルなので
    JSON化のために "gi_gj" 文字列に変換して保存する。
    ファイル名はJSTの時間帯キー("YYYYMMDD_HH")とし、同じ時間帯は1回しか保存しない
    （ディスク上の存在チェックによる冪等性 = Renderのスリープ/再起動をまたいでも安全）。
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    now_jst = datetime.now(JST)
    key = key or _snapshot_key(now_jst)
    path = _snapshot_path(key)
    payload = {
        "timestamp_jst": now_jst.isoformat(),
        "hour_key":      key,
        "updated":       cached_data.get("updated", ""),
        "quake_count":   len(cached_data.get("all", [])),
        "etas":          {f"{k[0]}_{k[1]}": v for k, v in cached_data.get("etas", {}).items()},
        "bvalue":        {f"{k[0]}_{k[1]}": v for k, v in cached_data.get("bvalue", {}).items()},
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"[スナップショット] 保存: {key}.json "
              f"(地震:{payload['quake_count']}件 ETAS格子:{len(payload['etas'])} b値格子:{len(payload['bvalue'])})")
    except Exception as e:
        print(f"[スナップショット] 保存失敗: {e}")
    _cleanup_old_snapshots()

def _cleanup_old_snapshots(keep_days=SNAPSHOT_KEEP_DAYS):
    """古いスナップショットファイルを削除してディスク肥大化を防ぐ。
    新形式("YYYYMMDD_HH.json")・旧形式("YYYYMMDD_HHMMSS.json")の両方に対応。"""
    if not os.path.isdir(SNAPSHOT_DIR):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for fname in os.listdir(SNAPSHOT_DIR):
        if not fname.endswith(".json"):
            continue
        stem = fname[:-5]
        ts = None
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d_%H"):
            try:
                ts = datetime.strptime(stem, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if ts is None:
            continue
        if ts < cutoff:
            try:
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

def build_snapshots_zip(max_files=None):
    """
    data/snapshots/ 配下のスナップショットJSONファイルをまとめて
    メモリ上でZIP化し、BytesIOバッファを返す。1件もない場合は None。

    max_files を指定すると新しい方から その件数だけに絞る。
    ★ Render無料プランはメモリ・CPUが限られるため、保持件数が多い
    （30日間×毎時 = 最大720件）場合に全件を一度にZIP化しようとすると
    メモリ不足やリクエストタイムアウトで失敗することがある。
    そのため既定では直近分のみに絞り、全件が欲しい場合は
    ?all=1 を明示的に指定してもらう方式にした。
    """
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    files = sorted((f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json")), reverse=True)
    if not files:
        return None
    if max_files is not None:
        files = files[:max_files]
    files = sorted(files)  # zip内は時系列順にしておく
    buf = io.BytesIO()
    # compresslevel を下げてCPU負荷を抑える（JSONはテキストなのでlevel=1でも十分縮む）
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for fname in files:
            zf.write(os.path.join(SNAPSHOT_DIR, fname), arcname=fname)
    buf.seek(0)
    return buf


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
        # 計算範囲: 逆L字型（先島諸島・沖縄・台湾近海 ＋ 九州〜北海道・千島海溝）
        # 中国大陸・日本海北部（ロシア・朝鮮半島側）は除外。範囲の定義は
        # ETAS_REGION_BOXES / in_etas_region() を参照。
        lat_g = ni * GRID_SIZE; lon_g = nj * GRID_SIZE
        mask = np.zeros(ni.shape, dtype=bool)
        for lat_min, lat_max, lon_min, lon_max in ETAS_REGION_BOXES:
            mask |= (lat_g>=lat_min)&(lat_g<=lat_max)&(lon_g>=lon_min)&(lon_g<=lon_max)
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
def compute_bvalue_grid(quakes, grid_size=BVALUE_GRID_SIZE, mc=1.0, min_count=5):
    """
    グリッドごとにb値を計算する。
    b = log10(e) / (mean(M) - Mc)  (最尤推定)
    グリッドサイズは BVALUE_GRID_SIZE (現在1.5°) を既定値として使用する。
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

_MAG_COLOR_STOPS = [
    (2.0, (148, 163, 184)),  # 灰 M2未満
    (3.0, (74, 222, 128)),   # 緑 M3
    (4.0, (250, 204, 21)),   # 黄 M4
    (5.0, (251, 146, 60)),   # 橙 M5
    (6.0, (239, 68, 68)),    # 赤 M6
    (7.0, (220, 38, 38)),    # 濃赤 M7
    (8.0, (255, 0, 255)),    # マゼンタ M8以上
]

def _mag_color(mag):
    """マグニチュードに応じた色を連続グラデーションで返す（区分けではなく線形補間）。"""
    stops = _MAG_COLOR_STOPS
    if mag <= stops[0][0]:
        r, g, b = stops[0][1]
    elif mag >= stops[-1][0]:
        r, g, b = stops[-1][1]
    else:
        for (m0, c0), (m1, c1) in zip(stops, stops[1:]):
            if m0 <= mag <= m1:
                t = (mag - m0) / (m1 - m0)
                r = c0[0] + (c1[0] - c0[0]) * t
                g = c0[1] + (c1[1] - c0[1]) * t
                b = c0[2] + (c1[2] - c0[2]) * t
                break
        else:
            r, g, b = stops[-1][1]
    return f"#{int(round(r)):02x}{int(round(g)):02x}{int(round(b)):02x}"

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
thead th{{padding:6px 5px;font-size:10px;color:#9ca3af;text-align:left;border-bottom:1px solid #374151;white-space:nowrap}}
.qrow:hover{{background:#1e2d40}}
.c1{{padding:5px 7px;font-weight:600;color:#f3f4f6;font-size:12px}}
.c2{{padding:5px 4px;color:#9ca3af;font-size:11px;white-space:nowrap}}
.c3{{padding:5px 4px;text-align:center;font-weight:700;font-size:12px;white-space:nowrap}}
.c4{{padding:5px 4px;text-align:center;white-space:nowrap}}
.c4 span{{white-space:nowrap}}
#mp{{flex:1;overflow:hidden;position:relative}}#map{{width:100%;height:100%}}
#mglg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 13px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.8;color:#f3f4f6}}
#mglg b{{font-size:12px}}
#lp{{position:relative;transition:margin-left 0.2s}}
#lp.closed{{margin-left:-380px}}
#lhTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}}
#lpClose{{flex-shrink:0;width:22px;height:22px;border:none;border-radius:5px;background:#374151;color:#d1d5db;
    cursor:pointer;font-size:13px;line-height:1;display:flex;align-items:center;justify-content:center}}
#lpClose:hover{{background:#4b5563;color:#fff}}
#lpReopen{{position:absolute;top:70px;left:0;z-index:5000;width:34px;height:78px;border:none;
    border-radius:0 8px 8px 0;background:#1f2937;color:#9ca3af;cursor:pointer;font-size:12px;
    display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;
    box-shadow:2px 0 8px rgba(0,0,0,.4)}}
#lpReopen:hover{{background:#2563eb;color:#fff}}
#lpReopen.show{{display:flex}}
#lpReopen .arrow{{font-size:15px;line-height:1}}
#lpReopen .vlabel{{writing-mode:vertical-rl;letter-spacing:1px;font-size:10px;font-weight:600}}
</style></head><body>
<button id="lpReopen" onclick="toggleHistoryPanel()" title="地震一覧を開く">
  <span class="arrow">▶</span><span class="vlabel">地震一覧</span>
</button>
<div id="lp">
  <div id="lh">
    <div id="lhTop">
      <h2>統合地震履歴（直近31日: {total}件 / 有感:{felt_n}件）</h2>
      <button id="lpClose" onclick="toggleHistoryPanel()" title="地震一覧を閉じる">✕</button>
    </div>
    <p>更新: {updated_str}</p>
  </div>
  <div id="fbar">
    <button class="fb on" onclick="filter('all',this)">すべて</button>
    <button class="fb" onclick="filter('felt',this)">有感のみ</button>
    <button class="fb" onclick="filter('unfelt',this)">無感のみ</button>
    <button class="fb" onclick="filter('jma',this)">JMA</button>
    <button class="fb" onclick="filter('p2p',this)">P2P(無感含む)</button>
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
    <div style="width:160px;height:10px;border-radius:3px;margin:5px 0 2px;
      background:linear-gradient(to right,#94a3b8,#4ade80,#facc15,#fb923c,#ef4444,#dc2626,#ff00ff)"></div>
    <div style="display:flex;justify-content:space-between;width:160px;font-size:10px;color:#9ca3af">
      <span>M2以下</span><span>M8以上</span>
    </div>
  </div>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true,zoomControl:false}});
L.control.zoom({{position:'topright'}}).addTo(map);
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
var MODE_MAP={{'all':function(r){{return true}},'felt':function(r){{return r.querySelector('.c4 span').style.background!='rgb(71, 85, 105)'}},'unfelt':function(r){{return r.querySelector('.c4 span').style.background==='rgb(71, 85, 105)'}},'jma':function(r){{return r.querySelector('.c2:last-child span').textContent==='JMA'}},'p2p':function(r){{return r.querySelector('.c2:last-child span').textContent==='P2P'}},'usgs':function(r){{return r.querySelector('.c2:last-child span').textContent==='USGS'}}}};

function filter(mode,btn){{
  document.querySelectorAll('.fb').forEach(function(b){{b.classList.remove('on')}});
  btn.classList.add('on');
  var fn=MODE_MAP[mode];
  allRows.forEach(function(r){{r.style.display=fn(r)?'':'none'}});
}}

function toggleHistoryPanel(){{
  var lp=document.getElementById('lp');
  var reopen=document.getElementById('lpReopen');
  lp.classList.toggle('closed');
  reopen.classList.toggle('show', lp.classList.contains('closed'));
  setTimeout(function(){{map.invalidateSize()}}, 220);
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
</style></head><body>
<div id="tb">
  <span>表示レイヤー:</span>
  <button class="tog on" id="togEtas" onclick="toggleLayer('etas',this)">ETASグリッド</button>
  <button class="tog" id="togRecent" onclick="toggleLayer('recent',this)">直近72h地震</button>
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
var recentGroup=L.layerGroup();
RECENT.forEach(function(d){{
  var size=Math.max(16, d.r*2.4);
  var icon=L.divIcon({{
    className:'',
    html:'<div style="width:'+size+'px;height:'+size+'px;display:flex;align-items:center;justify-content:center;'
        +'background:rgba(220,38,38,0.25);border:2px solid #facc15;border-radius:4px;'
        +'color:#ef4444;font-weight:900;font-size:'+Math.round(size*0.6)+'px;line-height:1">&#10005;</div>',
    iconSize:[size,size], iconAnchor:[size/2,size/2]
  }});
  L.marker([d.lat,d.lon],{{icon:icon}}).bindTooltip(d.tip).addTo(recentGroup);
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
    #
    # ★ 固定の数値レンジ(例: 0.6〜1.4)で色を決めると、その期間のデータの
    # 分布次第で赤・青のどちらかに大きく偏ってしまう。「赤っぽい場所」と
    # 「青っぽい場所」が常に同じくらいの割合になるよう、絶対値ではなく
    # 相対順位(パーセンタイル)で色を決める（＝分布の中央値が常に色の中間になる）。
    gs = BVALUE_GRID_SIZE

    cells_raw = []
    for (gi, gj), info in bvalue_grid.items():
        b = info["b"]; n = info["n"]; mean_m = info["mean_m"]
        lat = gi * gs; lon = gj * gs
        if not in_etas_region(lat, lon): continue
        cells_raw.append({"lat": lat, "lon": lon, "b": b, "n": n, "mean_m": mean_m})

    b_sorted = sorted(c["b"] for c in cells_raw)
    def percentile_rank(b):
        if len(b_sorted) <= 1: return 0.5
        lo = bisect.bisect_left(b_sorted, b)
        hi = bisect.bisect_right(b_sorted, b)
        mid_rank = (lo + hi - 1) / 2
        return mid_rank / (len(b_sorted) - 1)

    def b_color(ratio):
        r = int(220 * (1 - ratio))
        g = int(60 + 100 * ratio)
        bl = int(220 * ratio)
        return f"#{r:02x}{g:02x}{bl:02x}"

    cells = []
    for c in cells_raw:
        ratio = percentile_rank(c["b"])
        cells.append({**c, "color": b_color(ratio)})

    cells_js = json.dumps(cells)

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
    <span>低 ({b_min})</span><span>高 ({b_max})</span>
  </div>
  <hr style="border-color:#374151;margin:5px 0">
  <small>低b値地域(赤) = 大地震の可能性<br>色は数値の相対順位（中央値で赤/青が半々）で決定<br>
  Mc = 1.0 / 最小5件/グリッド<br>グリッドサイズ: {BVALUE_GRID_SIZE}°</small>
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
        画像の直接読み込みができません（CORSポリシー）。<br>下のリンクから直接確認してください。
      </div>
      <a href="https://aer-nc-web.nict.go.jp/iono/GEONET/" target="_blank" class="link-btn">
        NICT GEONET TECページを開く
      </a>
    </div>
    <div class="card">
      <h3>その他のデータソース</h3>
      <p>TECデータを提供する主要な機関・ツール</p>
      <div style="margin-top:8px">
        <a href="https://scidas.nict.go.jp/" target="_blank" class="link-btn" style="margin-bottom:6px">
          NICT SCIDAS（宇宙天気情報）
        </a>
        <a href="https://www.gsi.go.jp/denshi/denshi.html" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#065f46,#047857);margin-bottom:6px">
          国土地理院 電子基準点 TEC
        </a>
        <a href="https://ionex.jpl.nasa.gov/" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#7f1d1d,#b91c1c)">
          JPL Global Ionosphere Maps (GIM)
        </a>
      </div>
    </div>
    <div class="card">
      <h3>現在の取得状況</h3>
      <p style="margin-bottom:8px">
        <span class="badge">状態</span>
        <span style="color:#fbbf24;font-weight:700">準備中</span> — NICT APIへの直接アクセスは今後実装予定。
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
        国土地理院 TERRAS
      </a>
      <a href="https://www.gsi.go.jp/kanshi/gnss_crust.html" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#065f46,#047857)">
        GEONET 地殻変動情報
      </a>
      <a href="https://mekira.gsi.go.jp/" target="_blank" class="link-btn" style="background:linear-gradient(135deg,#78350f,#b45309)">
        MEKIRA（地殻変動モニタ）
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
# TAB: 活断層・プレート境界マップ
# ══════════════════════════════════════════════════════
PLATE_BOUNDARY_URL = "https://raw.githubusercontent.com/fraxen/tectonicplates/master/GeoJSON/PB2002_boundaries.json"
# GEM Global Active Faults Database（ベクター線データ、LineString/MultiLineString）
# 全世界で約12MBあるため、サーバー側で日本周辺だけに絞ってから配信する。
ACTIVE_FAULTS_URL = "https://raw.githubusercontent.com/cossatot/gem-global-active-faults/master/geojson/gem_active_faults.geojson"
# 日本周辺の外接矩形（先島諸島〜千島列島を余裕をもってカバー）
FAULTS_BBOX = (20.0, 52.0, 120.0, 157.0)  # (lat_min, lat_max, lon_min, lon_max)

_fault_cache = {"data": None, "fetched_at": None}
_FAULT_CACHE_TTL_SEC = 24 * 3600  # 活断層データは滅多に変わらないので1日キャッシュ

def get_japan_active_faults():
    """GEM Global Active Faultsを取得し、日本周辺のみにフィルタしたGeoJSONを返す（メモリキャッシュ付き）。"""
    now = time.time()
    if (_fault_cache["data"] is not None and _fault_cache["fetched_at"] is not None
            and now - _fault_cache["fetched_at"] < _FAULT_CACHE_TTL_SEC):
        return _fault_cache["data"]
    try:
        resp = requests.get(ACTIVE_FAULTS_URL, timeout=30)
        gj = resp.json()
    except Exception as e:
        print(f"[ActiveFaults] 取得失敗: {e}")
        return _fault_cache["data"] or {"type": "FeatureCollection", "features": []}

    lat_min, lat_max, lon_min, lon_max = FAULTS_BBOX
    feats = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "LineString":
            flat = coords
        elif gtype == "MultiLineString":
            flat = [pt for line in coords for pt in line]
        else:
            continue
        if any(lat_min <= pt[1] <= lat_max and lon_min <= pt[0] <= lon_max for pt in flat):
            props = feat.get("properties") or {}
            feats.append({
                "type": "Feature",
                "properties": {"name": props.get("name", ""), "slip_type": props.get("slip_type", "")},
                "geometry": geom,
            })
    result = {"type": "FeatureCollection", "features": feats}
    _fault_cache["data"] = result
    _fault_cache["fetched_at"] = now
    print(f"[ActiveFaults] 日本周辺 {len(feats)}件に絞り込み完了")
    return result

@app.route("/data/active_faults.geojson")
def active_faults_geojson():
    return get_japan_active_faults()

def render_faultmap(updated_str):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#hdr{{padding:8px 16px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0;
       display:flex;align-items:center;gap:10px;font-size:12px;color:#9ca3af;flex-wrap:wrap}}
#hdr b{{color:#f3f4f6;font-size:14px}}
.tog{{padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer;border:none;border-radius:5px;background:#1f2937;color:#9ca3af}}
.tog.on{{background:#2563eb;color:#fff}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
#lg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.9;color:#f3f4f6;max-width:260px}}
#loadState{{position:absolute;top:14px;right:56px;z-index:1000;background:rgba(17,24,39,.92);
    padding:6px 12px;border-radius:6px;font-size:11px;color:#9ca3af}}
</style></head><body>
<div id="hdr">
  <b>活断層・プレート境界マップ</b>
  <button class="tog on" id="togFault" onclick="toggleFault(this)">活断層</button>
  <button class="tog on" id="togPlate" onclick="togglePlate(this)">プレート境界</button>
  <div style="margin-left:auto;color:#6b7280">更新: {updated_str}</div>
</div>
<div id="mp">
  <div id="map"></div>
  <div id="loadState">データ読込中...</div>
  <div id="lg">
    <b>凡例</b><br>
    <span style="color:#ff8800">━</span> 活断層(GEM Global Active Faults)<br>
    <span style="color:#ff3b3b">━</span> 収束型境界(SUB/CRB)<br>
    <span style="color:#3b82f6">━</span> 発散型境界(OSR)<br>
    <span style="color:#facc15">━</span> トランスフォーム/その他<br>
    <hr style="border-color:#374151;margin:5px 0">
    <small>出典: GEM Global Active Faults Database（Styron &amp; Pagani, 2020）<br>
    プレート境界: Bird (2003) / fraxen/tectonicplates (Peter Bird, PB2002)</small>
  </div>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true,zoomControl:false}});
L.control.zoom({{position:'topright'}}).addTo(map);
{DARK_TILE}
{GEOJSON_JS}

var faultLayer = null;
var plateLayer = null;
var loadCount = 0;
function loadDone(){{
  loadCount++;
  if(loadCount>=2) document.getElementById('loadState').style.display='none';
}}

fetch('/data/active_faults.geojson')
  .then(function(r){{return r.json()}})
  .then(function(d){{
    faultLayer = L.geoJSON(d, {{
      style:function(){{return {{color:'#ff8800', weight:2, opacity:0.85}}}},
      onEachFeature:function(feature, layer){{
        var p = feature.properties || {{}};
        layer.bindTooltip(p.name || '活断層');
      }}
    }}).addTo(map);
    loadDone();
  }})
  .catch(function(e){{
    document.getElementById('loadState').textContent='活断層データの取得に失敗しました';
  }});

function plateColor(props){{
  var t = String((props && props.Type) || '').toUpperCase();
  if(t.indexOf('SUB')>=0 || t.indexOf('CRB')>=0) return '#ff3b3b';
  if(t.indexOf('OSR')>=0) return '#3b82f6';
  return '#facc15';
}}

fetch('{PLATE_BOUNDARY_URL}')
  .then(function(r){{return r.json()}})
  .then(function(d){{
    plateLayer = L.geoJSON(d, {{
      style:function(feature){{return {{color:plateColor(feature.properties), weight:2, opacity:0.9}}}},
      onEachFeature:function(feature, layer){{
        var p = feature.properties || {{}};
        var label = (p.PlateA && p.PlateB) ? (p.PlateA+' / '+p.PlateB) : (p.Name || 'プレート境界');
        layer.bindTooltip(label);
      }}
    }}).addTo(map);
    loadDone();
  }})
  .catch(function(e){{
    document.getElementById('loadState').textContent='プレート境界データの取得に失敗しました';
  }});

function toggleFault(btn){{
  btn.classList.toggle('on');
  if(!faultLayer) return;
  if(map.hasLayer(faultLayer)) map.removeLayer(faultLayer); else faultLayer.addTo(map);
}}
function togglePlate(btn){{
  btn.classList.toggle('on');
  if(!plateLayer) return;
  if(map.hasLayer(plateLayer)) map.removeLayer(plateLayer); else plateLayer.addTo(map);
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════
# 統合リスクマップ（β）
# ETAS・b値・活断層近接度・プレート境界近接度・気圧偏差を統合し、
# 地域ごとの「相対的な」地震リスク指数を算出する。
# 発生確率を予測するものではなく、あくまで複数指標の相対順位を
# 重み付け合成した比較指標である点に注意。
# ══════════════════════════════════════════════════════
RISK_GRID_SIZE = 0.5   # 統合リスクマップの共通格子（ETAS/b値より粗い格子に集約する）

# 各データソースの既定の重み（合計1.0。未選択/データ欠損のセルは
# 選択されている項目だけで自動的に再正規化される）
RISK_DEFAULT_WEIGHTS = {
    "etas":     0.35,
    "bvalue":   0.25,
    "fault":    0.20,
    "plate":    0.15,
    "pressure": 0.05,
}
RISK_LABELS = {"etas": "ETAS", "bvalue": "b値", "fault": "活断層",
               "plate": "プレート境界", "pressure": "気圧"}

def _build_risk_cells():
    """L字型のETAS計算対象範囲を RISK_GRID_SIZE 格子で分割し、
    セル中心 (lat, lon) を (gi, gj) キー付き辞書で返す（モジュール読込時に1度だけ構築）。"""
    cells = {}
    lat_min_all = min(b[0] for b in ETAS_REGION_BOXES)
    lat_max_all = max(b[1] for b in ETAS_REGION_BOXES)
    lon_min_all = min(b[2] for b in ETAS_REGION_BOXES)
    lon_max_all = max(b[3] for b in ETAS_REGION_BOXES)
    gi0 = int(math.floor(lat_min_all / RISK_GRID_SIZE))
    gi1 = int(math.ceil(lat_max_all / RISK_GRID_SIZE))
    gj0 = int(math.floor(lon_min_all / RISK_GRID_SIZE))
    gj1 = int(math.ceil(lon_max_all / RISK_GRID_SIZE))
    for gi in range(gi0, gi1 + 1):
        lat_c = (gi + 0.5) * RISK_GRID_SIZE
        for gj in range(gj0, gj1 + 1):
            lon_c = (gj + 0.5) * RISK_GRID_SIZE
            if in_etas_region(lat_c, lon_c):
                cells[(gi, gj)] = (lat_c, lon_c)
    return cells

_RISK_CELLS = _build_risk_cells()

# ── プレート境界GeoJSON（サーバー側キャッシュ。活断層近接度と同様の方式）──
PLATE_CACHE_TTL_SEC = 24 * 3600
_plate_cache = {"data": None, "fetched_at": None}

def get_plate_boundaries():
    """プレート境界GeoJSON(PB2002)を取得する。活断層マップタブではブラウザから
    直接fetchしているが、統合リスクマップの近接度計算にはサーバー側でも必要なため
    同様のキャッシュ機構を用意する。"""
    now = time.time()
    if (_plate_cache["data"] is not None and _plate_cache["fetched_at"] is not None
            and now - _plate_cache["fetched_at"] < PLATE_CACHE_TTL_SEC):
        return _plate_cache["data"]
    try:
        gj = requests.get(PLATE_BOUNDARY_URL, timeout=20).json()
    except Exception as e:
        print(f"[PlateBoundary] 取得失敗: {e}")
        return _plate_cache["data"] or {"type": "FeatureCollection", "features": []}
    _plate_cache["data"] = gj
    _plate_cache["fetched_at"] = now
    return gj

def _flatten_line_points(geojson, bbox=None):
    """LineString/MultiLineStringのGeoJSONから頂点座標を (lat, lon) のリストに変換する。"""
    pts = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "LineString":
            lines = [coords]
        elif gtype == "MultiLineString":
            lines = coords
        else:
            continue
        for line in lines:
            for pt in line:
                lon, lat = pt[0], pt[1]
                if bbox:
                    lat_min, lat_max, lon_min, lon_max = bbox
                    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                        continue
                pts.append((lat, lon))
    return pts

def _min_dist_km_grid(points_latlon, chunk=2500):
    """_RISK_CELLS の各セル中心から、与えられた点群への最短距離(km、平面近似)を計算する。
    点群数が多い場合でもメモリを抑えるためチャンク処理する。"""
    if not points_latlon:
        return {}
    cell_keys = list(_RISK_CELLS.keys())
    cell_latlon = np.array([_RISK_CELLS[k] for k in cell_keys])
    cos_lat = np.cos(np.radians(cell_latlon[:, 0]))
    pts = np.array(points_latlon)
    min_dist = np.full(len(cell_keys), np.inf)
    for start in range(0, len(pts), chunk):
        batch = pts[start:start + chunk]
        dlat = (cell_latlon[:, 0:1] - batch[:, 0][None, :]) * 111.0
        dlon = (cell_latlon[:, 1:2] - batch[:, 1][None, :]) * 111.0 * cos_lat[:, None]
        dist = np.sqrt(dlat ** 2 + dlon ** 2)
        min_dist = np.minimum(min_dist, dist.min(axis=1))
    return {cell_keys[i]: float(min_dist[i]) for i in range(len(cell_keys))}

_fault_proximity_cache = {"grid": None, "computed_for": None}
_plate_proximity_cache = {"grid": None, "computed_for": None}

def get_fault_proximity_grid():
    """各リスク格子セルから最寄りの活断層までの距離(km)。
    活断層データのキャッシュが更新された時だけ再計算する（距離計算は比較的重いため）。"""
    global _fault_proximity_cache
    fault_data = get_japan_active_faults()
    fetched_at = _fault_cache.get("fetched_at")
    if (_fault_proximity_cache["grid"] is not None
            and _fault_proximity_cache["computed_for"] == fetched_at):
        return _fault_proximity_cache["grid"]
    pts = _flatten_line_points(fault_data)
    grid = _min_dist_km_grid(pts)
    _fault_proximity_cache = {"grid": grid, "computed_for": fetched_at}
    print(f"[統合リスク] 活断層近接度 {len(grid)}セル計算完了")
    return grid

def get_plate_proximity_grid():
    """各リスク格子セルから最寄りのプレート境界までの距離(km)。"""
    global _plate_proximity_cache
    plate_data = get_plate_boundaries()
    fetched_at = _plate_cache.get("fetched_at")
    if (_plate_proximity_cache["grid"] is not None
            and _plate_proximity_cache["computed_for"] == fetched_at):
        return _plate_proximity_cache["grid"]
    pts = _flatten_line_points(plate_data, bbox=FAULTS_BBOX)
    grid = _min_dist_km_grid(pts)
    _plate_proximity_cache = {"grid": grid, "computed_for": fetched_at}
    print(f"[統合リスク] プレート境界近接度 {len(grid)}セル計算完了")
    return grid

def _risk_etas_raw(etas_grid_scores):
    """ETASの細かい格子(GRID_SIZE)を統合リスク格子(RISK_GRID_SIZE)に集約(合算)する。"""
    from collections import defaultdict
    raw = defaultdict(float)
    for (fgi, fgj), score in etas_grid_scores.items():
        lat = fgi * GRID_SIZE; lon = fgj * GRID_SIZE
        gi = int(math.floor(lat / RISK_GRID_SIZE)); gj = int(math.floor(lon / RISK_GRID_SIZE))
        if (gi, gj) in _RISK_CELLS:
            raw[(gi, gj)] += score
    return dict(raw)

def _risk_bvalue_raw(bvalue_grid):
    """b値格子(BVALUE_GRID_SIZE、より粗い)を統合リスク格子に割り当てる。"""
    raw = {}
    for (bgi, bgj), info in bvalue_grid.items():
        lat0 = bgi * BVALUE_GRID_SIZE; lon0 = bgj * BVALUE_GRID_SIZE
        gi0 = int(math.floor(lat0 / RISK_GRID_SIZE))
        gi1 = int(math.floor((lat0 + BVALUE_GRID_SIZE) / RISK_GRID_SIZE))
        gj0 = int(math.floor(lon0 / RISK_GRID_SIZE))
        gj1 = int(math.floor((lon0 + BVALUE_GRID_SIZE) / RISK_GRID_SIZE))
        for gi in range(gi0, gi1 + 1):
            for gj in range(gj0, gj1 + 1):
                if (gi, gj) in _RISK_CELLS:
                    if (gi, gj) in raw:
                        raw[(gi, gj)] = (raw[(gi, gj)] + info["b"]) / 2
                    else:
                        raw[(gi, gj)] = info["b"]
    return raw

def _risk_pressure_raw():
    """AMeDAS海面気圧の「地域平均からの偏差」を最寄り観測点からセルへ割り当てる。"""
    global _amedas_cache
    now = time.time()
    if _amedas_cache["data"] and now - _amedas_cache["ts"] < AMEDAS_CACHE_SEC:
        cached = _amedas_cache["data"]
        table, obs_data = cached["table"], cached["obs"]
    else:
        table = _fetch_amedas_table(); obs_data, time_label = _fetch_amedas_latest()
        _amedas_cache = {"data": {"table": table, "obs": obs_data, "label": time_label}, "ts": now}

    def _gv(obs, key):
        raw = obs.get(key)
        return raw[0] if isinstance(raw, list) and len(raw) > 0 and raw[0] is not None else None

    pts, vals = [], []
    for sid, obs in obs_data.items():
        info = table.get(sid)
        if not info: continue
        pres = _gv(obs, "normalPressure")
        if pres is None: continue
        pts.append((info["lat"], info["lon"])); vals.append(pres)
    if not pts:
        return {}
    mean_p = float(np.mean(vals))
    cell_keys = list(_RISK_CELLS.keys())
    cell_latlon = np.array([_RISK_CELLS[k] for k in cell_keys])
    pts_arr = np.array(pts); vals_arr = np.array(vals)
    cos_lat = np.cos(np.radians(cell_latlon[:, 0]))
    dlat = (cell_latlon[:, 0:1] - pts_arr[:, 0][None, :]) * 111.0
    dlon = (cell_latlon[:, 1:2] - pts_arr[:, 1][None, :]) * 111.0 * cos_lat[:, None]
    dist = np.sqrt(dlat ** 2 + dlon ** 2)
    nearest_idx = np.argmin(dist, axis=1)
    dev = np.abs(vals_arr[nearest_idx] - mean_p)
    return {cell_keys[i]: float(dev[i]) for i in range(len(cell_keys))}

def _percentile_rank_map(raw_map, invert=False):
    """セルごとのraw値を、他セルとの相対順位(0〜1、大きいほど1)に変換する。
    invert=Trueならraw値が小さいほど1に近くなる（b値・断層/プレート距離用）。"""
    if not raw_map:
        return {}
    keys = list(raw_map.keys())
    vals = np.array([raw_map[k] for k in keys], dtype=float)
    order = vals.argsort()
    ranks = np.empty(len(vals))
    ranks[order] = np.arange(len(vals))
    ranks = ranks / (len(vals) - 1) if len(vals) > 1 else np.array([0.5] * len(vals))
    if invert:
        ranks = 1.0 - ranks
    return {keys[i]: float(ranks[i]) for i in range(len(keys))}

def compute_risk_grid(etas_grid_scores, bvalue_grid):
    """統合リスクマップ用に、各データソースのセルごとの正規化スコア(0〜1)と
    元データ値をまとめたセル一覧を返す。重み付け合成はフロントエンド(JS)側で行い、
    チェックボックスの選択変更に即座に反映できるようにする。"""
    etas_raw     = _risk_etas_raw(etas_grid_scores)
    bvalue_raw   = _risk_bvalue_raw(bvalue_grid)
    fault_raw    = get_fault_proximity_grid()
    plate_raw    = get_plate_proximity_grid()
    pressure_raw = _risk_pressure_raw()

    etas_rank     = _percentile_rank_map(etas_raw, invert=False)
    bvalue_rank   = _percentile_rank_map(bvalue_raw, invert=True)    # 低b値 = 高リスク
    fault_rank    = _percentile_rank_map(fault_raw, invert=True)     # 近い = 高リスク
    plate_rank    = _percentile_rank_map(plate_raw, invert=True)     # 近い = 高リスク
    pressure_rank = _percentile_rank_map(pressure_raw, invert=False)

    cells = []
    for key, (lat, lon) in _RISK_CELLS.items():
        comp = {}
        if key in etas_rank:
            comp["etas"] = {"s": round(etas_rank[key], 4), "r": round(etas_raw[key], 4)}
        if key in bvalue_rank:
            comp["bvalue"] = {"s": round(bvalue_rank[key], 4), "r": round(bvalue_raw[key], 3)}
        if key in fault_rank:
            comp["fault"] = {"s": round(fault_rank[key], 4), "r": round(fault_raw[key], 1)}
        if key in plate_rank:
            comp["plate"] = {"s": round(plate_rank[key], 4), "r": round(plate_raw[key], 1)}
        if key in pressure_rank:
            comp["pressure"] = {"s": round(pressure_rank[key], 4), "r": round(pressure_raw[key], 2)}
        if comp:
            cells.append({"lat": round(lat, 3), "lon": round(lon, 3), "c": comp})
    return cells

def render_riskmap(risk_cells, updated_str):
    cells_js = json.dumps(risk_cells, ensure_ascii=False)
    weights_js = json.dumps(RISK_DEFAULT_WEIGHTS)
    labels_js = json.dumps(RISK_LABELS, ensure_ascii=False)
    gs = RISK_GRID_SIZE
    n_cells = len(risk_cells)
    w = RISK_DEFAULT_WEIGHTS

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;height:100vh;background:#0f172a;color:#fff;font-family:"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#panel{{width:300px;flex-shrink:0;background:#111827;border-right:2px solid #1f2937;overflow-y:auto;padding:14px}}
#panel h2{{font-size:14px;color:#f3f4f6;margin-bottom:4px}}
#panel p.sub{{font-size:11px;color:#6b7280;margin-bottom:12px;line-height:1.6}}
.sec{{margin-bottom:16px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid #1f2937;padding-bottom:4px}}
.preset-row{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px}}
.preset-btn{{flex:1 1 calc(50% - 6px);padding:6px 4px;font-size:11px;font-weight:600;cursor:pointer;
    border:1px solid #374151;border-radius:6px;background:#1f2937;color:#9ca3af;text-align:center}}
.preset-btn:hover{{background:#374151;color:#f3f4f6}}
.preset-btn.on{{background:linear-gradient(135deg,#2563eb,#7c3aed);color:#fff;border-color:transparent}}
.chk-row{{display:flex;align-items:center;gap:8px;padding:6px 4px;border-radius:5px;font-size:12px}}
.chk-row:hover{{background:#161b22}}
.chk-row.disabled{{opacity:0.45}}
.chk-row input{{width:15px;height:15px;flex-shrink:0}}
.chk-row .clabel{{flex:1;color:#e5e7eb}}
.chk-row .cweight{{font-size:10px;color:#6b7280}}
.chk-row .cbadge{{font-size:9px;padding:1px 5px;border-radius:3px;background:#374151;color:#9ca3af}}
#cellCount{{font-size:11px;color:#9ca3af;margin-top:10px}}
#cellCount span{{color:#60a5fa;font-weight:700}}
.note{{font-size:10.5px;color:#6b7280;line-height:1.7;margin-top:10px}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
#lg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 13px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.9;color:#f3f4f6}}
#hdr{{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:1000;
    background:rgba(17,24,39,.92);padding:6px 16px;border-radius:6px;font-size:11px;color:#9ca3af}}
.pop-title{{font-weight:700;font-size:13px;margin-bottom:4px}}
.pop-row{{display:flex;justify-content:space-between;gap:10px;font-size:11px;padding:2px 0;border-bottom:1px dashed #374151}}
</style></head><body>
<div id="panel">
  <h2>統合リスクマップ <span style="font-size:10px;color:#fbbf24">β</span></h2>
  <p class="sub">複数の地震関連データを統合した相対的な地震リスク指数です。地震の発生確率ではなく、地域ごとのリスクの高低を比較するための指標です。</p>

  <div class="sec">
    <h3>プリセット</h3>
    <div class="preset-row">
      <button class="preset-btn on" data-preset="standard" onclick="applyPreset('standard')">標準</button>
      <button class="preset-btn" data-preset="all" onclick="applyPreset('all')">全データ</button>
      <button class="preset-btn" data-preset="geo" onclick="applyPreset('geo')">地質</button>
      <button class="preset-btn" data-preset="crust" onclick="applyPreset('crust')">地殻変動</button>
      <button class="preset-btn" data-preset="custom" onclick="applyPreset('custom')">カスタム</button>
    </div>
  </div>

  <div class="sec">
    <h3>使用データ（チェックで選択）</h3>
    <div class="chk-row"><input type="checkbox" id="chk_etas" checked onchange="onToggle('etas')">
      <span class="clabel">ETAS（地震活動度）</span><span class="cweight">w={w['etas']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_bvalue" checked onchange="onToggle('bvalue')">
      <span class="clabel">b値（Gutenberg-Richter）</span><span class="cweight">w={w['bvalue']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_fault" checked onchange="onToggle('fault')">
      <span class="clabel">活断層近接度</span><span class="cweight">w={w['fault']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_plate" checked onchange="onToggle('plate')">
      <span class="clabel">プレート境界近接度</span><span class="cweight">w={w['plate']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_pressure" onchange="onToggle('pressure')">
      <span class="clabel">気圧偏差</span><span class="cweight">w={w['pressure']}</span></div>
    <div class="chk-row disabled"><input type="checkbox" disabled>
      <span class="clabel">TEC（電離圏）</span><span class="cbadge">近日公開</span></div>
    <div class="chk-row disabled"><input type="checkbox" disabled>
      <span class="clabel">GNSS（地殻変動）</span><span class="cbadge">近日公開</span></div>
  </div>

  <div id="cellCount">表示中のセル数: <span id="cellN">0</span> / {n_cells}</div>
  <div class="note">
    重みは選択されたデータのみを使い自動的に再正規化されます。<br>
    セルをクリックすると統合リスク指数と各データの寄与度（内訳）を表示します。<br>
    「地殻変動」プリセットは、GNSS実装までの暫定的な代理指標としてプレート境界近接度を使用しています。
  </div>
</div>
<div id="mp">
  <div id="map"></div>
  <div id="hdr">更新: {updated_str}</div>
  <div id="lg">
    <b>統合リスクレベル</b><br>
    <span style="color:#7f1d1d">■</span> Lv5（最高）<br>
    <span style="color:#dc2626">■</span> Lv4<br>
    <span style="color:#f97316">■</span> Lv3<br>
    <span style="color:#facc15">■</span> Lv2<br>
    <span style="color:#4ade80">■</span> Lv1（最低）<br>
    <hr style="border-color:#374151;margin:5px 0">
    <small>選択データの相対順位を重み付け合成した指数<br>（発生確率を意味するものではありません）</small>
  </div>
</div>
<script>
var CELLS = {cells_js};
var WEIGHTS = {weights_js};
var LABELS = {labels_js};
var GS = {gs};
var RISK_COLOR = {{5:'#7f1d1d',4:'#dc2626',3:'#f97316',2:'#facc15',1:'#4ade80'}};
var KEYS = ['etas','bvalue','fault','plate','pressure'];

var PRESETS = {{
  standard: {{etas:true, bvalue:true, fault:true, plate:true, pressure:false}},
  all:      {{etas:true, bvalue:true, fault:true, plate:true, pressure:true}},
  geo:      {{etas:false,bvalue:false,fault:true, plate:true, pressure:false}},
  crust:    {{etas:false,bvalue:false,fault:false,plate:true, pressure:false}}
}};
var selected = Object.assign({{}}, PRESETS.standard);

var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}

var rectLayer = null;
var popup = L.popup();

function applyCheckboxesFromSelected(){{
  KEYS.forEach(function(k){{ document.getElementById('chk_'+k).checked = !!selected[k]; }});
}}
function setActivePreset(name){{
  document.querySelectorAll('.preset-btn').forEach(function(b){{
    b.classList.toggle('on', b.dataset.preset===name);
  }});
}}
function applyPreset(name){{
  if(name !== 'custom'){{
    selected = Object.assign({{}}, PRESETS[name]);
    applyCheckboxesFromSelected();
  }}
  setActivePreset(name);
  redraw();
}}
function onToggle(key){{
  selected[key] = document.getElementById('chk_'+key).checked;
  setActivePreset('custom');
  redraw();
}}

function computeComposite(cell){{
  var wsum=0, ssum=0, used=[];
  KEYS.forEach(function(k){{
    if(selected[k] && cell.c[k]){{
      var wk = WEIGHTS[k];
      wsum += wk; ssum += wk*cell.c[k].s; used.push(k);
    }}
  }});
  if(wsum<=0) return null;
  return {{score: ssum/wsum, used: used, wsum: wsum}};
}}
function levelOf(score){{
  if(score>=0.8) return 5;
  if(score>=0.6) return 4;
  if(score>=0.4) return 3;
  if(score>=0.2) return 2;
  return 1;
}}

function showDetail(cell, comp, lv){{
  var rows = comp.used.map(function(k){{
    var d = cell.c[k];
    var nw = WEIGHTS[k]/comp.wsum;
    var contrib = nw*d.s;
    return '<div class="pop-row"><span>'+LABELS[k]+'</span>'+
      '<span>score='+d.s.toFixed(2)+' raw='+d.r+' / 寄与='+contrib.toFixed(3)+'</span></div>';
  }}).join('');
  var html = '<div class="pop-title">統合リスク指数: '+comp.score.toFixed(3)+' (Lv'+lv+')</div>'+
    '<div style="font-size:10px;color:#9ca3af;margin-bottom:6px">緯度'+cell.lat.toFixed(2)+' / 経度'+cell.lon.toFixed(2)+'</div>'+
    rows +
    '<div style="font-size:9px;color:#6b7280;margin-top:6px">score=相対順位(0-1) raw=元データ値(ETAS指数/b値/距離km/気圧偏差hPa) 寄与=重み正規化後の寄与度</div>';
  popup.setLatLng([cell.lat, cell.lon]).setContent(html).openOn(map);
}}

function redraw(){{
  if(rectLayer) map.removeLayer(rectLayer);
  rectLayer = L.layerGroup().addTo(map);
  var shown = 0;
  CELLS.forEach(function(cell){{
    var comp = computeComposite(cell);
    if(!comp) return;
    shown++;
    var lv = levelOf(comp.score);
    var rect = L.rectangle(
      [[cell.lat-GS/2, cell.lon-GS/2],[cell.lat+GS/2, cell.lon+GS/2]],
      {{color:null, weight:0, fill:true, fillColor:RISK_COLOR[lv], fillOpacity:0.6}}
    );
    rect.on('click', function(){{ showDetail(cell, comp, lv); }});
    rect.addTo(rectLayer);
  }});
  document.getElementById('cellN').textContent = shown;
}}

redraw();
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
#dlZip{display:block;width:calc(100% - 28px);margin:8px 14px 4px;padding:7px 0;text-align:center;
    font-size:11px;font-weight:600;color:#93c5fd;background:#1e3a5f;border:1px solid #2563eb;
    border-radius:6px;cursor:pointer;text-decoration:none}
#dlZip:hover{background:#2563eb;color:#fff}
#dlZipAll{display:block;width:calc(100% - 28px);margin:0 14px 8px;padding:5px 0;text-align:center;
    font-size:10px;font-weight:600;color:#9ca3af;background:transparent;border:1px solid #374151;
    border-radius:6px;cursor:pointer;text-decoration:none}
#dlZipAll:hover{background:#1f2937;color:#d1d5db}
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
  <a id="dlZip" href="/snapshots/download">全件をZIPでダウンロード（直近7日分）</a>
  <a id="dlZipAll" href="/snapshots/download?all=1">全期間をまとめてダウンロード</a>
  <div id="items"><div id="loading">読込中...</div></div>
</div>
<div id="detail"><div class="empty" style="margin:auto">左のリストからスナップショットを選択してください</div></div>
<script>
var GRID_SIZE = __GRID_SIZE__;
var BVALUE_GRID_SIZE = __BVALUE_GRID_SIZE__;
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

function bColor(ratio){
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
  var entries = Object.entries(bvObj);
  var bSorted = entries.map(function(kv){return kv[1].b}).sort(function(a,b){return a-b});
  function percentileRank(b){
    if(bSorted.length<=1) return 0.5;
    // 二分探索で順位(0〜1)を求める
    var lo=0, hi=bSorted.length;
    while(lo<hi){var mid=(lo+hi)>>1; if(bSorted[mid]<b) lo=mid+1; else hi=mid;}
    var left=lo;
    lo=0; hi=bSorted.length;
    while(lo<hi){var mid=(lo+hi)>>1; if(bSorted[mid]<=b) lo=mid+1; else hi=mid;}
    var right=lo;
    var midRank = (left+right-1)/2;
    return midRank/(bSorted.length-1);
  }
  return entries.map(function(kv){
    var parts = kv[0].split('_'); var info = kv[1];
    return {lat:parseInt(parts[0])*BVALUE_GRID_SIZE, lon:parseInt(parts[1])*BVALUE_GRID_SIZE,
            color:bColor(percentileRank(info.b)), b:info.b, n:info.n, mean_m:info.mean_m};
  });
}

fetch('/snapshots').then(function(r){return r.json()}).then(function(d){
  snapshots = d.snapshots || [];
  var wrap = document.getElementById('items');
  if(snapshots.length===0){
    wrap.innerHTML = '<div class="empty">まだスナップショットがありません<br>(起動後1時間ほどで作成されます)</div>';
    document.getElementById('dlZip').style.display = 'none';
    document.getElementById('dlZipAll').style.display = 'none';
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
    L.rectangle([[c.lat,c.lon],[c.lat+BVALUE_GRID_SIZE,c.lon+BVALUE_GRID_SIZE]],
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
    html = html.replace("__BVALUE_GRID_SIZE__", str(BVALUE_GRID_SIZE))
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
      <span class="label">地震履歴</span>
      <span class="badge">有感+無感</span>
    </button>
    <button class="tab-btn" onclick="sw(1)">
      <span class="label">ETASマップ</span>
      <span class="badge">P1</span>
    </button>
    <button class="tab-btn" onclick="sw(2)">
      <span class="label">b値マップ</span>
      <span class="badge">P4</span>
    </button>
    <button class="tab-btn" onclick="sw(3)">
      <span class="label">活断層・プレート境界</span>
      <span class="badge">地質</span>
    </button>
    <button class="tab-btn" onclick="sw(4)">
      <span class="label">統合リスクマップ</span>
      <span class="badge">β</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">地球物理データ</div>
    <button class="tab-btn" onclick="sw(5)">
      <span class="label">TEC</span>
      <span class="badge">P5+</span>
    </button>
    <button class="tab-btn" onclick="sw(6)">
      <span class="label">GNSS変位</span>
      <span class="badge">P5</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">気象</div>
    <button class="tab-btn" onclick="sw(7)">
      <span class="label">海面気圧</span>
      <span class="badge">AMeDAS</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">ログ</div>
    <button class="tab-btn" onclick="sw(8)">
      <span class="label">スナップショット</span>
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
    <iframe id="f7" src=""></iframe>
    <iframe id="f8" src=""></iframe>
  </div>
  <script>
    var URLS=['history','etas','bvalue','faultmap','riskmap','tec','gnss','pressure','snapshots'];
    var loaded=[true,false,false,false,false,false,false,false,false];
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
    global _cached_data, _last_update, _ready_phase, _last_snapshot_key
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

            # ★ JST時間帯(HH:00〜)ごとに解析結果のスナップショットをログ保存する。
            # メモリ上のカウンタではなく「その時間帯のファイルが既に存在するか」で
            # 判定するため、Renderのスリープ/プロセス再起動をまたいでも
            # 二重保存や取りこぼしにならない。
            current_key = _snapshot_key()
            if current_key != _last_snapshot_key:
                if not snapshot_exists(current_key):
                    save_snapshot(_cached_data, key=current_key)
                _last_snapshot_key = current_key

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
    elif name == "faultmap": html = render_faultmap(upd)
    elif name == "riskmap":
        risk_cells = compute_risk_grid(data["etas"], data["bvalue"])
        html = render_riskmap(risk_cells, upd)
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

@app.route("/snapshots/download")
def snapshots_download():
    """
    保存済みスナップショットをZIPにまとめてダウンロードさせる。
    既定では直近7日分（毎時想定で最大168件）のみに絞る。
    ?all=1 を付けると保持している全件（最大30日分）をまとめる
    （件数が多いとRender無料プランではメモリ/時間切れになりやすいので注意）。
    """
    want_all = request.args.get("all") == "1"
    max_files = None if want_all else 168
    try:
        buf = build_snapshots_zip(max_files=max_files)
    except Exception as e:
        print(f"[snapshots_download] ZIP作成失敗: {e}")
        return Response(f"ZIP作成中にエラーが発生しました: {e}", status=500)
    if buf is None:
        return Response("スナップショットがまだありません", status=404)
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    suffix = "all" if want_all else "recent7d"
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                      download_name=f"snapshots_{suffix}_{ts}.zip")

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
