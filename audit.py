import urllib.request, urllib.error
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urldefrag
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import csv, os, time, datetime

START = "https://dolatrammpi-coder.github.io/Quiz/index.html"
PREFIX = "/Quiz/"
HOST = urlsplit(START).netloc

# GitHub Actions me current directory me save karne ke liye relative path
REPORT = f"live_broken_links_{datetime.date.today().strftime('%Y%m%d')}.csv"

TIMEOUT = 15
MAX_PAGES = 2000

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.refs = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        for key in ("href", "src", "action"):
            val = a.get(key)
            if val:
                self.refs.append((tag, key, val))

def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; SiteLinkAudit/1.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read(2_000_000)
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), data, ""
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), "", b"", str(e)
    except Exception as e:
        return 0, url, "", b"", str(e)

def norm(url):
    return urldefrag(url)[0]

def internal_page(url):
    p = urlsplit(url)
    return p.scheme in ("http", "https") and p.netloc == HOST and p.path.startswith(PREFIX)

queue = deque([START])
seen_pages = set()
page_status = {}
targets = {}
found_on = {}
skipped = 0

print("Live website crawl shuru...")
while queue and len(seen_pages) < MAX_PAGES:
    page = norm(queue.popleft())
    if page in seen_pages:
        continue
    seen_pages.add(page)
    status, final_url, ctype, data, err = fetch(page)
    page_status[page] = (status, final_url, err)
    if not status or "html" not in ctype.lower():
        print(f"[PAGE {status or 'ERROR'}] {page}")
        continue
    parser = Parser()
    try:
        parser.feed(data.decode("utf-8", errors="replace"))
    except Exception:
        pass
    effective_base = urljoin(final_url, parser.base) if parser.base else final_url
    for tag, attr, raw in parser.refs:
        raw = raw.strip()
        if not raw or raw.startswith("#") or raw.lower().startswith(
            ("mailto:", "tel:", "javascript:", "data:", "blob:")
        ):
            continue
        target = norm(urljoin(effective_base, raw))
        if urlsplit(target).scheme not in ("http", "https"):
            continue
        targets.setdefault(target, (tag, attr))
        found_on.setdefault(target, set()).add(page)
        if internal_page(target) and target not in seen_pages:
            queue.append(target)
    print(f"[PAGE {status}] {page}")

print(f"\nPages crawled: {len(seen_pages)}")
print(f"Unique URLs discovered: {len(targets)}")
print("Ab links/assets ki live status checking...")

def check(url):
    if url in page_status:
        s, final, err = page_status[url]
        return url, s, final, err
    status, final, ctype, data, err = fetch(url)
    if status in (403, 405, 501):
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return url, r.status, r.geturl(), ""
        except urllib.error.HTTPError as e:
            return url, e.code, e.geturl(), str(e)
        except Exception as e:
            return url, 0, url, str(e)
    return url, status, final, err

results = []
with ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(check, u) for u in targets]
    for i, f in enumerate(as_completed(futures), 1):
        results.append(f.result())
        if i % 100 == 0:
            print(f"Checked {i}/{len(futures)}")

results.sort(key=lambda x: (x[1] == 200, x[0]))
with open(REPORT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["status", "url", "tag", "attribute", "found_on", "details"])
    for url, status, final, err in results:
        tag, attr = targets[url]
        if status not in (200, 301, 302, 303, 307, 308):
            sources = " ; ".join(sorted(found_on.get(url, [])))
            w.writerow([status or "NETWORK_ERROR", url, tag, attr, sources, err or final])

bad = [r for r in results if r[1] == 0 or r[1] >= 400]
redirects = [r for r in results if r[1] in (301, 302, 303, 307, 308)]

print("\n========== LIVE LINK AUDIT ==========")
print("Pages crawled:", len(seen_pages))
print("Unique URLs checked:", len(results))
print("HTTP 4xx/5xx or network errors:", len(bad))
print("Redirect responses:", len(redirects))
print("CSV report:", REPORT)
print("Note: 403/429, timeouts, and external anti-bot blocks may need manual verification.")
print("Audit complete.")
