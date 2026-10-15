# -*- coding: utf-8 -*-
"""
地震リスクマップ v9.21

■ 目的と成功の定義
  統合リスクマップの目的は、地震の「発生時期の予測」ではなく、「M6以上の大きな地震が起きる場所を、
  高リスクのセルに集められているか」。評価は「M6以上の何%が、面積の何%の高リスクセルに入ったか」
  (Molchan図／面積あたりの的中率)で行う。評価に使うのは発生“直前”の値（直後は本震・余震で必ず上がる）。
  (v9.08) 検証タブ(バックテスト)で、M6以上の直前・直後のリスク値を自動で記録・集計できるようになった
  （従来は人力でアーカイブタブを使って調べていた）。
  ※ Lv・スコアは発生確率ではない。表示にも「確率ではない」旨を残す。

■ 現在の統合リスクマップの仕様
  ・成分: ETAS / b値 / 活断層・プレート応力 … 過去65日の「そのセル自身の実績」との絶対評価。
          気圧 … 上限つき。GNSS変位 … 現在の水平変位(直近7日,mm)のみ・固定目盛り(GNSS_SCORE_POINTS)。過去値は使わない。
  ・重み(RISK_DEFAULT_WEIGHTS): ETAS 0.45 / b値 0.15 / 活断層 0.15 / プレート 0.10 / 気圧 0.05 / TEC 0.08 / GNSS 0.20
          TEC・GNSSは「押し上げ専用」(他成分の水準を超えた分だけ総合を上げ、平常範囲なら総合を変えない)
          （選択成分・そのセルにある成分だけで再正規化。現在の重みは判断値で、実データでの検証は未実施）
  ・GNSSは「他の成分の水準を超えた分だけ総合を押し上げ、平常範囲なら総合を変えない」。観測点から遠いセルはGNSSなし。
  ・レベル境界は、平穏時のLv別セル数(RISK_LEVEL_TARGETS)に合うよう過去65日から自動算出（GNSSは較正に含めない）。
  ・標準プリセット: ETAS+b値+活断層+プレート+GNSS（GNSSが使えない間は自動で外れる）。
  ・アーカイブ: GNSS変位は未実装。基準(過去65日)は既定では「現在」で作ったものを過去日付にも使う（既知の限界）。
    (v9.08) 「この日付で基準を作り直す」(calib=date)なら、基準日より前の65日だけで基準を作る（一元化カタログの範囲）。

■ 今後の方針（優先順。詳細な案はチャットで検討済み）
  1. 検証の自動化(最優先) ※v9.08で実装済み。重み・成分の取捨は、この検証タブの結果を見て決める: 気象庁一元化カタログ(data/jma_catalog/)のM6以上の一覧に対し、発生1日前などの時点で
     アーカイブと同じ計算を回し、震源セルのレベル・順位(上位何%)・周囲の最大レベル・高リスク面積比を記録し、
     Molchan図で集計。直前に近くで大きな地震があった例(余震)は分けて評価。比較対象は静的な長期ハザードマップ
     (過去の地震分布・活断層のみ)で、動的更新が静的に勝てるかを見る。重み・成分の取捨はこの結果で決める。
  2. 較正の安定化: 過去65日の基準は直近の大地震列で歪む。日付ごとに基準を作り直せるようにし(古い日付の評価の前提)、
     異常期の除外・近隣セルとの情報共有・数年スケールの併用を検討。
     ※「日付ごとの基準の作り直し」は v9.08 で実装済み（_compute_historical_calibration(end_utc, quakes, step_hours)）。残りは未着手。
  3. 大地震向けの成分: 長期評価(地震調査研究推進本部)・プレート固着・活断層を背景リスクに追加。規模別スコア(M5+/M6+/M7+)。
  4. 余震と異常の分離、空間平滑(カーネル)、データが薄いセルの不確かさ表示。
  5. GNSS: 定常速度の差し引き、広域ノイズ除去、複数点の整合、観測点の増加。検証用に、GNSS込みのリスクマップを
     毎日保存して将来の前向き検証に使う（アーカイブへのGNSS実装は履歴取得が必要で、現状は見送り）。
  6. 運用: 計算結果の永続化(再起動で較正を再計算しない)、データ鮮度の表示と障害検知。
  7. 使いやすさ: 自分の地点の表示、前日比で変わったセルだけ表示、Lv別セル数の推移、「なぜ高いか」の自動説明、
     共有URL/CSV出力、PWA化。通知は「時期の警報」と誤解されないよう、注目セルの変化として位置づける。

■ 更新履歴（要点のみ）
  v9.21 TECを統合リスクマップに接続(重み0.08・GNSSと同じ「他成分を超えた分だけ押し上げ」・広域成分を除いた局所偏差を固定目盛りでスコア化・標準/全データプリセットに追加)／
        バックテストに成分別の単独成績・AUCと的中率の95%信頼区間・年別/規模別の安定性を追加(記録形式変更のため旧記録は再計算)
  v9.20 app_35(v9.10: TEC実装更新)と app_38(v9.08: 検証タブ・日付ごとの基準)を統合。両者の変更は互いに独立で、競合なし
  v9.10 TEC取得をGNSSと同じ方式に統一(有効判定TEC_ENABLED／更新中フラグ付きの裏更新／古ければアクセス時に再取得／
        TEC_JSON_URLによる数値JSON代替経路／/tec/status・/tec/refresh_now・/tec/raw_image追加／3時間超の古いTECはリスクに使わない／
        NICT画像の公開遅れ(数時間)に合わせ遡りを6時間に拡大・TEC_URL_TEMPLATE環境変数で形式変更可)
  v9.08 検証タブ(M6以上バックテスト・Molchan図・静的ハザードとの比較・余震の分離)／日付ごとの基準の作り直し
        (_compute_historical_calibration に end_utc/quakes/step_hours を追加、アーカイブに calib=date)
  v9.07 標準プリセットにGNSS追加／重み見直し／GNSSは「超えた分だけ押し上げ」／アーカイブの重みをサーバーと一致
  v9.06 GNSSを現在値のみ(履歴・基準なし)に変更。アーカイブのGNSSは未実装に
  v9.05 リスクマップの再計算を高速化(計算結果は同じ)
  v9.04 GNSSタブの矢印・ラベルを見やすく刷新
  v9.01〜9.02 GNSS観測点IDの地点名表示を強化(表記ゆれ対応・名称ファイル gnss_station_names.json の探索場所を拡大)
  v9.00 GNSS変位を統合リスクマップ/アーカイブに追加(v9.06で現在値のみに変更)
  v8.22 アーカイブの60日より古い期間に気象庁一元化カタログ(data/jma_catalog/)を追加（無ければUSGS）
  v8.20〜8.21 レベル境界を過去65日から自動較正／相対順位を廃止し自セルの実績との絶対評価のみに

タブ構成:
  1. 地震履歴     - 有感・無感統合 (JMA / P2P / USGS / Hi-net)
  2. ETASマップ   - 地震発生確率 + ETAS残差（研究用）
  3. b値マップ    - グリッドごとのGutenberg-Richter b値
  4. 活断層・プレート境界 - 都市圏活断層図(GSI) + プレート境界(PB2002)
  5. 統合リスクマップ(β) - ETAS/b値/活断層/プレート境界/気圧/GNSSを統合した相対リスク指数
  6. TEC          - 電離圏全電子数 (NICT SCIDAS リンク)
  7. GNSS         - 地殻変動 (GEONET SFTP実データ変位ベクトル、未設定時はプレースホルダー)
  8. 海面気圧     - アメダス海面気圧マップ
  10. 検証       - M6以上のバックテスト(Molchan図)。静的ハザードとの比較、余震は分けて評価。CSV出力あり
  9. アーカイブ   - 日時指定で、その時点から過去1か月分の地震データで統合リスクマップを再計算（GNSSは未実装）
                  （直近60日以内はJMA/P2P統合データ、それより古い日時は気象庁一元化震源カタログ
                    (data/jma_catalog/ のファイル)、無ければUSGS過去カタログを都度取得）

コード内目次（"# ══" 区切りの主要セクション。おおよその行番号）:
   280  ETASパラメータ (Ogata 1998)
   337  データ取得（JMA/P2P/USGS・重複排除・CSV保存）
  1029  スナップショット（解析結果の時系列ログ）
  1226  quakes.csv のGitHubバックアップ/復元
  1393  ETAS解析
  1505  絶対基準キャリブレーション／レベル境界の自動算出
  1986  b値解析 (Gutenberg-Richter)
  2026  ユーティリティ
  2076  TAB1 統合地震履歴
  2261  TAB2 ETASマップ
  2413  TAB3 b値マップ
  2524  TAB4 TEC（電離圏全電子数）
  2893  TAB5 GNSS（地殻変動）
  3632  TAB6 海面気圧（アメダス）
  3817  活断層・プレート境界マップ
  3999  統合リスクマップ（β）
  4526  アーカイブ: 過去時点の統合リスクマップ再計算
  5219  メインページ（タブシェル）
  5222  TAB9 アーカイブUI
  5822  バックグラウンド更新
  5974  Flaskルーティング
  6457  手動再計算（各タブの「再計算」ボタン）
"""

from flask import Flask, Response, send_file, request
import requests, csv, os, math, re, json, threading, time, zipfile, io, bisect, tempfile, base64, gzip
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

# TEC画像(NICT)のデコードに使用。未インストールでもアプリ全体は動作する
# （その場合はTEC画像取得が自動的に無効化される。requirements.txtに Pillow の追加が必要）。
try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PILImage = None
    _PIL_AVAILABLE = False

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
# Renderから直接SFTPできない場合の代替: 別環境(PC/GitHub Actions)で作った変位JSONのURLを指定
#   例: https://raw.githubusercontent.com/<owner>/<repo>/main/gnss_vectors.json
GNSS_JSON_URL    = os.environ.get("GNSS_JSON_URL", "").strip()
GSI_GNSS_ENABLED = bool(GNSS_JSON_URL or (_PARAMIKO_AVAILABLE and GSI_SFTP_USER and GSI_SFTP_PASS))

GNSS_LOOKBACK_DAYS = 7          # 変位ベクトル計算に使う直近日数（短期変位）
GNSS_CACHE_SEC     = 6 * 3600   # 座標値は日次更新なので数時間キャッシュで十分
GNSS_SFTP_TIMEOUT  = 20

# (v9.00) GNSS変位を統合リスクマップ/アーカイブの成分にするための設定
GNSS_CELL_RADIUS_KM      = 60.0  # セル中心からこの距離以内で最も近い観測点の値をそのセルに割り当てる
GNSS_NOISE_MM            = 3.0   # 7日トレンドの典型的なノイズ水準(mm)
# (v9.06) 現在の水平変位(mm/7日) → スコア(0〜1)の固定目盛り。GNSSタブの色分け(ノイズ≤3/中>7/大>15)に合わせた。
# 過去実績は使わない。暫定値で、運用しながら調整する前提。大きな変位でもGNSS単独でLv5にならないよう上限は0.95。
GNSS_SCORE_POINTS        = [(0.0, 0.10), (3.0, 0.30), (7.0, 0.55), (15.0, 0.80), (30.0, 0.95)]
GNSS_LIVE_MAX_STALE_DAYS = 30    # 現在値として使う座標値の最大経過日数(これより古い観測点は除外)

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
    ("950499", "与那国",   24.4667, 123.0100),
    ("950500", "西表島",   24.3711, 123.7828),
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
    ("950267", "長野",     36.6513, 138.1810),
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

# ── Hi-net（防災科研）微小地震データ ───────────────────────
# JMAの公開情報（発表対象は基本的に有感地震、または一定規模以上）には出てこない
# 無感の微小地震を含む震源リストを、Hi-net自動処理震源リスト（速報値、前日・当日分、
# 3時間毎更新）から取得する。ユーザ登録（無料）とログインが必要。
# 以下の環境変数を設定すると有効になる（未設定なら完全に無効＝他機能に影響なし）。
#   HINET_USER / HINET_PASS : NIED地震観測網の登録ユーザ名・パスワード
# ★ 実装メモ（2026-09）: サイトはEUC-JPエンコードで、Cookieセッション認証。
# ログインはトップページ取得→POST(LANG/auth_un/auth_pw)の順で行う必要がある
# （事前のGETでセッションCookieを受け取ってからでないとログインが通らない）。
# データはJSON等ではなく固定カラムのテキスト（空白区切り、10トークン/行）で、
# 気象庁発表のような場所名(place)・震度(max_int)は含まれない。
HINET_USER = os.environ.get("HINET_USER", "").strip()
HINET_PASS = os.environ.get("HINET_PASS", "").strip()
HINET_ENABLED = bool(HINET_USER and HINET_PASS)

HINET_LOGIN_URL    = "https://hinetwww11.bosai.go.jp/auth/?LANG=ja"
HINET_HYPOLIST_URL = "https://hinetwww11.bosai.go.jp/auth/hinet_hypolist/?LANG=ja"
HINET_HEADERS      = {"User-Agent": "Mozilla/5.0 (SeismoApp/5.0)"}

_hinet_session      = None
_hinet_session_lock = threading.Lock()
_hinet_status       = {"last_success": 0.0, "last_error": None, "last_count": 0}

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

# ETAS・統合リスクマップ共通のレベル別配色（気象庁の警戒レベル風に統一）。
# Lv1=水色 / Lv2=黄色 / Lv3=赤 / Lv4=紫 / Lv5=黒。マップ上では半透明で描画する。
LEVEL_COLOR = {5:"#0c000c", 4:"#8000ff", 3:"#ff0000", 2:"#ffe600", 1:"#66ccff"}
# ↑ Lv5は内閣府(防災担当)が定める「警戒レベル配色ガイドライン」のレベル5(黒)に準拠。
#   公式RGB値は(12,0,12)＝#0c000c で、完全な黒(#000000)だと死を連想させるため
#   わずかに色味を残した"ほぼ黒"。ただしこのままではダーク地図に同化してしまうため、
#   Lv5セルのみ枠線を太く・不透明度を上げて視認性を確保している(下記redraw参照)。(v7.44)
LEVEL_FILL_OPACITY = 0.55  # マップの地形が透けて見えるよう半透明にする
ETAS_COLOR = LEVEL_COLOR

# JMA震度階級コード → 表示用震度文字列（P2Pの maxScale フィールド用）。
# 元は _parse_p2p_item() と fetch_quakes_p2p_jma() に同じ辞書が別々に
# 定義されていたため、ここに1つにまとめて両方から参照する。
JMA_SCALE_MAP = {10:"1", 20:"2", 30:"3", 40:"4", 45:"5-", 50:"5+", 55:"6-", 60:"6+", 70:"7"}

def jma_max_int(max_scale):
    """P2Pの maxScale 値（-1=無感）を表示用の震度文字列に変換する。"""
    if max_scale == -1:
        return ""
    return JMA_SCALE_MAP.get(int(max_scale), str(max_scale))

def jst_str_to_utc_iso(raw_time, fallback=None):
    """"YYYY/MM/DD HH:MM[:SS]" 形式のJST時刻文字列をUTCのISO8601文字列に変換する。
    パースできない場合は fallback（省略時は raw_time そのもの）を返す。
    元は fetch_quakes_p2p / fetch_quakes_p2p_jma の中に同じフォーマット試行
    ループが重複していたため、共通関数として1箇所にまとめた。"""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt_jst = datetime.strptime(raw_time, fmt)
            return dt_jst.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return raw_time if fallback is None else fallback

LEAFLET_CDN = """
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>"""

# ★ 2026-09: CARTOのdark_allタイルは無料APIキーでも月間クォータ超過等で
# 「API KEY REQUIRED」の透かしタイルが再発するため、APIキー管理が不要な
# Esri「World_Dark_Gray_Base」（キャンバス系ベースマップ、最初からダーク配色）に切り替え。
DARK_TILE = "L.tileLayer('https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri, HERE, Garmin, &copy; OpenStreetMap contributors',maxZoom:16}).addTo(map);"

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
        time_str = jst_str_to_utc_iso(eq.get("time", ""))
        max_int = jma_max_int(eq.get("maxScale", -1))  # maxScale: -1 = 無感
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
                    time_str = jst_str_to_utc_iso(eq.get("time", ""))

                    try:
                        t = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                        if t < cutoff:
                            stop_early = True
                            break
                    except Exception:
                        pass

                    max_int = jma_max_int(eq.get("maxScale", -1))

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

def fetch_quakes_usgs_range(start_utc, end_utc, timeout=30):
    """
    指定したUTC期間 [start_utc, end_utc] の地震をUSGSの過去カタログ(FDSNWS)から取得する。
    USGSのイベント検索APIは starttime/endtime に任意の日時（数十年前でも可）を指定できるため、
    quakes.csv がローカルに保持していない古い期間（アーカイブタブで65日より前を指定した場合）
    のデータ取得に使う。fetch_quakes_usgs()（直近90日固定）はこの関数のラッパーとして実装する。

    ★ 注意点（呼び出し側・UIでも案内していること）:
      - USGSカタログは全世界対象で、日本国内の小規模地震についてはJMAほど網羅的ではない
        （検知下限マグニチュードがJMAより大きい）。そのため古い期間のb値・ETAS計算は
        直近65日分（JMA/P2P統合データ使用）と比べて精度が落ちる可能性がある。
      - 震度情報(max_int)はUSGSデータには含まれないため空文字となる。
      - limit=20000（USGS FDSNWSの上限）に達した場合は結果が打ち切られている可能性がある
        （大規模な余震シーケンスを含む期間を指定した場合など）。
    """
    lat_min, lat_max, lon_min, lon_max = ETAS_FETCH_BBOX
    url = (f"https://earthquake.usgs.gov/fdsnws/event/1/query"
           f"?format=geojson&starttime={start_utc.strftime('%Y-%m-%dT%H:%M:%S')}"
           f"&endtime={end_utc.strftime('%Y-%m-%dT%H:%M:%S')}"
           f"&minlatitude={lat_min}&maxlatitude={lat_max}"
           f"&minlongitude={lon_min}&maxlongitude={lon_max}&minmagnitude=0.0&orderby=time&limit=20000")
    data = requests.get(url, timeout=timeout).json()
    quakes = []
    for feat in data.get("features", []):
        try:
            props = feat["properties"]; coords = feat["geometry"]["coordinates"]
            lat, lon = float(coords[1]), float(coords[0])
            if not in_etas_region(lat, lon): continue
            t = datetime.fromtimestamp(props["time"]/1000, tz=timezone.utc)
            quakes.append({"time":t.isoformat(),"lat":lat,"lon":lon,
                           "mag":float(props["mag"]),"depth":float(coords[2]),"source":"usgs",
                           "place":props.get("place",""),"max_int":""})
        except Exception: continue
    if len(data.get("features", [])) >= 20000:
        print(f"[USGS/range] 警告: limit(20000)に達しました。結果が打ち切られている可能性があります "
              f"({start_utc.date()}〜{end_utc.date()})")
    print(f"[USGS/range] {start_utc.date()}〜{end_utc.date()} {len(quakes)}件")
    return quakes

# ── 気象庁一元化震源カタログ（地震月報(カタログ編)の年別ファイル）────────────
# アーカイブタブで「60日より古い期間」を指定したときに、USGSより先に使うソース。
# 気象庁が配布している年別の固定長テキスト（96桁/行。h2023 など。zipで配布）を
#     data/jma_catalog/h2023   （h2023.txt / h2023.zip のままでも可）
# に置くと自動で読み込む。ファイルが無い年や、ファイルに収録済みの期間を超える期間を
# 指定した場合は None を返し、呼び出し側がUSGSへフォールバックする。
#
# 桁位置（1始まり。実ファイル h2023 で検証済み）:
#     1      レコード種別  J=日本付近（使用） / U=海外（読み飛ばす）
#     2-13   発震時 年(4)月日時分 ※JST
#     14-17  秒(0.01秒単位)
#     22-24  緯度(度)   25-28 緯度(分, 0.01分単位)
#     33-36  経度(度)   37-40 経度(分, 0.01分単位)
#     45-49  深さ: 末尾2桁が空白なら整数km（固定深さ等）、そうでなければ0.01km単位
#     53-54  M1  ※空白=マグニチュード未決定。負値は "-6"(=-0.6) / "A0"(=-1.0) 形式
#     62     最大震度  1-4 / A=5弱 B=5強 C=6弱 D=6強 / 7
#     69-92  震央地名（英語表記）
JMA_CATALOG_DIR = os.environ.get("JMA_CATALOG_DIR", "data/jma_catalog")
try:
    # 下限マグニチュード。カタログにはM<1の微小地震が年間10万件以上含まれ、ETAS計算
    # (イベント数に比例して重くなる)やb値の前提(通常データはM2前後以上が中心)と
    # 合わなくなるため、既定ではM2.0以上だけを使う。環境変数で調整可能。
    JMA_CATALOG_MIN_MAG = float(os.environ.get("JMA_CATALOG_MIN_MAG", "2.0"))
except ValueError:
    JMA_CATALOG_MIN_MAG = 2.0
# ファイル先頭・末尾の記録と指定期間の端がこの時間以内のズレなら「覆っている」とみなす
_JMA_CATALOG_EDGE_TOL = timedelta(hours=3)
_JMA_CATALOG_MAXI = {"1": "1", "2": "2", "3": "3", "4": "4",
                     "A": "5-", "B": "5+", "C": "6-", "D": "6+", "7": "7"}
_jma_catalog_span_cache = {}   # path -> ((mtime, size), first_utc, last_utc)

def _jma_catalog_find(year):
    """JMA_CATALOG_DIR 内の年別ファイルのパスを返す（展開済み優先、無ければzip）。無ければ None。"""
    base = os.path.join(JMA_CATALOG_DIR, f"h{year}")
    for path in (base, base + ".txt", base + ".zip"):
        if os.path.isfile(path):
            return path
    return None

def _jma_catalog_iter_lines(path):
    """年別ファイルを1行ずつ読む（zipの場合は中の最初のファイルを展開せずに読む）。"""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return
            with zf.open(names[0]) as raw:
                for line in io.TextIOWrapper(raw, encoding="ascii", errors="replace"):
                    yield line.rstrip("\r\n")
    else:
        with open(path, encoding="ascii", errors="replace") as f:
            for line in f:
                yield line.rstrip("\r\n")

def _jma_catalog_time(line):
    """発震時(JST)をUTCのdatetimeにして返す。"""
    t_jst = datetime(int(line[1:5]), int(line[5:7]), int(line[7:9]),
                     int(line[9:11]), int(line[11:13]), tzinfo=JST) \
            + timedelta(seconds=int(line[13:17]) / 100.0)
    return t_jst.astimezone(timezone.utc)

def _jma_catalog_mag(line):
    """M1を数値にして返す。未決定(空白)は None。"""
    tok = line[52:54]
    if not tok.strip():
        return None
    c = tok[0]
    if c == "-":
        return -int(tok[1]) / 10.0
    if c in "ABC" and tok[1].isdigit():          # A0=-1.0, B0=-2.0, C0=-3.0 ...
        return -((ord(c) - 64) + int(tok[1]) / 10.0)
    return int(tok) / 10.0

def _parse_jma_catalog_line(line, min_mag=None):
    """カタログ1行 -> (UTC datetime, 地震dict)。使えない行（海外・M未決定・M下限未満・
    桁崩れ）は None。地震dictの形は quakes.csv / fetch_quakes_usgs_range と同じ。"""
    if len(line) < 62 or line[0] != "J":
        return None
    try:
        mag = _jma_catalog_mag(line)
        if mag is None or (min_mag is not None and mag < min_mag):
            return None
        lat = int(line[21:24]) + int(line[24:28]) / 6000.0
        lon = int(line[32:36]) + int(line[36:40]) / 6000.0
        dep = line[44:49]
        depth = float(int(dep[:3])) if dep[3:5] == "  " else int(dep) / 100.0
        t = _jma_catalog_time(line)
    except (ValueError, IndexError):
        return None
    return t, {"time": t.isoformat(), "lat": lat, "lon": lon, "mag": mag, "depth": depth,
               "source": "jma_catalog", "place": line[68:92].strip(),
               "max_int": _JMA_CATALOG_MAXI.get(line[61], "")}

def _jma_catalog_span(path):
    """年別ファイルに収録されている最初・最後の記録時刻(UTC)を返す。ファイルの更新が
    無い限り結果をキャッシュする。"""
    st = os.stat(path)
    key = (st.st_mtime, st.st_size)
    hit = _jma_catalog_span_cache.get(path)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    first_line = last_line = None
    for line in _jma_catalog_iter_lines(path):
        if line[:1] == "J" and len(line) >= 17:
            if first_line is None:
                first_line = line
            last_line = line
    first = _jma_catalog_time(first_line) if first_line else None
    last = _jma_catalog_time(last_line) if last_line else None
    _jma_catalog_span_cache[path] = (key, first, last)
    return first, last

def fetch_quakes_jma_catalog_range(start_utc, end_utc, min_mag=None):
    """指定したUTC期間 [start_utc, end_utc] の地震を、気象庁一元化震源カタログの年別ファイル
    (JMA_CATALOG_DIR) から読み込む。

    戻り値:
      list : 読み込めた地震（該当期間に0件ならば空リスト）
      None : カタログが期間を覆っていない（該当年のファイルが無い／ファイルの収録期間が
             指定期間より短い）。呼び出し側はUSGSなど別ソースへフォールバックすること。
    ★ place は英語表記の震央地名。
    ★ 震度は最大震度のみ（観測点別は含まれない）。
    """
    min_mag = JMA_CATALOG_MIN_MAG if min_mag is None else min_mag
    s_jst = start_utc.astimezone(JST)
    e_jst = end_utc.astimezone(JST)
    paths = []
    for year in range(s_jst.year, e_jst.year + 1):
        path = _jma_catalog_find(year)
        if path is None:
            return None
        paths.append(path)
    first = _jma_catalog_span(paths[0])[0]
    last = _jma_catalog_span(paths[-1])[1]
    if first is None or last is None:
        return None
    if start_utc < first - _JMA_CATALOG_EDGE_TOL or end_utc > last + _JMA_CATALOG_EDGE_TOL:
        return None
    # 行頭の「年月日時分(JST)」を文字列のまま比較して、対象外の行を安く読み飛ばす
    lo = (s_jst - timedelta(minutes=1)).strftime("%Y%m%d%H%M")
    hi = (e_jst + timedelta(minutes=1)).strftime("%Y%m%d%H%M")
    found = []
    for path in paths:
        for line in _jma_catalog_iter_lines(path):
            if line[:1] != "J" or not (lo <= line[1:13] <= hi):
                continue
            parsed = _parse_jma_catalog_line(line, min_mag)
            if parsed is None:
                continue
            t, q = parsed
            if t < start_utc or t > end_utc or not in_etas_region(q["lat"], q["lon"]):
                continue
            found.append(q)
    found.sort(key=lambda q: q["time"])
    print(f"[JMAカタログ] {start_utc.date()}〜{end_utc.date()} {len(found)}件 (M>={min_mag})")
    return found

def fetch_quakes_usgs():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=90)
    try:
        return fetch_quakes_usgs_range(start, now, timeout=12)
    except Exception as e:
        print(f"[USGS] {e}"); return []

def _hinet_login():
    """
    Hi-netにログインし、認証済みセッションを返す。失敗時はNoneを返す。
    ログインフォーム表示時に発行される一時Cookieが必要なため、
    POST前に一度トップページをGETしてセッションを開始する。
    """
    session = requests.Session()
    try:
        session.get(HINET_LOGIN_URL, headers=HINET_HEADERS, timeout=15)
        resp = session.post(
            HINET_LOGIN_URL,
            data={"LANG": "ja", "auth_un": HINET_USER, "auth_pw": HINET_PASS},
            headers={**HINET_HEADERS, "Referer": HINET_LOGIN_URL},
            timeout=15,
        )
        resp.encoding = "euc-jp"
        # ログインフォームの画像がまだ含まれている＝ログイン画面に戻された＝失敗
        if "auth_login.png" in resp.text:
            print("[Hi-net] ログイン失敗（認証情報を確認してください）")
            return None
        return session
    except Exception as e:
        print(f"[Hi-net] ログインエラー: {e}")
        return None

def _get_hinet_session(force_relogin=False):
    """キャッシュ済みのHi-netセッションを返す。無い場合（または強制時）はログインし直す。"""
    global _hinet_session
    with _hinet_session_lock:
        if _hinet_session is None or force_relogin:
            _hinet_session = _hinet_login()
        return _hinet_session

_HINET_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s")

def _parse_hinet_hypolist(text):
    """
    Hi-net自動処理震源リストのテキストをパースする。
    フォーマット（空白区切り、10トークン/行、JST時刻）:
      YYYY-MM-DD HH:MM:SS.sss  OTerr  Lat  Yerr  Long  Xerr  Dep  Derr  Mag
    JMA公開情報のような場所名・震度は含まれないため place="" / max_int="" とする。
    """
    quakes = []
    for line in text.splitlines():
        line = line.strip()
        if not _HINET_LINE_RE.match(line):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        date_str, time_str = parts[0], parts[1]
        try:
            ot_err, lat, yerr, lon, xerr, dep, derr, mag = (float(x) for x in parts[2:10])
        except ValueError:
            continue
        try:
            dt_jst = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S.%f")
            dt_jst = dt_jst.replace(tzinfo=timezone(timedelta(hours=9)))
            time_iso = dt_jst.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
        quakes.append({
            "time": time_iso, "lat": lat, "lon": lon, "mag": mag, "depth": dep,
            "source": "hinet", "place": "", "max_int": "",
        })
    return quakes

def fetch_quakes_hinet():
    """
    Hi-net自動処理震源リスト（前日・当日分、速報値）から、JMAの公開情報には
    出てこない無感の微小地震を含む震源データを取得する。
    HINET_USER/HINET_PASS 未設定時は完全に無効（[]を返すだけで他機能に影響なし）。
    セッション切れ（ログイン画面に戻される）を検知した場合は1回だけ再ログインして
    リトライする。
    """
    if not HINET_ENABLED:
        return []
    session = _get_hinet_session()
    if session is None:
        _hinet_status["last_error"] = "ログイン失敗"
        return []
    try:
        resp = session.get(HINET_HYPOLIST_URL, headers=HINET_HEADERS, timeout=20)
        resp.encoding = "euc-jp"
        if "auth_login.png" in resp.text:
            session = _get_hinet_session(force_relogin=True)
            if session is None:
                _hinet_status["last_error"] = "再ログイン失敗"
                return []
            resp = session.get(HINET_HYPOLIST_URL, headers=HINET_HEADERS, timeout=20)
            resp.encoding = "euc-jp"
        quakes = _parse_hinet_hypolist(resp.text)
        _hinet_status["last_success"] = time.time()
        _hinet_status["last_error"] = None
        _hinet_status["last_count"] = len(quakes)
        print(f"[Hi-net] {len(quakes)}件")
        return quakes
    except Exception as e:
        print(f"[Hi-net] {e}")
        _hinet_status["last_error"] = str(e)
        return []

def fetch_all_quakes():
    results = {}
    def _run(name, fn):
        try: results[name] = fn()
        except Exception as e: print(f"[fetch_all] {name} {e}"); results[name] = []
    threads = [threading.Thread(target=_run, args=(n,f), daemon=True) for n,f in
               [("p2p",     fetch_quakes_p2p),
                ("p2p_jma", fetch_quakes_p2p_jma),
                ("usgs",    fetch_quakes_usgs),
                ("jma",     fetch_quakes_jma_bosai),
                ("hinet",   fetch_quakes_hinet)]]
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
    for name in ("p2p", "p2p_jma", "usgs", "jma", "hinet"):
        if name not in results:
            print(f"[fetch_all] {name} タイムアウトで未完了のためスキップ")
    all_q = (results.get("jma",[]) + results.get("p2p",[]) +
             results.get("p2p_jma",[]) + results.get("usgs",[]) +
             results.get("hinet",[]))
    return _deduplicate(all_q)

def _deduplicate(quakes, time_tol_min=5, dist_tol_deg=0.3):
    # 優先度: jma_bosai > p2p > p2p_jma > hinet > usgs
    # p2p_jmaはJMAデータの再配信なのでjma_bosaiと重複しやすい -> 低優先度
    # hinetは速報値の自動処理震源（無感の微小地震を補う狙い）なので、
    # 気象庁系データと重複する場合はそちらを優先する
    prio = {"jma_bosai":0,"p2p":1,"p2p_jma":2,"hinet":3,"usgs":4}
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

def _row_to_quake(row):
    """quakes.csv の1行(list[str])を地震dictへ変換する。
    load_quakes() と load_quakes_between() で同一のロジックが重複していたため
    共通化。変換できない行は None を返す（呼び出し側でスキップする）。"""
    try:
        return {"time": row[0], "lat": float(row[1]), "lon": float(row[2]),
                "mag": float(row[3]), "depth": float(row[4]),
                "source": row[5] if len(row) > 5 else "",
                "place":  row[6] if len(row) > 6 else "",
                "max_int": row[7] if len(row) > 7 else ""}
    except Exception:
        return None

def load_quakes(days=60):
    """直近 days 日分の地震をCSVから読み込む。"""
    if not os.path.exists(DATA_FILE): return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    data = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                if datetime.fromisoformat(row[0].replace("Z", "+00:00")) < cutoff:
                    continue
            except Exception:
                continue
            q = _row_to_quake(row)
            if q: data.append(q)
    return data

def load_quakes_between(start_utc, end_utc):
    """指定したUTC期間 [start_utc, end_utc] に含まれる地震だけをCSVから読み込む。
    load_quakes()は「直近N日」しか扱えないため、過去の任意時点を基準にした
    再解析（アーカイブタブの日時指定検索）用にこちらを使う。
    ★ quakes.csv は_cleanup_old_quakes(keep_days=65)により65日より古いデータが
    削除されるため、65日より前の期間を指定した場合はヒットしない点に注意。
    アーカイブタブでは、65日より古い期間は代わりに fetch_quakes_jma_catalog_range()
    （気象庁一元化震源カタログ）または fetch_quakes_usgs_range() を使うことで
    数年前まで対応している（archive_historical_riskmap参照）。"""
    if not os.path.exists(DATA_FILE): return []
    data = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                t = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                if t < start_utc or t > end_utc:
                    continue
            except Exception:
                continue
            q = _row_to_quake(row)
            if q: data.append(q)
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
# (v9.05) ETAS計算の高速化。
# 従来は「地震1件ずつ × 17×17近傍セル」をPythonのforループでdictへ加算しており、
# キャリブレーション(65時点 × 数千件)で全体の約6割を占めていた。
# 計算式・計算範囲(逆L字)・出力は変えず、numpyのbincountでまとめて集計する。
_ETAS_MASK_NI0 = int(math.floor(min(b[0] for b in ETAS_REGION_BOXES) / GRID_SIZE)) - 2
_ETAS_MASK_NJ0 = int(math.floor(min(b[2] for b in ETAS_REGION_BOXES) / GRID_SIZE)) - 2
def _build_etas_mask_table():
    """逆L字領域(ETAS_REGION_BOXES)の内外を、格子インデックス(ni,nj)ごとに引ける判定表にする。"""
    ni_hi = int(math.ceil(max(b[1] for b in ETAS_REGION_BOXES) / GRID_SIZE)) + 2
    nj_hi = int(math.ceil(max(b[3] for b in ETAS_REGION_BOXES) / GRID_SIZE)) + 2
    ni = np.arange(_ETAS_MASK_NI0, ni_hi + 1); nj = np.arange(_ETAS_MASK_NJ0, nj_hi + 1)
    lat_g = (ni * GRID_SIZE)[:, None]; lon_g = (nj * GRID_SIZE)[None, :]
    m = np.zeros((len(ni), len(nj)), dtype=bool)
    for lat_min, lat_max, lon_min, lon_max in ETAS_REGION_BOXES:
        m |= (lat_g >= lat_min) & (lat_g <= lat_max) & (lon_g >= lon_min) & (lon_g <= lon_max)
    return m
_ETAS_MASK = _build_etas_mask_table()

