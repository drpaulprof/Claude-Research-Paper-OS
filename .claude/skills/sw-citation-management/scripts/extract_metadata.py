#!/usr/bin/env python3
"""
Metadata Extraction Tool
Extract citation metadata from DOI, PMID, arXiv ID, or URL using various APIs.

Exit codes (a STATUS line is also printed to stderr):
    0  every identifier was extracted
    1  an identifier does not exist (HTTP 404, empty result) or has an unknown type
    2  bad command-line arguments
    3  network failure: no connection, DNS failure, timeout, or host blocked by a proxy
    4  rate limited: HTTP 429 (or 503 from arXiv) still returned after retries
    5  invalid response: the reply was not the JSON/XML the API documents
    6  other HTTP error, or an error message returned by the arXiv API
If some identifiers fail, the successful entries are still written and the
exit code is that of the first failure.
"""

import sys
import os
import requests
import argparse
import time
import re
import json
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

# --- Request error handling (same exit codes as the deep-research search scripts) ---
EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_NETWORK = 3
EXIT_RATE_LIMITED = 4
EXIT_INVALID_RESPONSE = 5
EXIT_HTTP_ERROR = 6
STATUS_NAMES = {EXIT_OK: 'ok', EXIT_NOT_FOUND: 'not_found', EXIT_NETWORK: 'network_error',
                EXIT_RATE_LIMITED: 'rate_limited', EXIT_INVALID_RESPONSE: 'invalid_response',
                EXIT_HTTP_ERROR: 'http_error'}
REQUEST_TIMEOUT = 15  # seconds per request
MAX_RETRIES = 3       # retries for 429 / temporary 5xx errors
MAX_RETRY_WAIT = 60   # never wait longer than this between retries


class LookupError_(Exception):
    """A failed lookup, with the exit code that describes the failure."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _describe_network_error(err: Exception) -> str:
    """Turn a requests exception into a plain explanation."""
    text = str(err)
    if isinstance(err, requests.exceptions.Timeout):
        return f'request timed out after {REQUEST_TIMEOUT}s'
    if 'Tunnel connection failed' in text or isinstance(err, requests.exceptions.ProxyError):
        return f'blocked by the network proxy ({text[:150]}); the host is probably not on the allowed list'
    if 'Name or service not known' in text or 'NameResolutionError' in text or 'getaddrinfo' in text:
        return 'DNS lookup failed; there may be no internet access, or the host is blocked'
    return f'network error: {text[:200]}'


def _retry_wait(response, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After if given, else 2, 4, 8..."""
    header = response.headers.get('Retry-After') if response is not None else None
    if header:
        try:
            return min(max(float(header), 0.0), MAX_RETRY_WAIT)
        except ValueError:
            pass
    return min(2 ** (attempt + 1), MAX_RETRY_WAIT)


