# -*- coding: utf-8 -*-
"""
地震研究統合プラットフォーム v6.4

タブ構成:
  1. 地震履歴     - 有感・無感統合 (JMA / P2P / USGS)
  2. ETASマップ   - 地震発生確率 + ETAS残差（研究用）
  3. b値マップ    - グリッドごとのGutenberg-Richter b値
  4. 活断層・プレート境界 - 都市圏活断層図(GSI) + プレート境界(PB2002)
  5. 統合リスクマップ(β) - ETAS/b値/活断層/プレート境界/気圧を統合した相対リスク指数
  6. TEC          - 電離圏全電子数 (NICT SCIDAS リンク)
  7. GNSS         - 地殻変動 (GEONET SFTP実データ変位ベクトル、未設定時はプレースホルダー)
  8. 海面気圧     - アメダス海面気圧マップ
  9. アーカイブ - 1時間ごとの解析結果ログ
"""

from flask import Flask, Response, send_file, request
import requests, csv, os, math, re, json, threading, time, zipfile, io, bisect, tempfile, base64
from datetime import datetime, timezone, timedelta
import numpy as np

# GNSS変位（GEONET日々の座標値）のSFTP取得に使用。未インストールでもアプリ全体は動作する
# （その場合はGNSS機能が自動的に無効化される。requirements.txtに paramiko の追加が必要）。
try:
    import paramiko
    _PARAMIKO_AVAILABLE = True
except ImportError:
    paramiko = None
    _PARAMIKO_AVAILABLE = False

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
SNAPSHOT_TMP_DIR      = "data/snapshots/_tmp"   # ZIPダウンロード用の一時ファイル置き場
SNAPSHOT_KEEP_DAYS    = 30       # 古いスナップショットの保持期間
JST                   = timezone(timedelta(hours=9))

# ── GitHubリポジトリへの自動バックアップ ─────────────────
# Render無料プランはディスクが永続化されない（再デプロイ/長期スリープで消える）ため、
# 蓄積したスナップショットをGitHubへコミットして退避できるようにする。
# 以下の環境変数を設定すると有効になる（未設定であれば完全に無効＝任意機能）。
#   GITHUB_BACKUP_REPO   : "owner/repo" 形式
#   GITHUB_BACKUP_TOKEN  : 対象リポジトリへの contents 書き込み権限を持つトークン
#   GITHUB_BACKUP_BRANCH : 省略時 "main"
#   GITHUB_BACKUP_PATH   : リポジトリ内の保存先ディレクトリ。省略時 "snapshots"
GITHUB_BACKUP_REPO    = os.environ.get("GITHUB_BACKUP_REPO", "").strip()
GITHUB_BACKUP_TOKEN   = os.environ.get("GITHUB_BACKUP_TOKEN", "").strip()
GITHUB_BACKUP_BRANCH  = os.environ.get("GITHUB_BACKUP_BRANCH", "main").strip() or "main"
GITHUB_BACKUP_PATH    = os.environ.get("GITHUB_BACKUP_PATH", "snapshots").strip().strip("/") or "snapshots"
GITHUB_BACKUP_ENABLED = bool(GITHUB_BACKUP_REPO and GITHUB_BACKUP_TOKEN)

# ★ Bug fix (2026-08): quakes.csv（生の地震履歴）自体はこれまでGitHubバックアップの
# 対象外だった。スナップショットは解析結果（ETAS/b値の格子）だけを保存しており、
# 生データはRenderの起動のたびにAPIから再取得する設計だったため、以下のケースで
# 特定の地震（例: 2026/07/28 16:27 熊本地方 M7.1, 最大震度7）が恒久的に欠落しうる:
#   1. その地震発生後、Renderが再デプロイ/長時間スリープでディスクをリセット
#   2. 再起動後 fetch_all_quakes() がAPIから直近分を再取得するが、当該地震は
#      直後から続く大規模な余震活動（数百〜数千回規模）により「直近30日分」の
#      中でもさらに古い側に押しやられ、各APIのページング上限
#      （下記 fetch_quakes_p2p の PAGES / fetch_quakes_p2p_jma の
#      MAX_PAGES_PER_TYPE）内に収まらなくなる
#   3. 結果として、一度取得できていたはずのデータがRenderの再起動を境に失われ、
#      再取得もできない状態になる
# 対策として、quakes.csv 自体も snapshots と同じ仕組みでGitHubへバックアップ/復元
# できるようにする（保存先は snapshots ディレクトリとは別の固定ファイル名）。
GITHUB_BACKUP_QUAKES_PATH = "quakes_backup/quakes.csv"

# ── GNSS変位（GEONET電子基準点 日々の座標値）─────────────
# 国土地理院(GSI)の「電子基準点日々の座標値」はSFTP経由でのみ取得できる
# （単純なHTTPS公開APIは無い）。利用にはSFTPユーザー登録（無料・要申請）が必要:
#   https://terras.gsi.go.jp/ftp_guide.php
# 以下の環境変数を設定すると実データ取得が有効になる。
# 未設定の場合は従来どおりプレースホルダー表示のまま（アプリの他機能には影響しない）。
#   GSI_SFTP_HOST : 省略時 "terras.gsi.go.jp"
#   GSI_SFTP_PORT : 省略時 22
#   GSI_SFTP_USER / GSI_SFTP_PASS : SFTPユーザー登録で発行されたユーザ名・パスワード
#     （「共通ログインサービス」のID/PWとは別物なので注意）
# ★ 実装メモ（2026-08）: .posファイルの列フォーマットは一次資料で完全確認できておらず、
# 暫定パーサー（_parse_pos_file）で対応している。SFTPユーザー登録後は
# GET /gnss/raw_sample?station=<観測点コード> で実際のファイル内容を確認し、
# 期待通りに解析できているか（/gnss/status）を必ず確認すること。
GSI_SFTP_HOST    = os.environ.get("GSI_SFTP_HOST", "terras.gsi.go.jp").strip() or "terras.gsi.go.jp"
GSI_SFTP_PORT    = int(os.environ.get("GSI_SFTP_PORT", "22") or "22")
GSI_SFTP_USER    = os.environ.get("GSI_SFTP_USER", "").strip()
GSI_SFTP_PASS    = os.environ.get("GSI_SFTP_PASS", "").strip()
GSI_GNSS_ENABLED = bool(_PARAMIKO_AVAILABLE and GSI_SFTP_USER and GSI_SFTP_PASS)

GNSS_LOOKBACK_DAYS = 7          # 変位ベクトル計算に使う直近日数（短期変位）
GNSS_CACHE_SEC     = 6 * 3600   # 座標値は日次更新なので数時間キャッシュで十分
GNSS_SFTP_TIMEOUT  = 20

# ETAS対象範囲（in_etas_region）に絞った代表的な電子基準点。
# 緯度経度は観測点名から得られる概算値（市区町村中心付近）であり、
# 電子基準点そのものの精密な設置位置（cm精度）ではない点に注意。
# 地図表示・変位ベクトルの向きの確認用途を想定。厳密な位置が必要な場合は
# 国土地理院「電子基準点検索」で照合すること。
GNSS_STATIONS = [
    # (観測点コード, 名称, 緯度, 経度)
    # ― 先島諸島・沖縄 ―
    ("021096", "那覇",     26.2124, 127.6809),
    ("960749", "石垣１",   24.3448, 124.1572),
    ("960750", "石垣２",   24.3500, 124.2000),
    ("950497", "与那国",   24.4667, 123.0100),
    ("950498", "西表島",   24.3711, 123.7828),
    ("940100", "玉城",     26.1500, 127.7667),
    # ― 九州・四国・中国 ―
    ("940097", "鹿児島１", 31.5966, 130.5571),
    ("940092", "長崎",     32.7503, 129.8779),
    ("940087", "古賀",     33.7489, 130.4747),
    ("940079", "下関",     33.9490, 130.9210),
    ("940083", "高知",     33.5597, 133.5311),
    ("940080", "高松",     34.3428, 134.0466),
    ("940082", "室戸",     33.2833, 134.1556),
    ("940074", "松江",     35.4723, 133.0505),
    ("940090", "大分佐伯", 32.9600, 131.8990),
    ("940094", "日向",     32.4200, 131.6250),
    ("940081", "阿南１",   33.9210, 134.6590),
    # ― 中部・関東 ―
    ("93078",  "静岡２",   34.9769, 138.3831),
    ("93054",  "浜松",     34.7108, 137.7261),
    ("93013",  "大宮",     35.9068, 139.6236),
    ("132009", "石岡",     36.1893, 140.2825),
    ("950266", "長野",     36.6513, 138.1810),
    # ― 東北・北海道 ―
    ("950128", "札幌",     43.0642, 141.3469),
    ("960521", "帯広",     42.9180, 143.2040),
    ("940010", "釧路市",   42.9850, 144.3820),
    ("940022", "函館",     41.7687, 140.7288),
    ("940025", "青森",     40.8244, 140.7400),
    ("940029", "水沢１",   39.1300, 141.1310),
    ("950172", "気仙沼",   38.9080, 141.5680),
    ("940036", "女川",     38.4430, 141.4500),
    ("940001", "稚内",     45.4150, 141.6730),
]

_gnss_cache = {"data": None, "ts": 0.0, "error": None, "updating": False}
_gnss_lock  = threading.Lock()

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
    # ★ Bug fix (2026-08): 大地震発生後は余震が数百〜数千回規模で続くことがあり、
    # PAGES=10（最大1000件）では「直近30日分」に到達する前にページ上限へ達し、
    # 本震そのものが取得対象から漏れることがあった（例: 2026/07/28 熊本地方 M7.1）。
    # 余震活動が活発な時期でも30日分を確実にカバーできるよう上限を引き上げる。
    PAGES    = 30       # 1ページ100件 → 最大3000件（大規模余震シーケンス対策で増量）
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
    # ★ Bug fix (2026-08): 大規模な余震シーケンス（例: 2026/07/28 熊本地方 M7.1、
    # その後も数百〜数千回規模の余震が継続）が発生すると、"since_date"で30日分を
    # 指定していても MAX_PAGES_PER_TYPE=15（1500件/タイプ）に達した時点で
    # ページングが打ち切られ、本震のように「余震群の中で最も古い側」にある
    # レコードが取得できなくなっていた。上限を引き上げて対応する
    # （fetch_all_quakes 側の thread.join タイムアウトも合わせて延長済み）。
    MAX_PAGES_PER_TYPE    = 30  # 1タイプあたり最大30ページ(3000件)に増量

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
    # 各最大30ページ(増量後)なので、最悪ケースで約6〜7分ほどかかる。
    # 旧コードは timeout=30 で join していたため、p2p_jma が時間内に完了せず
    # results["p2p_jma"] が一切セットされない（=空扱いになる）ことが多発し、
    # 無感地震が取得できていなかった。十分なタイムアウトに変更する。
    # ★ さらに2026-08、MAX_PAGES_PER_TYPEを15→30に増量したのに合わせて、
    # ここのタイムアウトも240秒のままだと新しい上限まで到達する前に打ち切られて
    # 元の木阿弥になるため、余裕を持って600秒に延長する。
    for t in threads: t.join(timeout=600)
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
    # ★ Bug fix (2026-08): 新規データが追加された時だけGitHubへバックアップし、
    # data/quakes.csv がRenderの再起動をまたいでも失われないようにする。
    # 変化がない場合は無駄なコミットを避けるためスキップする。
    if new_count > 0:
        _github_backup_quakes_csv_async()

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
        _github_backup_async(path, f"{GITHUB_BACKUP_PATH}/{key}.json", f"snapshot: {key}")
    except Exception as e:
        print(f"[スナップショット] 保存失敗: {e}")
    _cleanup_old_snapshots()

