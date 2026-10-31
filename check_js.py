import re
src = open(r'd:\Cheapest_Train_App\frontend\script.js', encoding='utf-8').read()
checks = [
    ('mapPolyline old singular',    r'\bmapPolyline\b(?!s)',       'stale',   0),
    ('populateStationsList',         r'populateStationsList',       'stale',   0),
    ('MOCK_ROUTES',                  r'MOCK_ROUTES',                'stale',   0),
    ('showRouteOnMap',               r'showRouteOnMap',             'stale',   0),
    ('highlightRouteOnMap',          r'highlightRouteOnMap',        'stale',   0),
    ('drawRouteLegs defined',        r'function drawRouteLegs',     'present', 1),
    ('drawRouteLegs called',         r'drawRouteLegs[(]',           'present', 1),
    ('loadStations defined',         r'async function loadStations','present', 1),
    ('parseStationCode defined',     r'function parseStationCode',  'present', 1),
    ('stationIndex declared',        r'const stationIndex',         'present', 1),
    ('mapPolylines plural',          r'mapPolylines',               'present', 1),
    ('India center setView',         r'20[.]59',                    'present', 1),
    ('IN_TRAIN solid blue',          r'1a73e8',                     'present', 1),
    ('TRANSFER dashed gray',         r'9ca3af',                     'present', 1),
    ('fitBounds present',            r'fitBounds',                  'present', 1),
    ('makeTerminusIcon defined',     r'makeTerminusIcon',           'present', 1),
]
ok = True
for label, pattern, kind, min_count in checks:
    hits = len(re.findall(pattern, src))
    if kind == 'stale':
        status = 'OK   ' if hits == 0 else 'STALE'
        if hits:
            ok = False
    else:
        status = 'OK   ' if hits >= min_count else 'MISS '
        if hits < min_count:
            ok = False
    print(f'  {status}  {label}  ({hits})')

print()
print(f'  Total lines: {len(src.splitlines())}')
print()
print('All checks passed.' if ok else 'ISSUES FOUND.')
