# -*- coding: utf-8 -*-
"""
地震発生確率マップ + 気象情報 統合版 (軽量版)

メモリ軽量化のポイント:
  - folium を完全廃止 → 純 Leaflet JS HTML
  - _cached_maps にデータのみ保持（HTML文字列を保持しない）
  - タブは /tab/<name> エンドポイントでリクエスト時レンダリング
  - アメダス・警報はオンデマンド取得 + 短期キャッシュ
  - GeoJSON はブラウザ側でフェッチ（srcdoc には含めない）
"""

from flask import Flask, Response
import requests, csv, os, math, re, hashlib, json, threading, time
from datetime import datetime, timezone, timedelta
import numpy as np

app = Flask(__name__)

# ── 定数 ──────────────────────────────────────────────
DATA_FILE          = "data/quakes.csv"
GRID_SIZE          = 0.1
FETCH_INTERVAL_SEC = 600

# ── グローバルキャッシュ（データのみ・HTML文字列は保持しない）──
_cache_lock   = threading.Lock()
_cached_data  = None   # {"jma": [...], "unfelt": [...], "etas": {...}, "updated": str}
_last_update  = 0.0
_ready_phase  = 0

# アメダス・警報用の短期キャッシュ（~5分）
_amedas_cache = {"data": None, "ts": 0.0}
_warning_cache = {"data": None, "ts": 0.0}
AMEDAS_CACHE_SEC = 300
WARNING_CACHE_SEC = 300


# ══════════════════════════════════════════════════════
# ETAS パラメータ
# ══════════════════════════════════════════════════════
class ETASParams:
    MU=0.05; K=0.020; C=0.010; P=1.11; ALPHA=2.30; M0=1.0
    D=0.015; GAMMA=0.50; Q=1.58; DEPTH_SCALE=80.0; SPACE_RADIUS=8
EP = ETASParams()

ETAS_COLOR = {5:"#1a0033",4:"#8000ff",3:"#ff0000",2:"#ff8800",1:"#66ccff"}


# ══════════════════════════════════════════════════════
# 地震データ取得
# ══════════════════════════════════════════════════════
def fetch_quakes_p2p():
    url = "https://api.p2pquake.net/v2/history?codes=551&limit=100"
    try:
        data = requests.get(url, timeout=10, headers={"User-Agent":"App/4.1"}).json()
    except Exception as e:
        print(f"[P2P] {e}"); return []
    quakes = []
    for item in data:
        if "earthquake" not in item: continue
        eq = item["earthquake"]; hypo = eq.get("hypocenter", {})
        try:
            lat = float(hypo["latitude"]); lon = float(hypo["longitude"])
            if lat == -200 or lon == -200: continue
            mag = float(hypo["magnitude"]); depth = abs(float(hypo.get("depth",0)))
            raw_time = eq.get("time","")
            try:
                now_y = datetime.now().year
                dt_jst = datetime.strptime(f"{now_y}/{raw_time}", "%Y/%m/%d %H:%M")
                time_str = dt_jst.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc).isoformat()
            except Exception: time_str = raw_time
            quakes.append({"time":time_str,"lat":lat,"lon":lon,"mag":mag,"depth":depth,"source":"p2p"})
        except Exception: continue
    print(f"[P2P] {len(quakes)}件"); return quakes

JMA_LIST_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"

def _parse_jma_cod(cod_str):
    m = re.match(r'([+-][0-9.]+)([+-][0-9.]+)([+-][0-9.]+)?/?', cod_str.strip())
    if not m: raise ValueError(cod_str)
    return float(m.group(1)), float(m.group(2)), abs(float(m.group(3)))/1000.0 if m.group(3) else 0.0

def fetch_quakes_jma_bosai():
    try:
        data = requests.get(JMA_LIST_URL, timeout=10, headers={"User-Agent":"App/4.1"}).json()
    except Exception as e:
        print(f"[JMA] {e}"); return []
    quakes = []
    for item in data:
        if item.get("ttl") != "震源・震度情報": continue
        if item.get("ift") in ("訂正","取消"): continue
        maxi = item.get("maxi","")
        if not maxi: continue
        try: lat, lon, depth = _parse_jma_cod(item["cod"])
        except Exception: continue
        try: mag = float(item.get("mag","0"))
        except Exception: mag = 0.0
        at_str = item.get("at", item.get("rdt",""))
        try: time_str = datetime.fromisoformat(at_str).astimezone(timezone.utc).isoformat()
        except Exception: time_str = at_str
        quakes.append({"time":time_str,"lat":lat,"lon":lon,"mag":mag,"depth":depth,
                       "source":"jma_bosai","place":item.get("anm","不明"),"max_int":maxi})
    print(f"[JMA] {len(quakes)}件"); return quakes

def fetch_quakes_usgs():
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    url = (f"https://earthquake.usgs.gov/fdsnws/event/1/query"
           f"?format=geojson&starttime={start}&minlatitude=24&maxlatitude=46"
           f"&minlongitude=122&maxlongitude=146&minmagnitude=1.0&orderby=time&limit=500")
    try: data = requests.get(url, timeout=15).json()
    except Exception as e:
        print(f"[USGS] {e}"); return []
    quakes = []
    for feat in data.get("features",[]):
        try:
            props = feat["properties"]; coords = feat["geometry"]["coordinates"]
            t = datetime.fromtimestamp(props["time"]/1000, tz=timezone.utc)
            quakes.append({"time":t.isoformat(),"lat":float(coords[1]),"lon":float(coords[0]),
                           "mag":float(props["mag"]),"depth":float(coords[2]),"source":"usgs"})
        except Exception: continue
    print(f"[USGS] {len(quakes)}件"); return quakes

def fetch_all_quakes():
    results = {}
    def _run(name, fn): results[name] = fn()
    threads = [threading.Thread(target=_run, args=(n,f)) for n,f in
               [("p2p",fetch_quakes_p2p),("usgs",fetch_quakes_usgs),("jma",fetch_quakes_jma_bosai)]]
    for t in threads: t.start()
    for t in threads: t.join()
    all_q = results.get("p2p",[]) + results.get("usgs",[]) + results.get("jma",[])
    return _deduplicate(all_q)

