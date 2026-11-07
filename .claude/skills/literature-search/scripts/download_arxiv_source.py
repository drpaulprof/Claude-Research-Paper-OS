#!/usr/bin/env python3
"""Download arXiv paper source by title, extract .tex content.

Searches the arXiv API by title, downloads the source tarball,
and extracts .tex files into a local directory.

Self-contained: uses only stdlib (urllib + xml.etree instead of feedparser).

Usage:
    python download_arxiv_source.py --title "Attention Is All You Need" --output-dir arxiv_papers/
    python download_arxiv_source.py --title "BERT" --max-results 3 --output-dir arxiv_papers/
    python download_arxiv_source.py --arxiv-id 1706.03762 --output-dir arxiv_papers/

Exit codes (a STATUS line is also printed to stderr):
    0  source downloaded and saved
    1  request worked, but nothing to download (no matching paper, no arXiv ID,
       or the source archive contains no .tex files)
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 or 503 still returned after retries
    5  invalid response: the reply was not a readable Atom feed or tar.gz archive
    6  other HTTP error, or an error message returned by the arXiv API
"""

import argparse
import json
import os
import re
import socket
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_SRC = "https://arxiv.org/src"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}

# --- Request error handling (same exit codes as the deep-research search scripts) ---
EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_NETWORK = 3
EXIT_RATE_LIMITED = 4
EXIT_INVALID_RESPONSE = 5
EXIT_HTTP_ERROR = 6
STATUS_NAMES = {EXIT_OK: "ok", EXIT_NOT_FOUND: "not_found", EXIT_NETWORK: "network_error",
                EXIT_RATE_LIMITED: "rate_limited", EXIT_INVALID_RESPONSE: "invalid_response",
                EXIT_HTTP_ERROR: "http_error"}
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


def http_get(url: str, timeout: float, what: str) -> bytes:
    """GET a URL with retries. Raises RequestError with a specific code on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "SkillScript/1.0"})
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code in (500, 502, 503, 504)) and attempt < MAX_RETRIES:
                wait = retry_wait(e, attempt)
                print(f"HTTP {e.code} from {what}, retrying in {wait:g}s "
                      f"(retry {attempt + 1} of {MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (429, 503):
                raise RequestError(EXIT_RATE_LIMITED, f"rate limited by {what} (HTTP {e.code}) after {MAX_RETRIES} retries")
            if e.code == 404:
                raise RequestError(EXIT_NOT_FOUND, f"{what} returned HTTP 404 Not Found")
            raise RequestError(EXIT_HTTP_ERROR, f"{what} returned HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise RequestError(EXIT_NETWORK, f"{what}: {describe_network_error(e, timeout)}")
    raise RequestError(EXIT_HTTP_ERROR, f"{what}: request failed")  # not reached


def build_title_query(query: str, search_field: str = "ti") -> str:
    """Build an arXiv search_query for a title.

    The text is encoded exactly once later by urlencode(). Multiword titles are
    sent as a quoted phrase, because arXiv treats unquoted words separately and
    ignores common words, so e.g. 'ti:a AND ti:is' never matches. Characters
    that are query syntax for arXiv (quotes, brackets, colons) are removed.
    """
    cleaned = " ".join(re.sub(r'["():]', " ", query).split())
    if " " in cleaned:
        return f'{search_field}:"{cleaned}"'
    return f"{search_field}:{cleaned}"


def search_arxiv(query: str, max_results: int = 5, search_field: str = "ti") -> list[dict]:
    """Search arXiv API and return paper metadata.

    Returns an empty list only when the search worked and nothing matched.
    Raises RequestError on network, HTTP, rate-limit or invalid-response failures.
    """
    params = urllib.parse.urlencode({
        # Encoded once, here. (Quoting the text before urlencode() turned a
        # space into "%2520", so multiword titles never matched.)
        "search_query": build_title_query(query, search_field),
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"

    try:
        xml_data = http_get(url, timeout=30, what="arXiv API")
    except RequestError as e:
        # A 404 from the search endpoint means a wrong URL, not "no matching paper".
        if e.code == EXIT_NOT_FOUND:
            raise RequestError(EXIT_HTTP_ERROR, e.message)
        raise

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        raise RequestError(EXIT_INVALID_RESPONSE, f"arXiv API reply is not valid XML ({e})")
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise RequestError(EXIT_INVALID_RESPONSE, f"arXiv API reply is not an Atom feed (root element {root.tag!r})")
    papers = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        # arXiv reports bad queries as HTTP 200 with a single "Error" entry.
        entry_id = entry.findtext("atom:id", default="", namespaces=ARXIV_NS)
        if "/api/errors" in entry_id:
            detail = " ".join((entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "").split())
            raise RequestError(EXIT_HTTP_ERROR, f"arXiv API reported an error: {detail or entry_id}")

        title_el = entry.find("atom:title", ARXIV_NS)
        title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""

        summary_el = entry.find("atom:summary", ARXIV_NS)
        summary = summary_el.text.strip() if summary_el is not None else ""

        authors = []
        for author in entry.findall("atom:author", ARXIV_NS):
            name_el = author.find("atom:name", ARXIV_NS)
            if name_el is not None:
                authors.append(name_el.text)

        published_el = entry.find("atom:published", ARXIV_NS)
        published = published_el.text if published_el is not None else ""

        abs_link = ""
        pdf_link = ""
        for link in entry.findall("atom:link", ARXIV_NS):
            href = link.get("href", "")
            link_type = link.get("type", "")
            rel = link.get("rel", "")
            if link_type == "application/pdf":
                pdf_link = href
            elif rel == "alternate":
                abs_link = href

        arxiv_id = ""
        id_el = entry.find("atom:id", ARXIV_NS)
        if id_el is not None:
            m = re.search(r"abs/(.+)", id_el.text)
            if m:
                arxiv_id = m.group(1)

        papers.append({
            "title": title,
            "authors": authors,
            "published": published,
            "summary": summary,
            "abs_link": abs_link,
            "pdf_link": pdf_link,
            "arxiv_id": arxiv_id,
        })

    return papers


def download_source(arxiv_id: str, output_dir: str) -> str:
    """Download arXiv source tarball and extract .tex files.

    Returns the path to the extracted content.
    Raises RequestError on failure (network, HTTP, rate limit, unreadable
    archive) and RequestError(EXIT_NOT_FOUND) if the archive has no .tex files.
    """
    # Strip version suffix for source download
    base_id = re.sub(r"v\d+$", "", arxiv_id)
    source_url = f"{ARXIV_SRC}/{base_id}"

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", arxiv_id)

    tar_data = http_get(source_url, timeout=60, what=f"arXiv source download for {arxiv_id}")
    os.makedirs(output_dir, exist_ok=True)

    # Save tarball to temp file, then extract
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp.write(tar_data)
        tmp_path = tmp.name

    tex_contents = []
    try:
        with tarfile.open(tmp_path, "r:gz") as tar:
            tex_files = [m for m in tar.getmembers() if m.name.endswith(".tex")]
            for tex_file in tex_files:
                f = tar.extractfile(tex_file)
                if f is not None:
                    try:
                        content = f.read().decode("utf-8")
                    except UnicodeDecodeError:
                        f.seek(0)
                        content = f.read().decode("latin-1")
                    tex_contents.append((tex_file.name, content))
    except (tarfile.TarError, EOFError, OSError) as e:
        raise RequestError(EXIT_INVALID_RESPONSE,
                           f"source for {arxiv_id} is not a readable tar.gz archive ({e}); "
                           "some papers only offer a PDF or a single gzipped file")
    finally:
        os.unlink(tmp_path)

    if not tex_contents:
        raise RequestError(EXIT_NOT_FOUND, f"No .tex files found in source for {arxiv_id}")

    # Find main tex file (contains \documentclass or largest file)
    main_file = None
    for name, content in tex_contents:
        if r"\documentclass" in content:
            main_file = (name, content)
            break
    if main_file is None:
        main_file = max(tex_contents, key=lambda x: len(x[1]))

    # Write all tex files
    out_subdir = os.path.join(output_dir, safe_name)
    os.makedirs(out_subdir, exist_ok=True)
    for name, content in tex_contents:
        safe_tex = re.sub(r"[/\\]", "_", name)
        out_path = os.path.join(out_subdir, safe_tex)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

    # Also write combined file
    combined_path = os.path.join(output_dir, f"{safe_name}.tex")
    with open(combined_path, "w", encoding="utf-8") as f:
        for name, content in tex_contents:
            f.write(f"\n{'=' * 50}\n% File: {name}\n{'=' * 50}\n")
            f.write(content)
            f.write("\n\n")

    return combined_path


def fail(err: RequestError) -> None:
    """Print a clear error and a STATUS line, then exit with the error's code."""
    print(f"ERROR: {err.message}", file=sys.stderr)
    print(f"STATUS: {STATUS_NAMES[err.code]}", file=sys.stderr)
    sys.exit(err.code)


