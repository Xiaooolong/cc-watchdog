# Turn a square artwork into the app icon: rounded-square mask, then app.png (tray, UI) and app.ico (window, shortcuts).
# usage: python dev/make_icon.py SOURCE.png            -> writes ccwatchdog/app.png + app.ico
#        python dev/make_icon.py --preview OUT.png A.png B.png ...
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PKG = Path(__file__).resolve().parent.parent / "ccwatchdog"
SIZE = 1024


def rounded(src):
    img = Image.open(src).convert("RGBA")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    # draw the mask 4x larger for smooth corners; same proportions as macOS-style icons
    mask = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    m = 32 * 4
    ImageDraw.Draw(mask).rounded_rectangle([m, m, SIZE * 4 - m, SIZE * 4 - m], radius=232 * 4, fill=255)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask.resize((SIZE, SIZE), Image.Resampling.LANCZOS))
    return out


def preview(out, sources):
    sheet = Image.new("RGBA", (len(sources) * 300, 420), (240, 238, 230, 255))
    sheet.paste((32, 32, 32, 255), (0, 330, sheet.width, 420))
    for i, src in enumerate(sources):
        icon = rounded(src)
        sheet.alpha_composite(icon.resize((256, 256), Image.Resampling.LANCZOS), (22 + i * 300, 16))
        for j, s in enumerate((48, 32, 16)):
            small = icon.resize((s, s), Image.Resampling.LANCZOS)
            sheet.alpha_composite(small, (40 + i * 300 + j * 80, 300 - s // 2))
            sheet.alpha_composite(small, (40 + i * 300 + j * 80, 375 - s // 2))
    sheet.save(out)


if __name__ == "__main__":
    if sys.argv[1] == "--preview":
        preview(sys.argv[2], sys.argv[3:])
    else:
        icon = rounded(sys.argv[1])
        icon.resize((256, 256), Image.Resampling.LANCZOS).save(PKG / "app.png")
        icon.save(PKG / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok")
