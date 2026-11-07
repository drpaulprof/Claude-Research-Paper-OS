#!/usr/bin/env python3
"""Harvest missing citations for a LaTeX paper.

Scans .tex for under-cited claims (sentences with factual assertions but no \\cite),
generates search queries, calls Semantic Scholar API, and outputs candidate BibTeX entries.

Self-contained: uses only stdlib.

Usage:
    python harvest_citations.py --tex main.tex --bib references.bib --output candidates.bib
    python harvest_citations.py --tex main.tex --bib references.bib --max-rounds 10 --dry-run
    python harvest_citations.py --tex main.tex --bib references.bib --output candidates.bib --verbose

Exit codes (a STATUS line is also printed to stderr):
    0  every search worked (this includes "0 candidates found")
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 still returned after retries
    5  invalid response: a reply was not the JSON the API documents
    6  other HTTP error
On a network failure or an exhausted rate limit the remaining searches are
skipped, because they would fail the same way. Candidates found before the
failure are still written, and the exit code is nonzero.
"""

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# --- Request error handling (same exit codes as the deep-research search scripts) ---
EXIT_OK = 0
EXIT_NETWORK = 3
EXIT_RATE_LIMITED = 4
EXIT_INVALID_RESPONSE = 5
EXIT_HTTP_ERROR = 6
STATUS_NAMES = {EXIT_OK: "ok", EXIT_NETWORK: "network_error", EXIT_RATE_LIMITED: "rate_limited",
                EXIT_INVALID_RESPONSE: "invalid_response", EXIT_HTTP_ERROR: "http_error"}
REQUEST_TIMEOUT = 15  # seconds per request
MAX_RETRIES = 3       # retries for 429 / temporary 5xx errors
MAX_RETRY_WAIT = 60   # never wait longer than this between retries


class RequestError(Exception):
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

CLAIM_PATTERNS = [
    r"(?:has been shown|have been shown|was shown|were shown|is known|are known)",
    r"(?:recent(?:ly)?|prior|previous) (?:work|studies?|research|methods?|approaches?)",
    r"(?:state[- ]of[- ]the[- ]art|SOTA|benchmark)",
    r"(?:outperform|surpass|exceed|achieve|obtain|report|demonstrate|propose|introduce)",
    r"(?:widely used|commonly used|popular|well-known|established)",
    r"(?:inspired by|motivated by|based on|building on|following)",
    r"(?:\d+\.?\d*)\s*%",  # Numbers that likely need citation
]

COMMON_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "with", "from", "by", "as",
    "we", "our", "this", "that", "these", "those", "it", "its",
}


def extract_existing_keys(bib_content: str) -> set[str]:
    """Extract all BibTeX keys from .bib file."""
    return set(re.findall(r"@\w+\{([^,]+),", bib_content))


def extract_cited_keys(tex_content: str) -> set[str]:
    """Extract all cited keys from .tex file."""
    keys = set()
    for match in re.findall(r"\\cite[a-z]*\{([^}]+)\}", tex_content):
        for key in match.split(","):
            keys.add(key.strip())
    return keys


def find_uncited_claims(tex_content: str) -> list[dict]:
    """Find sentences with factual claims that lack citations."""
    # Remove comments
    text = re.sub(r"%.*$", "", tex_content, flags=re.MULTILINE)
    # Remove math environments
    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    text = re.sub(r"\$.*?\$", "", text)
    # Remove commands but keep text
    text = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", text)

    sentences = re.split(r"(?<=[.!?])\s+", text)
    claims = []

    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 30:
            continue
        # Skip if already has a citation
        if re.search(r"\\cite", sent):
            continue
        # Check for claim patterns
        for pattern in CLAIM_PATTERNS:
            if re.search(pattern, sent, re.IGNORECASE):
                # Extract key terms for search query
                words = re.findall(r"[A-Za-z]+", sent)
                content_words = [w for w in words if w.lower() not in COMMON_WORDS and len(w) > 2]
                query = " ".join(content_words[:8])
                claims.append({
                    "sentence": sent[:200],
                    "pattern": pattern,
                    "query": query,
                })
                break

    return claims


