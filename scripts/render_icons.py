from playwright.sync_api import sync_playwright
svg = open("/work/bridge/btbridge/webui/static/logo.svg").read()
with sync_playwright() as p:
    b = p.chromium.launch()
    for size, name, pad in ((180, "apple-touch-icon.png", 0.10), (192, "icon-192.png", 0.0), (512, "icon-512.png", 0.0), (512, "icon-maskable-512.png", 0.18)):
        pg = b.new_page(viewport={"width": size, "height": size})
        inner = int(size * (1 - 2 * pad))
        pg.set_content(f'<html><body style="margin:0;background:#17171b;display:grid;place-items:center;width:{size}px;height:{size}px">'
                       f'<div style="width:{inner}px;height:{inner}px">{svg.replace("<svg ", f"<svg width=\'{inner}\' height=\'{inner}\' ", 1)}</div></body></html>')
        pg.screenshot(path=f"/work/bridge/btbridge/webui/static/{name}", omit_background=False)
        pg.close()
    b.close()
print("ok")