def _etas_core(lats, lons, mags, t_days, depth_factors, chunk=1500):
    """ETAS格子スコア本体（numpy配列入力）。戻り値は {(gi,gj): 値} で従来のanalyze_etasと同じ。"""
    n = len(lats)
    if n == 0: return {}
    time_decay    = depth_factors / (t_days + EP.C) ** EP.P
    mag_scale     = EP.K * np.exp(EP.ALPHA * (mags - EP.M0))
    spatial_scale = EP.D * np.exp(EP.GAMMA * mags)
    contributions = time_decay * mag_scale
    R = EP.SPACE_RADIUS
    gi = np.round(lats / GRID_SIZE).astype(np.int64); gj = np.round(lons / GRID_SIZE).astype(np.int64)
    di_arr = np.arange(-R, R + 1); dj_arr = np.arange(-R, R + 1)
    DI, DJ = np.meshgrid(di_arr, dj_arr, indexing="ij")
    dist2 = (DI * GRID_SIZE) ** 2 + (DJ * GRID_SIZE) ** 2           # (2R+1, 2R+1)
    H, W = _ETAS_MASK.shape
    acc = np.zeros(H * W)
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        ii = gi[s:e, None, None] + DI[None] - _ETAS_MASK_NI0
        jj = gj[s:e, None, None] + DJ[None] - _ETAS_MASK_NJ0
        inb = (ii >= 0) & (ii < H) & (jj >= 0) & (jj < W)
        ii = np.where(inb, ii, 0); jj = np.where(inb, jj, 0)
        ok = inb & _ETAS_MASK[ii, jj]
        w = contributions[s:e, None, None] / (dist2[None] + (spatial_scale[s:e, None, None]) ** 2) ** EP.Q
        acc += np.bincount((ii * W + jj)[ok], weights=w[ok], minlength=H * W)
    v = acc + EP.MU
    hit = np.nonzero(v > EP.MU * 1.01)[0]
    if len(hit) == 0: return {}
    return {(int(i // W) + _ETAS_MASK_NI0, int(i % W) + _ETAS_MASK_NJ0): float(v[i]) for i in hit}

def analyze_etas(quakes, ref_time=None):
    """ETAS格子スコアを計算する。ref_timeを指定すると「その時点」を基準に
    時間減衰を計算する（過去のある時点での統合リスクマップ再現用）。
    省略時は従来通り現在時刻を基準にする。"""
    if not quakes: return {}
    ref_time = ref_time or datetime.now(timezone.utc)
    cutoff  = ref_time - timedelta(days=60)
    valid = []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
            if t < cutoff: continue
            dt_days = (ref_time - t).total_seconds() / 86400
            if dt_days < 0: continue
            depth_factor = math.exp(-q["depth"] / EP.DEPTH_SCALE)
            valid.append((q["lat"], q["lon"], q["mag"], dt_days, depth_factor))
        except Exception: continue
    if not valid: return {}
    arr = np.array(valid, dtype=float)
    # 計算範囲: 逆L字型（先島諸島・沖縄・台湾近海 ＋ 九州〜北海道・千島海溝）。
    # 中国大陸・日本海北部は除外。範囲の定義は ETAS_REGION_BOXES / in_etas_region() を参照。
    return _etas_core(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])

def _build_quake_arrays(quakes):
    """(v9.05) キャリブレーション用: 地震リストを時刻順に並べ、数値配列にしておく（65時点で使い回す）。
    時刻が解釈できない/タイムゾーン無しの行は従来(load_quakes_between)と同様に除外する。"""
    rows = []
    for q in quakes:
        try:
            t = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
            if t.tzinfo is None: continue
            rows.append((t.timestamp(), q))
        except Exception:
            continue
    rows.sort(key=lambda r: r[0])
    qs = [r[1] for r in rows]
    return {
        "q":     qs,
        "t":     np.array([r[0] for r in rows], dtype=float),
        "lat":   np.array([q["lat"] for q in qs], dtype=float),
        "lon":   np.array([q["lon"] for q in qs], dtype=float),
        "mag":   np.array([q["mag"] for q in qs], dtype=float),
        "depth": np.array([q["depth"] for q in qs], dtype=float),
        "depth_load": np.array([(q.get("depth", 10.0) or 10.0) for q in qs], dtype=float),
    }

def _analyze_etas_window(qa, lo, hi, ref_epoch):
    """(v9.05) _build_quake_arrays の [lo:hi) 区間から、ref_epoch基準のETAS格子スコアを求める。"""
    dt = (ref_epoch - qa["t"][lo:hi]) / 86400.0
    m = (dt >= 0) & (dt <= 60.0)
    if not m.any(): return {}
    depth = qa["depth"][lo:hi][m]
    return _etas_core(qa["lat"][lo:hi][m], qa["lon"][lo:hi][m], qa["mag"][lo:hi][m],
                      dt[m], np.exp(-depth / EP.DEPTH_SCALE))

def _percentile_thresholds(values_arr):
    log_v = np.log(np.clip(values_arr, 0, None) + 1)
    return (np.percentile(log_v,99.8), np.percentile(log_v,98.5),
            np.percentile(log_v,95.0), np.percentile(log_v,85.0),
            np.percentile(log_v,50.0))


# ══════════════════════════════════════════════════════
# 絶対基準キャリブレーション（気象庁の警戒レベル方式に近づける）
# ──────────────────────────────────────────────────────
# 従来の _percentile_thresholds() / _percentile_rank_map() は「今この瞬間の
# グリッド間の相対順位」でレベルを決めていたため、日本全体が平穏な日でも
# 機械的に上位0.2%がLv5になってしまう（＝相対評価）。
#
# ここでは「アーカイブ」タブと同じ仕組み（load_quakes_between + analyze_etas の
# ref_time指定）を使い、取得できる範囲（quakes.csvの保持期間=65日分）を
# 一定間隔でサンプリングして「過去実績の生値の分布」をプールする。
# 現在の値をこの過去実績分布と比較した順位（＝絶対評価）を求め、
# 相対評価と絶対評価の小さい方（より保守的な方）を採用することで、
# 「相対的にも高く、かつ過去65日の実績と比べても稀」な場合だけ
# 高レベルになるようにする。
#
# さらに、統計的な稀さだけでは「たまたま直近65日がずっと静穏だった／
# 逆にずっと活発だった」場合に基準がぶれるため、ドメイン知識に基づく
# 絶対的な下限フィルタ（_make_etas_domain_floor / _bvalue_domain_floor / _make_stress_domain_floor）も
# 組み合わせるハイブリッド方式にする。
# ══════════════════════════════════════════════════════
HIST_CALIB_LOOKBACK_DAYS = 65     # quakes.csv の保持期間(_cleanup_old_quakes)に合わせる
HIST_CALIB_SAMPLE_HOURS  = 24     # 24時間おきにサンプリング（最大65点、計算負荷とのバランス）
HIST_CALIB_TTL_SEC       = 6 * 3600  # 6時間ごとに再キャリブレーション（新着データを反映）
HIST_CALIB_MIN_CELL_SAMPLES = 20  # (v7.56) セル単位の絶対評価に必要な最低サンプル数(最大65点中)。
                                   # これ未満のセルは自セルの履歴が薄いため、そのセルだけ相対評価にフォールバックする。

_hist_calib_cache = {"ts": 0.0,
                      "etas_cell": None, "bvalue_cell": None, "fault_cell": None, "plate_cell": None,
                      "risk_thresholds": None, "risk_calib_report": None,
                      "n_samples": 0}
_hist_calib_lock = threading.Lock()

# ══════════════════════════════════════════════════════
# (v8.21) 統合リスクマップのレベル境界を「平穏時の目標セル数」から自動決定する
# ──────────────────────────────────────────────────────
# 従来は JS の levelOf() が固定値(0.2/0.4/0.6/0.85/0.97)でレベルを切っていたため、
# 平穏時にどのレベルが何セル出るかは成り行きで、しきい値を手で調整するしかなかった。
# ここでは過去65日を24時間おきにサンプリングして各時点の統合スコア(合成値)を作り、
# 「典型的な(=中央値の)1日で、Lv別セル数が目標値に近くなる」しきい値を求める。
#   ・しきい値は固定(過去実績に対する絶対基準)。活発な時期はセル数が自然に増える。
#   ・平穏時の定義は RISK_CALM_QUANTILE(既定0.5=65日の典型的な日)。
#     直近に大地震があり余震が続いていて「平穏」の基準が上振れするなら、
#     0.3〜0.4 に下げると平穏寄りの日を基準にできる。
# 目標値は各レベルの「表示セル数」(そのレベルちょうど)。累積(そのレベル以上)に換算して使う。
# 幅のある想定はその中央値を採用: Lv1=500, Lv2=100〜200→150, Lv3=50〜100→75, Lv4=0〜10→5。
# ══════════════════════════════════════════════════════
RISK_LEVEL_TARGETS = {1: 500, 2: 150, 3: 75, 4: 5}
RISK_CALM_QUANTILE = 0.5      # 「平穏」= 過去サンプル日のセル数分布の何分位を基準にするか
RISK_LV5_EXCEED_PROB = 0.05   # Lv5: 過去サンプルのうち何割の日に1セルでも出るか(平穏時はほぼ0)
RISK_LEVEL_MIN_GAP = 0.005    # 隣り合うしきい値の最小間隔（同値で潰れないように）
RISK_THRESH_DEFAULT = [0.2, 0.4, 0.6, 0.85, 0.97]   # キャリブレーション未完了時のフォールバック(従来値)
_RISK_HIST_KEYS = ["etas", "bvalue", "fault", "plate"]   # 過去履歴を持つ成分(気圧・TEC・GNSSは履歴なし)
_RISK_STD_KEYS  = ["etas", "bvalue", "fault", "plate"]           # 「標準」プリセット(検算レポートの対象)

def _risk_subset_key(keys):
    """成分キーの集合を、JS側と共通の並び順のキー文字列にする。"""
    return ",".join(k for k in _RISK_HIST_KEYS if k in keys)

def _compute_risk_level_thresholds(per_time_raw, calib_pools):
    """過去サンプル(per_time_raw: [ {etas,bvalue,fault,plate の生値dict}, ... ])から、
    成分の選択パターンごと(15通り)にLv1〜Lv5のしきい値を求める。
    calib_pools: {etas_cell, bvalue_cell, fault_cell, plate_cell}（セル単位の履歴プール）
    戻り値: (thresholds{key:[t1..t5]}, report{...検算用の統計})。作れなければ (None, None)。"""
    import itertools
    cell_keys = list(_RISK_CELLS.keys())
    idx = {k: i for i, k in enumerate(cell_keys)}
    n_cells = len(cell_keys)
    T = len(per_time_raw)
    if T < 10 or n_cells == 0:
        return None, None

    etas_floor  = _make_etas_domain_floor(_cellwise_median_map(calib_pools["etas_cell"]))
    fault_floor = _make_stress_domain_floor(_cellwise_median_map(calib_pools["fault_cell"]))
    plate_floor = _make_stress_domain_floor(_cellwise_median_map(calib_pools["plate_cell"]))

    # S[t, セル, 成分] = 各時点の成分別スコア(0〜1)。成分が無いセルはNaN。
    S = np.full((T, n_cells, len(_RISK_HIST_KEYS)), np.nan)
    sc = {c: {} for c in _RISK_HIST_KEYS}   # (v9.05) セルごとの並べ替え済み履歴（65時点で使い回す）
    for t, raws in enumerate(per_time_raw):
        ranks = {
            "etas":   _absolute_rank_map(raws["etas"],   calib_pools["etas_cell"],   invert=False, log_transform=True,  domain_floor_fn=etas_floor, loo=True, sorted_cache=sc["etas"]),
            "bvalue": _absolute_rank_map(raws["bvalue"], calib_pools["bvalue_cell"], invert=True,  log_transform=False, domain_floor_fn=_bvalue_domain_floor, loo=True, sorted_cache=sc["bvalue"]),
            "fault":  _absolute_rank_map(raws["fault"],  calib_pools["fault_cell"],  invert=False, log_transform=False, domain_floor_fn=fault_floor, loo=True, sorted_cache=sc["fault"]),
            "plate":  _absolute_rank_map(raws["plate"],  calib_pools["plate_cell"],  invert=False, log_transform=False, domain_floor_fn=plate_floor, loo=True, sorted_cache=sc["plate"]),
        }
        for ci, comp in enumerate(_RISK_HIST_KEYS):
            for k, v in ranks[comp].items():
                if k in idx:
                    S[t, idx[k], ci] = v

    w_all = np.array([RISK_DEFAULT_WEIGHTS[k] for k in _RISK_HIST_KEYS])
    # 累積目標(そのレベル以上の合計セル数)
    cum = {}
    running = 0
    for lv in (4, 3, 2, 1):
        running += RISK_LEVEL_TARGETS[lv]
        cum[lv] = running

    thresholds, report = {}, {}
    for r in range(1, len(_RISK_HIST_KEYS) + 1):
        for combo in itertools.combinations(range(len(_RISK_HIST_KEYS)), r):
            sub = S[:, :, list(combo)]
            w = w_all[list(combo)][None, None, :]
            valid = ~np.isnan(sub)
            if not valid.any():
                continue
            wsum = (w * valid).sum(axis=2)
            # (v8.21) 選択した成分のうちそのセルに無いものは中立値0.5として扱う（JS側と同じ）。
            # 成分が1つしか無いセルの総合値がその成分の順位そのままになり、極端値
            # (上限99など)に張り付いて「必ず最上位が出る」状態になるのを防ぐ。
            filled = np.where(valid, sub, RISK_NEUTRAL_SCORE)
            comp = (filled * w).sum(axis=2) / w.sum()
            comp[wsum <= 0] = np.nan   # 1成分も評価できないセルは対象外  (T, n_cells)

            # 各時点で「上からk番目のスコア」を取り、その分位(既定=中央値)をしきい値にする
            desc = -np.sort(-np.where(np.isnan(comp), -1.0, comp), axis=1)
            th = {lv: float(np.quantile(desc[:, min(cum[lv], n_cells) - 1], RISK_CALM_QUANTILE))
                  for lv in (1, 2, 3, 4)}
            # Lv5: 「その日の最大スコア」の (1-p) 分位 → 過去のp割の日に1セル以上出る水準
            th5 = float(np.quantile(desc[:, 0], 1.0 - RISK_LV5_EXCEED_PROB))
            arr = [th[1], th[2], th[3], th[4], th5]
            # 単調増加を保証（同値・逆転を最小間隔で押し上げ）
            for i in range(1, 5):
                arr[i] = max(arr[i], arr[i - 1] + RISK_LEVEL_MIN_GAP)
            arr = [round(min(a, RISK_SCORE_MAX), 4) for a in arr]
            key = _risk_subset_key([_RISK_HIST_KEYS[c] for c in combo])
            thresholds[key] = arr

            # 検算: 標準構成(全4成分)で、しきい値を当てた場合の日別セル数の分布
            if set(_RISK_HIST_KEYS[c] for c in combo) == set(_RISK_STD_KEYS):   # 検算は標準構成
                cnt = {}
                for lv, (lo, hi) in {1: (arr[0], arr[1]), 2: (arr[1], arr[2]),
                                     3: (arr[2], arr[3]), 4: (arr[3], arr[4]), 5: (arr[4], 9)}.items():
                    c = ((comp >= lo) & (comp < hi)).sum(axis=1)
                    cnt[lv] = {"p10": int(np.percentile(c, 10)), "median": int(np.median(c)),
                               "p90": int(np.percentile(c, 90)), "max": int(c.max())}
                report = {"n_samples": T, "n_cells": n_cells, "targets": dict(RISK_LEVEL_TARGETS),
                          "calm_quantile": RISK_CALM_QUANTILE, "counts_per_day": cnt}
    return thresholds, report

def get_risk_level_thresholds():
    """現在有効なしきい値(成分選択パターン→[t1..t5])とフォールバック値を返す。"""
    calib = _get_historical_calibration()
    return {"by_subset": calib.get("risk_thresholds") or {}, "default": RISK_THRESH_DEFAULT,
            "calibrated": bool(calib.get("risk_thresholds")),
            "n_samples": calib.get("n_samples", 0)}

def _sample_ref_times(lookback_days=HIST_CALIB_LOOKBACK_DAYS, step_hours=HIST_CALIB_SAMPLE_HOURS, end_utc=None):
    # (v9.08) end_utc を指定すると「その日時より前」の期間でサンプリングする（日付ごとの基準の作り直し用）
    now = end_utc or datetime.now(timezone.utc)
    n = int(lookback_days * 24 / step_hours)
    # 直近(0時間前=現在)は「今まさに評価しようとしている値」と重なるため含めない
    return [now - timedelta(hours=step_hours * i) for i in range(1, n + 1)]

def _compute_historical_calibration(end_utc=None, quakes=None, step_hours=HIST_CALIB_SAMPLE_HOURS):
    """過去HIST_CALIB_LOOKBACK_DAYS日間を定期サンプリングし、ETAS/b値/断層・
    プレート応力それぞれの「生の値」の分布(プール)を作る。アーカイブタブの
    再計算ロジック(analyze_etas(ref_time=...) / _historical_fault_plate_raw)と同じ計算を、
    全時点分まとめて効率よく行う。

    (v7.56) 従来は全セル・全時刻の値を1本の配列にまとめる「空間+時間混合プール」
    だけを作っていたが、これだと活断層が密集する地域のセルは活動量に関わらず
    常に生値が高いため、プール全体の中での順位が恒常的に上位に張り付き、
    統合リスクマップ・ETASマップともにLv4/5が平穏な時期でも大量発生する
    原因になっていた（空間的な下駄と時間的な稀少性の混同）。
    そこでセルごとに「そのセル自身の過去HIST_CALIB_LOOKBACK_DAYS日間の値」だけを
    集めたプール(*_cellキー)のみを作る方式に統一する。

    (v9.05) 高速化（計算結果は変えない）:
      ・quakes.csv を65回読み直していたのを1回にし、時刻の解釈も1回だけにした。
      ・ETAS: 地震×近傍セルの集計をPythonループからnumpyの一括集計へ。
      ・断層/プレート応力: 距離行列を基準時刻ごとに作り直していたのを、1回だけ作って
        時間減衰の重みとの行列積で全時点を一度に出す方式へ。最寄りセルの割当もキャッシュ。
      ・しきい値算出での履歴の並べ替えを使い回す。

    (v9.08) 日付ごとの基準の作り直し:
      ・end_utc を指定すると、「現在」ではなくその日時の直前HIST_CALIB_LOOKBACK_DAYS日で基準を作る。
        古い日付の評価に「現在の基準」を使うと、その日付より未来の活動が基準に混ざる(先読み)ため。
      ・quakes を渡すと、quakes.csv(直近65日だけ保持)ではなくその地震リストから作る。
        古い日付では一元化カタログ等から読んだ [end_utc-125日, end_utc] の地震を渡す。
        （評価の窓は各サンプル時刻より前だけなので、end_utc より後の地震が混ざっても使われない）
      ・step_hours でサンプル間隔を変えられる（既定は従来どおり24時間）。
      省略時は従来と同じ動作（現在・quakes.csv・24時間）。"""
    from collections import defaultdict
    ref_times = _sample_ref_times(step_hours=step_hours, end_utc=end_utc)
    etas_cell, bvalue_cell, fault_cell, plate_cell = (defaultdict(list), defaultdict(list),
                                                       defaultdict(list), defaultdict(list))
    per_time_raw = []   # (v8.21) しきい値キャリブレーション用: 各時点の成分別生値

    # ── 1) 地震データを1回だけ読み込み、時刻順の配列にする ──
    win = 60 * 86400.0
    t_lo = min(ref_times) - timedelta(days=60)
    t_hi = max(ref_times)
    qa = _build_quake_arrays(load_quakes_between(t_lo, t_hi) if quakes is None else quakes)
    qt = qa["t"]
    samples = []   # (rt, rt_epoch, lo, hi): 各時点で使う地震の範囲 qa[lo:hi]
    for rt in ref_times:
        rt_e = rt.timestamp()
        lo = int(np.searchsorted(qt, rt_e - win, side="left"))
        hi = int(np.searchsorted(qt, rt_e, side="right"))
        if hi - lo > 0:
            samples.append((rt, rt_e, lo, hi))

    # ── 2) 時点ごとのETAS・b値 ──
    rows = []   # 有効だった時点の(rt, rt_e, lo, hi, etas_raw, bvalue_raw)
    for rt, rt_e, lo, hi in samples:
        try:
            etas_raw_t = _risk_etas_raw(_analyze_etas_window(qa, lo, hi, rt_e))
            bgrid = compute_bvalue_grid(qa["q"][lo:hi])
            bvalue_raw_t = _risk_bvalue_raw(bgrid)
            rows.append((rt, rt_e, lo, hi, etas_raw_t, bvalue_raw_t))
        except Exception as e:
            print(f"[絶対基準キャリブレーション] サンプル({rt})でエラー: {e}")

    # ── 3) 断層・プレート境界の応力負荷（全時点まとめて）──
    if rows:
        try:
            fpts = _decimate_points(_flatten_line_points(get_japan_active_faults()), FSP.MAX_POINTS)
            ppts = _decimate_points(_flatten_line_points(get_plate_boundaries(), bbox=FAULTS_BBOX), FSP.MAX_POINTS)
            sp = [(r[1], r[2], r[3]) for r in rows]
            fault_list = _load_to_grid_max_multi(fpts, _fault_plate_loads_multi(fpts, qa, sp))
            plate_list = _load_to_grid_max_multi(ppts, _fault_plate_loads_multi(ppts, qa, sp))
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[絶対基準キャリブレーション] 応力負荷の計算でエラー: {e}")
            rows = []

    ok = 0
    for n, (rt, rt_e, lo, hi, etas_raw_t, bvalue_raw_t) in enumerate(rows):
        try:
            fault_raw, plate_raw = fault_list[n], plate_list[n]
            for k, v in etas_raw_t.items(): etas_cell[k].append(v)
            for k, v in bvalue_raw_t.items(): bvalue_cell[k].append(v)
            for k, v in fault_raw.items(): fault_cell[k].append(v)
            for k, v in plate_raw.items(): plate_cell[k].append(v)
            per_time_raw.append({"etas": etas_raw_t, "bvalue": bvalue_raw_t,
                                 "fault": fault_raw, "plate": plate_raw})
            ok += 1
        except Exception as e:
            print(f"[絶対基準キャリブレーション] サンプル({rt})でエラー: {e}")
            continue
    print(f"[絶対基準キャリブレーション] {ok}/{len(ref_times)}時点サンプリング完了 "
          f"(etas_cells={len(etas_cell)} bvalue_cells={len(bvalue_cell)} "
          f"fault_cells={len(fault_cell)} plate_cells={len(plate_cell)})")
    # セル単位プールは、サンプル時点数(ok)が少なすぎる(=積み上げ途中)場合は
    # 誤ったセル基準値で誤判定するより「未キャリブレーション」扱いの方が安全なため、
    # 十分な時点数が確保できるまではNoneのままにする。
    cell_ready = ok >= max(10, HIST_CALIB_MIN_CELL_SAMPLES // 2)

    # (v8.21) 平穏時の目標セル数に合わせた統合スコアのレベル境界を算出する。
    # 注: プールに各サンプル自身も含まれる(in-sample)ため、絶対順位はごくわずかに
    #     甘く出るが、65点のプールに対する影響は小さいので許容している。
    risk_thresholds, risk_report = None, None
    if cell_ready and etas_cell and bvalue_cell and fault_cell and plate_cell:
        try:
            risk_thresholds, risk_report = _compute_risk_level_thresholds(
                per_time_raw,
                {"etas_cell": dict(etas_cell), "bvalue_cell": dict(bvalue_cell),
                 "fault_cell": dict(fault_cell), "plate_cell": dict(plate_cell)})
            if risk_thresholds:
                std = risk_thresholds.get(_risk_subset_key(_RISK_STD_KEYS))
                print(f"[リスクレベル境界] 標準構成のしきい値 Lv1〜Lv5 = {std}")
                if risk_report:
                    print(f"[リスクレベル境界] 過去{risk_report['n_samples']}日の日別セル数(中央値) = "
                          + ", ".join(f"Lv{lv}:{v['median']}" for lv, v in risk_report['counts_per_day'].items()))
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[リスクレベル境界] 算出に失敗（従来の固定しきい値にフォールバック）: {e}")
    return {
        "risk_thresholds": risk_thresholds,
        "risk_calib_report": risk_report,
        "ts": time.time(),
        "etas_cell":   dict(etas_cell)   if (cell_ready and etas_cell)   else None,
        "bvalue_cell": dict(bvalue_cell) if (cell_ready and bvalue_cell) else None,
        "fault_cell":  dict(fault_cell)  if (cell_ready and fault_cell)  else None,
        "plate_cell":  dict(plate_cell)  if (cell_ready and plate_cell)  else None,
        "n_samples": ok,
    }

def _get_historical_calibration():
    """キャリブレーション結果をキャッシュから返す。期限切れ・未計算の場合は
    バックグラウンドスレッドで再計算を開始しつつ、それまでは古い(または空の)
    結果をそのまま返す（Webリクエストの応答をブロックしないため）。"""
    global _hist_calib_cache
    if time.time() - _hist_calib_cache["ts"] > HIST_CALIB_TTL_SEC:
        if _hist_calib_lock.acquire(blocking=False):
            def _job():
                global _hist_calib_cache
                try:
                    _hist_calib_cache = _compute_historical_calibration()
                finally:
                    _hist_calib_lock.release()
            threading.Thread(target=_job, daemon=True).start()
    return _hist_calib_cache

def _historical_abs_rank(raw_map, pool, invert=False, log_transform=False):
    """raw_mapの各セルの値を『そのセル自身の過去実績分布(pool[cell_key])の中での
    位置』(0〜1)に変換する(v7.56: セル単位。旧: 全セル混合の1本のプール)。
    これにより「活断層近接セルは常に生値が高い」という空間的な特徴と、
    「今その場所がいつもより活発かどうか」という時間的な異常度を分離する。
    pool自体が未キャリブレーション、またはそのセルの履歴サンプルが
    HIST_CALIB_MIN_CELL_SAMPLES未満の場合は、そのセルについてNone相当
    (=呼び出し側で相対評価にフォールバック)として扱う。"""
    if not raw_map or not pool:
        return None
    out = {}
    for k, v in raw_map.items():
        hist = pool.get(k)
        if hist is None or len(hist) < HIST_CALIB_MIN_CELL_SAMPLES:
            continue
        hist_arr = np.asarray(hist, dtype=float)
        ref = np.log(np.clip(hist_arr, 0, None) + 1) if log_transform else hist_arr
        ref_sorted = np.sort(ref)
        n = len(ref_sorted)
        x = math.log(max(v, 0) + 1) if log_transform else v
        rank = np.searchsorted(ref_sorted, x, side="right") / n
        if invert:
            rank = 1.0 - rank
        out[k] = float(np.clip(rank, 0.0, 1.0))
    return out if out else None

def _cellwise_median_map(pool):
    """(v7.56) セル単位履歴プール(dict: cell_key -> 値のリスト)から、
    各セルの「平常時の中央値」を返す。断層/プレートの絶対下限フィルタで、
    そのセル自身の平常値に対する倍率を求めるために使う。"""
    if not pool:
        return {}
    return {k: float(np.median(v)) for k, v in pool.items() if v}

def _make_etas_domain_floor(median_map, lv3_ratio=1.5, lv4_ratio=3.0):
    """(v7.56) ETAS用ドメイン下限フィルタ。以前(_etas_domain_floor)は全国一律の
    固定値(EP.MU、ETASモデルの背景発生率パラメータ)を基準にしていたため、
    三陸沖・南海トラフ・伊豆諸島のように恒常的に地震活動が活発な地域では、
    ありふれたM4程度の地震1回でも生スコアがEP.MUの数十〜数百倍になり、
    この下限フィルタが事実上機能していなかった(=断層/プレートと同種の
    「全国一律の基準による空間的な下駄」がETASにも残っていた)。
    そのセル自身の過去実績の中央値(median_map、etas_cellプール由来)を基準に
    することで、「そのセルにとって普段よりどれだけ活発か」を測る真の絶対基準にする。
    キャリブレーション未完了・新規セルなど中央値が無い場合は、従来通りEP.MUを
    暫定基準として使う（立ち上げ初期の安全弁）。"""
    def _floor(cell_key, raw_v, s):
        base = median_map.get(cell_key, EP.MU)
        ratio = raw_v / max(base, EP.MU, 1e-9)
        if ratio < lv3_ratio: return min(s, 0.55)   # Lv3相当まで
        if ratio < lv4_ratio: return min(s, 0.80)   # Lv4相当まで
        return s
    return _floor

def _bvalue_domain_floor(cell_key, raw_v, s):
    """ドメイン知識: 地殻の平均的なb値はおよそ1.0前後とされる。0.8を下回って
    初めて応力集中の可能性を疑うレベルとみなし、それより高い(平均的な)場合は
    スコアを頭打ちにする。
    (v7.56: _hybrid_rank_mapの呼び出し規約統一のためcell_key引数を追加、未使用)"""
    if raw_v >= 0.9: return min(s, 0.55)
    if raw_v >= 0.8: return min(s, 0.80)
    return s

def _make_stress_domain_floor(median_map, lv3_ratio=3.0, lv4_ratio=8.0, min_baseline=1e-6):
    """(v7.56) 断層/プレート応力負荷用のドメイン知識フィルタ。
    そのセル自身の平常時中央値(median_map)に対して現在の生値が何倍かを見て、
    一定倍率に達していなければLv4/5相当のスコアにならないよう頭打ちにする。
    活断層/プレート境界に恒常的に近いセルであっても、"いつも通り"の負荷では
    高スコアにならず、直近の地震活動で自セルの平常値から明確に逸脱した場合
    のみ高スコアを許す。倍率は経験則であり、観測実績の蓄積とともに
    再チューニングする前提の暫定値。"""
    def _floor(cell_key, raw_v, s):
        base = median_map.get(cell_key, 0.0)
        ratio = raw_v / max(base, min_baseline)
        if ratio < lv3_ratio: return min(s, 0.55)   # Lv3相当まで
        if ratio < lv4_ratio: return min(s, 0.80)   # Lv4相当まで
        return s
    return _floor

# 気圧偏差・TEC(電離圏)は過去分のデータを保持しておらず絶対評価の基準を
# 作れないため、単独でLv4/5の主因にならないようスコアに上限を設ける
# （ドメイン知識: 気圧・電離圏異常と地震の関連は実証段階であり、あくまで
# 補助的な参考指標として扱う、という判断）。
PRESSURE_TEC_SCORE_CAP = 0.6

def _hybrid_rank_map(raw_map, pool, invert=False, log_transform=False, domain_floor_fn=None):
    """『今この瞬間の空間内相対順位』と『そのセル自身の過去65日間の実績と
    比べた絶対順位』の両方を求め、小さい方（より保守的な方）を採用する。
    さらにdomain_floor_fnが与えられればドメイン知識による絶対下限フィルタも
    適用する。domain_floor_fnは (cell_key, raw_v, s) を受け取る形式
    (v7.56: セル単位の絶対基準に対応するためcell_keyを渡すよう変更)。
    キャリブレーション未完了時は相対評価のみにフォールバックする。"""
    rel = _percentile_rank_map(raw_map, invert=invert)
    absr = _historical_abs_rank(raw_map, pool, invert=invert, log_transform=log_transform)
    out = {}
    for k, rv in rel.items():
        s = min(rv, absr.get(k, rv)) if absr is not None else rv
        if domain_floor_fn is not None:
            s = domain_floor_fn(k, raw_map[k], s)
        out[k] = s
    return out


# ══════════════════════════════════════════════════════
# (v8.21) 統合リスクマップ用: 純粋な絶対評価スコア
# ──────────────────────────────────────────────────────
# 旧 _hybrid_rank_map は min(今の全セル間の相対順位, 自セル履歴に対する絶対順位) を
# 採っていた。相対順位は定義上「最上位セルが必ず1.0」になるため、成分(ETAS・b値・
# 活断層・プレート)の最上位が同じセルに重なると、絶対評価のはずの総合値が
# 平穏時でも常に100のセルが出てしまっていた。また、履歴が薄いセルは相対順位に
# フォールバックしていたため、そこでも必ず1.0が出る構造だった。
# ここでは相対順位を一切使わず、「そのセル自身の過去実績の中での位置」だけを使う。
#   ・順位は (件数+0.5)/(n+1) のプロッティングポジション。過去65点では最大でも
#     約0.992で、1.0には決してならない（65点の経験分布では『確実に史上最大』とは
#     言い切れないため）。さらに RISK_SCORE_MAX で頭打ちにする。
#   ・過去値と同値のときは中央順位(タイは半分)で扱い、変化が無いのに高スコアに
#     張り付くことを防ぐ。
#   ・履歴が HIST_CALIB_MIN_CELL_SAMPLES 件未満のセル・成分は評価せず除外する
#     （総合スコアは残りの成分で再正規化される）。
#   ・過去サンプル自身のスコアを求めるとき(loo=True)は、そのサンプルをプールから
#     除いて(leave-one-out)順位を出す。ライブの値はプールに含まれないため、
#     境界の算出時とライブ表示時で条件を揃えるため。
# ══════════════════════════════════════════════════════
RISK_SCORE_MAX = 0.99
RISK_NEUTRAL_SCORE = 0.5   # 履歴のあるはずの成分がそのセルに無い場合に補う中立スコア

def _prep_sorted_ref(hist, log_transform):
    """(v9.05) 履歴(hist)を(必要ならlog変換して)昇順に並べたリストにする。同じ履歴に対し何度も順位を
    求める場面(しきい値算出: 65時点×全セル)で、毎回の並べ替えを避けるために分離した。"""
    hist_arr = np.asarray(hist, dtype=float)
    ref = np.log(np.clip(hist_arr, 0, None) + 1) if log_transform else hist_arr
    return np.sort(ref).tolist()

def _abs_rank_from_sorted(ref_sorted, v, invert, log_transform, loo=False):
    n = len(ref_sorted)
    if loo:
        n -= 1
    if n < HIST_CALIB_MIN_CELL_SAMPLES:
        return None
    x = math.log(max(v, 0) + 1) if log_transform else v
    lo = bisect.bisect_left(ref_sorted, x)
    hi = bisect.bisect_right(ref_sorted, x)
    c = (lo + hi - 1) / 2.0 if loo else (lo + hi) / 2.0    # タイは中央順位
    rank = (c + 0.5) / (n + 1)
    if invert:
        rank = 1.0 - rank
    return float(min(max(rank, 0.0), RISK_SCORE_MAX))

def _abs_rank_value(hist, v, invert, log_transform, loo=False):
    """1セル分: 履歴(hist)に対する値vの絶対順位(0〜RISK_SCORE_MAX)。作れなければNone。"""
    n = len(hist)
    if loo:
        n -= 1
    if n < HIST_CALIB_MIN_CELL_SAMPLES:
        return None
    return _abs_rank_from_sorted(_prep_sorted_ref(hist, log_transform), v, invert, log_transform, loo=loo)

def _absolute_rank_map(raw_map, pool, invert=False, log_transform=False,
                       domain_floor_fn=None, loo=False, sorted_cache=None):
    """raw_mapの各セルを『そのセル自身の過去実績の中での位置』(絶対評価)のみでスコア化する。
    履歴の無い/薄いセルは結果に含めない(=相対評価へのフォールバックはしない)。
    sorted_cache(dict)を渡すと、セルごとの並べ替え済み履歴を使い回す（同じpoolに対して
    繰り返し呼ぶ場合用。(v9.05)）"""
    out = {}
    if not raw_map or not pool:
        return out
    for k, v in raw_map.items():
        hist = pool.get(k)
        if hist is None:
            continue
        if sorted_cache is not None:
            rs = sorted_cache.get(k)
            if rs is None:
                rs = sorted_cache[k] = _prep_sorted_ref(hist, log_transform)
            s = _abs_rank_from_sorted(rs, v, invert, log_transform, loo=loo)
        else:
            s = _abs_rank_value(hist, v, invert, log_transform, loo=loo)
        if s is None:
            continue
        if domain_floor_fn is not None:
            s = domain_floor_fn(k, v, s)
        out[k] = min(s, RISK_SCORE_MAX)
    return out

def _abs_score_single(cell_key, v, pool, invert, log_transform, floor_maker):
    """1セル・1値だけの絶対スコア（推移グラフ用。全セル計算を避ける）。"""
    if not pool or cell_key not in pool:
        return None
    s = _abs_rank_value(pool[cell_key], v, invert, log_transform)
    if s is None:
        return None
    if floor_maker is not None:
        med = {cell_key: float(np.median(pool[cell_key]))}
        s = floor_maker(med)(cell_key, v, s)
    return min(s, RISK_SCORE_MAX)


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
        src_badge = {"jma_bosai":"JMA","p2p":"P2P","p2p_jma":"P2P","usgs":"USGS","hinet":"Hi-net"}.get(src,"?")

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
            f'<td class="c4"><span style="background:{ic};color:#000;padding:1px 5px;border-radius:7px;font-size:11px;font-weight:700">{il}</span></td>'
            f'<td class="c2">{depth:.0f}km</td>'
            f'<td class="c2"><span style="background:rgba(56,132,255,.25);padding:1px 5px;border-radius:7px;font-size:10px">{src_badge}</span></td>'
            f'</tr>'
        )

    markers_js = json.dumps(markers)
    total = len(recent)
    felt_n = sum(1 for q in recent if q.get("max_int","").strip() not in ("","−","-"))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;overflow:hidden}}
#lp{{width:380px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-right:1px solid rgba(255,255,255,.14);display:flex;flex-direction:column;overflow:hidden}}
#lh{{padding:12px 14px 8px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border-bottom:1px solid rgba(255,255,255,.18);flex-shrink:0}}
#lh h2{{font-size:14px;color:#f3f4f6;margin-bottom:4px}}
#lh p{{font-size:11px;color:rgba(235,238,245,.46)}}
#fbar{{display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0}}
.fb{{flex:1;padding:5px 2px;text-align:center;cursor:pointer;font-size:11px;font-weight:600;
     color:rgba(235,238,245,.62);border:none;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border-radius:12px}}
.fb:hover{{color:#f3f4f6;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%)}}
.fb.on{{color:#fff;background:linear-gradient(135deg,#0a84ff,#7c5cff)}}
#ls{{flex:1;overflow-y:auto}}
table{{width:100%;border-collapse:collapse}}
thead tr{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);position:sticky;top:0;z-index:10}}
thead th{{padding:6px 5px;font-size:10px;color:rgba(235,238,245,.62);text-align:left;border-bottom:1px solid rgba(255,255,255,.18);white-space:nowrap}}
.qrow:hover{{background:rgba(255,255,255,.12)}}
.c1{{padding:5px 7px;font-weight:600;color:#f3f4f6;font-size:12px}}
.c2{{padding:5px 4px;color:rgba(235,238,245,.62);font-size:11px;white-space:nowrap}}
.c3{{padding:5px 4px;text-align:center;font-weight:700;font-size:12px;white-space:nowrap}}
.c4{{padding:5px 4px;text-align:center;white-space:nowrap}}
.c4 span{{white-space:nowrap}}
#mp{{flex:1;overflow:hidden;position:relative}}#map{{width:100%;height:100%}}
#mglg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:10px 13px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:11px;line-height:1.8;color:#f3f4f6}}
#mglg b{{font-size:12px}}
#lp{{position:relative;transition:margin-left 0.2s}}
#lp.closed{{margin-left:-380px}}
#lhTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}}
#lpClose{{flex-shrink:0;width:22px;height:22px;border:none;border-radius:12px;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:rgba(235,238,245,.72);
    cursor:pointer;font-size:13px;line-height:1;display:flex;align-items:center;justify-content:center}}
#lpClose:hover{{background:rgba(255,255,255,.3);color:#fff}}
#lpReopen{{position:absolute;top:70px;left:0;z-index:5000;width:34px;height:78px;border:none;
    border-radius:0 18px 18px 0;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62);cursor:pointer;font-size:12px;
    display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;
    box-shadow:2px 0 8px rgba(0,0,0,.4)}}
#lpReopen:hover{{background:rgba(10,132,255,.55);color:#fff}}
#lpReopen.show{{display:flex}}
#lpReopen .arrow{{font-size:15px;line-height:1}}
#lpReopen .vlabel{{writing-mode:vertical-rl;letter-spacing:1px;font-size:10px;font-weight:600}}

@media (max-width:768px){{
  #lp{{position:fixed;top:0;left:0;height:100%;width:86vw;max-width:340px;z-index:2000;
      box-shadow:4px 0 24px rgba(0,0,0,.7)}}
  #lp.closed{{margin-left:-86vw}}
  #lhTop h2{{font-size:12.5px}}
  thead th,.c1,.c2,.c3{{font-size:10.5px}}
}}
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
    <button class="fb" onclick="filter('hinet',this)">Hi-net(無感)</button>
  </div>
  <div id="ls"><table>
    <thead><tr><th>震源名</th><th>発生時刻</th><th>M</th><th>最大震度</th><th>深さ</th><th>ソース</th></tr></thead>
    <tbody id="tbody">{rows}</tbody>
  </table></div>
</div>
<div id="mp"><div id="map"></div>
  <div id="mglg">
    <b>マップの円色（マグニチュード）</b><br>
    <div style="width:160px;height:10px;border-radius:7px;margin:5px 0 2px;
      background:linear-gradient(to right,#94a3b8,#4ade80,#facc15,#fb923c,#ef4444,#dc2626,#ff00ff)"></div>
    <div style="display:flex;justify-content:space-between;width:160px;font-size:10px;color:rgba(235,238,245,.62)">
      <span>M2以下</span><span>M8以上</span>
    </div>
  </div>
</div>
<script>
if(window.innerWidth<=768){{
  document.getElementById('lp').classList.add('closed');
  document.getElementById('lpReopen').classList.add('show');
}}
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
  var row=document.getElementById('qrow_'+idx); if(row)row.style.background='rgba(10,132,255,.3)';
  map.flyTo([lat,lon],8,{{duration:0.7}});
  if(layers[idx])setTimeout(function(){{layers[idx].openPopup()}},800);
}}