def _parse_snapshot_ts(fname):
    """スナップショットのファイル名からUTCタイムスタンプを復元する。
    新形式("YYYYMMDD_HH.json")・旧形式("YYYYMMDD_HHMMSS.json")の両方に対応。
    解釈できない場合は None を返す。"""
    stem = fname[:-5] if fname.endswith(".json") else fname
    for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d_%H"):
        try:
            return datetime.strptime(stem, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def _cleanup_old_snapshots(keep_days=SNAPSHOT_KEEP_DAYS):
    """古いスナップショットファイルを削除してディスク肥大化を防ぐ。"""
    if not os.path.isdir(SNAPSHOT_DIR):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for fname in os.listdir(SNAPSHOT_DIR):
        if not fname.endswith(".json"):
            continue
        ts = _parse_snapshot_ts(fname)
        if ts is None:
            continue
        if ts < cutoff:
            try:
                os.remove(os.path.join(SNAPSHOT_DIR, fname))
            except Exception:
                continue

def _github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_BACKUP_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def github_backup_file(local_path, repo_relpath, commit_message):
    """
    ローカルファイルをGitHubリポジトリへ Contents API 経由でPUT(作成 or 更新)する。
    既に同名ファイルがリポジトリ側にある場合は sha を取得してから上書きする。
    GITHUB_BACKUP_REPO/TOKEN が未設定、または通信に失敗した場合は例外を投げず
    False を返すのみ（バックアップの失敗でメインの解析処理を止めたくないため）。
    """
    if not GITHUB_BACKUP_ENABLED:
        return False
    api_url = f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{repo_relpath}"
    try:
        with open(local_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")
        sha = None
        r = requests.get(api_url, headers=_github_api_headers(),
                          params={"ref": GITHUB_BACKUP_BRANCH}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
        elif r.status_code not in (404,):
            print(f"[GitHub backup] 既存ファイル確認失敗 {repo_relpath}: {r.status_code}")
        payload = {"message": commit_message, "content": content_b64, "branch": GITHUB_BACKUP_BRANCH}
        if sha:
            payload["sha"] = sha
        r = requests.put(api_url, headers=_github_api_headers(), json=payload, timeout=20)
        if r.status_code in (200, 201):
            return True
        print(f"[GitHub backup] 保存失敗 {repo_relpath}: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[GitHub backup] 例外 {repo_relpath}: {e}")
        return False

def _github_backup_async(local_path, repo_relpath, commit_message):
    """GitHubへの通信でメインの解析スレッドをブロックしないよう、別スレッドで実行する。"""
    if not GITHUB_BACKUP_ENABLED:
        return
    threading.Thread(
        target=github_backup_file,
        args=(local_path, repo_relpath, commit_message),
        daemon=True,
    ).start()

def github_restore_snapshots():
    """
    起動時にGitHubリポジトリ側のスナップショットを取得し、ローカルの
    data/snapshots/ に無いものだけを復元する。

    ★ Renderの無料プランはディスクが永続化されないため、再デプロイや長時間
    スリープ後の起動直後は data/snapshots/ が空になる。これを放置すると
    「アーカイブ」タブに蓄積されていたデータが見た目上すべて消えてしまう。
    そこで起動のたびにGitHub側の内容で埋め戻し、蓄積データを維持する。
    保持期間(SNAPSHOT_KEEP_DAYS)より古いものは復元してもどうせすぐ
    _cleanup_old_snapshots() で削除されるだけなので、復元対象から除外して
    無駄な通信を減らしている。
    """
    if not GITHUB_BACKUP_ENABLED:
        return
    api_url = f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{GITHUB_BACKUP_PATH}"
    try:
        r = requests.get(api_url, headers=_github_api_headers(),
                          params={"ref": GITHUB_BACKUP_BRANCH}, timeout=20)
        if r.status_code == 404:
            print("[GitHub restore] バックアップ先ディレクトリが未作成のため復元対象なし")
            return
        if r.status_code != 200:
            print(f"[GitHub restore] 一覧取得失敗: {r.status_code} {r.text[:200]}")
            return
        items = r.json()
        if not isinstance(items, list):
            print("[GitHub restore] 想定外のレスポンス形式のため中断")
            return
    except Exception as e:
        print(f"[GitHub restore] 一覧取得例外: {e}")
        return

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_KEEP_DAYS)
    restored, skipped, failed = 0, 0, 0
    for item in items:
        name = item.get("name", "")
        if item.get("type") != "file" or not name.endswith(".json"):
            continue
        ts = _parse_snapshot_ts(name)
        if ts is not None and ts < cutoff:
            continue  # 保持期間より古いものは復元しない
        local_path = os.path.join(SNAPSHOT_DIR, name)
        if os.path.exists(local_path):
            skipped += 1
            continue
        try:
            fr = requests.get(item["url"], headers=_github_api_headers(),
                               params={"ref": GITHUB_BACKUP_BRANCH}, timeout=20)
            if fr.status_code != 200:
                failed += 1
                continue
            content_b64 = fr.json().get("content", "")
            raw = base64.b64decode(content_b64)
            with open(local_path, "wb") as f:
                f.write(raw)
            restored += 1
        except Exception as e:
            print(f"[GitHub restore] {name} 復元失敗: {e}")
            failed += 1
    print(f"[GitHub restore] 完了 復元:{restored}件 既存済み:{skipped}件 失敗:{failed}件")
    _cleanup_old_snapshots()

def _github_restore_async():
    """復元処理でアプリの起動自体をブロックしないよう、別スレッドで実行する。"""
    if not GITHUB_BACKUP_ENABLED:
        return
    threading.Thread(target=github_restore_snapshots, daemon=True).start()


# ══════════════════════════════════════════════════════
# quakes.csv（生の地震履歴）のGitHubバックアップ/復元
# ★ Bug fix (2026-08): 詳細はファイル冒頭 GITHUB_BACKUP_QUAKES_PATH のコメント参照。
# 生データはRenderの再起動のたびにAPIから再取得する設計だったため、大規模な余震
# シーケンスでAPIのページング上限を超えてしまうと、本震のような重要なレコードが
# 恒久的に欠落する不具合があった。スナップショットと同様にGitHubへ退避することで、
# 「一度取得できたデータは再起動後も失われない」状態にする。
# ══════════════════════════════════════════════════════
def github_backup_quakes_csv():
    """ローカルの quakes.csv をGitHubへバックアップする（同期実行・失敗しても例外を投げない）。"""
    if not GITHUB_BACKUP_ENABLED:
        return False
    if not os.path.exists(DATA_FILE):
        return False
    return github_backup_file(DATA_FILE, GITHUB_BACKUP_QUAKES_PATH, "quakes.csv backup")

def _github_backup_quakes_csv_async():
    if not GITHUB_BACKUP_ENABLED:
        return
    threading.Thread(target=github_backup_quakes_csv, daemon=True).start()

def github_restore_quakes_csv():
    """
    起動時にGitHub側にバックアップされた quakes.csv を取得し、ローカルの
    data/quakes.csv とマージする（時刻・緯度・経度が一致するものは重複除外）。

    ★ ローカルファイルが存在しない（Render再起動直後でディスクが空）場合はもちろん、
    既に存在する場合でも欠けている行だけを補完できるよう、常にマージ処理を行う。
    これにより、大規模な余震シーケンスでAPIのページング上限に引っかかって
    再取得できなくなった古いレコード（本震など）も、過去にバックアップ済みで
    あれば復元できる。
    """
    if not GITHUB_BACKUP_ENABLED:
        return
    api_url = f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{GITHUB_BACKUP_QUAKES_PATH}"
    try:
        r = requests.get(api_url, headers=_github_api_headers(),
                          params={"ref": GITHUB_BACKUP_BRANCH}, timeout=20)
        if r.status_code == 404:
            print("[GitHub restore] quakes.csv バックアップ未作成のため復元対象なし")
            return
        if r.status_code != 200:
            print(f"[GitHub restore] quakes.csv 取得失敗: {r.status_code} {r.text[:200]}")
            return
        content_b64 = r.json().get("content", "")
        raw = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[GitHub restore] quakes.csv 取得例外: {e}")
        return

    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    existing = set()
    existing_rows = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 3:
                    existing.add((row[0], row[1], row[2]))
                    existing_rows.append(row)

    restored_rows = []
    for row in csv.reader(io.StringIO(raw)):
        if len(row) >= 3 and (row[0], row[1], row[2]) not in existing:
            existing.add((row[0], row[1], row[2]))
            restored_rows.append(row)

    if not restored_rows:
        print("[GitHub restore] quakes.csv 復元: 追加分なし（既に最新）")
        return

    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(restored_rows)
    print(f"[GitHub restore] quakes.csv 復元: {len(restored_rows)}件追加")
    _cleanup_old_quakes()

def _github_restore_quakes_csv_sync():
    """
    quakes.csv の復元はバックグラウンド更新ループの1回目のキャッシュ読み込み
    (load_quakes) より前に完了している必要があるため、非同期スレッドではなく
    起動シーケンス内で同期的に実行する（ファイル1つの取得のみなので数秒程度）。
    """
    if not GITHUB_BACKUP_ENABLED:
        return
    try:
        github_restore_quakes_csv()
    except Exception as e:
        print(f"[GitHub restore] quakes.csv 復元処理で例外: {e}")

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
    data/snapshots/ 配下のスナップショットJSONファイルをまとめてZIP化し、
    ディスク上の一時ファイルとして書き出してそのパスを返す。1件もない場合は None。
    呼び出し側は使用後に必ずこのファイルを削除すること。

    max_files を指定すると新しい方から その件数だけに絞る。
    ★ 以前はio.BytesIOでメモリ上にZIPを構築していたが、それだと
      ・件数/サイズが大きいとZIP全体を一度にメモリへ載せることになり、
        Render無料プラン(メモリ512MB程度)ではプロセスがOOMで落ちて
        ダウンロードが失敗することがあった
      ・メモリバッファにはファイルとしての実体(mtime等)が無いため、
        ブラウザがサイズの大きいダウンロードに対してRangeリクエスト
        （分割・再開ダウンロード）を送ってきた際にsend_fileが正しく
        扱えず、一定サイズを超えると「途中で止まる/保存できない」
        不具合につながっていた
      という問題があったため、ディスク上の一時ファイルに書き出し、
      send_file()にはそのファイルパスを渡す方式に変更した
      （実ファイルであればWerkzeugが正しくConditional/Rangeに対応できる）。
      allowZip64も明示的に有効化し、件数・サイズが将来的に増えても
      ZIP仕様上の上限(4GB/65535件)に達しないようにしてある。
    """
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    files = sorted((f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json")), reverse=True)
    if not files:
        return None
    if max_files is not None:
        files = files[:max_files]
    files = sorted(files)  # zip内は時系列順にしておく

    os.makedirs(SNAPSHOT_TMP_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="snapshots_", dir=SNAPSHOT_TMP_DIR)
    try:
        with os.fdopen(fd, "wb") as raw:
            # compresslevel を下げてCPU負荷を抑える（JSONはテキストなのでlevel=1でも十分縮む）
            with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zf:
                for fname in files:
                    zf.write(os.path.join(SNAPSHOT_DIR, fname), arcname=fname)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise
    return tmp_path


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
# ══════════════════════════════════════════════════════
# TEC 実データ取得（NICT準リアルタイムTECマップの色逆変換／実験的）
# ══════════════════════════════════════════════════════
# 方針:
#   NICTはTEC数値を返すAPIを公開していないため、公開されている10分間隔の
#   準リアルタイムTECマップ(PNG画像)を取得し、画像内のカラーバー（凡例）から
#   「ピクセル色 → TEC値(TECU)」のルックアップテーブル(LUT)を作成、
#   マップ本体の各ピクセルを最近傍色マッチングで数値に逆変換する。
#   画像圧縮・アンチエイリアシング・海岸線オーバーレイなどにより誤差が出るため、
#   あくまで「傾向を見るための近似値」として扱うこと（学術的な精度は無い）。
#
# ★★★ 要キャリブレーション ★★★
#   下記の座標・値域は実際のNICT画像を目視確認した上で設定する必要がある。
#   1) calibrate_tec.py を一度実行して tec_raw.png / tec_grid.png を保存する
#   2) 地図本体が画像のどのピクセル範囲(x0,y0)-(x1,y1)にあり、それが
#      どの緯度経度範囲(lat0,lon0)-(lat1,lon1)に対応するかを読み取る
#   3) カラーバー（凡例）が画像のどのピクセル範囲にあり、両端が何TECUに
#      対応するかを読み取る
#   4) 下のTEC_CALIBRATIONを埋める。未設定(None)のままだとTEC実データ取得は
#      スキップされ、フォールバックの画像埋め込み表示のみになる。
#
# ── 画像URLについて ──
# NICT QR_GEONETのTECマップ画像はファイル名に生成時刻が入っており、固定URLでは
# 参照できない（例: .../AMAP/2026/205/05_30_0724_2026_Amap.jpg
#   = 2026年通日205日(7/24)の 05:30 生成分, "AMAP"=TEC(全電子数)本体マップ）。
# 10分間隔で生成されるが、①タイムゾーンがUTCかJSTか、②実際の公開までの遅延(分)が
# 不明なため、直近の「その場で組み立てたURL」を新しい方から順に何個か試して、
# 200が返ってきたものを採用する方式にする。一度成功した(タイムゾーン,遅延)は
# 次回以降の最有力候補として先頭に回すことで、通常は1〜2回のリクエストで済む。
TEC_URL_TEMPLATE = ("https://aer-nc-web.nict.go.jp/GPS/QR_GEONET/AMAP/"
                     "{year}/{doy:03d}/{hh}_{mm}_{month:02d}{day:02d}_{year}_Amap.jpg")
TEC_URL_STEP_MIN      = 10   # 生成間隔
TEC_URL_LOOKBACK_MIN  = 90   # どこまで過去に遡って候補を試すか
_tec_url_pref = {"tz": "jst", "lag_min": 0}   # 直近成功した組み合わせ（起動時はJST・遅延0から）

#   ★ 2026/07/24 実測キャリブレーション済み ★
#   ユーザーが実際にNICT画像(AMAP)を保存したスクリーンショット(368x368px)を元に、
#   緯度経度目盛りラベル・カラーバー目盛りラベルのピクセル位置を回帰分析して算出。
#   ただしそのスクリーンショットはEXIFにGhostscript/MuPDF系のICCプロファイルが
#   付与されており、requests.get()で直接取得する生JPEGとは解像度が異なる可能性が
#   高いため、ピクセル座標は絶対値ではなく「画像サイズに対する比率(0〜1)」で
#   保持し、_build_tec_lut()/_decode_tec_grid() 内で実際に取得した画像の
#   width/heightに掛けて絶対ピクセルへ変換する（多少の解像度差があっても崩れない）。
#   ズレが大きい場合は、実際にRender/ローカルで取得された tec_raw.png を確認し、
#   下記の *_frac 値を微調整すること。
TEC_CALIBRATION = {
    "map_px_box_frac":    (0.2554, 0.2065, 0.7446, 0.8777),  # (x0,y0,x1,y1) 比率。地図本体領域
    "map_latlon_box":     (44.0, 128.0, 24.0, 144.0),  # (lat0, lon0, lat1, lon1)
                                  #   lat0=画像上端(北)の緯度, lat1=画像下端(南)の緯度
    "legend_px_box_frac": (0.8478, 0.0978, 0.8696, 0.4973),  # (x0,y0,x1,y1) 比率。カラーバー領域
    "legend_orientation": "vertical",  # "vertical"(縦棒。上端がlegend_value_range[0]) か "horizontal"
    "legend_value_range": (50.0, 0.0),  # (上端/左端の値, 下端/右端の値)
    "bg_color_max_dist":  18.0,  # LUTとの色距離がこれ以上なら「凡例外」として除外(背景/海岸線/文字)
}

def _frac_box_to_px(frac_box, w, h):
    """(x0,y0,x1,y1)の比率(0〜1)を、実際の画像サイズ(w,h)に応じた絶対ピクセルへ変換する。"""
    x0f, y0f, x1f, y1f = frac_box
    return (x0f * w, y0f * h, x1f * w, y1f * h)

TEC_FETCH_INTERVAL_SEC = 600      # 元データが10分間隔のため合わせる
TEC_HISTORY_MAX        = 6 * 24   # 直近24時間分(10分間隔換算)を基準値計算に保持

_tec_lock    = threading.Lock()
_tec_cache   = {"grid": None, "ts": 0.0, "updated": None}
_tec_history = []   # [(epoch_sec, {(gi,gj): value, ...}), ...] 古い→新しい順

def _tec_calibrated():
    c = TEC_CALIBRATION
    return all(c.get(k) is not None for k in
               ("map_px_box_frac", "map_latlon_box", "legend_px_box_frac", "legend_value_range"))

def _tec_build_url(dt):
    doy = dt.timetuple().tm_yday
    return TEC_URL_TEMPLATE.format(year=dt.year, doy=doy, hh=f"{dt.hour:02d}",
                                    mm=f"{dt.minute:02d}", month=dt.month, day=dt.day)

def _tec_candidate_list():
    """(tz, lag_min, url) の候補リストを、前回成功した組み合わせを最優先にして返す。"""
    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    step = TEC_URL_STEP_MIN
    lags = list(range(0, TEC_URL_LOOKBACK_MIN + 1, step))

    def _rounded(base):
        m = (base.minute // step) * step
        return base.replace(minute=m, second=0, microsecond=0)

    bases = {"jst": _rounded(now_jst), "utc": _rounded(now_utc)}
    pref_tz, pref_lag = _tec_url_pref["tz"], _tec_url_pref["lag_min"]

    ordered = []
    # 前回成功した組み合わせを最優先
    if pref_lag in lags:
        ordered.append((pref_tz, pref_lag))
    for tz in (pref_tz, "jst" if pref_tz == "utc" else "utc"):
        for lag in lags:
            if (tz, lag) not in ordered:
                ordered.append((tz, lag))

    out = []
    for tz, lag in ordered:
        dt = bases[tz] - timedelta(minutes=lag)
        out.append((tz, lag, _tec_build_url(dt)))
    return out

def _fetch_tec_image():
    """NICT準リアルタイムTECマップ(AMAP)のJPEGを、時刻候補を順に試して取得する。失敗時はNone。"""
    global _tec_url_pref
    try:
        from PIL import Image
    except ImportError:
        print("[TEC] Pillowが未インストールです（pip install Pillow --break-system-packages）")
        return None
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SeismoApp/5.0; research use)"}
    for tz, lag, url in _tec_candidate_list():
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            if resp.status_code == 200 and resp.content:
                _tec_url_pref = {"tz": tz, "lag_min": lag}
                print(f"[TEC] 画像取得成功: {url}")
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception:
            continue
    print("[TEC] 候補URLすべて404/失敗でした（TEC_URL_TEMPLATEやAMAP以外の"
          "命名パターンに変わっている可能性があります）")
    return None

def _build_tec_lut(img):
    """カラーバー領域を等間隔サンプリングして (R,G,B)→TEC値 のLUTを作る。"""
    arr = np.asarray(img)
    h, w = arr.shape[0], arr.shape[1]
    x0, y0, x1, y1 = _frac_box_to_px(TEC_CALIBRATION["legend_px_box_frac"], w, h)
    v_start, v_end = TEC_CALIBRATION["legend_value_range"]
    n_samples = 64
    lut_colors, lut_values = [], []
    for i in range(n_samples):
        t = i / (n_samples - 1)
        if TEC_CALIBRATION["legend_orientation"] == "vertical":
            py = int(round(y0 + t * (y1 - y0))); px = int(round((x0 + x1) / 2))
        else:
            px = int(round(x0 + t * (x1 - x0))); py = int(round((y0 + y1) / 2))
        px = min(max(px, 0), w - 1)
        py = min(max(py, 0), h - 1)
        lut_colors.append(arr[py, px].astype(float))
        lut_values.append(v_start + t * (v_end - v_start))
    return np.array(lut_colors), np.array(lut_values)

def _decode_pixels(pixels, lut_colors, lut_values, max_dist):
    """pixels: (N,3)。各ピクセルをLUTの最近傍色にマッチングしてTEC値を返す。
    色距離がmax_dist以上のピクセルはNaN（背景/海岸線/文字などとして除外）。"""
    diff = pixels[:, None, :] - lut_colors[None, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))
    nearest = dist.argmin(axis=1)
    min_dist = dist[np.arange(len(pixels)), nearest]
    values = lut_values[nearest]
    return np.where(min_dist < max_dist, values, np.nan)

def _decode_tec_grid(img):
    """画像全体をRISK_CELLSの各セルに割り当てて中央値TEC値を計算する。"""
    lut_colors, lut_values = _build_tec_lut(img)
    arr = np.asarray(img).astype(float)
    h, w = arr.shape[0], arr.shape[1]
    mx0, my0, mx1, my1 = _frac_box_to_px(TEC_CALIBRATION["map_px_box_frac"], w, h)
    lat0, lon0, lat1, lon1 = TEC_CALIBRATION["map_latlon_box"]
    max_dist = TEC_CALIBRATION["bg_color_max_dist"]

    def _px(lat, lon):
        xf = (lon - lon0) / (lon1 - lon0)
        yf = (lat - lat0) / (lat1 - lat0)
        return (mx0 + xf * (mx1 - mx0), my0 + yf * (my1 - my0))

    grid = {}
    for (gi, gj), (lat_c, lon_c) in _RISK_CELLS.items():
        if not (min(lat0, lat1) <= lat_c <= max(lat0, lat1) and
                min(lon0, lon1) <= lon_c <= max(lon0, lon1)):
            continue
        half = RISK_GRID_SIZE / 2
        xs, ys = [], []
        for lat in (lat_c - half, lat_c + half):
            for lon in (lon_c - half, lon_c + half):
                px, py = _px(lat, lon)
                xs.append(px); ys.append(py)
        x_lo, x_hi = int(max(min(xs), 0)), int(min(max(xs), arr.shape[1] - 1))
        y_lo, y_hi = int(max(min(ys), 0)), int(min(max(ys), arr.shape[0] - 1))
        if x_hi <= x_lo or y_hi <= y_lo:
            continue
        block = arr[y_lo:y_hi + 1, x_lo:x_hi + 1].reshape(-1, 3)
        if block.shape[0] == 0:
            continue
        vals = _decode_pixels(block, lut_colors, lut_values, max_dist)
        vals = vals[~np.isnan(vals)]
        if vals.size == 0:
            continue
        grid[(gi, gj)] = float(np.median(vals))
    return grid

def update_tec_cache():
    """TEC画像を取得・デコードしてキャッシュを更新する。バックグラウンドループから呼ぶ。"""
    global _tec_cache, _tec_history
    if not _tec_calibrated():
        print("[TEC] 未キャリブレーションのためスキップ（TEC_CALIBRATIONを設定してください）")
        return
    img = _fetch_tec_image()
    if img is None:
        return
    grid = _decode_tec_grid(img)
    if not grid:
        print("[TEC] デコード結果が空でした（キャリブレーション値を確認してください）")
        return
    now = time.time()
    with _tec_lock:
        _tec_cache = {"grid": grid, "ts": now,
                      "updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")}
        _tec_history.append((now, grid))
        cutoff = now - TEC_HISTORY_MAX * TEC_FETCH_INTERVAL_SEC
        _tec_history = [(t, g) for t, g in _tec_history if t >= cutoff]
    print(f"[TEC] 更新完了 セル数:{len(grid)}")

def _risk_tec_raw():
    """各セルについて「現在のTEC値 − 直近履歴の平均値」の絶対偏差を返す。
    急激な増減を電離圏擾乱の目安として扱うが、学術的に確立した指標ではない点に注意。"""
    with _tec_lock:
        cur = _tec_cache["grid"]
        hist = list(_tec_history)
    if not cur or len(hist) < 3:
        return {}
    baseline = {}
    for _, g in hist[:-1]:
        for k, v in g.items():
            baseline.setdefault(k, []).append(v)
    raw = {}
    for k, v in cur.items():
        if k in baseline and len(baseline[k]) >= 2:
            raw[k] = abs(v - float(np.mean(baseline[k])))
    return raw


def render_tec(updated_str):
    nict_url = "https://aer-nc-web.nict.go.jp/iono/GEONET/latest_map.png"
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    date_str = now_jst.strftime("%Y年%m月%d日 %H:%M JST")

    with _tec_lock:
        grid = _tec_cache["grid"]
        tec_updated = _tec_cache["updated"]

    if not grid:
        # 未キャリブレーション/未取得時は、従来どおり画像埋め込み＋リンク集のフォールバック表示
        status_html = ('<span style="color:#fbbf24;font-weight:700">未取得</span> — '
                        'TEC_CALIBRATION が未設定か、直近の取得に失敗しています。'
                        '下の画像・リンクを参考値としてご利用ください。')
        cells_js = "[]"
    else:
        status_html = (f'<span style="color:#34d399;font-weight:700">取得中（実験的・色逆変換）</span> '
                        f'— セル数:{len(grid)} / 元画像更新: {tec_updated or "不明"}')
        cells = [{"lat": round(_RISK_CELLS[k][0], 3), "lon": round(_RISK_CELLS[k][1], 3),
                  "v": round(v, 2)} for k, v in grid.items()]
        cells_js = json.dumps(cells, ensure_ascii=False)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#f3f4f6;font-family:"Helvetica Neue",Arial,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
#hdr{{padding:12px 20px;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0}}
#hdr h2{{font-size:15px;font-weight:700;color:#f3f4f6;margin-bottom:4px}}
#hdr p{{font-size:11px;color:#6b7280}}
#body{{flex:1;display:flex;gap:0;overflow:hidden}}
#map{{flex:1;min-width:0}}
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
</style></head><body>
<div id="hdr">
  <h2>TEC（電離圏全電子数）モニタリング</h2>
  <p>NICT GEONET TEC / {date_str} / 更新: {updated_str} / 取得状況: {status_html}</p>
</div>
<div id="body">
  {"<div id='map'></div>" if grid else ""}
  <div id="left" style="{'display:none' if grid else ''}">
    <div class="card">
      <h3>NICT 最新 TEC マップ（GEONET・フォールバック表示）</h3>
      <p>実データ（色逆変換）が未取得のため、参考として画像を埋め込んでいます。</p>
      <img src="{nict_url}" class="tec-img" alt="NICT TEC Map"
           onerror="this.style.display='none';document.getElementById('img-err').style.display='block'">
      <div id="img-err" style="display:none;padding:12px;background:#1f2937;border-radius:6px;margin-top:8px;font-size:12px;color:#9ca3af">
        画像の直接読み込みができません（CORSポリシー）。下のリンクから直接確認してください。
      </div>
      <a href="https://aer-nc-web.nict.go.jp/iono/GEONET/" target="_blank" class="link-btn">
        NICT GEONET TECページを開く
      </a>
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
    <h3>この数値について</h3>
    <div class="note">
      NICTはTEC数値APIを公開していないため、<br>
      公開画像のカラーバーを解析して<br>
      色→TECUに逆変換した<b style="color:#fbbf24">近似値</b>です。<br>
      統合リスクマップでは、直近履歴平均からの<br>
      偏差（急な増減）をリスク成分として使用しています。
    </div>
  </div>
</div>
<script>
var cells = {cells_js};
if (cells.length > 0) {{
  var map = L.map('map', {{center:[36,138], zoom:5, preferCanvas:true}});
  {DARK_TILE}
  {GEOJSON_JS}
  var vals = cells.map(c => c.v);
  var vmin = Math.min.apply(null, vals), vmax = Math.max.apply(null, vals);
  function colorFor(v) {{
    var t = vmax > vmin ? (v - vmin) / (vmax - vmin) : 0.5;
    var r = Math.round(255 * t), b = Math.round(255 * (1 - t));
    return 'rgb(' + r + ',80,' + b + ')';
  }}
  var half = {RISK_GRID_SIZE} / 2;
  cells.forEach(function(c) {{
    L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], {{
      color: colorFor(c.v), weight: 0, fillColor: colorFor(c.v), fillOpacity: 0.55
    }}).bindTooltip('TEC(近似): ' + c.v + ' TECU').addTo(map);
  }});
  var legend = L.control({{position:'bottomleft'}});
  legend.onAdd = function() {{
    var d = L.DomUtil.create('div');
    d.style.cssText = 'background:rgba(17,24,39,.92);padding:10px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;color:#f3f4f6;line-height:1.8';
    d.innerHTML = '<b>TEC（色逆変換・近似値）</b><br>低 <span style="color:#6699ff">■</span> 〜 高 <span style="color:#ff6666">■</span><br><small>範囲: ' + vmin.toFixed(1) + ' 〜 ' + vmax.toFixed(1) + ' TECU相当</small>';
    return d;
  }};
  legend.addTo(map);
}}
</script>
</body></html>"""


# ══════════════════════════════════════════════════════
# TAB 5: GNSS（地殻変動）
# ══════════════════════════════════════════════════════

def _ecef_to_geodetic(x, y, z):
    """
    ECEF直交座標(m)を緯度・経度(度)・楕円体高(m)へ変換する（Bowring法、GRS80楕円体）。
    GEONETの解析はGRS80/ITRF系のため、標準的なWGS84近似で十分な精度が得られる。
    """
    a = 6378137.0
    f = 1.0 / 298.257222101  # GRS80
    b = a * (1.0 - f)
    e2  = 1.0 - (b * b) / (a * a)
    ep2 = (a * a - b * b) / (b * b)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-6:
        return (90.0 if z > 0 else -90.0), math.degrees(lon), z - b
    theta = math.atan2(z * a, p * b)
    lat = math.atan2(z + ep2 * b * math.sin(theta) ** 3, p - e2 * a * math.cos(theta) ** 3)
    N = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - N
    return math.degrees(lat), math.degrees(lon), h


def _parse_pos_file(text):
    """
    GEONET「電子基準点日々の座標値」posファイル(1観測点1年分)をパースし、
    [(date, X, Y, Z), ...] （ECEF座標, 単位m）のリストを返す。

    ★ 注意: GSIの一次資料からは正確な列定義を確認できていない暫定パーサー。
    ヘッダ行（'*'や'#'始まり、非数値始まりの行）を読み飛ばし、
    「先頭列が8桁日付(yyyymmdd)、続く3列がECEFのX,Y,Z(概ね10^6〜10^7 m オーダー)」
    というパターンに一致する行だけを抽出する。想定と異なるフォーマットの場合は
    黙って0件になるだけなので、/gnss/raw_sample で実物を確認して調整すること。
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ("*", "#"):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) < 4:
            continue
        date_tok = parts[0]
        try:
            if len(date_tok) == 8 and date_tok.isdigit():
                d = datetime.strptime(date_tok, "%Y%m%d")
            else:
                continue
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except (ValueError, IndexError):
            continue
        # ECEF座標として妥当な範囲（地球半径オーダー）か簡易チェック
        if not (1e5 < abs(x) < 1e8 and 1e5 < abs(y) < 1e8 and 1e5 < abs(z) < 1e8):
            continue
        rows.append((d, x, y, z))
    return rows


def _gsi_sftp_connect():
    transport = paramiko.Transport((GSI_SFTP_HOST, GSI_SFTP_PORT))
    transport.banner_timeout = GNSS_SFTP_TIMEOUT
    transport.connect(username=GSI_SFTP_USER, password=GSI_SFTP_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    sftp.get_channel().settimeout(GNSS_SFTP_TIMEOUT)
    return sftp, transport


# 日々の座標値の格納ディレクトリ候補。2026年4月にF5.1解へ移行中のため両方試す。
_GNSS_POS_DIR_CANDIDATES = ("/data/coordinates_F5.1/GPS", "/data/coordinates_F5/GPS")


def _fetch_station_pos_text(sftp, code, year):
    """指定観測点・年のposファイル本文を取得する。見つからなければNone。"""
    yy = str(year)[2:]
    fname = f"{code}.{yy}.pos"
    for base in _GNSS_POS_DIR_CANDIDATES:
        path = f"{base}/{year}/{fname}"
        try:
            with sftp.open(path, "r") as f:
                return f.read().decode("utf-8", errors="ignore"), path
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"[GNSS] {code} {path} 取得エラー: {e}")
    return None, None


def _fetch_station_positions(sftp, code, lookback_days=GNSS_LOOKBACK_DAYS):
    """
    指定観測点の直近 lookback_days 日分の(date, X, Y, Z)を返す（新しい順ではなく日付昇順）。
    posファイルは1年分がまとまっているため、年をまたぐ場合は前年分も取得する。
    """
    today = datetime.now(timezone.utc).date()
    years = [today.year]
    if today.timetuple().tm_yday <= lookback_days + 5:
        years.append(today.year - 1)

    all_rows = []
    for year in years:
        text, _path = _fetch_station_pos_text(sftp, code, year)
        if text:
            all_rows.extend(_parse_pos_file(text))

    dedup = {d: (x, y, z) for d, x, y, z in all_rows}  # 同一日付は後勝ちで統一
    rows = sorted((d, x, y, z) for d, (x, y, z) in dedup.items())
    return rows[-(lookback_days + 5):]


def _compute_station_displacement(rows, lookback_days=GNSS_LOOKBACK_DAYS):
    """
    (date, X, Y, Z)の時系列から、直近lookback_days日間の東西・南北・上下変位(mm)を求める。
    初日を原点としたローカルENU座標に変換し、日々のばらつきを最小二乗直線で
    ならした上で、期間全体（span_days）分のトレンド変位を返す（短期変位ベクトル）。
    データ点が3点未満の場合はNoneを返す。
    """
    if len(rows) < 3:
        return None
    cutoff = rows[-1][0] - timedelta(days=lookback_days)
    recent = [r for r in rows if r[0] >= cutoff]
    if len(recent) < 3:
        recent = rows[-max(3, lookback_days):]
    if len(recent) < 3:
        return None

    x0, y0, z0 = recent[0][1], recent[0][2], recent[0][3]
    lat0, lon0, _h0 = _ecef_to_geodetic(x0, y0, z0)
    lat0r, lon0r = math.radians(lat0), math.radians(lon0)
    sl, cl = math.sin(lat0r), math.cos(lat0r)
    so, co = math.sin(lon0r), math.cos(lon0r)
    t0 = recent[0][0]

    ts, es, ns, us = [], [], [], []
    for d, x, y, z in recent:
        dx, dy, dz = x - x0, y - y0, z - z0
        e = -so * dx + co * dy
        n = -sl * co * dx - sl * so * dy + cl * dz
        u =  cl * co * dx + cl * so * dy + sl * dz
        ts.append((d - t0).days)
        es.append(e); ns.append(n); us.append(u)

    def _slope(xs, ys):
        n = len(xs)
        mx = sum(xs) / n; my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        return num / den if den > 1e-9 else 0.0

    span = ts[-1] - ts[0]
    if span <= 0:
        return None
    de = _slope(ts, es) * span * 1000.0  # m → mm
    dn = _slope(ts, ns) * span * 1000.0
    du = _slope(ts, us) * span * 1000.0
    return {"dE_mm": de, "dN_mm": dn, "dU_mm": du, "n_points": len(recent), "span_days": span}


def _refresh_gnss_cache():
    """GNSS_STATIONS全点について実データを取得し、_gnss_cacheを更新する（バックグラウンド実行想定）。"""
    with _gnss_lock:
        if _gnss_cache["updating"]:
            return
        _gnss_cache["updating"] = True
    try:
        try:
            sftp, transport = _gsi_sftp_connect()
        except Exception as e:
            msg = f"SFTP接続エラー: {e}"
            print(f"[GNSS] {msg}")
            with _gnss_lock:
                _gnss_cache["error"] = msg
            return

        results, errors = [], 0
        try:
            for code, name, lat, lon in GNSS_STATIONS:
                try:
                    rows = _fetch_station_positions(sftp, code)
                    disp = _compute_station_displacement(rows)
                    if disp:
                        results.append({"code": code, "name": name, "lat": lat, "lon": lon, **disp})
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    print(f"[GNSS] {code}({name}) 処理エラー: {e}")
        finally:
            try:
                sftp.close(); transport.close()
            except Exception:
                pass

        with _gnss_lock:
            if results:
                _gnss_cache["data"] = results
                _gnss_cache["error"] = (f"{errors}点でデータ取得/解析に失敗（{len(results)}/{len(GNSS_STATIONS)}点は成功）"
                                         if errors else None)
            else:
                _gnss_cache["error"] = f"全{len(GNSS_STATIONS)}点でデータ取得に失敗しました"
            _gnss_cache["ts"] = time.time()
        print(f"[GNSS] 更新完了: {len(results)}/{len(GNSS_STATIONS)}点 成功（エラー{errors}件）")
    finally:
        with _gnss_lock:
            _gnss_cache["updating"] = False


def get_gnss_vectors():
    """
    キャッシュ済みのGNSS変位データを返す。
    無効時（SFTP未設定/paramiko未インストール）や未取得時はNoneを返し、
    呼び出し側でプレースホルダー表示にフォールバックする。
    キャッシュが古い場合はバックグラウンドで非同期更新を開始する。
    """
    if not GSI_GNSS_ENABLED:
        return None
    with _gnss_lock:
        stale   = (time.time() - _gnss_cache["ts"]) > GNSS_CACHE_SEC
        data    = _gnss_cache["data"]
        updating = _gnss_cache["updating"]
    if (data is None or stale) and not updating:
        threading.Thread(target=_refresh_gnss_cache, daemon=True).start()
    return data


def render_gnss(updated_str):
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    date_str = now_jst.strftime("%Y年%m月%d日")

    vectors = get_gnss_vectors()  # None＝無効/未取得（プレースホルダー表示にフォールバック）
    with _gnss_lock:
        gnss_ts    = _gnss_cache["ts"]
        gnss_error = _gnss_cache["error"]

    live = vectors is not None
    if live:
        gnss_updated_str = (datetime.fromtimestamp(gnss_ts, JST).strftime("%Y-%m-%d %H:%M JST")
                             if gnss_ts else "取得中…")
        badge_bg, badge_fg, badge_txt = "#064e3b", "#6ee7b7", "実データ (GEONET SFTP)"
    elif GSI_GNSS_ENABLED:
        gnss_updated_str = "取得中…"
        badge_bg, badge_fg, badge_txt = "#1e3a5f", "#93c5fd", "実データ取得中…"
    else:
        gnss_updated_str = "―"
        badge_bg, badge_fg, badge_txt = "#1e3a5f", "#93c5fd", "プレースホルダー表示"

    vectors_json = json.dumps(vectors or [], ensure_ascii=False)
    lookback_note = f"直近 {GNSS_LOOKBACK_DAYS} 日間の座標差分（短期変位ベクトル・mm単位）"
    error_html = f'<p style="color:#f87171">⚠ {gnss_error}</p>' if (live and gnss_error) else ""
    not_configured_html = ("" if GSI_GNSS_ENABLED else
        '<p style="color:#fbbf24">SFTP未設定のためプレースホルダー表示です。'
        '環境変数 GSI_SFTP_USER / GSI_SFTP_PASS を設定すると実データ表示に切り替わります。</p>')

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
#panel{{width:270px;flex-shrink:0;background:#111827;border-left:2px solid #1f2937;overflow-y:auto;padding:14px}}
.sec{{margin-bottom:14px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid #1f2937;padding-bottom:4px}}
.sec p{{font-size:11px;color:#9ca3af;line-height:1.7}}
.link-btn{{display:block;padding:8px 12px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
           color:#fff;font-weight:700;font-size:12px;text-decoration:none;border-radius:6px;
           text-align:center;margin-bottom:6px;transition:opacity 0.2s}}
.link-btn:hover{{opacity:0.85}}
.phase{{background:#1f2937;border-left:3px solid #7c3aed;padding:6px 8px;border-radius:0 5px 5px 0;
        margin-bottom:6px;font-size:11px;color:#d1d5db}}
#station-list{{max-height:220px;overflow-y:auto;font-size:10px;color:#9ca3af;line-height:1.6}}
#station-list div{{padding:2px 0;border-bottom:1px solid #1f2937}}
</style></head><body>
<div id="hdr">
  <div>
    <h2>GNSS 地殻変動モニタリング（GEONET）</h2>
    <p>国土地理院 電子基準点ネットワーク / {date_str} / 地震データ更新: {updated_str} / GNSS更新: {gnss_updated_str}</p>
  </div>
  <span class="hbadge" style="background:{badge_bg};color:{badge_fg};margin-left:auto">{badge_txt}</span>
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
      <h3>変位ベクトルについて</h3>
      <p>
        {lookback_note}。<br>
        矢印は東西・南北方向の変位を誇張した縮尺で表示しています（実際の変位量はmm〜cmオーダー）。<br>
        対象範囲: ETAS計算対象域（先島諸島〜北海道）内の代表{len(GNSS_STATIONS)}点。
      </p>
      {error_html}
      {not_configured_html}
    </div>
    <div class="sec">
      <h3>観測点リスト</h3>
      <div id="station-list"></div>
    </div>
  </div>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}

var LIVE = {str(live).lower()};
var VECTORS = {vectors_json};

// プレースホルダー用の観測点座標（実データ無効時のフォールバック表示）
var placeholders=[
  [43.1,141.3],[42.9,143.2],[41.8,140.7],[40.8,140.7],[39.7,141.1],[38.3,140.9],
  [37.7,140.5],[37.0,140.4],[36.6,140.9],[36.4,140.5],[36.1,140.1],[35.9,139.6],
  [35.7,139.7],[35.5,139.6],[35.2,136.9],[35.0,135.8],[34.7,135.5],[34.4,132.5],
  [33.8,132.8],[33.6,133.5],[33.3,131.6],[33.2,130.3],[32.8,130.7],[31.9,131.4],
  [31.6,130.6],[26.2,127.7],[24.3,124.2],
  [36.7,137.2],[36.6,136.6],[36.1,136.2],[35.7,138.6],[35.4,133.9],[35.5,134.2]
];

var listEl = document.getElementById('station-list');

if (!LIVE) {{
  placeholders.forEach(function(p){{
    L.circleMarker(p,{{radius:4,color:'#34d399',fillColor:'#34d399',fillOpacity:0.7,weight:1}})
     .bindTooltip('GEONET電子基準点（プレースホルダー）').addTo(map);
  }});
  listEl.innerHTML = '<div style="color:#6b7280">実データ未取得のため一覧なし</div>';
}} else {{
  // 変位量(mm)を地図上で見やすくするための誇張スケール（メートル/mm）
  var SCALE_M_PER_MM = 400;
  var mPerDegLat = 111320.0;

  function destPoint(lat, lon, dE_mm, dN_mm){{
    var mPerDegLon = 111320.0 * Math.cos(lat * Math.PI/180);
    var dxM = dE_mm * SCALE_M_PER_MM, dyM = dN_mm * SCALE_M_PER_MM;
    return [lat + dyM/mPerDegLat, lon + dxM/mPerDegLon];
  }}

  var listHtml = '';
  VECTORS.forEach(function(v){{
    var mag = Math.sqrt(v.dE_mm*v.dE_mm + v.dN_mm*v.dN_mm);
    var color = mag > 15 ? '#f87171' : (mag > 7 ? '#fbbf24' : '#34d399');
    var dest = destPoint(v.lat, v.lon, v.dE_mm, v.dN_mm);

    L.circleMarker([v.lat, v.lon], {{radius:3.5, color:color, fillColor:color, fillOpacity:0.9, weight:1}})
     .addTo(map);
    L.polyline([[v.lat, v.lon], dest], {{color:color, weight:2, opacity:0.85}})
     .bindTooltip(v.name + '：東' + v.dE_mm.toFixed(1) + 'mm / 北' + v.dN_mm.toFixed(1) + 'mm（' + v.span_days + '日間）')
     .addTo(map);
    // 簡易矢頭
    var ang = Math.atan2(dest[0]-v.lat, dest[1]-v.lon);
    var ah = 0.10, aw = 0.35;
    var wing1 = [dest[0] - ah*Math.sin(ang) + aw*ah*Math.cos(ang), dest[1] - ah*Math.cos(ang) - aw*ah*Math.sin(ang)];
    var wing2 = [dest[0] - ah*Math.sin(ang) - aw*ah*Math.cos(ang), dest[1] - ah*Math.cos(ang) + aw*ah*Math.sin(ang)];
    L.polygon([dest, wing1, wing2], {{color:color, fillColor:color, fillOpacity:0.9, weight:0}}).addTo(map);

    listHtml += '<div><b style="color:#e5e7eb">' + v.name + '</b>（' + v.code + '）<br>'
      + '東西: ' + v.dE_mm.toFixed(1) + 'mm　南北: ' + v.dN_mm.toFixed(1) + 'mm　上下: ' + v.dU_mm.toFixed(1) + 'mm'
      + '　<span style="color:#6b7280">(' + v.n_points + '点/' + v.span_days + '日)</span></div>';
  }});
  listEl.innerHTML = listHtml || '<div style="color:#6b7280">データなし</div>';
}}

// 凡例
var legend=L.control({{position:'bottomleft'}});
legend.onAdd=function(){{
  var d=L.DomUtil.create('div');
  d.style.cssText='background:rgba(17,24,39,.92);padding:10px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;color:#f3f4f6;line-height:2';
  if (LIVE) {{
    d.innerHTML='<b>GNSS変位ベクトル</b><br>'
      + '<span style="color:#34d399">━</span> 小（≤7mm）　'
      + '<span style="color:#fbbf24">━</span> 中（〜15mm）　'
      + '<span style="color:#f87171">━</span> 大（15mm〜）<br>'
      + '<hr style="border-color:#374151;margin:4px 0">'
      + '<small>矢印の向き＝変位方向、長さは誇張表示</small>';
  }} else {{
    d.innerHTML='<b>GNSS 電子基準点</b><br><span style="color:#34d399">●</span> GEONET基準点（仮）<br>'
      + '<hr style="border-color:#374151;margin:4px 0"><small>実データはSFTP設定後に表示されます</small>';
  }}
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
<style>
/* ★ Bug fix: 観測点の気圧値ラベル。ズームアウト時は密集して見づらいため、
   一定のズームレベル以上でのみ表示する（updateValueLabels()で制御）。*/
.pres-val-label{{background:rgba(17,24,39,.85);border:1px solid #374151;color:#f3f4f6;
  font-size:11px;font-weight:600;padding:1px 4px;border-radius:3px;white-space:nowrap;
  box-shadow:none}}
.pres-val-label::before{{display:none}}
</style>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK={markers_js};
// ★ Bug fix: 拡大（ズームイン）した際に観測値（気圧の数値）が見えるように、
// 各観測点に常設ツールチップで数値ラベルを付与する。ただしズームアウト時は
// 観測点が密集して数値が重なり読めなくなるため、一定のズームレベル未満では
// ラベルを非表示にする（ホバー時のツールチップ／クリック時のポップアップは
// ズームレベルに関わらず従来通り利用可能）。
var VALUE_LABEL_MIN_ZOOM = 8;
var valueLabelMarkers = MK.map(function(d){{
  // Leafletは1レイヤーにつきtooltipを1つしか保持できないため、
  // 観測値ラベル（常設・ズーム連動で表示/非表示）に一本化する。
  // 観測点名込みの詳細はクリック時のポップアップ(bindPopup)で確認できる。
  var m = L.circleMarker([d.lat,d.lon],{{radius:4,color:d.color,fillColor:d.color,fillOpacity:1.0,weight:0.5}})
   .bindPopup(d.pop)
   .addTo(map);
  m.bindTooltip(d.pres+'hPa', {{permanent:true, direction:'top', offset:[0,-6], className:'pres-val-label'}});
  return m;
}});
function updateValueLabels(){{
  var show = map.getZoom() >= VALUE_LABEL_MIN_ZOOM;
  valueLabelMarkers.forEach(function(m){{
    var tt = m.getTooltip();
    if(!tt) return;
    var el = tt.getElement();
    if(el) el.style.display = show ? '' : 'none';
  }});
}}
map.on('zoomend', updateValueLabels);
map.whenReady(updateValueLabels);
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

# ★ Bug fix: GEM Global Active Faults Database には、日本周辺の海溝軸沿いの
# 沈み込み帯（プレート境界そのもの）も slip_type="Subduction Thrust" 等として
# 収録されており、これがそのまま「活断層」レイヤーに表示されると、別途PB2002由来の
# 「プレート境界」レイヤーと内容が重複・混同してしまっていた。
# 活断層レイヤーからはプレート境界(沈み込み帯)由来のフィーチャーを除外する。
PLATE_BOUNDARY_SLIP_KEYWORDS = ("subduction",)

def _is_plate_boundary_fault(slip_type):
    s = (slip_type or "").strip().lower()
    return any(kw in s for kw in PLATE_BOUNDARY_SLIP_KEYWORDS)

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
            slip_type = props.get("slip_type", "")
            if _is_plate_boundary_fault(slip_type):
                continue  # プレート境界(沈み込み帯)は「活断層」レイヤーから除外
            feats.append({
                "type": "Feature",
                "properties": {"name": props.get("name", ""), "slip_type": slip_type},
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
# ETAS・b値・活断層/プレート境界への応力負荷・気圧偏差を統合し、
# 地域ごとの「相対的な」地震リスク指数を算出する。
# 発生確率を予測するものではなく、あくまで複数指標の相対順位を
# 重み付け合成した比較指標である点に注意。
# ══════════════════════════════════════════════════════
RISK_GRID_SIZE = 0.5   # 統合リスクマップの共通格子（ETAS/b値より粗い格子に集約する）

# 各データソースの既定の重み（合計1.0。未選択/データ欠損のセルは
# 選択されている項目だけで自動的に再正規化される）
RISK_DEFAULT_WEIGHTS = {
    "etas":     0.55,
    "bvalue":   0.15,
    "fault":    0.10,
    "plate":    0.05,
    "pressure": 0.05,
    "tec":      0.10,
}
RISK_LABELS = {"etas": "ETAS", "bvalue": "b値", "fault": "活断層",
               "plate": "プレート境界", "pressure": "気圧", "tec": "TEC(電離圏・実験的)"}

# ── 活断層・プレート境界への「応力負荷」評価パラメータ ──────────
# 活断層/プレート境界近接度は、以前は「格子セルから断層線までの単純な最短距離」
# だったが、それだと地震活動と無関係な静的指標になってしまう。
# ここでは各地震の震源が周辺の断層・プレート境界に与える影響を、
# メカニズム解（走向・傾斜・すべり角）を使わない等方近似のクーロン応力変化に
# 準じたモデルで評価し、断層・プレート境界側の「応力負荷」として数値化する。
#   ・地震のモーメント M0 = 10^(1.5M+9.05) [Hanks & Kanamori 1979]
#   ・破壊サイズ（下限距離）: 地表断層長 L[km] = 10^(-2.44+0.59M) [Wells & Coppersmith 1994]
#   ・静的応力変化は震源近傍で r^-3 減衰する等方近似（メカニズム解が無いための簡略化）
#   ・時間経過とともにOmori-Utsu型 (Δt+c)^-p で寄与を減衰させる
#     （応力変化自体は本来は減衰しないが、本アプリでは「直近の活動によって
#      現在どの断層/プレート境界が注視すべき状態にあるか」という監視指標として
#      扱うため、意図的に古い地震の寄与を弱めている）
class FaultStressParams:
    M0_REF_MAG = 5.0     # 正規化基準マグニチュード（この規模の地震1回分を概ね1.0とする）
    R_MIN_FLOOR_KM = 1.0 # 破壊サイズがごく小さい場合の距離下限
    TIME_C = 1.0         # Omori-Utsu 減衰の c（日）
    TIME_P = 1.0         # Omori-Utsu 減衰の p
    # ★ Bug fix (2026-08): Renderのメモリ制限でOOMになる問題への対策。
    # quakes.csvは無期限に蓄積されるため地震件数が数千〜数万件に増える一方、
    # 活断層データ(GEM Global Active Faults)もJapanバウンディングボックス内だけで
    # 数千〜数万頂点になりうる。対策なしだと (点数 × 地震数) の密行列がそのまま
    # メモリに載ってしまうため、以下で計算前に上限を設けて間引く。
    MAX_LOOKBACK_DAYS = 730   # これより古い地震は時間減衰でほぼ寄与ゼロなので除外
    MAX_QUAKES        = 4000  # 上記フィルタ後もなお多い場合は直近優先で間引く
    MAX_POINTS        = 2000  # 断層/プレート境界サンプル点の上限（多い場合は等間隔に間引く）
    POINT_CHUNK       = 300   # 点側のチャンクサイズ（ピークメモリを固定するため）
    QUAKE_CHUNK       = 800   # 地震側のチャンクサイズ
FSP = FaultStressParams()

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

_fault_stress_cache = {"grid": None, "computed_for": None}
_plate_stress_cache = {"grid": None, "computed_for": None}

def _quake_signature(quakes):
    """地震リストの「版」を安価に識別するための署名。件数＋最新時刻で近似する
    （厳密なハッシュではないが、応力負荷キャッシュの再計算タイミング判定には十分）。"""
    if not quakes:
        return (0, None)
    latest_time = max((q.get("time", "") for q in quakes), default=None)
    return (len(quakes), latest_time)

def _decimate_points(points_latlon, max_points):
    """点群が多すぎる場合、等間隔に間引いて上限点数以下にする。
    リスク格子(RISK_GRID_SIZE=0.5°)に対して十分な密度は保ちつつ、
    メモリ/計算量を抑えるための簡易な間引き。"""
    n = len(points_latlon)
    if n <= max_points:
        return points_latlon
    stride = math.ceil(n / max_points)
    return points_latlon[::stride]

def _fault_plate_load_at_points(points_latlon, quakes, ref_time=None,
                                 quake_chunk=None, point_chunk=None):
    """
    断層/プレート境界のサンプル点(points_latlon)それぞれについて、与えられた
    地震リストからの「応力負荷」を等方近似のクーロン応力変化モデルで積算する。
        load = Σ_quakes  (M0/M0_ref) / max(r_km, r_min_km)^3 × 時間減衰(Δt)
    r は震源とサンプル点の3次元距離（水平距離と震源深さから算出。断層/プレート
    境界のトレースはほぼ地表付近とみなす）。
    戻り値は points_latlon と同じ順序の numpy 配列。

    ★ Bug fix (2026-08): 点数×地震数の密行列を一括確保するとRenderのメモリ制限で
    OOMになったため、(1) 寄与がほぼゼロな古い地震を事前に除外、(2) それでも
    件数が多い場合は直近優先で上限件数まで間引き、(3) 点側・地震側の両方を
    チャンク分割してピークメモリを固定サイズに抑える、の3段構えで対策する。
    """
    quake_chunk = quake_chunk or FSP.QUAKE_CHUNK
    point_chunk = point_chunk or FSP.POINT_CHUNK
    n_pts = len(points_latlon)
    if n_pts == 0 or not quakes:
        return np.zeros(n_pts)

    ref_time = ref_time or datetime.now(timezone.utc)
    qlat, qlon, qmag, qdepth, qdt_days = [], [], [], [], []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
            dt_days = (ref_time - t).total_seconds() / 86400.0
            if dt_days < 0 or dt_days > FSP.MAX_LOOKBACK_DAYS:
                continue  # 未来の地震、または古すぎて寄与が無視できる地震は除外
        except Exception:
            continue
        qlat.append(q["lat"]); qlon.append(q["lon"]); qmag.append(q["mag"])
        qdepth.append(q.get("depth", 10.0) or 10.0)
        qdt_days.append(dt_days)
    if not qlat:
        return np.zeros(n_pts)

    # 上限件数を超える場合は直近の地震を優先して間引く（時間減衰が効くため
    # 古い地震から間引いても寄与への影響は小さい）
    if len(qlat) > FSP.MAX_QUAKES:
        order = np.argsort(qdt_days)[:FSP.MAX_QUAKES]  # dt_daysが小さい=新しい順
        qlat = [qlat[i] for i in order]; qlon = [qlon[i] for i in order]
        qmag = [qmag[i] for i in order]; qdepth = [qdepth[i] for i in order]
        qdt_days = [qdt_days[i] for i in order]

    qlat = np.array(qlat); qlon = np.array(qlon); qmag = np.array(qmag)
    qdepth = np.array(qdepth); qdt = np.array(qdt_days)

    m0_ref = 10.0 ** (1.5 * FSP.M0_REF_MAG + 9.05)
    moment_ratio = (10.0 ** (1.5 * qmag + 9.05)) / m0_ref
    rupture_len_km = 10.0 ** (-2.44 + 0.59 * qmag)          # Wells & Coppersmith 1994
    r_min = np.maximum(rupture_len_km / 2.0, FSP.R_MIN_FLOOR_KM)
    time_w = (qdt + FSP.TIME_C) ** (-FSP.TIME_P)             # Omori-Utsu型 時間減衰

    pts = np.array(points_latlon)
    cos_lat_all = np.cos(np.radians(pts[:, 0]))
    load = np.zeros(n_pts)

    # 点側・地震側の両方をチャンク分割し、ピークメモリを point_chunk×quake_chunk
    # の行列サイズ（固定）に抑える
    for pstart in range(0, n_pts, point_chunk):
        p_batch = pts[pstart:pstart + point_chunk]
        p_cos = cos_lat_all[pstart:pstart + point_chunk]
        batch_load = np.zeros(len(p_batch))
        for qstart in range(0, len(qlat), quake_chunk):
            blat = qlat[qstart:qstart + quake_chunk]; blon = qlon[qstart:qstart + quake_chunk]
            bdepth = qdepth[qstart:qstart + quake_chunk]
            bmoment = moment_ratio[qstart:qstart + quake_chunk]
            brmin = r_min[qstart:qstart + quake_chunk]
            btime = time_w[qstart:qstart + quake_chunk]

            dlat = (p_batch[:, 0:1] - blat[None, :]) * 111.0
            dlon = (p_batch[:, 1:2] - blon[None, :]) * 111.0 * p_cos[:, None]
            dh = np.sqrt(dlat ** 2 + dlon ** 2)
            r = np.sqrt(dh ** 2 + bdepth[None, :] ** 2)
            r = np.maximum(r, brmin[None, :])

            batch_load += (bmoment[None, :] / (r ** 3) * btime[None, :]).sum(axis=1)
        load[pstart:pstart + point_chunk] = batch_load
    return load

def _load_to_grid_max(points_latlon, loads):
    """断層/プレート境界のサンプル点ごとの応力負荷を、最も近いリスク格子セルへ
    割り当てる。1セルに複数の点が対応する場合はその中の最大値をセルの代表値とする
    （＝そのセル付近を通る断層/プレート境界のうち最も負荷が高いセグメントを表す）。"""
    n_pts = len(points_latlon)
    if n_pts == 0:
        return {}
    cell_keys = list(_RISK_CELLS.keys())
    cell_latlon = np.array([_RISK_CELLS[k] for k in cell_keys])
    cos_lat = np.cos(np.radians(cell_latlon[:, 0]))
    pts = np.array(points_latlon)

    nearest_cell_idx = np.empty(n_pts, dtype=int)
    chunk = 2500
    for start in range(0, n_pts, chunk):
        batch = pts[start:start + chunk]
        dlat = (cell_latlon[:, 0:1] - batch[:, 0][None, :]) * 111.0
        dlon = (cell_latlon[:, 1:2] - batch[:, 1][None, :]) * 111.0 * cos_lat[:, None]
        dist = np.sqrt(dlat ** 2 + dlon ** 2)
        nearest_cell_idx[start:start + chunk] = dist.argmin(axis=0)

    best = {}
    for i, ci in enumerate(nearest_cell_idx):
        key = cell_keys[ci]
        v = float(loads[i])
        if key not in best or v > best[key]:
            best[key] = v
    return best

def get_fault_stress_grid(quakes):
    """各リスク格子セル付近を通る活断層について、直近の地震活動による
    クーロン応力変化の簡易近似（等方近似・時間減衰つき）に基づく「応力負荷」を返す。
    断層線データ or 地震リストのいずれかが更新されるまではキャッシュを使い回す。"""
    global _fault_stress_cache
    fault_data = get_japan_active_faults()
    cache_key = (_fault_cache.get("fetched_at"), _quake_signature(quakes))
    if (_fault_stress_cache["grid"] is not None
            and _fault_stress_cache["computed_for"] == cache_key):
        return _fault_stress_cache["grid"]
    pts = _flatten_line_points(fault_data)
    pts = _decimate_points(pts, FSP.MAX_POINTS)
    loads = _fault_plate_load_at_points(pts, quakes)
    grid = _load_to_grid_max(pts, loads)
    _fault_stress_cache = {"grid": grid, "computed_for": cache_key}
    print(f"[統合リスク] 活断層 応力負荷 {len(grid)}セル計算完了（サンプル点{len(pts)}件）")
    return grid

def get_plate_stress_grid(quakes):
    """各リスク格子セル付近を通るプレート境界について、同様の応力負荷を返す。"""
    global _plate_stress_cache
    plate_data = get_plate_boundaries()
    cache_key = (_plate_cache.get("fetched_at"), _quake_signature(quakes))
    if (_plate_stress_cache["grid"] is not None
            and _plate_stress_cache["computed_for"] == cache_key):
        return _plate_stress_cache["grid"]
    pts = _flatten_line_points(plate_data, bbox=FAULTS_BBOX)
    pts = _decimate_points(pts, FSP.MAX_POINTS)
    loads = _fault_plate_load_at_points(pts, quakes)
    grid = _load_to_grid_max(pts, loads)
    _plate_stress_cache = {"grid": grid, "computed_for": cache_key}
    print(f"[統合リスク] プレート境界 応力負荷 {len(grid)}セル計算完了（サンプル点{len(pts)}件）")
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

def compute_risk_grid(etas_grid_scores, bvalue_grid, quakes):
    """統合リスクマップ用に、各データソースのセルごとの正規化スコア(0〜1)と
    元データ値をまとめたセル一覧を返す。重み付け合成はフロントエンド(JS)側で行い、
    チェックボックスの選択変更に即座に反映できるようにする。"""
    etas_raw     = _risk_etas_raw(etas_grid_scores)
    bvalue_raw   = _risk_bvalue_raw(bvalue_grid)
    fault_raw    = get_fault_stress_grid(quakes)
    plate_raw    = get_plate_stress_grid(quakes)
    pressure_raw = _risk_pressure_raw()
    tec_raw      = _risk_tec_raw()

    etas_rank     = _percentile_rank_map(etas_raw, invert=False)
    bvalue_rank   = _percentile_rank_map(bvalue_raw, invert=True)    # 低b値 = 高リスク
    fault_rank    = _percentile_rank_map(fault_raw, invert=False)    # 応力負荷が大きい = 高リスク
    plate_rank    = _percentile_rank_map(plate_raw, invert=False)    # 応力負荷が大きい = 高リスク
    pressure_rank = _percentile_rank_map(pressure_raw, invert=False)
    tec_rank      = _percentile_rank_map(tec_raw, invert=False)      # 偏差が大きい = 高リスク

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
        if key in tec_rank:
            comp["tec"] = {"s": round(tec_rank[key], 4), "r": round(tec_raw[key], 2)}
        if comp:
            cells.append({"lat": round(lat, 3), "lon": round(lon, 3), "c": comp})
    return cells


# ── 統合リスクマップ: セル別「前日比」「推移」────────────────────
# 総合リスクマップは毎時のスナップショット(data/snapshots/)に保存された
# ETAS/b値格子から過去分を再計算できるが、活断層・プレート境界への応力負荷は
# 本来は地震のたびに変化する動的な値であるものの、過去168時点それぞれを
# 再計算するのは負荷が大きいため常に「現在値」を用いる簡易実装とし、気圧偏差は
# スナップショットに保存されていないため過去分の内訳には含めない
# （選択項目から欠けても重み再正規化により自動的に無視される）。
RISK_TREND_MAX_POINTS = 7 * 24  # 直近7日分（毎時想定で最大168点）に限定

def _risk_component_keys_from_param(sel_param):
    keys = [s for s in (sel_param or "").split(",") if s in RISK_LABELS]
    return keys or ["etas", "bvalue", "fault", "plate"]

def compute_risk_trend_for_cell(gi, gj, selected_keys):
    """指定したリスク格子セル(gi,gj)について、選択中のデータソースだけを使って
    総合リスクスコア(0〜1)の推移を計算し、[{"t": ISO時刻, "score": 0-1}, ...] を
    時系列順（古い→新しい）で返す。あわせて「前日比」比較用のスコアも返す。"""
    selected = set(selected_keys)

    # 活断層・プレート境界の応力負荷は本来は地震のたびに変化する動的な値だが、
    # 過去168時点それぞれについて全地震データを再フィルタ・再計算するのは重いため、
    # ここでは従来と同様に「現在の応力負荷」を全期間の推移に使い回す簡易実装とする
    # （推移グラフ上のfault/plate成分は常に最新値になる）。
    fault_rank_all = plate_rank_all = {}
    if "fault" in selected or "plate" in selected:
        quakes_now = load_quakes()
    if "fault" in selected:
        fault_rank_all = _percentile_rank_map(get_fault_stress_grid(quakes_now), invert=False)
    if "plate" in selected:
        plate_rank_all = _percentile_rank_map(get_plate_stress_grid(quakes_now), invert=False)

    fnames = list_snapshots()  # 新しい順
    fnames = list(reversed(fnames[:RISK_TREND_MAX_POINTS]))  # 古い→新しい、直近分のみ

    points = []
    for fname in fnames:
        snap = load_snapshot(fname)
        if snap is None:
            continue
        comp = {}
        if "etas" in selected:
            etas_rank = _percentile_rank_map(_risk_etas_raw(snap["etas"]), invert=False)
            if (gi, gj) in etas_rank:
                comp["etas"] = etas_rank[(gi, gj)]
        if "bvalue" in selected:
            bvalue_rank = _percentile_rank_map(_risk_bvalue_raw(snap["bvalue"]), invert=True)
            if (gi, gj) in bvalue_rank:
                comp["bvalue"] = bvalue_rank[(gi, gj)]
        if (gi, gj) in fault_rank_all:
            comp["fault"] = fault_rank_all[(gi, gj)]
        if (gi, gj) in plate_rank_all:
            comp["plate"] = plate_rank_all[(gi, gj)]
        if not comp:
            continue
        wsum = sum(RISK_DEFAULT_WEIGHTS[k] for k in comp)
        score = sum(RISK_DEFAULT_WEIGHTS[k] * v for k, v in comp.items()) / wsum
        points.append({"t": snap.get("timestamp_jst", ""), "score": round(score, 4)})

    prev_day_score = None
    if len(points) >= 2:
        try:
            latest_dt = datetime.fromisoformat(points[-1]["t"])
            target = latest_dt - timedelta(hours=24)
            best, best_diff = None, None
            for p in points[:-1]:
                try:
                    dt = datetime.fromisoformat(p["t"])
                except Exception:
                    continue
                diff = abs(dt - target)
                if best_diff is None or diff < best_diff:
                    best, best_diff = p, diff
            if best is not None and best_diff is not None and best_diff <= timedelta(hours=3):
                prev_day_score = best["score"]
        except Exception:
            pass

    return points, prev_day_score

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
#panel{{width:300px;flex-shrink:0;background:#111827;border-right:2px solid #1f2937;overflow-y:auto;padding:14px;
    position:relative;transition:margin-left 0.2s}}
#panel.closed{{margin-left:-300px}}
#panelTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px}}
#panel h2{{font-size:14px;color:#f3f4f6}}
#panelClose{{flex-shrink:0;width:22px;height:22px;border:none;border-radius:5px;background:#374151;color:#d1d5db;
    cursor:pointer;font-size:13px;line-height:1;display:flex;align-items:center;justify-content:center}}
#panelClose:hover{{background:#4b5563;color:#fff}}
#panelReopen{{position:absolute;top:70px;left:0;z-index:5000;width:34px;height:78px;border:none;
    border-radius:0 8px 8px 0;background:#1f2937;color:#9ca3af;cursor:pointer;font-size:12px;
    display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;
    box-shadow:2px 0 8px rgba(0,0,0,.4)}}
#panelReopen:hover{{background:#2563eb;color:#fff}}
#panelReopen.show{{display:flex}}
#panelReopen .arrow{{font-size:15px;line-height:1}}
#panelReopen .vlabel{{writing-mode:vertical-rl;letter-spacing:1px;font-size:10px;font-weight:600}}
#panel p.sub{{font-size:11px;color:#6b7280;margin-bottom:12px;line-height:1.6}}
.sec{{margin-bottom:16px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid #1f2937;padding-bottom:4px;
    display:flex;align-items:center;justify-content:space-between}}
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
.tbl-toggle{{border:none;background:#1f2937;color:#9ca3af;font-size:10px;font-weight:600;
    padding:2px 8px;border-radius:4px;cursor:pointer;flex-shrink:0}}
.tbl-toggle:hover{{background:#374151;color:#f3f4f6}}
#rankWrap{{max-height:260px;overflow-y:auto;border:1px solid #1f2937;border-radius:6px}}
#rankWrap.collapsed{{display:none}}
table.rank-table{{width:100%;border-collapse:collapse;font-size:10.5px}}
table.rank-table thead tr{{background:#1f2937;position:sticky;top:0}}
table.rank-table th{{padding:5px 4px;color:#9ca3af;text-align:left;font-weight:600;white-space:nowrap}}
table.rank-table td{{padding:4px;color:#e5e7eb;border-top:1px solid #1f2937;white-space:nowrap}}
.rrow{{cursor:pointer}}
.rrow:hover{{background:#1e2d40}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
#lg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:10px 13px;border-radius:8px;border:1px solid #374151;font-size:11px;line-height:1.9;color:#f3f4f6}}
#hdr{{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:1000;
    background:rgba(17,24,39,.92);padding:6px 16px;border-radius:6px;font-size:11px;color:#9ca3af}}
/* ── セル詳細パネル（クリックで表示）: 内訳/棒グラフ/前日比/推移を
   はっきり区切って表示し、ごちゃつかないようにする ── */
#detailBox{{display:none;position:absolute;top:14px;right:14px;z-index:1500;width:290px;
    background:rgba(17,24,39,.97);border:1px solid #374151;border-radius:10px;padding:12px 14px;
    font-size:11px;color:#e5e7eb;max-height:calc(100% - 28px);overflow-y:auto}}
#detailBox .dHead{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}}
#dTitle{{font-weight:700;font-size:14px;color:#f3f4f6}}
#dClose{{flex-shrink:0;width:20px;height:20px;border:none;border-radius:5px;background:#374151;color:#d1d5db;
    cursor:pointer;font-size:12px;line-height:1}}
#dClose:hover{{background:#4b5563;color:#fff}}
#dLoc{{font-size:10px;color:#6b7280;margin:2px 0 10px}}
.dSection{{margin-top:10px;padding-top:10px;border-top:1px solid #1f2937}}
.dSection h4{{font-size:11px;font-weight:700;color:#60a5fa;margin-bottom:6px}}
.brk-row{{display:flex;justify-content:space-between;font-size:11px;padding:2px 0;color:#d1d5db}}
.brk-val{{font-weight:700;color:#f3f4f6}}
#dPrevDay{{font-size:11.5px;margin-top:2px}}
#dTrendState{{font-size:10px;color:#6b7280;margin-bottom:4px}}
canvas.dChart{{display:block;width:100%}}
</style></head><body>
<button id="panelReopen" onclick="togglePanel()" title="設定・一覧を開く">
  <span class="arrow">▶</span><span class="vlabel">設定・一覧</span>
</button>
<div id="panel">
  <div id="panelTop">
    <h2>統合リスクマップ <span style="font-size:10px;color:#fbbf24">β</span></h2>
    <button id="panelClose" onclick="togglePanel()" title="パネルを閉じる">✕</button>
  </div>
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
      <span class="clabel">活断層 応力負荷</span><span class="cweight">w={w['fault']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_plate" checked onchange="onToggle('plate')">
      <span class="clabel">プレート境界 応力負荷</span><span class="cweight">w={w['plate']}</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_pressure" onchange="onToggle('pressure')">
      <span class="clabel">気圧偏差</span><span class="cweight">w={w['pressure']}</span></div>
    <div class="chk-row disabled"><input type="checkbox" disabled>
      <span class="clabel">TEC（電離圏）</span><span class="cbadge">近日公開</span></div>
    <div class="chk-row disabled"><input type="checkbox" disabled>
      <span class="clabel">GNSS（地殻変動）</span><span class="cbadge">近日公開</span></div>
  </div>

  <div class="sec">
    <h3>セル別ランキング（総合リスク順）
      <button class="tbl-toggle" id="rankToggleBtn" onclick="toggleRankTable()">閉じる</button>
    </h3>
    <div id="rankWrap">
      <table class="rank-table">
        <thead><tr><th>#</th><th>緯度</th><th>経度</th><th>総合</th><th>Lv</th></tr></thead>
        <tbody id="rankBody"></tbody>
      </table>
    </div>
    <div style="font-size:10px;color:#6b7280;margin-top:4px">上位<span id="rankShown">0</span>件 / 表示中<span id="rankTotal">0</span>件中（行クリックで地図上を表示）</div>
  </div>

  <div id="cellCount">表示中のセル数: <span id="cellN">0</span> / {n_cells}</div>
  <div class="note">
    重みは選択されたデータのみを使い自動的に再正規化されます。<br>
    セルをクリックすると統合リスク指数と各データの寄与度（内訳）を表示します。<br>
    「地殻変動」プリセットは、GNSS実装までの暫定的な代理指標としてプレート境界応力負荷を使用しています。
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
  <div id="detailBox">
    <div class="dHead">
      <div>
        <div id="dTitle">-</div>
        <div id="dLoc"></div>
      </div>
      <button id="dClose" onclick="closeDetail()" title="閉じる">✕</button>
    </div>

    <div class="dSection">
      <h4>内訳（寄与度）</h4>
      <div id="dBreakdown"></div>
      <canvas id="dBarCanvas" class="dChart" width="256" height="110"></canvas>
    </div>

    <div class="dSection">
      <h4>前日比</h4>
      <div id="dPrevDay">-</div>
    </div>

    <div class="dSection">
      <h4>推移（直近・最大7日）</h4>
      <div id="dTrendState">-</div>
      <canvas id="dTrendCanvas" class="dChart" width="256" height="90"></canvas>
    </div>
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
var COMP_COLOR = {{etas:'#ef4444', bvalue:'#f97316', fault:'#facc15', plate:'#38bdf8', pressure:'#a78bfa'}};
var lastShownList = [];   // redraw()のたびに更新: [{{cell, comp, lv}}, ...]

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

// 寄与度(浮動小数)の配列を、合計が total(整数)に一致するよう最大剰余法で丸める
function roundPartsToTotal(parts, total){{
  var floors = parts.map(Math.floor);
  var used = floors.reduce(function(a,b){{return a+b;}}, 0);
  var remainder = Math.round(total - used);
  var order = parts.map(function(p,i){{return {{i:i, frac:p-Math.floor(p)}};}})
                   .sort(function(a,b){{return b.frac-a.frac;}});
  var result = floors.slice();
  for(var k=0; k<remainder && k<order.length; k++){{ result[order[k].i] += 1; }}
  return result;
}}

// 内訳の横棒グラフ（外部ライブラリ不使用、Canvas直描画）
function drawBarChart(canvas, labels, values, colors){{
  var ctx = canvas.getContext('2d');
  var W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);
  var n = labels.length;
  if(n===0) return;
  var maxV = Math.max(1, Math.max.apply(null, values.map(function(v){{return Math.abs(v);}})));
  var labelW = 66, valW = 30;
  var trackW = W - labelW - valW;
  var rowH = H / n;
  var barH = Math.min(14, rowH - 6);
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  for(var i=0; i<n; i++){{
    var cy = rowH*i + rowH/2;
    ctx.fillStyle = '#9ca3af';
    ctx.fillText(labels[i], 0, cy);
    ctx.fillStyle = '#1f2937';
    ctx.fillRect(labelW, cy-barH/2, trackW, barH);
    var w = Math.max(1, trackW * (Math.abs(values[i]) / maxV));
    ctx.fillStyle = colors[i] || '#60a5fa';
    ctx.fillRect(labelW, cy-barH/2, w, barH);
    ctx.fillStyle = '#f3f4f6';
    ctx.fillText((values[i]>=0?'+':'')+values[i], labelW+trackW+4, cy);
  }}
}}

// 推移（時系列）の折れ線グラフ
function drawLineChart(canvas, points){{
  var ctx = canvas.getContext('2d');
  var W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);
  if(!points || points.length<2){{
    ctx.fillStyle='#6b7280'; ctx.font='11px sans-serif'; ctx.textBaseline='middle';
    ctx.fillText('データが不足しています', 4, H/2);
    return;
  }}
  var pad = 8, topPad = 14;
  var vals = points.map(function(p){{return p.score*100;}});
  var minV = Math.min.apply(null, vals), maxV = Math.max.apply(null, vals);
  if(maxV-minV < 1){{ minV -= 1; maxV += 1; }}
  ctx.strokeStyle = '#60a5fa'; ctx.lineWidth = 1.5; ctx.beginPath();
  points.forEach(function(p,i){{
    var x = pad + (W-2*pad) * (points.length===1 ? 0 : i/(points.length-1));
    var y = H-pad - (H-topPad-pad) * ((p.score*100-minV)/(maxV-minV));
    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  }});
  ctx.stroke();
  ctx.fillStyle = '#9ca3af'; ctx.font = '9px sans-serif'; ctx.textBaseline = 'top';
  ctx.fillText(Math.round(maxV), 2, 2);
  ctx.textBaseline = 'bottom';
  ctx.fillText(Math.round(minV), 2, H-2);
}}

function closeDetail(){{
  document.getElementById('detailBox').style.display = 'none';
}}

function showDetail(cell, comp, lv){{
  document.getElementById('detailBox').style.display = 'block';
  var total = Math.round(comp.score*100);
  document.getElementById('dTitle').textContent = '総合リスク ' + total + ' (Lv' + lv + ')';
  document.getElementById('dLoc').textContent = '緯度' + cell.lat.toFixed(2) + ' / 経度' + cell.lon.toFixed(2);

  var labels = [], contribFloat = [], colors = [];
  comp.used.forEach(function(k){{
    var d = cell.c[k];
    var nw = WEIGHTS[k] / comp.wsum;
    labels.push(LABELS[k]);
    contribFloat.push(nw * d.s * 100);
    colors.push(COMP_COLOR[k] || '#60a5fa');
  }});
  var contribInt = roundPartsToTotal(contribFloat, total);

  document.getElementById('dBreakdown').innerHTML = comp.used.map(function(k, i){{
    return '<div class="brk-row"><span>' + LABELS[k] + '</span>' +
      '<span class="brk-val">' + (contribInt[i]>=0?'+':'') + contribInt[i] + '</span></div>';
  }}).join('');
  drawBarChart(document.getElementById('dBarCanvas'), labels, contribInt, colors);

  // 前日比・推移は別セクションとして非同期取得（内訳の表示とはごちゃ混ぜにしない）
  document.getElementById('dPrevDay').textContent = '取得中…';
  document.getElementById('dTrendState').textContent = '取得中…';
  drawLineChart(document.getElementById('dTrendCanvas'), []);

  var sel = comp.used.join(',');
  fetch('/data/risk_cell_trend?lat=' + cell.lat + '&lon=' + cell.lon + '&sel=' + encodeURIComponent(sel))
    .then(function(r){{ return r.json(); }})
    .then(function(res){{
      var pts = res.points || [];
      if(res.prev_day_score != null){{
        var diff = total - Math.round(res.prev_day_score*100);
        var color = diff>0 ? '#f87171' : (diff<0 ? '#4ade80' : '#9ca3af');
        document.getElementById('dPrevDay').innerHTML =
          '<span style="color:'+color+';font-weight:700">' + (diff>0?'+':'') + diff + '</span>' +
          ' <span style="color:#6b7280">(24時間前比)</span>';
      }} else {{
        document.getElementById('dPrevDay').textContent = 'データ不足のため算出できません';
      }}
      if(pts.length>=2){{
        document.getElementById('dTrendState').textContent = '直近' + pts.length + '時点の推移';
        drawLineChart(document.getElementById('dTrendCanvas'), pts);
      }} else {{
        document.getElementById('dTrendState').textContent = '推移データが不足しています';
      }}
    }})
    .catch(function(){{
      document.getElementById('dPrevDay').textContent = '取得に失敗しました';
      document.getElementById('dTrendState').textContent = '取得に失敗しました';
    }});
}}

function renderRankTable(list){{
  list.sort(function(a,b){{ return b.comp.score - a.comp.score; }});
  var TOP_N = 60;
  var top = list.slice(0, TOP_N);
  document.getElementById('rankBody').innerHTML = top.map(function(item, idx){{
    return '<tr class="rrow" onclick="focusCell(' + item.cell.lat + ',' + item.cell.lon + ')">' +
      '<td>' + (idx+1) + '</td>' +
      '<td>' + item.cell.lat.toFixed(2) + '</td>' +
      '<td>' + item.cell.lon.toFixed(2) + '</td>' +
      '<td style="color:' + RISK_COLOR[item.lv] + ';font-weight:700">' + Math.round(item.comp.score*100) + '</td>' +
      '<td>Lv' + item.lv + '</td></tr>';
  }}).join('');
  document.getElementById('rankShown').textContent = top.length;
  document.getElementById('rankTotal').textContent = list.length;
}}

function focusCell(lat, lon){{
  var item = lastShownList.find(function(x){{
    return Math.abs(x.cell.lat-lat)<1e-6 && Math.abs(x.cell.lon-lon)<1e-6;
  }});
  if(!item) return;
  map.flyTo([lat, lon], Math.max(map.getZoom(), 7), {{duration:0.6}});
  showDetail(item.cell, item.comp, item.lv);
}}

function toggleRankTable(){{
  var wrap = document.getElementById('rankWrap');
  var btn = document.getElementById('rankToggleBtn');
  var collapsed = wrap.classList.toggle('collapsed');
  btn.textContent = collapsed ? '開く' : '閉じる';
}}

function togglePanel(){{
  var panel = document.getElementById('panel');
  var reopen = document.getElementById('panelReopen');
  panel.classList.toggle('closed');
  reopen.classList.toggle('show', panel.classList.contains('closed'));
  setTimeout(function(){{ map.invalidateSize(); }}, 220);
}}

function redraw(){{
  if(rectLayer) map.removeLayer(rectLayer);
  rectLayer = L.layerGroup().addTo(map);
  var shown = 0;
  lastShownList = [];
  CELLS.forEach(function(cell){{
    var comp = computeComposite(cell);
    if(!comp) return;
    shown++;
    var lv = levelOf(comp.score);
    lastShownList.push({{cell:cell, comp:comp, lv:lv}});
    var rect = L.rectangle(
      [[cell.lat-GS/2, cell.lon-GS/2],[cell.lat+GS/2, cell.lon+GS/2]],
      {{color:null, weight:0, fill:true, fillColor:RISK_COLOR[lv], fillOpacity:0.6}}
    );
    rect.on('click', function(){{ showDetail(cell, comp, lv); }});
    rect.addTo(rectLayer);
  }});
  document.getElementById('cellN').textContent = shown;
  renderRankTable(lastShownList);
}}

redraw();
</script></body></html>"""


# ══════════════════════════════════════════════════════
# メインページ（タブシェル）
# ══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════
# TAB 7: アーカイブ（1時間ごとの解析結果ログ、旧称スナップショット）
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
#ghBackup{display:none;width:calc(100% - 28px);margin:0 14px 10px;padding:5px 0;text-align:center;
    font-size:10px;font-weight:600;color:#a7f3d0;background:transparent;border:1px solid #065f46;
    border-radius:6px;cursor:pointer}
#ghBackup:hover{background:#064e3b}
#ghBackup:disabled{opacity:.5;cursor:default}
#ghRestore{display:none;width:calc(100% - 28px);margin:0 14px 4px;padding:5px 0;text-align:center;
    font-size:10px;font-weight:600;color:#93c5fd;background:transparent;border:1px solid #1e3a5f;
    border-radius:6px;cursor:pointer}
