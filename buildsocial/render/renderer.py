"""Render a PageSpec to a PNG with a headless browser (Playwright + Chromium), and measure the result.

One browser per Renderer, one page per render: on a small machine that is about 400 MB at peak.
The browser picks the largest headline size that fits its box (between the TYPE limits), then reports
where every piece of text and the logo ended up, so code can check the result (see checks).
"""

from dataclasses import dataclass, field

from playwright.async_api import Browser, async_playwright

from buildsocial.design_specs import TYPE
from buildsocial.render.layouts import PageSpec, build_html

# Shrink the headline until the text box doesn't overflow and the headline has at most N lines.
FIT_HEADLINE = """([minPx, maxPx, maxLines, step]) => {
  const box = document.getElementById('hbox'), h = document.getElementById('headline');
  const lines = () => Math.round(h.getBoundingClientRect().height / (parseFloat(getComputedStyle(h).fontSize) * 1.04));
  let size = maxPx;
  h.style.fontSize = size + 'px';
  while ((box.scrollHeight > box.clientHeight + 1 || lines() > maxLines) && size - step >= minPx) {
    size -= step; h.style.fontSize = size + 'px';
  }
  const rect = (id) => { const e = document.getElementById(id); if (!e) return null;
    const r = e.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; };
  return {
    headline_px: size, headline_lines: lines(),
    overflow: box.scrollHeight > box.clientHeight + 1 || lines() > maxLines,
    boxes: {headline: rect('headline'), subline: rect('subline'), logo: rect('logo')},
    subline_px: document.getElementById('subline') ? parseFloat(getComputedStyle(document.getElementById('subline')).fontSize) : null,
  };
}"""


@dataclass
class Rendered:
    png: bytes
    headline_px: float
    headline_lines: int
    overflow: bool  # the text didn't fit even at the smallest headline size
    boxes: dict[str, dict | None] = field(default_factory=dict)  # headline / subline / logo, in canvas pixels
    subline_px: float | None = None


class Renderer:
    """async with Renderer() as r: png = await r.render(spec, font_css)"""

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "Renderer":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def render(self, spec: PageSpec, font_css: str) -> Rendered:
        assert self._browser, "use 'async with Renderer()'"
        page = await self._browser.new_page(viewport={"width": spec.canvas.width, "height": spec.canvas.height})
        try:
            # Everything is inline (fonts, photo, logo): block any network request a page might try.
            await page.route("**/*", lambda route: route.abort() if route.request.url.startswith("http") else route.continue_())
            await page.set_content(build_html(spec, font_css), wait_until="load")
            await page.evaluate("document.fonts.ready")
            m = await page.evaluate(FIT_HEADLINE, [TYPE.headline_px[0], TYPE.headline_px[1], TYPE.max_headline_lines, 4])
            png = await page.screenshot(type="png")
        finally:
            await page.close()
        return Rendered(png=png, headline_px=m["headline_px"], headline_lines=m["headline_lines"],
                        overflow=m["overflow"], boxes=m["boxes"], subline_px=m["subline_px"])
