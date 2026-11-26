"""Brand fonts for rendering: fetched from Google Fonts once, kept in storage, embedded in the page.

Renders never touch the network for fonts: each family+weight becomes one @font-face CSS block with the
font file inlined (base64), cached at fonts/<family>-<weight>.css. Only the Latin subsets are kept
(English and Hinglish written in Roman script); that keeps each block around 30-60 KB.
"""

import base64
import re

import httpx

from buildsocial.harness.storage import Storage

CSS_URL = "https://fonts.googleapis.com/css2"
# Google serves woff2 (the small modern format) only to browsers that say they support it.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
KEEP_SUBSETS = ("latin", "latin-ext")

_BLOCK = re.compile(r"/\* ([\w-]+) \*/\s*(@font-face\s*\{[^}]*\})")
_SRC = re.compile(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)")


class FontNotFound(ValueError):
    pass


def _key(family: str, weight: int) -> str:
    return f"fonts/{re.sub(r'[^a-z0-9]+', '-', family.lower()).strip('-')}-{weight}.css"


def parse_blocks(css: str) -> list[tuple[str, str]]:
    """(subset, @font-face block) pairs from a Google Fonts css2 response."""
    return _BLOCK.findall(css)


async def font_css(store: Storage, family: str, weight: int, client: httpx.AsyncClient | None = None) -> str:
    """@font-face CSS for one family and weight, with the font files inlined."""
    key = _key(family, weight)
    if store.exists(key):
        return store.read_text(key)

    own = client is None
    client = client or httpx.AsyncClient(timeout=30, headers={"User-Agent": BROWSER_UA})
    try:
        r = await client.get(CSS_URL, params={"family": f"{family}:wght@{weight}", "display": "swap"})
        if r.status_code == 400:
            raise FontNotFound(f"Google Fonts has no '{family}' in weight {weight}")
        r.raise_for_status()
        blocks = [b for subset, b in parse_blocks(r.text) if subset in KEEP_SUBSETS]
        if not blocks:
            raise FontNotFound(f"Google Fonts returned no Latin files for '{family}' {weight}")
        inlined = []
        for block in blocks:
            url = _SRC.search(block)
            if not url:
                continue
            data = (await client.get(url.group(1))).raise_for_status().content
            uri = "data:font/woff2;base64," + base64.b64encode(data).decode("ascii")
            inlined.append(block.replace(url.group(1), uri))
    finally:
        if own:
            await client.aclose()

    css = "\n".join(inlined)
    store.write_text(key, css)
    return css


async def brand_font_css(store: Storage, families: dict[str, list[int]]) -> str:
    """All the CSS a render needs, e.g. {"Fraunces": [800], "DM Sans": [400, 600]}."""
    parts = []
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": BROWSER_UA}) as client:
        for family, weights in families.items():
            for w in sorted(set(weights)):
                parts.append(await font_css(store, family, w, client))
    return "\n".join(parts)