var allRows=Array.from(document.querySelectorAll('.qrow'));
var MODE_MAP={{'all':function(r){{return true}},'felt':function(r){{return r.querySelector('.c4 span').style.background!='rgb(71, 85, 105)'}},'unfelt':function(r){{return r.querySelector('.c4 span').style.background==='rgb(71, 85, 105)'}},'jma':function(r){{return r.querySelector('.c2:last-child span').textContent==='JMA'}},'p2p':function(r){{return r.querySelector('.c2:last-child span').textContent==='P2P'}},'usgs':function(r){{return r.querySelector('.c2:last-child span').textContent==='USGS'}},'hinet':function(r){{return r.querySelector('.c2:last-child span').textContent==='Hi-net'}}}};

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
        th5,th4,th3,th2,th1 = _percentile_thresholds(vals)          # 相対評価（今この瞬間の空間内順位。細かい格子内の濃淡表現はそのまま維持）

        # (v7.56) 絶対評価(アーカイブ=過去実績との比較)を、統合リスクマップと同じ
        # 「そのエリア自身の過去実績」方式に統一する。
        # 旧実装は全セル・全時刻を混ぜた1本のプール(calib["etas"])と比較していたため、
        # 三陸沖・南海トラフ等の恒常的に活発なエリアは、平常運転でも絶対評価側で
        # 常に上位に張り付く「空間的な下駄」問題があった。
        # ETASの細かい格子(GRID_SIZE=0.1°)ではセルごとの過去サンプルが疎になり
        # 単独では履歴を組めないため、統合リスクマップと同じ粗い格子
        # (RISK_GRID_SIZE=0.5°)に集約した上で「そのエリアが今、平常時と比べて
        # 活発かどうか」を絶対評価し、その結果を該当エリア内の細かい格子すべてに
        # 上限として適用する。相対評価(①)による細かい濃淡表現はそのまま活かしつつ、
        # 絶対評価(②)・ドメイン知識(③)の頭打ちだけをエリア単位の真の実績で行う。
        calib = _get_historical_calibration()
        etas_raw_coarse = _risk_etas_raw(grid_scores)
        etas_cell_pool = calib.get("etas_cell")
        abs_rank_coarse = _historical_abs_rank(etas_raw_coarse, etas_cell_pool, invert=False, log_transform=True)
        median_map = _cellwise_median_map(etas_cell_pool)

        for (gi,gj), score in grid_scores.items():
            s = math.log(score+1)
            # ① 相対評価: 今の細かい格子内での順位
            if   s>=th5: lv=5
            elif s>=th4: lv=4
            elif s>=th3: lv=3
            elif s>=th2: lv=2
            elif s>=th1: lv=1
            else: continue
            # ② 絶対評価: そのエリア(RISK_GRID_SIZE格子)自身の過去実績と比べて稀な値かどうか
            #    （キャリブレーション未完了、またはそのエリアの履歴サンプルが不足している場合はスキップ）
            lat = gi * GRID_SIZE; lon = gj * GRID_SIZE
            rkey = (int(math.floor(lat / RISK_GRID_SIZE)), int(math.floor(lon / RISK_GRID_SIZE)))
            if abs_rank_coarse is not None and rkey in abs_rank_coarse:
                abs_s = abs_rank_coarse[rkey]
                if   abs_s>=0.97: lv_abs=5
                elif abs_s>=0.85: lv_abs=4
                elif abs_s>=0.6:  lv_abs=3
                elif abs_s>=0.4:  lv_abs=2
                else: lv_abs=0
                lv = min(lv, lv_abs)
            # ③ ドメイン知識: 全国一律のEP.MUではなく、そのエリア自身の平常時中央値に
            #    対する倍率が小さいうちはLv4/5にしない安全弁（中央値が無ければEP.MUで代用）
            base = median_map.get(rkey, EP.MU)
            ratio = etas_raw_coarse.get(rkey, score) / max(base, EP.MU, 1e-9)
            if ratio < 1.5: lv = min(lv, 3)
            elif ratio < 3.0: lv = min(lv, 4)
            if lv <= 0: continue
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif}}
#tb{{display:flex;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0;padding:6px 10px;gap:8px;align-items:center;flex-wrap:wrap}}
#tb span{{font-size:12px;color:rgba(235,238,245,.62)}}
.tog{{padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer;border:none;border-radius:12px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62)}}
.tog.on{{background:rgba(10,132,255,.55);color:#fff}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:11px 14px;border-radius:18px;border:1px solid #8800cc;font-size:12px;line-height:2;color:#f3f4f6}}

@media (max-width:768px){{
  #tb span:last-child{{margin-left:0;flex-basis:100%;order:5}}
  #lg{{left:8px;bottom:8px;right:8px;font-size:11px;padding:9px 11px;line-height:1.7}}
}}
</style></head><body>
<div id="tb">
  <span>表示レイヤー:</span>
  <button class="tog on" id="togEtas" onclick="toggleLayer('etas',this)">ETASグリッド</button>
  <button class="tog" id="togRecent" onclick="toggleLayer('recent',this)">直近72h地震</button>
  <span style="margin-left:auto;color:rgba(235,238,245,.46);font-size:11px">更新: {updated_str}</span>
</div>
<div id="map"></div>
<div id="lg">
  <b>ETAS 地震発生確率</b><br>
  <span style="color:{LEVEL_COLOR[5]};background:{LEVEL_COLOR[5]};opacity:0.9;padding:0 6px;border:1px solid #fff">■</span> Lv5（特に稀な高リスク）<br>
  <span style="color:{LEVEL_COLOR[4]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv4（警戒）<br>
  <span style="color:{LEVEL_COLOR[3]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv3（注意）<br>
  <span style="color:{LEVEL_COLOR[2]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv2（やや注意）<br>
  <span style="color:{LEVEL_COLOR[1]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv1（平常）<br>
  <small style="color:rgba(235,238,245,.46)">Lv4・5は過去65日間の実績と比べても稀な場合のみ表示されます</small>
  <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
  <small>JMA:{src_count.get('jma_bosai',0)} P2P:{src_count.get('p2p',0)+src_count.get('p2p_jma',0)} USGS:{src_count.get('usgs',0)} Hi-net:{src_count.get('hinet',0)}<br>計{len(quakes)}件</small>
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
    {{color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:{LEVEL_FILL_OPACITY}}})
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif}}
#hdr{{padding:8px 14px;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0;
       display:flex;align-items:center;gap:16px;font-size:12px;color:rgba(235,238,245,.62);flex-wrap:wrap}}
