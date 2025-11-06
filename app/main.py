import os
import io
import requests
import pandas as pd
from typing import Dict, Optional
from functools import lru_cache
from datetime import datetime

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from flask import Flask, render_template

# Geo stack
import geopandas as gpd
from shapely.geometry import Point, box, mapping

# ----------- Config -----------
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
DEFAULT_SOURCE = "VIIRS_NOAA20_NRT"  # valid default dataset
EQUAL_AREA_EPSG = 6933  # World Cylindrical Equal Area (meters)

# ----------- Helper: Load Natural Earth Countries -----------
def load_countries() -> gpd.GeoDataFrame:
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    local_zip = os.path.join(data_dir, "ne_110m_admin_0_countries.zip")

    if not os.path.exists(local_zip):
        urls = [
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip",
            "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip",
        ]
        ok = False
        for url in urls:
            try:
                r = requests.get(url, timeout=60)
                if r.ok:
                    with open(local_zip, "wb") as f:
                        f.write(r.content)
                    ok = True
                    break
            except Exception:
                pass
        if not ok:
            raise HTTPException(status_code=500, detail="Could not download Natural Earth countries.")

    gdf = gpd.read_file(f"zip://{local_zip}").to_crs("EPSG:4326")

    # Standardize names
    name_cands = ["name", "NAME", "ADMIN", "NAME_EN", "SOVEREIGNT"]
    iso_cands = ["iso_a3", "ISO_A3", "ADM0_A3"]

    if "name" not in gdf.columns:
        for c in name_cands:
            if c in gdf.columns:
                gdf = gdf.rename(columns={c: "name"})
                break
    if "iso_a3" not in gdf.columns:
        for c in iso_cands:
            if c in gdf.columns:
                gdf = gdf.rename(columns={c: "iso_a3"})
                break

    if "name" not in gdf.columns:
        gdf["name"] = "(unknown)"
    if "iso_a3" not in gdf.columns:
        gdf["iso_a3"] = "NA"

    return gdf[["name", "iso_a3", "geometry"]]

