#!/usr/bin/env python3
"""Search arxiv via the Atom API and output JSONL paper metadata.

Self-contained: uses only stdlib (urllib, xml.etree).

Usage:
    python search_arxiv.py --query "long context reasoning" --max-results 50
    python search_arxiv.py --query "protein language model" --categories q-bio.BM cs.LG --max-results 100
    python search_arxiv.py --query "LLM agent" --sort-by lastUpdatedDate --start-date 2024-01-01

Exit codes (a STATUS line is also printed to stderr):
    0  search succeeded (this includes "0 matching papers")
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 or 503 still returned after all retries
    5  invalid response: the reply was not a readable arXiv Atom feed
    6  other HTTP error, or an error message returned by the arXiv API

On failure nothing is written to --output, so an empty file always means
"the search ran and found nothing". If later pages fail after earlier pages
succeeded, the papers already retrieved are written and the exit code is
still nonzero (the STATUS line says "partial").
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

# HTTPS: plain HTTP is refused by many proxies and corporate networks.
ARXIV_API = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT = 30      # seconds per request
DEFAULT_MAX_RETRIES = 3   # retries for 429 / 503 / other temporary 5xx errors
MAX_RETRY_WAIT = 60       # never wait longer than this between retries

EXIT_OK = 0
EXIT_NETWORK = 3
EXIT_RATE_LIMITED = 4
EXIT_INVALID_RESPONSE = 5
EXIT_HTTP_ERROR = 6
STATUS_NAMES = {
    EXIT_NETWORK: "network_error",
    EXIT_RATE_LIMITED: "rate_limited",
    EXIT_INVALID_RESPONSE: "invalid_response",
    EXIT_HTTP_ERROR: "http_error",
}


class SearchError(Exception):
    """A failed request, with the exit code that describes the failure."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def describe_network_error(err: Exception, timeout: float) -> str:
    """Turn a low-level network exception into a plain explanation."""
    reason = getattr(err, "reason", err)
    text = str(reason)
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return f"request timed out after {timeout}s"
    if "Tunnel connection failed" in text:
        return f"blocked by the network proxy ({text}); the host is probably not on the allowed list"
    if "Name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text:
        return f"DNS lookup failed ({text}); there may be no internet access, or the host is blocked"
    return f"network error: {text}"