#hdr b{{color:#f3f4f6;font-size:13px}}
.stat{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);padding:4px 10px;border-radius:12px;font-size:11px;color:rgba(235,238,245,.72)}}
.stat span{{color:#60a5fa;font-weight:700}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:11px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:12px;line-height:2;color:#f3f4f6}}

@media (max-width:768px){{
  #hdr{{gap:8px;padding:6px 10px}}
  #hdr>div[style*="margin-left:auto"]{{flex-basis:100%;order:5;margin-left:0!important}}
  #lg{{left:8px;bottom:8px;right:8px;font-size:11px;padding:9px 11px;line-height:1.7}}
}}
</style></head><body>
<div id="hdr">
  <b>b値マップ (Gutenberg-Richter則)</b>
  <div class="stat">グリッド数: <span>{len(cells)}</span></div>
  <div class="stat">使用地震数: <span>{total_used}</span></div>
  <div class="stat">b平均: <span>{b_mean}</span></div>
  <div class="stat">b最小: <span style="color:#ef4444">{b_min}</span></div>
  <div class="stat">b最大: <span style="color:#60a5fa">{b_max}</span></div>
  <div style="margin-left:auto;color:rgba(235,238,245,.46)">更新: {updated_str}</div>
</div>
<div id="map"></div>
<div id="lg">
  <b>b値カラースケール</b><br>
  <div style="width:130px;height:10px;border-radius:7px;
    background:linear-gradient(to right,#dc3c3c,#60a0dc);margin:5px 0 2px"></div>
  <div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:rgba(235,238,245,.62)">
    <span>低 ({b_min})</span><span>高 ({b_max})</span>
  </div>
  <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
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
# URL形式が変わった場合は、コードを直さず環境変数 TEC_URL_TEMPLATE で差し替えられる。
# 使えるプレースホルダー: {year} {doy:03d} {hh} {mm} {month:02d} {day:02d}
TEC_URL_TEMPLATE = os.environ.get("TEC_URL_TEMPLATE", "").strip() or (
                    "https://aer-nc-web.nict.go.jp/GPS/QR_GEONET/AMAP/"
                    "{year}/{doy:03d}/{hh}_{mm}_{month:02d}{day:02d}_{year}_Amap.jpg")
TEC_URL_STEP_MIN      = 10   # 生成間隔
# (v9.10) 準リアルタイム版は「数時間(〜4時間程度)遅延」で公開され、ページには最新6時間分が載る。
# 以前の90分では最新画像まで届かず全候補が404になっていたため、6時間まで遡る。
TEC_URL_LOOKBACK_MIN  = 360  # どこまで過去に遡って候補を試すか
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
TEC_CACHE_SEC          = TEC_FETCH_INTERVAL_SEC   # (GNSS_CACHE_SEC相当) これを過ぎたら次のアクセスで裏更新
TEC_LIVE_MAX_STALE_SEC = 3 * 3600  # (GNSS_LIVE_MAX_STALE_DAYS相当) これより古いTECはリスクマップに使わない
# (v9.21) TEC成分の固定目盛り: 「広域成分を除いた局所的なTEC偏差(TECU)」→スコア(0〜1)。
#   日変化・磁気嵐・太陽活動による日本全体の増減は _risk_tec_raw で差し引くので、ここに来るのは局所的な異常だけ。
#   TEC-地震前兆は学術的に確立していないため、上限を0.70に抑え(TEC単独ではLv4以上になりにくい)、
#   さらに統合側では「他成分の水準を超えた分だけ押し上げる」扱い(GNSSと同じ)にする。
TEC_SCORE_POINTS = [(0.0, 0.10), (2.0, 0.25), (4.0, 0.45), (8.0, 0.65), (15.0, 0.70)]
TEC_SCORE_MAX    = 0.70
TEC_MIN_CELLS    = 30   # 平面フィット(広域成分の除去)に必要な最少セル数

# ── 代替取得経路（GNSS_JSON_URL と同じ考え方）──────────────
# NICTの画像を色逆変換する代わりに、別環境(PC/GitHub Actions等)で作った数値JSONのURLを指定できる。
#   例: https://raw.githubusercontent.com/<owner>/<repo>/main/tec_grid.json
#   形式: {"updated": "2026-10-14 12:00 JST",
#          "cells": [{"lat": 35.25, "lon": 138.25, "v": 22.1}, ...]}      ← v は TECU
#   lat/lon は任意の点でよく、リスク格子(0.5°)ごとに中央値へ集約される。
# 設定されている間は画像取得より優先される（未設定なら従来どおり画像の色逆変換）。
TEC_JSON_URL = os.environ.get("TEC_JSON_URL", "").strip()

_tec_lock    = threading.Lock()
_tec_cache   = {"grid": None,       # {(gi,gj): TECU}
                "ts": 0.0,          # 最後に「成功」した時刻（データの鮮度）
                "tried": 0.0,       # 最後に「試行」した時刻（失敗時の連打防止）
                "updated": None,    # 表示用の更新時刻文字列
                "error": None,      # 直近の失敗理由（成功したらNone）
                "updating": False,  # 更新中フラグ（二重起動防止）
                "source": None,     # "image" / "json"
                "image_url": None,  # 採用した画像URL
                "hist_url": None,   # 最後に履歴へ入れた画像URL（同じ画像を二重に入れない）
                "attempts": []}     # 直近の試行ログ（/tec/status で確認）
_tec_history = []   # [(epoch_sec, {(gi,gj): value, ...}), ...] 古い→新しい順

def _tec_calibrated():
    c = TEC_CALIBRATION
    return all(c.get(k) is not None for k in
               ("map_px_box_frac", "map_latlon_box", "legend_px_box_frac", "legend_value_range"))

# (GSI_GNSS_ENABLED 相当) JSON URLがあるか、Pillow+キャリブレーション済みで画像を読めるとき有効
TEC_ENABLED = bool(TEC_JSON_URL or (_PIL_AVAILABLE and _tec_calibrated()))

def _tec_build_url(dt):
    doy = dt.timetuple().tm_yday
    return TEC_URL_TEMPLATE.format(year=dt.year, doy=doy, hh=f"{dt.hour:02d}",
                                    mm=f"{dt.minute:02d}", month=dt.month, day=dt.day)

def _tec_candidate_list():
    """(tz, lag_min, url, dt) の候補リストを、前回成功した組み合わせを最優先にして返す。"""
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
    # 前回成功した組み合わせを最優先（遅れが縮んでいれば1つ新しい画像を、そうでなければ前回と同じ遅れを）
    if pref_lag in lags:
        if pref_lag - step >= 0:
            ordered.append((pref_tz, pref_lag - step))
        ordered.append((pref_tz, pref_lag))
    for tz in (pref_tz, "jst" if pref_tz == "utc" else "utc"):
        for lag in lags:
            if (tz, lag) not in ordered:
                ordered.append((tz, lag))

    out = []
    for tz, lag in ordered:
        dt = bases[tz] - timedelta(minutes=lag)
        out.append((tz, lag, _tec_build_url(dt), dt))
    return out

def _fetch_tec_image():
    """NICT準リアルタイムTECマップ(AMAP)のJPEGを、時刻候補を順に試して取得する。
    戻り値: (PIL画像 or None, 採用したURL or None, 試行ログ[str, ...])"""
    global _tec_url_pref
    attempts = []
    if not _PIL_AVAILABLE:
        return None, None, ["Pillowが未インストールです（pip install Pillow）"]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SeismoApp/5.0; research use)"}
    for tz, lag, url, dt in _tec_candidate_list():
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            if resp.status_code == 200 and resp.content:
                _tec_url_pref = {"tz": tz, "lag_min": lag,
                                 "image_time": f"{dt:%Y-%m-%d %H:%M} {'JST' if tz == 'jst' else 'UT'}"}
                attempts.append(f"200 {url}")
                return _PILImage.open(io.BytesIO(resp.content)).convert("RGB"), url, attempts
            attempts.append(f"{resp.status_code} {url}")
        except Exception as e:
            attempts.append(f"ERR {type(e).__name__} {url}")
    return None, None, attempts

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

def _tec_grid_from_json(obj):
    """TEC_JSON_URL の中身 {"cells":[{"lat","lon","v"},...]} をリスク格子 {(gi,gj): TECU} に集約する。"""
    acc = {}
    for c in (obj.get("cells") or []):
        try:
            lat, lon, v = float(c["lat"]), float(c["lon"]), float(c["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(v) and 0.0 <= v < 500.0):    # TECUとして非現実的な値は捨てる
            continue
        key = (int(math.floor(lat / RISK_GRID_SIZE)), int(math.floor(lon / RISK_GRID_SIZE)))
        if key in _RISK_CELLS:
            acc.setdefault(key, []).append(v)
    return {k: float(np.median(vs)) for k, vs in acc.items()}


def _refresh_tec_cache():
    """TECを取得して _tec_cache / _tec_history を更新する（バックグラウンド実行想定）。
    GNSSの _refresh_gnss_cache と同じく、更新中フラグで二重起動を防ぎ、失敗理由を error に残す。
    失敗しても直前の成功データ(grid/ts)は消さない。"""
    global _tec_history
    with _tec_lock:
        if _tec_cache["updating"]:
            return
        _tec_cache["updating"] = True
    try:
        grid, source, error = None, None, None
        image_url, attempts, upd_str = None, [], None
        if TEC_JSON_URL:
            try:
                r = requests.get(TEC_JSON_URL, timeout=20); r.raise_for_status()
                obj = r.json()
                grid = _tec_grid_from_json(obj)
                if grid:
                    source = "json"
                    upd_str = str(obj.get("updated") or "").strip() or None
                else:
                    error = "TEC JSONに有効なセルがありません"
            except Exception as e:
                error = f"TEC JSON取得エラー: {e}"
        elif not _PIL_AVAILABLE:
            error = "Pillowが未インストールです（requirements.txtに Pillow を追加してください）"
        elif not _tec_calibrated():
            error = "TEC_CALIBRATION が未設定です"
        else:
            img, image_url, attempts = _fetch_tec_image()
            if img is None:
                last = attempts[-1] if attempts else ""
                error = f"TEC画像を取得できませんでした（候補{len(attempts)}件すべて失敗。最後: {last}）"
            else:
                try:
                    grid = _decode_tec_grid(img)
                except Exception as e:
                    error = f"TEC画像のデコードエラー: {e}"
                if grid:
                    source = "image"
                    upd_str = f"画像 {_tec_url_pref.get('image_time', '?')}／取得 {datetime.now(JST).strftime('%H:%M')} JST"
                elif not error:
                    error = "TEC画像のデコード結果が空でした（/tec/raw_image?overlay=1 で枠位置を確認）"

        now = time.time()
        with _tec_lock:
            _tec_cache["tried"] = now
            _tec_cache["attempts"] = (attempts if len(attempts) <= 10 else
                                      attempts[:4] + [f"…({len(attempts) - 8}件省略)…"] + attempts[-4:])
            _tec_cache["image_url"] = image_url
            if grid:
                _tec_cache.update(grid=grid, ts=now, source=source, error=None,
                                  updated=upd_str or datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"))
                if not (image_url and image_url == _tec_cache.get("hist_url")):
                    _tec_history.append((now, grid))
                    _tec_cache["hist_url"] = image_url
                cutoff = now - TEC_HISTORY_MAX * TEC_FETCH_INTERVAL_SEC
                _tec_history = [(t, g) for t, g in _tec_history if t >= cutoff]
            else:
                _tec_cache["error"] = error
        if grid:
            print(f"[TEC] 更新完了 セル数:{len(grid)} 取得元:{source}")
        else:
            print(f"[TEC] {error}")
    except Exception as e:
        import traceback; traceback.print_exc()
        with _tec_lock:
            _tec_cache["tried"] = time.time()
            _tec_cache["error"] = f"TEC更新中の予期しないエラー: {e}"
    finally:
        with _tec_lock:
            _tec_cache["updating"] = False


def update_tec_cache():
    """(互換) バックグラウンドループ・手動再計算から呼ぶ同期版。実体は _refresh_tec_cache。"""
    _refresh_tec_cache()


def get_tec_grid():
    """キャッシュ済みのTECグリッドを返す（無効時・未取得時はNone）。
    キャッシュが古ければバックグラウンドで非同期更新を開始する（get_gnss_vectors と同じ方式）。
    Renderの無料枠でスリープ明けに最初のアクセスで取得が走る。"""
    if not TEC_ENABLED:
        return None
    with _tec_lock:
        stale    = (time.time() - _tec_cache["tried"]) > TEC_CACHE_SEC
        grid     = _tec_cache["grid"]
        updating = _tec_cache["updating"]
    if stale and not updating:
        threading.Thread(target=_refresh_tec_cache, daemon=True).start()
    return dict(grid) if grid else None

_tec_risk_state = {"mode": "", "why": ""}   # (v9.21) 画面に出す理由: mode=temporal/spatial/none

def _plane_residual(keys, vals):
    """値を (定数+緯度+経度) の平面で当てはめ、残差を返す。セルが少なければ中央値だけ引く。"""
    vals = np.array(vals, dtype=float)
    if len(keys) >= TEC_MIN_CELLS:
        lat = np.array([_RISK_CELLS[k][0] for k in keys], dtype=float)
        lon = np.array([_RISK_CELLS[k][1] for k in keys], dtype=float)
        A = np.column_stack([np.ones_like(lat), lat - lat.mean(), lon - lon.mean()])
        try:
            coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
            return vals - A @ coef
        except Exception:
            pass
    return vals - float(np.median(vals))

def _risk_tec_raw():
    """(v9.21) 各セルの「局所的なTEC偏差(TECU)」を返す。
    履歴が2件以上たまっていれば:
      偏差 = 現在のTEC − 直近履歴の平均 → 平面(定数+緯度+経度)を引いて日本全体の変化を除く（temporal）
    履歴がまだ足りない時（再起動・スリープ明け直後）は、履歴なしで使える代替として:
      現在のTEC分布から平面を引いた残差を使う（spatial。時間変化は見ないので精度は劣る）
    学術的に確立した地震前兆指標ではない点に注意（実験的）。"""
    st = _tec_risk_state
    get_tec_grid()   # 古ければ裏で更新を起動（今回の計算には現在のキャッシュを使う）
    with _tec_lock:
        cur = _tec_cache["grid"]
        ts = _tec_cache["ts"]
        hist = list(_tec_history)
    if not cur:
        st.update(mode="none", why="TECを取得できていません"); return {}
    if (time.time() - ts) > TEC_LIVE_MAX_STALE_SEC:
        st.update(mode="none", why="TECが古い(3時間超)"); return {}
    baseline = {}
    for _, g in hist[:-1]:
        for k, v in g.items():
            baseline.setdefault(k, []).append(v)
    keys, dev = [], []
    for k, v in cur.items():
        if k in baseline and len(baseline[k]) >= 1 and k in _RISK_CELLS:
            keys.append(k); dev.append(v - float(np.mean(baseline[k])))
    if len(keys) >= TEC_MIN_CELLS and len(hist) >= 2:
        st.update(mode="temporal", why="")
        return {k: float(abs(r)) for k, r in zip(keys, _plane_residual(keys, dev))}
    # 履歴が足りない → 空間残差で代替
    skeys = [k for k in cur if k in _RISK_CELLS]
    if len(skeys) < TEC_MIN_CELLS:
        st.update(mode="none", why="TECのセル数が不足"); return {}
    st.update(mode="spatial", why=f"履歴蓄積中({len(hist)}件)・空間偏差で代替")
    return {k: float(abs(r)) for k, r in zip(skeys, _plane_residual(skeys, [cur[k] for k in skeys]))}


def _tec_score_map(tec_raw):
    """(v9.21) 局所TEC偏差(TECU)を固定目盛り TEC_SCORE_POINTS でスコア(0〜1)にする。
    旧版は「その瞬間の全セル内の順位」だったため、平穏でも必ず一部が高スコアになった。固定目盛りならその心配がない。"""
    xs = [p[0] for p in TEC_SCORE_POINTS]
    ys = [p[1] for p in TEC_SCORE_POINTS]
    return {k: float(min(np.interp(v, xs, ys), TEC_SCORE_MAX)) for k, v in tec_raw.items()}


def render_tec(updated_str):
    nict_url = "https://aer-nc-web.nict.go.jp/iono/GEONET/latest_map.png"
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    date_str = now_jst.strftime("%Y年%m月%d日 %H:%M JST")

    grid = get_tec_grid()      # 古ければ裏で更新を起動する
    with _tec_lock:
        tec_updated = _tec_cache["updated"]
        tec_error   = _tec_cache["error"]
        tec_source  = _tec_cache["source"]

    if not grid:
        # 未取得時は、従来どおり画像埋め込み＋リンク集のフォールバック表示
        import html as _html
        reason = _html.escape(tec_error) if tec_error else "取得中です（数十秒後に再読込してください）"
        status_html = ('<span style="color:#fbbf24;font-weight:700">未取得</span> — '
                        f'{reason}。詳細は /tec/status 。'
                        '下の画像・リンクを参考値としてご利用ください。')
        cells_js = "[]"
    else:
        how = "外部JSON" if tec_source == "json" else "実験的・色逆変換"
        status_html = (f'<span style="color:#34d399;font-weight:700">取得中（{how}）</span> '
                        f'— セル数:{len(grid)} / 元データ更新: {tec_updated or "不明"}')
        cells = [{"lat": round(_RISK_CELLS[k][0], 3), "lon": round(_RISK_CELLS[k][1], 3),
                  "v": round(v, 2)} for k, v in grid.items()]
        cells_js = json.dumps(cells, ensure_ascii=False)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
#hdr{{padding:12px 20px;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0}}
#hdr h2{{font-size:15px;font-weight:700;color:#f3f4f6;margin-bottom:4px}}
#hdr p{{font-size:11px;color:rgba(235,238,245,.46)}}
#body{{flex:1;display:flex;gap:0;overflow:hidden}}
#map{{flex:1;min-width:0}}
#left{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px;overflow-y:auto}}
#right{{width:280px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-left:1px solid rgba(255,255,255,.14);padding:16px;overflow-y:auto}}

@media (max-width:768px){{
  #hdr{{padding:10px 14px}}
  #hdr p{{font-size:10px}}
  #body{{flex-direction:column;overflow-y:auto}}
  #map{{flex:none;height:42vh;min-height:220px}}
  #right{{width:100%;border-left:none;border-top:1px solid rgba(255,255,255,.14)}}
  #left{{padding:14px}}
}}
.card{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border:1px solid rgba(255,255,255,.18);border-radius:20px;padding:16px;margin-bottom:12px;width:100%;max-width:700px}}
.card h3{{font-size:13px;font-weight:700;color:#60a5fa;margin-bottom:8px}}
.card p{{font-size:12px;color:rgba(235,238,245,.62);line-height:1.7}}
.link-btn{{display:block;padding:10px 16px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
           color:#fff;font-weight:700;font-size:13px;text-decoration:none;border-radius:18px;
           text-align:center;margin-top:8px;transition:opacity 0.2s}}
.link-btn:hover{{opacity:0.85}}
.tec-img{{width:100%;border-radius:14px;border:1px solid rgba(255,255,255,.18);margin-top:8px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;
        background:rgba(56,132,255,.25);color:#93c5fd;margin-right:4px;margin-bottom:4px}}
#right h3{{font-size:13px;font-weight:700;color:#f3f4f6;margin-bottom:10px;border-bottom:1px solid rgba(255,255,255,.18);padding-bottom:6px}}
.note{{font-size:12px;color:rgba(235,238,245,.62);line-height:1.8;margin-bottom:12px}}
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
      <div id="img-err" style="display:none;padding:12px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border-radius:14px;margin-top:8px;font-size:12px;color:rgba(235,238,245,.62)">
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
    d.style.cssText = 'background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);padding:10px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:12px;color:#f3f4f6;line-height:1.8';
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

    実ファイルの形式（F5.1解で確認済み）:
      +SITE/INF ... -SITE/INF, +SOLVER/INF ... -SOLVER/INF のヘッダ部のあと
      +DATA
      *yyyy mm dd HH:MM:SS  X (m)  Y (m)  Z (m)  Lat.  Lon.  Height
       2026 01 01 12:00:00 -3.5228455513E+06 2.7771438743E+06 4.5189589316E+06 4.54E+01 1.41E+02 7.46E+01
      -DATA
    日付は「年 月 日」の3列に分かれ、4列目が時刻、5〜7列目がECEFのX,Y,Z。
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "*+-#":
            continue
        p = line.split()
        if len(p) < 7:
            continue
        try:
            d = datetime(int(p[0]), int(p[1]), int(p[2]))
            x, y, z = float(p[4]), float(p[5]), float(p[6])
        except (ValueError, IndexError):
            continue
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


# 日々の座標値の格納場所（実機のSFTPで確認済み）:
#   /data/coordinates_F5.1/<年>/<観測点ID6文字>.<年下2桁>.pos.gz   （gzip圧縮）
# 以前の "…/GPS/<年>/…" という階層は存在しない。
_GNSS_POS_DIR_CANDIDATES = ("/data/coordinates_F5.1", "/data/coordinates_F5")


def _fetch_station_pos_text(sftp, code, year):
    """指定観測点・年のposファイル本文（gzipは解凍済み）を取得する。見つからなければ(None, None)。"""
    yy = str(year)[2:]
    for base in _GNSS_POS_DIR_CANDIDATES:
        for ext in (".pos.gz", ".pos"):
            path = f"{base}/{year}/{code}.{yy}{ext}"
            try:
                with sftp.open(path, "rb") as f:
                    raw = f.read()
                if ext.endswith(".gz"):
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="ignore"), path
            except FileNotFoundError:
                continue
            except Exception as e:
                print(f"[GNSS] {code} {path} 取得エラー: {e}")
    return None, None


def _fetch_station_positions(sftp, code, lookback_days=GNSS_LOOKBACK_DAYS, full=False):
    """
    指定観測点の直近 lookback_days 日分の(date, X, Y, Z)を返す（新しい順ではなく日付昇順）。
    posファイルは1年分がまとまっているため、年をまたぐ場合は前年分も取得する。
    """
    today = datetime.now(timezone.utc).date()
    need = lookback_days + 5

    all_rows = []
    for year in (today.year, today.year - 1):
        text, _path = _fetch_station_pos_text(sftp, code, year)
        if text:
            all_rows.extend(_parse_pos_file(text))
        if not full and len(all_rows) >= need:   # (v9.00) full=Trueは履歴構築用に前年分も必ず取得
            break

    dedup = {d: (x, y, z) for d, x, y, z in all_rows}  # 同一日付は後勝ちで統一
    rows = sorted((d, x, y, z) for d, (x, y, z) in dedup.items())
    return rows if full else rows[-(lookback_days + 5):]


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
    return {"dE_mm": de, "dN_mm": dn, "dU_mm": du, "n_points": len(recent), "span_days": span,
            "last_date": recent[-1][0].strftime("%Y-%m-%d")}


def _refresh_gnss_cache():
    """GNSS_STATIONS全点について実データを取得し、_gnss_cacheを更新する（バックグラウンド実行想定）。"""
    with _gnss_lock:
        if _gnss_cache["updating"]:
            return
        _gnss_cache["updating"] = True
    try:
        if GNSS_JSON_URL:
            try:
                r = requests.get(GNSS_JSON_URL, timeout=20); r.raise_for_status()
                st = r.json().get("stations", [])
                with _gnss_lock:
                    if st:
                        _gnss_cache["data"] = st; _gnss_cache["error"] = None
                    else:
                        _gnss_cache["error"] = "GNSS JSONに観測点データがありません"
                    _gnss_cache["ts"] = time.time()
            except Exception as e:
                with _gnss_lock:
                    _gnss_cache["error"] = f"GNSS JSON取得エラー: {e}"; _gnss_cache["ts"] = time.time()
            return
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


# (v9.00) 観測点ID(070809など)を地点名(韮崎など)で表示するための対応表。
# 優先順: ①データ側のnameが地点名ならそれ ②下の上書き表 ③data/gnss_station_names.json
# ({"070809": "韮崎", ...} 形式。追加はこのファイルに書くだけで反映) ④GNSS_STATIONSの名称 ⑤ID。
GNSS_NAME_OVERRIDES = {"070809": "韮崎"}
GNSS_NAMES_FILE = os.environ.get("GNSS_NAMES_FILE", "data/gnss_station_names.json")
def _gnss_code_variants(code):
    """(v9.01) 観測点IDの表記ゆれ（先頭ゼロの有無・空白・全角数字）を吸収するための照合キー群。"""
    c = str(code if code is not None else "").strip()
    c = c.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    out = [c]
    if c.isdigit():
        if len(c) < 6: out.append(c.zfill(6))      # 70809 → 070809
        z = c.lstrip("0")
        if z and z != c: out.append(z)             # 070809 → 70809
    return out

def _gnss_serial_key(code):
    """(v9.01) 局番号の下4桁（全国で重複しない連番）。上2桁が違う表記（070809と970809）でも同じ局に当てる。"""
    c = "".join(_gnss_code_variants(code)[:1])
    return ("#" + c[-4:]) if (c.isdigit() and len(c) >= 4) else None

_gnss_names_info = {"path": None, "count": 0, "tried": [], "error": None, "logged": False}

def _gnss_names_candidates():
    here = os.path.dirname(os.path.abspath(__file__))
    names = [GNSS_NAMES_FILE, "data/gnss_station_names.json", "gnss_station_names.json"]
    out = []
    for n in names:
        if os.path.isabs(n):
            out.append(n)
        else:
            out += [os.path.abspath(n), os.path.join(here, n)]   # カレントディレクトリ基準 / app本体と同じ場所基準
    out.append("/etc/secrets/gnss_station_names.json")             # Renderの Secret Files 置き場
    seen, res = set(), []
    for p in out:
        if p not in seen: seen.add(p); res.append(p)
    return res

def _gnss_names_path():
    """名称ファイル(別ファイル)の場所。複数の候補から最初に見つかったものを使う。無ければNone。"""
    cands = _gnss_names_candidates()
    _gnss_names_info["tried"] = cands
    for p in cands:
        if os.path.isfile(p): return p
    return None

_gnss_names_cache = {"mtime": None, "built": None}

def _gnss_name_table():
    """ID→地点名の対応表（キャッシュ付き。名称ファイルが更新されたときだけ作り直す）。
    優先順位（後ろほど優先）: GNSS_STATIONS < data/gnss_station_names.json < GNSS_NAME_OVERRIDES"""
    path = _gnss_names_path()
    try:
        mt = (path, os.path.getmtime(path)) if path else None
    except Exception:
        mt = None
    if _gnss_names_cache["built"] is not None and _gnss_names_cache["mtime"] == mt:
        return _gnss_names_cache["built"]
    table = {}
    def _add(src):
        for k, v in src.items():
            v = str(v).strip()
            if not v: continue
            for kk in _gnss_code_variants(k): table[kk] = v
            sk = _gnss_serial_key(k)
            if sk: table[sk] = v
    _add({str(c): n for c, n, _la, _lo in GNSS_STATIONS})
    _gnss_names_info.update(path=path, count=0, error=None)
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                loaded = {str(k): str(v) for k, v in json.load(f).items()}
            _add(loaded)
            _gnss_names_info["count"] = len(loaded)
        except Exception as e:
            _gnss_names_info["error"] = f"{type(e).__name__}: {e}"
    if not _gnss_names_info["logged"] or _gnss_names_info["error"]:
        _gnss_names_info["logged"] = True
        if _gnss_names_info["error"]:
            print(f"[GNSS] 名称ファイルの読込エラー ({path}): {_gnss_names_info['error']}")
        elif path:
            print(f"[GNSS] 名称ファイルを読み込みました: {path} ({_gnss_names_info['count']}件)")
        else:
            print("[GNSS] 名称ファイルが見つかりません。探した場所: " + ", ".join(_gnss_names_info["tried"]))
    _add(GNSS_NAME_OVERRIDES)
    _gnss_names_cache["mtime"] = mt
    _gnss_names_cache["built"] = table
    return table

def _gnss_display_name(code, name=None):
    """観測点の表示名。nameがID（数字のみ・codeと同一）なら対応表で地点名に置き換える。
    (v9.01) 先頭ゼロの欠落・空白・全角数字の表記ゆれ、および局番号の上2桁違い(070809/970809)でも当たる。"""
    code = str(code if code is not None else "").strip()
    nm = str(name if name is not None else "").strip()
    nm_is_id = (not nm) or nm == code or nm.isdigit() or set(_gnss_code_variants(nm)) & set(_gnss_code_variants(code))
    if not nm_is_id:
        return nm
    table = _gnss_name_table()
    for k in _gnss_code_variants(code):
        if table.get(k): return table[k]
    sk = _gnss_serial_key(code)
    if sk and table.get(sk): return table[sk]
    return nm or code

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
    if data:   # (v9.00) 表示用に地点名へ置換したコピーを返す（キャッシュ本体は変えない）
        data = [dict(v, name=_gnss_display_name(v.get("code", ""), v.get("name"))) for v in data]
    return data


def _gnss_station_values():
    """(v9.06) 各観測点の現在の水平変位(mm/7日)を [(code, name, lat, lon, horiz_mm, 基準日), ...] で返す。
    座標値の公表遅れで古すぎる観測点(GNSS_LIVE_MAX_STALE_DAYS超)は除く。"""
    out = []
    vectors = get_gnss_vectors()
    today = datetime.now(timezone.utc).date()
    for v in (vectors or []):
        try:
            last = v.get("last_date") or ""
            if last:
                age = (today - datetime.strptime(last, "%Y-%m-%d").date()).days
                if age > GNSS_LIVE_MAX_STALE_DAYS:
                    continue
            out.append((v["code"], v.get("name", v["code"]), float(v["lat"]), float(v["lon"]),
                        math.hypot(float(v["dE_mm"]), float(v["dN_mm"])), last))
        except Exception:
            continue
    return out


_gnss_assign_cache = {}

def _gnss_assign_cells(stations):
    """(v9.00) 観測点の値を、半径GNSS_CELL_RADIUS_KM以内で最寄りの観測点からセルへ割り当てる。
    戻り値: (raw{cell_key: 水平変位mm}, src{cell_key: (観測点名, 距離km, 基準日)})"""
    if not stations:
        return {}, {}
    cell_keys = list(_RISK_CELLS.keys())
    pts = np.array([[st[2], st[3]] for st in stations])
    # (v9.05) 観測点の顔ぶれが同じなら(65時点でほぼ毎回そう)、距離と最寄り観測点を使い回す
    gkey = (pts.shape, hash(pts.tobytes()))
    cached = _gnss_assign_cache.get(gkey)
    if cached is None:
        cell_latlon = np.array([_RISK_CELLS[k] for k in cell_keys])
        cos_lat = np.cos(np.radians(cell_latlon[:, 0]))
        dlat = (cell_latlon[:, 0:1] - pts[:, 0][None, :]) * 111.0
        dlon = (cell_latlon[:, 1:2] - pts[:, 1][None, :]) * 111.0 * cos_lat[:, None]
        dist = np.sqrt(dlat ** 2 + dlon ** 2)
        nearest = np.argmin(dist, axis=1)
        nd = dist[np.arange(len(cell_keys)), nearest]
        if len(_gnss_assign_cache) >= 6:
            _gnss_assign_cache.clear()
        cached = _gnss_assign_cache[gkey] = (nearest, nd)
    nearest, nd = cached
    raw, src = {}, {}
    for i, k in enumerate(cell_keys):
        j = int(nearest[i])
        if nd[i] <= GNSS_CELL_RADIUS_KM:
            raw[k] = float(stations[j][4])
            src[k] = (stations[j][1], float(nd[i]), stations[j][5])
    return raw, src


def _gnss_risk_raw():
    """統合リスクマップ用のGNSS生値(現在値)。戻り値は (raw, src)。"""
    return _gnss_assign_cells(_gnss_station_values())


def _gnss_score_map(gnss_raw):
    """(v9.06) GNSS生値(水平変位mm/7日)を、固定の目盛り GNSS_SCORE_POINTS でスコア(0〜1)にする。
    過去実績との比較はしない(現在値のみ)。"""
    xs = [p[0] for p in GNSS_SCORE_POINTS]
    ys = [p[1] for p in GNSS_SCORE_POINTS]
    return {k: float(min(np.interp(v, xs, ys), RISK_SCORE_MAX)) for k, v in gnss_raw.items()}


def _gnss_comp_entry(key, gnss_rank, gnss_raw, gnss_src):
    """(v9.00) セル一覧(cells)に載せるGNSS成分。n=観測点名, d=距離km, t=座標値の基準日。"""
    s = gnss_rank.get(key)
    if s is None:
        return None
    name, dist_km, ds = gnss_src.get(key, ("", 0.0, ""))
    return {"s": round(s, 4), "r": round(gnss_raw[key], 2), "n": name, "d": int(round(dist_km)), "t": ds}


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
        badge_bg, badge_fg, badge_txt = "rgba(56,132,255,.25)", "#93c5fd", "実データ取得中…"
    else:
        gnss_updated_str = "―"
        badge_bg, badge_fg, badge_txt = "rgba(56,132,255,.25)", "#93c5fd", "プレースホルダー表示"

    vectors_json = json.dumps(vectors or [], ensure_ascii=False)
    stations_json = json.dumps([{"code": c, "name": n, "lat": la, "lon": lo} for c, n, la, lo in GNSS_STATIONS],
                               ensure_ascii=False)
    lookback_note = f"直近 {GNSS_LOOKBACK_DAYS} 日間の座標差分（短期変位ベクトル・mm単位）"
    error_html = f'<p style="color:#f87171">⚠ {gnss_error}</p>' if (GSI_GNSS_ENABLED and gnss_error) else ""
    not_configured_html = ("" if GSI_GNSS_ENABLED else
        '<p style="color:#fbbf24">SFTP未設定のためプレースホルダー表示です。'
        '環境変数 GSI_SFTP_USER / GSI_SFTP_PASS を設定すると実データ表示に切り替わります。</p>')

    _gnss_name_table()
    if _gnss_names_info["error"]:
        names_html = f'<p style="color:#f87171">⚠ 地点名ファイルの読込エラー: {_gnss_names_info["error"]}</p>'
    elif not _gnss_names_info["path"]:
        names_html = ('<p style="color:#fbbf24">地点名ファイル(gnss_station_names.json)が見つからないため、'
                      '一部の観測点はIDのまま表示されます。詳細は /gnss/status の names_file を参照。</p>')
    else:
        names_html = ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;overflow:hidden}}
#hdr{{padding:10px 16px;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
#hdr h2{{font-size:14px;font-weight:700}}
#hdr p{{font-size:11px;color:rgba(235,238,245,.46)}}
.hbadge{{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700}}
#content{{flex:1;display:flex;overflow:hidden}}
#map{{flex:1}}
#panel{{width:270px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-left:1px solid rgba(255,255,255,.14);overflow-y:auto;padding:14px}}

@media (max-width:768px){{
  #hdr{{padding:8px 12px}}
  #content{{flex-direction:column;overflow-y:auto}}
  #map{{flex:none;height:42vh;min-height:220px}}
  #panel{{width:100%;border-left:none;border-top:1px solid rgba(255,255,255,.14)}}
  #station-list{{max-height:240px}}
}}
.sec{{margin-bottom:14px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid rgba(255,255,255,.14);padding-bottom:4px}}
.sec p{{font-size:11px;color:rgba(235,238,245,.62);line-height:1.7}}
.link-btn{{display:block;padding:8px 12px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
           color:#fff;font-weight:700;font-size:12px;text-decoration:none;border-radius:14px;
           text-align:center;margin-bottom:6px;transition:opacity 0.2s}}
.link-btn:hover{{opacity:0.85}}
.phase{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border-left:3px solid #7c3aed;padding:6px 8px;border-radius:0 12px 12px 0;
        margin-bottom:6px;font-size:11px;color:rgba(235,238,245,.72)}}
#station-list{{max-height:46vh;overflow-y:auto;font-size:10px;color:rgba(235,238,245,.62);line-height:1.6}}
#station-list div{{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.14)}}
/* (v9.00) 観測点名ラベル: 地図上に常時表示（背景なし・縁取り文字で地図上でも読める） */
.gnss-label{{background:transparent!important;border:none!important;box-shadow:none!important;color:#f3f4f6;font-size:10px;font-weight:600;padding:0 2px;text-shadow:0 0 3px #000,0 0 3px #000,0 0 2px #000;pointer-events:none}}
.gnss-label:before{{display:none!important}}
#map.hide-names .gnss-label:not(.gnss-label-big){{display:none}}
/* (v9.04) 大きい変位の観測点は名称＋mmを常時表示 */
.gnss-label.gnss-label-big{{font-size:11px;font-weight:700}}
.gnss-label.gnss-label-big b{{font-weight:800}}
.gctl{{background:rgba(26,29,44,.62);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);padding:10px 12px;border-radius:16px;border:1px solid rgba(255,255,255,.18);font-size:11px;color:#f3f4f6;width:196px;line-height:1.5}}
.gctl .row{{display:flex;justify-content:space-between;align-items:baseline;gap:6px}}
.gctl .ttl{{font-weight:700;font-size:12px}}
.gctl .val{{color:#93c5fd;font-weight:700;font-variant-numeric:tabular-nums}}
.gctl input[type=range]{{width:100%;margin:6px 0 4px;accent-color:#60a5fa}}
.gctl label{{display:flex;align-items:center;gap:6px;cursor:pointer;color:rgba(240,242,247,.88)}}
.gctl label input{{width:14px;height:14px}}
.gctl small{{display:block;color:rgba(235,238,245,.5);margin-top:4px;font-size:10px;line-height:1.5}}
#gsum{{font-size:11px;color:rgba(240,242,247,.85);line-height:1.7;margin-bottom:8px;padding:6px 8px;background:rgba(255,255,255,.07);border-radius:10px}}
#gsum b{{font-variant-numeric:tabular-nums}}
.st-row{{padding:4px 0!important;cursor:pointer}}
.st-row:hover{{background:rgba(255,255,255,.06)}}
.st-head{{display:flex;align-items:baseline;gap:6px}}
.st-head .nm{{font-weight:700;color:rgba(240,242,247,.92);font-size:11px}}
.st-head .cd{{color:rgba(235,238,245,.36);font-size:9px}}
.st-head .mg{{margin-left:auto;font-weight:800;font-size:11px;font-variant-numeric:tabular-nums}}
.st-bar{{height:4px;border-radius:2px;background:rgba(255,255,255,.08);margin:3px 0 2px;overflow:hidden}}
.st-bar i{{display:block;height:100%;border-radius:2px}}
.st-sub{{color:rgba(235,238,245,.55);font-size:10px}}
.opt-row{{display:flex;align-items:center;gap:8px;font-size:12px;color:rgba(240,242,247,.85);cursor:pointer}}
.opt-row input{{width:15px;height:15px}}
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
        矢印は水平変位（東西・南北）を誇張した縮尺で表示しています（実際の変位量はmm〜cmオーダー）。<br>
        地図右上のスライダーで倍率を変えられます。「大きい変位を強調」がONの間は、変位の大きい点ほど長さをさらに上乗せして目立たせます（向きは変わりません）。<br>
        対象範囲: ETAS計算対象域（先島諸島〜北海道）内の代表{len(GNSS_STATIONS)}点。
      </p>
      {error_html}
      {not_configured_html}
      {names_html}
    </div>
    <div class="sec">
      <h3>表示設定</h3>
      <label class="opt-row"><input type="checkbox" id="chkNames" onchange="toggleNames(this.checked)">観測点名を地図上に表示</label>
    </div>
    <div class="sec">
      <h3>観測点リスト（変位の大きい順）</h3>
      <div id="gsum"></div>
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
var CONFIGURED = {stations_json};   // 設定済みの全観測点（実データ未取得時の名称表示用）


var listEl = document.getElementById('station-list');
var sumEl  = document.getElementById('gsum');
function esc(t){{ return String(t).replace(/[&<>"']/g, function(c){{ return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]; }}); }}

// (v9.00) 観測点名ラベル（パネルのチェックでON/OFF）
document.getElementById('map').classList.add('hide-names');   // 既定は非表示（チェックで表示）
var NAME_OPT = {{permanent:true, direction:'right', offset:[6,0], className:'gnss-label', opacity:1}};
var NAME_BIG = {{permanent:true, direction:'right', offset:[10,0], className:'gnss-label gnss-label-big', opacity:1}};
function toggleNames(on){{ document.getElementById('map').classList.toggle('hide-names', !on); }}

if (!LIVE) {{
  CONFIGURED.forEach(function(s){{
    L.circleMarker([s.lat, s.lon],{{radius:4,color:'#34d399',fillColor:'#34d399',fillOpacity:0.7,weight:1}})
     .bindTooltip(s.name, NAME_OPT).addTo(map);
  }});
  listEl.innerHTML = '<div style="color:rgba(235,238,245,.46)">実データ未取得のため変位は表示されません（設定済みの観測点名のみ表示）</div>';
  sumEl.style.display = 'none';
}} else {{
  // ── (v9.04) 矢印の表示設定 ─────────────────────────
  var OLD_SCALE = 400;            // v9.03までの倍率(m/mm)。倍率表示の基準
  var scaleMPerMm = 1500;         // 既定の倍率(m/mm)。旧の約3.8倍
  var emphasize = true;           // 大きい変位を強調（長さの非線形上乗せ）
  var NOISE_MM = 3, MID_MM = 7, BIG_MM = 15;
  var COLORS  = ['#8b9bb4', '#34d399', '#fbbf24', '#f87171'];   // ノイズ級 / 小 / 中 / 大
  var LV_TEXT = ['ノイズ級', '小', '中', '大'];
  var mPerDegLat = 111320.0;

  VECTORS.forEach(function(v){{ v.mag = Math.sqrt(v.dE_mm*v.dE_mm + v.dN_mm*v.dN_mm); }});
  var byMagAsc  = VECTORS.slice().sort(function(a,b){{ return a.mag - b.mag; }});   // 小→大の順に描く＝大きい点が最前面
  var byMagDesc = byMagAsc.slice().reverse();
  var MAXMAG = byMagDesc.length ? Math.max(byMagDesc[0].mag, 1) : 1;

  function level(mag){{ return mag > BIG_MM ? 3 : (mag > MID_MM ? 2 : (mag > NOISE_MM ? 1 : 0)); }}

  // 強調ON: 3mm以下は等倍、それより大きいほど長さを上乗せ（5mm→×1.0, 15mm→×1.55, 30mm→×2.0）
  function effMag(mag){{ return emphasize ? mag * Math.pow(Math.max(mag, NOISE_MM) / 5.0, 0.4) : mag; }}

  function destPoint(v){{
    var k = v.mag > 0 ? effMag(v.mag) / v.mag : 0;
    var mPerDegLon = 111320.0 * Math.cos(v.lat * Math.PI/180);
    var dxM = v.dE_mm * k * scaleMPerMm, dyM = v.dN_mm * k * scaleMPerMm;
    return [v.lat + dyM/mPerDegLat, v.lon + dxM/mPerDegLon];
  }}

  // 矢頭はピクセル基準で作る（ズームしても形が崩れない）
  function headPolygon(from, to, headPx){{
    var z = map.getZoom();
    var p0 = map.project(L.latLng(from[0], from[1]), z), p1 = map.project(L.latLng(to[0], to[1]), z);
    var dx = p1.x - p0.x, dy = p1.y - p0.y, len = Math.sqrt(dx*dx + dy*dy);
    if (len < 3) return null;
    var hs = Math.min(headPx, len * 0.7), ux = dx/len, uy = dy/len, a = 0.45;
    function wing(s){{
      var bx = -ux*Math.cos(a) + uy*s*Math.sin(a), by = -ux*s*Math.sin(a) - uy*Math.cos(a);
      var q = map.unproject(L.point(p1.x + hs*bx, p1.y + hs*by), z);
      return [q.lat, q.lng];
    }}
    return [to, wing(1), wing(-1)];
  }}

  var layer = L.layerGroup().addTo(map);
  function draw(){{
    layer.clearLayers();
    byMagAsc.forEach(function(v){{
      var lv = level(v.mag), color = COLORS[lv];
      var w  = 1.6 + Math.min(v.mag, 30) / 30 * 3.4;           // 1.6〜5px: 大きい変位ほど太く
      var op = lv === 0 ? 0.55 : (lv === 1 ? 0.82 : 1.0);       // ノイズ級は控えめに
      var dest = destPoint(v);
      var tip = esc(v.name) + '：東' + v.dE_mm.toFixed(1) + 'mm / 北' + v.dN_mm.toFixed(1) + 'mm → 水平 <b>' + v.mag.toFixed(1) + 'mm</b>（' + v.span_days + '日間）';

      if (lv >= 2) {{   // 強調リング
        L.circleMarker([v.lat, v.lon], {{radius: lv === 3 ? 12 : 9, color: color, weight: 2, opacity: 0.55, fill: false, interactive: false}}).addTo(layer);
      }}
      var label = lv >= 2
        ? '<span style="color:' + color + '">' + esc(v.name) + ' <b>' + v.mag.toFixed(1) + 'mm</b></span>'
        : esc(v.name);
      L.circleMarker([v.lat, v.lon], {{radius: 3 + lv * 0.8, color: '#05070d', weight: 1, fillColor: color, fillOpacity: 1}})
       .bindTooltip(label, lv >= 2 ? NAME_BIG : NAME_OPT).addTo(layer);

      if (v.mag > 0) {{
        L.polyline([[v.lat, v.lon], dest], {{color: '#000', weight: w + 3, opacity: 0.5, lineCap: 'round', interactive: false}}).addTo(layer);   // 縁取り
        L.polyline([[v.lat, v.lon], dest], {{color: color, weight: w, opacity: op, lineCap: 'round'}}).bindTooltip(tip).addTo(layer);
        var hp = headPolygon([v.lat, v.lon], dest, 7 + w * 2.2);
        if (hp) L.polygon(hp, {{color: '#000', weight: 1, opacity: 0.5, fillColor: color, fillOpacity: op, interactive: false}}).addTo(layer);
      }}
    }});
    updateLegend();
  }}

  // ── 右上コントロール（倍率・強調） ──────────────────
  var ctl = L.control({{position: 'topright'}});
  ctl.onAdd = function(){{
    var d = L.DomUtil.create('div', 'gctl');
    d.innerHTML =
        '<div class="row"><span class="ttl">矢印の倍率</span><span class="val" id="gScaleVal"></span></div>'
      + '<input type="range" id="gScale" min="400" max="6000" step="100" value="' + scaleMPerMm + '">'
      + '<label><input type="checkbox" id="gEmph" checked>大きい変位を強調</label>'
      + '<small id="gScaleNote"></small>';
    L.DomEvent.disableClickPropagation(d);
    L.DomEvent.disableScrollPropagation(d);
    return d;
  }};
  ctl.addTo(map);
  function refreshCtl(){{
    document.getElementById('gScaleVal').textContent = '×' + (scaleMPerMm / OLD_SCALE).toFixed(1) + '（従来比）';
    document.getElementById('gScaleNote').textContent = '1mm ＝ ' + (scaleMPerMm / 1000).toFixed(1) + 'km（強調' + (emphasize ? 'ON' : 'OFF') + '）';
  }}
  document.getElementById('gScale').addEventListener('input', function(e){{ scaleMPerMm = +e.target.value; refreshCtl(); draw(); }});
  document.getElementById('gEmph').addEventListener('change', function(e){{ emphasize = e.target.checked; refreshCtl(); draw(); }});
  map.on('zoomend', draw);   // 矢頭はピクセル基準なのでズーム後に作り直す

  // ── 観測点リスト（変位の大きい順）＋サマリー ─────────
  var nBig = byMagDesc.filter(function(v){{ return v.mag > BIG_MM; }}).length;
  var nMid = byMagDesc.filter(function(v){{ return v.mag > MID_MM; }}).length;
  sumEl.innerHTML = byMagDesc.length
    ? '最大 <b style="color:' + COLORS[level(byMagDesc[0].mag)] + '">' + byMagDesc[0].mag.toFixed(1) + 'mm</b>（' + esc(byMagDesc[0].name) + '）<br>'
      + '15mm超 <b style="color:' + COLORS[3] + '">' + nBig + '</b>点 ／ 7mm超 <b style="color:' + COLORS[2] + '">' + nMid + '</b>点 ／ 全 <b>' + byMagDesc.length + '</b>点'
    : '';
  var listHtml = '';
  byMagDesc.forEach(function(v){{
    var lv = level(v.mag), color = COLORS[lv];
    var pct = Math.max(2, Math.min(100, v.mag / Math.max(MAXMAG, BIG_MM) * 100));
    listHtml += '<div class="st-row" onclick="map.flyTo([' + v.lat + ',' + v.lon + '],8,{{duration:0.6}})">'
      + '<div class="st-head"><span class="nm">' + esc(v.name) + '</span>'
      + (/^[0-9]+$/.test(v.name) ? '' : '<span class="cd">' + esc(v.code) + '</span>')
      + '<span class="mg" style="color:' + color + '">' + v.mag.toFixed(1) + 'mm</span></div>'
      + '<div class="st-bar"><i style="width:' + pct.toFixed(0) + '%;background:' + color + '"></i></div>'
      + '<div class="st-sub">東西 ' + v.dE_mm.toFixed(1) + '　南北 ' + v.dN_mm.toFixed(1) + '　上下 ' + v.dU_mm.toFixed(1) + 'mm　(' + v.n_points + '点/' + v.span_days + '日)</div></div>';
  }});
  listEl.innerHTML = listHtml || '<div style="color:rgba(235,238,245,.46)">データなし</div>';
}}

// 凡例
var legendDiv = null;
function updateLegend(){{
  if (!legendDiv || !LIVE) return;
  var km = function(mm){{ return (effMag(mm) * scaleMPerMm / 1000); }};
  var row = function(c, w, t){{ return '<span style="display:inline-block;width:22px;height:' + w + 'px;background:' + c + ';border-radius:2px;vertical-align:middle;margin-right:6px"></span>' + t + '<br>'; }};
  legendDiv.innerHTML = '<b>GNSS変位ベクトル（水平・直近' + (VECTORS.length ? VECTORS[0].span_days : '') + '日）</b><br>'
    + row(COLORS[3], 5, '大（15mm超）　長さ ' + km(20).toFixed(0) + 'km@20mm')
    + row(COLORS[2], 4, '中（7〜15mm）')
    + row(COLORS[1], 3, '小（3〜7mm）')
    + row(COLORS[0], 2, 'ノイズ級（3mm以下）')
    + '<hr style="border-color:rgba(255,255,255,.18);margin:4px 0">'
    + '<small>10mm ＝ 地図上 ' + km(10).toFixed(1) + 'km　／　向き＝変位方向<br>'
    + (emphasize ? '大きい変位ほど長さを非線形に強調中' : '強調OFF（変位に比例）') + '</small>';
}}
var legend = L.control({{position:'bottomleft'}});
legend.onAdd = function(){{
  var d = L.DomUtil.create('div');
  d.style.cssText = 'background:rgba(26,29,44,.55);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);padding:10px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:12px;color:#f3f4f6;line-height:1.9';
  legendDiv = d;
  if (!LIVE) {{
    d.innerHTML = '<b>GNSS 電子基準点</b><br><span style="color:#34d399">●</span> GEONET基準点（仮）<br>'
      + '<hr style="border-color:rgba(255,255,255,.18);margin:4px 0"><small>実データはSFTP設定後に表示されます</small>';
  }}
  return d;
}};
legend.addTo(map);
if (LIVE) {{ refreshCtl(); draw(); }}
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

def _get_amedas():
    """(table, obs, label) を返す。取得に失敗した(空の)結果はキャッシュしない。
    失敗時に前回成功分が残っていればそれで代用する。
    (旧実装は失敗した空データを AMEDAS_CACHE_SEC の間キャッシュしていたため、
     気圧タブと統合リスクマップの気圧成分が数分間まるごと欠損していた)"""
    global _amedas_cache
    now = time.time()
    if _amedas_cache["data"] and now - _amedas_cache["ts"] < AMEDAS_CACHE_SEC:
        c = _amedas_cache["data"]
        return c["table"], c["obs"], c["label"]
    table = _fetch_amedas_table()
    obs_data, time_label = _fetch_amedas_latest()
    if table and obs_data:
        _amedas_cache = {"data": {"table": table, "obs": obs_data, "label": time_label}, "ts": now}
        return table, obs_data, time_label
    if _amedas_cache["data"]:
        c = _amedas_cache["data"]
        return c["table"], c["obs"], c["label"] + "（取得失敗のため前回分）"
    return table, obs_data, time_label

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
    table, obs_data, time_label = _get_amedas()

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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif}}
#hdr{{padding:8px 16px;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0;
       display:flex;align-items:center;gap:14px;font-size:12px;color:rgba(235,238,245,.62);flex-wrap:wrap}}
#hdr b{{color:#f3f4f6;font-size:14px}}
.stat{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);padding:4px 10px;border-radius:12px;font-size:11px;color:rgba(235,238,245,.72)}}
.stat span{{color:#60a5fa;font-weight:700}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:11px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:12px;line-height:2;color:#f3f4f6}}

@media (max-width:768px){{
  #hdr{{gap:8px;padding:6px 10px}}
  #hdr>div[style*="margin-left:auto"]{{flex-basis:100%;order:5;margin-left:0!important}}
  #lg{{left:8px;bottom:8px;right:8px;font-size:11px;padding:9px 11px;line-height:1.7}}
}}
</style></head><body>
<div id="hdr">
  <b>海面平均気圧マップ</b>
  <div class="stat">観測点数: <span>{len(markers)}</span></div>
  <div class="stat">最低: <span style="color:#c084fc">{pr_min:.1f}hPa</span></div>
  <div class="stat">最高: <span style="color:#93c5fd">{pr_max:.1f}hPa</span></div>
  <div class="stat">平均: <span>{pr_mean}hPa</span></div>
  <div style="margin-left:auto;color:rgba(235,238,245,.46)">{time_label} / 更新: {updated_str}</div>
</div>
<div id="map"></div>
<div id="lg">
  <b>海面気圧スケール</b><br>
  <div style="width:130px;height:10px;border-radius:7px;
    background:linear-gradient(to right,#b400b4,#a03cf0,#64a0ff,#c8dcff,#ffffff);margin:5px 0 2px"></div>
  <div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:rgba(235,238,245,.62)">
    <span>低({pr_min:.0f})</span><span>高({pr_max:.0f})hPa</span>
  </div>
  <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
  <small>出典: 気象庁アメダス<br>正規圧力（海面気圧）<br>{updated_str}</small>
</div>
<style>
/* ★ Bug fix: 観測点の気圧値ラベル。ズームアウト時は密集して見づらいため、
   一定のズームレベル以上でのみ表示する（updateValueLabels()で制御）。*/
.pres-val-label{{background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid rgba(255,255,255,.18);color:#f3f4f6;
  font-size:11px;font-weight:600;padding:1px 4px;border-radius:7px;white-space:nowrap;
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif}}
#hdr{{padding:8px 16px;background:rgba(255,255,255,.06);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid rgba(255,255,255,.14);flex-shrink:0;
       display:flex;align-items:center;gap:10px;font-size:12px;color:rgba(235,238,245,.62);flex-wrap:wrap}}
#hdr b{{color:#f3f4f6;font-size:14px}}
.tog{{padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer;border:none;border-radius:12px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62)}}
.tog.on{{background:rgba(10,132,255,.55);color:#fff}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
#lg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:11px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:11px;line-height:1.9;color:#f3f4f6;max-width:260px}}
#loadState{{position:absolute;top:14px;right:56px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:6px 12px;border-radius:14px;font-size:11px;color:rgba(235,238,245,.62)}}

@media (max-width:768px){{
  #hdr{{gap:6px;padding:6px 10px}}
  #hdr>div[style*="margin-left:auto"]{{flex-basis:100%;order:5;margin-left:0!important}}
  #lg{{left:8px;bottom:8px;max-width:none;right:8px;font-size:10.5px;padding:9px 11px}}
  #loadState{{right:8px;top:8px}}
}}
</style></head><body>
<div id="hdr">
  <b>活断層・プレート境界マップ</b>
  <button class="tog on" id="togFault" onclick="toggleFault(this)">活断層</button>
  <button class="tog on" id="togPlate" onclick="togglePlate(this)">プレート境界</button>
  <div style="margin-left:auto;color:rgba(235,238,245,.46)">更新: {updated_str}</div>
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
    <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
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

# 各データソースの既定の重み。選択された項目(とそのセルにある項目)だけで自動的に再正規化されるので、
# 合計は1.0でなくてよい（比率だけが意味を持つ）。レベル境界(しきい値)はこの重みから自動較正される。
# (v9.07) ETAS・b値・活断層/プレート応力は同じ地震カタログから作られ相関が強い。ETASへの一極集中を和らげ、
#         地震活動とは独立な観測であるGNSSを手厚くした。標準プリセットの比は
#         ETAS 0.45 : b値 0.15 : 活断層 0.15 : プレート 0.10 (+ GNSSが使えるセルはGNSS 0.20)。
RISK_DEFAULT_WEIGHTS = {
    "etas":     0.45,
    "bvalue":   0.15,
    "fault":    0.15,
    "plate":    0.10,
    "pressure": 0.05,
    "tec":      0.08,   # (v9.21) 学術的に未確立のため低め。さらに「他成分を超えた分だけ押し上げ」扱い(GNSSと同じ)
    "gnss":     0.20,   # (v9.07) GNSS変位(現在値)。基準(他成分)を超えた分だけ総合を押し上げる扱い
}
RISK_LABELS = {"etas": "ETAS", "bvalue": "b値", "fault": "活断層",
               "plate": "プレート境界", "pressure": "気圧", "tec": "TEC(電離圏・実験的)",
               "gnss": "GNSS変位"}

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

_nearest_cell_cache = {}
def _nearest_cell_idx(pts):
    """(v9.05) 断層/プレート境界のサンプル点 → 最寄りのリスク格子セル(_RISK_CELLS内の番号)。
    点群は(断層データの再取得が無い限り)毎回同じなので、結果をキャッシュして使い回す。"""
    key = (pts.shape, hash(pts.tobytes()))
    hit = _nearest_cell_cache.get(key)
    if hit is not None:
        return hit
    cell_keys = list(_RISK_CELLS.keys())
    cell_latlon = np.array([_RISK_CELLS[k] for k in cell_keys])
    cos_lat = np.cos(np.radians(cell_latlon[:, 0]))
    n_pts = len(pts)
    idx = np.empty(n_pts, dtype=int)
    chunk = 2500
    for start in range(0, n_pts, chunk):
        batch = pts[start:start + chunk]
        dlat = (cell_latlon[:, 0:1] - batch[:, 0][None, :]) * 111.0
        dlon = (cell_latlon[:, 1:2] - batch[:, 1][None, :]) * 111.0 * cos_lat[:, None]
        dist = np.sqrt(dlat ** 2 + dlon ** 2)
        idx[start:start + chunk] = dist.argmin(axis=0)
    if len(_nearest_cell_cache) >= 6:
        _nearest_cell_cache.clear()
    _nearest_cell_cache[key] = idx
    return idx

def _load_to_grid_max(points_latlon, loads):
    """断層/プレート境界のサンプル点ごとの応力負荷を、最も近いリスク格子セルへ
    割り当てる。1セルに複数の点が対応する場合はその中の最大値をセルの代表値とする
    （＝そのセル付近を通る断層/プレート境界のうち最も負荷が高いセグメントを表す）。"""
    n_pts = len(points_latlon)
    if n_pts == 0:
        return {}
    return _load_to_grid_max_multi(points_latlon, np.asarray(loads, dtype=float).reshape(n_pts, 1))[0]

def _load_to_grid_max_multi(points_latlon, loads_mat):
    """(v9.05) loads_mat: (点数, 時点数)。時点ごとに {セルkey: 最大負荷} のdictを返す。"""
    n_pts = len(points_latlon)
    T = loads_mat.shape[1]
    if n_pts == 0:
        return [{} for _ in range(T)]
    cell_keys = list(_RISK_CELLS.keys())
    nearest = _nearest_cell_idx(np.array(points_latlon))
    order = np.argsort(nearest, kind="stable")
    sidx = nearest[order]
    starts = np.concatenate(([0], np.nonzero(np.diff(sidx))[0] + 1))
    gmax = np.maximum.reduceat(loads_mat[order], starts, axis=0)      # (セル数G, T)
    cells = [cell_keys[c] for c in sidx[starts]]
    return [{cells[g]: float(gmax[g, t]) for g in range(len(cells))} for t in range(T)]

def _fault_plate_loads_multi(points_latlon, qa, samples, window_days=60.0):
    """(v9.05) 複数の基準時刻について、断層/プレート境界点の応力負荷をまとめて求める。
    応力負荷 = Σ (M0/M0_ref) / r^3 × 時間減衰 の「距離項」は基準時刻に依存しないので、
    (点×地震)の距離行列は1回だけ作り、時間減衰の重み(地震×時点)との行列積で全時点を一度に出す。
    各時点で使う地震(直近window_days日・MAX_QUAKES件まで直近優先)は従来と同じ。
    samples: [(ref_epoch, lo, hi), ...]  戻り値: (点数, 時点数) のndarray"""
    n_pts = len(points_latlon); T = len(samples)
    out = np.zeros((n_pts, T))
    nq = len(qa["t"])
    if n_pts == 0 or nq == 0 or T == 0:
        return out
    TW = np.zeros((nq, T))
    for ti, (rt_e, lo, hi) in enumerate(samples):
        dt = (rt_e - qa["t"][lo:hi]) / 86400.0
        idx = np.nonzero((dt >= 0) & (dt <= min(window_days, FSP.MAX_LOOKBACK_DAYS)))[0]
        if len(idx) > FSP.MAX_QUAKES:
            idx = idx[np.argsort(dt[idx])[:FSP.MAX_QUAKES]]
        TW[lo + idx, ti] = (dt[idx] + FSP.TIME_C) ** (-FSP.TIME_P)
    used = np.nonzero(TW.any(axis=1))[0]            # どの時点でも使われない地震は除外
    if len(used) == 0:
        return out
    TW = TW[used]
    qlat = qa["lat"][used]; qlon = qa["lon"][used]; qdepth = qa["depth_load"][used]; qmag = qa["mag"][used]
    m0_ref = 10.0 ** (1.5 * FSP.M0_REF_MAG + 9.05)
    moment_ratio = (10.0 ** (1.5 * qmag + 9.05)) / m0_ref
    r_min = np.maximum((10.0 ** (-2.44 + 0.59 * qmag)) / 2.0, FSP.R_MIN_FLOOR_KM)   # Wells & Coppersmith 1994
    pts = np.array(points_latlon)
    cos_lat = np.cos(np.radians(pts[:, 0]))
    pc = max(8, min(FSP.POINT_CHUNK, 1_000_000 // max(len(qlat), 1)))   # ピークメモリを固定(約8MB×数本)
    for ps in range(0, n_pts, pc):
        pb = pts[ps:ps + pc]
        dlat = (pb[:, 0:1] - qlat[None, :]) * 111.0
        dlon = (pb[:, 1:2] - qlon[None, :]) * 111.0 * cos_lat[ps:ps + pc, None]
        r = np.sqrt(dlat ** 2 + dlon ** 2 + qdepth[None, :] ** 2)
        r = np.maximum(r, r_min[None, :])
        out[ps:ps + pc] = (moment_ratio[None, :] / (r ** 3)) @ TW
    return out

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

_RISK_CELL_LOOKUP = None
def _risk_cell_lookup():
    """(v9.05) リスク格子(gi,gj) → _RISK_CELLS 内の通し番号 を引く表（初回に1度だけ作る）。"""
    global _RISK_CELL_LOOKUP
    if _RISK_CELL_LOOKUP is None:
        keys = list(_RISK_CELLS.keys())
        gis = [k[0] for k in keys]; gjs = [k[1] for k in keys]
        gi0, gj0 = min(gis), min(gjs)
        tab = np.full((max(gis) - gi0 + 1, max(gjs) - gj0 + 1), -1, dtype=np.int64)
        for n, (gi, gj) in enumerate(keys):
            tab[gi - gi0, gj - gj0] = n
        _RISK_CELL_LOOKUP = (keys, gi0, gj0, tab)
    return _RISK_CELL_LOOKUP

def _risk_etas_raw(etas_grid_scores):
    """ETASの細かい格子(GRID_SIZE)を統合リスク格子(RISK_GRID_SIZE)に集約(合算)する。
    (v9.05) numpyで一括集計（結果は従来と同じ）。"""
    if not etas_grid_scores:
        return {}
    keys, gi0, gj0, tab = _risk_cell_lookup()
    fk = np.array(list(etas_grid_scores.keys()), dtype=np.int64)
    vals = np.fromiter(etas_grid_scores.values(), dtype=float, count=len(fk))
    lat = fk[:, 0] * GRID_SIZE; lon = fk[:, 1] * GRID_SIZE
    ii = np.floor(lat / RISK_GRID_SIZE).astype(np.int64) - gi0
    jj = np.floor(lon / RISK_GRID_SIZE).astype(np.int64) - gj0
    inb = (ii >= 0) & (ii < tab.shape[0]) & (jj >= 0) & (jj < tab.shape[1])
    ci = np.full(len(fk), -1, dtype=np.int64)
    ci[inb] = tab[ii[inb], jj[inb]]
    sel = ci >= 0
    if not sel.any():
        return {}
    sums = np.bincount(ci[sel], weights=vals[sel], minlength=len(keys))
    cnt = np.bincount(ci[sel], minlength=len(keys))
    return {keys[c]: float(sums[c]) for c in np.nonzero(cnt)[0]}

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
    table, obs_data, _label = _get_amedas()

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
    # (v8.21) 絶対基準(過去65日のセル別実績)が未完成のときは相対評価で代用せず、
    # 表示を保留する（相対評価は最上位セルが必ず100になるため）。
    if not _get_historical_calibration().get("risk_thresholds"):
        return []
    etas_raw     = _risk_etas_raw(etas_grid_scores)
    bvalue_raw   = _risk_bvalue_raw(bvalue_grid)
    fault_raw    = get_fault_stress_grid(quakes)
    plate_raw    = get_plate_stress_grid(quakes)
    pressure_raw = _risk_pressure_raw()
    tec_raw      = _risk_tec_raw()
    gnss_raw, gnss_src = _gnss_risk_raw()   # (v9.06) 現在のGNSS変位(過去値は使わない)

    # ── ハイブリッド方式 ──────────────────────────────
    # 「今この瞬間の空間内相対順位」だけでなく、「アーカイブ(過去65日間)の
    # 実績分布と比べても稀な値か」「ドメイン知識から見て意味のある水準か」
    # も併せて満たす場合にのみスコアが高くなるようにする。
    # 気圧偏差・TECは過去分の履歴を保持していないため絶対評価ができず、
    # 単独でLv4/5の主因にならないよう固定の上限(PRESSURE_TEC_SCORE_CAP)を課す。
    calib = _get_historical_calibration()
    # (v7.56) セル単位の絶対評価プール(*_cell)を使用。ETAS/断層/プレートは、
    # そのセル自身の平常時中央値に対する倍率フィルタ(_make_etas_domain_floor /
    # _make_stress_domain_floor)を適用し、「元々活発な/近い地域だから高スコア」
    # ではなく「自セルの平常値から逸脱したから高スコア」という真の絶対評価にする。
    etas_rank     = _absolute_rank_map(etas_raw, calib.get("etas_cell"), invert=False, log_transform=True,
                                      domain_floor_fn=_make_etas_domain_floor(_cellwise_median_map(calib.get("etas_cell"))))
    bvalue_rank   = _absolute_rank_map(bvalue_raw, calib.get("bvalue_cell"), invert=True,
                                      log_transform=False, domain_floor_fn=_bvalue_domain_floor)
    fault_rank    = _absolute_rank_map(fault_raw, calib.get("fault_cell"), invert=False, log_transform=False,
                                      domain_floor_fn=_make_stress_domain_floor(_cellwise_median_map(calib.get("fault_cell"))))
    plate_rank    = _absolute_rank_map(plate_raw, calib.get("plate_cell"), invert=False, log_transform=False,
                                      domain_floor_fn=_make_stress_domain_floor(_cellwise_median_map(calib.get("plate_cell"))))
    gnss_rank     = _gnss_score_map(gnss_raw)   # (v9.06) 現在値のみ・固定目盛りでスコア化
    pressure_rank = {k: min(v, PRESSURE_TEC_SCORE_CAP)
                      for k, v in _percentile_rank_map(pressure_raw, invert=False).items()}
    tec_rank      = _tec_score_map(tec_raw)   # (v9.21) 局所偏差(TECU)を固定目盛りでスコア化

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
        _g = _gnss_comp_entry(key, gnss_rank, gnss_raw, gnss_src)
        if _g:
            comp["gnss"] = _g
        if key in pressure_rank:
            comp["pressure"] = {"s": round(pressure_rank[key], 4), "r": round(pressure_raw[key], 2)}
        if key in tec_rank:
            comp["tec"] = {"s": round(tec_rank[key], 4), "r": round(tec_raw[key], 2)}
        # 過去履歴に基づく成分(ETAS/b値/活断層/プレート)が1つも無いセルは、絶対評価が
        # できないため出さない（気圧・TECだけでは表示しない）
        if any(k in comp for k in _RISK_HIST_KEYS):
            cells.append({"lat": round(lat, 3), "lon": round(lon, 3), "c": comp})
    return cells


# ══════════════════════════════════════════════════════
# アーカイブタブ: 過去の任意時点における統合リスクマップの再計算
# ══════════════════════════════════════════════════════
# 通常の統合リスクマップ(tab: riskmap)は「現在」を基準にETAS/応力負荷を計算するが、
# こちらは指定された過去の日時(ref_time)を基準に、その1か月前までの地震データだけを
# 使って同様のマップを再現する。GNSS変位(v9.00)は保持している日次履歴から基準日の値を使う。
# 気圧偏差(pressure)とTEC(tec)は過去分のデータを
# 保持していないため、この再計算には含めない（選択項目から欠けても重み再正規化で
# 自動的に無視される、という既存のフロントエンドの仕組みをそのまま利用する）。
def _historical_fault_plate_raw(quakes, ref_time):
    """過去時点(ref_time)を基準に、活断層・プレート境界への応力負荷を計算する。
    通常版(get_fault_stress_grid/get_plate_stress_grid)は「現在」基準の結果を
    キャッシュして使い回す設計のため、ここでは流用せず都度計算する
    （アーカイブ検索は頻繁には呼ばれない想定のため、キャッシュ無しでも許容できる）。"""
    fault_data = get_japan_active_faults()
    fpts = _decimate_points(_flatten_line_points(fault_data), FSP.MAX_POINTS)
    fault_raw = _load_to_grid_max(fpts, _fault_plate_load_at_points(fpts, quakes, ref_time=ref_time))

    plate_data = get_plate_boundaries()
    ppts = _decimate_points(_flatten_line_points(plate_data, bbox=FAULTS_BBOX), FSP.MAX_POINTS)
    plate_raw = _load_to_grid_max(ppts, _fault_plate_load_at_points(ppts, quakes, ref_time=ref_time))

    return fault_raw, plate_raw

def compute_risk_grid_historical(quakes, ref_time, calib=None):
    """過去時点(ref_time)における統合リスクマップ用のセル一覧を返す。
    quakesは事前に [ref_time - 1か月, ref_time] の範囲でフィルタ済みのものを渡す。
    (v9.08) calib を渡すと、その基準(日付ごとに作り直したもの)で評価する。省略時は現在の基準。"""
    etas_grid_scores = analyze_etas(quakes, ref_time=ref_time)
    bvalue_grid = compute_bvalue_grid(quakes)
    fault_raw, plate_raw = _historical_fault_plate_raw(quakes, ref_time)

    etas_raw   = _risk_etas_raw(etas_grid_scores)
    bvalue_raw = _risk_bvalue_raw(bvalue_grid)

    # 通常版(compute_risk_grid)と同じハイブリッド方式（相対順位×過去実績との
    # 絶対比較×ドメイン知識）を適用し、アーカイブ表示と現在表示でレベルの
    # 意味がぶれないようにする。
    calib = calib or _get_historical_calibration()
    # (v7.56) 通常版(compute_risk_grid)と同じくセル単位絶対評価プールを使用。
    etas_rank   = _absolute_rank_map(etas_raw, calib.get("etas_cell"), invert=False, log_transform=True,
                                    domain_floor_fn=_make_etas_domain_floor(_cellwise_median_map(calib.get("etas_cell"))))
    bvalue_rank = _absolute_rank_map(bvalue_raw, calib.get("bvalue_cell"), invert=True,
                                    log_transform=False, domain_floor_fn=_bvalue_domain_floor)
    fault_rank  = _absolute_rank_map(fault_raw, calib.get("fault_cell"), invert=False, log_transform=False,
                                    domain_floor_fn=_make_stress_domain_floor(_cellwise_median_map(calib.get("fault_cell"))))
    plate_rank  = _absolute_rank_map(plate_raw, calib.get("plate_cell"), invert=False, log_transform=False,
                                    domain_floor_fn=_make_stress_domain_floor(_cellwise_median_map(calib.get("plate_cell"))))

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
    # (v8.21) マップ本体と同じ「そのセル自身の過去実績に対する絶対評価」に統一する
    # (旧: 全セル間の相対順位。最上位セルが常に100になる原因の一つだった)。
    calib = _get_historical_calibration()
    key = (gi, gj)
    fault_score = plate_score = None
    if "fault" in selected or "plate" in selected:
        quakes_now = load_quakes()
    if "fault" in selected:
        fv = get_fault_stress_grid(quakes_now).get(key)
        if fv is not None:
            fault_score = _abs_score_single(key, fv, calib.get("fault_cell"), False, False, _make_stress_domain_floor)
    if "plate" in selected:
        pv = get_plate_stress_grid(quakes_now).get(key)
        if pv is not None:
            plate_score = _abs_score_single(key, pv, calib.get("plate_cell"), False, False, _make_stress_domain_floor)

    fnames = list_snapshots()  # 新しい順
    fnames = list(reversed(fnames[:RISK_TREND_MAX_POINTS]))  # 古い→新しい、直近分のみ

    points = []
    for fname in fnames:
        snap = load_snapshot(fname)
        if snap is None:
            continue
        comp = {}
        if "etas" in selected:
            ev = _risk_etas_raw(snap["etas"]).get(key)
            if ev is not None:
                s = _abs_score_single(key, ev, calib.get("etas_cell"), False, True, _make_etas_domain_floor)
                if s is not None:
                    comp["etas"] = s
        if "bvalue" in selected:
            bv = _risk_bvalue_raw(snap["bvalue"]).get(key)
            if bv is not None:
                s = _abs_score_single(key, bv, calib.get("bvalue_cell"), True, False,
                                      lambda med: _bvalue_domain_floor)
                if s is not None:
                    comp["bvalue"] = s
        if fault_score is not None:
            comp["fault"] = fault_score
        if plate_score is not None:
            comp["plate"] = plate_score
        if not comp:
            continue
        sel_hist = [k for k in selected if k in _RISK_HIST_KEYS]
        wsum = sum(RISK_DEFAULT_WEIGHTS[k] for k in sel_hist)
        score = sum(RISK_DEFAULT_WEIGHTS[k] * comp.get(k, RISK_NEUTRAL_SCORE) for k in sel_hist) / wsum
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

# ══════════════════════════════════════════════════════
# 検証: M6以上バックテスト(Molchan図) と 日付ごとの基準の作り直し (v9.08)
# ──────────────────────────────────────────────────────
# ■ 目的
#   統合リスクマップが「M6以上の震源を、面積の何%の高リスクセルに集められているか」を、
#   気象庁一元化カタログ(data/jma_catalog/)の過去のM6以上すべてについて自動で測る。
#   これまで人力でアーカイブタブを使って記録していた作業（直前・直後のリスク値）の自動化。
#
# ■ 評価の手順（1地震ごと）
#   1) 発生の lead_hours 前(既定24時間前)を評価時点とし、その時点までの地震だけで
#      アーカイブと同じ計算（ETAS/b値/活断層/プレート → 絶対評価 → 合成）を行う。
#   2) 震源セルの「順位」τ = そのセルより高スコアのセルの面積比（上位τ。0に近いほど良い）。
#      面積はセルの緯度による縮み(cos)で重み付け。スコアの無いセルは「警戒できない」扱いで
#      面積には数える（甘くならないように）。
#   3) 周囲(±BT_NEAR_RADIUS_CELLS セル)の最大レベル／最大スコアでの順位も記録する。
#   4) レベルごとの高リスク面積比（Lv>=1〜5 のセルが占める面積の割合）も記録する。
#   5) 発生 after_hours 後(既定1時間後)のスコアも「参考用」として記録する。
#   6) 比較用に、静的な長期ハザードマップ（その時点から過去 BT_STATIC_LOOKBACK_YEARS 年の
#      M>=BT_STATIC_MIN_MAG の地震分布を平滑化したもの／それに活断層への近さを加えたもの）でも
#      同じ順位τを出す。評価時点より後の地震は一切使わない。
#   Molchan図: 横軸=警戒した面積の割合τ、縦軸=外れ率(τ以内に入らなかった地震の割合)。
#   対角線(ランダム)より下にあるほど良い。
#
# ■ 余震の分離
#   発生前に近くでM>=aftershock_prev_mag の大地震があった例を分ける。
#     aftershock : 評価時点より前にあった(マップが知っていた) → 余震・続発
#     unseen     : 評価時点と発生の間にあった(マップは知らない) → 直前の前震など
#     independent: 近くに大地震なし
#   「近く・直前」の範囲は大地震の規模から決める(_bt_seq_window)。
#
# ■ 日付ごとの基準の作り直し
#   通常の基準(_hist_calib_cache)は「現在から過去65日」で作る。古い日付の評価に使うと、
#   その日付より未来の活動で基準が歪む（先読み）。ここでは基準日(anchor)より前の65日だけで
#   基準(セル別プール・しきい値)を作り直す。anchorは評価時点を rebuild_days 日の格子に
#   切り下げたもの(0なら1地震ごとに作り直す)。基準の作成は重いので、同じanchorの地震で共有する。
# ══════════════════════════════════════════════════════
BACKTEST_DIR = "data/backtest"
BT_WINDOW_DAYS = 60.0          # 較正(_compute_historical_calibration)の窓と同じ。変えるなら較正側も合わせること
BT_STD_KEY = _risk_subset_key(_RISK_STD_KEYS)
BT_NEAR_RADIUS_CELLS = 1       # 「周囲」= 震源セルから±1セル(約±55km)
# 余震判定: 前の大地震の規模Mpから「近く(km)」「直前(日)」を決める
BT_SEQ_MIN_RADIUS_KM = 50.0
BT_SEQ_MAX_RADIUS_KM = 400.0
BT_SEQ_MIN_DAYS      = 30.0
BT_SEQ_MAX_DAYS      = 365.0
# 静的ハザードマップ
BT_STATIC_MIN_MAG        = 4.5
BT_STATIC_LOOKBACK_YEARS = 10
BT_STATIC_SIGMA_CELLS    = 1.0
BT_STATIC_W_SEIS         = 0.7   # 「地震分布＋活断層」版の重み(順位の加重平均)
BT_STATIC_W_FAULT        = 0.3
BT_TAU_GRID = [i / 100.0 for i in range(101)]
BT_GROUP_LABELS = {"all": "全体", "independent": "独立(近くに大地震なし)",
                   "aftershock": "余震・続発(評価時点で既知の大地震の後)",
                   "unseen": "直前に発生(評価時点では未発生の前震等)"}
BT_MODEL_LABELS = {"dyn": "動的(統合リスクマップ)", "st_seis": "静的(地震分布のみ)",
                   "st_comb": "静的(地震分布＋活断層)"}

_BT_KEYS  = list(_RISK_CELLS.keys())
_BT_IDX   = {k: i for i, k in enumerate(_BT_KEYS)}
_BT_N     = len(_BT_KEYS)
_BT_W     = np.array([math.cos(math.radians(_RISK_CELLS[k][0])) for k in _BT_KEYS])
_BT_WTOT  = float(_BT_W.sum())
_BT_GI0   = min(k[0] for k in _BT_KEYS)
_BT_GJ0   = min(k[1] for k in _BT_KEYS)
_BT_CI    = np.array([k[0] - _BT_GI0 for k in _BT_KEYS], dtype=np.int64)
_BT_CJ    = np.array([k[1] - _BT_GJ0 for k in _BT_KEYS], dtype=np.int64)
_BT_SHAPE = (int(_BT_CI.max()) + 1, int(_BT_CJ.max()) + 1)


# ── 小物 ───────────────────────────────────────────
def _bt_cell_index(lat, lon):
    """震源の緯度経度 → _RISK_CELLS 内の通し番号。対象外(領域外)は None。"""
    return _BT_IDX.get((int(math.floor(lat / RISK_GRID_SIZE)), int(math.floor(lon / RISK_GRID_SIZE))))

def _bt_dist_km(a, b):
    dlat = (a["lat"] - b["lat"]) * 111.0
    dlon = (a["lon"] - b["lon"]) * 111.0 * math.cos(math.radians((a["lat"] + b["lat"]) / 2.0))
    return math.hypot(dlat, dlon)

def _bt_seq_window(mag_prev):
    """前の大地震(規模mag_prev)の『近く(km)』と『直前(日)』。
    近く: 断層長(Wells&Coppersmith)の2倍を BT_SEQ_MIN〜MAX_RADIUS_KM に収めたもの。
    直前: M6で30日、規模が1上がるごとに約3.2倍、BT_SEQ_MIN〜MAX_DAYS に収めたもの。"""
    L = 10.0 ** (-2.44 + 0.59 * mag_prev)
    r = min(max(2.0 * L, BT_SEQ_MIN_RADIUS_KM), BT_SEQ_MAX_RADIUS_KM)
    d = min(max(BT_SEQ_MIN_DAYS * 10.0 ** (0.5 * (mag_prev - 6.0)), BT_SEQ_MIN_DAYS), BT_SEQ_MAX_DAYS)
    return r, d

def _bt_pct_rank(x):
    """ベクトルxの順位(0〜1、大きい値ほど1に近い。同値は平均順位)。"""
    x = np.asarray(x, dtype=float)
    u, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    below = np.cumsum(cnt) - cnt
    avg = below + (cnt - 1) / 2.0
    return (avg[inv] + 0.5) / len(x)

def _bt_tau(vec, idx):
    """セルidxの順位τ = (それより高いセルの面積 + 同点の面積/2) / 全面積。小さいほど『上位』。
    スコアが無い(NaN)セルは警戒できないので τ=1.0。"""
    s = vec[idx]
    if np.isnan(s):
        return 1.0
    ok = ~np.isnan(vec)
    above = _BT_W[ok & (vec > s)].sum()
    tie = _BT_W[ok & (vec == s)].sum()
    return float((above + 0.5 * tie) / _BT_WTOT)

def _bt_maxfilter(vec, r):
    """各セルについて、±rセル四方の最大値(NaNは無視。全部NaNならNaN)。
    『しきい値以上のセルを周囲r個ぶん広げた領域』= 『最大値フィルタ後がしきい値以上』なので、
    周囲を含めた面積・順位をそのまま計算できる。"""
    H, W = _BT_SHAPE
    grid = np.full((H + 2 * r, W + 2 * r), -np.inf)
    grid[_BT_CI + r, _BT_CJ + r] = np.where(np.isnan(vec), -np.inf, vec)
    out = np.full((H, W), -np.inf)
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            out = np.maximum(out, grid[r + di:r + di + H, r + dj:r + dj + W])
    res = out[_BT_CI, _BT_CJ]
    return np.where(np.isinf(res), np.nan, res)

def _bt_levels(score_vec, th):
    lv = np.zeros(len(score_vec), dtype=int)
    with np.errstate(invalid="ignore"):
        for t in th:
            lv += (score_vec >= t)
    return lv

def _bt_binom_sf(k, n, p):
    """二項分布 P(X>=k | n, p)。ランダムな警戒面積pで、n個中k個以上が入る確率。"""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lp, lq = math.log(p), math.log(1.0 - p)
    lg_n = math.lgamma(n + 1)
    s = 0.0
    for i in range(k, n + 1):
        s += math.exp(lg_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, s)

def _bt_jst(dt):
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


# ── 日本の一元化カタログの収録範囲 ────────────────────
def _jma_catalog_coverage():
    """年別ファイルがある年と、収録期間(最初・最後の記録時刻)を返す。無ければ None。"""
    years = [y for y in range(1923, datetime.now(timezone.utc).year + 1) if _jma_catalog_find(y)]
    if not years:
        return None
    first = _jma_catalog_span(_jma_catalog_find(years[0]))[0]
    last = _jma_catalog_span(_jma_catalog_find(years[-1]))[1]
    if first is None or last is None:
        return None
    return {"first": first, "last": last, "years": years,
            "contiguous": years == list(range(years[0], years[-1] + 1))}

def _fetch_catalog_strict(start_utc, end_utc, min_mag=None):
    """一元化カタログから読む。期間を覆っていなければ例外（USGSへは逃げない。
    基準と評価で地震の検知下限が混ざると、b値やETASの絶対評価が歪むため）。"""
    qs = fetch_quakes_jma_catalog_range(start_utc, end_utc, min_mag=min_mag)
    if qs is None:
        raise RuntimeError(f"一元化カタログ(data/jma_catalog/)が {start_utc.astimezone(JST):%Y-%m-%d}〜"
                           f"{end_utc.astimezone(JST):%Y-%m-%d} を覆っていません")
    return qs


# ── 日付ごとの基準（アーカイブタブの「基準を作り直す」用の小さなキャッシュ）────────
_date_calib_cache = {}          # key(JST日付) -> 較正結果
_date_calib_state = {"key": None, "error": None, "started": 0.0, "running": False}
_date_calib_lock = threading.Lock()
DATE_CALIB_KEEP = 3

def _date_calib_anchor(ref_utc):
    """基準日時の属するJST日付の0時(UTC)。基準日時以前なので先読みにならない。"""
    j = ref_utc.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return j.astimezone(timezone.utc)

def get_calibration_at(ref_utc):
    """基準日(ref_utcのJST日付の0時)より前の65日だけで作った基準を返す。
    未作成なら背景スレッドで作り始め、(None, 状態) を返す。作成に失敗したら (None, {"error":...})。"""
    anchor = _date_calib_anchor(ref_utc)
    key = anchor.astimezone(JST).strftime("%Y%m%d")
    with _date_calib_lock:
        hit = _date_calib_cache.get(key)
        if hit is not None:
            return hit, {"key": key}
        st = _date_calib_state
        if st["running"] and time.time() - st["started"] < 1800:
            return None, {"pending": True, "key": st["key"], "same": st["key"] == key}
        if st["key"] == key and st["error"]:
            err = st["error"]
            st["key"], st["error"] = None, None     # 次回は再試行できるように
            return None, {"error": err}
        st.update(key=key, error=None, started=time.time(), running=True)

    def _job():
        try:
            lo = anchor - timedelta(days=HIST_CALIB_LOOKBACK_DAYS + BT_WINDOW_DAYS)
            qs = _fetch_catalog_strict(lo, anchor)
            calib = _compute_historical_calibration(end_utc=anchor, quakes=qs)
            if not calib.get("risk_thresholds"):
                raise RuntimeError(f"基準のサンプルが不足しています（{calib.get('n_samples', 0)}時点）")
            calib["anchor_jst"] = _bt_jst(anchor)
            with _date_calib_lock:
                _date_calib_cache[key] = calib
                while len(_date_calib_cache) > DATE_CALIB_KEEP:
                    _date_calib_cache.pop(next(iter(_date_calib_cache)))
                _date_calib_state.update(key=None, running=False)
        except Exception as e:
            import traceback; traceback.print_exc()
            with _date_calib_lock:
                _date_calib_state.update(error=str(e) or e.__class__.__name__, running=False)
    threading.Thread(target=_job, daemon=True).start()
    return None, {"pending": True, "key": key, "same": True}


# ── 地震(M6以上)の一覧と、余震の分類 ──────────────────
def _bt_load_events(start_utc, end_utc, min_mag, prev_mag):
    """一元化カタログから M>=min(min_mag, prev_mag) の地震を時系列で返す。
    余震判定のため、start より BT_SEQ_MAX_DAYS 日前から読む。"""
    cov = _jma_catalog_coverage()
    if not cov:
        raise RuntimeError("一元化カタログ(data/jma_catalog/ の年別ファイル)が見つかりません")
    if not cov["contiguous"]:
        raise RuntimeError(f"年別ファイルが連続していません（{cov['years'][0]}〜{cov['years'][-1]} の欠けを確認してください）")
    lo = max(start_utc - timedelta(days=BT_SEQ_MAX_DAYS), cov["first"])
    hi = min(end_utc, cov["last"])
    if lo >= hi:
        raise RuntimeError("指定期間がカタログの収録期間外です")
    qs = _fetch_catalog_strict(lo, hi, min_mag=min(min_mag, prev_mag))
    events = []
    for q in qs:
        t = datetime.fromisoformat(q["time"].replace("Z", "+00:00"))
        events.append({"t": t, "lat": q["lat"], "lon": q["lon"], "mag": q["mag"], "depth": q.get("depth", 0.0),
                       "place": q.get("place", ""),
                       "id": f"{t.strftime('%Y%m%d%H%M%S')}_{q['lat']:.2f}_{q['lon']:.2f}"})
    events.sort(key=lambda e: e["t"])
    return events, cov

def _bt_predecessors(ev, big):
    """ev の『近く・直前』にあった大地震(big: M>=prev_mag、時系列)の一覧。"""
    out = []
    for p in big:
        if p["t"] >= ev["t"]:
            break
        r, d = _bt_seq_window(p["mag"])
        gap = (ev["t"] - p["t"]).total_seconds() / 86400.0
        if gap > d:
            continue
        dist = _bt_dist_km(ev, p)
        if dist > r:
            continue
        out.append({"t": p["t"], "mag": p["mag"], "place": p["place"], "dist_km": dist, "gap_days": gap})
    return out

def _bt_classify(ev, big, eval_t):
    """('independent'|'aftershock'|'unseen', 一番大きい前の大地震 or None)"""
    preds = _bt_predecessors(ev, big)
    if not preds:
        return "independent", None
    known = [p for p in preds if p["t"] <= eval_t]
    if known:
        return "aftershock", max(known, key=lambda p: p["mag"])
    return "unseen", max(preds, key=lambda p: p["mag"])


# ── 静的な長期ハザードマップ ──────────────────────────
def _bt_smooth(grid):
    R = 2
    k1 = np.exp(-0.5 * (np.arange(-R, R + 1) / BT_STATIC_SIGMA_CELLS) ** 2)
    K = np.outer(k1, k1); K /= K.sum()
    H, W = grid.shape
    pad = np.pad(grid, R)
    out = np.zeros_like(grid)
    for di in range(-R, R + 1):
        for dj in range(-R, R + 1):
            out += K[di + R, dj + R] * pad[R + di:R + di + H, R + dj:R + dj + W]
    return out

def _bt_static_prepare(start_utc, end_utc, cov):
    """静的ハザードマップの材料（M>=BT_STATIC_MIN_MAG の長期カタログと、活断層への近さ）。評価ごとの切り出しは _bt_static_vectors。"""
    s0 = max(start_utc - timedelta(days=365.25 * BT_STATIC_LOOKBACK_YEARS), cov["first"])
    e0 = min(end_utc, cov["last"])
    qs = _fetch_catalog_strict(s0, e0, min_mag=BT_STATIC_MIN_MAG)
    keys, gi0, gj0, tab = _risk_cell_lookup()
    t = np.array([datetime.fromisoformat(q["time"].replace("Z", "+00:00")).timestamp() for q in qs], dtype=float)
    cell = np.full(len(qs), -1, dtype=np.int64)
    for n, q in enumerate(qs):
        i = _bt_cell_index(q["lat"], q["lon"])
        if i is not None:
            cell[n] = i
    fault_pct = None
    try:
        fpts = _decimate_points(_flatten_line_points(get_japan_active_faults()), FSP.MAX_POINTS)
        d = _min_dist_km_grid(fpts)
        if d:
            fd = np.array([d.get(k, np.inf) for k in _BT_KEYS], dtype=float)
            fault_pct = _bt_pct_rank(-fd)        # 近いほど高い
    except Exception as e:
        print(f"[バックテスト] 活断層への近さを作れません（静的の『＋活断層』は地震分布のみになります）: {e}")
    print(f"[バックテスト] 静的ハザード用カタログ: M>={BT_STATIC_MIN_MAG} {len(qs)}件")
    return {"t": t, "cell": cell, "fault_pct": fault_pct,
            "lookback_sec": 365.25 * 86400.0 * BT_STATIC_LOOKBACK_YEARS}

def _bt_static_vectors(st, eval_epoch):
    lo = int(np.searchsorted(st["t"], eval_epoch - st["lookback_sec"], side="left"))
    hi = int(np.searchsorted(st["t"], eval_epoch, side="right"))
    c = st["cell"][lo:hi]
    c = c[c >= 0]
    cnt = np.bincount(c, minlength=_BT_N).astype(float)
    grid = np.zeros(_BT_SHAPE)
    grid[_BT_CI, _BT_CJ] = cnt
    seis = _bt_smooth(grid)[_BT_CI, _BT_CJ]
    if st["fault_pct"] is not None:
        comb = BT_STATIC_W_SEIS * _bt_pct_rank(seis) + BT_STATIC_W_FAULT * st["fault_pct"]
    else:
        comb = seis.copy()
    return {"st_seis": seis, "st_comb": comb}


# ── 動的(統合リスクマップ)のスコア ────────────────────
class _BtRankCtx:
    """1つの基準(calib)に対するランク計算の下準備（ドメイン下限・並べ替え済み履歴）を使い回す。
    計算は compute_risk_grid_historical と同じ（絶対評価のみ・loo無し）。"""
    def __init__(self, calib):
        self.calib = calib
        self.floors = {
            "etas":   _make_etas_domain_floor(_cellwise_median_map(calib.get("etas_cell"))),
            "bvalue": _bvalue_domain_floor,
            "fault":  _make_stress_domain_floor(_cellwise_median_map(calib.get("fault_cell"))),
            "plate":  _make_stress_domain_floor(_cellwise_median_map(calib.get("plate_cell"))),
        }
        self.sc = {k: {} for k in _RISK_HIST_KEYS}

    def composite(self, raw):
        c = self.calib
        ranks = {
            "etas":   _absolute_rank_map(raw["etas"], c.get("etas_cell"), invert=False, log_transform=True,
                                         domain_floor_fn=self.floors["etas"], sorted_cache=self.sc["etas"]),
            "bvalue": _absolute_rank_map(raw["bvalue"], c.get("bvalue_cell"), invert=True, log_transform=False,
                                         domain_floor_fn=self.floors["bvalue"], sorted_cache=self.sc["bvalue"]),
            "fault":  _absolute_rank_map(raw["fault"], c.get("fault_cell"), invert=False, log_transform=False,
                                         domain_floor_fn=self.floors["fault"], sorted_cache=self.sc["fault"]),
            "plate":  _absolute_rank_map(raw["plate"], c.get("plate_cell"), invert=False, log_transform=False,
                                         domain_floor_fn=self.floors["plate"], sorted_cache=self.sc["plate"]),
        }
        S = np.full((_BT_N, len(_RISK_HIST_KEYS)), np.nan)
        for ci, comp in enumerate(_RISK_HIST_KEYS):
            for k, v in ranks[comp].items():
                i = _BT_IDX.get(k)
                if i is not None:
                    S[i, ci] = v
        self.last_S = S   # (v9.21) 成分別の順位を記録に残すため、最後に計算した成分行列を保持
        w = np.array([RISK_DEFAULT_WEIGHTS[k] for k in _RISK_HIST_KEYS])
        valid = ~np.isnan(S)
        # しきい値の算出(_compute_risk_level_thresholds)・JS側と同じ: 無い成分は中立値で補い、全成分の重みで割る
        comp = (np.where(valid, S, RISK_NEUTRAL_SCORE) * w).sum(axis=1) / w.sum()
        comp[(w * valid).sum(axis=1) <= 0] = np.nan
        return comp

def _bt_raw_series(qa, epochs, fpts, ppts):
    """各時刻(epoch秒)について、その時点までの直近 BT_WINDOW_DAYS 日の地震から ETAS・b値・活断層・
    プレート境界の『生の値』を作る（較正と同じ計算。断層・プレートは全時刻まとめて）。"""
    qt = qa["t"]
    win = BT_WINDOW_DAYS * 86400.0
    rows = []
    for e in epochs:
        lo = int(np.searchsorted(qt, e - win, side="left"))
        hi = int(np.searchsorted(qt, e, side="right"))
        if hi > lo:
            etas_raw = _risk_etas_raw(_analyze_etas_window(qa, lo, hi, e))
            bvalue_raw = _risk_bvalue_raw(compute_bvalue_grid(qa["q"][lo:hi]))
        else:
            etas_raw, bvalue_raw = {}, {}
        rows.append((e, lo, hi, etas_raw, bvalue_raw))
    sp = [(e, lo, hi) for e, lo, hi, _, _ in rows]
    fl = _load_to_grid_max_multi(fpts, _fault_plate_loads_multi(fpts, qa, sp, window_days=BT_WINDOW_DAYS))
    pl = _load_to_grid_max_multi(ppts, _fault_plate_loads_multi(ppts, qa, sp, window_days=BT_WINDOW_DAYS))
    return [{"etas": r[3], "bvalue": r[4], "fault": fl[i], "plate": pl[i]} for i, r in enumerate(rows)]


# ── 1地震ぶんの記録 ───────────────────────────────
def _bt_model_stats(vec, idx, r):
    D = _bt_maxfilter(vec, r)
    return {"tau": round(_bt_tau(vec, idx), 5), "tau_nb": round(_bt_tau(D, idx), 5)}

def _bt_make_record(ev, group, prev, eval_t, anchor, score_vec, after_vec, th, st_vecs, p, comp_S=None):
    idx = ev["cell_idx"]
    r = BT_NEAR_RADIUS_CELLS
    lv = _bt_levels(score_vec, th)
    D = _bt_maxfilter(score_vec, r)
    valid = ~np.isnan(score_vec)
    validD = ~np.isnan(D)
    with np.errstate(invalid="ignore"):
        area_ge = [round(float(_BT_W[valid & (score_vec >= t)].sum() / _BT_WTOT), 5) for t in th]
        area_dil = [round(float(_BT_W[validD & (D >= t)].sum() / _BT_WTOT), 5) for t in th]
    nb_level = int(sum(1 for t in th if validD[idx] and D[idx] >= t))
    s_before = float(score_vec[idx]) if valid[idx] else None
    a_valid = not np.isnan(after_vec[idx])
    s_after = float(after_vec[idx]) if a_valid else None
    rec = {
        "id": ev["id"], "t_utc": ev["t"].isoformat(), "t_jst": _bt_jst(ev["t"]),
        "place": ev["place"], "mag": ev["mag"], "depth": ev["depth"], "lat": round(ev["lat"], 3), "lon": round(ev["lon"], 3),
        "cell": list(_BT_KEYS[idx]), "group": group,
        "prev": None if prev is None else {"t_jst": _bt_jst(prev["t"]), "mag": prev["mag"], "place": prev["place"],
                                           "dist_km": round(prev["dist_km"], 1), "gap_days": round(prev["gap_days"], 2)},
        "eval_jst": _bt_jst(eval_t), "anchor_jst": _bt_jst(anchor),
        "dyn": {"score": None if s_before is None else round(s_before * 100, 1),
                "level": int(lv[idx]) if valid[idx] else 0, "valid": bool(valid[idx]),
                "nb_level": nb_level, "n_scored": int(valid.sum()),
                "area_ge": area_ge, "area_dil_ge": area_dil, **_bt_model_stats(score_vec, idx, r)},
        "after": {"score": None if s_after is None else round(s_after * 100, 1),
                  "level": int(sum(1 for t in th if a_valid and after_vec[idx] >= t))},
        "st_seis": _bt_model_stats(st_vecs["st_seis"], idx, r),
        "st_comb": _bt_model_stats(st_vecs["st_comb"], idx, r),
        "thresholds": [round(float(t), 4) for t in th],
    }
    # (v9.21) 成分別の震源セル順位τ(上位何%か)。どの成分が効いているかを重み見直しの根拠にする
    if comp_S is not None:
        rec["comp"] = {k: round(_bt_tau(comp_S[:, ci], idx), 5) for ci, k in enumerate(_RISK_HIST_KEYS)}
    return rec


# ── 実行・保存 ────────────────────────────────────
BT_DEFAULT_PARAMS = {"start": None, "end": None, "min_mag": 6.0, "lead_hours": 24.0, "after_hours": 1.0,
                     "rebuild_days": 30.0, "calib_step_hours": float(HIST_CALIB_SAMPLE_HOURS),
                     "prev_mag": 6.0, "max_depth_km": 0.0}

def _bt_parse_params(d):
    """画面/APIの入力を検証して実行パラメータにする。start/end は JST の 'YYYY-MM-DD'。"""
    p = dict(BT_DEFAULT_PARAMS)
    try:
        for k in ("min_mag", "lead_hours", "after_hours", "rebuild_days", "calib_step_hours", "prev_mag", "max_depth_km"):
            if d.get(k) not in (None, ""):
                p[k] = float(d[k])
        s = datetime.strptime(str(d.get("start")), "%Y-%m-%d").replace(tzinfo=JST)
        e = datetime.strptime(str(d.get("end")), "%Y-%m-%d").replace(tzinfo=JST) + timedelta(days=1)
    except (TypeError, ValueError):
        raise ValueError("開始日・終了日は YYYY-MM-DD で、数値の項目は数字で入力してください")
    if not (4.5 <= p["min_mag"] <= 9.5):
        raise ValueError("対象のマグニチュードは 4.5〜9.5 の範囲で指定してください")
    if not (1.0 <= p["lead_hours"] <= 24 * 30):
        raise ValueError("直前の時点は 1〜720 時間で指定してください")
    if not (0.0 <= p["after_hours"] <= 48):
        raise ValueError("直後の時点は 0〜48 時間で指定してください")
    if not (0.0 <= p["rebuild_days"] <= 365):
        raise ValueError("基準の作り直し間隔は 0〜365 日で指定してください（0=1地震ごと）")
    if not (6.0 <= p["calib_step_hours"] <= 72):
        raise ValueError("基準のサンプル間隔は 6〜72 時間で指定してください")
    if not (4.5 <= p["prev_mag"] <= 9.5):
        raise ValueError("余震判定の『前の大地震』の規模は 4.5〜9.5 で指定してください")
    if s >= e:
        raise ValueError("開始日は終了日より前にしてください")
    if s.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ValueError("未来の期間は指定できません")
    p["start"] = s.strftime("%Y-%m-%d")
    p["end"] = (e - timedelta(days=1)).strftime("%Y-%m-%d")
    return p

def _bt_cfg_hash(p):
    """記録(1地震ぶん)の中身が変わる設定だけでハッシュを作る。期間・対象Mは含めない
    （期間を広げて再実行したとき、計算済みの地震は再利用できる）。"""
    import hashlib
    key = json.dumps({"v": 2, "lead": p["lead_hours"], "after": p["after_hours"], "rebuild": p["rebuild_days"],
                      "step": p["calib_step_hours"], "prev": p["prev_mag"], "near": BT_NEAR_RADIUS_CELLS,
                      "jma_min": JMA_CATALOG_MIN_MAG, "static": [BT_STATIC_MIN_MAG, BT_STATIC_LOOKBACK_YEARS,
                      BT_STATIC_SIGMA_CELLS, BT_STATIC_W_SEIS, BT_STATIC_W_FAULT],
                      "w": {k: RISK_DEFAULT_WEIGHTS[k] for k in _RISK_HIST_KEYS}}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:10]

def _bt_records_path(cfg):
    return os.path.join(BACKTEST_DIR, f"records_{cfg}.jsonl")

def _bt_read_records(cfg):
    path = _bt_records_path(cfg)
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["id"]] = r
                except Exception:
                    continue
    return out

def _bt_anchor(eval_t, rebuild_days):
    if rebuild_days <= 0:
        return eval_t
    step = rebuild_days * 86400.0
    return datetime.fromtimestamp(math.floor(eval_t.timestamp() / step) * step, tz=timezone.utc)

_bt_job = {"running": False, "cancel": False, "phase": "", "done": 0, "total": 0, "message": "",
           "started": None, "finished": None, "error": None, "params": None, "cfg": None, "skipped": {}}
_bt_lock = threading.Lock()

def _bt_set(**kw):
    with _bt_lock:
        _bt_job.update(kw)

def _bt_run(p):
    """バックグラウンドで全地震を評価する。結果は1地震ごとに JSONL へ追記（中断しても再開できる）。"""
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    cfg = _bt_cfg_hash(p)
    try:
        start_utc = datetime.strptime(p["start"], "%Y-%m-%d").replace(tzinfo=JST).astimezone(timezone.utc)
        end_utc = (datetime.strptime(p["end"], "%Y-%m-%d").replace(tzinfo=JST) + timedelta(days=1)).astimezone(timezone.utc)
        end_utc = min(end_utc, datetime.now(timezone.utc))
        lead = timedelta(hours=p["lead_hours"]); after = timedelta(hours=p["after_hours"])
        calib_span = timedelta(days=HIST_CALIB_LOOKBACK_DAYS + BT_WINDOW_DAYS)

        _bt_set(phase="M6以上の一覧を読み込み中")
        events, cov = _bt_load_events(start_utc, end_utc, p["min_mag"], p["prev_mag"])
        # カタログは確定まで数か月遅れる。直後の評価時点までカタログが続く地震だけを対象にする
        end_utc = min(end_utc, cov["last"] - after)
        big = [e for e in events if e["mag"] >= p["prev_mag"]]
        targets, skipped = [], {"領域外": 0, "深さ超過": 0, "基準期間がカタログ外": 0}
        for ev in events:
            if ev["mag"] < p["min_mag"] or not (start_utc <= ev["t"] < end_utc):
                continue
            ev["cell_idx"] = _bt_cell_index(ev["lat"], ev["lon"])
            if ev["cell_idx"] is None:
                skipped["領域外"] += 1; continue
            if p["max_depth_km"] > 0 and ev["depth"] > p["max_depth_km"]:
                skipped["深さ超過"] += 1; continue
            ev["eval_t"] = ev["t"] - lead
            ev["anchor"] = _bt_anchor(ev["eval_t"], p["rebuild_days"])
            if ev["anchor"] - calib_span < cov["first"] - _JMA_CATALOG_EDGE_TOL:
                skipped["基準期間がカタログ外"] += 1; continue
            ev["group"], ev["prev"] = _bt_classify(ev, big, ev["eval_t"])
            targets.append(ev)
        done_recs = _bt_read_records(cfg)
        todo = [e for e in targets if e["id"] not in done_recs]
        _bt_set(total=len(targets), done=len(targets) - len(todo), skipped=skipped,
                message=f"対象{len(targets)}件（計算済み{len(targets) - len(todo)}件・残り{len(todo)}件）")
        if not todo:
            _bt_set(phase="完了", message=f"対象{len(targets)}件はすべて計算済みです")
            return

        _bt_set(phase="静的ハザードマップの材料を準備中")
        st = _bt_static_prepare(min(e["eval_t"] for e in todo), end_utc, cov)
        fpts = _decimate_points(_flatten_line_points(get_japan_active_faults()), FSP.MAX_POINTS)
        ppts = _decimate_points(_flatten_line_points(get_plate_boundaries(), bbox=FAULTS_BBOX), FSP.MAX_POINTS)
        if not fpts or not ppts:
            raise RuntimeError("活断層・プレート境界のデータを取得できません（ネットワークを確認してください）")

        groups = {}
        for ev in todo:
            groups.setdefault(ev["anchor"], []).append(ev)
        out_path = _bt_records_path(cfg)
        n_done = len(targets) - len(todo)
        for gi, anchor in enumerate(sorted(groups)):
            if _bt_job["cancel"]:
                _bt_set(phase="中止", message=f"中止しました（{n_done}/{len(targets)}件まで保存済み）")
                return
            evs = sorted(groups[anchor], key=lambda e: e["t"])
            _bt_set(phase=f"基準の作成と評価 {gi + 1}/{len(groups)}（基準日 {_bt_jst(anchor)[:10]}・{len(evs)}件）")
            q_lo = anchor - calib_span
            q_hi = max([anchor] + [e["t"] + after for e in evs])
            quakes = _fetch_catalog_strict(q_lo, min(q_hi, end_utc + after))
            calib = _compute_historical_calibration(end_utc=anchor, quakes=quakes, step_hours=p["calib_step_hours"])
            th_all = calib.get("risk_thresholds") or {}
            th = th_all.get(BT_STD_KEY)
            if not th:
                skipped["基準を作れない"] = skipped.get("基準を作れない", 0) + len(evs)
                _bt_set(skipped=dict(skipped))
                print(f"[バックテスト] 基準日{_bt_jst(anchor)}: 基準を作れずスキップ（{calib.get('n_samples', 0)}時点）")
                n_done += len(evs); _bt_set(done=n_done)
                continue
            ctx = _BtRankCtx(calib)
            qa = _build_quake_arrays(quakes)
            epochs = []
            for ev in evs:
                epochs.append(ev["eval_t"].timestamp())
                epochs.append((ev["t"] + after).timestamp())
            raws = _bt_raw_series(qa, epochs, fpts, ppts)
            with open(out_path, "a", encoding="utf-8") as f:
                for n, ev in enumerate(evs):
                    score_vec = ctx.composite(raws[2 * n])
                    comp_S = ctx.last_S.copy()   # (v9.21) 直前時点の成分別スコア
                    after_vec = ctx.composite(raws[2 * n + 1])
                    st_vecs = _bt_static_vectors(st, ev["eval_t"].timestamp())
                    rec = _bt_make_record(ev, ev["group"], ev["prev"], ev["eval_t"], anchor,
                                          score_vec, after_vec, th, st_vecs, p, comp_S=comp_S)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    n_done += 1
                    _bt_set(done=n_done)
            del quakes, qa, raws
        _bt_set(phase="完了", message=f"{n_done}/{len(targets)}件を評価しました")
    except Exception as e:
        import traceback; traceback.print_exc()
        _bt_set(phase="エラー", error=str(e) or e.__class__.__name__)
    finally:
        _bt_set(running=False, finished=time.time())


# ── 集計(Molchan図など) ───────────────────────────
def _bt_curve(taus):
    """Molchan曲線: 警戒面積τ(0〜1)ごとの外れ率（τ以内に入らなかった地震の割合）。"""
    if not taus:
        return []
    a = np.array(taus, dtype=float)
    return [round(float((a > g + 1e-12).mean()), 4) for g in BT_TAU_GRID]

def _bt_sig_curve(n, alpha=0.05):
    """ランダムに警戒したとき、片側 alpha で『有意に良い』と言える外れ率の境界(各τ)。n件中k件以上入れば有意。"""
    if n <= 0:
        return []
    lf = np.concatenate(([0.0], np.cumsum(np.log(np.arange(1, n + 1)))))
    i = np.arange(n + 1)
    out = []
    for g in BT_TAU_GRID:
        if g <= 0 or g >= 1:
            out.append(None); continue
        logp = lf[n] - lf[i] - lf[n - i] + i * math.log(g) + (n - i) * math.log(1 - g)
        tail = np.cumsum(np.exp(logp)[::-1])[::-1]       # P(X>=i)
        ks = np.nonzero(tail < alpha)[0]
        out.append(None if len(ks) == 0 else round(1.0 - int(ks[0]) / n, 4))
    return out

def _bt_wilson(k, n, z=1.96):
    """的中率の95%信頼区間(Wilson)。件数が少ないときの不確かさを見せる。"""
    if n <= 0:
        return None
    ph = k / n
    den = 1.0 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]

def _bt_auc_ci(a, n_boot=1000, seed=12345):
    """AUC(=1−平均順位)の95%信頼区間(ブートストラップ。乱数固定で再現可能)。"""
    n = len(a)
    if n < 5:
        return None
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    lo, hi = np.percentile(1.0 - means, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]

def _bt_metrics(taus):
    n = len(taus)
    if n == 0:
        return {"n": 0}
    a = np.array(taus, dtype=float)
    m = {"n": n, "median_tau": round(float(np.median(a)), 4), "mean_tau": round(float(a.mean()), 4),
         "auc": round(float(1.0 - a.mean()), 4), "auc_ci": _bt_auc_ci(a)}
    for x in (0.05, 0.10, 0.20):
        k = int((a <= x + 1e-12).sum())
        tag = int(round(x * 100))
        m[f"hit{tag}"] = round(k / n, 4); m[f"k{tag}"] = k; m[f"p{tag}"] = round(_bt_binom_sf(k, n, x), 5)
        m[f"ci{tag}"] = _bt_wilson(k, n)
    return m

def _bt_component_table(rs):
    """(v9.21) 成分ごとの単独の成績(震源セル)。『その成分だけで警戒したら』のAUC・上位10%的中率。
    成分別の記録が無い古い記録は除く(n で件数を表示)。重み・成分の取捨の根拠にする。"""
    rows = []
    rs = [r for r in rs if r.get("comp")]
    for k in _RISK_HIST_KEYS:
        taus = [r["comp"][k] for r in rs if k in r["comp"]]
        if not taus:
            continue
        mt = _bt_metrics(taus)
        rows.append({"key": k, "label": RISK_LABELS.get(k, k), "weight": RISK_DEFAULT_WEIGHTS[k], "n": mt["n"],
                     "auc": mt["auc"], "auc_ci": mt["auc_ci"], "hit10": mt["hit10"], "p10": mt["p10"],
                     "no_score": round(float(np.mean([t >= 0.9999 for t in taus])), 4)})   # スコアが無く警戒できなかった割合
    return rows

def _bt_breakdown(rs):
    """(v9.21) 年別・規模別の安定性。『特定の年(例:大地震列のある年)だけ良い』を見抜く。"""
    def row(label, sub):
        d = _bt_metrics([r["dyn"]["tau"] for r in sub]); st = _bt_metrics([r["st_comb"]["tau"] for r in sub])
        return {"label": label, "n": d["n"], "dyn_auc": d["auc"], "dyn_hit10": d["hit10"],
                "st_auc": st["auc"], "st_hit10": st["hit10"]}
    years = {}
    for r in rs:
        years.setdefault(r["t_jst"][:4], []).append(r)
    by_year = [row(y + "年", years[y]) for y in sorted(years)]
    bins = [("M6.0〜6.4", 6.0, 6.5), ("M6.5〜6.9", 6.5, 7.0), ("M7.0以上", 7.0, 99.0)]
    by_mag = []
    for lab, lo, hi in bins:
        sub = [r for r in rs if lo <= r["mag"] < hi]
        if sub:
            by_mag.append(row(lab, sub))
    return {"by_year": by_year, "by_mag": by_mag}

def _bt_paired(a, b):
    """動的(a)と静的(b)の順位の対比較。符号検定(両側)。aが小さい=動的が良い。"""
    d = np.array(a, dtype=float) - np.array(b, dtype=float)
    better = int((d < -1e-9).sum()); worse = int((d > 1e-9).sum()); n = better + worse
    p = 1.0 if n == 0 else min(1.0, 2.0 * _bt_binom_sf(max(better, worse), n, 0.5))
    return {"n": len(d), "dyn_better": better, "dyn_worse": worse, "tie": len(d) - n,
            "median_diff": round(float(np.median(d)), 4) if len(d) else None, "p": round(p, 5)}

def _bt_level_table(recs):
    rows = []
    for L in range(1, 6):
        rs = [r for r in recs if r.get("dyn")]
        if not rs:
            continue
        hit = float(np.mean([r["dyn"]["level"] >= L for r in rs]))
        hit_nb = float(np.mean([r["dyn"]["nb_level"] >= L for r in rs]))
        area = float(np.mean([r["dyn"]["area_ge"][L - 1] for r in rs]))
        area_nb = float(np.mean([r["dyn"]["area_dil_ge"][L - 1] for r in rs]))
        rows.append({"level": L, "hit": round(hit, 4), "area": round(area, 5),
                     "lift": None if area <= 0 else round(hit / area, 2),
                     "hit_nb": round(hit_nb, 4), "area_nb": round(area_nb, 5),
                     "lift_nb": None if area_nb <= 0 else round(hit_nb / area_nb, 2)})
    return rows

def _bt_summarize(records, p):
    """records: {id: 記録} から、期間・規模・深さで絞って、グループ別・モデル別に集計する。"""
    start = datetime.strptime(p["start"], "%Y-%m-%d").replace(tzinfo=JST).astimezone(timezone.utc)
    end = (datetime.strptime(p["end"], "%Y-%m-%d").replace(tzinfo=JST) + timedelta(days=1)).astimezone(timezone.utc)
    recs = []
    for r in records.values():
        t = datetime.fromisoformat(r["t_utc"])
        if not (start <= t < end) or r["mag"] < p["min_mag"]:
            continue
        if p["max_depth_km"] > 0 and r["depth"] > p["max_depth_km"]:
            continue
        recs.append(r)
    recs.sort(key=lambda r: r["t_utc"])
    groups = {"all": recs}
    for g in ("independent", "aftershock", "unseen"):
        groups[g] = [r for r in recs if r["group"] == g]
    out = {"params": p, "n_events": len(recs), "group_labels": BT_GROUP_LABELS, "model_labels": BT_MODEL_LABELS,
           "tau_grid": BT_TAU_GRID, "groups": {}, "rows": recs}
    for g, rs in groups.items():
        gg = {"n": len(rs), "models": {}, "levels": _bt_level_table(rs), "sig05": _bt_sig_curve(len(rs)), "paired": {},
              "components": _bt_component_table(rs), "breakdown": _bt_breakdown(rs) if rs else {"by_year": [], "by_mag": []}}
        for m in ("dyn", "st_seis", "st_comb"):
            gg["models"][m] = {}
            for v in ("tau", "tau_nb"):
                taus = [r[m][v] for r in rs]
                gg["models"][m][v] = {"metrics": _bt_metrics(taus), "curve": _bt_curve(taus)}
        for m in ("st_seis", "st_comb"):
            gg["paired"][m] = {v: _bt_paired([r["dyn"][v] for r in rs], [r[m][v] for r in rs]) for v in ("tau", "tau_nb")}
        out["groups"][g] = gg
    return out

def _bt_csv(records, p):
    """手作業の記録（時刻・震源名・M・直前リスク値・直後リスク値）を先頭に、評価の詳細を右に並べたCSV。"""
    summary = _bt_summarize(records, p)
    start = datetime.strptime(p["start"], "%Y-%m-%d").replace(tzinfo=JST).astimezone(timezone.utc)
    end = (datetime.strptime(p["end"], "%Y-%m-%d").replace(tzinfo=JST) + timedelta(days=1)).astimezone(timezone.utc)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["時刻(JST)", "震源名", "マグニチュード", "発生直前リスク値", "発生直後リスク値(参考用)",
                "直前Lv", "直後Lv", "震源セル順位(上位%)", "周囲の最大Lv", "高リスク面積比(Lv3以上,%)",
                "高リスク面積比(周囲込み,Lv3以上,%)", "静的:地震分布 順位(上位%)", "静的:地震分布+活断層 順位(上位%)",
                "区分", "直前の大地震", "震源緯度", "震源経度", "深さkm", "評価時点(JST)", "基準日時(JST)"]
               + [f"成分順位:{RISK_LABELS.get(k, k)}(上位%)" for k in _RISK_HIST_KEYS])
    for r in sorted(records.values(), key=lambda r: r["t_utc"]):
        t = datetime.fromisoformat(r["t_utc"])
        if not (start <= t < end) or r["mag"] < p["min_mag"]:
            continue
        if p["max_depth_km"] > 0 and r["depth"] > p["max_depth_km"]:
            continue
        d = r["dyn"]; pv = r.get("prev")
        w.writerow([r["t_jst"], r["place"], r["mag"],
                    "" if d["score"] is None else d["score"], "" if r["after"]["score"] is None else r["after"]["score"],
                    d["level"], r["after"]["level"], round(d["tau"] * 100, 2), d["nb_level"],
                    round(d["area_ge"][2] * 100, 2), round(d["area_dil_ge"][2] * 100, 2),
                    round(r["st_seis"]["tau"] * 100, 2), round(r["st_comb"]["tau"] * 100, 2),
                    {"independent": "独立", "aftershock": "余震・続発", "unseen": "直前に発生(未観測)"}[r["group"]],
                    "" if pv is None else f"{pv['t_jst']} M{pv['mag']} {pv['place']} {pv['dist_km']}km {pv['gap_days']}日前",
                    r["lat"], r["lon"], r["depth"], r["eval_jst"], r["anchor_jst"]]
                   + [("" if not r.get("comp") or k not in r["comp"] else round(r["comp"][k] * 100, 2)) for k in _RISK_HIST_KEYS])
    return "\ufeff" + buf.getvalue()


def render_riskmap(risk_cells, updated_str):
    cells_js = json.dumps(risk_cells, ensure_ascii=False)
    weights_js = json.dumps(RISK_DEFAULT_WEIGHTS)
    labels_js = json.dumps(RISK_LABELS, ensure_ascii=False)
    thresh_js = json.dumps(get_risk_level_thresholds())
    gs = RISK_GRID_SIZE
    n_cells = len(risk_cells)
    w = RISK_DEFAULT_WEIGHTS

    # (v9.06) GNSS成分: 現在値だけを使う。現在のセルにGNSS値がある時だけ選択可能にする
    gnss_ready = any("gnss" in c["c"] for c in risk_cells)
    if gnss_ready:
        gnss_row = ('<div class="chk-row"><input type="checkbox" id="chk_gnss" checked onchange="onToggle(\'gnss\')">'
                    f'<span class="clabel">GNSS変位（地殻変動・現在値）</span><span class="cweight">w={w["gnss"]}</span></div>')
    else:
        if not GSI_GNSS_ENABLED:
            _why = "未設定"
        elif _gnss_cache.get("data") is None and (_gnss_cache["updating"] or not _gnss_cache["ts"]):
            _why = "取得中"
        else:
            _why = "現在値なし"
        gnss_row = ('<div class="chk-row disabled"><input type="checkbox" id="chk_gnss" disabled>'
                    f'<span class="clabel">GNSS変位（地殻変動・現在値）</span><span class="cbadge">{_why}</span></div>')
    gnss_ready_js = "true" if gnss_ready else "false"

    # (v9.21) TEC成分: 現在のセルにTEC値がある時だけ選択可能にする（GNSSと同じ扱い）
    tec_ready = any("tec" in c["c"] for c in risk_cells)
    if tec_ready:
        tec_row = ('<div class="chk-row"><input type="checkbox" id="chk_tec" checked onchange="onToggle(\'tec\')">'
                   f'<span class="clabel">TEC（電離圏・局所偏差）</span><span class="cweight">w={w["tec"]}'
                   + ('・履歴蓄積中' if _tec_risk_state.get("mode") == "spatial" else '') + '</span></div>')
    else:
        if not TEC_ENABLED:
            _twhy = "未設定"
        elif _tec_cache.get("updating") or not _tec_cache.get("ts"):
            _twhy = "取得中"
        else:
            _twhy = _tec_risk_state.get("why") or "現在値なし"
        tec_row = ('<div class="chk-row disabled"><input type="checkbox" id="chk_tec" disabled>'
                   f'<span class="clabel">TEC（電離圏・局所偏差）</span><span class="cbadge">{_twhy}</span></div>')
    tec_ready_js = "true" if tec_ready else "false"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;overflow:hidden}}
#panel{{width:300px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-right:1px solid rgba(255,255,255,.14);overflow-y:auto;padding:14px;
    position:relative;transition:margin-left 0.2s}}
#panel.closed{{margin-left:-300px}}
#panelTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px}}
#panel h2{{font-size:14px;color:#f3f4f6}}
#panelClose{{flex-shrink:0;width:22px;height:22px;border:none;border-radius:12px;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:rgba(235,238,245,.72);
    cursor:pointer;font-size:13px;line-height:1;display:flex;align-items:center;justify-content:center}}
#panelClose:hover{{background:rgba(255,255,255,.3);color:#fff}}
#panelReopen{{position:absolute;top:70px;left:0;z-index:5000;width:34px;height:78px;border:none;
    border-radius:0 18px 18px 0;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62);cursor:pointer;font-size:12px;
    display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;
    box-shadow:2px 0 8px rgba(0,0,0,.4)}}
#panelReopen:hover{{background:rgba(10,132,255,.55);color:#fff}}
#panelReopen.show{{display:flex}}
#panelReopen .arrow{{font-size:15px;line-height:1}}
#panelReopen .vlabel{{writing-mode:vertical-rl;letter-spacing:1px;font-size:10px;font-weight:600}}
#panel p.sub{{font-size:11px;color:rgba(235,238,245,.46);margin-bottom:12px;line-height:1.6}}
.sec{{margin-bottom:16px}}
.sec h3{{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid rgba(255,255,255,.14);padding-bottom:4px;
    display:flex;align-items:center;justify-content:space-between}}
.preset-row{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px}}
.preset-btn{{flex:1 1 calc(50% - 6px);padding:6px 4px;font-size:11px;font-weight:600;cursor:pointer;
    border:1px solid rgba(255,255,255,.18);border-radius:14px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62);text-align:center}}
