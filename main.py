import argparse
import asyncio
import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, unquote

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import async_playwright
from playwright._impl._errors import TargetClosedError

ASSET_DOWNLOAD_RETRIES = 3
ASSET_DOWNLOAD_RETRY_DELAY = 2.0
ASSET_DOWNLOAD_TIMEOUT = 90
_logged_404_asset_urls: set[str] = set()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

ALLOW_INSECURE_ASSET_SSL = os.getenv("ALLOW_INSECURE_ASSET_SSL", "1") == "1"

def derive_output_dir_name(start_url: str) -> str:
    normalized = normalize_url(start_url)
    parsed = urlparse(normalized)
    path = parsed.path.strip("/")
    if not path:
        return "apple-docs"
    safe_path = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")
    return safe_path or "apple-docs"



def get_allowed_prefix(start_url: str) -> str:
    normalized = normalize_url(start_url)
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    if not path:
        return normalized

    parts = [part for part in path.split("/") if part]
    if not parts:
        return normalized

    if len(parts) >= 2 and parts[0].lower() == "documentation":
        return f"{parsed.scheme}://{parsed.netloc}/documentation/{parts[1]}"

    if len(parts) >= 3 and parts[0].lower() == "cn" and parts[1].lower() == "design" and parts[2].lower() == "human-interface-guidelines":
        return f"{parsed.scheme}://{parsed.netloc}/cn/design/human-interface-guidelines"

    return f"{parsed.scheme}://{parsed.netloc}{path}"


DENY_PATTERNS = [
    r"/documentation/.+\?changes=",
    r"[?&]language=",
    r"[?&]changes=",
    r"[?&]utm_",
    r"/videos/",
    r"/news/",
    r"/forums/",
    r"/downloads/",
    r"/account/",
]

NAV_SELECTORS = [
    "nav",
    "header",
    "footer",
    "aside",
    "[role='navigation']",
    ".navigator",
    ".sidebar",
    ".menu",
    ".breadcrumbs",
    ".pagination",
    ".footer",
    ".header",
]

MAIN_SELECTORS = [
    "main",
    "[role='main']",
    ".main",
    ".content",
    ".content-body",
    ".documentation",
    ".doc-content",
    "article",
]


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    cleaned = parsed._replace(query="", fragment="")
    return cleaned.geturl().rstrip("/")


def is_allowed(url: str, allowed_prefix: str) -> bool:
    normalized = normalize_url(url)
    normalized_lower = normalized.lower()
    allowed_prefix_lower = allowed_prefix.lower()
    if not normalized_lower.startswith(allowed_prefix_lower):
        return False
    for pattern in DENY_PATTERNS:
        if re.search(pattern, normalized):
            return False
    return True




# Characters invalid in filenames on Windows; also avoid on macOS/Linux for portability
_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]+')


def safe_filename(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        path = "index"
    path = path.replace("/", "__")
    path = _FILENAME_UNSAFE_RE.sub("_", path)
    # Collapse multiple underscores and strip leading/trailing
    path = re.sub(r"_+", "_", path).strip("_") or "index"
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return f"{path}__{digest}.md"


def load_existing_manifest(manifest_path: Path) -> tuple[list[dict], set[str]]:
    if not manifest_path.exists():
        return [], set()

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] failed to read manifest: {manifest_path} -> {exc}")
        return [], set()

    if not isinstance(data, list):
        print(f"[WARN] manifest format is invalid: {manifest_path}")
        return [], set()

    manifest_entries = []
    existing_urls = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        manifest_entries.append(item)
        existing_urls.add(url)

    return manifest_entries, existing_urls


def upsert_manifest_entry(manifest: list[dict], manifest_index: dict[str, int], entry: dict) -> None:
    url = entry["url"]
    if url in manifest_index:
        manifest[manifest_index[url]] = entry
    else:
        manifest_index[url] = len(manifest)
        manifest.append(entry)


# --- Asset/image helpers ---
def safe_asset_name(url: str) -> str:
    parsed = urlparse(url)
    raw_name = Path(unquote(parsed.path)).name or "asset"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)
    stem = Path(base).stem or "asset"
    suffix = Path(base).suffix
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return f"{stem}__{digest}{suffix}"


