"""color_extractor.py — Street View画像からドミナントカラーを抽出 (Pro)"""
from __future__ import annotations


def extract_dominant_color(image_bytes: bytes, n_clusters: int = 5) -> tuple[int, int, int]:
    try:
        from PIL import Image
        import numpy as np
        from sklearn.cluster import KMeans
        import io

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img).reshape(-1, 3).astype(float)
        km = KMeans(n_clusters=n_clusters, n_init=3, random_state=0).fit(arr)
        counts = np.bincount(km.labels_)
        dominant = km.cluster_centers_[counts.argmax()]
        return tuple(int(c) for c in dominant)
    except ImportError:
        return (128, 128, 128)