.preset-btn:hover{{background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:#f3f4f6}}
.preset-btn.on{{background:linear-gradient(135deg,#0a84ff,#7c5cff);color:#fff;border-color:transparent}}
.chk-row{{display:flex;align-items:center;gap:8px;padding:6px 4px;border-radius:12px;font-size:12px}}
.chk-row:hover{{background:rgba(255,255,255,.07)}}
.chk-row.disabled{{opacity:0.45}}
.chk-row input{{width:15px;height:15px;flex-shrink:0}}
.chk-row .clabel{{flex:1;color:rgba(240,242,247,.85)}}
.chk-row .cweight{{font-size:10px;color:rgba(235,238,245,.46)}}
.chk-row .cbadge{{font-size:9px;padding:1px 5px;border-radius:7px;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:rgba(235,238,245,.62)}}
#cellCount{{font-size:11px;color:rgba(235,238,245,.62);margin-top:10px}}
#cellCount span{{color:#60a5fa;font-weight:700}}
.note{{font-size:10.5px;color:rgba(235,238,245,.46);line-height:1.7;margin-top:10px}}
.tbl-toggle{{border:none;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);color:rgba(235,238,245,.62);font-size:10px;font-weight:600;
    padding:2px 8px;border-radius:4px;cursor:pointer;flex-shrink:0}}
.tbl-toggle:hover{{background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:#f3f4f6}}
#rankWrap{{max-height:260px;overflow-y:auto;border:1px solid rgba(255,255,255,.14);border-radius:14px}}
#rankWrap.collapsed{{display:none}}
table.rank-table{{width:100%;border-collapse:collapse;font-size:10.5px}}
table.rank-table thead tr{{background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);position:sticky;top:0}}
table.rank-table th{{padding:5px 4px;color:rgba(235,238,245,.62);text-align:left;font-weight:600;white-space:nowrap}}
table.rank-table td{{padding:4px;color:rgba(240,242,247,.85);border-top:1px solid rgba(255,255,255,.14);white-space:nowrap}}
.rrow{{cursor:pointer}}
.rrow:hover{{background:rgba(255,255,255,.12)}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
/* 地図タイル自体を少し明るく・彩度を落として、暗色オーバーレイとの同化を防ぐ(v7.44) */
.leaflet-tile-pane{{filter:brightness(1.18) saturate(0.8) contrast(0.95)}}
#lg{{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:10px 13px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:11px;line-height:1.9;color:#f3f4f6}}
#hdr{{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:1000;
    background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);padding:6px 16px;border-radius:14px;font-size:11px;color:rgba(235,238,245,.62)}}
/* ── セル詳細パネル（クリックで表示）: 内訳/棒グラフ/前日比/推移を
   はっきり区切って表示し、ごちゃつかないようにする ── */
#detailBox{{display:none;position:absolute;top:14px;right:14px;z-index:1500;width:290px;
    background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid rgba(255,255,255,.18);border-radius:20px;padding:12px 14px;
    font-size:11px;color:rgba(240,242,247,.85);max-height:calc(100% - 28px);overflow-y:auto}}
#detailBox .dHead{{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}}
#dTitle{{font-weight:700;font-size:14px;color:#f3f4f6}}
#dClose{{flex-shrink:0;width:20px;height:20px;border:none;border-radius:12px;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:rgba(235,238,245,.72);
    cursor:pointer;font-size:12px;line-height:1}}
