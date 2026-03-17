from typing import Any, Dict, List, Optional
import threading
import requests
import xml.etree.ElementTree as ET
from core.tools.base import BaseTool

_SEMAPHORE = threading.Semaphore(2)  # max 2 concurrent pubmed_search requests

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_HEADERS     = {"User-Agent": "OpenResearchClaw/1.0"}


class PubMedSearchTool(BaseTool):
    """
    Search PubMed/MEDLINE — the authoritative database for biomedical and
    life sciences literature maintained by the US National Library of Medicine.

    DOMAIN COVERAGE: Biomedical sciences, clinical medicine, pharmacology,
    molecular biology, genetics, neuroscience, public health, nursing,
    dentistry, veterinary medicine.

    USE WHEN:
    - Research topic is in biology, medicine, or health sciences
    - You need peer-reviewed clinical/experimental papers
    - MeSH-indexed, authoritative biomedical literature is required
    - Looking for drug interactions, clinical trials, or disease studies

    NOT SUITABLE FOR:
    - Pure computer science / AI (use arxiv_search or openalex_search)
    - Physics, chemistry theory (use arxiv_search)
    - Cross-domain or social science topics (use openalex_search)
    """

    def __init__(self, api_key: Optional[str] = None):
        # NCBI API key increases rate limit from 3 to 10 req/sec
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "pubmed_search"

    @property
    def description(self) -> str:
        return (
            "Search PubMed/MEDLINE for biomedical and life sciences papers "
            "(medicine, biology, pharmacology, genetics, neuroscience, clinical trials). "
            "Authoritative for health/medical research. "
            "Supports MeSH terms, author search, journal filter, date range. "
            "NOT suitable for pure CS/AI or physics — use arxiv_search for those."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "PubMed query string. Supports field tags: "
                        "[ti] for title, [ab] for abstract, [au] for author, "
                        "[mh] for MeSH term, [pt] for publication type. "
                        "Examples: 'CRISPR gene editing[ti]', "
                        "'Alzheimer disease[mh] AND machine learning[ab]', "
                        "'clinical trial[pt] AND COVID-19'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of papers to return (default: 10, max: 50).",
                    "default": 10,
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "date"],
                    "description": (
                        "'relevance' (default) — best match score; "
                        "'date' — most recently published first."
                    ),
                    "default": "relevance",
                },
                "date_from": {
                    "type": "string",
                    "description": "Filter papers published on or after this date (YYYY/MM/DD or YYYY).",
                },
                "date_to": {
                    "type": "string",
                    "description": "Filter papers published on or before this date (YYYY/MM/DD or YYYY).",
                },
                "publication_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by publication type. Common values: "
                        "'Journal Article', 'Review', 'Clinical Trial', "
                        "'Meta-Analysis', 'Systematic Review', 'Case Reports'."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results (for pagination). Default: 0.",
                    "default": 0,
                },
            },
            "required": ["query"],
        }

    def execute(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        publication_types: Optional[List[str]] = None,
        offset: int = 0,
        **kwargs,
    ) -> str:
        with _SEMAPHORE:
            return self._execute(
                query=query, max_results=max_results, sort_by=sort_by,
                date_from=date_from, date_to=date_to,
                publication_types=publication_types, offset=offset, **kwargs,
            )

    def _execute(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        publication_types: Optional[List[str]] = None,
        offset: int = 0,
        **kwargs,
    ) -> str:
        max_results = min(max_results, 50)

        # Build query with filters
        full_query = query
        if publication_types:
            pt_parts = " OR ".join(f'"{pt}"[pt]' for pt in publication_types)
            full_query = f"({full_query}) AND ({pt_parts})"

        sort_param = "relevance" if sort_by == "relevance" else "pub+date"

        esearch_params: Dict[str, Any] = {
            "db": "pubmed",
            "term": full_query,
            "retmax": max_results,
            "retstart": offset,
            "retmode": "json",
            "sort": sort_param,
            "usehistory": "y",
        }
        if date_from:
            esearch_params["datetype"] = "pdat"
            esearch_params["mindate"] = date_from
        if date_to:
            esearch_params.setdefault("datetype", "pdat")
            esearch_params["maxdate"] = date_to
        if self.api_key:
            esearch_params["api_key"] = self.api_key

        try:
            r = requests.get(_ESEARCH_URL, params=esearch_params, headers=_HEADERS, timeout=15)
            r.raise_for_status()
            search_data = r.json()
        except Exception as e:
            return f"Error querying PubMed (esearch): {e}"

        esearch_result = search_data.get("esearchresult", {})
        pmids = esearch_result.get("idlist", [])
        total_count = esearch_result.get("count", "?")

        if not pmids:
            return f"No papers found on PubMed matching '{query}'."

        # Fetch details via efetch
        fetch_params: Dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if self.api_key:
            fetch_params["api_key"] = self.api_key

        try:
            r2 = requests.get(_EFETCH_URL, params=fetch_params, headers=_HEADERS, timeout=20)
            r2.raise_for_status()
            root = ET.fromstring(r2.content)
        except Exception as e:
            return f"Error fetching PubMed details (efetch): {e}"

        papers = []
        for article in root.findall(".//PubmedArticle"):
            papers.append(_parse_pubmed_article(article))

        header_parts = [f'query: "{query}"', f"sort: {sort_by}", f"results: {len(papers)}/{total_count}"]
        if date_from or date_to:
            header_parts.append(f"date: {date_from or ''}–{date_to or ''}")
        if offset:
            header_parts.append(f"offset: {offset}")

        lines = [
            "PubMed Search Results — " + "  ".join(header_parts),
            "=" * 60,
        ]

        for i, p in enumerate(papers, 1):
            entry = [f"[{i}] {p['title']}"]
            entry.append(f"    PMID      : {p['pmid']}")
            if p['pub_date']:
                entry.append(f"    Published : {p['pub_date']}")
            if p['authors']:
                entry.append(f"    Authors   : {p['authors']}")
            if p['journal']:
                entry.append(f"    Journal   : {p['journal']}")
            if p['pub_types']:
                entry.append(f"    Type      : {', '.join(p['pub_types'])}")
            if p['doi']:
                entry.append(f"    DOI       : {p['doi']}")
            entry.append(f"    URL       : https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/")
            if p['pmc_id']:
                entry.append(f"    PDF       : https://www.ncbi.nlm.nih.gov/pmc/articles/{p['pmc_id']}/pdf/")
            if p['abstract']:
                entry.append(f"    Abstract  : {p['abstract']}")
            lines.append("\n".join(entry))

        return "\n\n---\n\n".join(lines)