def main():
    try:
        run()
    except RequestError as e:
        fail(e)
    print("STATUS: ok", file=sys.stderr)
    sys.exit(EXIT_OK)


def run():
    parser = argparse.ArgumentParser(description="Download arXiv paper source by title")
    parser.add_argument("--title", help="Paper title to search for")
    parser.add_argument("--arxiv-id", help="Direct arXiv ID (e.g., 1706.03762)")
    parser.add_argument("--max-results", type=int, default=5, help="Max search results (default: 5)")
    parser.add_argument("--output-dir", default="arxiv_papers", help="Output directory (default: arxiv_papers/)")
    parser.add_argument("--metadata", action="store_true", help="Also output metadata JSON")
    args = parser.parse_args()

    if not args.title and not args.arxiv_id:
        parser.error("must specify --title or --arxiv-id")

    if args.arxiv_id:
        print(f"Downloading source for arXiv:{args.arxiv_id}", file=sys.stderr)
        result = download_source(args.arxiv_id, args.output_dir)
        print(f"Saved to: {result}")
        return

    # Search by title
    print(f"Searching arXiv for: {args.title}", file=sys.stderr)
    papers = search_arxiv(args.title, max_results=args.max_results)
    if not papers:
        raise RequestError(EXIT_NOT_FOUND, "the arXiv search worked, but no paper matched this title")

    print(f"Found {len(papers)} results:", file=sys.stderr)
    for i, p in enumerate(papers):
        print(f"  [{i+1}] {p['title'][:80]} ({p['arxiv_id']})", file=sys.stderr)

    if args.metadata:
        meta_path = os.path.join(args.output_dir, "metadata.json")
        os.makedirs(args.output_dir, exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)
        print(f"Metadata saved to: {meta_path}", file=sys.stderr)

    # Download first result
    paper = papers[0]
    if not paper["arxiv_id"]:
        raise RequestError(EXIT_NOT_FOUND, "No arXiv ID found for top result.")

    print(f"\nDownloading source for: {paper['title'][:60]}...", file=sys.stderr)
    time.sleep(1)  # Be polite to arXiv
    result = download_source(paper["arxiv_id"], args.output_dir)
    print(f"Saved to: {result}")


if __name__ == "__main__":
    main()