#dClose:hover{{background:rgba(255,255,255,.3);color:#fff}}
#dLoc{{font-size:10px;color:rgba(235,238,245,.46);margin:2px 0 10px}}
.dSection{{margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.14)}}
.dSection h4{{font-size:11px;font-weight:700;color:#60a5fa;margin-bottom:6px}}
.brk-row{{display:flex;justify-content:space-between;font-size:11px;padding:2px 0;color:rgba(235,238,245,.72)}}
.brk-val{{font-weight:700;color:#f3f4f6}}
#dPrevDay{{font-size:11.5px;margin-top:2px}}
#dTrendState{{font-size:10px;color:rgba(235,238,245,.46);margin-bottom:4px}}
canvas.dChart{{display:block;width:100%}}

@media (max-width:768px){{
  #panel{{position:fixed;top:0;left:0;height:100%;width:86vw;max-width:340px;z-index:2000;
      box-shadow:4px 0 24px rgba(0,0,0,.7)}}
  #panel.closed{{margin-left:-86vw}}
  #detailBox{{width:82vw;max-width:290px;right:8px;top:8px}}
  #hdr{{font-size:10px;padding:5px 10px}}
}}
</style></head><body>
<button id="panelReopen" onclick="togglePanel()" title="設定・一覧を開く">
  <span class="arrow">▶</span><span class="vlabel">設定・一覧</span>
</button>
<div id="panel">
  <div id="panelTop">
    <h2>統合リスクマップ <span style="font-size:10px;color:#fbbf24">β</span></h2>
    <button id="panelClose" onclick="togglePanel()" title="パネルを閉じる">✕</button>
  </div>
  <p class="sub">複数の地震関連データを統合した地震リスク指数です。各セルを「そのセル自身の過去65日の実績と比べてどれだけ普段と違うか」で絶対評価しています。地震の発生確率ではありません。</p>

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
    {tec_row}
    {gnss_row}
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
    <div style="font-size:10px;color:rgba(235,238,245,.46);margin-top:4px">上位<span id="rankShown">0</span>件 / 表示中<span id="rankTotal">0</span>件中（行クリックで地図上を表示）</div>
  </div>

  <div id="calibBanner" style="display:none;font-size:11px;color:#fbbf24;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.35);border-radius:10px;padding:8px 10px;margin-bottom:10px;line-height:1.6"></div>
  <div id="cellCount">表示中のセル数: <span id="cellN">0</span> / {n_cells}</div>
  <div id="lvCounts" style="font-size:10.5px;color:rgba(235,238,245,.62);margin-top:3px"></div>
  <div style="font-size:10px;color:rgba(235,238,245,.46);margin-top:2px">平穏時の目安: Lv1 {RISK_LEVEL_TARGETS[1]} / Lv2 {RISK_LEVEL_TARGETS[2]} / Lv3 {RISK_LEVEL_TARGETS[3]} / Lv4 {RISK_LEVEL_TARGETS[4]} セル</div>
  <div class="note">
    重みは選択されたデータのみを使い自動的に再正規化されます。<br>
    セルをクリックすると統合リスク指数と各データの寄与度（内訳）を表示します。<br>
    GNSS変位は、各セルに半径{GNSS_CELL_RADIUS_KM:.0f}km以内で最も近い電子基準点の直近{GNSS_LOOKBACK_DAYS}日の水平変位（現在値）を割り当て、過去の値とは比べず、変位の大きさ(mm)だけで固定の目盛りでスコア化します（ノイズ水準{GNSS_NOISE_MM:.0f}mm程度で低め、7mm・15mmで高めに評価）。観測点から遠いセルはGNSSを使わず、他の成分だけで評価します。座標値は公表に遅れがあるため、基準日は内訳に表示します。GNSSは「他の成分の水準を超えて大きい時だけ総合値を押し上げ、平常範囲なら総合値を変えない」扱いです（小さい変位を低リスクの証拠とはみなしません）。TEC（電離圏）は、現在のTECと直近24時間平均との差から、日本全体の増減（日変化・磁気嵐など）を差し引いた「局所的な偏差(TECU)」を固定の目盛りでスコア化します（2・4・8TECUで低〜高、上限0.70）。学術的に確立した前兆指標ではないため重みは小さく（w={w['tec']}）、GNSSと同じく「他成分を超えた分だけ総合値を押し上げ、平常範囲なら変えない」扱いです。標準プリセットはETAS・b値・活断層・プレート境界＋GNSS＋TEC（GNSS・TECが使えない間は外れます）、「地殻変動」はプレート境界＋GNSSです（GNSSが使えない間はプレート境界のみ）。
  </div>
</div>
<div id="mp">
  <div id="map"></div>
  <div id="hdr">更新: {updated_str}</div>
  <div id="lg">
    <b>統合リスクレベル</b><br>
    <span style="color:{LEVEL_COLOR[5]};background:{LEVEL_COLOR[5]};opacity:0.9;padding:0 6px;border:1px solid #fff">■</span> Lv5（特に稀な高リスク）<br>
    <span style="color:{LEVEL_COLOR[4]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv4（警戒）<br>
    <span style="color:{LEVEL_COLOR[3]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv3（注意）<br>
    <span style="color:{LEVEL_COLOR[2]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv2（やや注意）<br>
    <span style="color:{LEVEL_COLOR[1]};opacity:{LEVEL_FILL_OPACITY}">■</span> Lv1（平常）<br>
    <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
    <small style="color:rgba(235,238,245,.46)">Lv0（しきい値未満）のセルは地図上に表示されません</small><br>
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
var THRESH = {thresh_js};   // (v8.21) レベル境界: 成分選択パターン別に自動キャリブレーション
var GS = {gs};
var RISK_COLOR = {{5:'{LEVEL_COLOR[5]}',4:'{LEVEL_COLOR[4]}',3:'{LEVEL_COLOR[3]}',2:'{LEVEL_COLOR[2]}',1:'{LEVEL_COLOR[1]}'}};
var KEYS = ['etas','bvalue','fault','plate','pressure','tec','gnss'];
var GNSS_READY = {gnss_ready_js};   // (v9.06) 現在のGNSS値が使えるか
var TEC_READY = {tec_ready_js};     // (v9.21) 現在のTEC値が使えるか

var PRESETS = {{
  standard: {{etas:true, bvalue:true, fault:true, plate:true, pressure:false, tec:true,  gnss:true}},   // (v9.07/9.21) GNSS・TECが使えない間は自動で外れる
  all:      {{etas:true, bvalue:true, fault:true, plate:true, pressure:true,  tec:true,  gnss:true}},
  geo:      {{etas:false,bvalue:false,fault:true, plate:true, pressure:false, tec:false, gnss:false}},
  crust:    {{etas:false,bvalue:false,fault:false,plate:true, pressure:false, tec:false, gnss:true}}
}};
var selected = Object.assign({{}}, PRESETS.standard);
if(!GNSS_READY) selected.gnss = false;   // (v9.07)
if(!TEC_READY) selected.tec = false;     // (v9.21)

if(window.innerWidth<=768){{
  document.getElementById('panel').classList.add('closed');
  document.getElementById('panelReopen').classList.add('show');
}}
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}

var rectLayer = null;
var COMP_COLOR = {{etas:'#ef4444', bvalue:'#f97316', fault:'#facc15', plate:'#38bdf8', pressure:'#a78bfa', tec:'#f472b6', gnss:'#34d399'}};
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
    if(!GNSS_READY) selected.gnss = false;   // (v9.00) GNSSが使えない間は外す（「地殻変動」はプレートのみに戻る）
    if(!TEC_READY) selected.tec = false;     // (v9.21)
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

// (v8.21) 過去履歴のある成分(ETAS/b値/活断層/プレート)がそのセルに無い場合は中立0.5で補う。
// 成分が少ないセルの総合値が極端値に張り付くのを防ぐ（サーバー側のしきい値算出と同じ扱い）。
var HIST_KEYS = ['etas','bvalue','fault','plate'];   // (v9.06) GNSSはそのセルに無ければ中立補完せず除外する
// (v9.21) 「押し上げ専用」成分: GNSS・TEC。他成分の水準(base)を超えた分だけ総合を押し上げ、平常範囲なら総合を変えない。
var BOOST_KEYS = ['gnss','tec'];
function computeComposite(cell){{
  var wsum=0, ssum=0, used=[], wmiss=0, boost={{}};
  KEYS.forEach(function(k){{
    if(!selected[k]) return;
    if(BOOST_KEYS.indexOf(k) >= 0){{ if(cell.c[k]) boost[k] = cell.c[k].s; return; }}   // 下で別扱い
    var wk = WEIGHTS[k];
    if(cell.c[k]){{ wsum += wk; ssum += wk*cell.c[k].s; used.push(k); }}
    else if(HIST_KEYS.indexOf(k) >= 0){{ wmiss += wk; }}
  }});
  // (v9.07/9.21) 押し上げ成分は、baseを超えた分だけ総合を上げる。小さい値を低リスクの証拠にしない。
  //   baseは「押し上げ成分を入れる前」の他成分の加重平均で固定する(GNSSとTECで順序依存にならない)。
  //   他の成分が1つも無い時は、そのスコア自体を使う。
  var eff = {{}};
  var base = (wsum > 0) ? (ssum + 0.5*wmiss)/(wsum + wmiss) : null;
  BOOST_KEYS.forEach(function(k){{
    if(boost[k] === undefined) return;
    var ge = (base === null) ? boost[k] : Math.max(boost[k], base);
    eff[k] = ge;
    wsum += WEIGHTS[k]; ssum += WEIGHTS[k]*ge; used.push(k);
  }});
  if(wsum<=0) return null;
  var wall = wsum + wmiss;
  return {{score: (ssum + 0.5*wmiss)/wall, used: used, wsum: wall, wmiss: wmiss, eff: eff}};
}}
// (v7.56) Lv5=特別警報級(年数回程度)/Lv4=危険警報級(活発期でも数日に一度程度)を
// 目指す絶対基準。以前は0.95/0.7だったが、サーバー側の絶対評価がセル単位の
// 空間的な下駄(活断層近接セルの恒常的な高スコア)を含んでいたため、平穏な時期
// でもLv5が10セル前後、Lv4が100セル前後常時表示される状態だった。
// サーバー側の絶対評価をセル自身の時系列基準に修正した上で、この最終しきい値も
// 引き上げて二重に頭打ちにする。表示頻度は今後の実績を見ながら再チューニングする。
// (v7.56) Lv0(色無し)を追加。0.2未満は「表示するほどの水準ではない」として
// 地図上に描画しない(redraw側でlv===0を除外)。これによりマップ全体が常に
// 色で埋め尽くされる状態を避け、実際に注意すべきセルだけが目立つようにする。
// (v8.21) しきい値はサーバー側で「平穏時のLv別セル数が目標値に近くなる」よう
// 過去65日の実績から自動算出したもの(THRESH)を使う。成分の選択パターンごとに
// 合成スコアの分布が違うため、選択中の成分(ETAS/b値/活断層/プレート境界)で引き当てる。
// 気圧・GNSSは過去履歴が無くキャリブレーションできないため、キーには含めない。
function currentThresholds(){{
  var ks = ['etas','bvalue','fault','plate'].filter(function(k){{ return selected[k]; }});   // (v9.06) GNSSはキーに含めない
  return (THRESH.by_subset && THRESH.by_subset[ks.join(',')]) || THRESH.default;
}}
function levelOf(score){{
  var t = currentThresholds();
  if(score>=t[4]) return 5;
  if(score>=t[3]) return 4;
  if(score>=t[2]) return 3;
  if(score>=t[1]) return 2;
  if(score>=t[0]) return 1;
  return 0;
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
    ctx.fillStyle = 'rgba(255,255,255,.12)';
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
    contribFloat.push(nw * ((comp.eff && comp.eff[k] != null) ? comp.eff[k] : d.s) * 100);   // (v9.07) GNSSは合成に使った値
    colors.push(COMP_COLOR[k] || '#60a5fa');
  }});
  var names = comp.used.map(function(k){{ return LABELS[k]; }});
  if(comp.wmiss > 0){{   // データの無い成分は中立0.5として合計に入っている
    names.push('データ無し(中立)'); labels.push('データ無し'); colors.push('#6b7280');
    contribFloat.push(0.5 * comp.wmiss / comp.wsum * 100);
  }}
  var contribInt = roundPartsToTotal(contribFloat, total);

  document.getElementById('dBreakdown').innerHTML = names.map(function(nm, i){{
    return '<div class="brk-row"><span>' + nm + '</span>' +
      '<span class="brk-val">' + (contribInt[i]>=0?'+':'') + contribInt[i] + '</span></div>';
  }}).join('');
  if(cell.c.gnss && comp.used.indexOf('gnss') >= 0){{   // (v9.00) どの観測点の値かを表示
    var g = cell.c.gnss;
    document.getElementById('dBreakdown').innerHTML +=
      '<div style="font-size:10px;color:rgba(235,238,245,.46);margin-top:4px">GNSS: ' + g.n +
      '（約' + g.d + 'km）水平' + g.r.toFixed(1) + 'mm/7日　基準日' + g.t +
      ((comp.eff && comp.eff.gnss > g.s + 1e-9) ? '　→ 他成分より大きく総合値を押し上げ' : '　→ 平常範囲のため総合値は変えません') + '</div>';
  }}
  if(cell.c.tec && comp.used.indexOf('tec') >= 0){{   // (v9.21) TECの内訳説明
    var tc = cell.c.tec;
    document.getElementById('dBreakdown').innerHTML +=
      '<div style="font-size:10px;color:rgba(235,238,245,.46);margin-top:4px">TEC: 広域成分を除いた局所偏差 ' + tc.r.toFixed(1) + ' TECU' +
      ((comp.eff && comp.eff.tec > tc.s + 1e-9) ? '　→ 他成分より大きく総合値を押し上げ' : '　→ 平常範囲のため総合値は変えません') +
      '（実験的指標。地震の前兆として確立したものではありません）</div>';
  }}
  drawBarChart(document.getElementById('dBarCanvas'), labels, contribInt, colors);

  // 前日比・推移は別セクションとして非同期取得（内訳の表示とはごちゃ混ぜにしない）
  document.getElementById('dPrevDay').textContent = '取得中…';
  document.getElementById('dTrendState').textContent = '取得中…';
  drawLineChart(document.getElementById('dTrendCanvas'), []);

  var sel = KEYS.filter(function(k){{ return selected[k]; }}).join(',');   // 欠けた成分も中立扱いで推移に反映するため、選択中の成分を渡す
  fetch('/data/risk_cell_trend?lat=' + cell.lat + '&lon=' + cell.lon + '&sel=' + encodeURIComponent(sel))
    .then(function(r){{ return r.json(); }})
    .then(function(res){{
      var pts = res.points || [];
      if(res.prev_day_score != null){{
        var diff = total - Math.round(res.prev_day_score*100);
        var color = diff>0 ? '#f87171' : (diff<0 ? '#4ade80' : '#9ca3af');
        document.getElementById('dPrevDay').innerHTML =
          '<span style="color:'+color+';font-weight:700">' + (diff>0?'+':'') + diff + '</span>' +
          ' <span style="color:rgba(235,238,245,.46)">(24時間前比)</span>';
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
  var lvN = {{1:0,2:0,3:0,4:0,5:0}};
  lastShownList = [];
  CELLS.forEach(function(cell){{
    var comp = computeComposite(cell);
    if(!comp) return;
    var lv = levelOf(comp.score);
    if(lv===0) return;  // (v7.56) Lv0(色無し)は地図・件数・ランキングいずれにも出さない
    shown++; lvN[lv]++;
    lastShownList.push({{cell:cell, comp:comp, lv:lv}});
    var rect = L.rectangle(
      [[cell.lat-GS/2, cell.lon-GS/2],[cell.lat+GS/2, cell.lon+GS/2]],
      {{color: lv===5 ? '#ffffff' : null, weight: lv===5 ? 2 : 0, fill:true, fillColor:RISK_COLOR[lv], fillOpacity: lv===5 ? 0.9 : {LEVEL_FILL_OPACITY}}}
    );
    rect.on('click', function(){{ showDetail(cell, comp, lv); }});
    rect.addTo(rectLayer);
  }});
  document.getElementById('cellN').textContent = shown;
  document.getElementById('lvCounts').textContent =
    '現在: Lv1 '+lvN[1]+' / Lv2 '+lvN[2]+' / Lv3 '+lvN[3]+' / Lv4 '+lvN[4]+' / Lv5 '+lvN[5];
  renderRankTable(lastShownList);
}}

redraw();
if(!THRESH.calibrated){{
  var cb = document.getElementById('calibBanner');
  cb.style.display = 'block';
  cb.textContent = '絶対基準（過去65日の実績）を計算中、または過去データが不足しています。'
    + '完了するまでリスクは表示しません（相対評価での代用はしません）。1分ごとに自動で再読み込みします。'
    + '（取得済みサンプル数: ' + (THRESH.n_samples||0) + '）';
  setTimeout(function(){{ location.reload(); }}, 60000);
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════
# メインページ（タブシェル）
# ══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════
# TAB 10: 検証（M6以上バックテスト / Molchan図）
# ══════════════════════════════════════════════════════
# 使い方: 期間と条件を決めて「実行」。計算はバックグラウンドで進み、1地震ごとに
# data/backtest/records_<設定>.jsonl へ保存される（途中で止めても、期間を広げても、
# 計算済みの地震は再利用される）。結果は /backtest/result、CSVは /backtest/events.csv。
def render_backtest(updated_str):
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{display:flex;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;color:#f3f4f6}
#panel{width:310px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-right:1px solid rgba(255,255,255,.14);overflow-y:auto;padding:14px}
#panel h2{font-size:14px;margin-bottom:4px}
.sub{font-size:11px;color:rgba(235,238,245,.55);line-height:1.6;margin-bottom:10px}
.sec{margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.09)}
.sec h3{font-size:11px;color:#93c5fd;margin-bottom:6px;letter-spacing:.5px}
.field{display:flex;align-items:center;gap:6px;margin-bottom:6px;font-size:12px}
.field label{flex:1;color:rgba(235,238,245,.75);line-height:1.35}
.field input{width:104px;padding:5px 6px;border-radius:7px;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.08);color:#f3f4f6;font-size:12px}
.hint{font-size:10px;color:rgba(235,238,245,.42);line-height:1.5;margin:-2px 0 6px}
button{cursor:pointer;font-size:12.5px;font-weight:600;color:#fff;border:1px solid rgba(255,255,255,.2);border-radius:10px;padding:9px 12px;background:linear-gradient(135deg,rgba(10,132,255,.45),rgba(124,92,255,.32))}
button:hover{filter:brightness(1.15)} button:disabled{opacity:.5;cursor:default;filter:none}
button.sec2{background:rgba(255,255,255,.08)}
.row{display:flex;gap:6px}.row button{flex:1}
#bar{height:7px;background:rgba(255,255,255,.1);border-radius:4px;overflow:hidden;margin:8px 0 4px;display:none}
#barIn{height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#8b5cf6);transition:width .4s}
#stat{font-size:11.5px;line-height:1.6;color:rgba(235,238,245,.8)}
#stat.err{color:#fca5a5}
#main{flex:1;overflow-y:auto;padding:16px 18px 40px}
#empty{margin-top:12vh;text-align:center;color:rgba(235,238,245,.45);font-size:13px;line-height:1.8}
.card{background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:12px 14px;margin-bottom:14px}
.card h3{font-size:12.5px;margin-bottom:8px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.tabs button{padding:6px 10px;font-size:11.5px;background:rgba(255,255,255,.07)}
.tabs button.on{background:linear-gradient(135deg,rgba(10,132,255,.6),rgba(124,92,255,.4))}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th,td{padding:5px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.08);white-space:nowrap}
th{color:rgba(235,238,245,.55);font-weight:600} th:first-child,td:first-child{text-align:left}
.scroll{overflow-x:auto}
.note{font-size:11px;color:rgba(235,238,245,.55);line-height:1.65;margin-top:8px}
.warn{color:#fcd34d}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;margin-top:6px}
.legend i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:5px}
svg text{fill:rgba(235,238,245,.7);font-size:11px}
.lv0{color:#94a3b8}.lv1{color:#86efac}.lv2{color:#fde047}.lv3{color:#fdba74}.lv4{color:#f87171}.lv5{color:#f0abfc}
@media (max-width:768px){body{flex-direction:column;overflow-y:auto;height:auto}#panel{width:100%;border-right:none;border-bottom:1px solid rgba(255,255,255,.14)}#main{overflow:visible}}
</style></head><body>
<div id="panel">
  <h2>検証：M6以上バックテスト</h2>
  <p class="sub">気象庁一元化カタログのM6以上について、発生の少し前の時点でアーカイブと同じ計算を行い、震源が「面積の何%の高リスクセル」に入っていたかをMolchan図で集計します。静的な長期ハザードマップと比較し、余震は分けて評価します。リスク値・レベルは確率ではありません。成分別の単独成績・95%信頼区間・年別/規模別の安定性も表示します（v9.21。これらを出すため、保存済みの旧記録は再計算になります）。</p>
  <div class="sec">
    <h3>期間（JST）</h3>
    <div class="field"><label>開始日</label><input type="date" id="pStart"></div>
    <div class="field"><label>終了日</label><input type="date" id="pEnd"></div>
    <div class="hint" id="covHint"></div>
    <div class="field"><label>対象のM以上</label><input type="number" id="pMag" step="0.1" value="6.0"></div>
    <div class="field"><label>震源の深さ上限(km, 0=なし)</label><input type="number" id="pDepth" step="10" value="0"></div>
  </div>
  <div class="sec">
    <h3>評価する時点</h3>
    <div class="field"><label>直前(発生の何時間前)</label><input type="number" id="pLead" step="1" value="24"></div>
    <div class="field"><label>直後(発生の何時間後・参考用)</label><input type="number" id="pAfter" step="0.5" value="1"></div>
  </div>
  <div class="sec">
    <h3>基準の作り直し（日付ごとの基準）</h3>
    <div class="field"><label>作り直す間隔(日, 0=1地震ごと)</label><input type="number" id="pRebuild" step="1" value="30"></div>
    <div class="field"><label>基準のサンプル間隔(時間)</label><input type="number" id="pStep" step="6" value="24"></div>
    <div class="hint">基準は「評価時点より前の65日」だけで作ります（先読みなし）。作り直し間隔を短くするほど厳密ですが、基準1回ぶんの計算が地震の数だけ増えます。間隔0は最も厳密で最も遅い設定です。</div>
  </div>
  <div class="sec">
    <h3>余震の判定</h3>
    <div class="field"><label>前の大地震のM以上</label><input type="number" id="pPrev" step="0.1" value="6.0"></div>
    <div class="hint">近く(50〜400km)・直前(30〜365日)の範囲は、前の大地震の規模から自動で決めます。</div>
  </div>
  <div class="row"><button id="runBtn" onclick="startRun()">実行</button><button id="cancelBtn" class="sec2" onclick="cancelRun()" disabled>中止</button></div>
  <div id="bar"><div id="barIn"></div></div>
  <div id="stat"></div>
  <div class="row" style="margin-top:10px"><button class="sec2" onclick="loadResult()">結果を再読み込み</button><button class="sec2" onclick="dlCsv()">CSV</button></div>
</div>
<div id="main"><div id="empty">期間を決めて「実行」を押すと、結果がここに表示されます。<br>計算済みの地震は保存されるので、中断しても期間を広げても続きから進みます。</div></div>
<script>
var DEF = __DEFAULTS__;
var S = {data:null, group:'all', variant:'tau', zoom:false, timer:null, t0:0};
var COLORS = {dyn:'#60a5fa', st_seis:'#fb923c', st_comb:'#34d399'};
function $(id){return document.getElementById(id)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function pct(x,d){return x==null?'-':(x*100).toFixed(d==null?1:d)+'%'}
function ciTxt(c){return c?'<br><small>95%CI '+Math.round(c[0]*100)+'〜'+Math.round(c[1]*100)+'%</small>':''}
function aciTxt(c){return c?'<br><small>95%CI '+c[0].toFixed(2)+'〜'+c[1].toFixed(2)+'</small>':''}
function params(){
  return {start:$('pStart').value, end:$('pEnd').value, min_mag:$('pMag').value, max_depth_km:$('pDepth').value,
          lead_hours:$('pLead').value, after_hours:$('pAfter').value, rebuild_days:$('pRebuild').value,
          calib_step_hours:$('pStep').value, prev_mag:$('pPrev').value};
}
function qs(o){return Object.keys(o).map(function(k){return encodeURIComponent(k)+'='+encodeURIComponent(o[k])}).join('&')}
function setStat(msg, err){var s=$('stat'); s.className=err?'err':''; s.innerHTML=msg}
function init(){
  fetch('/backtest/info').then(function(r){return r.json()}).then(function(d){
    var p = d.last_params || DEF;
    $('pStart').value=p.start; $('pEnd').value=p.end;
    ['Mag:min_mag','Depth:max_depth_km','Lead:lead_hours','After:after_hours','Rebuild:rebuild_days','Step:calib_step_hours','Prev:prev_mag'].forEach(function(s){
      var a=s.split(':'); if(p[a[1]]!=null) $('p'+a[0]).value=p[a[1]];
    });
    $('covHint').textContent = d.coverage ? ('カタログ収録: '+d.coverage.first+' 〜 '+d.coverage.last+'（基準に約125日必要なため、先頭から約4か月ぶんは評価できません）') : 'カタログ(data/jma_catalog/)が見つかりません';
    if(d.job && d.job.running){ watch(); }
    else if(d.has_records){ loadResult(); }
  }).catch(function(e){ setStat('初期化に失敗: '+esc(e.message), true) });
}
function startRun(){
  $('runBtn').disabled=true; setStat('開始しています…');
  fetch('/backtest/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params())})
   .then(function(r){return r.json().then(function(j){ if(!r.ok) throw new Error(j.error||('HTTP '+r.status)); return j; })})
   .then(function(){ S.t0=Date.now(); watch(); })
   .catch(function(e){ $('runBtn').disabled=false; setStat('開始できません: '+esc(e.message), true) });
}
function cancelRun(){ fetch('/backtest/cancel',{method:'POST'}); setStat('中止を要求しました（今の基準の評価が終わり次第止まります）'); }
function watch(){
  $('runBtn').disabled=true; $('cancelBtn').disabled=false; $('bar').style.display='block';
  clearTimeout(S.timer);
  (function tick(){
    fetch('/backtest/status').then(function(r){return r.json()}).then(function(s){
      var tot=s.total||0, dn=s.done||0;
      $('barIn').style.width = (tot? (100*dn/tot):0)+'%';
      var sk = Object.keys(s.skipped||{}).filter(function(k){return s.skipped[k]>0}).map(function(k){return k+' '+s.skipped[k]+'件'}).join('、');
      setStat(esc(s.phase||'')+'<br>'+dn+' / '+tot+' 件'+(s.elapsed?'（'+Math.round(s.elapsed/60*10)/10+'分経過）':'')+(sk?'<br>対象外: '+esc(sk):'')+(s.message&&!s.running?'<br>'+esc(s.message):''), !!s.error);
      if(s.error) setStat('エラー: '+esc(s.error), true);
      if(dn>0 && (dn%5===0 || !s.running)) loadResult(true);
      if(s.running){ S.timer=setTimeout(tick, 2500); }
      else { $('runBtn').disabled=false; $('cancelBtn').disabled=true; if(!s.error) loadResult(); }
    }).catch(function(){ S.timer=setTimeout(tick, 5000) });
  })();
}
function loadResult(quiet){
  fetch('/backtest/result?'+qs(params())).then(function(r){return r.json().then(function(j){ if(!r.ok) throw new Error(j.error||('HTTP '+r.status)); return j; })})
   .then(function(d){ S.data=d; render(); })
   .catch(function(e){ if(!quiet) setStat('結果を読めません: '+esc(e.message), true) });
}
function dlCsv(){ window.location='/backtest/events.csv?'+qs(params()); }
function pick(k,v){ S[k]=v; render(); }

function drawMolchan(G){
  var W=540,H=450,ml=54,mr=16,mt=14,mb=50, pw=W-ml-mr, ph=H-mt-mb;
  var xmax = S.zoom?0.3:1.0, grid=S.data.tau_grid;
  function X(t){return ml+pw*Math.min(t,xmax)/xmax} function Y(v){return mt+ph*(1-v)}
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" style="max-width:620px">';
  var xt = S.zoom?[0,.05,.1,.15,.2,.25,.3]:[0,.2,.4,.6,.8,1];
  xt.forEach(function(t){ s+='<line x1="'+X(t)+'" x2="'+X(t)+'" y1="'+mt+'" y2="'+(mt+ph)+'" stroke="rgba(255,255,255,.08)"/><text x="'+X(t)+'" y="'+(mt+ph+16)+'" text-anchor="middle">'+Math.round(t*100)+'%</text>'; });
  [0,.2,.4,.6,.8,1].forEach(function(v){ s+='<line x1="'+ml+'" x2="'+(ml+pw)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="rgba(255,255,255,.08)"/><text x="'+(ml-6)+'" y="'+(Y(v)+4)+'" text-anchor="end">'+Math.round(v*100)+'%</text>'; });
  s+='<text x="'+(ml+pw/2)+'" y="'+(H-8)+'" text-anchor="middle">警戒した面積の割合（高リスクセルから順に）</text>';
  s+='<text transform="translate(14,'+(mt+ph/2)+') rotate(-90)" text-anchor="middle">外れ率（入らなかった地震の割合）</text>';
  s+='<line x1="'+X(0)+'" y1="'+Y(1)+'" x2="'+X(1)+'" y2="'+Y(0)+'" stroke="#9ca3af" stroke-dasharray="5 4"/>';
  var sig=G.sig05||[], pts=[];
  grid.forEach(function(t,i){ if(sig[i]!=null && t<=xmax) pts.push(X(t)+','+Y(sig[i])); });
  if(pts.length>1) s+='<polyline fill="none" stroke="#c084fc" stroke-width="1.2" stroke-dasharray="2 3" points="'+pts.join(' ')+'"/>';
  ['st_seis','st_comb','dyn'].forEach(function(m){
    var c=G.models[m][S.variant].curve; if(!c.length) return;
    var p=[]; grid.forEach(function(t,i){ if(t<=xmax+1e-9) p.push(X(t)+','+Y(c[i])); });
    s+='<polyline fill="none" stroke="'+COLORS[m]+'" stroke-width="'+(m==='dyn'?2.6:1.8)+'" points="'+p.join(' ')+'"/>';
  });
  return s+'</svg>';
}
function levelCell(l){ return '<span class="lv'+l+'">Lv'+l+'</span>'; }
function render(){
  var D=S.data, G=D.groups[S.group], L=D.group_labels, M=D.model_labels, h='';
  if(!D.n_events){ $('main').innerHTML='<div id="empty">この条件で保存済みの結果はまだありません。「実行」を押してください。</div>'; return; }
  h+='<div class="card"><div class="tabs">';
  Object.keys(L).forEach(function(g){ h+='<button class="'+(S.group===g?'on':'')+'" onclick="pick(\\'group\\',\\''+g+'\\')">'+esc(L[g])+'（'+D.groups[g].n+'件）</button>'; });
  h+='</div><div class="tabs"><button class="'+(S.variant==='tau'?'on':'')+'" onclick="pick(\\'variant\\',\\'tau\\')">震源セルだけで判定</button>'+
     '<button class="'+(S.variant==='tau_nb'?'on':'')+'" onclick="pick(\\'variant\\',\\'tau_nb\\')">周囲±1セル(約55km)も含める</button>'+
     '<button class="'+(S.zoom?'on':'')+'" onclick="pick(\\'zoom\\','+(!S.zoom)+')">横軸 0〜30%に拡大</button></div>';
  if(G.n<10) h+='<div class="note warn">この区分は '+G.n+' 件しかないため、結果は参考程度です（偶然で説明できる範囲が広い）。</div>';
  if(G.n===0){ $('main').innerHTML=h+'</div>'; return; }
  h+='<h3>Molchan図（'+esc(L[S.group])+'・'+G.n+'件）</h3>'+drawMolchan(G);
  h+='<div class="legend"><span><i style="background:'+COLORS.dyn+'"></i>'+esc(M.dyn)+'</span><span><i style="background:'+COLORS.st_comb+'"></i>'+esc(M.st_comb)+'</span><span><i style="background:'+COLORS.st_seis+'"></i>'+esc(M.st_seis)+'</span><span><i style="background:#9ca3af"></i>ランダム</span><span><i style="background:#c084fc"></i>ランダムより有意に良い境界(片側5%)</span></div>';
  h+='<div class="note">左下に近いほど良い（少ない面積で多くの地震を捉えている）。ランダム（点線の対角線）より下にあり、紫の境界よりも下なら、偶然とは言いにくい水準です。</div></div>';

  h+='<div class="card"><h3>指標（'+(S.variant==='tau'?'震源セルのみ':'周囲込み')+'）</h3><div class="scroll"><table><tr><th>モデル</th><th>上位5%以内</th><th>上位10%以内</th><th>上位20%以内</th><th>中央順位</th><th>AUC</th><th>p(10%)</th></tr>';
  ['dyn','st_comb','st_seis'].forEach(function(m){ var x=G.models[m][S.variant].metrics;
    h+='<tr><td>'+esc(M[m])+'</td><td>'+pct(x.hit5,0)+'（'+x.k5+'/'+x.n+'）</td><td>'+pct(x.hit10,0)+'（'+x.k10+'/'+x.n+'）'+ciTxt(x.ci10)+'</td><td>'+pct(x.hit20,0)+'（'+x.k20+'/'+x.n+'）</td><td>上位'+pct(x.median_tau)+'</td><td>'+x.auc.toFixed(3)+aciTxt(x.auc_ci)+'</td><td>'+x.p10+'</td></tr>'; });
  h+='</table></div><div class="note">「上位10%以内」= 面積の10%だけ警戒したとき、そのうち何%の地震が入っていたか（ランダムなら10%）。AUC=1−平均順位（ランダムで0.5）。p(10%)=ランダムでこれ以上入る確率。</div>';
  h+='<h3 style="margin-top:12px">動的 vs 静的（同じ地震どうしの順位を比較）</h3><div class="scroll"><table><tr><th>比較相手</th><th>動的が上位</th><th>動的が下位</th><th>順位差の中央値</th><th>符号検定p</th></tr>';
  ['st_comb','st_seis'].forEach(function(m){ var x=G.paired[m][S.variant];
    h+='<tr><td>'+esc(M[m])+'</td><td>'+x.dyn_better+'件</td><td>'+x.dyn_worse+'件</td><td>'+(x.median_diff==null?'-':(x.median_diff*100).toFixed(1)+'pt')+'</td><td>'+x.p+'</td></tr>'; });
  h+='</table></div><div class="note">順位差は「動的−静的」。負なら動的のほうが上位。pが大きい(目安0.05以上)なら、どちらが良いとは言えません。</div></div>';

  h+='<div class="card"><h3>レベル別（動的・実際の運用に近い見方）</h3><div class="scroll"><table><tr><th>レベル以上</th><th>面積比</th><th>的中率(震源セル)</th><th>倍率</th><th>面積比(周囲込み)</th><th>的中率(周囲込み)</th><th>倍率</th></tr>';
  G.levels.forEach(function(r){ h+='<tr><td>'+levelCell(r.level)+'</td><td>'+pct(r.area,2)+'</td><td>'+pct(r.hit,0)+'</td><td>'+(r.lift==null?'-':'×'+r.lift)+'</td><td>'+pct(r.area_nb,2)+'</td><td>'+pct(r.hit_nb,0)+'</td><td>'+(r.lift_nb==null?'-':'×'+r.lift_nb)+'</td></tr>'; });
  h+='</table></div><div class="note">倍率=的中率÷面積比（1ならランダムと同じ）。面積比は評価した各時点の平均。</div></div>';

  // (v9.21) 成分別の単独成績: どの成分が効いているか（重み・成分の取捨の根拠）
  var comps = G.components || [];
  if(comps.length){
    h+="<div class='card'><h3>成分別の単独成績（震源セル・動的の各成分だけで警戒した場合）</h3><div class='scroll'><table><tr><th>成分</th><th>現在の重み</th><th>件数</th><th>AUC</th><th>上位10%以内</th><th>p(10%)</th><th>スコア無し</th></tr>";
    comps.forEach(function(c){
      var cls = (c.auc_ci && c.auc_ci[0] > 0.5) ? "lv1" : ((c.auc_ci && c.auc_ci[1] < 0.5) ? "lv4" : "");
      h+="<tr><td>"+esc(c.label)+"</td><td>"+c.weight+"</td><td>"+c.n+"</td><td class='"+cls+"'>"+c.auc.toFixed(3)+aciTxt(c.auc_ci)+"</td><td>"+pct(c.hit10,0)+"</td><td>"+c.p10+"</td><td>"+pct(c.no_score,0)+"</td></tr>";
    });
    h+="</table></div><div class='note'>AUC 0.5=ランダム。緑=95%信頼区間が0.5より上(偶然とは言いにくい)、赤=0.5より下(逆効果)。「スコア無し」=その成分が無く警戒できなかった割合。成分どうしは相関があるので、単独成績が良くても合成で効くとは限りません。重みを変える際はこの表と上の合成成績を併せて見てください。TEC・GNSS・気圧は過去値を保持していないためバックテストの対象外です。</div></div>";
  }
  // (v9.21) 年別・規模別の安定性
  var bd = G.breakdown || {};
  function bdTable(title, rows){
    if(!rows || !rows.length) return "";
    var t="<h3 style='margin-top:12px'>"+title+"</h3><div class='scroll'><table><tr><th></th><th>件数</th><th>動的AUC</th><th>動的 上位10%</th><th>静的(地震+断層)AUC</th><th>静的 上位10%</th></tr>";
    rows.forEach(function(r){
      var cls = r.n<5 ? "warn" : "";
      t+="<tr><td>"+esc(r.label)+"</td><td class='"+cls+"'>"+r.n+"</td><td>"+(r.dyn_auc==null?"-":r.dyn_auc.toFixed(3))+"</td><td>"+pct(r.dyn_hit10,0)+"</td><td>"+(r.st_auc==null?"-":r.st_auc.toFixed(3))+"</td><td>"+pct(r.st_hit10,0)+"</td></tr>";
    });
    return t+"</table></div>";
  }
  if((bd.by_year||[]).length || (bd.by_mag||[]).length){
    h+="<div class='card'><h3>安定性（震源セルのみ・区分ごと）</h3>"+bdTable("年別", bd.by_year)+bdTable("規模別", bd.by_mag)+
       "<div class='note'>特定の年（大地震列があった年など）だけ成績が良い場合、全体の成績はその年に引っ張られています。件数が5件未満の行（黄色）は参考程度です。</div></div>";
  }

  h+='<div class="card"><h3>地震ごとの記録（'+esc(L[S.group])+'）</h3><div class="scroll"><table><tr><th>時刻(JST)</th><th>震源名</th><th>M</th><th>直前</th><th>直後(参考)</th><th>順位</th><th>周囲Lv</th><th>静的順位</th><th>区分</th></tr>';
  var rows=(D.rows||[]).filter(function(r){ return S.group==='all'||r.group===S.group; });
  rows.slice(-400).reverse().forEach(function(r){
    var g={independent:'独立',aftershock:'余震・続発',unseen:'直前発生'}[r.group];
    h+='<tr><td>'+esc(r.t_jst.slice(0,16))+'</td><td>'+esc(r.place)+'</td><td>'+r.mag+'</td><td>'+(r.dyn.score==null?'-':r.dyn.score)+' '+levelCell(r.dyn.level)+'</td><td>'+(r.after.score==null?'-':r.after.score)+' '+levelCell(r.after.level)+'</td><td>上位'+pct(r.dyn.tau)+'</td><td>'+levelCell(r.dyn.nb_level)+'</td><td>上位'+pct(r.st_comb.tau)+'</td><td>'+g+(r.prev?'<br><small>M'+r.prev.mag+' '+r.prev.dist_km+'km '+r.prev.gap_days+'日前</small>':'')+'</td></tr>'; });
  h+='</table></div><div class="note">直前/直後はリスク値(0〜100)とレベル。「順位」は震源セルが全セル中の上位何%か。震源名はカタログの英語表記です。全件はCSVで取得できます。</div></div>';
  $('main').innerHTML=h;
}
init();
</script></body></html>"""
    d = datetime.now(JST)
    cov = _jma_catalog_coverage()
    default_end = (cov["last"].astimezone(JST) - timedelta(days=2)).strftime("%Y-%m-%d") if cov else d.strftime("%Y-%m-%d")
    default_start = (datetime.strptime(default_end, "%Y-%m-%d") - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    defaults = {**BT_DEFAULT_PARAMS, "start": default_start, "end": default_end}
    return html.replace("__DEFAULTS__", json.dumps(defaults, ensure_ascii=False))


# ══════════════════════════════════════════════════════
# TAB 9: アーカイブ（過去の任意時点における統合リスクマップ検索）
# ══════════════════════════════════════════════════════
# ★ 旧仕様（1時間ごとのETAS/b値スナップショット一覧を左のリストから選んで
# 見る形式）は撤去した。代わりに、日付と時刻を指定すると「その時刻」を基準に
# 1か月前までの地震データを取得し直し、統合リスクマップ（ETAS/b値/活断層/
# プレート境界）をその時点の状態として再計算・表示する形式にしている。
# なお1時間ごとのスナップショット保存自体（data/snapshots/）は、統合リスク
# マップタブのセル詳細に出る「推移（直近7日）」グラフが引き続き利用するため、
# バックエンド側の保存処理は維持している。
def render_snapshots(updated_str):
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
__LEAFLET_CDN__
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{display:flex;height:100vh;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif;color:#f3f4f6}
#panel{width:300px;flex-shrink:0;background:rgba(255,255,255,.055);backdrop-filter:blur(28px) saturate(180%);-webkit-backdrop-filter:blur(28px) saturate(180%);border-right:1px solid rgba(255,255,255,.14);overflow-y:auto;padding:14px}
#panel h2{font-size:14px;color:#f3f4f6;margin-bottom:4px}
#panel p.sub{font-size:11px;color:rgba(235,238,245,.46);margin-bottom:14px;line-height:1.6}
.sec{margin-bottom:16px}
.sec h3{font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:8px;border-bottom:1px solid rgba(255,255,255,.14);padding-bottom:4px}
.field{margin-bottom:10px}
.field label{display:block;font-size:11px;color:rgba(235,238,245,.62);margin-bottom:4px}
.field input{width:100%;padding:7px 8px;font-size:12px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);border:1px solid rgba(255,255,255,.18);
    border-radius:14px;color:#f3f4f6}
.field input:focus{outline:none;border-color:#3b82f6}
#fetchBtn{width:100%;padding:9px 0;font-size:12.5px;font-weight:700;color:#fff;cursor:pointer;
    background:linear-gradient(135deg,#0a84ff,#7c5cff);border:none;border-radius:16px;margin-top:2px}
#fetchBtn:hover{filter:brightness(1.1)}
#fetchBtn:disabled{opacity:.5;cursor:default;filter:none}
#statusBox{font-size:11px;color:rgba(235,238,245,.62);line-height:1.8;margin-top:12px;padding:10px 12px;
    background:rgba(255,255,255,.07);border-radius:16px;border:1px solid rgba(255,255,255,.14);display:none}
#statusBox b{color:#60a5fa}
#statusBox.err{border-color:#7f1d1d;color:#fca5a5}
.chk-row{display:flex;align-items:center;gap:8px;padding:5px 4px;border-radius:12px;font-size:12px}
.chk-row:hover{background:rgba(255,255,255,.07)}
.chk-row input{width:15px;height:15px;flex-shrink:0}
.chk-row .clabel{flex:1;color:rgba(240,242,247,.85)}
.chk-row .cweight{font-size:10px;color:rgba(235,238,245,.46)}
#cellCount{font-size:11px;color:rgba(235,238,245,.62);margin-top:8px}
#cellCount span{color:#60a5fa;font-weight:700}
.note{font-size:10.5px;color:rgba(235,238,245,.46);line-height:1.7;margin-top:10px}
#mp{flex:1;overflow:hidden;position:relative}
#map{width:100%;height:100%}
/* 地図タイル自体を少し明るく・彩度を落として、暗色オーバーレイとの同化を防ぐ(v7.44) */
.leaflet-tile-pane{filter:brightness(1.18) saturate(0.8) contrast(0.95)}
#lg{position:absolute;bottom:20px;left:20px;z-index:1000;background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
    padding:10px 13px;border-radius:18px;border:1px solid rgba(255,255,255,.18);font-size:11px;line-height:1.9;color:#f3f4f6}
#mapEmpty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    color:rgba(235,238,245,.4);font-size:13px;text-align:center;padding:20px}
#detailBox{display:none;position:absolute;top:14px;right:14px;z-index:1500;width:270px;
    background:rgba(26,29,44,.5);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid rgba(255,255,255,.18);border-radius:20px;padding:12px 14px;
    font-size:11px;color:rgba(240,242,247,.85)}
#detailBox .dHead{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
#dTitle{font-weight:700;font-size:14px;color:#f3f4f6}
#dClose{flex-shrink:0;width:20px;height:20px;border:none;border-radius:12px;background:rgba(255,255,255,.18);backdrop-filter:blur(10px) saturate(160%);-webkit-backdrop-filter:blur(10px) saturate(160%);color:rgba(235,238,245,.72);
    cursor:pointer;font-size:12px;line-height:1}
#dClose:hover{background:rgba(255,255,255,.3);color:#fff}
#dLoc{font-size:10px;color:rgba(235,238,245,.46);margin:2px 0 10px}
.brk-row{display:flex;justify-content:space-between;font-size:11px;padding:2px 0;color:rgba(235,238,245,.72)}
.brk-val{font-weight:700;color:#f3f4f6}
canvas.dChart{display:block;width:100%;margin-top:6px}

@media (max-width:768px){
  body{flex-direction:column;overflow-y:auto}
  #panel{width:100%;border-right:none;border-bottom:1px solid rgba(255,255,255,.14);max-height:46vh}
  #mp{flex:none;height:46vh;min-height:220px}
  #detailBox{width:82vw;max-width:270px;right:8px;top:8px}
}
</style></head><body>
<div id="panel">
  <h2>過去時点マップ検索</h2>
  <p class="sub">日付と時刻を指定すると、その時刻から1か月前までの地震データを取得し直し、その時点の統合リスクマップ（ETAS・b値・活断層・プレート境界）を再現します。GNSS変位は未実装です。</p>

  <div class="sec">
    <h3>基準日時（JST）</h3>
    <div class="field"><label>日付</label><input type="date" id="inDate"></div>
    <div class="field"><label>時刻</label><input type="time" id="inTime" value="00:00"></div>
    <div style="margin-bottom:10px;font-size:11.5px;color:rgba(235,238,245,.75);line-height:1.5"><label><input type="checkbox" id="inCalib" style="width:auto;margin-right:6px">この日付で基準を作り直す（古い日付向け）</label>
      <div style="font-size:10px;color:rgba(235,238,245,.42);margin-top:3px">基準日より前の65日だけで基準を作ります（一元化カタログの範囲のみ。初回は数十秒〜数分）。</div></div>
    <button id="fetchBtn" onclick="runFetch()">この時点のリスクマップを取得</button>
    <div id="statusBox"></div>
  </div>

  <div class="sec" id="chkSec" style="display:none">
    <h3>使用データ（チェックで選択）</h3>
    <div class="chk-row"><input type="checkbox" id="chk_etas" checked onchange="onToggle('etas')">
      <span class="clabel">ETAS（地震活動度）</span><span class="cweight">w=__W_ETAS__</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_bvalue" checked onchange="onToggle('bvalue')">
      <span class="clabel">b値（Gutenberg-Richter）</span><span class="cweight">w=__W_BVALUE__</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_fault" checked onchange="onToggle('fault')">
      <span class="clabel">活断層 応力負荷</span><span class="cweight">w=__W_FAULT__</span></div>
    <div class="chk-row"><input type="checkbox" id="chk_plate" checked onchange="onToggle('plate')">
      <span class="clabel">プレート境界 応力負荷</span><span class="cweight">w=__W_PLATE__</span></div>
    <div class="chk-row disabled"><input type="checkbox" disabled>
      <span class="clabel">GNSS変位（地殻変動）</span><span class="cbadge">未実装</span></div>
  </div>

  <div id="cellCount" style="display:none">表示中のセル数: <span id="cellN">0</span></div>
  <div class="note">
    気圧偏差・TECは過去分のデータを保持していないため、この検索には含まれません。<br>
    GNSS変位は、アーカイブでは未実装です（統合リスクマップタブでは現在値のみで使えます）。<br>
    直近60日以内は気象庁(JMA)・P2P地震情報を統合した高精度データを使用します。<br>
    それより古い日時は、サーバーに気象庁一元化震源カタログの年別ファイルがある期間ならそれを使います。
    無い期間はUSGSの過去カタログから都度取得します（数年前まで指定可能）。
    ただしUSGSは国内のごく小規模な地震が漏れる場合があり、震度情報も含まれません。
  </div>
</div>
<div id="mp">
  <div id="map"></div>
  <div id="mapEmpty">日付と時刻を指定して取得してください</div>
  <div id="lg" style="display:none">
    <b>統合リスクレベル</b><br>
    <span style="color:#0c000c;background:#0c000c;opacity:0.9;padding:0 6px;border:1px solid #fff">■</span> Lv5（特に稀な高リスク）<br>
    <span style="color:#8000ff;opacity:0.55">■</span> Lv4（警戒）<br>
    <span style="color:#ff0000;opacity:0.55">■</span> Lv3（注意）<br>
    <span style="color:#ffe600;opacity:0.55">■</span> Lv2（やや注意）<br>
    <span style="color:#66ccff;opacity:0.55">■</span> Lv1（平常）<br>
    <hr style="border-color:rgba(255,255,255,.18);margin:5px 0">
    <small style="color:rgba(235,238,245,.46)">Lv0（しきい値未満）のセルは地図上に表示されません</small><br>
    <small>指定時点における相対リスク指数<br>（発生確率を意味するものではありません）</small>
  </div>
  <div id="detailBox">
    <div class="dHead">
      <div><div id="dTitle">-</div><div id="dLoc"></div></div>
      <button id="dClose" onclick="closeDetail()" title="閉じる">✕</button>
    </div>
    <div id="dBreakdown"></div>
    <canvas id="dBarCanvas" class="dChart" width="230" height="100"></canvas>
  </div>
</div>
<script>
var RISK_COLOR = {5:'#0c000c',4:'#8000ff',3:'#ff0000',2:'#ffe600',1:'#66ccff'};
var COMP_COLOR = {etas:'#ef4444', bvalue:'#f97316', fault:'#facc15', plate:'#38bdf8'};
var WEIGHTS = __WEIGHTS_JSON__;
var LABELS  = {etas:'ETAS', bvalue:'b値', fault:'活断層', plate:'プレート境界'};
var KEYS = ['etas','bvalue','fault','plate'];
var GS = __RISK_GRID_SIZE__;
var selected = {etas:true, bvalue:true, fault:true, plate:true};
var CELLS = [];
var lastShownList = [];
var map = null, rectLayer = null;

function onToggle(key){
  selected[key] = document.getElementById('chk_'+key).checked;
  redraw();
}

// (v8.21) 通常版と同じ: 履歴のある成分がそのセルに無い場合は中立0.5で補う
function computeComposite(cell){
  var wsum=0, ssum=0, used=[], wmiss=0;
  KEYS.forEach(function(k){
    if(!selected[k]) return;
    var wk = WEIGHTS[k];
    if(cell.c[k]){ wsum += wk; ssum += wk*cell.c[k].s; used.push(k); }
    else { wmiss += wk; }
  });
  if(wsum<=0) return null;
  var wall = wsum + wmiss;
  return {score: (ssum + 0.5*wmiss)/wall, used: used, wsum: wall, wmiss: wmiss};
}
// (v7.56) アーカイブ版も通常版(riskmapタブ)と同じ絶対基準に統一。
// (v7.56) アーカイブ版も通常版(riskmapタブ)と同じ絶対基準に統一。Lv0(色無し)も同様。
// (v8.21) 通常版と同じ、過去実績からの自動キャリブレーション済みしきい値を使う
// （サーバーが /archive/historical_riskmap の応答に同梱してTHRESHへ設定する）。
var THRESH = {by_subset:{}, default:[0.2,0.4,0.6,0.85,0.97]};
function currentThresholds(){
  var ks = KEYS.filter(function(k){ return selected[k]; });
  return (THRESH.by_subset && THRESH.by_subset[ks.join(',')]) || THRESH.default;
}
function levelOf(score){
  var t = currentThresholds();
  if(score>=t[4]) return 5;
  if(score>=t[3]) return 4;
  if(score>=t[2]) return 3;
  if(score>=t[1]) return 2;
  if(score>=t[0]) return 1;
  return 0;
}

function roundPartsToTotal(parts, total){
  var floors = parts.map(Math.floor);
  var used = floors.reduce(function(a,b){return a+b;}, 0);
  var remainder = Math.round(total - used);
  var order = parts.map(function(p,i){return {i:i, frac:p-Math.floor(p)};})
                   .sort(function(a,b){return b.frac-a.frac;});
  var result = floors.slice();
  for(var k=0; k<remainder && k<order.length; k++){ result[order[k].i] += 1; }
  return result;
}

function drawBarChart(canvas, labels, values, colors){
  var ctx = canvas.getContext('2d');
  var W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);
  var n = labels.length;
  if(n===0) return;
  var maxV = Math.max(1, Math.max.apply(null, values.map(function(v){return Math.abs(v);})));
  var labelW = 60, valW = 28;
  var trackW = W - labelW - valW;
  var rowH = H / n;
  var barH = Math.min(14, rowH - 6);
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  for(var i=0; i<n; i++){
    var cy = rowH*i + rowH/2;
    ctx.fillStyle = '#9ca3af';
    ctx.fillText(labels[i], 0, cy);
    ctx.fillStyle = 'rgba(255,255,255,.12)';
    ctx.fillRect(labelW, cy-barH/2, trackW, barH);
    var w = Math.max(1, trackW * (Math.abs(values[i]) / maxV));
    ctx.fillStyle = colors[i] || '#60a5fa';
    ctx.fillRect(labelW, cy-barH/2, w, barH);
    ctx.fillStyle = '#f3f4f6';
    ctx.fillText((values[i]>=0?'+':'')+values[i], labelW+trackW+4, cy);
  }
}

function closeDetail(){ document.getElementById('detailBox').style.display = 'none'; }

function showDetail(cell, comp, lv){
  document.getElementById('detailBox').style.display = 'block';
  var total = Math.round(comp.score*100);
  document.getElementById('dTitle').textContent = '総合リスク ' + total + ' (Lv' + lv + ')';
  document.getElementById('dLoc').textContent = '緯度' + cell.lat.toFixed(2) + ' / 経度' + cell.lon.toFixed(2);
  var labels=[], contribFloat=[], colors=[];
  comp.used.forEach(function(k){
    var d = cell.c[k];
    var nw = WEIGHTS[k] / comp.wsum;
    labels.push(LABELS[k]); contribFloat.push(nw * d.s * 100); colors.push(COMP_COLOR[k] || '#60a5fa');
  });
  var names = comp.used.map(function(k){ return LABELS[k]; });
  if(comp.wmiss > 0){
    names.push('データ無し(中立)'); labels.push('データ無し'); colors.push('#6b7280');
    contribFloat.push(0.5 * comp.wmiss / comp.wsum * 100);
  }
  var contribInt = roundPartsToTotal(contribFloat, total);
  document.getElementById('dBreakdown').innerHTML = names.map(function(nm, i){
    return '<div class="brk-row"><span>' + nm + '</span>' +
      '<span class="brk-val">' + (contribInt[i]>=0?'+':'') + contribInt[i] + '</span></div>';
  }).join('');
  drawBarChart(document.getElementById('dBarCanvas'), labels, contribInt, colors);
}

function ensureMap(){
  if(map) return;
  map = L.map('map',{center:[36,138],zoom:5,preferCanvas:true});
  __DARK_TILE__
  __GEOJSON_JS__
}

function redraw(){
  if(!map) return;
  if(rectLayer) map.removeLayer(rectLayer);
  rectLayer = L.layerGroup().addTo(map);
  var shown = 0;
  lastShownList = [];
  CELLS.forEach(function(cell){
    var comp = computeComposite(cell);
    if(!comp) return;
    var lv = levelOf(comp.score);
    if(lv===0) return;  // (v7.56) Lv0(色無し)は地図・件数・ランキングいずれにも出さない
    shown++;
    lastShownList.push({cell:cell, comp:comp, lv:lv});
    var rect = L.rectangle(
      [[cell.lat-GS/2, cell.lon-GS/2],[cell.lat+GS/2, cell.lon+GS/2]],
      {color: lv===5 ? '#ffffff' : null, weight: lv===5 ? 2 : 0, fill:true, fillColor:RISK_COLOR[lv], fillOpacity: lv===5 ? 0.9 : 0.55}
    );
    rect.on('click', function(){ showDetail(cell, comp, lv); });
    rect.addTo(rectLayer);
  });
  document.getElementById('cellN').textContent = shown;
}

function fmtStatus(d){
  var srcLabel;
  if(d.data_source === 'jma_catalog'){
    srcLabel = '気象庁一元化震源カタログ（60日超のため。M' +
               (d.min_mag != null ? Number(d.min_mag).toFixed(1) : '') + '以上のみ使用）';
  } else if(d.data_source === 'usgs_historical'){
    srcLabel = 'USGS過去カタログ（60日超のため。震度情報なし・小規模地震が漏れる場合あり）';
  } else {
    srcLabel = 'JMA/P2P統合データ（直近60日）';
  }
  var calibLine = (d.calib_mode === 'date') ? ('基準: <b>' + d.calib_anchor_jst + ' より前の65日で作り直し</b><br>') : '';
  return '基準時刻: <b>' + d.ref_time_jst + '</b><br>' + calibLine +
         '取得期間: ' + d.period_start_jst + ' 〜 ' + d.ref_time_jst + '<br>' +
         'データソース: <b>' + srcLabel + '</b><br>' +
         '使用した地震データ: <b>' + d.quake_count + '</b>件 ／ セル数: <b>' + d.cells.length + '</b>';
}

function runFetch(){
  var dateVal = document.getElementById('inDate').value;
  var timeVal = document.getElementById('inTime').value || '00:00';
  var statusBox = document.getElementById('statusBox');
  var btn = document.getElementById('fetchBtn');
  if(!dateVal){
    statusBox.className = 'err'; statusBox.style.display = 'block';
    statusBox.innerHTML = '日付を入力してください。';
    return;
  }
  btn.disabled = true; btn.textContent = '取得中...';
  statusBox.className = ''; statusBox.style.display = 'block';
  statusBox.innerHTML = '地震データを取得し、統合リスクマップを再計算しています…（数十秒かかる場合があります）';
  closeDetail();

  var pendingRetry = false;
  var calibQ = document.getElementById('inCalib').checked ? '&calib=date' : '';
  fetch('/archive/historical_riskmap?datetime=' + encodeURIComponent(dateVal + 'T' + timeVal) + calibQ)
    .then(function(r){
      if(r.status === 202) return r.json().then(function(e){ return {pending: true, message: e.message}; });
      if(!r.ok) return r.json().then(function(e){ throw new Error(e.error || ('HTTPエラー ' + r.status)); });
      return r.json();
    })
    .then(function(d){
      if(d.pending){
        pendingRetry = true;
        statusBox.className = ''; statusBox.innerHTML = d.message;
        setTimeout(runFetch, 5000);
        return;
      }
      CELLS = d.cells || [];
      if(d.thresholds) THRESH = d.thresholds;
      statusBox.className = ''; statusBox.innerHTML = fmtStatus(d);
      document.getElementById('chkSec').style.display = CELLS.length ? 'block' : 'none';
      document.getElementById('cellCount').style.display = CELLS.length ? 'block' : 'none';
      document.getElementById('lg').style.display = CELLS.length ? 'block' : 'none';
      document.getElementById('mapEmpty').style.display = CELLS.length ? 'none' : 'flex';
      if(CELLS.length){
        ensureMap();
        setTimeout(function(){ map.invalidateSize(); redraw(); }, 50);
      }
    })
    .catch(function(e){
      statusBox.className = 'err';
      statusBox.innerHTML = '取得に失敗しました: ' + e.message;
    })
    .finally(function(){
      if(pendingRetry) return;
      btn.disabled = false; btn.textContent = 'この時点のリスクマップを取得';
    });
}
</script></body></html>"""
    html = html.replace("__RISK_GRID_SIZE__", str(RISK_GRID_SIZE))
    # (v9.07) 重みはサーバー側(しきい値の較正に使うもの)と必ず一致させる
    html = html.replace("__WEIGHTS_JSON__", json.dumps({k: RISK_DEFAULT_WEIGHTS[k] for k in _RISK_HIST_KEYS}))
    for _k in _RISK_HIST_KEYS:
        html = html.replace(f"__W_{_k.upper()}__", f"{RISK_DEFAULT_WEIGHTS[_k]:.2f}")
    html = html.replace("__LEAFLET_CDN__", LEAFLET_CDN)
    html = html.replace("__DARK_TILE__", DARK_TILE)
    html = html.replace("__GEOJSON_JS__", GEOJSON_JS)
    return html


SHELL_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
  <title>Earthquake-risk-map v9.20</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
  <link rel="shortcut icon" href="/favicon.ico">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <meta name="theme-color" content="#05070d">
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    html,body{height:100%;overflow:hidden;background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;font-family:-apple-system,BlinkMacSystemFont,"SF Pro JP","Hiragino Sans",sans-serif}
    #sidebar{
      position:fixed;top:0;left:0;width:188px;height:100%;
      background:rgba(255,255,255,.05);backdrop-filter:blur(30px) saturate(180%);-webkit-backdrop-filter:blur(30px) saturate(180%);border-right:1px solid rgba(255,255,255,.14);
      display:flex;flex-direction:column;padding-top:0;z-index:100;
      transition:transform 0.25s ease;
    }
    #menuBtn{display:none}
    #sbOverlay{display:none}
    .app-title{
      padding:14px 14px 12px;
      background:linear-gradient(135deg,rgba(10,132,255,.35),rgba(124,92,255,.22));
      border-bottom:1px solid rgba(255,255,255,.14);
    }
    .app-title div:first-child{font-size:12px;font-weight:700;color:#60a5fa;letter-spacing:0.5px}
    .app-title div:last-child{font-size:10px;color:rgba(235,238,245,.4);margin-top:2px}
    .group-title{
      padding:10px 14px 4px;font-size:10px;font-weight:700;
      color:rgba(235,238,245,.32);text-transform:uppercase;letter-spacing:1px;
    }
    .tab-btn{
      width:100%;text-align:left;padding:10px 14px 10px 16px;cursor:pointer;
      font-size:13px;font-weight:500;color:rgba(235,238,245,.46);
      border:none;background:none;transition:0.15s;display:flex;align-items:center;gap:8px;
    }
    .tab-btn:hover{color:#f3f4f6;background:rgba(255,255,255,.07)}
    .tab-btn.active{
      color:#fff;background:linear-gradient(90deg,rgba(10,132,255,.22),rgba(124,92,255,.05));
      border-left:3px solid #3b82f6;padding-left:13px;
    }
    .tab-btn .icon{font-size:14px;flex-shrink:0}
    .tab-btn .label{flex:1}
    .tab-btn .badge{font-size:9px;padding:1px 5px;border-radius:7px;background:rgba(56,132,255,.25);color:#93c5fd;flex-shrink:0}
    .tab-btn.active .badge{background:rgba(10,132,255,.55)}
    .sep{height:1px;background:rgba(255,255,255,.09);backdrop-filter:blur(14px) saturate(160%);-webkit-backdrop-filter:blur(14px) saturate(160%);margin:6px 12px}
    .version{margin-top:0;padding:10px 14px;font-size:10px;color:rgba(235,238,245,.32);border-top:1px solid rgba(255,255,255,.14)}
    .recalc-box{margin-top:auto;padding:10px 12px}
    #recalcBtn{
      width:100%;padding:10px 10px;font-size:13px;font-weight:600;color:#fff;cursor:pointer;
      border:1px solid rgba(255,255,255,.2);border-radius:10px;
      background:linear-gradient(135deg,rgba(10,132,255,.45),rgba(124,92,255,.32));
      transition:0.15s;
    }
    #recalcBtn:hover{filter:brightness(1.15)}
    #recalcBtn:disabled{opacity:.55;cursor:default;filter:none}
    #toast{
      display:none;position:fixed;left:50%;transform:translateX(-50%);
      bottom:calc(16px + env(safe-area-inset-bottom,0px));z-index:400;
      max-width:88vw;padding:9px 15px;border-radius:14px;font-size:12.5px;line-height:1.5;color:#f3f4f6;
      background:rgba(20,24,38,.86);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
      border:1px solid rgba(255,255,255,.16);box-shadow:0 4px 16px rgba(0,0,0,.5);pointer-events:none;
    }
    #main{margin-left:188px;height:100vh;overflow:hidden}
    iframe{width:100%;height:100%;border:none;display:none}
    iframe.active{display:block}

    @media (max-width:768px){
      #menuBtn{
        display:flex;align-items:center;justify-content:center;
        position:fixed;top:10px;left:10px;z-index:300;
        width:38px;height:38px;border-radius:18px;border:none;
        background:rgba(255,255,255,.14);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);color:#f3f4f6;font-size:18px;
        box-shadow:0 2px 8px rgba(0,0,0,.5);
      }
      #sidebar{
        width:78vw;max-width:280px;
        transform:translateX(-100%);
        box-shadow:2px 0 16px rgba(0,0,0,.6);
      }
      #sidebar.open{transform:translateX(0)}
      #sbOverlay{
        display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99;
      }
      #sbOverlay.show{display:block}
      #main{margin-left:0}
      .tab-btn{padding:12px 14px 12px 16px;font-size:14px}
      .tab-btn.active{padding-left:13px}
    }
  </style>
</head>
<body>
  <button id="menuBtn" onclick="toggleSb()">&#9776;</button>
  <div id="sbOverlay" onclick="toggleSb()"></div>
  <div id="toast"></div>
  <div id="sidebar">
    <div class="app-title">
      <div>地震リスクマップ</div>
      <div>v9.20 / 研究用</div>
    </div>

    <div class="group-title">地震データ</div>
    <button class="tab-btn active" onclick="sw(0)">
      <span class="label">統合リスクマップ</span>
      <span class="badge">β</span>
    </button>
    <button class="tab-btn" onclick="sw(1)">
      <span class="label">地震履歴</span>
      <span class="badge">JMA/P2P/USGS/Hi-net</span>
    </button>
    <button class="tab-btn" onclick="sw(2)">
      <span class="label">ETASマップ</span>
      <span class="badge">JMA/P2P/USGS/Hi-net</span>
    </button>
    <button class="tab-btn" onclick="sw(3)">
      <span class="label">b値マップ</span>
      <span class="badge">JMA/P2P/USGS/Hi-net</span>
    </button>
    <button class="tab-btn" onclick="sw(4)">
      <span class="label">活断層・プレート境界</span>
      <span class="badge">GEM</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">地球物理データ</div>
    <button class="tab-btn" onclick="sw(5)">
      <span class="label">TEC</span>
      <span class="badge">NICT</span>
    </button>
    <button class="tab-btn" onclick="sw(6)">
      <span class="label">GNSS変位</span>
      <span class="badge">GSI</span>
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
      <span class="badge">JMA/USGS</span>
    </button>

    <div class="sep"></div>
    <div class="group-title">検証</div>
    <button class="tab-btn" onclick="sw(9)">
      <span class="label">バックテスト</span>
      <span class="badge">Molchan</span>
    </button>

    <div class="recalc-box">
      <button id="recalcBtn" onclick="recalc()">&#8635; このタブを再計算</button>
    </div>
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
    <iframe id="f9" src=""></iframe>
  </div>
  <script>
    var URLS=['riskmap','history','etas','bvalue','faultmap','tec','gnss','pressure','snapshots','backtest'];
    var loaded=[true,false,false,false,false,false,false,false,false,false];
    function sw(idx){
      curTab=idx;
      document.querySelectorAll('.tab-btn').forEach(function(b,i){b.classList.toggle('active',i===idx)});
      document.querySelectorAll('iframe').forEach(function(f,i){f.classList.toggle('active',i===idx)});
      if(!loaded[idx]){
        document.getElementById('f'+idx).src='/tab/'+URLS[idx];
        loaded[idx]=true;
      }
      if(window.innerWidth<=768){closeSb()}
    }
    function toggleSb(){
      document.getElementById('sidebar').classList.toggle('open');
      document.getElementById('sbOverlay').classList.toggle('show');
    }
    function closeSb(){
      document.getElementById('sidebar').classList.remove('open');
      document.getElementById('sbOverlay').classList.remove('show');
    }

    // ── 各タブの「再計算」 ──
    var TAB_LABELS=['統合リスクマップ','地震履歴','ETASマップ','b値マップ','活断層・プレート境界','TEC','GNSS変位','海面気圧','アーカイブ','バックテスト'];
    var curTab=0, recalcBusy=false, toastTimer=null;
    function showToast(msg,ms){
      var t=document.getElementById('toast');
      t.textContent=msg; t.style.display='block';
      clearTimeout(toastTimer);
      if(ms){toastTimer=setTimeout(function(){t.style.display='none'},ms)}
    }
    function reloadTab(idx){
      document.getElementById('f'+idx).src='/tab/'+URLS[idx]+'?_='+Date.now();
      loaded[idx]=true;
    }
    function recalc(){
      if(recalcBusy) return;
      var idx=curTab, name=URLS[idx], label=TAB_LABELS[idx];
      if(window.innerWidth<=768){closeSb()}
      if(idx===9){
        showToast('バックテストは、タブ左側の「実行」ボタンで計算します',4000);
        return;
      }
      if(idx===8){
        // アーカイブは指定日時ごとの再計算UI。タブ内の取得ボタンを同じ入力で再実行する
        var w=document.getElementById('f8').contentWindow;
        if(w && typeof w.runFetch==='function'){ w.runFetch(); showToast('入力中の日時で再計算します',3000); }
        else { showToast('日時を入力して「この時点のリスクマップを取得」を押してください',4000); }
        return;
      }
      var btn=document.getElementById('recalcBtn');
      var t0=Date.now();
      recalcBusy=true; btn.disabled=true; btn.textContent='↻ 再計算中…';
      showToast(label+' 再計算中…');
      function finish(ok,msg){
        recalcBusy=false; btn.disabled=false; btn.innerHTML='&#8635; このタブを再計算';
        if(ok){ reloadTab(idx); showToast('✓ '+label+' を再計算しました（'+msg+'）',8000); }
        else { showToast('⚠ '+label+' の再計算に失敗: '+msg,10000); }
      }
      function poll(){
        fetch('/recalc/'+name+'/status').then(function(r){return r.json()}).then(function(s){
          if(s.running){
            showToast(label+' 再計算中… '+Math.round((Date.now()-t0)/1000)+'秒');
            setTimeout(poll,1500);
          } else if(s.ok===null){
            finish(false,'状態が不明です（サーバーが再起動した可能性があります）');
          } else {
            finish(s.ok,s.message);
          }
        }).catch(function(e){ finish(false,'状態の取得に失敗: '+e.message) });
      }
      fetch('/recalc/'+name,{method:'POST'}).then(function(r){
        if(!r.ok) return r.json().then(function(e){ throw new Error(e.error||('HTTPエラー '+r.status)); });
        return r.json();
      }).then(poll).catch(function(e){ finish(false,e.message) });
    }
  </script>
</body>
</html>"""

LOADING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#05070d">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<style>body{background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:white;display:flex;align-items:center;justify-content:center;
height:100vh;font-family:sans-serif;flex-direction:column;gap:16px}
.sp{width:48px;height:48px;border:4px solid rgba(255,255,255,.14);border-top-color:#0a84ff;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="sp"></div><p>データを準備中...</p>
<p style="font-size:12px;color:rgba(235,238,245,.4)">地震データ取得 → ETAS解析 → b値計算...</p></body></html>"""


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
# ウェブサイトアイコン（ファビコン）
#   地震計の波形をモチーフにしたアイコンをコードだけで生成する（画像ファイル不要）。
#   /favicon.svg … モダンブラウザ用（ベクター）
#   /favicon-32.png, /favicon.ico, /apple-touch-icon.png … PNG（numpy + zlibで生成）
# ══════════════════════════════════════════════════════
_ICON_WAVE = [(6,34),(18,34),(22,28),(27,46),(33,12),(39,52),(44,30),(48,34),(58,34)]  # 64x64座標系
_ICON_CACHE = {}

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#233560"/><stop offset="1" stop-color="#3a2560"/>'
    '</linearGradient></defs>'
    '<rect width="64" height="64" rx="14" fill="url(#g)"/>'
    '<polyline points="' + " ".join(f"{x},{y}" for x, y in _ICON_WAVE) + '" fill="none" '
    'stroke="#5ee0ff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    '</svg>'
)

def _render_icon_png(size, rounded=True):
    """アイコンをsize×sizeのRGBA PNGとして描画してbytesで返す。"""
    import zlib, struct
    key = ("png", size, rounded)
    if key in _ICON_CACHE: return _ICON_CACHE[key]
    k = 64.0 / size
    ys, xs = np.mgrid[0:size, 0:size]
    px = (xs + 0.5) * k; py = (ys + 0.5) * k          # 64座標系のピクセル中心
    # 背景グラデーション（#233560 → #3a2560）
    t = ((px + py) / 128.0)[..., None]
    c0 = np.array([0x23, 0x35, 0x60], float); c1 = np.array([0x3a, 0x25, 0x60], float)
    rgb = c0 + (c1 - c0) * t
    # 角丸マスク（半径14）。iOSのapple-touch-iconは自前でマスクするので角丸なし
    if rounded:
        r = 14.0
        qx = np.abs(px - 32) - (32 - r); qy = np.abs(py - 32) - (32 - r)
        dist = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
        alpha = np.clip(0.5 - dist / k, 0, 1)
    else:
        alpha = np.ones((size, size))
    # 波形（太さ4の丸端ポリライン）との距離でアンチエイリアス
    d = np.full((size, size), 1e9)
    for (x0, y0), (x1, y1) in zip(_ICON_WAVE[:-1], _ICON_WAVE[1:]):
        vx, vy = x1 - x0, y1 - y0
        u = np.clip(((px - x0) * vx + (py - y0) * vy) / (vx * vx + vy * vy), 0, 1)
        d = np.minimum(d, np.hypot(px - (x0 + u * vx), py - (y0 + u * vy)))
    line = np.clip((2.0 - d) / k + 0.5, 0, 1)[..., None]
    rgb = rgb * (1 - line) + np.array([0x5e, 0xe0, 0xff], float) * line
    img = np.dstack([rgb, alpha[..., None] * 255]).clip(0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(size))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    _ICON_CACHE[key] = png
    return png

def _render_icon_ico():
    """48x48のPNGを埋め込んだ .ico（/favicon.ico を直接取りに来る古いブラウザ用）。"""
    import struct
    if "ico" in _ICON_CACHE: return _ICON_CACHE["ico"]
    png = _render_icon_png(48)
    hdr = struct.pack("<HHH", 0, 1, 1)
    ent = struct.pack("<BBBBHHII", 48, 48, 0, 0, 1, 32, len(png), 22)
    _ICON_CACHE["ico"] = hdr + ent + png
    return _ICON_CACHE["ico"]

_ICON_HEADERS = {"Cache-Control": "public, max-age=86400"}

@app.route("/favicon.svg")
def favicon_svg():
    return Response(FAVICON_SVG, mimetype="image/svg+xml", headers=_ICON_HEADERS)

@app.route("/favicon-32.png")
def favicon_png32():
    return Response(_render_icon_png(32), mimetype="image/png", headers=_ICON_HEADERS)

@app.route("/favicon.ico")
def favicon_ico():
    return Response(_render_icon_ico(), mimetype="image/x-icon", headers=_ICON_HEADERS)

@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return Response(_render_icon_png(180, rounded=False), mimetype="image/png", headers=_ICON_HEADERS)


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
        return Response("<html><body style='background:radial-gradient(at 18% 15%,#233560 0%,transparent 55%),radial-gradient(at 85% 12%,#3a2560 0%,transparent 50%),radial-gradient(at 60% 92%,#0f3a4a 0%,transparent 55%),#05070d;background-attachment:fixed;color:white;padding:20px'>ロード中...</body></html>", mimetype="text/html")
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
    elif name == "backtest":  html = render_backtest(upd)
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
        "names_file": (_gnss_name_table() and {                      # (v9.02) 地点名ファイルの読込状況
            "found": bool(_gnss_names_info["path"]), "path": _gnss_names_info["path"],
            "entries": _gnss_names_info["count"], "error": _gnss_names_info["error"],
            "searched": _gnss_names_info["tried"]}),
    }

@app.route("/hinet/debug_raw")
def hinet_debug_raw():
    """
    Hi-netログイン処理の生の応答を確認するデバッグ用エンドポイント。
    ID/パスワードなど認証情報自体は一切返さない。
    ローカル環境では成功するのにRender等のサーバー環境では失敗する場合、
    データセンターIPがブロックされているのか、それとも本当にログインが
    失敗しているのか（想定外の隠しフィールド等）を切り分けるために使う。
    """
    if not HINET_ENABLED:
        return Response("HINET_USER / HINET_PASS が未設定です", status=400)
    debug = {}
    session = requests.Session()
    try:
        r1 = session.get(HINET_LOGIN_URL, headers=HINET_HEADERS, timeout=15)
        debug["step1_get_status"] = r1.status_code
        debug["step1_get_final_url"] = r1.url
        debug["step1_cookies_after_get"] = list(session.cookies.keys())
    except Exception as e:
        debug["step1_error"] = str(e)
        return debug
    try:
        r2 = session.post(
            HINET_LOGIN_URL,
            data={"LANG": "ja", "auth_un": HINET_USER, "auth_pw": HINET_PASS},
            headers={**HINET_HEADERS, "Referer": HINET_LOGIN_URL},
            timeout=15,
        )
        r2.encoding = "euc-jp"
        debug["step2_post_status"] = r2.status_code
        debug["step2_post_final_url"] = r2.url
        debug["step2_redirect_chain_status"] = [h.status_code for h in r2.history]
        debug["step2_cookies_after_post"] = list(session.cookies.keys())
        debug["step2_still_shows_login_form"] = "auth_login.png" in r2.text
        debug["step2_text_preview"] = r2.text[:400]
    except Exception as e:
        debug["step2_error"] = str(e)
    return debug

@app.route("/hinet/status")
def hinet_status():
    """Hi-net（NIED）微小地震データ取得の設定・稼働状況を返す（パスワード等は含めない）。"""
    ts = _hinet_status["last_success"]
    return {
        "enabled": HINET_ENABLED,
        "user_set": bool(HINET_USER),
        "session_active": _hinet_session is not None,
        "last_success": ts,
        "last_success_str": (datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d %H:%M JST") if ts else None),
        "last_error": _hinet_status["last_error"],
        "last_count": _hinet_status["last_count"],
    }

@app.route("/gnss/net_test")
def gnss_net_test():
    """サーバーからGSIのSFTPポートへTCP接続できるかだけを確認する（認証はしない）。"""
    import socket
    try:
        sock = socket.create_connection((GSI_SFTP_HOST, GSI_SFTP_PORT), timeout=10)
        sock.close()
        return {"ok": True, "host": GSI_SFTP_HOST, "port": GSI_SFTP_PORT}
    except Exception as e:
        return {"ok": False, "host": GSI_SFTP_HOST, "port": GSI_SFTP_PORT, "error": repr(e)}

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

@app.route("/tec/status")
def tec_status():
    """TEC取得の設定・稼働状況を返す（/gnss/status のTEC版）。"""
    with _tec_lock:
        c = dict(_tec_cache)
        n_hist = len(_tec_history)
    ts, tried = c["ts"], c["tried"]
    fmt = lambda t: datetime.fromtimestamp(t, JST).strftime("%Y-%m-%d %H:%M JST") if t else None
    return {
        "enabled": TEC_ENABLED,
        "mode": "json" if TEC_JSON_URL else "image",
        "json_url_set": bool(TEC_JSON_URL),
        "pillow_available": _PIL_AVAILABLE,
        "calibrated": _tec_calibrated(),
        "cells": len(c["grid"]) if c["grid"] else 0,
        "history_samples": n_hist,
        "source": c["source"],
        "last_success": ts, "last_success_str": fmt(ts),
        "last_attempt_str": fmt(tried),
        "data_updated": c["updated"],
        "age_sec": round(time.time() - ts) if ts else None,
        "used_in_riskmap": bool(ts and (time.time() - ts) <= TEC_LIVE_MAX_STALE_SEC and n_hist >= 3),
        "updating": c["updating"],
        "error": c["error"],
        "image_url": c["image_url"],
        "url_pref": dict(_tec_url_pref),
        "image_time": _tec_url_pref.get("image_time"),
        "lookback_min": TEC_URL_LOOKBACK_MIN,
        "url_template": TEC_URL_TEMPLATE,
        "recent_attempts": c["attempts"],
    }

@app.route("/tec/refresh_now", methods=["POST"])
def tec_refresh_now():
    """TECの再取得を手動でトリガーする（デバッグ・動作確認用）。"""
    if not TEC_ENABLED:
        return Response("TECが無効です（TEC_JSON_URL 未設定、かつPillow未インストールまたはTEC_CALIBRATION未設定）", status=400)
    with _tec_lock:
        already = _tec_cache["updating"]
    if already:
        return {"status": "already_updating"}
    threading.Thread(target=_refresh_tec_cache, daemon=True).start()
    return {"status": "started"}

@app.route("/tec/raw_image")
def tec_raw_image():
    """取得できた最新のNICT TEC画像をPNGで返す（/gnss/raw_sample のTEC版）。
    ?overlay=1 を付けると、地図本体の枠(赤)とカラーバーの枠(緑)を重ね描きする。
    TEC_CALIBRATION の *_frac が実画像とずれていないかを目視確認するために使う。"""
    if not _PIL_AVAILABLE:
        return Response("Pillowが未インストールです", status=400)
    img, url, attempts = _fetch_tec_image()
    if img is None:
        return Response("画像を取得できませんでした\n" + "\n".join(attempts), status=502, mimetype="text/plain")
    if request.args.get("overlay") == "1" and _tec_calibrated():
        from PIL import ImageDraw
        w, h = img.size
        d = ImageDraw.Draw(img)
        for key, col in (("map_px_box_frac", (255, 0, 0)), ("legend_px_box_frac", (0, 200, 0))):
            x0, y0, x1, y1 = _frac_box_to_px(TEC_CALIBRATION[key], w, h)
            d.rectangle([x0, y0, x1, y1], outline=col, width=1)
    buf = io.BytesIO(); img.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"X-Source-Url": url, "X-Image-Size": f"{img.size[0]}x{img.size[1]}"})

# quakes.csv（JMA/P2P/USGS統合データ）はkeep_days=HIST_CALIB_LOOKBACK_DAYS(65)より
# 古いデータを保持していない。アーカイブ検索の対象期間がこの保持範囲より古い場合は
# ローカルCSVではなく、気象庁一元化震源カタログ(fetch_quakes_jma_catalog_range)、
# それが期間を覆っていなければUSGSの過去カタログ(fetch_quakes_usgs_range)から取得する。
# 保持日数ギリギリではなく少し早めに切り替えることで、バックグラウンド更新の
# タイミング次第で「本来は残っているはずのデータが直前に削除されていた」ケースを避ける。
ARCHIVE_LOCAL_SAFE_DAYS = HIST_CALIB_LOOKBACK_DAYS - 5   # = 60日

def _archive_with_date_calibration(ref_time_jst, ref_time_utc):
    calib, info = get_calibration_at(ref_time_utc)
    if calib is None:
        if info.get("error"):
            return {"error": f"この日付の基準を作れませんでした: {info['error']}"}, 503
        who = "この日付の基準（その日より前の65日）" if info.get("same") else "別の日付の基準"
        return {"pending": True, "message": f"{who}を作成中です。完了すると自動で表示します（初回は数十秒〜数分）"}, 202
    start_utc = ref_time_utc - timedelta(days=BT_WINDOW_DAYS)   # 基準のサンプルと同じ60日窓で評価する
    try:
        quakes = _fetch_catalog_strict(start_utc, ref_time_utc)
    except Exception as e:
        return {"error": str(e)}, 503
    try:
        cells = compute_risk_grid_historical(quakes, ref_time_utc, calib=calib)
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": f"リスクマップの再計算に失敗しました: {e}"}, 500
    return {
        "ref_time_jst": ref_time_jst.strftime("%Y-%m-%d %H:%M JST"),
        "period_start_jst": start_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M JST"),
        "quake_count": len(quakes), "data_source": "jma_catalog", "min_mag": JMA_CATALOG_MIN_MAG,
        "calib_mode": "date", "calib_anchor_jst": calib.get("anchor_jst"),
        "thresholds": {"by_subset": calib["risk_thresholds"], "default": RISK_THRESH_DEFAULT,
                       "calibrated": True, "n_samples": calib.get("n_samples", 0)},
        "cells": cells,
    }

@app.route("/archive/historical_riskmap")
def archive_historical_riskmap():
    """
    アーカイブタブ用: 指定された日時(JST)を基準に、その1か月前までの
    地震データを取得し直して、その時点の統合リスクマップ（ETAS/b値/活断層/
    プレート境界）を再計算して返す。
    クエリパラメータ: datetime = "YYYY-MM-DDTHH:MM" (JST基準、入力欄のvalueをそのまま渡す想定)

    データソースの切り替え:
      - 取得期間の開始が直近 ARCHIVE_LOCAL_SAFE_DAYS 日以内に収まる場合は、
        従来どおりローカルのquakes.csv（JMA/P2P/USGS統合・重複排除済み）を使う。
      - それより古い期間を含む場合は、quakes.csvには既に無いため、
        (1) 気象庁一元化震源カタログ(JMA_CATALOG_DIR の年別ファイル)が期間全体を
            覆っていればそれを使う（data_source="jma_catalog"。M下限は JMA_CATALOG_MIN_MAG）。
        (2) 覆っていなければ（該当年のファイル無し・収録期間外）、USGSの過去カタログAPIから
            期間全体を都度取得する（data_source="usgs_historical"。数年前でも取得可能）。
        期間の一部だけ別ソースに差し替えると検知下限マグニチュードの違いで
        b値等の計算が歪むため、期間全体を同一ソースに揃えている。
    """
    dt_str = request.args.get("datetime", "")
    try:
        naive = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M")
    except Exception:
        return {"error": "日時の形式が正しくありません（YYYY-MM-DDTHH:MM）"}, 400

    ref_time_jst = naive.replace(tzinfo=JST)
    ref_time_utc = ref_time_jst.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if ref_time_utc > now_utc:
        return {"error": "未来の日時は指定できません"}, 400

    # (v9.08) calib=date: 現在の基準(過去65日)ではなく、基準日より前の65日だけで基準を作り直して評価する。
    # 古い日付の評価に現在の基準を使うと、その日付より未来の活動が基準に混ざるため。
    # 基準・評価とも一元化カタログだけを使う（地震の検知下限が混ざらないように）。
    if request.args.get("calib") == "date":
        return _archive_with_date_calibration(ref_time_jst, ref_time_utc)

    start_utc = ref_time_utc - timedelta(days=30)
    local_cutoff = now_utc - timedelta(days=ARCHIVE_LOCAL_SAFE_DAYS)

    if start_utc >= local_cutoff:
        data_source = "local"
        quakes = load_quakes_between(start_utc, ref_time_utc)
    else:
        quakes = None
        try:
            quakes = fetch_quakes_jma_catalog_range(start_utc, ref_time_utc)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[archive] JMAカタログの読み込みに失敗。USGSへフォールバックします: {e}")
        if quakes is not None:
            data_source = "jma_catalog"
        else:
            data_source = "usgs_historical"
            try:
                quakes = fetch_quakes_usgs_range(start_utc, ref_time_utc)
            except Exception as e:
                return {"error": f"過去データ(USGS)の取得に失敗しました: {e}"}, 500

    if not get_risk_level_thresholds()["calibrated"]:
        return {"error": "絶対基準(過去65日の実績)を計算中です。数分後にもう一度お試しください。"}, 503

    try:
        cells = compute_risk_grid_historical(quakes, ref_time_utc)
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": f"リスクマップの再計算に失敗しました: {e}"}, 500

    return {
        "ref_time_jst": ref_time_jst.strftime("%Y-%m-%d %H:%M JST"),
        "period_start_jst": (start_utc.astimezone(JST)).strftime("%Y-%m-%d %H:%M JST"),
        "quake_count": len(quakes),
        "data_source": data_source,
        "min_mag": JMA_CATALOG_MIN_MAG if data_source == "jma_catalog" else None,
        "thresholds": get_risk_level_thresholds(),
        "cells": cells,
    }

@app.route("/data/risk_level_thresholds")
def risk_level_thresholds():
    """(v8.21) 統合リスクマップのレベル境界と、その境界を過去実績に当てはめた場合の
    日別セル数(p10/中央値/p90/最大)を返す。目標値に合っているかの検算用。"""
    calib = _get_historical_calibration()
    return {"calibrated": bool(calib.get("risk_thresholds")),
            "thresholds": calib.get("risk_thresholds") or {},
            "default": RISK_THRESH_DEFAULT,
            "report": calib.get("risk_calib_report"),
            "calib_samples": calib.get("n_samples", 0)}


# ── 検証タブ(バックテスト)のAPI ─────────────────────────
@app.route("/backtest/info")
def backtest_info():
    cov = _jma_catalog_coverage()
    last = None
    lp = os.path.join(BACKTEST_DIR, "last_params.json")
    if os.path.exists(lp):
        try:
            with open(lp, encoding="utf-8") as f:
                last = json.load(f)
        except Exception:
            last = None
    has = os.path.isdir(BACKTEST_DIR) and any(n.startswith("records_") for n in os.listdir(BACKTEST_DIR))
    with _bt_lock:
        job = {"running": _bt_job["running"]}
    return {"coverage": None if not cov else {"first": cov["first"].astimezone(JST).strftime("%Y-%m-%d"),
                                              "last": cov["last"].astimezone(JST).strftime("%Y-%m-%d"),
                                              "contiguous": cov["contiguous"]},
            "defaults": BT_DEFAULT_PARAMS, "last_params": last, "job": job, "has_records": has}

@app.route("/backtest/start", methods=["POST"])
def backtest_start():
    try:
        p = _bt_parse_params(request.get_json(silent=True) or {})
    except ValueError as e:
        return {"error": str(e)}, 400
    with _bt_lock:
        if _bt_job["running"]:
            return {"error": "すでに実行中です"}, 409
        _bt_job.update(running=True, cancel=False, phase="準備中", done=0, total=0, message="", started=time.time(),
                       finished=None, error=None, params=p, cfg=_bt_cfg_hash(p), skipped={})
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    with open(os.path.join(BACKTEST_DIR, "last_params.json"), "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)
    threading.Thread(target=_bt_run, args=(p,), daemon=True).start()
    return {"status": "started", "cfg": _bt_job["cfg"]}

@app.route("/backtest/status")
def backtest_status():
    with _bt_lock:
        j = dict(_bt_job)
    end = j["finished"] or time.time()
    j["elapsed"] = round(end - j["started"], 1) if j["started"] else 0
    j.pop("params", None)
    return j

@app.route("/backtest/cancel", methods=["POST"])
def backtest_cancel():
    _bt_set(cancel=True)
    return {"status": "cancel_requested"}

def _bt_params_from_request():
    args = request.args.to_dict()
    return _bt_parse_params(args)

@app.route("/backtest/result")
def backtest_result():
    try:
        p = _bt_params_from_request()
    except ValueError as e:
        return {"error": str(e)}, 400
    return _bt_summarize(_bt_read_records(_bt_cfg_hash(p)), p)

@app.route("/backtest/events.csv")
def backtest_events_csv():
    try:
        p = _bt_params_from_request()
    except ValueError as e:
        return {"error": str(e)}, 400
    body = _bt_csv(_bt_read_records(_bt_cfg_hash(p)), p)
    return Response(body, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=backtest_M{p['min_mag']:.1f}_{p['start']}_{p['end']}.csv"})

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

# ══════════════════════════════════════════════════════
# 手動再計算（各タブの「再計算」ボタン用）
# ══════════════════════════════════════════════════════
# 各タブの表示は _cached_data と各種メモリキャッシュから作られるため、計算や取得が
# 一時的に失敗して欠損すると、次の自動更新(最大10分)まで欠損したまま残る。
# サイドバーの「再計算」ボタンから、開いているタブ分だけをその場で作り直せるようにする。
#
#  ・地震データのAPI再取得はしない(p2p_jmaのレート制限で最大10分かかるため)。
#    保存済みの data/quakes.csv から再計算する。API取得は従来どおり自動更新ループの担当。
#  ・失敗したときは表示中のキャッシュを上書きしない(空データで置き換えない)。
#  ・処理は別スレッドで実行し、フロントは /recalc/<name>/status をポーリングする
#    (Renderのリクエストタイムアウトを避けるため)。
_recalc_jobs = {}
_recalc_jobs_lock = threading.Lock()
_recalc_core_lock = threading.Lock()

def _recalc_core_cache():
    """保存済みquakes.csvからETAS・b値を作り直して _cached_data を更新する。
    (地震履歴 / ETAS / b値 / 統合リスクマップの共通の土台)"""
    global _cached_data
    with _recalc_core_lock:
        quakes = load_quakes()
        if not quakes:
            raise RuntimeError("保存済みの地震データがありません（自動更新の取得完了をお待ちください）")
        grid_scores = analyze_etas(quakes)
        bvalue_grid = compute_bvalue_grid(quakes)
        if not grid_scores and not bvalue_grid:
            raise RuntimeError("ETAS・b値の計算結果が空でした（表示中のデータは変更していません）")
        updated_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST") + "(手動再計算)"
        with _cache_lock:
            _cached_data = {"all": quakes, "etas": grid_scores, "bvalue": bvalue_grid,
                            "updated": updated_str}
    return f"地震{len(quakes)}件・ETAS格子{len(grid_scores)}・b値格子{len(bvalue_grid)}"

def _recalc_calibration():
    """絶対基準キャリブレーション(過去65日分のセル別プール)を今すぐ作り直す。
    通常は6時間キャッシュで、途中で失敗した結果もその間残ってしまうため。"""
    global _hist_calib_cache
    if not _hist_calib_lock.acquire(blocking=False):
        raise RuntimeError("キャリブレーションが計算中です。少し待ってからもう一度お試しください")
    try:
        new = _compute_historical_calibration()
        if new["etas_cell"] is None:
            raise RuntimeError(f"キャリブレーションのサンプルが不足しています（{new['n_samples']}時点）。以前の結果を維持しました")
        _hist_calib_cache = new
    finally:
        _hist_calib_lock.release()
    return f"キャリブレーション{new['n_samples']}時点"

def _recalc_geo_data(force=False):
    """活断層(GEM)・プレート境界データを確認し、空または force のときは取得し直す。"""
    if force or not (_fault_cache["data"] or {}).get("features"):
        _fault_cache["fetched_at"] = None
    if force or not (_plate_cache["data"] or {}).get("features"):
        _plate_cache["fetched_at"] = None
    faults = get_japan_active_faults()
    if _fault_cache["fetched_at"] is None or not faults.get("features"):
        raise RuntimeError("活断層データ(GEM)の取得に失敗しました")
    plates = get_plate_boundaries()
    if _plate_cache["fetched_at"] is None or not (plates or {}).get("features"):
        raise RuntimeError("プレート境界データの取得に失敗しました")
    return f"活断層{len(faults['features'])}件"

def _recalc_pressure():
    global _amedas_cache
    table = _fetch_amedas_table()
    obs, label = _fetch_amedas_latest()
    if not table or not obs:
        raise RuntimeError("AMeDASの取得に失敗しました（表示中のデータは変更していません）")
    _amedas_cache = {"data": {"table": table, "obs": obs, "label": label}, "ts": time.time()}
    return f"{len(obs)}観測点・{label}"

def _recalc_tec():
    if not TEC_ENABLED:
        raise RuntimeError("TECが無効です（TEC_JSON_URL 未設定、かつPillow未インストールまたはTEC_CALIBRATION未設定）")
    with _tec_lock:
        if _tec_cache["updating"]:
            raise RuntimeError("TECの更新が既に実行中です。しばらくしてからもう一度お試しください")
        before = _tec_cache["ts"]
    _refresh_tec_cache()
    with _tec_lock:
        after, grid, err, src_ = _tec_cache["ts"], _tec_cache["grid"], _tec_cache["error"], _tec_cache["source"]
    if after == before or not grid:
        raise RuntimeError(err or "TECを取得できませんでした")
    return f"{len(grid)}セル（{'JSON' if src_ == 'json' else '画像'}）"

def _recalc_gnss():
    if not GSI_GNSS_ENABLED:
        raise RuntimeError("GNSSが無効です（GSI_SFTP_USER / GSI_SFTP_PASS 未設定、またはparamiko未インストール）")
    with _gnss_lock:
        if _gnss_cache["updating"]:
            raise RuntimeError("GNSSの更新が既に実行中です。しばらくしてからもう一度お試しください")
    _refresh_gnss_cache()
    with _gnss_lock:
        n = len(_gnss_cache["data"] or [])
        err = _gnss_cache["error"]
    if n == 0:
        raise RuntimeError(err or "GNSSデータを取得できませんでした")
    return f"{n}/{len(GNSS_STATIONS)}点" + (f"（{err}）" if err else "")

def _recalc_etas():
    msg = _recalc_core_cache()          # 失敗したらここで中断
    try:
        msg += "・" + _recalc_calibration()
    except Exception as e:
        msg += f"／⚠ {e}"
    return msg

def _recalc_riskmap():
    msg = _recalc_core_cache()          # 失敗したらここで中断
    problems = []
    global _fault_stress_cache, _plate_stress_cache
    _fault_stress_cache = {"grid": None, "computed_for": None}
    _plate_stress_cache = {"grid": None, "computed_for": None}

    def _step(label, fn):
        try:
            fn()
        except Exception as e:
            print(f"[再計算/riskmap] {label}: {e}")
            problems.append(f"{label}: {e}")

    _step("活断層・プレート境界", _recalc_geo_data)
    with _cache_lock:
        quakes = _cached_data["all"]
    _step("活断層の応力負荷", lambda: get_fault_stress_grid(quakes))
    _step("プレート境界の応力負荷", lambda: get_plate_stress_grid(quakes))
    _step("気圧", _recalc_pressure)
    if TEC_ENABLED:
        _step("TEC", _recalc_tec)
    _step("キャリブレーション", _recalc_calibration)   # 一番重いので最後
    if problems:
        msg += "／⚠ " + " / ".join(problems)
    return msg

_RECALC_HANDLERS = {
    "riskmap":  _recalc_riskmap,
    "history":  _recalc_core_cache,
    "etas":     _recalc_etas,
    "bvalue":   _recalc_core_cache,
    "faultmap": lambda: _recalc_geo_data(force=True),
    "tec":      _recalc_tec,
    "gnss":     _recalc_gnss,
    "pressure": _recalc_pressure,
}

def _recalc_job_view(job):
    end = job["finished"] or time.time()
    return {"running": job["running"], "ok": job["ok"], "message": job["message"],
            "elapsed": round(end - job["started"], 1)}

@app.route("/recalc/<name>", methods=["POST"])
def recalc_start(name):
    fn = _RECALC_HANDLERS.get(name)
    if fn is None:
        return {"error": "このタブは再計算に対応していません"}, 404
    with _recalc_jobs_lock:
        job = _recalc_jobs.get(name)
        if job and job["running"]:
            return {"status": "already_running", **_recalc_job_view(job)}
        job = {"running": True, "ok": None, "message": "", "started": time.time(), "finished": None}
        _recalc_jobs[name] = job

    def _run():
        try:
            job["message"] = fn() or "完了"
            job["ok"] = True
        except Exception as e:
            import traceback; traceback.print_exc()
            job["message"] = str(e) or e.__class__.__name__
            job["ok"] = False
        finally:
            job["finished"] = time.time()
            job["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}

@app.route("/recalc/<name>/status")
def recalc_status(name):
    with _recalc_jobs_lock:
        job = _recalc_jobs.get(name)
        if job is None:
            return {"running": False, "ok": None, "message": "", "elapsed": 0}
        return _recalc_job_view(job)


if __name__ == "__main__":
    # ★ Bug fix (2026-08): quakes.csv の復元は、バックグラウンド更新ループが
    # 1回目に load_quakes() でキャッシュを温める前に完了している必要があるため、
    # スナップショット復元（非同期）とは別に、ここで同期的に実行する。
    _github_restore_quakes_csv_sync()
    _github_restore_async()
    # 絶対基準キャリブレーション（過去65日間の実績プール作成）も起動直後に
    # 開始しておく。この関数自体が内部でバックグラウンドスレッドを起動して
    # 即座に返るため、ここで呼んでもアプリ起動をブロックしない。
    # 初回リクエストまで待つと最初の数時間は相対評価のみにフォールバック
    # してしまうため、できるだけ早く準備を始める。
    _get_historical_calibration()
    threading.Thread(target=_update_data, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
