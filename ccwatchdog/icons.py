# app.png / app.ico come from dev/icon-source.png via dev/make_icon.py
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).parent
PNG, ICO = HERE / "app.png", HERE / "app.ico"


def tray_icon(active=True):
    img = Image.open(PNG).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    if active:
        return img
    gray = ImageOps.grayscale(img).convert("RGBA")
    gray.putalpha(img.getchannel("A"))
    return gray
