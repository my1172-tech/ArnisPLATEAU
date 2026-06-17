"""streetview_fetcher.py — Google Street View Static API からパノラマ画像を取得 (Pro)"""
import urllib.request
import urllib.parse


SV_BASE = "https://maps.googleapis.com/maps/api/streetview"


def fetch_image(lat: float, lng: float, api_key: str, size: str = "640x640") -> bytes:
    params = urllib.parse.urlencode({
        "size": size, "location": f"{lat},{lng}",
        "fov": 90, "heading": 0, "pitch": 0, "key": api_key,
    })
    url = f"{SV_BASE}?{params}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read()
