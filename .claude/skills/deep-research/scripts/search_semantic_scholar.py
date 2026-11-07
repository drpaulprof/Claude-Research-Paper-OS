#!/usr/bin/env python3
"""Search Semantic Scholar Graph API and output JSONL paper metadata.

Self-contained: uses only stdlib (urllib, json).

Usage:
    python search_semantic_scholar.py --query "long horizon reasoning" --max-results 100
    python search_semantic_scholar.py --query "protein language model" --min-citations 10 --year-range 2020-2026
    python search_semantic_scholar.py --query "LLM agent planning" --venue NeurIPS ICML --max-results 50

Exit codes (a STATUS line is also printed to stderr):
    0  search succeeded (this includes "0 matching papers")
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 still returned after all retries
       (without an API key the shared public limit is often exhausted)
    5  invalid response: the reply was not the JSON structure the API documents
    6  other HTTP error (for example 400 bad query, 404 unknown paper ID, 5xx)

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

S2_API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,authors,abstract,year,venue,citationCount,externalIds,url,referenceCount,publicationDate"
SEARCH_LIMIT = 100  # S2 max per request
DEFAULT_TIMEOUT = 30      # seconds per request
DEFAULT_MAX_RETRIES = 3   # retries for 429 / temporary 5xx errors
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

# Top-tier AI/ML conferences (peer-reviewed)
TOP_CONFERENCES = {
    # ML core
    "NeurIPS", "ICML", "ICLR",
    # NLP
    "ACL", "EMNLP", "NAACL", "EACL", "COLING",
    # AI general
    "AAAI", "IJCAI",
    # Vision (occasionally relevant)
    "CVPR", "ICCV", "ECCV",
    # IR / data mining
    "KDD", "WWW", "SIGIR",
    # Robotics / agents
    "ICRA", "CoRL",
}

# Normalized aliases for S2 venue matching
VENUE_ALIASES = {
    "neurips": "NeurIPS", "nips": "NeurIPS",
    "icml": "ICML",
    "iclr": "ICLR",
    "acl": "ACL",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "eacl": "EACL",
    "coling": "COLING",
    "aaai": "AAAI",
    "ijcai": "IJCAI",
    "cvpr": "CVPR",
    "iccv": "ICCV",
    "eccv": "ECCV",
    "kdd": "KDD",
    "www": "WWW",
    "sigir": "SIGIR",
    "icra": "ICRA",
    "corl": "CoRL",
}


def is_peer_reviewed(venue: str) -> bool:
    """Check if a paper's venue is a recognized peer-reviewed conference."""
    if not venue:
        return False
    venue_lower = venue.lower()
    for alias in VENUE_ALIASES:
        if alias in venue_lower:
            return True
    # Also check for "journal" or "transactions" as peer-reviewed
    if any(kw in venue_lower for kw in ("journal", "transactions", "review")):
        return True
    return False


def normalize_venue(venue: str) -> str:
    """Normalize venue name to canonical form."""
    if not venue:
        return ""
    venue_lower = venue.lower()
    for alias, canonical in VENUE_ALIASES.items():
        if alias in venue_lower:
            return canonical
    return venue


def s2_request(url: str, api_key: str | None = None,
               timeout: float = DEFAULT_TIMEOUT,
               max_retries: int = DEFAULT_MAX_RETRIES) -> dict:
    """Make a request to the Semantic Scholar API with retry logic.

    Returns the decoded JSON object. Raises SearchError with a specific exit
    code on any failure; it never returns an empty result in place of an error.
    """
    headers = {"User-Agent": "deep-research/1.0"}
    if api_key:
        headers["x-api-key"] = api_key

    req = urllib.request.Request(url, headers=headers)

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code in (500, 502, 503, 504)
            if retryable and attempt < max_retries:
                wait = retry_wait(e, attempt)
                label = "Rate limited (HTTP 429)" if e.code == 429 else f"HTTP {e.code}"
                print(f"{label}, retrying in {wait:g}s (retry {attempt + 1} of {max_retries})...",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code == 429:
                hint = "" if api_key else " An optional free API key raises the limit."
                raise SearchError(EXIT_RATE_LIMITED,
                                  f"rate limited by Semantic Scholar (HTTP 429) after {max_retries} retries.{hint}")
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200].strip()
            except Exception:
                pass
            raise SearchError(EXIT_HTTP_ERROR,
                              f"Semantic Scholar returned HTTP {e.code} {e.reason}" + (f": {detail}" if detail else ""))
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise SearchError(EXIT_NETWORK, describe_network_error(e, timeout))

        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            snippet = body[:120].decode("utf-8", "replace").replace("\n", " ")
            raise SearchError(EXIT_INVALID_RESPONSE, f"reply is not valid JSON ({e}); starts with: {snippet!r}")
        if not isinstance(data, dict):
            raise SearchError(EXIT_INVALID_RESPONSE, f"expected a JSON object, got {type(data).__name__}")
        return data
    raise SearchError(EXIT_HTTP_ERROR, "request failed")  # not reached


