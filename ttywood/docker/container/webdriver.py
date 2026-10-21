#!/usr/bin/env python3
"""ttywood web driver: runs INSIDE the episode container.

A persistent headless Chromium driven by JSON lines on stdin, one action per
line, one JSON reply per line on stdout. Playwright records the session video
itself (webm); every action, key and click is logged with wall-clock times so
the renderer can align the browser video with the terminal recording.

Human touches: curved eased mouse moves with a hover pause, per-character
typing with jitter, and a visible cursor (headless video has none) injected
into every page.

Actions (fields beyond "action" are optional unless noted):
  {"action":"goto","url":...}
  {"action":"click","target":...}            target: css | text:... | role:button:Name
  {"action":"fill","target":...,"text":...}   clicks the field, then types
  {"action":"type","text":...}                types into whatever is focused
  {"action":"press","key":"Enter"}
  {"action":"hover","target":...}
  {"action":"wait","ms":800} | {"action":"wait","target":...} | {"action":"wait","text":...}
  {"action":"scroll","dy":600}
  {"action":"close"}
Every action accepts "label" (what the command bar shows).
"""

import json
import math
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("/episode/web")
VIEWPORT = {"width": 1600, "height": 900}
CURSOR_JS = """
(() => {
  if (window.__dtCursor) return;
  const c = document.createElement('div');
  c.id = '__dt_cursor';
  c.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:22px;height:22px;'
    + 'left:-100px;top:-100px;transform:translate(-3px,-2px);'
    + "background:url(\\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='22' height='22'>"
    + "<path d='M3 2 L3 18 L7.5 14 L10.5 20.5 L13 19.5 L10 13 L16 13 Z' fill='white' stroke='black' stroke-width='1.4'/></svg>\\") no-repeat;"
    + 'transition:none;';
  const ring = document.createElement('div');
  ring.id = '__dt_ring';
  ring.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;width:34px;height:34px;'
    + 'border:3px solid rgba(124,156,255,.9);border-radius:50%;left:-100px;top:-100px;'
    + 'transform:translate(-17px,-17px) scale(.3);opacity:0;';
  const add = () => { document.documentElement.appendChild(c); document.documentElement.appendChild(ring); };
  if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add);
  window.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  window.addEventListener('mousedown', e => {
    ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
    ring.style.transition = 'none'; ring.style.opacity = '1'; ring.style.transform = 'translate(-17px,-17px) scale(.3)';
    requestAnimationFrame(() => { ring.style.transition = 'transform .35s ease-out, opacity .35s ease-out';
      ring.style.opacity = '0'; ring.style.transform = 'translate(-17px,-17px) scale(1.2)'; });
  }, true);
  window.__dtCursor = true;
})();
"""


