"""Helpers for converting a coordinate search into a FIRMS request area."""

from math import cos, radians


def coordinate_search_box(latitude: float, longitude: float, radius_km: float) -> str:
    """Convert a center point and radius into a FIRMS west,south,east,north box."""
    if radius_km >= 20015.0:
        return "-180.0000,-90.0000,180.0000,90.0000"

    latitude_delta = radius_km / 111.32
    longitude_scale = abs(cos(radians(latitude)))
    longitude_delta = (
        180.0
        if longitude_scale < 1e-6
        else min(180.0, radius_km / (111.32 * longitude_scale))
    )
    south = max(-90.0, latitude - latitude_delta)
    north = min(90.0, latitude + latitude_delta)
    if longitude_delta >= 180.0:
        west, east = -180.0, 180.0
    else:
        west = max(-180.0, longitude - longitude_delta)
        east = min(180.0, longitude + longitude_delta)
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
