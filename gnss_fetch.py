#!/usr/bin/env python3
"""GEONET日々の座標値(SFTP)を取得し、変位ベクトルを gnss_vectors.json に書き出す。
PC / GitHub Actions など「GSIに繋がる環境」で実行する。
必要: pip install paramiko / 環境変数 GSI_SFTP_USER, GSI_SFTP_PASS
任意: GNSS_DIRS(カンマ区切り, 既定 R5.1,F5.1の順) GNSS_LOOKBACK_DAYS(既定30) GNSS_OUT GNSS_STEP(間引き, 既定1)
"""
import os, re, io, gzip, json, math, sys
from datetime import datetime, timedelta, timezone
import paramiko

HOST = os.environ.get("GSI_SFTP_HOST", "terras.gsi.go.jp")
USER = os.environ["GSI_SFTP_USER"]; PASS = os.environ["GSI_SFTP_PASS"]
DIRS = [d.strip() for d in os.environ.get("GNSS_DIRS", "/data/coordinates_R5.1,/data/coordinates_F5.1").split(",") if d.strip()]
LOOKBACK = int(os.environ.get("GNSS_LOOKBACK_DAYS", "30"))
OUT = os.environ.get("GNSS_OUT", "gnss_vectors.json")
STEP = max(1, int(os.environ.get("GNSS_STEP", "1")))
LAT_RANGE, LON_RANGE = (24.0, 46.0), (122.0, 146.5)   # ETAS対象域の概略

def parse(text):
    rows = []; lat = lon = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "*+-#": continue
        p = line.split()
        if len(p) < 9: continue
        try:
            d = datetime(int(p[0]), int(p[1]), int(p[2]))
            x, y, z = float(p[4]), float(p[5]), float(p[6]); lat, lon = float(p[7]), float(p[8])
        except ValueError: continue
        if all(1e5 < abs(v) < 1e8 for v in (x, y, z)): rows.append((d, x, y, z))
    return rows, lat, lon

def displacement(rows):
    if len(rows) < 3: return None
    cut = rows[-1][0] - timedelta(days=LOOKBACK)
    rec = [r for r in rows if r[0] >= cut]
    if len(rec) < 3: return None
    _, x0, y0, z0 = rec[0]
    lat0 = math.atan2(z0, math.hypot(x0, y0))   # 近似で十分（ENU回転用）
    lon0 = math.atan2(y0, x0)
    sl, cl, so, co = math.sin(lat0), math.cos(lat0), math.sin(lon0), math.cos(lon0)
    t, E, N, U = [], [], [], []
    for d, x, y, z in rec:
        dx, dy, dz = x-x0, y-y0, z-z0
        t.append((d-rec[0][0]).days)
        E.append(-so*dx+co*dy); N.append(-sl*co*dx-sl*so*dy+cl*dz); U.append(cl*co*dx+cl*so*dy+sl*dz)
    span = t[-1]-t[0]
    if span <= 0: return None
    def slope(ys):
        n=len(t); mx=sum(t)/n; my=sum(ys)/n
        den=sum((a-mx)**2 for a in t)
        return sum((a-mx)*(b-my) for a,b in zip(t,ys))/den if den else 0.0
    return {"dE_mm": slope(E)*span*1000, "dN_mm": slope(N)*span*1000, "dU_mm": slope(U)*span*1000,
            "n_points": len(rec), "span_days": span, "last_date": rec[-1][0].strftime("%Y-%m-%d")}

def read(sftp, path):
    try:
        with sftp.open(path, "rb") as f: raw = f.read()
        return gzip.decompress(raw).decode("utf-8", "ignore")
    except FileNotFoundError: return None
    except Exception as e:
        print("  err", path, e); return None

def main():
    t = paramiko.Transport((HOST, 22)); t.banner_timeout = 30
    t.connect(username=USER, password=PASS)
    sftp = paramiko.SFTPClient.from_transport(t); sftp.get_channel().settimeout(60)
    year = datetime.now(timezone.utc).year
    ids = {}
    for base in DIRS:
        try:
            for n in sftp.listdir(f"{base}/{year}"):
                m = re.match(r"^(\w{6})\.\d\d\.pos\.gz$", n)
                if m: ids.setdefault(m.group(1), None)
            print(base, "OK")
        except Exception as e:
            print(base, "listdir失敗:", e)
    codes = sorted(ids)[::STEP]
    print("観測点数:", len(codes))
    results = []
    for i, code in enumerate(codes, 1):
        best = None
        for base in DIRS:
            txt = None; rows = []
            for y in (year, year-1):
                txt = read(sftp, f"{base}/{y}/{code}.{str(y)[2:]}.pos.gz")
                if txt:
                    r, la, lo = parse(txt)
                    rows = r + rows if y != year else rows + r
                    if y == year: last_ll = (la, lo)
                if len(rows) >= LOOKBACK + 5: break
            rows = sorted(dict((d, (d, x, y_, z)) for d, x, y_, z in rows).values())
            if rows and (best is None or rows[-1][0] > best[0][-1][0]):
                best = (rows, base, last_ll)
        if not best: continue
        rows, base, (la, lo) = best
        if la is None or not (LAT_RANGE[0] <= la <= LAT_RANGE[1] and LON_RANGE[0] <= lo <= LON_RANGE[1]): continue
        d = displacement(rows)
        if d: results.append({"code": code, "name": code, "lat": la, "lon": lo, "src": base.rsplit("_",1)[-1], **d})
        if i % 100 == 0: print(i, "/", len(codes))
    sftp.close(); t.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "lookback_days": LOOKBACK, "stations": results}, f, ensure_ascii=False)
    print("書き出し:", OUT, len(results), "点")
    if not results: sys.exit(1)

if __name__ == "__main__": main()
