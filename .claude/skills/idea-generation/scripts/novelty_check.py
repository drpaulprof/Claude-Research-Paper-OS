#!/usr/bin/env python3
"""Novelty checker for research ideas via Semantic Scholar API.

Iteratively searches literature to assess if a research idea is novel.
Self-contained: uses only stdlib.

Adapted from AI-Scientist's check_idea_novelty() in generate_ideas.py.

Usage:
    python novelty_check.py --idea "Adaptive attention pruning via gradient importance"
    python novelty_check.py --idea "..." --max-rounds 10
    python novelty_check.py --idea-file idea.json

Exit codes (a STATUS line is also printed to stderr):
    0  every search worked (the result may still list 0 similar papers)
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 still returned after retries
    5  invalid response: a reply was not the JSON the API documents
    6  other HTTP error
If any search fails, the summary says the check is INCOMPLETE and the JSON
result has "status" set to the failure type, with details in "search_errors".
A failed check must never be read as evidence that an idea is novel.
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,authors,venue,year,abstract,citationCount"

# --- Request error handling (same exit codes as the deep-research search scripts) ---
EXIT_OK = 0
EXIT_NETWORK = 3
EXIT_RATE_LIMITED = 4
EXIT_INVALID_RESPONSE = 5
EXIT_HTTP_ERROR = 6
STATUS_NAMES = {EXIT_OK: "ok", EXIT_NETWORK: "network_error", EXIT_RATE_LIMITED: "rate_limited",
                EXIT_INVALID_RESPONSE: "invalid_response", EXIT_HTTP_ERROR: "http_error"}
REQUEST_TIMEOUT = 30  # seconds per request
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


def search_semantic_scholar(query: str, limit: int = 10) -> list[dict]:
    """Search Semantic Scholar for papers matching the query.

    Returns an empty list only when the search worked and nothing matched.
    Raises RequestError on network, HTTP, rate-limit or invalid-response failures.
    """
    params = urllib.parse.urlencode({
        "query": query,
        "limit": limit,
        "fields": FIELDS,
    })
    url = f"{S2_SEARCH_URL}?{params}"
    req = urllib.request.Request(url)
    if S2_API_KEY:
        req.add_header("X-API-KEY", S2_API_KEY)

    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code in (500, 502, 503, 504)) and attempt < MAX_RETRIES:
                wait = retry_wait(e, attempt)
                label = "Rate limited" if e.code == 429 else f"HTTP {e.code}"
                print(f"  {label}, waiting {wait:g}s (retry {attempt + 1} of {MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code == 429:
                hint = "" if S2_API_KEY else " An optional free API key raises the limit."
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


def format_paper(paper: dict) -> str:
    """Format a single paper for display."""
    title = paper.get("title", "Unknown")
    year = paper.get("year", "?")
    venue = paper.get("venue", "")
    citations = paper.get("citationCount", 0)
    authors = paper.get("authors", [])
    author_str = ", ".join(a.get("name", "") for a in authors[:3])
    if len(authors) > 3:
        author_str += " et al."
    abstract = paper.get("abstract", "") or ""
    if len(abstract) > 300:
        abstract = abstract[:300] + "..."
    venue_str = f" ({venue})" if venue else ""
    return (
        f"  [{year}] {title}{venue_str}\n"
        f"  Authors: {author_str} | Citations: {citations}\n"
        f"  Abstract: {abstract}"
    )


def generate_search_queries(idea: str) -> list[str]:
    """Generate diverse search queries from an idea description."""
    # Extract key phrases by splitting on common delimiters
    words = idea.lower().split()

    # Strategy 1: Use the full idea as a query (truncated)
    queries = [" ".join(words[:15])]

    # Strategy 2: Extract noun-phrase-like chunks
    # Look for sequences of capitalized words or technical terms
    chunks = []
    current_chunk = []
    for word in idea.split():
        if len(word) > 3 and not word.lower() in {"with", "from", "that", "this", "using", "based", "through", "which", "their", "have", "been", "into", "also", "more"}:
            current_chunk.append(word)
        else:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
    if current_chunk:
        chunks.append(" ".join(current_chunk))

    # Add the longest chunks as queries
    chunks.sort(key=len, reverse=True)
    for chunk in chunks[:3]:
        if len(chunk.split()) >= 2 and chunk not in queries:
            queries.append(chunk)

    return queries[:5]


def run_novelty_check(idea: str, max_rounds: int = 5, result_limit: int = 10) -> dict:
    """Run iterative novelty checking against Semantic Scholar.

    Returns a dict with novelty assessment and similar papers found.
    """
    print(f"Checking novelty of idea:")
    print(f"  \"{idea[:200]}{'...' if len(idea) > 200 else ''}\"")
    print()

    all_papers_seen = {}
    queries_used = []
    search_errors = []   # list of {"query", "status", "message"}
    first_error_code = EXIT_OK

    # Generate initial search queries
    search_queries = generate_search_queries(idea)

    for round_num in range(1, max_rounds + 1):
        if not search_queries:
            break

        query = search_queries.pop(0)
        queries_used.append(query)
        print(f"Round {round_num}/{max_rounds}: Searching \"{query}\"")

        try:
            papers = search_semantic_scholar(query, limit=result_limit)
        except RequestError as e:
            search_errors.append({"query": query, "status": STATUS_NAMES[e.code], "message": e.message})
            if first_error_code == EXIT_OK:
                first_error_code = e.code
            print(f"  SEARCH FAILED ({STATUS_NAMES[e.code]}): {e.message}")
            print()
            if e.code in (EXIT_NETWORK, EXIT_RATE_LIMITED):
                print("  Stopping: the remaining searches would fail the same way.")
                print()
                break
            continue

        if not papers:
            print("  No results found.")
            print()
            continue

        new_papers = 0
        for paper in papers:
            title = paper.get("title", "")
            if title and title not in all_papers_seen:
                all_papers_seen[title] = paper
                new_papers += 1

        print(f"  Found {len(papers)} papers ({new_papers} new)")
        for paper in papers[:3]:
            print(format_paper(paper))
        if len(papers) > 3:
            print(f"  ... and {len(papers) - 3} more")
        print()

        # If we got results, try to refine with more specific queries
        if papers and round_num < max_rounds and not search_queries:
            # Generate follow-up queries from the most relevant paper titles
            for p in papers[:2]:
                t = p.get("title", "")
                if t and len(t.split()) >= 3:
                    search_queries.append(t[:80])

    # Rank by relevance (citation count as proxy)
    ranked = sorted(all_papers_seen.values(),
                    key=lambda p: p.get("citationCount", 0), reverse=True)

    result = {
        "idea": idea,
        "total_papers_found": len(all_papers_seen),
        "rounds_used": len(queries_used),
        "queries_used": queries_used,
        "most_cited_similar": [
            {
                "title": p.get("title", ""),
                "year": p.get("year"),
                "venue": p.get("venue", ""),
                "citations": p.get("citationCount", 0),
                "abstract": (p.get("abstract", "") or "")[:200],
            }
            for p in ranked[:10]
        ],
        "status": STATUS_NAMES[first_error_code],
        "search_errors": search_errors,
    }

    print("=" * 60)
    print(f"NOVELTY CHECK SUMMARY")
    print(f"Total unique papers found: {len(all_papers_seen)}")
    print(f"Rounds used: {len(queries_used)}/{max_rounds}")
    print()
    print("Most cited similar papers:")
    for i, p in enumerate(ranked[:5], 1):
        print(f"  {i}. [{p.get('year', '?')}] {p.get('title', '?')} "
              f"(citations: {p.get('citationCount', 0)})")
    print()
    if search_errors:
        print(f"WARNING: NOVELTY CHECK INCOMPLETE. {len(search_errors)} search(es) failed "
              f"({search_errors[0]['status']}: {search_errors[0]['message']}).")
        print("Finding few or no similar papers here does NOT mean the idea is novel.")
        print("Fix the problem above and run the check again.")
    else:
        print("NOTE: Review the papers above to determine if your idea is novel.")
        print("An idea is novel if no paper significantly overlaps with it.")
    print("=" * 60)

    return result


def main():
    parser = argparse.ArgumentParser(description="Check research idea novelty via Semantic Scholar")
    parser.add_argument("--idea", type=str, help="Research idea to check (text)")
    parser.add_argument("--idea-file", type=str, help="JSON file containing idea (must have 'Title' or 'Experiment' field)")
    parser.add_argument("--max-rounds", type=int, default=5, help="Max search rounds (default: 5)")
    parser.add_argument("--result-limit", type=int, default=10, help="Results per query (default: 10)")
    parser.add_argument("--output", type=str, help="Output JSON file for results")
    args = parser.parse_args()

    if args.idea_file:
        with open(args.idea_file, encoding="utf-8") as f:
            idea_data = json.load(f)
        idea_text = idea_data.get("Title", "") + ". " + idea_data.get("Experiment", "")
        if not idea_text.strip(". "):
            idea_text = json.dumps(idea_data)
    elif args.idea:
        idea_text = args.idea
    else:
        parser.error("Either --idea or --idea-file is required")
        return

    result = run_novelty_check(idea_text, max_rounds=args.max_rounds,
                                result_limit=args.result_limit)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    if result["search_errors"]:
        print(f"ERROR: novelty check incomplete: {result['search_errors'][0]['message']}", file=sys.stderr)
        print(f"STATUS: {result['status']} failed_searches={len(result['search_errors'])} "
              f"papers={result['total_papers_found']}", file=sys.stderr)
        sys.exit(next(code for code, name in STATUS_NAMES.items() if name == result["status"]))
    print(f"STATUS: ok papers={result['total_papers_found']}", file=sys.stderr)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