def _is_retryable_error(exc: BaseException) -> bool:
    """True for connection resets, timeouts, SSL protocol EOF, IncompleteRead, and other transient errors."""
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg or "incompleteread" in msg:
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return True
        if isinstance(reason, OSError) and getattr(reason, "errno", None) in (104, 54, 110):
            return True  # 104=ECONNRESET, 54=ECONNRESET on macOS, 110=ETIMEDOUT
        if isinstance(reason, ssl.SSLError) and not isinstance(reason, ssl.SSLCertVerificationError):
            rmsg = str(reason).upper()
            if "UNEXPECTED_EOF" in rmsg or "EOF" in rmsg or "PROTOCOL" in rmsg or "CONNECTION_RESET" in rmsg:
                return True
        if reason is not None and ("timed out" in str(reason).lower() or "incompleteread" in str(reason).lower()):
            return True
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError, OSError)):
        errno = getattr(exc, "errno", None)
        if errno in (104, 54, 110):
            return True
    if isinstance(exc, ssl.SSLError) and not isinstance(exc, ssl.SSLCertVerificationError):
        rmsg = str(exc).upper()
        if "UNEXPECTED_EOF" in rmsg or "EOF" in rmsg or "PROTOCOL" in rmsg or "CONNECTION_RESET" in rmsg:
            return True
    return False


def download_asset_sync(url: str, file_path: Path) -> bool:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
    )

    def write_response(response) -> bool:
        data = response.read()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        return True

    def do_request(context: ssl.SSLContext | None = None) -> bool:
        with urllib.request.urlopen(request, timeout=ASSET_DOWNLOAD_TIMEOUT, context=context) as response:
            return write_response(response)

    def insecure_fallback() -> bool:
        if not ALLOW_INSECURE_ASSET_SSL:
            return False
        try:
            insecure_context = ssl._create_unverified_context()
            if do_request(insecure_context):
                print(f"[INFO] asset downloaded with insecure SSL fallback: {url}")
                return True
        except Exception as fallback_exc:
            print(f"[WARN] asset download failed after insecure SSL fallback: {url} -> {fallback_exc}")
        return False

    last_exc: BaseException | None = None
    for attempt in range(ASSET_DOWNLOAD_RETRIES):
        try:
            return do_request(None)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                global _logged_404_asset_urls
                if url not in _logged_404_asset_urls:
                    _logged_404_asset_urls.add(url)
                    print(f"[WARN] asset not found (404): {url}")
                return False
            last_exc = exc
            if attempt == ASSET_DOWNLOAD_RETRIES - 1:
                print(f"[WARN] asset download failed: {url} -> {exc}")
                return False
            time.sleep(ASSET_DOWNLOAD_RETRY_DELAY)
        except ssl.SSLCertVerificationError:
            if not ALLOW_INSECURE_ASSET_SSL:
                print(f"[WARN] asset download failed: {url} -> SSL verification error")
                return False
            return insecure_fallback()
        except ssl.SSLError as exc:
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == ASSET_DOWNLOAD_RETRIES - 1:
                print(f"[WARN] asset download failed: {url} -> {exc}")
                return False
            time.sleep(ASSET_DOWNLOAD_RETRY_DELAY)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                if not ALLOW_INSECURE_ASSET_SSL:
                    print(f"[WARN] asset download failed: {url} -> {exc}")
                    return False
                return insecure_fallback()
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == ASSET_DOWNLOAD_RETRIES - 1:
                print(f"[WARN] asset download failed: {url} -> {exc}")
                return False
            time.sleep(ASSET_DOWNLOAD_RETRY_DELAY)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == ASSET_DOWNLOAD_RETRIES - 1:
                print(f"[WARN] asset download failed: {url} -> {exc}")
                return False
            time.sleep(ASSET_DOWNLOAD_RETRY_DELAY)

    if last_exc is not None:
        print(f"[WARN] asset download failed: {url} -> {last_exc}")
    return False


