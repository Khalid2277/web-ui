"""Small-area geodesy for farm fields: local metric frame, areas, point-in-polygon.

Fields are a few hectares, so an equirectangular projection around the field centroid is
accurate to well under 0.1 % (far below GPS error). Coordinates are (lat, lon) in degrees.
"""
import math

R_EARTH = 6371008.8  # mean Earth radius, m


class LocalFrame:
    """Equirectangular projection centred on (lat0, lon0): x east, y north, metres."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.cos(math.radians(lat0)) * math.pi / 180 * R_EARTH
        self.ky = math.pi / 180 * R_EARTH

    def to_xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def to_latlon(self, x, y):
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx


def centroid(points):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def frame_for(points):
    return LocalFrame(*centroid(points))


def shoelace(xy):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2


def area_m2(points):
    f = frame_for(points)
    return abs(shoelace([f.to_xy(*p) for p in points]))


def point_in_polygon(x, y, xy):
    inside = False
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def distance_m(a, b):
    """Haversine distance between (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def _segments_intersect(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def validate_polygon(points):
    """Return an error message, or None if the polygon is a usable field boundary."""
    if not isinstance(points, list) or len(points) < 3:
        return "A field needs at least 3 corner points."
    try:
        pts = [(float(p[0]), float(p[1])) for p in points]
    except (TypeError, ValueError, IndexError):
        return "Corner points must be [latitude, longitude] numbers."
    if any(not (-90 <= la <= 90 and -180 <= lo <= 180) for la, lo in pts):
        return "A corner point is outside valid latitude/longitude ranges."
    if len(pts) > 200:
        return "Too many corner points (maximum 200)."
    f = frame_for(pts)
    xy = [f.to_xy(*p) for p in pts]
    n = len(xy)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(i - j) in (1, n - 1):
                continue
            if _segments_intersect(xy[i], xy[(i + 1) % n], xy[j], xy[(j + 1) % n]):
                return "The boundary crosses itself. Redraw it without crossing lines."
    a = abs(shoelace(xy))
    if a < 25:
        return "The field is smaller than 25 m², which is too small to survey."
    if a > 5_000_000:
        return "The field is larger than 500 ha, which is beyond this drone's range."
    return None