class Driver:
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.log = open(OUT / "actions.jsonl", "a")
        self.rng = random.Random()
        self.pw = sync_playwright().start()
        # the browser is warm from the start of the recording, but the
        # recorded context is created at the first web action: a context
        # created at launch records the whole terminal part of the episode
        # too (15 minutes of a blank page), and finalizing that much video
        # is what made `close` hang and lose the real frames
        self.browser = self.pw.chromium.launch(headless=True)
        self.ctx = None
        self.page = None
        self.t0 = time.time()
        self.mouse = [VIEWPORT["width"] * 0.5, VIEWPORT["height"] * 0.6]
        (OUT / "meta.json").write_text(json.dumps({"t0": self.t0, "viewport": VIEWPORT}))

    def ensure_page(self):
        if self.page is not None:
            return
        self.ctx = self.browser.new_context(
            viewport=VIEWPORT, record_video_dir=str(OUT), record_video_size=VIEWPORT,
            device_scale_factor=1)
        self.ctx.add_init_script(CURSOR_JS)
        self.page = self.ctx.new_page()
        # Chromium only starts emitting video frames at the first paint
        self.page.goto("data:text/html,<html style='background:%23fff'></html>")
        self.t0 = time.time()
        self.page.mouse.move(*self.mouse)
        (OUT / "meta.json").write_text(json.dumps({"t0": self.t0, "viewport": VIEWPORT}))

    # ---- humanised primitives ----
    def move_to(self, x, y):
        x0, y0 = self.mouse
        dist = math.hypot(x - x0, y - y0)
        steps = max(12, min(45, int(dist / 18)))
        # a control point off the straight line gives a natural arc
        nx, ny = -(y - y0), (x - x0)
        norm = math.hypot(nx, ny) or 1
        bend = self.rng.uniform(-0.18, 0.18) * dist
        cx, cy = (x0 + x) / 2 + nx / norm * bend, (y0 + y) / 2 + ny / norm * bend
        for i in range(1, steps + 1):
            u = i / steps
            e = u * u * (3 - 2 * u)  # ease in-out
            px = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e ** 2 * x
            py = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e ** 2 * y
            self.page.mouse.move(px, py)
            time.sleep(self.rng.uniform(0.006, 0.014))
        self.mouse = [x, y]

    def locate(self, target: str):
        if target.startswith("text:"):
            return self.page.get_by_text(target[5:], exact=False).first
        if target.startswith("role:"):
            _, role, name = target.split(":", 2)
            return self.page.get_by_role(role, name=name).first
        if target.startswith("label:"):
            return self.page.get_by_label(target[6:]).first
        if target.startswith("placeholder:"):
            return self.page.get_by_placeholder(target[12:]).first
        return self.page.locator(target).first

    def aim(self, target: str):
        loc = self.locate(target)
        loc.wait_for(state="visible", timeout=15000)
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        if not box:
            raise RuntimeError(f"no bounding box for {target!r}")
        x = box["x"] + box["width"] * self.rng.uniform(0.35, 0.65)
        y = box["y"] + box["height"] * self.rng.uniform(0.4, 0.6)
        self.move_to(x, y)
        time.sleep(self.rng.uniform(0.12, 0.3))
        return loc

    def type_text(self, text: str, keys: list):
        for ch in text:
            self.page.keyboard.type(ch)
            keys.append(round(time.time(), 3))
            d = self.rng.lognormvariate(math.log(0.085), 0.45)
            time.sleep(min(max(d, 0.03), 0.35) + (0.04 if ch == " " else 0))

    # ---- actions ----
    def run(self, a: dict) -> dict:
        act = a["action"]
        keys: list = []
        clicks: list = []
        if act != "close":
            self.ensure_page()
        if act == "goto":
            self.page.goto(a["url"], wait_until=a.get("wait_until", "load"), timeout=60000)
            time.sleep(0.3)
            # the cursor overlay is re-injected on navigation; nudge the mouse so it shows
            self.page.mouse.move(self.mouse[0] + 1, self.mouse[1] + 1)
            self.page.mouse.move(*self.mouse)
            time.sleep(0.2)
        elif act == "click":
            if a.get("optional"):
                # a dismissable modal (onboarding, survey, cookie banner) that
                # may or may not appear: click it if it is there, else move on
                try:
                    self.locate(a["target"]).wait_for(state="visible", timeout=a.get("ms", 3000))
                except Exception:
                    return {"keys": keys, "clicks": clicks, "mouse": self.mouse[:], "skipped": True}
            self.aim(a["target"])
            self.page.mouse.down(); self.page.mouse.up()
            clicks.append(round(time.time(), 3))
            time.sleep(self.rng.uniform(0.25, 0.5))
        elif act == "hover":
            self.aim(a["target"])
            time.sleep(a.get("ms", 600) / 1000)
        elif act == "fill":
            self.aim(a["target"])
            self.page.mouse.down(); self.page.mouse.up()
            clicks.append(round(time.time(), 3))
            time.sleep(0.2)
            self.page.keyboard.press("ControlOrMeta+A")
            self.type_text(a["text"], keys)
        elif act == "type":
            self.type_text(a["text"], keys)
        elif act == "press":
            self.page.keyboard.press(a["key"])
            keys.append(round(time.time(), 3))
            time.sleep(0.3)
        elif act == "wait":
            if "target" in a:
                self.locate(a["target"]).wait_for(state="visible", timeout=a.get("ms", 30000))
            elif "text" in a:
                self.page.get_by_text(a["text"]).first.wait_for(state="visible", timeout=a.get("ms", 30000))
            else:
                time.sleep(a.get("ms", 800) / 1000)
        elif act == "scroll":
            self.page.mouse.wheel(0, a.get("dy", 600))
            time.sleep(0.5)
        elif act == "close":
            path = self.page.video.path() if self.page is not None and self.page.video else None
            if self.page is not None:
                self.page.close()
            if self.ctx is not None:
                self.ctx.close()  # finalizes the webm
            self.browser.close()
            self.pw.stop()
            return {"video": path}
        else:
            raise ValueError(f"unknown action {act!r}")
        return {"keys": keys, "clicks": clicks, "mouse": self.mouse[:]}


def main():
    drv = Driver()
    print(json.dumps({"ready": True, "t0": drv.t0}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        a = json.loads(line)
        start = time.time()
        try:
            extra = drv.run(a)
            rec = {"id": a.get("id"), "ok": True, "action": a["action"],
                   "label": a.get("label", ""), "start": round(start, 3),
                   "end": round(time.time(), 3), **extra}
        except Exception as e:  # keep the channel alive; the host decides
            # what the page showed at that moment (title, URL, first visible
            # lines, a screenshot) so the failure can be diagnosed and repaired
            context = ""
            try:
                shot = OUT / f"fail-{a.get('id', 0)}.png"
                drv.page.screenshot(path=str(shot))
                body = drv.page.evaluate("() => document.body.innerText") or ""
                lines = [l.strip() for l in body.splitlines() if l.strip()][:25]
                context = (f" | page: {drv.page.title()!r} {drv.page.url} | visible: "
                           + " / ".join(lines)[:700] + f" | screenshot: web/{shot.name}")
            except Exception:
                pass
            rec = {"id": a.get("id"), "ok": False, "action": a["action"],
                   "label": a.get("label", ""), "start": round(start, 3),
                   "end": round(time.time(), 3), "error": (str(e)[:400] + context)[:1400]}
        drv.log.write(json.dumps(rec) + "\n"); drv.log.flush()
        print(json.dumps(rec), flush=True)
        if a["action"] == "close":
            break


if __name__ == "__main__":
    main()
