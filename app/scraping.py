import re
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

BLOCKLIST_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "crunchbase.com", "bloomberg.com", "wikipedia.org", "indeed.com",
    "glassdoor.com", "youtube.com", "yelp.com", "zoominfo.com",
    "duckduckgo.com", "google.com", "apollo.io",
}

REQUEST_TIMEOUT = 10
MAX_TEXT_CHARS = 6000


def _is_blocked(url: str) -> bool:
    netloc = urlparse(url).netloc.lower().replace("www.", "")
    return any(netloc == d or netloc.endswith("." + d) for d in BLOCKLIST_DOMAINS)


def ddg_search_homepage(company_name: str) -> str | None:
    """Search DuckDuckGo's HTML endpoint for the company's likely homepage."""
    query = f"{company_name} official website"
    try:
        resp = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.select("a.result__a"):
        href = a.get("href")
        if not href:
            continue
        parsed = urlparse(href)
        if parsed.netloc == "" or "duckduckgo.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            real = qs.get("uddg", [None])[0]
            if real:
                href = unquote(real)
        if href.startswith("http") and not _is_blocked(href):
            return href
    return None


def ddg_search_results(query: str, max_results: int = 5) -> list[dict]:
    """General-purpose DuckDuckGo HTML search returning title/url/snippet for
    up to max_results results, filtering out known directory/social sites."""
    try:
        resp = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for result in soup.select(".result"):
        link = result.select_one("a.result__a")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        parsed = urlparse(href)
        if parsed.netloc == "" or "duckduckgo.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            real = qs.get("uddg", [None])[0]
            if real:
                href = unquote(real)
        if not href.startswith("http") or _is_blocked(href):
            continue
        snippet_el = result.select_one(".result__snippet")
        results.append(
            {
                "title": link.get_text(strip=True),
                "url": href,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            }
        )
        if len(results) >= max_results:
            break
    return results


def fetch_soup(url: str) -> BeautifulSoup | None:
    """Fetches a page and returns its parsed soup (for link discovery), or
    None on any failure — kept separate from fetch_page_text since callers
    here need the raw DOM, not just extracted text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def find_link_by_keywords(soup: BeautifulSoup, base_url: str, keywords: list[str]) -> str | None:
    base_domain = urlparse(base_url).netloc.lower()
    for a in soup.find_all("a", href=True):
        label = (a.get_text() or "").strip().lower()
        href = a["href"]
        if any(kw in label or kw in href.lower() for kw in keywords):
            full = requests.compat.urljoin(base_url, href)
            if urlparse(full).netloc.lower() == base_domain:
                return full
    return None


def _find_about_link(soup: BeautifulSoup, base_url: str) -> str | None:
    return find_link_by_keywords(soup, base_url, ["about"])


def fetch_page_text_only(url: str, max_chars: int = MAX_TEXT_CHARS) -> str | None:
    """Fetches a single page's cleaned text with no About-page chasing —
    used for targeted blog/careers page fetches where we already know the URL."""
    soup = fetch_soup(url)
    if not soup:
        return None
    text = _clean_text(soup).strip()
    return text[:max_chars] if text else None


def fetch_page_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    text = _clean_text(soup)

    about_url = _find_about_link(soup, url)
    if about_url and about_url != url:
        try:
            about_resp = requests.get(about_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            about_resp.raise_for_status()
            about_soup = BeautifulSoup(about_resp.text, "html.parser")
            text += " " + _clean_text(about_soup)
        except requests.RequestException:
            pass

    text = text.strip()
    if not text:
        return None
    return text[:MAX_TEXT_CHARS]


def get_company_text(company_name: str, domain: str | None) -> tuple[str | None, str | None]:
    """Returns (source_url, page_text) for a company, trying the guessed domain
    first and falling back to a DuckDuckGo search."""
    candidate_urls = []
    if domain:
        candidate_urls = [f"https://{domain}", f"https://www.{domain}"]

    for url in candidate_urls:
        text = fetch_page_text(url)
        if text and len(text) > 200:
            return url, text

    homepage = ddg_search_homepage(company_name)
    if homepage:
        text = fetch_page_text(homepage)
        if text and len(text) > 200:
            return homepage, text

    return None, None