def _deduplicate(quakes, time_tol_min=5, dist_tol_deg=0.3):
    prio = {"jma_bosai":0,"p2p":1,"usgs":2}
    sorted_q = sorted(quakes, key=lambda q: prio.get(q["source"],9))
    kept = []
    for q in sorted_q:
        try: t_q = datetime.fromisoformat(q["time"].replace("Z","+00:00"))
        except Exception: t_q = None
        dup = False
        for k in kept:
            try: t_k = datetime.fromisoformat(k["time"].replace("Z","+00:00")); dt = abs((t_q-t_k).total_seconds())/60 if t_q and t_k else 999
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

def load_quakes():
    if not os.path.exists(DATA_FILE): return []
    data = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                data.append({"time":row[0],"lat":float(row[1]),"lon":float(row[2]),
                             "mag":float(row[3]),"depth":float(row[4]),
                             "source":row[5] if len(row)>5 else "","place":row[6] if len(row)>6 else "",
                             "max_int":row[7] if len(row)>7 else ""})
            except Exception: continue
    return data


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
    dist2 = (DI[:,:,None] * GRID_SIZE)**2 + (DJ[:,:,None] * GRID_SIZE)**2
    keys_list = []; vals_list = []
    for ei in range(len(valid)):
        sc = spatial_scale[ei]
        q_val = EP.Q
        weight = contributions[ei] / (dist2[:,:,ei] + sc**2) ** q_val
        ni = gi[ei] + DI; nj = gj[ei] + DJ
        mask = (ni>=240)&(ni<=460)&(nj>=1220)&(nj<=1460)
        keys_flat = ni[mask].astype(np.int64)*100000 + nj[mask].astype(np.int64)
        keys_list.append(keys_flat); vals_list.append(weight[mask])
    if not keys_list: return {}
    all_keys = np.concatenate(keys_list); all_vals = np.concatenate(vals_list)
    unique_keys, inverse = np.unique(all_keys, return_inverse=True)
    agg_vals = np.zeros(len(unique_keys)); np.add.at(agg_vals, inverse, all_vals)
    grid_scores = {}
    for idx, k in enumerate(unique_keys):
        gi_k = int(k) // 100000; gj_k = int(k) % 100000
        v = float(agg_vals[idx]) + EP.MU
        if v > EP.MU * 1.01: grid_scores[(gi_k, gj_k)] = v
    return grid_scores

def _percentile_thresholds(values_arr):
    log_v = np.log(np.clip(values_arr, 0, None) + 1)
    return (np.percentile(log_v,99.8), np.percentile(log_v,98.5),
            np.percentile(log_v,95.0), np.percentile(log_v,85.0),
            np.percentile(log_v,50.0))


# ══════════════════════════════════════════════════════
# 色・ラベルユーティリティ
# ══════════════════════════════════════════════════════
INTENSITY_COLOR = {"1":"#4ade80","2":"#a3e635","3":"#facc15","4":"#fb923c",
                   "5-":"#f87171","5+":"#ef4444","6-":"#dc2626","6+":"#b91c1c","7":"#7f1d1d"}
INTENSITY_LABEL = {"1":"震度1","2":"震度2","3":"震度3","4":"震度4",
                   "5-":"震度5弱","5+":"震度5強","6-":"震度6弱","6+":"震度6強","7":"震度7","-":"不明"}
WIND_DIR_16 = ["北","北北東","北東","東北東","東","東南東","南東","南南東",
               "南","南南西","南西","西南西","西","西北西","北西","北北西"]

def _int_color(v): return INTENSITY_COLOR.get(v,"#94a3b8")
def _int_label(v): return INTENSITY_LABEL.get(v,"不明")

def _quake_after(q, cutoff):
    try: return datetime.fromisoformat(q["time"].replace("Z","+00:00")) >= cutoff
    except Exception: return True

def _fmt_time_jst(time_str):
    try:
        dt = datetime.fromisoformat(time_str.replace("Z","+00:00"))
        return dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
    except Exception: return time_str


# ══════════════════════════════════════════════════════
# 共通Leaflet HTMLヘルパー
# ══════════════════════════════════════════════════════
LEAFLET_CDN = """
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>"""

GEOJSON_JS = """
    fetch('https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson')
      .then(r=>r.json())
      .then(d=>L.geoJSON(d,{style:{fillOpacity:0,color:'#555',weight:1}}).addTo(map));"""

DARK_TILE = "L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy;CartoDB',subdomains:'abcd',maxZoom:18}).addTo(map);"


# ══════════════════════════════════════════════════════
# 有感地震履歴タブ
# ══════════════════════════════════════════════════════
def render_felt_quake(jma_quakes, updated_str):
    one_month_ago = datetime.now(timezone.utc) - timedelta(days=31)
    sorted_q = sorted([q for q in jma_quakes if _quake_after(q, one_month_ago)],
                      key=lambda q: q.get("time",""), reverse=True)

    # マーカーデータをJSに渡す（foliumなし）
    markers = []
    for i, q in enumerate(sorted_q):
        ci  = _int_color(q.get("max_int","-"))
        mi  = _int_label(q.get("max_int","-"))
        mag = q.get("mag", 0)
        markers.append({"lat":q["lat"],"lon":q["lon"],"color":ci,
                        "radius":max(5,mag*3),"idx":i,
                        "tip":f"{q.get('place','不明')} M{mag:.1f} {mi}",
                        "pop":f"<b>{q.get('place','不明')}</b><br>{_fmt_time_jst(q.get('time',''))} JST<br>M{mag:.1f} / {mi}<br>深さ {q.get('depth',0):.0f}km"})

    rows = ""
    for i, q in enumerate(sorted_q):
        ci    = _int_color(q.get("max_int","-"))
        mi    = _int_label(q.get("max_int","-"))
        place = q.get("place","不明"); mag = q.get("mag",0)
        depth = q.get("depth",0); t_str = _fmt_time_jst(q.get("time",""))
        rows += (f'<tr onclick="focusQ({i},{q["lat"]},{q["lon"]})" '
                 f'style="cursor:pointer" class="qrow" id="qrow_{i}">'
                 f'<td class="c1">{place}</td>'
                 f'<td class="c2">{t_str}</td>'
                 f'<td class="c3">M{mag:.1f}</td>'
                 f'<td class="c4"><span style="background:{ci};color:#000;padding:2px 5px;border-radius:3px;font-size:11px;font-weight:700;white-space:nowrap">{mi}</span></td>'
                 f'<td class="c2">{depth:.0f}km</td></tr>')

    markers_js = json.dumps(markers)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;height:100vh;background:#0f172a;color:#fff;font-family:"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#lp{{width:360px;flex-shrink:0;background:#111827;border-right:2px solid #1f2937;display:flex;flex-direction:column;overflow:hidden}}