def retry_wait(err: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After if given, else 2, 4, 8..."""
    header = err.headers.get("Retry-After") if err.headers else None
    if header:
        try:
            return min(max(float(header), 0.0), MAX_RETRY_WAIT)
        except ValueError:
            pass
    return min(2 ** (attempt + 1), MAX_RETRY_WAIT)
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

SORT_MAP = {
    "relevance": "relevance",
    "lastUpdatedDate": "lastUpdatedDate",
    "submittedDate": "submittedDate",
}


def build_query(keywords: str, categories: list[str] | None = None) -> str:
    """Build an arxiv search query string."""
    parts = []
    # keyword search across all fields
    parts.append(f"all:{keywords}")
    if categories:
        cat_query = " OR ".join(f"cat:{c}" for c in categories)
        parts.append(f"({cat_query})")
    return " AND ".join(parts)


def fetch_results(
    query: str,
    start: int,
    max_results: int,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> bytes:
    """Fetch a page of results from the arxiv API.

    Raises SearchError with a specific exit code on any failure.
    """
    params = {
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "deep-research/1.0"})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code in (500, 502, 503, 504)
            if retryable and attempt < max_retries:
                wait = retry_wait(e, attempt)
                print(f"HTTP {e.code} from arXiv, retrying in {wait:g}s "
                      f"(retry {attempt + 1} of {max_retries})...", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (429, 503):
                raise SearchError(EXIT_RATE_LIMITED,
                                  f"rate limited by arXiv (HTTP {e.code}) after {max_retries} retries")
            raise SearchError(EXIT_HTTP_ERROR, f"arXiv returned HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise SearchError(EXIT_NETWORK, describe_network_error(e, timeout))
    raise SearchError(EXIT_HTTP_ERROR, "request failed")  # not reached


def parse_entry(entry: ET.Element) -> dict:
    """Parse a single Atom entry into a paper record."""
    def text(tag: str, ns: str = "atom") -> str:
        el = entry.find(f"{ns}:{tag}", NS) if ns else entry.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    # Extract arxiv ID from the entry id URL
    entry_id = text("id")
    arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else entry_id

    # Authors
    authors = []
    for author_el in entry.findall("atom:author", NS):
        name_el = author_el.find("atom:name", NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    # Categories
    categories = []
    for cat_el in entry.findall("arxiv:primary_category", NS):
        term = cat_el.get("term", "")
        if term:
            categories.append(term)
    for cat_el in entry.findall("atom:category", NS):
        term = cat_el.get("term", "")
        if term and term not in categories:
            categories.append(term)

    # PDF link
    pdf_url = ""
    for link_el in entry.findall("atom:link", NS):
        if link_el.get("title") == "pdf":
            pdf_url = link_el.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    # Comment (often contains page count, conference info)
    comment = text("comment", "arxiv")

    # Abstract: normalize whitespace
    abstract = " ".join(text("summary").split())

    published = text("published")
    year = int(published[:4]) if len(published) >= 4 else None

    return {
        "arxiv_id": arxiv_id,
        "title": " ".join(text("title").split()),
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "published": published,
        "updated": text("updated"),
        "categories": categories,
        "pdf_url": pdf_url,
        "comment": comment,
        "source": "arxiv",
    }


def parse_feed(xml_data: bytes) -> list[ET.Element]:
    """Parse an arXiv Atom feed and return its entries.

    Raises SearchError(EXIT_INVALID_RESPONSE) if the reply is not an Atom feed,
    and SearchError(EXIT_HTTP_ERROR) if arXiv sent back an error entry
    (arXiv reports bad queries as HTTP 200 with a single "Error" entry).
    """
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        snippet = xml_data[:120].decode("utf-8", "replace").replace("\n", " ")
        raise SearchError(EXIT_INVALID_RESPONSE, f"reply is not valid XML ({e}); starts with: {snippet!r}")
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise SearchError(EXIT_INVALID_RESPONSE, f"reply is XML but not an Atom feed (root element {root.tag!r})")
    entries = root.findall("atom:entry", NS)
    for entry in entries:
        entry_id = entry.findtext("atom:id", default="", namespaces=NS)
        if "/api/errors" in entry_id:
            detail = " ".join((entry.findtext("atom:summary", default="", namespaces=NS) or "").split())
            raise SearchError(EXIT_HTTP_ERROR, f"arXiv API reported an error: {detail or entry_id}")
    return entries


def search(
    keywords: str,
    categories: list[str] | None = None,
    max_results: int = 50,
    sort_by: str = "relevance",
    start_date: str | None = None,
    end_date: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[list[dict], SearchError | None]:
    """Run a full paginated search and return (papers, error).

    error is None when every request succeeded. If a later page fails, the
    papers from earlier pages are returned together with the error.
    """
    query = build_query(keywords, categories)
    page_size = min(max_results, 100)  # arxiv max per request
    all_papers = []
    seen_ids = set()

    for start in range(0, max_results, page_size):
        fetch_count = min(page_size, max_results - start)
        try:
            xml_data = fetch_results(query, start, fetch_count, sort_by,
                                     timeout=timeout, max_retries=max_retries)
            entries = parse_feed(xml_data)
        except SearchError as e:
            e.message = f"{e.message} (at result offset {start})"
            return all_papers, e

        if not entries:
            break

        for entry in entries:
            paper = parse_entry(entry)
            if not paper["title"] or paper["arxiv_id"] in seen_ids:
                continue

            # Date filtering
            if start_date or end_date:
                pub = paper["published"][:10]  # YYYY-MM-DD
                if start_date and pub < start_date:
                    continue
                if end_date and pub > end_date:
                    continue

            seen_ids.add(paper["arxiv_id"])
            all_papers.append(paper)

        # Respect rate limit: 1 request per 3 seconds
        if start + page_size < max_results:
            time.sleep(3)

    return all_papers, None


def main():
    parser = argparse.ArgumentParser(description="Search arxiv and output JSONL")
    parser.add_argument("--query", required=True, help="Search keywords")
    parser.add_argument("--max-results", type=int, default=50, help="Max papers to return")
    parser.add_argument("--categories", nargs="*", help="arxiv categories (e.g. cs.AI cs.CL q-bio.BM)")
    parser.add_argument("--sort-by", choices=list(SORT_MAP.keys()), default="relevance")
    parser.add_argument("--start-date", help="Filter: earliest publication date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Filter: latest publication date (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"Seconds to wait for each request (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help=f"Retries after HTTP 429/5xx (default {DEFAULT_MAX_RETRIES})")
    args = parser.parse_args()

    papers, error = search(
        keywords=args.query,
        categories=args.categories,
        max_results=args.max_results,
        sort_by=SORT_MAP[args.sort_by],
        start_date=args.start_date,
        end_date=args.end_date,
        timeout=args.timeout,
        max_retries=max(args.max_retries, 0),
    )

    if error and not papers:
        # Nothing retrieved: do not create or overwrite the output file.
        print(f"ERROR: arXiv search failed: {error.message}", file=sys.stderr)
        print(f"STATUS: {STATUS_NAMES[error.code]} papers=0", file=sys.stderr)
        sys.exit(error.code)

    out = open(args.output, "w") if args.output else sys.stdout
    try:
        for paper in papers:
            out.write(json.dumps(paper, ensure_ascii=False) + "\n")
    finally:
        if args.output:
            out.close()

    if error:
        print(f"ERROR: arXiv search stopped early: {error.message}", file=sys.stderr)
        print(f"Found {len(papers)} papers before the failure (partial results written)", file=sys.stderr)
        print(f"STATUS: {STATUS_NAMES[error.code]} partial papers={len(papers)}", file=sys.stderr)
        sys.exit(error.code)

    print(f"Found {len(papers)} papers", file=sys.stderr)
    if not papers:
        print("Search succeeded but no papers matched the query and filters.", file=sys.stderr)
    print(f"STATUS: ok papers={len(papers)}", file=sys.stderr)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