#ghRestore:hover{background:#1e3a5f}
#ghRestore:disabled{opacity:.5;cursor:default}
#ghStatus{display:none;font-size:9px;color:#6b7280;margin:0 14px 8px;text-align:center}
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
  <div id="hdr"><b>アーカイブ一覧</b>1時間ごとの解析結果ログ</div>
  <a id="dlZip" href="/snapshots/download">全件をZIPでダウンロード（直近7日分）</a>
  <a id="dlZipAll" href="/snapshots/download?all=1">全期間をまとめてダウンロード</a>
  <button id="ghBackup" onclick="runGhBackup()">GitHubへ手動バックアップ</button>
  <button id="ghRestore" onclick="runGhRestore()">GitHubから復元</button>
  <div id="ghStatus"></div>
  <div id="items"><div id="loading">読込中...</div></div>
</div>
<div id="detail"><div class="empty" style="margin:auto">左のリストからアーカイブを選択してください</div></div>
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

fetch('/snapshots/backup_status').then(function(r){return r.json()}).then(function(d){
  if(!d.enabled) return;
  var btn = document.getElementById('ghBackup');
  var rbtn = document.getElementById('ghRestore');
  var st = document.getElementById('ghStatus');
  btn.style.display = 'block';
  rbtn.style.display = 'block';
  st.style.display = 'block';
  st.textContent = 'GitHub: ' + d.repo + ' (' + d.branch + ')';
}).catch(function(e){});