def _parse_pubmed_article(article: ET.Element) -> Dict[str, Any]:
    """Extract key fields from a PubmedArticle XML element."""
    def text(el: Optional[ET.Element]) -> str:
        return el.text.strip() if el is not None and el.text else ""

    medline = article.find("MedlineCitation")
    if medline is None:
        return {"title": "", "pmid": "", "authors": "", "journal": "",
                "pub_date": "", "abstract": "", "doi": "", "pub_types": []}

    pmid = text(medline.find("PMID"))
    art = medline.find("Article")
    if art is None:
        return {"title": "", "pmid": pmid, "authors": "", "journal": "",
                "pub_date": "", "abstract": "", "doi": "", "pub_types": []}

    title = text(art.find("ArticleTitle"))

    # Authors
    author_list = art.find("AuthorList")
    authors_out = []
    if author_list:
        for au in list(author_list)[:5]:
            last = text(au.find("LastName"))
            fore = text(au.find("ForeName"))
            if last:
                authors_out.append(f"{last} {fore}".strip())
        if len(list(author_list)) > 5:
            authors_out.append(f"... (+{len(list(author_list)) - 5} more)")
    authors_str = ", ".join(authors_out)

    # Journal
    journal_el = art.find("Journal")
    journal_name = ""
    pub_date = ""
    if journal_el is not None:
        journal_name = text(journal_el.find("Title"))
        ji = journal_el.find("JournalIssue")
        if ji is not None:
            pd = ji.find("PubDate")
            if pd is not None:
                year = text(pd.find("Year"))
                month = text(pd.find("Month"))
                day = text(pd.find("Day"))
                med_date = text(pd.find("MedlineDate"))
                pub_date = " ".join(filter(None, [year, month, day])) or med_date

    # Abstract
    abstract_parts = []
    for ab_text in art.findall(".//AbstractText"):
        label = ab_text.get("Label", "")
        content = ab_text.text or ""
        abstract_parts.append(f"{label + ': ' if label else ''}{content.strip()}")
    abstract = " ".join(abstract_parts)

    # DOI
    doi = ""
    for eid in art.findall(".//ELocationID"):
        if eid.get("EIdType") == "doi":
            doi = text(eid)
            break

    # PMC ID (for free full-text PDF)
    pmc_id = ""
    pubmed_data = article.find("PubmedData")
    if pubmed_data is not None:
        for aid in pubmed_data.findall(".//ArticleId"):
            if aid.get("IdType") == "pmc":
                raw = text(aid)
                # Normalize: ensure "PMC" prefix
                pmc_id = raw if raw.startswith("PMC") else f"PMC{raw}"
                break

    # Publication types
    pub_types = [text(pt) for pt in medline.findall(".//PublicationType")]
    # Filter out very generic ones
    ignore = {"Journal Article"}
    filtered_types = [pt for pt in pub_types if pt not in ignore] or pub_types[:1]

    return {
        "title": title,
        "pmid": pmid,
        "authors": authors_str,
        "journal": journal_name,
        "pub_date": pub_date,
        "abstract": abstract,
        "doi": doi,
        "pmc_id": pmc_id,
        "pub_types": filtered_types,
    }
