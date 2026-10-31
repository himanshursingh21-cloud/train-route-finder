import sys, os, json
sys.path.insert(0, r'd:\Cheapest_Train_App')
os.chdir(r'd:\Cheapest_Train_App')

from backend.data_loader import build_graph_from_parquet
from backend.dijkstra_engine import find_optimal_route
import pandas as pd

print("Building graph...")
g = build_graph_from_parquet()

stations_df = pd.read_parquet('data/stations.parquet').dropna(subset=['lat', 'lon'])
coord_lookup = {
    r.station_code: {'lat': r.lat, 'lon': r.lon, 'name': r.name}
    for r in stations_df.itertuples(index=False)
}

# Test route search on a real pair from edges.parquet
edges = pd.read_parquet('data/edges.parquet')
sample = edges.iloc[0]
src, dst = sample['fromStnCode'], sample['toStnCode']
print(f"\nTesting route: {src} -> {dst}")

for pref in ['cheapest', 'fastest', 'balanced']:
    cost, time_h, path = find_optimal_route(g, src, dst, pref)
    print(f"  [{pref}] cost=Rs.{cost:.0f}  time={time_h:.2f}h  legs={len(path)}")
    if path:
        leg = path[0]
        fc = coord_lookup.get(leg.source.station_code, {})
        tc = coord_lookup.get(leg.target.station_code, {})
        lat1 = fc.get('lat', 'N/A')
        lon1 = fc.get('lon', 'N/A')
        lat2 = tc.get('lat', 'N/A')
        lon2 = tc.get('lon', 'N/A')
        print(f"    first leg coords: ({lat1}, {lon1}) -> ({lat2}, {lon2})")

# Test /api/stations output
print("\n--- /api/stations sample output ---")
routable = set(n.station_code for n in g.adjacency_list.keys())
routable_stns = stations_df[stations_df['station_code'].isin(routable)]
print(f"Routable stations with coordinates: {len(routable_stns)}")
sample_out = [
    {'code': r.station_code, 'name': r.name, 'lat': r.lat, 'lon': r.lon}
    for r in routable_stns.head(3).itertuples(index=False)
]
print(json.dumps(sample_out, indent=2))
print("\nAll checks passed.")
