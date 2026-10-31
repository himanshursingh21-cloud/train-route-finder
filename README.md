# 🚂 TrainRoute Finder — Cheapest Train Route Finder

Find the cheapest, fastest, and most balanced train routes across India.  
TrainRoute Finder is a full-stack route-planning application that models the Indian Railways network as a **time-expanded graph** and computes optimal journeys using a custom **$O(n \log n)$ Dijkstra's algorithm**. It supports multi-leg itineraries with transfers, per-class fare selection (SL / 3A / 2A / 1A / CC / 2S), and an interactive Leaflet.js map visualization.

## ✨ Features

- **Multi-criteria routing**: Choose *Cheapest*, *Fastest*, or *Balanced* (fare + ₹150/hr time valuation) preferences.
- **Per-class fares**: Select from Sleeper (SL), AC 3-Tier (3A), AC 2-Tier (2A), AC First Class (1A), Chair Car (CC), or Second Sitting (2S).
- **Time-expanded graph**: Models trains and transfers as time-state nodes so layovers and connections are handled correctly (minimum 20 min, maximum 6 hr buffers).
- **Interactive map**: Leaflet.js renders each journey leg — solid blue lines for train travel, dashed gray lines for transfers — with auto-fit bounds.
- **REST API**: Flask backend exposing `/api/route` and `/api/stations` endpoints with CORS enabled.
- **Station autocomplete**: 818+ routable stations with coordinates, populated via datalist.

## 🧠 How It Works

### Time-Expanded Graph
The backend loads `data/edges.parquet` (100k+ fare/travel-time edges) and `data/stations.parquet` (8.7k stations) into a `RouteGraph`. Each train stop becomes a time-state node `(station, time, train_id)`. Two edge types connect them:

- **`IN_TRAIN`** — travel on a train between two stops, carrying fare and duration.
- **`TRANSFER`** — a layover at the same station between two different trains (cost = ₹0, time = wait duration), only added when the layover falls within `[20 min, 6 hr]`.

### Routing Engine
`dijkstra_engine.py` runs a multi-criteria Dijkstra over the time-expanded graph:

| Preference | Edge Weight |
|------------|-------------|
| `cheapest` | `cost` (fare) |
| `fastest` | `time_taken` (hours) |
| `balanced` | `cost + (time_taken × ₹150)` |

When a travel class is selected, only `IN_TRAIN` edges matching that `classCode` are considered; transfer edges are always eligible. The algorithm returns total cost, total time, and the ordered list of route legs with per-leg coordinates.

Complexity: **$O((V + E) \log V)$** using a binary-heap priority queue.

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Flask 3, Flask-CORS |
| Data | Pandas, PyArrow (Parquet) |
| Algorithm | Custom Dijkstra (Python stdlib `heapq`) |
| Frontend | HTML5, CSS3, vanilla JavaScript |
| Map | Leaflet.js + OpenStreetMap tiles |
| Data source | Indian Railways schedules, station codes, and fare tables |

## 📁 Project Structure

```
Cheapest_Train_App/
├── backend/
│   ├── app.py              # Flask REST API (/api/route, /api/stations)
│   ├── data_loader.py      # Builds time-expanded RouteGraph from Parquet
│   ├── dijkstra_engine.py  # Multi-criteria Dijkstra router
│   ├── graph_models.py     # StateNode, Edge, RouteGraph data models
│   ├── mock_data.py        # Test fixture + smoke test
│   └── __init__.py
├── frontend/
│   ├── index.html          # Search form + results + map container
│   ├── script.js           # API calls, autocomplete, Leaflet map drawing
│   └── style.css           # Responsive, mobile-first styling
├── data/
│   ├── edges.parquet       # Fare + travel-time edges per class
│   ├── stations.parquet    # Station metadata + coordinates
│   ├── schedules.csv       # Raw train schedules
│   ├── stations.csv        # Raw station coordinates
│   ├── price_data.csv      # Raw fare data
│   └── trains.csv          # Raw train metadata
├── prepare_data.py         # ETL: CSV → Parquet (schedule, stations, edges)
├── test_backend.py         # End-to-end backend smoke test
├── check_js.py             # Frontend JS sanity checks
├── requirements.txt        # Python dependencies
└── README.md
```

## 🚀 Local Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd Cheapest_Train_App
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# or
source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

### 3. Generate the Parquet data files

The backend reads pre-processed Parquet files from `data/`. Generate them from the raw CSVs:

```bash
python prepare_data.py
```

This writes `data/schedule_flat.parquet`, `data/stations.parquet`, and `data/edges.parquet`.

### 4. Run the backend API

```bash
python -m backend.app
```

The Flask server starts on `http://localhost:5000`. You should see:

```
Loading graph from parquet files … (this may take a few seconds)
  Graph ready — 4,419 source nodes, 100,449 IN_TRAIN, 3,666 TRANSFER edges
  Stations loaded — 818 routable stations with coordinates
```

### 5. Open the frontend

Open `frontend/index.html` directly in your browser, or serve it with any static file server:

```bash
# Example using Python's built-in server
python -m http.server 8000 --directory frontend
# Then visit http://localhost:8000
```

Ensure the Flask backend is running on port 5000 before searching.

### 6. Run the backend smoke test

```bash
python test_backend.py
```

## 🔌 API Reference

### `GET /api/route`
Find the optimal train route between two stations.

**Query parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | string | Yes | — | Departure station code (e.g. `NDLS`) |
| `destination` | string | Yes | — | Arrival station code (e.g. `BCT`) |
| `preference` | string | No | `cheapest` | `cheapest`, `fastest`, or `balanced` |
| `travel_class` | string | No | `SL` | `SL`, `3A`, `2A`, `1A`, `CC`, `2S` |

**Response (200)**

```json
{
  "total_cost": 145.0,
  "total_time": 0.55,
  "route_legs": [
    {
      "from_station": "JBP",
      "to_station": "SRID",
      "depart_time": 12.0,
      "arrive_time": 12.55,
      "cost": 145.0,
      "edge_type": "IN_TRAIN",
      "from_lat": 23.16,
      "from_lon": 79.95,
      "to_lat": 23.03,
      "to_lon": 79.48
    }
  ]
}
```

### `GET /api/stations`
Return all routable stations with coordinates.

**Response**

```json
[
  { "code": "NDLS", "name": "New Delhi", "lat": 28.64, "lon": 77.22 },
  ...
]
```

## ⚠️ Known Limitations

- **Fixed running days**: Schedule data reflects a static snapshot; train-specific running days (e.g. "runs Mon/Wed/Fri only") are not accounted for.
- **Static fare snapshot**: Fares are from a fixed historical price table and do not reflect dynamic pricing, tatkal charges, or real-time availability.
- **Maximum layover threshold**: Connections with waits longer than 6 hours are pruned, which may exclude valid overnight itineraries.
- **Single route per preference**: The API returns one optimal route per preference; alternative near-optimal options are not enumerated.
- **No date selection**: The date picker in the UI is cosmetic — the underlying dataset is time-invariant.

## 📄 License

MIT License. Data is indicative — verify fares and schedules on the official IRCTC portal before travel.