class MetadataExtractor:
    """Extract metadata from various sources and generate BibTeX."""
    
    def __init__(self, email: Optional[str] = None):
        """
        Initialize extractor.
        
        Args:
            email: Email for Entrez API (recommended for PubMed)
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'MetadataExtractor/1.0 (Citation Management Tool)'
        })
        self.email = email or os.getenv('NCBI_EMAIL', '')
        self.last_error = None  # LookupError_ from the most recent failed lookup

    def _get(self, url: str, service: str, params: Optional[Dict] = None,
             rate_limit_codes=(429,)):
        """GET with timeout and retries. Returns a 200 response or raises LookupError_."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as e:
                raise LookupError_(EXIT_NETWORK, f'{service}: {_describe_network_error(e)}')
            code = response.status_code
            if code == 200:
                return response
            if (code == 429 or code in (500, 502, 503, 504)) and attempt < MAX_RETRIES:
                wait = _retry_wait(response, attempt)
                print(f'HTTP {code} from {service}, retrying in {wait:g}s '
                      f'(retry {attempt + 1} of {MAX_RETRIES})...', file=sys.stderr)
                time.sleep(wait)
                continue
            if code in rate_limit_codes:
                raise LookupError_(EXIT_RATE_LIMITED, f'rate limited by {service} (HTTP {code}) after {MAX_RETRIES} retries')
            if code == 404:
                raise LookupError_(EXIT_NOT_FOUND, f'{service} returned HTTP 404 Not Found')
            raise LookupError_(EXIT_HTTP_ERROR, f'{service} returned HTTP {code}')
        raise LookupError_(EXIT_HTTP_ERROR, f'{service}: request failed')  # not reached

    def _fail(self, err: 'LookupError_', label: str) -> None:
        """Record and print a failed lookup."""
        self.last_error = err
        print(f'Error: {label}: {err.message} [{STATUS_NAMES[err.code]}]', file=sys.stderr)
    
    def identify_type(self, identifier: str) -> Tuple[str, str]:
        """
        Identify the type of identifier.
        
        Args:
            identifier: DOI, PMID, arXiv ID, or URL
            
        Returns:
            Tuple of (type, cleaned_identifier)
        """
        identifier = identifier.strip()
        
        # Check if URL
        if identifier.startswith('http://') or identifier.startswith('https://'):
            return self._parse_url(identifier)
        
        # Check for DOI
        if identifier.startswith('10.'):
            return ('doi', identifier)
        
        # Check for arXiv ID
        if re.match(r'^\d{4}\.\d{4,5}(v\d+)?$', identifier):
            return ('arxiv', identifier)
        if identifier.startswith('arXiv:'):
            return ('arxiv', identifier.replace('arXiv:', ''))
        
        # Check for PMID (8-digit number typically)
        if identifier.isdigit() and len(identifier) >= 7:
            return ('pmid', identifier)
        
        # Check for PMCID
        if identifier.upper().startswith('PMC') and identifier[3:].isdigit():
            return ('pmcid', identifier.upper())
        
        return ('unknown', identifier)
    
    def _parse_url(self, url: str) -> Tuple[str, str]:
        """Parse URL to extract identifier type and value."""
        parsed = urlparse(url)
        
        # DOI URLs
        if 'doi.org' in parsed.netloc:
            doi = parsed.path.lstrip('/')
            return ('doi', doi)
        
        # PubMed URLs
        if 'pubmed.ncbi.nlm.nih.gov' in parsed.netloc or 'ncbi.nlm.nih.gov/pubmed' in url:
            pmid = re.search(r'/(\d+)', parsed.path)
            if pmid:
                return ('pmid', pmid.group(1))
        
        # arXiv URLs
        if 'arxiv.org' in parsed.netloc:
            arxiv_id = re.search(r'/abs/(\d{4}\.\d{4,5})', parsed.path)
            if arxiv_id:
                return ('arxiv', arxiv_id.group(1))
        
        # Nature, Science, Cell, etc. - try to extract DOI from URL
        doi_match = re.search(r'10\.\d{4,}/[^\s/]+', url)
        if doi_match:
            return ('doi', doi_match.group())
        
        return ('url', url)
    
    def extract_from_doi(self, doi: str) -> Optional[Dict]:
        """
        Extract metadata from DOI using CrossRef API.
        
        Args:
            doi: Digital Object Identifier
            
        Returns:
            Metadata dictionary or None
        """
        url = f'https://api.crossref.org/works/{doi}'
        
        try:
            response = self._get(url, 'CrossRef API')
            try:
                data = response.json()
            except ValueError as e:
                raise LookupError_(EXIT_INVALID_RESPONSE, f'CrossRef reply is not valid JSON ({e})')
            message = data.get('message') if isinstance(data, dict) else None
            if not isinstance(message, dict):
                raise LookupError_(EXIT_INVALID_RESPONSE, 'CrossRef reply has no "message" object')
            
            metadata = {
                'type': 'doi',
                'entry_type': self._crossref_type_to_bibtex(message.get('type')),
                'doi': doi,
                'title': message.get('title', [''])[0],
                'authors': self._format_authors_crossref(message.get('author', [])),
                'year': self._extract_year_crossref(message),
                'journal': message.get('container-title', [''])[0] if message.get('container-title') else '',
                'volume': str(message.get('volume', '')) if message.get('volume') else '',
                'issue': str(message.get('issue', '')) if message.get('issue') else '',
                'pages': message.get('page', ''),
                'publisher': message.get('publisher', ''),
                'url': f'https://doi.org/{doi}'
            }
            
            return metadata
            
        except LookupError_ as e:
            self._fail(e, f'DOI {doi}')
            return None
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            # Reply parsed, but a documented field was missing or had the wrong type
            self._fail(LookupError_(EXIT_INVALID_RESPONSE, f'reply is missing expected fields ({e!r})'), f'DOI {doi}')
            return None
    
    def extract_from_pmid(self, pmid: str) -> Optional[Dict]:
        """
        Extract metadata from PMID using PubMed E-utilities.
        
        Args:
            pmid: PubMed ID
            
        Returns:
            Metadata dictionary or None
        """
        url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
        params = {
            'db': 'pubmed',
            'id': pmid,
            'retmode': 'xml',
            'rettype': 'abstract'
        }
        
        if self.email:
            params['email'] = self.email
        
        api_key = os.getenv('NCBI_API_KEY')
        if api_key:
            params['api_key'] = api_key
        
        try:
            response = self._get(url, 'PubMed API', params=params)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                raise LookupError_(EXIT_INVALID_RESPONSE, f'PubMed reply is not valid XML ({e})')
            article = root.find('.//PubmedArticle')
            
            if article is None:
                raise LookupError_(EXIT_NOT_FOUND, f'No article found for PMID: {pmid}')
            
            # Extract metadata from XML
            medline_citation = article.find('.//MedlineCitation')
            article_elem = medline_citation.find('.//Article')
            journal = article_elem.find('.//Journal')
            
            # Get DOI if available
            doi = None
            article_ids = article.findall('.//ArticleId')
            for article_id in article_ids:
                if article_id.get('IdType') == 'doi':
                    doi = article_id.text
                    break
            
            metadata = {
                'type': 'pmid',
                'entry_type': 'article',
                'pmid': pmid,
                'title': article_elem.findtext('.//ArticleTitle', ''),
                'authors': self._format_authors_pubmed(article_elem.findall('.//Author')),
                'year': self._extract_year_pubmed(article_elem),
                'journal': journal.findtext('.//Title', ''),
                'volume': journal.findtext('.//JournalIssue/Volume', ''),
                'issue': journal.findtext('.//JournalIssue/Issue', ''),
                'pages': article_elem.findtext('.//Pagination/MedlinePgn', ''),
                'doi': doi
            }
            
            return metadata
            
        except LookupError_ as e:
            self._fail(e, f'PMID {pmid}')
            return None
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            # A <PubmedArticle> without the usual MedlineCitation/Article/Journal parts
            self._fail(LookupError_(EXIT_INVALID_RESPONSE, f'PubMed reply is missing expected fields ({e})'), f'PMID {pmid}')
            return None
    
    def extract_from_arxiv(self, arxiv_id: str) -> Optional[Dict]:
        """
        Extract metadata from arXiv ID using arXiv API.
        
        Args:
            arxiv_id: arXiv identifier
            
        Returns:
            Metadata dictionary or None
        """
        url = 'https://export.arxiv.org/api/query'
        params = {
            'id_list': arxiv_id,
            'max_results': 1
        }
        
        try:
            response = self._get(url, 'arXiv API', params=params, rate_limit_codes=(429, 503))
            # Parse Atom XML
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                raise LookupError_(EXIT_INVALID_RESPONSE, f'arXiv reply is not valid XML ({e})')
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
            if root.tag != '{http://www.w3.org/2005/Atom}feed':
                raise LookupError_(EXIT_INVALID_RESPONSE, f'arXiv reply is not an Atom feed (root {root.tag!r})')
            
            entry = root.find('atom:entry', ns)
            if entry is None:
                raise LookupError_(EXIT_NOT_FOUND, f'No entry found for arXiv ID: {arxiv_id}')
            # arXiv reports bad IDs as HTTP 200 with an "Error" entry; without
            # this check that entry was turned into a BibTeX record titled "Error".
            entry_id = entry.findtext('atom:id', '', ns)
            if '/api/errors' in entry_id:
                detail = ' '.join(entry.findtext('atom:summary', '', ns).split())
                raise LookupError_(EXIT_HTTP_ERROR, f'arXiv API reported an error: {detail or entry_id}')
            
            # Extract DOI if published
            doi_elem = entry.find('arxiv:doi', ns)
            doi = doi_elem.text if doi_elem is not None else None
            
            # Extract journal reference if published
            journal_ref_elem = entry.find('arxiv:journal_ref', ns)
            journal_ref = journal_ref_elem.text if journal_ref_elem is not None else None
            
            # Get publication date
            published = entry.findtext('atom:published', '', ns)
            year = published[:4] if published else ''
            
            # Get authors
            authors = []
            for author in entry.findall('atom:author', ns):
                name = author.findtext('atom:name', '', ns)
                if name:
                    authors.append(name)
            
            metadata = {
                'type': 'arxiv',
                'entry_type': 'misc' if not doi else 'article',
                'arxiv_id': arxiv_id,
                'title': entry.findtext('atom:title', '', ns).strip().replace('\n', ' '),
                'authors': ' and '.join(authors),
                'year': year,
                'doi': doi,
                'journal_ref': journal_ref,
                'abstract': entry.findtext('atom:summary', '', ns).strip().replace('\n', ' '),
                'url': f'https://arxiv.org/abs/{arxiv_id}'
            }
            
            return metadata
            
        except LookupError_ as e:
            self._fail(e, f'arXiv {arxiv_id}')
            return None
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            # Reply parsed, but a documented field was missing or had the wrong type
            self._fail(LookupError_(EXIT_INVALID_RESPONSE, f'reply is missing expected fields ({e!r})'), f'arXiv {arxiv_id}')
            return None
    
    def metadata_to_bibtex(self, metadata: Dict, citation_key: Optional[str] = None) -> str:
        """
        Convert metadata dictionary to BibTeX format.
        
        Args:
            metadata: Metadata dictionary
            citation_key: Optional custom citation key
            
        Returns:
            BibTeX string
        """
        if not citation_key:
            citation_key = self._generate_citation_key(metadata)
        
        entry_type = metadata.get('entry_type', 'misc')
        
        # Build BibTeX entry
        lines = [f'@{entry_type}{{{citation_key},']
        
        # Add fields
        if metadata.get('authors'):
            lines.append(f'  author  = {{{metadata["authors"]}}},')
        
        if metadata.get('title'):
            # Protect capitalization
            title = self._protect_title(metadata['title'])
            lines.append(f'  title   = {{{title}}},')
        
        if entry_type == 'article' and metadata.get('journal'):
            lines.append(f'  journal = {{{metadata["journal"]}}},')
        elif entry_type == 'misc' and metadata.get('type') == 'arxiv':
            lines.append(f'  howpublished = {{arXiv}},')
        
        if metadata.get('year'):
            lines.append(f'  year    = {{{metadata["year"]}}},')
        
        if metadata.get('volume'):
            lines.append(f'  volume  = {{{metadata["volume"]}}},')
        
        if metadata.get('issue'):
            lines.append(f'  number  = {{{metadata["issue"]}}},')
        
        if metadata.get('pages'):
            pages = metadata['pages'].replace('-', '--')  # En-dash
            lines.append(f'  pages   = {{{pages}}},')
        
        if metadata.get('doi'):
            lines.append(f'  doi     = {{{metadata["doi"]}}},')
        elif metadata.get('url'):
            lines.append(f'  url     = {{{metadata["url"]}}},')
        
        if metadata.get('pmid'):
            lines.append(f'  note    = {{PMID: {metadata["pmid"]}}},')
        
        if metadata.get('type') == 'arxiv' and not metadata.get('doi'):
            lines.append(f'  note    = {{Preprint}},')
        
        # Remove trailing comma from last field
        if lines[-1].endswith(','):
            lines[-1] = lines[-1][:-1]
        
        lines.append('}')
        
        return '\n'.join(lines)
    
    def _crossref_type_to_bibtex(self, crossref_type: str) -> str:
        """Map CrossRef type to BibTeX entry type."""
        type_map = {
            'journal-article': 'article',
            'book': 'book',
            'book-chapter': 'incollection',
            'proceedings-article': 'inproceedings',
            'posted-content': 'misc',
            'dataset': 'misc',
            'report': 'techreport'
        }
        return type_map.get(crossref_type, 'misc')
    
    def _format_authors_crossref(self, authors: List[Dict]) -> str:
        """Format author list from CrossRef data."""
        if not authors:
            return ''
        
        formatted = []
        for author in authors:
            given = author.get('given', '')
            family = author.get('family', '')
            if family:
                if given:
                    formatted.append(f'{family}, {given}')
                else:
                    formatted.append(family)
        
        return ' and '.join(formatted)
    
    def _format_authors_pubmed(self, authors: List) -> str:
        """Format author list from PubMed XML."""
        formatted = []
        for author in authors:
            last_name = author.findtext('.//LastName', '')
            fore_name = author.findtext('.//ForeName', '')
            if last_name:
                if fore_name:
                    formatted.append(f'{last_name}, {fore_name}')
                else:
                    formatted.append(last_name)
        
        return ' and '.join(formatted)
    
    def _extract_year_crossref(self, message: Dict) -> str:
        """Extract year from CrossRef message."""
        # Try published-print first, then published-online
        date_parts = message.get('published-print', {}).get('date-parts', [[]])
        if not date_parts or not date_parts[0]:
            date_parts = message.get('published-online', {}).get('date-parts', [[]])
        
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
        return ''
    
    def _extract_year_pubmed(self, article: ET.Element) -> str:
        """Extract year from PubMed XML."""
        year = article.findtext('.//Journal/JournalIssue/PubDate/Year', '')
        if not year:
            medline_date = article.findtext('.//Journal/JournalIssue/PubDate/MedlineDate', '')
            if medline_date:
                year_match = re.search(r'\d{4}', medline_date)
                if year_match:
                    year = year_match.group()
        return year
    
    def _generate_citation_key(self, metadata: Dict) -> str:
        """Generate a citation key from metadata."""
        # Get first author last name
        authors = metadata.get('authors', '')
        if authors:
            first_author = authors.split(' and ')[0]
            if ',' in first_author:
                last_name = first_author.split(',')[0].strip()
            else:
                last_name = first_author.split()[-1] if first_author else 'Unknown'
        else:
            last_name = 'Unknown'
        
        # Get year
        year = metadata.get('year', '').strip()
        if not year:
            year = 'XXXX'
        
        # Clean last name (remove special characters)
        last_name = re.sub(r'[^a-zA-Z]', '', last_name)
        
        # Get keyword from title
        title = metadata.get('title', '')
        words = re.findall(r'\b[a-zA-Z]{4,}\b', title)
        keyword = words[0].lower() if words else 'paper'
        
        return f'{last_name}{year}{keyword}'
    
    def _protect_title(self, title: str) -> str:
        """Protect capitalization in title for BibTeX."""
        # Protect common acronyms and proper nouns
        protected_words = [
            'DNA', 'RNA', 'CRISPR', 'COVID', 'HIV', 'AIDS', 'AlphaFold',
            'Python', 'AI', 'ML', 'GPU', 'CPU', 'USA', 'UK', 'EU'
        ]
        
        for word in protected_words:
            title = re.sub(rf'\b{word}\b', f'{{{word}}}', title, flags=re.IGNORECASE)
        
        return title
    
    def extract(self, identifier: str) -> Optional[str]:
        """
        Extract metadata and return BibTeX.
        
        Args:
            identifier: DOI, PMID, arXiv ID, or URL
            
        Returns:
            BibTeX string or None
        """
        self.last_error = None
        id_type, clean_id = self.identify_type(identifier)
        
        print(f'Identified as {id_type}: {clean_id}', file=sys.stderr)
        
        metadata = None
        
        if id_type == 'doi':
            metadata = self.extract_from_doi(clean_id)
        elif id_type == 'pmid':
            metadata = self.extract_from_pmid(clean_id)
        elif id_type == 'arxiv':
            metadata = self.extract_from_arxiv(clean_id)
        else:
            self._fail(LookupError_(EXIT_NOT_FOUND, 'unknown identifier type (expected DOI, PMID, arXiv ID or URL)'),
                       identifier)
            return None
        
        if metadata:
            return self.metadata_to_bibtex(metadata)
        else:
            return None


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description='Extract citation metadata from DOI, PMID, arXiv ID, or URL',
        epilog='Example: python extract_metadata.py --doi 10.1038/s41586-021-03819-2'
    )
    
    parser.add_argument('--doi', help='Digital Object Identifier')
    parser.add_argument('--pmid', help='PubMed ID')
    parser.add_argument('--arxiv', help='arXiv ID')
    parser.add_argument('--url', help='URL to article')
    parser.add_argument('-i', '--input', help='Input file with identifiers (one per line)')
    parser.add_argument('-o', '--output', help='Output file for BibTeX (default: stdout)')
    parser.add_argument('--format', choices=['bibtex', 'json'], default='bibtex', help='Output format')
    parser.add_argument('--email', help='Email for NCBI E-utilities (recommended)')
    
    args = parser.parse_args()
    
    # Collect identifiers
    identifiers = []
    if args.doi:
        identifiers.append(args.doi)
    if args.pmid:
        identifiers.append(args.pmid)
    if args.arxiv:
        identifiers.append(args.arxiv)
    if args.url:
        identifiers.append(args.url)
    
    if args.input:
        try:
            with open(args.input, 'r', encoding='utf-8') as f:
                file_ids = [line.strip() for line in f if line.strip()]
                identifiers.extend(file_ids)
        except Exception as e:
            print(f'Error reading input file: {e}', file=sys.stderr)
            sys.exit(1)
    
    if not identifiers:
        parser.print_help()
        sys.exit(1)
    
    # Extract metadata
    extractor = MetadataExtractor(email=args.email)
    bibtex_entries = []
    failures = []  # (identifier, LookupError_)
    
    for i, identifier in enumerate(identifiers):
        print(f'\nProcessing {i+1}/{len(identifiers)}...', file=sys.stderr)
        bibtex = extractor.extract(identifier)
        if bibtex:
            bibtex_entries.append(bibtex)
        else:
            failures.append((identifier, extractor.last_error
                             or LookupError_(EXIT_HTTP_ERROR, 'extraction failed')))
        
        # Rate limiting
        if i < len(identifiers) - 1:
            time.sleep(0.5)
    
    if not bibtex_entries:
        print('Error: No successful extractions', file=sys.stderr)
        first = failures[0][1]
        print(f'STATUS: {STATUS_NAMES[first.code]} extracted=0 failed={len(failures)}', file=sys.stderr)
        sys.exit(first.code)
    
    # Format output
    if args.format == 'bibtex':
        output = '\n\n'.join(bibtex_entries) + '\n'
    else:  # json
        output = json.dumps({
            'count': len(bibtex_entries),
            'entries': bibtex_entries
        }, indent=2)
    
    # Write output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f'\nSuccessfully wrote {len(bibtex_entries)} entries to {args.output}', file=sys.stderr)
    else:
        print(output)
    
    print(f'\nExtracted {len(bibtex_entries)}/{len(identifiers)} entries', file=sys.stderr)

    if failures:
        print(f'WARNING: {len(failures)} identifier(s) failed:', file=sys.stderr)
        for identifier, err in failures:
            print(f'  - {identifier}: {STATUS_NAMES[err.code]}: {err.message}', file=sys.stderr)
        first = failures[0][1]
        print(f'STATUS: {STATUS_NAMES[first.code]} extracted={len(bibtex_entries)} failed={len(failures)}',
              file=sys.stderr)
        sys.exit(first.code)
    print(f'STATUS: ok extracted={len(bibtex_entries)}', file=sys.stderr)
    sys.exit(EXIT_OK)


if __name__ == '__main__':
    main()