def check_list_response(resp: dict, what: str) -> list:
    """Return resp["data"] for list endpoints, or raise on a malformed reply.

    A reply with total == 0 and no "data" key is a valid empty result.
    """
    if "data" in resp:
        if not isinstance(resp["data"], list):
            raise SearchError(EXIT_INVALID_RESPONSE, f"{what}: \"data\" is not a list")
        return resp["data"]
    if resp.get("total") == 0:
        return []
    raise SearchError(EXIT_INVALID_RESPONSE,
                      f"{what}: reply has no \"data\" field (keys: {', '.join(sorted(resp)) or 'none'})")


def parse_paper(data: dict) -> dict | None:
    """Parse an S2 paper response into our standard record format."""
    if not data or not data.get("title"):
        return None

    authors = []
    for a in data.get("authors", []) or []:
        name = a.get("name", "")
        if name:
            authors.append(name)

    external_ids = data.get("externalIds", {}) or {}
    arxiv_id = external_ids.get("ArXiv", "")

    pdf_url = ""
    if arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    abstract = data.get("abstract", "") or ""

    venue = data.get("venue", "") or ""
    reviewed = is_peer_reviewed(venue)

    return {
        "paperId": data.get("paperId", ""),
        "arxiv_id": arxiv_id,
        "title": data["title"],
        "authors": authors,
        "abstract": " ".join(abstract.split()),
        "year": data.get("year"),
        "venue": venue,
        "venue_normalized": normalize_venue(venue),
        "peer_reviewed": reviewed,
        "citationCount": data.get("citationCount", 0) or 0,
        "referenceCount": data.get("referenceCount", 0) or 0,
        "url": data.get("url", ""),
        "publicationDate": data.get("publicationDate", ""),
        "pdf_url": pdf_url,
        "source": "semantic_scholar",
    }


def search_papers(
    query: str,
    max_results: int = 100,
    year_range: str | None = None,
    min_citations: int = 0,
    venue_filter: list[str] | None = None,
    peer_reviewed_only: bool = False,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[list[dict], SearchError | None]:
    """Search for papers and return (papers, error).

    If peer_reviewed_only=True, only returns papers from recognized conferences/journals.
    This fetches more results from S2 to compensate for filtering.
    error is None when every request succeeded. If a later page fails, the
    papers from earlier pages are returned together with the error.
    """
    all_papers = []
    seen_ids = set()

    # When filtering peer-reviewed, fetch more to compensate
    fetch_multiplier = 3 if peer_reviewed_only else 1
    fetch_max = max_results * fetch_multiplier

    offset = 0
    while offset < fetch_max and len(all_papers) < max_results:
        limit = min(SEARCH_LIMIT, fetch_max - offset)
        params = {
            "query": query,
            "offset": offset,
            "limit": limit,
            "fields": FIELDS,
        }
        if year_range:
            params["year"] = year_range

        url = f"{S2_API}/paper/search?{urllib.parse.urlencode(params)}"

        try:
            resp = s2_request(url, api_key, timeout, max_retries)
            papers = check_list_response(resp, "paper search")
        except SearchError as e:
            e.message = f"{e.message} (at result offset {offset})"
            return all_papers, e

        if not papers:
            break

        for item in papers:
            if len(all_papers) >= max_results:
                break

            paper = parse_paper(item)
            if not paper:
                continue

            # Dedup by paperId
            pid = paper["paperId"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            # Citation filter
            if paper["citationCount"] < min_citations:
                continue

            # Venue filter (explicit list)
            if venue_filter:
                paper_venue = (paper.get("venue", "") or "").lower()
                if not any(v.lower() in paper_venue for v in venue_filter):
                    continue

            # Peer-reviewed filter
            if peer_reviewed_only and not paper.get("peer_reviewed", False):
                continue

            all_papers.append(paper)

        total = resp.get("total", 0)
        offset += limit
        if offset >= total:
            break

        # Rate limit: ~10 requests per second for public API
        time.sleep(0.5)

    return all_papers, None


def get_paper_details(paper_id: str, api_key: str | None = None,
                      timeout: float = DEFAULT_TIMEOUT,
                      max_retries: int = DEFAULT_MAX_RETRIES) -> dict | None:
    """Get detailed info for a single paper by S2 paperId or arxiv:<id>.

    Raises SearchError on failure (it no longer hides errors behind None).
    """
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}?fields={FIELDS}"
    data = s2_request(url, api_key, timeout, max_retries)
    return parse_paper(data)


def get_citations(paper_id: str, max_results: int = 50, api_key: str | None = None,
                  timeout: float = DEFAULT_TIMEOUT,
                  max_retries: int = DEFAULT_MAX_RETRIES) -> list[dict]:
    """Get papers that cite the given paper. Raises SearchError on failure."""
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}/citations?fields={FIELDS}&limit={min(max_results, 1000)}"
    resp = s2_request(url, api_key, timeout, max_retries)
    results = []
    for item in check_list_response(resp, "citations"):
        citing = (item or {}).get("citingPaper", {}) if isinstance(item, dict) else {}
        paper = parse_paper(citing)
        if paper:
            results.append(paper)
    return results[:max_results]


