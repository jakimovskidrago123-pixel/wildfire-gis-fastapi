# Wildfire GIS Analytics (NASA FIRMS → FastAPI → Leaflet)

This project implements a geospatial analytics pipeline for active wildfire detections from NASA FIRMS (MODIS & VIIRS).  
The backend uses **FastAPI** to ingest fire detections, perform **spatial computations** (convex hull, bounding box, FRP stats), and return JSON to a web UI.  
The frontend uses **Leaflet.js** for interactive global visualisation.

**Features**
- MODIS (1 km) + VIIRS (375 m) fire detections
- REST API endpoints: `/api/fires`, `/api/analyze`, `/api/by_country`, `/api/hull`
- Convex hull & bounding box area computations
- FRP (Fire Radiative Power) averaging & filtering
- Automatic projection EPSG:4326 → EPSG:6933 for km² area

**Tech stack**
FastAPI, GeoPandas, Shapely, Leaflet.js, Python, GitHub

**Use case**
Designed as a scalable backend for global wildfire risk mapping and situational awareness dashboards.
