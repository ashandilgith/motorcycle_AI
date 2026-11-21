import math

from app.curves.alert import AlertLevel, RideConditions, evaluate, required_distance_m
from app.curves.geometry import circumradius, distance_along, find_curves, safe_speed_kmh
from app.integrations.google_maps import decode_polyline

from .helpers import ORIGIN, offset, road_with_curve


def test_circumradius_of_known_circle():
    r = 50.0
    pts = [(r * math.cos(a), r * math.sin(a)) for a in (0.0, 0.3, 0.6)]
    assert abs(circumradius(*pts) - r) < 1e-6


def test_straight_line_has_infinite_radius():
    assert math.isinf(circumradius((0, 0), (1, 1), (2, 2)))


def test_safe_speed_formula():
    # 40 m curve, 2.5 m/s^2 -> 10 m/s = 36 km/h
    assert abs(safe_speed_kmh(40, 2.5) - 36.0) < 0.01
    assert safe_speed_kmh(40, 1.5) < safe_speed_kmh(40, 2.5)


def test_finds_single_curve_with_right_radius():
    _, _, curves = find_curves(road_with_curve(radius_m=40))
    assert len(curves) == 1
    c = curves[0]
    assert 30 < c.min_radius_m < 50
    assert 280 < c.start_m < 320  # curve starts after the 300 m straight
    assert c.safe_kmh_wet < c.safe_kmh_dry


def test_straight_road_has_no_curves():
    pts = [offset(ORIGIN, 0, y) for y in range(0, 1000, 10)]
    assert find_curves(pts)[2] == []


def test_distance_along_route():
    pts, cum, _ = find_curves(road_with_curve())
    pos_m, off = distance_along(pts, cum, offset(ORIGIN, 3, 100))
    assert abs(pos_m - 100) < 2
    assert abs(off - 3) < 0.5


def test_alert_levels():
    _, _, curves = find_curves(road_with_curve(radius_m=40))
    start = curves[0].start_m
    assert evaluate(start - 200, 30, curves).level == AlertLevel.NONE        # already slow enough
    assert evaluate(start - 390, 70, curves).level == AlertLevel.NONE        # still far away
    assert evaluate(start - 80, 70, curves).level == AlertLevel.CAUTION
    assert evaluate(start - 30, 70, curves).level == AlertLevel.URGENT
    urgent = evaluate(start - 30, 70, curves, RideConditions(wet=True))
    assert "basa" in urgent.message_tl


def test_wet_road_warns_earlier():
    _, _, curves = find_curves(road_with_curve(radius_m=40))
    d = curves[0].start_m - 95  # dry needs ~87 m to caution, wet ~98 m
    assert evaluate(d, 60, curves).level == AlertLevel.NONE
    assert evaluate(d, 60, curves, RideConditions(wet=True)).level != AlertLevel.NONE


def test_required_distance():
    assert required_distance_m(30, 40) == 0
    assert required_distance_m(70, 36) > 40


def test_decode_polyline_google_example():
    # Example from Google's polyline documentation
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