# ----------- FastAPI Setup -----------
api = FastAPI(
    title="Wildland Fire Map API",
    version="0.3.1",
    description="FIRMS active fire points and geospatial analytics."
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_map_key() -> str:
    key = os.getenv("FIRMS_MAP_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="FIRMS_MAP_KEY env var is not set.")
    return key

def normalize_source(source: str) -> str:
    """Normalize legacy dataset names to valid FIRMS datasets."""
    source_map = {
        "viirs": "VIIRS_NOAA20_NRT",
        "viirs_noaa20": "VIIRS_NOAA20_NRT",
        "viirs_snpp": "VIIRS_SNPP_NRT",
        "modis": "MODIS_C6_1",
    }
    return source_map.get(source.lower(), source)

def fetch_firms_df(bbox: str, days: int, source: str) -> pd.DataFrame:
    """Fetches FIRMS CSV into a DataFrame."""
    url = f"{FIRMS_BASE}/{get_map_key()}/{source}/{bbox.replace(' ', '')}/{days}"
    print(f"\n[DEBUG] Requesting FIRMS data:\n{url}\n")

    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"FIRMS error {r.status_code}: {r.text[:200]}")
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch/parse FIRMS data: {e}")

    print(f"[DEBUG] Loaded {len(df)} hotspots for bbox={bbox}, days={days}, source={source}")
    return df

def normalize_confidence_counts(df: pd.DataFrame) -> Dict[str, int]:
    """Return counts for low/nominal/high confidence."""
    if "confidence" not in df.columns:
        return {"low": 0, "nominal": 0, "high": 0}
    s = df["confidence"]
    if s.dtype == object:
        l = (s.str.lower() == "l").sum()
        n = (s.str.lower() == "n").sum()
        h = (s.str.lower() == "h").sum()
        return {"low": int(l), "nominal": int(n), "high": int(h)}
    else:
        vals = pd.to_numeric(s, errors="coerce")
        low = (vals <= 40).sum()
        nominal = ((vals > 40) & (vals <= 70)).sum()
        high = (vals > 70).sum()
        return {"low": int(low), "nominal": int(nominal), "high": int(high)}

# ----------- Weather helpers (Open-Meteo) -----------
def _parse_hour(hhmm: str) -> str:
    """'0345' -> '03:00'; '17' -> '17:00'."""
    s = (hhmm or "").strip()
    if len(s) < 2 or not s.isdigit():
        return "00:00"
    if len(s) <= 2:
        hh = int(s)
    else:
        hh = int(s[:-2])
    hh = max(0, min(23, hh))
    return f"{hh:02d}:00"

def _nearest_hour_index(target_iso: str, time_arr: list[str]) -> Optional[int]:
    try:
        target = datetime.fromisoformat(target_iso)
    except Exception:
        return None
    best_i, best_diff = None, None
    for i, t in enumerate(time_arr or []):
        try:
            dt = datetime.fromisoformat(t)
        except Exception:
            continue
        diff = abs((dt - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

@lru_cache(maxsize=10000)
def _cached_weather(lat_key: float, lon_key: float, date: str, hour: str):
    """Fetch one-day hourly temperature & humidity and return the exact or nearest hour."""
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat_key}&longitude={lon_key}"
        f"&start_date={date}&end_date={date}"
        "&hourly=temperature_2m,relative_humidity_2m"
        "&timezone=UTC"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    j = r.json()
    hourly = (j or {}).get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    hums  = hourly.get("relative_humidity_2m") or []

    target_iso = f"{date}T{hour}"

    try:
        idx = times.index(target_iso)
    except ValueError:
        idx = _nearest_hour_index(target_iso, times)

    if idx is None:
        return None

    t_val = temps[idx] if idx < len(temps) else None
    h_val = hums[idx]  if idx < len(hums)  else None

    if t_val is None or h_val is None:
        for j_idx in (idx - 1, idx + 1):
            if 0 <= j_idx < len(times):
                t2 = temps[j_idx] if j_idx < len(temps) else None
                h2 = hums[j_idx]  if j_idx < len(hums)  else None
                if t2 is not None and h2 is not None:
                    idx, t_val, h_val = j_idx, t2, h2
                    break

    if t_val is None or h_val is None:
        return None

    return {
        "time": times[idx],
        "temperature_2m": float(t_val),
        "relative_humidity_2m": float(h_val),
        "units": {"temperature_2m": "°C", "relative_humidity_2m": "%"},
        "note": "Nearest hour returned." if times[idx] != target_iso else "Exact hour returned."
    }

# ----------- API Endpoints -----------

# /api root → overview
@api.get("/", summary="API Overview")
def api_overview():
    return {
        "name": "Wildland Fire Map API",
        "version": "0.3.1",
        "description": "Provides FIRMS wildfire hotspots, geospatial analytics, and visualization.",
        "available_endpoints": {
            "/api/health": "Check API health status",
            "/api/version": "Get API version and description",
            "/api/fires": "Fetch FIRMS wildfire hotspots as GeoJSON",
            "/api/analyze": "Perform analytics on wildfire hotspots",
            "/api/by_country": "Get hotspots count grouped by country",
            "/api/hull": "Convex hull polygon for FIRMS points",
            "/api/weather_at": "Historical temp & humidity for a point/time (Open-Meteo)"
        }
    }

@api.get("/health", summary="Health check")
def health_check():
    return {"status": "ok"}

@api.get("/version", summary="API version")
def version():
    return {
        "app": "Wildland Fire Map API",
        "version": "0.3.1",
        "description": "FIRMS active fire points and geospatial analytics",
    }

@api.get("/fires", summary="FIRMS fires as GeoJSON")
def fires(
    bbox: str = Query("-180,-90,180,90"),
    days: int = Query(1, ge=1, le=10),
    source: str = Query(DEFAULT_SOURCE),
    min_conf: str = Query("n"),
):
    source = normalize_source(source)
    df = fetch_firms_df(bbox, days, source)
    if df.empty:
        return {"type": "FeatureCollection", "features": []}

    # Confidence filter (for VIIRS letter codes)
    if "confidence" in df.columns and df["confidence"].dtype == object:
        levels = {"l": 0, "n": 1, "h": 2}
        min_level = levels.get(min_conf.lower(), 1)
        df = df[df["confidence"].map(lambda c: levels.get(str(c).lower(), -1)) >= min_level]

    features = []
    for _, row in df.iterrows():
        try:
            lon = float(row["longitude"])
            lat = float(row["latitude"])
        except Exception:
            continue
        props = row.to_dict()
        props.pop("longitude", None)
        props.pop("latitude", None)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props
        })
    return JSONResponse({"type": "FeatureCollection", "features": features})