function runGhBackup(){
  var btn = document.getElementById('ghBackup');
  var st = document.getElementById('ghStatus');
  btn.disabled = true;
  btn.textContent = '送信開始中...';
  fetch('/snapshots/backup_now', {method:'POST'}).then(function(r){return r.json()}).then(function(d){
    btn.textContent = 'バックアップ開始（' + d.count + '件）';
    st.textContent += ' - バックグラウンドで実行中';
    setTimeout(function(){ btn.disabled = false; btn.textContent = 'GitHubへ手動バックアップ'; }, 4000);
  }).catch(function(e){
    btn.disabled = false;
    btn.textContent = '送信失敗';
  });
}

function runGhRestore(){
  var rbtn = document.getElementById('ghRestore');
  rbtn.disabled = true;
  rbtn.textContent = '復元開始中...';
  fetch('/snapshots/restore_now', {method:'POST'}).then(function(r){return r.json()}).then(function(d){
    rbtn.textContent = '復元中（少し待って再読込）';
    setTimeout(function(){ rbtn.disabled = false; rbtn.textContent = 'GitHubから復元'; }, 6000);
  }).catch(function(e){
    rbtn.disabled = false;
    rbtn.textContent = '復元失敗';
  });
}

fetch('/snapshots').then(function(r){return r.json()}).then(function(d){
  snapshots = d.snapshots || [];
  var wrap = document.getElementById('items');
  if(snapshots.length===0){
    wrap.innerHTML = '<div class="empty">まだアーカイブがありません<br>(起動後1時間ほどで作成されます)</div>';
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
  <title>地震研究統合プラットフォーム v6.4</title>
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
      <div>v6.4 / 研究用</div>
    </div>

    <div class="group-title">地震データ</div>
    <button class="tab-btn active" onclick="sw(0)">
      <span class="label">統合リスクマップ</span>
      <span class="badge">β</span>
    </button>
    <button class="tab-btn" onclick="sw(1)">
      <span class="label">地震履歴</span>
      <span class="badge">有感+無感</span>
    </button>
    <button class="tab-btn" onclick="sw(2)">
      <span class="label">ETASマップ</span>
      <span class="badge">P1</span>
    </button>
    <button class="tab-btn" onclick="sw(3)">
      <span class="label">b値マップ</span>
      <span class="badge">P4</span>
    </button>
    <button class="tab-btn" onclick="sw(4)">
      <span class="label">活断層・プレート境界</span>
      <span class="badge">地質</span>
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
      <span class="label">アーカイブ</span>
      <span class="badge">1h</span>
    </button>

    <div class="version">ETAS残差研究プロジェクト</div>
  </div>
  <div id="main">
    <iframe id="f0" class="active" src="/tab/riskmap"></iframe>
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
    var URLS=['riskmap','history','etas','bvalue','faultmap','tec','gnss','pressure','snapshots'];
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

            try:
                update_tec_cache()
            except Exception as e:
                print(f"[TEC] 更新中にエラー: {e}")

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
        risk_cells = compute_risk_grid(data["etas"], data["bvalue"], data["all"])
        html = render_riskmap(risk_cells, upd)
    elif name == "tec":      html = render_tec(upd)
    elif name == "gnss":     html = render_gnss(upd)
    elif name == "pressure": html = render_pressure(upd)
    elif name == "snapshots": html = render_snapshots(upd)
    else: return Response("Not found", status=404)
    return Response(html, mimetype="text/html")

@app.route("/data/risk_cell_trend")
def risk_cell_trend():
    """
    統合リスクマップで特定の格子セルをタップした際に、そのセルの総合リスク
    スコアの「前日比」と「推移（直近7日・時間解像度）」を返す。
    クエリパラメータ:
      lat, lon : セル中心の緯度経度（フロントエンドが持つCELLSの値をそのまま渡す）
      sel      : 現在選択中のデータソースをカンマ区切りで（例: "etas,bvalue,fault,plate"）
    """
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return {"error": "lat, lon must be numbers"}, 400

    gi = int(math.floor(lat / RISK_GRID_SIZE))
    gj = int(math.floor(lon / RISK_GRID_SIZE))
    if (gi, gj) not in _RISK_CELLS:
        return {"error": "cell out of range"}, 404

    selected_keys = _risk_component_keys_from_param(request.args.get("sel"))
    points, prev_day_score = compute_risk_trend_for_cell(gi, gj, selected_keys)
    return {"points": points, "prev_day_score": prev_day_score}

@app.route("/status")
def status():
    with _cache_lock:
        return {"phase":_ready_phase,"last_update":_last_update,
                "quakes":len(_cached_data["all"]) if _cached_data else 0}

@app.route("/gnss/status")
def gnss_status():
    """GNSS(GEONET SFTP)実データ取得の設定・稼働状況を返す（パスワード等は含めない）。"""
    with _gnss_lock:
        n_data = len(_gnss_cache["data"]) if _gnss_cache["data"] else 0
        ts = _gnss_cache["ts"]
        error = _gnss_cache["error"]
        updating = _gnss_cache["updating"]
    return {
        "paramiko_available": _PARAMIKO_AVAILABLE,
        "enabled": GSI_GNSS_ENABLED,
        "sftp_host": GSI_SFTP_HOST,
        "sftp_user_set": bool(GSI_SFTP_USER),
        "stations_configured": len(GNSS_STATIONS),
        "stations_with_data": n_data,
        "last_update": ts,
        "last_update_str": (datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d %H:%M JST") if ts else None),
        "updating": updating,
        "error": error,
        "lookback_days": GNSS_LOOKBACK_DAYS,
    }

@app.route("/gnss/refresh_now", methods=["POST"])
def gnss_refresh_now():
    """GNSSデータの再取得を手動でトリガーする（デバッグ・動作確認用）。"""
    if not GSI_GNSS_ENABLED:
        return Response("GSI_SFTP_USER / GSI_SFTP_PASS が未設定、またはparamiko未インストールです", status=400)
    with _gnss_lock:
        already = _gnss_cache["updating"]
    if already:
        return {"status": "already_updating"}
    threading.Thread(target=_refresh_gnss_cache, daemon=True).start()
    return {"status": "started"}

@app.route("/gnss/raw_sample")
def gnss_raw_sample():
    """
    指定した観測点コードの.posファイル生データ（先頭・末尾数十行）をそのまま返すデバッグ用エンドポイント。
    実際のファイルフォーマットを確認し、_parse_pos_file() の解析ロジックを検証・調整するために使う。
    例: /gnss/raw_sample?station=940025
    """
    if not GSI_GNSS_ENABLED:
        return Response("GSI_SFTP_USER / GSI_SFTP_PASS が未設定、またはparamiko未インストールです", status=400)
    code = (request.args.get("station") or "").strip()
    if not code:
        return Response("?station=<観測点コード> を指定してください（例: 940025）", status=400)
    year = request.args.get("year", str(datetime.now(timezone.utc).year)).strip()
    try:
        sftp, transport = _gsi_sftp_connect()
    except Exception as e:
        return Response(f"SFTP接続エラー: {e}", status=502)
    try:
        text, path = _fetch_station_pos_text(sftp, code, year)
    finally:
        try:
            sftp.close(); transport.close()
        except Exception:
            pass
    if text is None:
        return Response(f"ファイルが見つかりませんでした（station={code}, year={year}）。"
                         f"候補ディレクトリ: {', '.join(_GNSS_POS_DIR_CANDIDATES)}", status=404)
    lines = text.splitlines()
    preview = "\n".join(lines[:40] + (["...(中略)..."] if len(lines) > 80 else []) + (lines[-20:] if len(lines) > 80 else []))
    parsed = _parse_pos_file(text)
    return Response(
        f"path: {path}\ntotal_lines: {len(lines)}\nparsed_rows: {len(parsed)}\n\n--- preview ---\n{preview}",
        mimetype="text/plain")

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
        tmp_path = build_snapshots_zip(max_files=max_files)
    except Exception as e:
        print(f"[snapshots_download] ZIP作成失敗: {e}")
        return Response(f"ZIP作成中にエラーが発生しました: {e}", status=500)
    if tmp_path is None:
        return Response("アーカイブがまだありません", status=404)
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    suffix = "all" if want_all else "recent7d"
    # 実ファイルとして送るのでWerkzeugがConditional/Rangeリクエストを正しく処理でき、
    # 大きいZIPでもダウンロードが途中で失敗しない。送信完了後に一時ファイルを削除する。
    resp = send_file(tmp_path, mimetype="application/zip", as_attachment=True,
                      download_name=f"snapshots_{suffix}_{ts}.zip", conditional=True,
                      max_age=0)

    @resp.call_on_close
    def _cleanup_tmp_zip():
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return resp

@app.route("/snapshots/backup_status")
def snapshots_backup_status():
    """GitHub自動バックアップの設定状況を返す（トークン自体は返さない）。"""
    return {
        "enabled": GITHUB_BACKUP_ENABLED,
        "repo":   GITHUB_BACKUP_REPO if GITHUB_BACKUP_ENABLED else None,
        "branch": GITHUB_BACKUP_BRANCH if GITHUB_BACKUP_ENABLED else None,
        "path":   GITHUB_BACKUP_PATH if GITHUB_BACKUP_ENABLED else None,
        "quakes_path": GITHUB_BACKUP_QUAKES_PATH if GITHUB_BACKUP_ENABLED else None,
    }

@app.route("/quakes/backup_now", methods=["POST"])
def quakes_backup_now():
    """
    ローカルの data/quakes.csv を手動でGitHubへバックアップする。
    導入直後にこれまでの蓄積分を退避したい場合や、自動バックアップに
    失敗していた疑いがある場合の手動リトライ用。
    """
    if not GITHUB_BACKUP_ENABLED:
        return Response("GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN が未設定です", status=400)
    if not os.path.exists(DATA_FILE):
        return Response("data/quakes.csv がまだ存在しません", status=400)
    _github_backup_quakes_csv_async()
    return {"status": "started"}

@app.route("/quakes/restore_now", methods=["POST"])
def quakes_restore_now():
    """
    GitHub側にバックアップされた quakes.csv をローカルへ復元（マージ）する処理を
    手動で再実行する。通常は起動時に自動実行されるが、後からバックアップ設定を
    追加した場合や復元に失敗した疑いがある場合に手動で再試行できるようにしている。
    """
    if not GITHUB_BACKUP_ENABLED:
        return Response("GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN が未設定です", status=400)
    threading.Thread(target=github_restore_quakes_csv, daemon=True).start()
    return {"status": "started"}

@app.route("/quakes/manual_add_kumamoto_20260728", methods=["POST"])
def quakes_manual_add_kumamoto_20260728():
    """
    ★ 手動データ補完用（一時的なルート）:
    2026/07/28 16:27頃 熊本県熊本地方 M7.1 最大震度7（令和8年熊本地震・本震）は、
    その後の大規模な余震シーケンスによりAPIのページング上限を突破してしまい、
    通常の自動取得では再取得できなくなっている。気象庁発表の確定値を手動で
    1件だけCSVに追記するための使い切りエンドポイント。
    save_quakes() 経由で追加するため、(time, lat, lon) が完全一致する行が
    既に存在する場合は追加されず、複数回叩いても安全（冪等）。

    ★ Bug fix (2026-08): save_quakes() 内のGitHubバックアップは非同期(別スレッド)
    で動くため、このエンドポイントがレスポンスを返した直後にRenderが再起動
    （スリープ/再デプロイ）すると、GitHubへの書き込みが完了する前にプロセスごと
    終了してしまい、せっかく追加したデータがバックアップされないまま消える
    ことがあった。手動追加は頻繁に叩くものではないため、ここでは非同期にせず
    同期的にバックアップを実行し、成功したかどうかをレスポンスに含めることで
    「本当に保存されたか」をその場で確認できるようにする。
    """
    quake = {
        "time":  "2026-07-28T07:27:00+00:00",  # JST 16:27 → UTC 07:27
        "lat":   32.6,
        "lon":   130.7,
        "mag":   7.1,
        "depth": 16.0,
        "source": "jma_bosai",
        "place":  "熊本県熊本地方",
        "max_int": "7",
    }
    before = load_quakes()
    save_quakes([quake])  # 内部で追加時に非同期バックアップも走るが、下で同期バックアップを重ねて確実化する
    after = load_quakes()
    added = len(after) - len(before)

    backup_success = None
    if GITHUB_BACKUP_ENABLED:
        backup_success = github_backup_quakes_csv()  # ここで完了を待つ

    # ★ Bug fix (2026-08): CSVには追記されるが、地震履歴タブが参照している
    # メモリ上のキャッシュ(_cached_data)はバックグラウンド更新ループ
    # (最大 FETCH_INTERVAL_SEC=600秒間隔) でしか作り直されないため、
    # 手動追加した直後は画面に反映されず「追加したのに見えない」状態になっていた。
    # ここでキャッシュも即座に作り直して、追加後すぐ画面に反映されるようにする。
    global _cached_data, _last_update, _ready_phase
    try:
        quakes_now = load_quakes()
        grid_scores = analyze_etas(quakes_now)
        bvalue_grid = compute_bvalue_grid(quakes_now)
        updated_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST") + "(手動追加反映)"
        with _cache_lock:
            _cached_data = {"all": quakes_now, "etas": grid_scores, "bvalue": bvalue_grid,
                             "updated": updated_str}
            _last_update = time.time(); _ready_phase = 2
        cache_refreshed = True
    except Exception as e:
        print(f"[手動追加] キャッシュ再構築失敗: {e}")
        cache_refreshed = False

    return {
        "status": "done",
        "added": added,
        "quake": quake,
        "github_backup_enabled": GITHUB_BACKUP_ENABLED,
        "github_backup_success": backup_success,
        "cache_refreshed": cache_refreshed,
    }


def snapshots_backup_now():
    """
    ローカルに保存済みの全スナップショットをGitHubへまとめてバックアップする。
    毎時の自動バックアップに失敗していた分の取りこぼしを手動で解消したい場合や、
    初回導入時に既存の蓄積分をまとめて退避したい場合に使う。
    件数が多いとGitHub APIを連続で叩くことになるため、レート制限に配慮して
    バックグラウンドスレッドで1件ずつ間隔を空けながら実行する。
    """
    if not GITHUB_BACKUP_ENABLED:
        return Response("GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN が未設定です", status=400)
    files = list_snapshots()

    def _run_backup_all():
        ok, ng = 0, 0
        for fname in files:
            key = fname[:-5]
            success = github_backup_file(
                os.path.join(SNAPSHOT_DIR, fname),
                f"{GITHUB_BACKUP_PATH}/{fname}",
                f"backup: {key}",
            )
            ok += 1 if success else 0
            ng += 0 if success else 1
            time.sleep(1)  # GitHub APIのレート制限に配慮
        print(f"[GitHub backup] 一括バックアップ完了 成功:{ok} 失敗:{ng}")

    threading.Thread(target=_run_backup_all, daemon=True).start()
    return {"status": "started", "count": len(files)}

@app.route("/snapshots/restore_now", methods=["POST"])
def snapshots_restore_now():
    """
    GitHub側のスナップショットをローカルへ復元する処理を手動で再実行する。
    通常は起動時に自動実行されるが、バックアップ設定を後から追加した場合や
    復元に失敗した疑いがある場合に手動で再試行できるようにしている。
    """
    if not GITHUB_BACKUP_ENABLED:
        return Response("GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN が未設定です", status=400)
    threading.Thread(target=github_restore_snapshots, daemon=True).start()
    return {"status": "started"}

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
    # ★ Bug fix (2026-08): quakes.csv の復元は、バックグラウンド更新ループが
    # 1回目に load_quakes() でキャッシュを温める前に完了している必要があるため、
    # スナップショット復元（非同期）とは別に、ここで同期的に実行する。
    _github_restore_quakes_csv_sync()
    _github_restore_async()
    threading.Thread(target=_update_data, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
