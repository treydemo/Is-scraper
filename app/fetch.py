"""Fetching: raw HTTP (what AI crawlers see) and rendered browser (what humans see).

The core trick of the audit lives here: AI crawlers like GPTBot, ClaudeBot and PerplexityBot
do not execute JavaScript. Googlebot does. So we fetch every page two ways and compare.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from . import config

# Pages that are actually a bot-protection challenge, not the site.
_WALL_MARKERS = [
    "just a moment...",
    "checking your browser",
    "cf-browser-verification",
    "challenge-platform",
    "attention required! | cloudflare",
    "_incapsula_resource",
    "px-captcha",
    "ddos protection by",
    "please enable javascript and cookies to continue",
    "verifying you are human",
    "access denied | ",
    "sucuri website firewall",
]


def normalize_url(raw: str) -> str:
    url = re.sub(r"\s+", "", (raw or "").strip())
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    return urlunparse(p._replace(path=p.path or "/", params="", query="", fragment=""))


def base_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_bot_wall(status: int | None, html: str) -> bool:
    if not html:
        return status in (403, 429, 503)
    head = html[:6000].lower()
    if any(m in head for m in _WALL_MARKERS):
        return True
    # Tiny 403/503 pages are almost always a firewall, not the real site.
    return status in (403, 429, 503) and len(visible_text(html)) < 400


def visible_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


@dataclass
class RawPage:
    url: str
    final_url: str = ""
    status: int | None = None
    html: str = ""
    headers: dict = field(default_factory=dict)
    error: str | None = None
    walled: bool = False

    @property
    def ok(self) -> bool:
        return self.status is not None and self.status < 400 and not self.walled and bool(self.html)


async def fetch_raw(client: httpx.AsyncClient, url: str, ua: str = config.CHROME_UA,
                    timeout: float = 20) -> RawPage:
    page = RawPage(url=url)
    try:
        r = await client.get(url, headers={"User-Agent": ua, "Accept": "text/html,*/*;q=0.8",
                                            "Accept-Language": "en-US,en;q=0.9"},
                             timeout=timeout, follow_redirects=True)
        page.status = r.status_code
        page.final_url = str(r.url)
        page.headers = {k.lower(): v for k, v in r.headers.items()}
        ctype = page.headers.get("content-type", "")
        if "html" in ctype or "xml" in ctype or ctype == "" or "text/plain" in ctype:
            page.html = r.text
        page.walled = is_bot_wall(r.status_code, page.html)
    except Exception as e:  # network errors, TLS, timeouts
        page.error = f"{type(e).__name__}: {e}"[:300]
    return page


async def fetch_text(client: httpx.AsyncClient, url: str, ua: str = config.CHROME_UA,
                     timeout: float = 12) -> tuple[int | None, str, str]:
    """Small helper for robots.txt / llms.txt / sitemaps. Returns (status, content-type, text)."""
    try:
        r = await client.get(url, headers={"User-Agent": ua}, timeout=timeout, follow_redirects=True)
        return r.status_code, r.headers.get("content-type", ""), r.text
    except Exception:
        return None, "", ""


# ── Rendered browser ─────────────────────────────────────────────────────────

# Collected inside the page: real computed colors/fonts, logo candidates, and visible text.
_BRAND_JS = r"""
() => {
  const out = {colors: {}, fonts: {}, logo: [], fontLinks: [], text: ''};
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const addColor = (c, weight, role) => {
    if (!c || c === 'transparent' || c.startsWith('rgba(0, 0, 0, 0)')) return;
    const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
    if (!m) return;
    if (m[4] !== undefined && parseFloat(m[4]) < 0.5) return;
    const hex = '#' + [m[1], m[2], m[3]].map(x => (+x).toString(16).padStart(2, '0')).join('').toUpperCase();
    out.colors[hex] = out.colors[hex] || {weight: 0, roles: {}};
    out.colors[hex].weight += weight;
    out.colors[hex].roles[role] = (out.colors[hex].roles[role] || 0) + weight;
  };
  const roleSel = {
    header: 'header, nav, [class*="header"], [class*="navbar"]',
    button: 'button, .btn, [class*="button"], a[class*="cta"], input[type=submit]',
    heading: 'h1, h2, h3',
    link: 'a',
    footer: 'footer',
    body: 'body, main, section'
  };
  for (const [role, sel] of Object.entries(roleSel)) {
    document.querySelectorAll(sel).forEach((el, i) => {
      if (i > 60 || !isVisible(el)) return;
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const area = Math.min(r.width * r.height, 400000);
      addColor(s.backgroundColor, area / 1000 + 1, role + '-bg');
      addColor(s.color, (role === 'button' || role === 'heading' || role === 'link') ? 20 : 2, role + '-text');
      if (role === 'heading' || role === 'body' || role === 'button') {
        const fam = s.fontFamily.split(',')[0].replace(/["']/g, '').trim();
        if (fam) {
          const key = role === 'heading' ? 'heading' : (role === 'button' ? 'button' : 'body');
          out.fonts[key] = out.fonts[key] || {};
          out.fonts[key][fam] = (out.fonts[key][fam] || 0) + 1;
        }
      }
    });
  }
  document.querySelectorAll('link[href*="fonts.googleapis.com"], link[href*="use.typekit.net"], link[href*="fonts.bunny.net"]')
    .forEach(l => out.fontLinks.push(l.href));
  // Logo candidates, best first
  const cand = [];
  const push = (src, how, el) => { if (src) cand.push({src, how, w: el ? el.getBoundingClientRect().width : 0}); };
  document.querySelectorAll('header img, nav img, [class*="logo"] img, img[class*="logo"], img[id*="logo"], img[alt*="logo" i]')
    .forEach(img => { if (isVisible(img)) push(img.currentSrc || img.src, 'header-img', img); });
  document.querySelectorAll('header svg, [class*="logo"] svg, a[class*="logo"] svg').forEach(svg => {
    if (isVisible(svg) && svg.outerHTML.length < 60000) push('inline-svg', 'header-svg', svg), cand[cand.length-1].svg = svg.outerHTML;
  });
  const touch = document.querySelector('link[rel="apple-touch-icon"]'); if (touch) push(touch.href, 'touch-icon');
  const og = document.querySelector('meta[property="og:image"]'); if (og) push(og.content, 'og-image');
  const icon = document.querySelector('link[rel~="icon"]'); if (icon) push(icon.href, 'favicon');
  out.logo = cand.slice(0, 6);
  out.text = (document.body && document.body.innerText || '').slice(0, 60000);
  return out;
}
"""


@dataclass
class RenderedPage:
    url: str
    html: str = ""
    text: str = ""
    status: int | None = None
    brand: dict = field(default_factory=dict)
    walled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.html) and not self.walled and (self.status is None or self.status < 400)


class Browser:
    """One Chromium per job, reused across pages. Rendering is the expensive step, so we keep it to a few pages."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._ctx = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"])
        self._ctx = await self._browser.new_context(user_agent=config.CHROME_UA, locale="en-US",
                                                    viewport={"width": 1366, "height": 900})
        return self

    async def __aexit__(self, *exc):
        try:
            await self._browser.close()
        finally:
            await self._pw.stop()

    async def render(self, url: str, collect_brand: bool = False, timeout_ms: int = 30000) -> RenderedPage:
        out = RenderedPage(url=url)
        page = await self._ctx.new_page()
        try:
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(2500)
            out.status = resp.status if resp else None
            html = await page.content()
            # A challenge page often clears itself after a few seconds in a real browser.
            if is_bot_wall(out.status, html):
                await page.wait_for_timeout(8000)
                html = await page.content()
            out.html = html
            out.walled = is_bot_wall(out.status, html)
            if not out.walled:
                if collect_brand:
                    out.brand = await page.evaluate(_BRAND_JS)
                    out.text = re.sub(r"\s+", " ", out.brand.pop("text", ""))
                else:
                    out.text = visible_text(html)
        except Exception as e:
            out.error = f"{type(e).__name__}: {e}"[:300]
        finally:
            await page.close()
        return out