@api.get("/analyze", summary="Aggregate analytics for FIRMS points")
def analyze(
    bbox: str = Query("-180,-90,180,90"),
    days: int = Query(2, ge=1, le=10),
    source: str = Query(DEFAULT_SOURCE),
    min_conf: str = Query("n"),
):
    source = normalize_source(source)
    df = fetch_firms_df(bbox, days, source)
    if df.empty:
        return {
            "count": 0,
            "by_date": [],
            "confidence": {"low": 0, "nominal": 0, "high": 0},
            "hull_area_km2": 0.0,
            "bbox_area_km2": 0.0,
            "mean_frp": None,
        }

    if "confidence" in df.columns and df["confidence"].dtype == object:
        levels = {"l": 0, "n": 1, "h": 2}
        min_level = levels.get(min_conf.lower(), 1)
        df = df[df["confidence"].map(lambda c: levels.get(str(c).lower(), -1)) >= min_level]

    df = df.dropna(subset=["longitude", "latitude"])
    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=[Point(xy) for xy in zip(df["longitude"].astype(float), df["latitude"].astype(float))],
        crs="EPSG:4326"
    )

    west, south, east, north = [float(v) for v in bbox.split(",")]
    bbox_poly = gpd.GeoSeries([box(west, south, east, north)], crs="EPSG:4326")
    bbox_area_km2 = float(bbox_poly.to_crs(EQUAL_AREA_EPSG).area.iloc[0] / 1e6)

    # Convex hull area
    if len(gdf) >= 3:
        hull = gdf.to_crs(EQUAL_AREA_EPSG).unary_union.convex_hull
        hull_area_km2 = float(hull.area / 1e6)
    else:
        hull_area_km2 = 0.0

    # Time series by date
    by_date_list = []
    if "acq_date" in df.columns:
        by_date = (
            df["acq_date"].astype(str).value_counts().sort_index()
            .rename_axis("date").reset_index(name="count")
        )
        by_date_list = by_date.to_dict(orient="records")

    confidence = normalize_confidence_counts(df)

    # Mean FRP (or brightness proxy)
    mean_frp = None
    for col in ["frp", "bright_ti4", "brightness"]:
        if col in df.columns:
            mean_frp = float(pd.to_numeric(df[col], errors="coerce").dropna().mean())
            break

    return {
        "count": int(len(df)),
        "by_date": by_date_list,
        "confidence": confidence,
        "hull_area_km2": round(hull_area_km2, 3),
        "bbox_area_km2": round(bbox_area_km2, 3),
        "mean_frp": None if mean_frp is None else round(mean_frp, 3),
    }