def search_semantic_scholar(query: str, limit: int = 3, api_key: str = "") -> list[dict]:
    """Search Semantic Scholar for papers matching the query.

    Returns an empty list only when the search worked and nothing matched.
    Raises RequestError on network, HTTP, rate-limit or invalid-response failures.
    """
    params = urllib.parse.urlencode({
        "query": query,
        "limit": limit,
        "fields": "title,authors,year,venue,externalIds,citationCount,abstract",
    })
    url = f"{S2_API}?{params}"
    headers = {"User-Agent": "SkillScript/1.0"}
    if api_key:
        headers["x-api-key"] = api_key

    req = urllib.request.Request(url, headers=headers)
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code in (500, 502, 503, 504)) and attempt < MAX_RETRIES:
                wait = retry_wait(e, attempt)
                print(f"  HTTP {e.code} from Semantic Scholar, retrying in {wait:g}s "
                      f"(retry {attempt + 1} of {MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code == 429:
                hint = "" if api_key else " An optional free API key raises the limit."
                raise RequestError(EXIT_RATE_LIMITED,
                                   f"rate limited by Semantic Scholar (HTTP 429) after {MAX_RETRIES} retries.{hint}")
            raise RequestError(EXIT_HTTP_ERROR, f"Semantic Scholar returned HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise RequestError(EXIT_NETWORK, describe_network_error(e, REQUEST_TIMEOUT))

    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RequestError(EXIT_INVALID_RESPONSE, f"Semantic Scholar reply is not valid JSON ({e})")
    if not isinstance(data, dict):
        raise RequestError(EXIT_INVALID_RESPONSE, "Semantic Scholar reply is not a JSON object")
    if "data" not in data:
        if data.get("total") == 0:
            return []  # valid empty result
        raise RequestError(EXIT_INVALID_RESPONSE,
                           f"Semantic Scholar reply has no \"data\" field (keys: {', '.join(sorted(data)) or 'none'})")
    if not isinstance(data["data"], list):
        raise RequestError(EXIT_INVALID_RESPONSE, "Semantic Scholar reply: \"data\" is not a list")
    return data["data"]


def make_bibtex_key(paper: dict) -> str:
    """Generate a BibTeX key from a Semantic Scholar paper."""
    authors = paper.get("authors", [])
    family = ""
    if authors:
        name = authors[0].get("name", "")
        parts = name.split()
        family = re.sub(r"[^a-zA-Z]", "", parts[-1]) if parts else ""
    year = str(paper.get("year", ""))
    title = paper.get("title", "")
    title_words = re.findall(r"[A-Za-z]+", title)
    content_words = [w.lower() for w in title_words if w.lower() not in COMMON_WORDS]
    title_part = content_words[0] if content_words else ""
    return family.lower() + year + title_part


def paper_to_bibtex(paper: dict, key: str) -> str:
    """Convert a Semantic Scholar paper to a BibTeX entry."""
    title = paper.get("title", "")
    authors = " and ".join(a.get("name", "") for a in paper.get("authors", []))
    year = str(paper.get("year", ""))
    venue = paper.get("venue", "")
    doi = ""
    ext_ids = paper.get("externalIds", {})
    if ext_ids:
        doi = ext_ids.get("DOI", "")

    if venue:
        entry_type = "inproceedings"
        lines = [f"@inproceedings{{{key},"]
        lines.append(f"  author = {{{authors}}},")
        lines.append(f"  title = {{{title}}},")
        lines.append(f"  booktitle = {{{venue}}},")
        lines.append(f"  year = {{{year}}},")
    else:
        entry_type = "article"
        lines = [f"@article{{{key},"]
        lines.append(f"  author = {{{authors}}},")
        lines.append(f"  title = {{{title}}},")
        lines.append(f"  year = {{{year}}},")

    if doi:
        lines.append(f"  doi = {{{doi}}},")
    lines.append("}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Harvest missing citations for a LaTeX paper")
    parser.add_argument("--tex", required=True, help="Main .tex file")
    parser.add_argument("--bib", required=True, help=".bib file")
    parser.add_argument("--output", "-o", help="Output .bib file for candidates")
    parser.add_argument("--max-rounds", type=int, default=10, help="Max harvesting rounds (default: 10)")
    parser.add_argument("--api-key", default="", help="Semantic Scholar API key")
    parser.add_argument("--dry-run", action="store_true", help="Only show claims, don't search")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress")
    args = parser.parse_args()

    with open(args.tex, encoding="utf-8", errors="replace") as f:
        tex_content = f.read()
    with open(args.bib, encoding="utf-8", errors="replace") as f:
        bib_content = f.read()

    existing_keys = extract_existing_keys(bib_content)
    cited_keys = extract_cited_keys(tex_content)
    claims = find_uncited_claims(tex_content)

    print(f"Existing bib entries: {len(existing_keys)}", file=sys.stderr)
    print(f"Cited keys in tex: {len(cited_keys)}", file=sys.stderr)
    print(f"Uncited claims found: {len(claims)}", file=sys.stderr)

    if args.dry_run:
        print(f"\n## Uncited Claims (top {min(len(claims), args.max_rounds)}):")
        for i, claim in enumerate(claims[:args.max_rounds]):
            print(f"\n[{i+1}] {claim['sentence'][:120]}...")
            print(f"    Pattern: {claim['pattern']}")
            print(f"    Query: {claim['query']}")
        sys.exit(0)

    if not claims:
        print("No uncited claims found.", file=sys.stderr)
        sys.exit(0)

    candidates = []
    used_keys = set(existing_keys)
    rounds = min(len(claims), args.max_rounds)
    failures = []          # (query, RequestError)
    searches_done = 0
    stopped_early = False

    for i, claim in enumerate(claims[:rounds]):
        print(f"\n[{i+1}/{rounds}] Searching for: {claim['query'][:60]}...", file=sys.stderr)
        try:
            papers = search_semantic_scholar(claim["query"], limit=3, api_key=args.api_key)
        except RequestError as e:
            failures.append((claim["query"], e))
            print(f"  SEARCH FAILED ({STATUS_NAMES[e.code]}): {e.message}", file=sys.stderr)
            if e.code in (EXIT_NETWORK, EXIT_RATE_LIMITED):
                stopped_early = True
                print("  Stopping: the remaining searches would fail the same way.", file=sys.stderr)
                break
            continue
        searches_done += 1
        time.sleep(1)  # Rate limiting

        if not papers:
            if args.verbose:
                print(f"  No results found.", file=sys.stderr)
            continue

        # Pick the most cited result
        papers.sort(key=lambda p: p.get("citationCount", 0), reverse=True)
        best = papers[0]
        key = make_bibtex_key(best)
        if not key or key in used_keys:
            if args.verbose:
                print(f"  Skipping duplicate key: {key}", file=sys.stderr)
            continue

        used_keys.add(key)
        bibtex = paper_to_bibtex(best, key)
        candidates.append(bibtex)

        if args.verbose:
            print(f"  Found: {best.get('title', '')[:60]}", file=sys.stderr)
            print(f"  Key: {key}, Citations: {best.get('citationCount', 0)}", file=sys.stderr)

    print(f"\nHarvested {len(candidates)} candidate citations.", file=sys.stderr)

    if candidates:
        output_text = "\n\n".join(candidates) + "\n"
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_text)
            print(f"Written to: {args.output}", file=sys.stderr)
        else:
            print(output_text)

    if failures:
        skipped = rounds - searches_done - len(failures)
        first = failures[0][1]
        print(f"ERROR: {len(failures)} of {rounds} searches failed"
              + (f" and {skipped} were skipped" if stopped_early and skipped > 0 else "")
              + f". First failure: {first.message}", file=sys.stderr)
        print("The candidate list is incomplete. Do not read it as 'no citations exist'.", file=sys.stderr)
        print(f"STATUS: {STATUS_NAMES[first.code]} searches_ok={searches_done} failed={len(failures)} "
              f"candidates={len(candidates)}", file=sys.stderr)
        sys.exit(first.code)

    print(f"STATUS: ok searches_ok={searches_done} candidates={len(candidates)}", file=sys.stderr)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
