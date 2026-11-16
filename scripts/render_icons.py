"""Render the home-screen icons (PNG) from logo.svg with Chromium. Run in the browser image:
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work btbridge-browser:dev python3 /work/scripts/render_icons.py
"""
from playwright.sync_api import sync_playwright

STATIC = "/work/bridge/btbridge/webui/static"
ICONS = ((180, "apple-touch-icon.png", 0.10), (192, "icon-192.png", 0.0), (512, "icon-512.png", 0.0),
         (512, "icon-maskable-512.png", 0.18))          # maskable: content inside the 80 % safe zone

svg = open(f"{STATIC}/logo.svg").read()
with sync_playwright() as p:
    b = p.chromium.launch()
    for size, name, pad in ICONS:
        pg = b.new_page(viewport={"width": size, "height": size})
        inner = int(size * (1 - 2 * pad))
        sized = svg.replace("<svg ", "<svg width='%d' height='%d' " % (inner, inner), 1)
        pg.set_content(f'<html><body style="margin:0;background:#17171b;display:grid;place-items:center;width:{size}px;height:{size}px">'
                       f'<div style="width:{inner}px;height:{inner}px">{sized}</div></body></html>')
        pg.screenshot(path=f"{STATIC}/{name}", omit_background=False)
        pg.close()
    b.close()
print("ok")