@api.get("/by_country", summary="Counts per country for FIRMS points")
def by_country(
    bbox: str = Query("-180,-90,180,90"),
    days: int = Query(2, ge=1, le=10),
    source: str = Query(DEFAULT_SOURCE),
    min_conf: str = Query("n"),
    top: int = Query(15, ge=1, le=50),
):
    source = normalize_source(source)
    df = fetch_firms_df(bbox, days, source)
    if df.empty:
        return {"items": [], "total": 0}

    if "confidence" in df.columns and df["confidence"].dtype == object:
        levels = {"l": 0, "n": 1, "h": 2}
        min_level = levels.get(min_conf.lower(), 1)
        df = df[df["confidence"].map(lambda c: levels.get(str(c).lower(), -1)) >= min_level]

    df = df.dropna(subset=["longitude", "latitude"])
    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["longitude"].astype(float), df["latitude"].astype(float)),
        crs="EPSG:4326"
    )

    west, south, east, north = [float(v) for v in bbox.split(",")]
    bbox_geom = box(west, south, east, north)
    countries = load_countries()
    countries_clip = countries[countries.intersects(bbox_geom)]

    if countries_clip.empty or gdf.empty:
        return {"items": [], "total": int(len(gdf))}

    joined = gpd.sjoin(
        gdf,
        countries_clip[["name", "iso_a3", "geometry"]],
        how="left",
        predicate="within"
    )

    value_col = None
    for col in ["frp", "bright_ti4", "brightness"]:
        if col in joined.columns:
            value_col = col
            break

    if value_col:
        agg = joined.groupby(["name", "iso_a3"], dropna=True).agg(
            count=("geometry", "size"),
            mean_value=(value_col, "mean")
        ).reset_index()
    else:
        agg = joined.groupby(["name", "iso_a3"], dropna=True).agg(
            count=("geometry", "size")
        ).reset_index()
        agg["mean_value"] = None

    agg = agg.sort_values("count", ascending=False).head(top)

    items = [
        {
            "country": str(r["name"]),
            "iso_a3": str(r["iso_a3"]),
            "count": int(r["count"]),
            "mean_frp": (None if pd.isna(r["mean_value"]) else round(float(r["mean_value"]), 3))
        }
        for _, r in agg.iterrows()
    ]
    return {"items": items, "total": int(len(gdf))}

@api.get("/hull", summary="Convex hull polygon for FIRMS points (GeoJSON)")
def hull(
    bbox: str = Query("-180,-90,180,90"),
    days: int = Query(2, ge=1, le=10),
    source: str = Query(DEFAULT_SOURCE),
    min_conf: str = Query("n"),
):
    source = normalize_source(source)
    df = fetch_firms_df(bbox, days, source)
    if df.empty:
        return {"type": "FeatureCollection", "features": []}

    if "confidence" in df.columns and df["confidence"].dtype == object:
        levels = {"l": 0, "n": 1, "h": 2}
        min_level = levels.get(min_conf.lower(), 1)
        df = df[df["confidence"].map(lambda c: levels.get(str(c).lower(), -1)) >= min_level]

    df = df.dropna(subset=["longitude", "latitude"])
    if len(df) < 3:
        return {"type": "FeatureCollection", "features": []}

    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["longitude"].astype(float), df["latitude"].astype(float)),
        crs="EPSG:4326"
    )

    gdf_eq = gdf.to_crs(EQUAL_AREA_EPSG)
    hull_eq = gdf_eq.unary_union.convex_hull
    area_km2 = float(hull_eq.area / 1e6)
    hull_wgs = gpd.GeoSeries([hull_eq], crs=EQUAL_AREA_EPSG).to_crs(4326).iloc[0]

    feature = {
        "type": "Feature",
        "geometry": mapping(hull_wgs),
        "properties": {"area_km2": round(area_km2, 3)}
    }
    return {"type": "FeatureCollection", "features": [feature]}

@api.get("/weather_at", summary="Historical weather (Open-Meteo) at given lat/lon/date/time (UTC)")
def weather_at(
    lat: float = Query(...),
    lon: float = Query(...),
    date: str = Query(..., description="YYYY-MM-DD"),
    time: str = Query(..., description="HHMM from FIRMS, e.g., 0345"),
):
    """
    Returns temperature_2m and relative_humidity_2m for the requested moment
    (rounded to nearest hour). All times are UTC. No API key required.
    """
    # validate date
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    hour = _parse_hour(time)  # -> 'HH:00'

    # small quantization for cache key
    lat_key = round(lat, 2)
    lon_key = round(lon, 2)

    try:
        out = _cached_weather(lat_key, lon_key, date, hour)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Open-Meteo error: {e}")

    if not out:
        raise HTTPException(status_code=404, detail="Weather not available for that moment.")
    return out

# ----------- Flask Frontend -----------
flask_app = Flask(__name__, template_folder="templates", static_folder="static")

@flask_app.route("/")
def index() -> HTMLResponse:
    return render_template("index.html")

# ----------- Root App Mounts -----------
root = FastAPI()
root.mount("/api", api)                  # everything under /api → FastAPI
root.mount("/", WSGIMiddleware(flask_app))  # homepage → Flask

app = root