def rewrite_local_doc_links(soup: BeautifulSoup, page_url: str, allowed_prefix: str) -> BeautifulSoup:
    for node in soup.select("a[href]"):
        href = (node.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("#"):
            continue
        absolute = urljoin(page_url, href)
        absolute, fragment = urldefrag(absolute)
        normalized = normalize_url(absolute)
        if is_allowed(normalized, allowed_prefix):
            local_href = safe_filename(normalized)
            if fragment:
                local_href = f"{local_href}#{fragment}"
            node["href"] = local_href
    return soup


async def localize_images(page, page_url: str, output_dir: Path, filename_stem: str) -> None:
    assets_dir = output_dir / "assets" / filename_stem
    image_urls = await page.eval_on_selector_all(
        "img[src]",
        """
        (nodes) => nodes
          .map(n => n.getAttribute('src'))
          .filter(Boolean)
        """,
    )

    unique_urls = []
    seen = set()
    for src in image_urls:
        absolute = urljoin(page_url, src)
        absolute = urldefrag(absolute)[0]
        if absolute and absolute not in seen:
            seen.add(absolute)
            unique_urls.append(absolute)

    async def download_one(absolute: str) -> tuple[str, Path, bool]:
        asset_name = safe_asset_name(absolute)
        local_file = assets_dir / asset_name
        if local_file.exists():
            return absolute, local_file, True
        ok = await asyncio.to_thread(download_asset_sync, absolute, local_file)
        return absolute, local_file, ok

    results = await asyncio.gather(*(download_one(abs_url) for abs_url in unique_urls))

    for absolute, local_file, ok in results:
        if not ok:
            continue
        asset_name = local_file.name
        rel_path = Path("assets") / filename_stem / asset_name
        try:
            await page.eval_on_selector_all(
                "img[src]",
                """
                (nodes, payload) => {
                    const [targetUrl, localPath] = payload;
                    for (const node of nodes) {
                        const src = node.getAttribute('src');
                        if (!src) continue;
                        const absolute = new URL(src, document.baseURI).href.split('#')[0];
                        if (absolute === targetUrl) {
                            node.setAttribute('src', localPath);
                        }
                    }
                }
                """,
                [absolute, rel_path.as_posix()],
            )
        except Exception:
            continue


def extract_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ["h1", "title"]:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return fallback


def clean_dom(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in NAV_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    for node in soup.select("script, style, noscript, svg, button"):
        node.decompose()

    for node in soup.select("[aria-hidden='true']"):
        node.decompose()

    return soup


def pick_main_content(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in MAIN_SELECTORS:
        node = soup.select_one(selector)
        if node:
            return BeautifulSoup(str(node), "lxml")
    return soup


def html_to_markdown(html: str, page_url: str, title: str, allowed_prefix: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    soup = clean_dom(soup)
    soup = rewrite_local_doc_links(soup, page_url, allowed_prefix)
    main_soup = pick_main_content(soup)

    markdown = md(
        str(main_soup),
        heading_style="ATX",
        bullets="-",
    ).strip()

    front_matter = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f'url: "{page_url}"',
        "---",
        "",
    ]
    return "\n".join(front_matter) + markdown + "\n"


async def extract_links(page, base_url: str, allowed_prefix: str) -> list[str]:
    hrefs = await page.eval_on_selector_all(
        "a[href]",
        """
        (nodes) => nodes
          .map(n => n.getAttribute('href'))
          .filter(Boolean)
        """,
    )

    results = []
    for href in hrefs:
        absolute = urljoin(base_url, href)
        absolute = normalize_url(absolute)
        if is_allowed(absolute, allowed_prefix):
            results.append(absolute)

    return sorted(set(results))


async def fetch_page_content(page, url: str, wait_after_load_ms: int = 500) -> tuple[str, str]:
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    if wait_after_load_ms > 0:
        await page.wait_for_timeout(wait_after_load_ms)

    try:
        await page.locator("main, article, [role='main']").first.wait_for(timeout=4000)
    except Exception:
        pass

    title = await page.title()
    html = await page.content()
    return title, html


async def crawl(
    start_url: str,
    out_dir: str,
    max_pages: int | None,
    concurrency: int,
    wait_after_load_ms: int = 500,
) -> None:
    start_url = normalize_url(start_url)
    allowed_prefix = get_allowed_prefix(start_url)
    print(f"[INFO] allowed_prefix={allowed_prefix}")
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    visited = set()
    queued = set([normalize_url(start_url)])

    manifest_path = output / "manifest.json"
    manifest, existing_manifest_urls = load_existing_manifest(manifest_path)
    manifest_index = {
        entry["url"]: index
        for index, entry in enumerate(manifest)
        if isinstance(entry, dict) and "url" in entry
    }
    manifest_lock = asyncio.Lock()
    queue_put_lock = asyncio.Lock()

    def under_limit() -> bool:
        if max_pages is None or max_pages <= 0:
            return True
        return len(visited) < max_pages

    work_queue: asyncio.Queue[str | None] = asyncio.Queue()
    work_queue.put_nowait(start_url)
    queued.add(start_url)

    async def worker() -> None:
        while True:
            try:
                url = await work_queue.get()
            except asyncio.CancelledError:
                break
            if url is None:
                break
            if url in visited:
                work_queue.task_done()
                continue
            if not under_limit():
                work_queue.task_done()
                continue
            visited.add(url)
            page = await context.new_page()
            try:
                title, html = await fetch_page_content(page, url, wait_after_load_ms)
                soup = await asyncio.to_thread(BeautifulSoup, html, "lxml")
                parsed_title = extract_title(soup, title or url)

                filename = safe_filename(url)
                file_path = output / filename

                links = await extract_links(page, url, allowed_prefix)

                entry = {
                    "title": parsed_title,
                    "url": url,
                    "file": filename,
                }

                if url in existing_manifest_urls and file_path.exists():
                    async with manifest_lock:
                        upsert_manifest_entry(manifest, manifest_index, entry)
                    print(f"[SKIP] {url} ({len(visited)} pages done)")
                else:
                    filename_stem = Path(filename).stem
                    await localize_images(page, url, output, filename_stem)
                    html = await page.content()
                    markdown = await asyncio.to_thread(
                        html_to_markdown, html, url, parsed_title, allowed_prefix
                    )
                    await asyncio.to_thread(
                        file_path.write_text, markdown, encoding="utf-8"
                    )
                    async with manifest_lock:
                        upsert_manifest_entry(manifest, manifest_index, entry)
                        existing_manifest_urls.add(url)
                    print(f"[OK] {url} ({len(visited)} pages done)")

                async with queue_put_lock:
                    for link in links:
                        if link not in visited and link not in queued:
                            work_queue.put_nowait(link)
                            queued.add(link)
            except Exception as exc:
                print(f"[ERR] {url} -> {exc}")
            finally:
                try:
                    await page.close()
                except TargetClosedError:
                    pass
                except Exception as close_exc:
                    print(f"[WARN] page close failed: {url} -> {close_exc}")
                work_queue.task_done()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, ignore_https_errors=True)

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        try:
            await work_queue.join()
        finally:
            for _ in range(concurrency):
                work_queue.put_nowait(None)
            await asyncio.gather(*workers)

        await browser.close()

    manifest.sort(key=lambda x: x["url"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        required=True,
        help="Start URL to crawl",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output directory; if omitted, derive automatically from the URL",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of pages to crawl (default: no limit)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Concurrent browser pages (default: 8)",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=500,
        metavar="MS",
        help="Wait time after page load before extracting content (default: 500)",
    )
    parser.add_argument(
        "--strict-asset-ssl",
        action="store_true",
        help="Require normal SSL verification when downloading images/assets",
    )
    args = parser.parse_args()

    out_dir = args.out or derive_output_dir_name(args.url)

    global ALLOW_INSECURE_ASSET_SSL
    if args.strict_asset_ssl:
        ALLOW_INSECURE_ASSET_SSL = False

    asyncio.run(
        crawl(
            start_url=args.url,
            out_dir=out_dir,
            max_pages=args.max_pages,
            concurrency=args.concurrency,
            wait_after_load_ms=args.wait_ms,
        )
    )


if __name__ == "__main__":
    main()