def get_references(paper_id: str, max_results: int = 50, api_key: str | None = None,
                   timeout: float = DEFAULT_TIMEOUT,
                   max_retries: int = DEFAULT_MAX_RETRIES) -> list[dict]:
    """Get papers referenced by the given paper. Raises SearchError on failure."""
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}/references?fields={FIELDS}&limit={min(max_results, 1000)}"
    resp = s2_request(url, api_key, timeout, max_retries)
    results = []
    for item in check_list_response(resp, "references"):
        cited = (item or {}).get("citedPaper", {}) if isinstance(item, dict) else {}
        paper = parse_paper(cited)
        if paper:
            results.append(paper)
    return results[:max_results]


def main():
    # Handle --list-conferences early (no other args needed)
    if "--list-conferences" in sys.argv:
        print("Recognized top conferences:")
        for conf in sorted(TOP_CONFERENCES):
            print(f"  {conf}")
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Search Semantic Scholar and output JSONL")
    parser.add_argument("--query", required=True, help="Search keywords")
    parser.add_argument("--max-results", type=int, default=100, help="Max papers to return")
    parser.add_argument("--min-citations", type=int, default=0, help="Minimum citation count")
    parser.add_argument("--year-range", help="Year range filter (e.g. 2020-2026)")
    parser.add_argument("--venue", nargs="*", help="Venue filter (e.g. NeurIPS ICML)")
    parser.add_argument("--peer-reviewed-only", action="store_true",
                        help="Only return papers from peer-reviewed conferences/journals")
    parser.add_argument("--top-conferences", action="store_true",
                        help="Shorthand for --venue with all top AI conferences")
    parser.add_argument("--list-conferences", action="store_true",
                        help="Print the list of recognized top conferences and exit")
    parser.add_argument("--api-key", help="S2 API key (optional, increases rate limits)")
    parser.add_argument("--citations-of", help="Get papers citing this paper ID")
    parser.add_argument("--references-of", help="Get papers referenced by this paper ID")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"Seconds to wait for each request (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help=f"Retries after HTTP 429/5xx (default {DEFAULT_MAX_RETRIES})")
    args = parser.parse_args()
    retries = max(args.max_retries, 0)

    if args.list_conferences:
        print("Recognized top conferences:")
        for conf in sorted(TOP_CONFERENCES):
            print(f"  {conf}")
        sys.exit(0)

    # --top-conferences expands to venue filter with all top conferences
    venue_filter = args.venue
    if args.top_conferences:
        venue_filter = list(TOP_CONFERENCES)

    error = None
    try:
        if args.citations_of:
            papers = get_citations(args.citations_of, args.max_results, args.api_key, args.timeout, retries)
        elif args.references_of:
            papers = get_references(args.references_of, args.max_results, args.api_key, args.timeout, retries)
        else:
            papers = None
    except SearchError as e:
        papers, error = [], e
    if papers is None:
        papers, error = search_papers(
            query=args.query,
            max_results=args.max_results,
            year_range=args.year_range,
            min_citations=args.min_citations,
            venue_filter=venue_filter,
            peer_reviewed_only=args.peer_reviewed_only,
            api_key=args.api_key,
            timeout=args.timeout,
            max_retries=retries,
        )

    if error and not papers:
        # Nothing retrieved: do not create or overwrite the output file.
        print(f"ERROR: Semantic Scholar request failed: {error.message}", file=sys.stderr)
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
        print(f"ERROR: Semantic Scholar search stopped early: {error.message}", file=sys.stderr)
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