#lh{{padding:12px 14px 8px;background:#1f2937;border-bottom:1px solid #374151;flex-shrink:0}}
#lh h2{{font-size:15px;color:#f3f4f6;margin-bottom:3px}}#lh p{{font-size:11px;color:#6b7280}}
#ls{{flex:1;overflow-y:auto}}
table{{width:100%;border-collapse:collapse}}
thead tr{{background:#1f2937;position:sticky;top:0;z-index:10}}
thead th{{padding:7px 5px;font-size:11px;color:#9ca3af;text-align:left;border-bottom:1px solid #374151}}
.qrow:hover{{background:#1f2937}}.qrow:nth-child(even){{background:#0d1117}}
.c1{{padding:6px 7px;font-weight:600;color:#f3f4f6;font-size:12px}}
.c2{{padding:6px 4px;color:#9ca3af;font-size:11px}}
.c3{{padding:6px 4px;text-align:center;font-weight:700;color:#60a5fa;font-size:12px}}
.c4{{padding:6px 4px;text-align:center}}
#mp{{flex:1;overflow:hidden;position:relative}}
#map{{width:100%;height:100%}}
#db{{position:fixed;bottom:0;left:360px;right:0;background:rgba(17,24,39,.95);
    border-top:1px solid #374151;padding:7px 14px;font-size:12px;color:#d1d5db;display:none;z-index:999}}
</style></head><body>
<div id="lp">
  <div id="lh"><h2>&#127981; 有感地震履歴（JMA）</h2><p>直近{len(sorted_q)}件 / {updated_str}</p></div>
  <div id="ls"><table>
    <thead><tr><th>震源名</th><th>発生時刻</th><th>M</th><th>最大震度</th><th>深さ</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</div>
<div id="mp"><div id="map"></div></div>
<div id="db"><span id="dt"></span></div>
<script>
var map = L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK = {markers_js};
var layers = MK.map(function(d){{
  return L.circleMarker([d.lat,d.lon],{{radius:d.radius,color:d.color,fillColor:d.color,fillOpacity:0.8,weight:1}})
          .bindTooltip(d.tip).bindPopup(d.pop);
}});
var lg = L.layerGroup(layers).addTo(map);
function focusQ(idx,lat,lon){{
  document.querySelectorAll('.qrow').forEach(function(r){{r.style.background=''}});
  var row=document.getElementById('qrow_'+idx); if(row) row.style.background='#1e3a5f';
  map.flyTo([lat,lon],8,{{duration:0.8}});
  if(layers[idx]) setTimeout(function(){{layers[idx].openPopup()}},900);
  var d=MK[idx];
  document.getElementById('dt').innerHTML='&#128205; <b>'+d.tip+'</b>';
  document.getElementById('db').style.display='block';
}}
</script></body></html>"""


# ══════════════════════════════════════════════════════
# 無感地震履歴タブ
# ══════════════════════════════════════════════════════
def render_unfelt_quake(unfelt_quakes, updated_str):
    sorted_q = sorted(unfelt_quakes, key=lambda q: q.get("time",""), reverse=True)

    def _mag_color(mag):
        if   mag >= 8.0: return "#ff00ff"
        elif mag >= 7.0: return "#7f1d1d"
        elif mag >= 6.0: return "#ef4444"
        elif mag >= 5.0: return "#fb923c"
        elif mag >= 4.0: return "#facc15"
        elif mag >= 3.0: return "#4ade80"
        else:            return "#94a3b8"

    src_count = {}
    markers = []
    for q in sorted_q:
        src = q.get("source","?"); src_count[src] = src_count.get(src,0)+1
        mag=q.get("mag",0); depth=q.get("depth",0); t_str=_fmt_time_jst(q.get("time",""))
        ci=_mag_color(mag)
        markers.append({"lat":q["lat"],"lon":q["lon"],"color":ci,
                        "radius":max(3,mag*2.5),
                        "tip":f"M{mag:.1f} / {depth:.0f}km / {t_str} [{src}]",
                        "pop":f"M{mag:.1f}<br>深さ {depth:.0f}km<br>{t_str} JST<br>ソース:{src}"})

    markers_js = json.dumps(markers)
    p2p_n = src_count.get("p2p",0); usgs_n = src_count.get("usgs",0)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>*{{box-sizing:border-box;margin:0;padding:0}}body,html{{height:100%;overflow:hidden}}
#map{{width:100%;height:100vh}}</style></head><body>
<div id="map"></div>
<div style="position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:2;color:#f3f4f6">
  <b>&#127774; 無感地震履歴</b><br>
  <span style="color:#ff00ff">&#9679;</span> M8.0以上<br>
  <span style="color:#7f1d1d">&#9679;</span> M7.0〜7.9<br>
  <span style="color:#ef4444">&#9679;</span> M6.0〜6.9<br>
  <span style="color:#fb923c">&#9679;</span> M5.0〜5.9<br>
  <span style="color:#facc15">&#9679;</span> M4.0〜4.9<br>
  <span style="color:#4ade80">&#9679;</span> M3.0〜3.9<br>
  <span style="color:#94a3b8">&#9679;</span> M2台以下<br>
  <hr style="border-color:#374151;margin:5px 0">
  <small>P2P:{p2p_n}件 USGS:{usgs_n}件<br>計{len(sorted_q)}件 | {updated_str}</small>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK={markers_js};
MK.forEach(function(d){{
  L.circleMarker([d.lat,d.lon],{{radius:d.radius,color:d.color,fillColor:d.color,fillOpacity:0.7,weight:1}})
   .bindTooltip(d.tip).bindPopup(d.pop).addTo(map);
}});
</script></body></html>"""


# ══════════════════════════════════════════════════════
# アメダス観測値タブ（オンデマンド取得 + 短期キャッシュ）
# ══════════════════════════════════════════════════════
def _fetch_amedas_table():
    url = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
    try:
        raw = requests.get(url, timeout=10, headers={"User-Agent":"App/4.1"}).json()
        table = {}
        for sid, info in raw.items():
            lr = info.get("lat",[0,0]); lo = info.get("lon",[0,0])
            table[sid] = {"name":info.get("kjName",sid),
                          "lat":lr[0]+lr[1]/60.0, "lon":lo[0]+lo[1]/60.0}
        print(f"[AMEDAS] テーブル:{len(table)}局"); return table
    except Exception as e:
        print(f"[AMEDAS] テーブルエラー: {e}"); return {}

def _fetch_amedas_latest():
    try:
        t_text = requests.get("https://www.jma.go.jp/bosai/amedas/data/latest_time.txt",
                              timeout=8, headers={"User-Agent":"App/4.1"}).text.strip()
        dt = datetime.fromisoformat(t_text)
        ts = dt.strftime("%Y%m%d%H%M%S")
        data = requests.get(f"https://www.jma.go.jp/bosai/amedas/data/map/{ts}.json",
                            timeout=10, headers={"User-Agent":"App/4.1"}).json()
        time_label = dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M JST")
        print(f"[AMEDAS] 観測値:{len(data)}局 ({time_label})"); return data, time_label
    except Exception as e:
        print(f"[AMEDAS] 観測値エラー: {e}"); return {}, "取得失敗"

def _gradient_color(val, vmin, vmax, scheme):
    ratio = max(0.0, min(1.0, (val-vmin)/max(vmax-vmin, 0.01)))
    SCHEMES = {
        "heat": [(0,(0,0,180)),(0.20,(0,80,255)),(0.35,(0,220,100)),(0.50,(255,255,0)),(0.65,(255,160,0)),(0.80,(255,60,0)),(1.0,(180,0,0))],
        "prec": [(0,(255,255,255)),(0.10,(150,220,255)),(0.25,(0,80,220)),(0.40,(0,200,80)),(0.55,(230,230,0)),(0.70,(255,130,0)),(0.85,(220,20,20)),(1.0,(140,0,200))],
        "pres": [(0,(80,0,180)),(0.25,(160,80,240)),(0.50,(230,230,230)),(0.75,(255,180,40)),(1.0,(200,80,0))],
        "wind": [(0,(240,240,255)),(0.15,(80,220,240)),(0.30,(0,100,220)),(0.50,(230,230,0)),(0.65,(255,130,0)),(0.80,(220,20,20)),(1.0,(140,0,200))],
    }
    stops = SCHEMES.get(scheme, [(0,(128,128,128)),(1,(128,128,128))])
    r,g,b = stops[-1][1]
    for k in range(len(stops)-1):
        lo,hi = stops[k][0], stops[k+1][0]
        if lo <= ratio <= hi:
            t = (ratio-lo)/(hi-lo)
            r0,g0,b0 = stops[k][1]; r1,g1,b1 = stops[k+1][1]
            r,g,b = int(r0+(r1-r0)*t), int(g0+(g1-g0)*t), int(b0+(b1-b0)*t)
            break
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"

def render_amedas(updated_str):
    global _amedas_cache
    now = time.time()
    if _amedas_cache["data"] and now - _amedas_cache["ts"] < AMEDAS_CACHE_SEC:
        cached = _amedas_cache["data"]
        table, obs_data, time_label = cached["table"], cached["obs"], cached["label"]
    else:
        table = _fetch_amedas_table(); obs_data, time_label = _fetch_amedas_latest()
        _amedas_cache = {"data":{"table":table,"obs":obs_data,"label":time_label},"ts":now}

    if not obs_data or not table:
        return "<html><body style='background:#0f172a;color:white;display:flex;align-items:center;justify-content:center;height:100vh'>アメダスデータ取得失敗</body></html>"

    def _gv(obs, key):
        raw = obs.get(key)
        return raw[0] if isinstance(raw,list) and len(raw)>0 and raw[0] is not None else None

    temp_vals=[]; prec_vals=[]; pres_vals=[]; wind_vals=[]
    for sid,obs in obs_data.items():
        v=_gv(obs,"temp");            temp_vals.append(v) if v is not None else None
        v=_gv(obs,"precipitation1h"); prec_vals.append(v) if v is not None else None
        v=_gv(obs,"pressure");        pres_vals.append(v) if v is not None else None
        v=_gv(obs,"wind");            wind_vals.append(v) if v is not None else None

    t_min=-20.0; t_max=45.0
    p_min=min(prec_vals) if prec_vals else 0;   p_max=max(prec_vals) if prec_vals else 50
    pr_min=min(pres_vals) if pres_vals else 980; pr_max=max(pres_vals) if pres_vals else 1030
    w_min=0; w_max=max(wind_vals) if wind_vals else 20

    layers = {"temp":[],"prec":[],"pres":[],"wind":[]}
    for sid, obs in obs_data.items():
        info = table.get(sid)
        if not info: continue
        lat,lon = info["lat"], info["lon"]
        if not (24<=lat<=46 and 122<=lon<=146): continue
        name=info["name"]
        temp=_gv(obs,"temp"); prec=_gv(obs,"precipitation1h")
        pres=_gv(obs,"pressure"); wind=_gv(obs,"wind")
        wdir_raw=obs.get("windDirection"); wdir_idx=wdir_raw[0] if isinstance(wdir_raw,list) else None
        wdir_str=WIND_DIR_16[wdir_idx-1] if wdir_idx and 1<=wdir_idx<=16 else "静穏"
        if temp is not None:
            layers["temp"].append({"lat":lat,"lon":lon,"color":_gradient_color(temp,t_min,t_max,"heat"),
                "tip":f"{name} {temp}℃","pop":f"<b>{name}</b><br>気温:<b>{temp}℃</b>"})
        if prec is not None and prec > 0:
            layers["prec"].append({"lat":lat,"lon":lon,"color":_gradient_color(prec,p_min,p_max,"prec"),
                "tip":f"{name} {prec}mm/h","pop":f"<b>{name}</b><br>降水量:<b>{prec}mm/h</b>"})
        if pres is not None:
            layers["pres"].append({"lat":lat,"lon":lon,"color":_gradient_color(pres,pr_min,pr_max,"pres"),
                "tip":f"{name} {pres}hPa","pop":f"<b>{name}</b><br>海面気圧:<b>{pres}hPa</b>"})
        if wind is not None:
            ang = (wdir_idx-1)*22.5 if wdir_idx and 1<=wdir_idx<=16 else 0
            layers["wind"].append({"lat":lat,"lon":lon,"color":_gradient_color(wind,w_min,w_max,"wind"),
                "tip":f"{name} {wdir_str} {wind}m/s","pop":f"<b>{name}</b><br>風速:<b>{wind}m/s</b><br>風向:{wdir_str}",
                "angle":ang})

    data_js = json.dumps(layers)
    legends = {
        "temp": f'<b>🌡 気温</b><br><div style="width:130px;height:10px;border-radius:3px;background:linear-gradient(to right,#0000b4,#0050ff,#00e050,#ffff00,#ff8200,#ff3c00,#b40000);margin:5px 0 2px"></div><div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af"><span>-20℃</span><span>45℃</span></div>',
        "prec": f'<b>🌧 降水量</b><br><div style="width:130px;height:10px;border-radius:3px;background:linear-gradient(to right,#fff,#96dcff,#0050dc,#00c850,#e6e600,#ff8200,#dc1414,#8c00c8);margin:5px 0 2px"></div><div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af"><span>0mm</span><span>{p_max:.0f}mm</span></div>',
        "pres": f'<b>📊 海面気圧</b><br><div style="width:130px;height:10px;border-radius:3px;background:linear-gradient(to right,#5000b4,#a050f0,#e6e6e6,#ffb428,#c85000);margin:5px 0 2px"></div><div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af"><span>{pr_min:.0f}hPa</span><span>{pr_max:.0f}hPa</span></div>',
        "wind": f'<b>💨 風速（▲風向）</b><br><div style="width:130px;height:10px;border-radius:3px;background:linear-gradient(to right,#f0f0ff,#50dcf0,#0064dc,#e6e600,#ff8200,#dc1414,#8c00c8);margin:5px 0 2px"></div><div style="display:flex;justify-content:space-between;width:130px;font-size:10px;color:#9ca3af"><span>0m/s</span><span>{w_max:.0f}m/s</span></div>',
    }
    legends_js = json.dumps(legends)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#tb{{display:flex;background:#111827;border-bottom:2px solid #1f2937;flex-shrink:0}}
.at{{flex:1;padding:10px 4px;text-align:center;cursor:pointer;font-size:13px;font-weight:600;color:#9ca3af;border:none;background:none}}
.at:hover{{color:#f3f4f6;background:#1f2937}}.at.active{{color:#fff;background:linear-gradient(135deg,#2563eb,#7c3aed);border-bottom:2px solid #60a5fa}}
#map{{flex:1}}
#lg{{position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:1.8;color:#f3f4f6;pointer-events:none}}
</style></head><body>
<div id="tb">
  <button class="at active" onclick="sw('temp',this)">🌡 気温</button>
  <button class="at" onclick="sw('prec',this)">🌧 降水量</button>
  <button class="at" onclick="sw('pres',this)">📊 気圧</button>
  <button class="at" onclick="sw('wind',this)">💨 風速</button>
</div>
<div id="map"></div>
<div id="lg"></div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
var DATA={data_js};
var LGD={legends_js};
var cur=null;
function mkWindIcon(color,angle){{
  var sz=14,c=document.createElement('canvas');c.width=sz;c.height=sz;
  var ctx=c.getContext('2d');ctx.save();ctx.translate(sz/2,sz/2);ctx.rotate(angle*Math.PI/180);
  ctx.beginPath();ctx.moveTo(0,-sz/2+1);ctx.lineTo(sz/2-1,sz/2-2);ctx.lineTo(-sz/2+1,sz/2-2);
  ctx.closePath();ctx.fillStyle=color;ctx.globalAlpha=0.9;ctx.fill();ctx.restore();
  return L.icon({{iconUrl:c.toDataURL(),iconSize:[sz,sz],iconAnchor:[sz/2,sz/2]}});
}}
function sw(key,btn){{
  document.querySelectorAll('.at').forEach(function(b){{b.classList.remove('active')}});
  btn.classList.add('active');
  if(cur)map.removeLayer(cur);
  var items=DATA[key];
  var mk=items.map(function(d){{
    if(key==='wind')return L.marker([d.lat,d.lon],{{icon:mkWindIcon(d.color,d.angle||0)}}).bindTooltip(d.tip).bindPopup(d.pop);
    return L.circleMarker([d.lat,d.lon],{{radius:5,color:d.color,fillColor:d.color,fillOpacity:0.85,weight:1}}).bindTooltip(d.tip).bindPopup(d.pop);
  }});
  cur=L.layerGroup(mk).addTo(map);
  document.getElementById('lg').innerHTML=LGD[key]+'<hr style="border-color:#374151;margin:5px 0"><small>{time_label}<br>{updated_str}</small>';
}}
sw('temp',document.querySelector('.at.active'));
</script></body></html>"""


# ══════════════════════════════════════════════════════
# 雨雲レーダータブ
# ══════════════════════════════════════════════════════
def render_radar(updated_str):
    now_utc = datetime.now(timezone.utc)
    hour_floor = now_utc.replace(minute=0,second=0,microsecond=0) - timedelta(hours=1)
    frames = []
    for i in range(23,-1,-1):
        dt_f = hour_floor - timedelta(hours=i)
        frames.append({"dt_str":dt_f.strftime("%Y%m%d%H%M%S"),
                       "label":dt_f.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M JST")})
    frames_js = json.dumps(frames)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{display:flex;flex-direction:column;height:100vh;background:#0f172a;overflow:hidden;font-family:"Helvetica Neue",Arial,sans-serif}}
#map{{flex:1}}
#ctrl{{background:#111827;border-top:2px solid #1f2937;padding:9px 14px;flex-shrink:0;
       display:flex;align-items:center;gap:12px;color:#f3f4f6;font-size:13px}}
#pb{{background:#2563eb;color:white;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px;font-weight:600;flex-shrink:0}}
#sl{{flex:1;accent-color:#3b82f6}}#tl{{min-width:90px;text-align:right;color:#93c5fd;font-weight:600}}
#lg{{position:fixed;bottom:60px;left:12px;z-index:1000;background:rgba(17,24,39,.92);padding:9px 12px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:1.9;color:#f3f4f6;pointer-events:none}}
</style></head><body>
<div id="map"></div>
<div id="ctrl">
  <button id="pb" onclick="tp()">▶ 再生</button>
  <input type="range" id="sl" min="0" max="23" value="23" step="1" oninput="sf(+this.value)">
  <span id="tl">--:-- JST</span>
</div>
<div id="lg">
  <b>🌧 高解像度降水ナウキャスト</b><br>
  <div style="display:flex;gap:3px;align-items:center;font-size:11px;margin-top:4px">
    <div style="width:12px;height:10px;background:#c8f0ff;border:1px solid #555"></div>1未満
    <div style="width:12px;height:10px;background:#5db8f5;border:1px solid #555;margin-left:2px"></div>5
    <div style="width:12px;height:10px;background:#0050f0;border:1px solid #555;margin-left:2px"></div>10
    <div style="width:12px;height:10px;background:#faf500;border:1px solid #555;margin-left:2px"></div>20
    <div style="width:12px;height:10px;background:#ff9900;border:1px solid #555;margin-left:2px"></div>30
    <div style="width:12px;height:10px;background:#ff2800;border:1px solid #555;margin-left:2px"></div>50
    <div style="width:12px;height:10px;background:#b400e6;border:1px solid #555;margin-left:2px"></div>80+
  </div>
  <hr style="border-color:#374151;margin:5px 0"><small>出典: 気象庁<br>{updated_str}</small>
</div>
<script>
var FRAMES={frames_js};
var map=L.map('map',{{center:[36,138],zoom:6,preferCanvas:true}});
{DARK_TILE}
fetch('https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson')
  .then(r=>r.json()).then(d=>L.geoJSON(d,{{style:{{fillOpacity:0,color:'#555',weight:1}}}}).addTo(map));
var rl=null,ci=FRAMES.length-1,playing=false,pt=null;
function sf(idx){{ci=idx;document.getElementById('sl').value=idx;document.getElementById('tl').textContent=FRAMES[idx].label;
  var dt=FRAMES[idx].dt_str;
  if(rl)map.removeLayer(rl);
  rl=L.tileLayer('https://www.jma.go.jp/bosai/jmatile/data/nowc/'+dt+'/none/'+dt+'/surf/hrpns/{{z}}/{{x}}/{{y}}.png',
    {{attribution:'気象庁',opacity:0.75,minZoom:4,maxZoom:14,errorTileUrl:''}}).addTo(map);
}}
function tp(){{playing=!playing;document.getElementById('pb').textContent=playing?'⏸ 停止':'▶ 再生';
  if(playing){{pt=setInterval(function(){{sf((ci+1)%FRAMES.length)}},800)}}else{{clearInterval(pt)}}
}}
sf(ci);
</script></body></html>"""


# ══════════════════════════════════════════════════════
# 警報・注意報タブ（オンデマンド取得 + 短期キャッシュ）
# ══════════════════════════════════════════════════════
PREF_CODE_LIST = [
    "011000","012000","013000","014030","014100","015000","016000","017000","018000","019000",
    "020000","030000","040000","050000","060000","070000","080000","090000","100000",
    "110000","120000","130000","140000","150000","160000","170000","180000","190000","200000",
    "210000","220000","230000","240000","250000","260000","270000","280000","290000","300000",
    "310000","320000","330000","340000","350000","360000","370000","380000","390000","400000",
    "410000","420000","430000","440000","450000","460100","471000","472000","473000","474000",
]
WARNING_TYPES = {
    "10":("大雨警報",3,"#ef4444"),"12":("洪水警報",3,"#ef4444"),"14":("暴風警報",3,"#ef4444"),
    "15":("暴風雪警報",3,"#ef4444"),"16":("大雪警報",3,"#ef4444"),"17":("波浪警報",3,"#ef4444"),
    "18":("高潮警報",3,"#ef4444"),"19":("津波警報",3,"#ef4444"),"20":("大津波警報",3,"#7f1d1d"),
    "32":("大雨注意報",2,"#fb923c"),"33":("洪水注意報",2,"#fb923c"),"34":("強風注意報",2,"#fb923c"),
    "35":("風雪注意報",2,"#fb923c"),"36":("大雪注意報",2,"#fb923c"),"37":("波浪注意報",2,"#fb923c"),
    "38":("高潮注意報",2,"#fb923c"),"39":("濃霧注意報",1,"#fbbf24"),"40":("雷注意報",1,"#fbbf24"),
    "42":("乾燥注意報",1,"#fbbf24"),"43":("なだれ注意報",1,"#fbbf24"),"44":("低温注意報",1,"#fbbf24"),
    "45":("霜注意報",1,"#fbbf24"),"46":("着氷注意報",1,"#fbbf24"),"47":("着雪注意報",1,"#fbbf24"),
    "50":("記録的短時間大雨情報",2,"#c026d3"),
}
PREF_CENTERS = {
    "011000":(43.06,141.35),"012000":(43.4,142.8),"013000":(42.5,143.5),
    "014030":(41.8,140.7),"014100":(42.3,143.3),"015000":(43.5,144.4),
    "016000":(43.3,145.2),"017000":(42.7,141.7),"018000":(43.1,140.6),
    "019000":(42.1,142.9),"020000":(40.82,140.74),"030000":(39.70,141.15),
    "040000":(38.27,140.87),"050000":(39.72,140.10),"060000":(38.24,140.36),
    "070000":(37.75,140.47),"080000":(36.34,140.45),"090000":(36.56,139.88),
    "100000":(36.39,139.06),"110000":(35.86,139.65),"120000":(35.61,140.12),
    "130000":(35.69,139.69),"140000":(35.45,139.64),"150000":(37.90,139.02),
    "160000":(36.70,137.21),"170000":(36.59,136.63),"180000":(36.06,136.22),
    "190000":(35.66,138.57),"200000":(36.65,138.18),"210000":(35.39,136.72),
    "220000":(34.98,138.38),"230000":(35.18,136.91),"240000":(34.73,136.51),
    "250000":(35.00,135.87),"260000":(35.02,135.76),"270000":(34.69,135.50),
    "280000":(34.69,135.18),"290000":(34.69,135.83),"300000":(33.77,135.37),
    "310000":(35.50,134.24),"320000":(35.47,133.06),"330000":(34.66,133.93),
    "340000":(34.40,132.46),"350000":(34.19,131.48),"360000":(34.07,134.56),
    "370000":(34.34,134.05),"380000":(33.84,132.77),"390000":(33.56,133.53),
    "400000":(33.61,130.42),"410000":(33.27,130.30),"420000":(32.74,129.87),
    "430000":(32.79,130.74),"440000":(33.24,131.61),"450000":(31.91,131.42),
    "460100":(31.56,130.56),"471000":(26.21,127.68),"472000":(26.19,127.68),
    "473000":(24.34,124.16),"474000":(24.80,125.28),
}

def _fetch_warnings_all():
    global _warning_cache
    now = time.time()
    if _warning_cache["data"] is not None and now - _warning_cache["ts"] < WARNING_CACHE_SEC:
        return _warning_cache["data"]

    unique_codes = list(dict.fromkeys(PREF_CODE_LIST))
    warning_data = {}
    def _fetch_one(code):
        url = f"https://www.jma.go.jp/bosai/warning/data/warning/{code}.json"
        try:
            data = requests.get(url, timeout=8, headers={"User-Agent":"App/4.1"}).json()
        except Exception: return
        for area_type in ("areaWarning","areaForecast"):
            for area in data.get(area_type,[]):
                items = area.get("warnings",[])
                if not items: continue
                active = []
                for w in items:
                    if w.get("status") not in ("発表","継続","特別警報"): continue
                    wcode = str(w.get("code",""))
                    if wcode in WARNING_TYPES:
                        name,level,color = WARNING_TYPES[wcode]
                        active.append({"type_name":name,"level":level,"color":color})
                if active:
                    active.sort(key=lambda x: -x["level"])
                    area_name = area.get("name",code)
                    key = f"{area_name}:{code}"
                    if key not in warning_data:
                        warning_data[key] = {"pref_code":code,"area_name":area_name,"warnings":active}
    threads = [threading.Thread(target=_fetch_one, args=(c,)) for c in unique_codes]
    for t in threads: t.start()
    for t in threads: t.join()
    _warning_cache = {"data": warning_data, "ts": now}
    return warning_data

def render_warning(updated_str):
    warning_data = _fetch_warnings_all()

    markers = []
    for key, info in warning_data.items():
        pref_code = info["pref_code"]; area_code = key.split(":")[-1] if ":" in key else pref_code
        coord = PREF_CENTERS.get(area_code) or PREF_CENTERS.get(pref_code)
        if not coord:
            for k in PREF_CENTERS:
                if k.startswith(pref_code[:3]): coord = PREF_CENTERS[k]; break
        if not coord: continue
        h = int(hashlib.md5(key.encode()).hexdigest()[:4], 16)
        lat = coord[0]+((h%100)-50)*0.008; lon = coord[1]+((h//100%100)-50)*0.010
        top = info["warnings"][0]; color = top["color"]
        radius = {3:14,2:10,1:7}.get(top["level"],7)
        names = "・".join(w["type_name"] for w in info["warnings"])
        popup = (f"<b>{info['area_name']}</b><br>"
                 + "<br>".join(f'<span style="color:{w["color"]};font-weight:700">&#9632; {w["type_name"]}</span>'
                               for w in info["warnings"]))
        markers.append({"lat":lat,"lon":lon,"color":color,"radius":radius,
                        "tip":f"{info['area_name']}: {names}","pop":popup})

    markers_js = json.dumps(markers)
    n_alert = sum(1 for v in warning_data.values() if any(w["level"]==3 for w in v["warnings"]))
    n_adv   = sum(1 for v in warning_data.values() if all(w["level"]<3  for w in v["warnings"]))

    no_warn_html = ""
    if not warning_data:
        no_warn_html = """<div id="nw" style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
          z-index:1000;background:rgba(17,24,39,.95);padding:24px 32px;border-radius:12px;
          border:2px solid #10b981;font-size:15px;color:#f3f4f6;text-align:center">
          <div style="font-size:40px;margin-bottom:12px">&#9989;</div>
          <b>現在、警報・注意報の発表はありません。</b></div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>*{{box-sizing:border-box;margin:0;padding:0}}body,html{{height:100%;overflow:hidden}}
#map{{width:100%;height:100vh}}</style></head><body>
<div id="map"></div>
{no_warn_html}
<div style="position:fixed;bottom:20px;left:20px;z-index:1000;background:rgba(17,24,39,.92);
    padding:11px 14px;border-radius:8px;border:1px solid #374151;font-size:12px;line-height:2;color:#f3f4f6">
  <b>&#9888; 警報・注意報</b><br>
  <span style="color:#ef4444">&#9679;</span> 警報発令中<br>
  <span style="color:#fb923c">&#9679;</span> 注意報発令中<br>
  <span style="color:#fbbf24">&#9679;</span> その他注意報<br>
  <hr style="border-color:#374151;margin:5px 0">
  <small>警報:{n_alert}地域 / 注意報:{n_adv}地域<br>出典: 気象庁<br>{updated_str}</small>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var MK={markers_js};
MK.forEach(function(d){{
  L.circleMarker([d.lat,d.lon],{{radius:d.radius,color:d.color,fillColor:d.color,fillOpacity:0.75,weight:2}})
   .bindTooltip(d.tip).bindPopup(d.pop).addTo(map);
}});
</script></body></html>"""


# ══════════════════════════════════════════════════════
# ETASマップタブ
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
            cells.append({"lat":gi*GRID_SIZE,"lon":gj*GRID_SIZE,"color":ETAS_COLOR[lv],"lv":lv,"score":round(score,4)})

    cells_js = json.dumps(cells)
    gs = GRID_SIZE
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
{LEAFLET_CDN}
<style>*{{box-sizing:border-box;margin:0;padding:0}}body,html{{height:100%;overflow:hidden}}
#map{{width:100%;height:100vh}}</style></head><body>
<div id="map"></div>
<div style="position:fixed;bottom:20px;left:20px;z-index:1000;background:white;
    padding:11px 14px;border-radius:8px;border:2px solid #8800cc;font-size:12px;line-height:2;color:#111">
  <b>&#9312; ETAS 地震発生確率</b><br>
  <span style="color:#1a0033">&#9632;</span> Level 5（上位0.2%）<br>
  <span style="color:#8000ff">&#9632;</span> Level 4（上位1.5%）<br>
  <span style="color:#ff0000">&#9632;</span> Level 3（上位5.0%）<br>
  <span style="color:#ff8800">&#9632;</span> Level 2（上位15%）<br>
  <span style="color:#66ccff">&#9632;</span> Level 1（上位50%）<br>
  <hr style="border-color:#ccc;margin:5px 0">
  <small>JMA:{src_count.get('jma_bosai',0)} P2P:{src_count.get('p2p',0)} USGS:{src_count.get('usgs',0)}<br>
  計{len(quakes)}件 | {updated_str}</small>
</div>
<script>
var map=L.map('map',{{center:[36,138],zoom:5,preferCanvas:true}});
{DARK_TILE}
{GEOJSON_JS}
var CELLS={cells_js};
var gs={gs};
CELLS.forEach(function(c){{
  L.rectangle([[c.lat,c.lon],[c.lat+gs,c.lon+gs]],
    {{color:null,weight:0,fill:true,fillColor:c.color,fillOpacity:0.65}})
   .bindTooltip('Level '+c.lv+' rate='+c.score).addTo(map);
}});
</script></body></html>"""


# ══════════════════════════════════════════════════════
# メインページ（タブシェル）
# ══════════════════════════════════════════════════════
SHELL_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>地震・気象統合情報システム</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    html,body{height:100%;overflow:hidden;background:#0f172a;font-family:"Helvetica Neue",Arial,sans-serif}
    #sidebar{
      position:fixed;top:0;left:0;width:180px;height:100%;
      background:#111827;border-right:2px solid #1f2937;
      display:flex;flex-direction:column;padding-top:10px;z-index:100;
    }
    .app-title{
      padding:10px 14px 14px;font-size:13px;font-weight:700;
      color:#60a5fa;letter-spacing:0.5px;border-bottom:1px solid #1f2937;margin-bottom:6px;
    }
    .group-title{
      padding:8px 14px 4px;font-size:10px;font-weight:700;
      color:#4b5563;text-transform:uppercase;letter-spacing:1px;
    }
    .tab-btn{
      width:100%;text-align:left;padding:10px 14px;cursor:pointer;
      font-size:13px;font-weight:500;color:#9ca3af;
      border:none;background:none;transition:0.15s;
    }
    .tab-btn:hover{color:#f3f4f6;background:#1f2937}
    .tab-btn.active{
      color:#fff;background:linear-gradient(90deg,#1e3a5f,#1f2937);
      border-left:3px solid #3b82f6;
    }
    .version{margin-top:auto;padding:10px 14px;font-size:10px;color:#374151}
    #main{margin-left:180px;height:100vh;overflow:hidden}
    iframe{width:100%;height:100%;border:none;display:none}
    iframe.active{display:block}
  </style>
</head>
<body>
  <div id="sidebar">
    <div class="app-title">&#127981; 気象地震情報</div>
    <div class="group-title">地震</div>
    <button class="tab-btn active" onclick="sw(0)">有感地震履歴</button>
    <button class="tab-btn" onclick="sw(1)">無感地震履歴</button>
    <div class="group-title">気象</div>
    <button class="tab-btn" onclick="sw(2)">アメダス観測値</button>
    <button class="tab-btn" onclick="sw(3)">雨雲レーダー</button>
    <button class="tab-btn" onclick="sw(4)">警報・注意報</button>
    <div class="group-title">地震リスクマップ</div>
    <button class="tab-btn" onclick="sw(5)">ETASマップ</button>
    <div class="version">β4.2.0</div>
  </div>
  <div id="main">
    <iframe id="f0" class="active" src="/tab/felt"></iframe>
    <iframe id="f1" src=""></iframe>
    <iframe id="f2" src=""></iframe>
    <iframe id="f3" src=""></iframe>
    <iframe id="f4" src=""></iframe>
    <iframe id="f5" src=""></iframe>
  </div>
  <script>
    var URLS=['felt','unfelt','amedas','radar','warning','etas'];
    var loaded=[true,false,false,false,false,false];
    var cur=0;
    function sw(idx){
      document.querySelectorAll('.tab-btn').forEach(function(b,i){b.classList.toggle('active',i===idx)});
      document.querySelectorAll('iframe').forEach(function(f,i){f.classList.toggle('active',i===idx)});
      if(!loaded[idx]){
        document.getElementById('f'+idx).src='/tab/'+URLS[idx];
        loaded[idx]=true;
      }
      cur=idx;
    }
  </script>
</body>
</html>"""

LOADING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>起動中</title><meta http-equiv="refresh" content="5">
<style>body{background:#1a1a2e;color:white;display:flex;align-items:center;justify-content:center;
height:100vh;font-family:sans-serif;flex-direction:column;gap:16px}
.sp{width:48px;height:48px;border:5px solid #0f3460;border-top-color:#e94560;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div class="sp"></div><p>データを準備中...</p>
<p style="font-size:12px;color:#aaa">数秒〜数十秒でロードされます</p></body></html>"""


# ══════════════════════════════════════════════════════
# バックグラウンド更新（データのみキャッシュ）
# ══════════════════════════════════════════════════════
def _update_data():
    global _cached_data, _last_update, _ready_phase
    while True:
        try:
            print("[BG] 更新開始")
            updated_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_q = fetch_all_quakes()
            save_quakes(new_q)
            quakes = load_quakes()
            grid_scores = analyze_etas(quakes)
            jma  = [q for q in quakes if q.get("source")=="jma_bosai"]
            unft = [q for q in quakes if q.get("source") in ("p2p","usgs")]
            with _cache_lock:
                _cached_data = {"jma":jma,"unfelt":unft,"etas":grid_scores,
                                "all":quakes,"updated":updated_str}
                _last_update = time.time()
                _ready_phase = 2
            print(f"[BG] 完了 JMA:{len(jma)} 無感:{len(unft)} ETAS格子:{len(grid_scores)}")
        except Exception as e:
            import traceback; print(f"[BG] エラー: {e}"); traceback.print_exc()
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
    if data is None: return Response("<html><body style='background:#0f172a;color:white;padding:20px'>ロード中...</body></html>", mimetype="text/html")
    upd = data["updated"]
    if   name == "felt":    html = render_felt_quake(data["jma"], upd)
    elif name == "unfelt":  html = render_unfelt_quake(data["unfelt"], upd)
    elif name == "amedas":  html = render_amedas(upd)
    elif name == "radar":   html = render_radar(upd)
    elif name == "warning": html = render_warning(upd)
    elif name == "etas":    html = render_etas(data["etas"], data["all"], upd)
    else: return Response("Not found", status=404)
    return Response(html, mimetype="text/html")

@app.route("/status")
def status():
    with _cache_lock: return {"phase":_ready_phase,"last_update":_last_update}

if __name__ == "__main__":
    threading.Thread(target=_update_data, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
