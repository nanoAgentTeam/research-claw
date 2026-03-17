from typing import Any, Dict, List, Optional
import threading
import requests
from core.tools.base import BaseTool

_SEMAPHORE = threading.Semaphore(2)  # max 2 concurrent openalex_search requests

_BASE_URL = "https://api.openalex.org/works"
_HEADERS = {"User-Agent": "OpenResearchClaw/1.0 (mailto:research@openresearchclaw.ai)"}

# OpenAlex concept/field IDs for common domains (for documentation purposes)
_FIELD_HINTS = """
Common OpenAlex field filter values (use with `fields` param):
  Computer Science       : "computer science"
  Medicine               : "medicine"
  Biology                : "biology"
  Physics                : "physics"
  Chemistry              : "chemistry"
  Mathematics            : "mathematics"
  Economics              : "economics"
  Psychology             : "psychology"
  Engineering            : "engineering"
  Materials Science      : "materials science"
"""


class OpenAlexSearchTool(BaseTool):
    """
    Search OpenAlex — a comprehensive open academic knowledge graph covering
    ALL research domains (250M+ works). Best used as a cross-domain fallback
    or for fields not well-covered by arxiv/PubMed.

    DOMAIN COVERAGE: Universal — Computer Science, Medicine, Biology, Physics,
    Chemistry, Mathematics, Economics, Engineering, Social Sciences, and more.

    USE WHEN:
    - The topic spans multiple disciplines (e.g., computational biology, medical AI)
    - Domain-specific tools (arxiv, pubmed) return insufficient results
    - You need citation counts, venue info, or open-access links
    - You want to search across broad keyword concepts

    PREFER OVER THIS TOOL:
    - arxiv_search for CS/AI/Physics preprints (faster, more complete)
    - pubmed_search for biomedical/clinical literature (authoritative)
    """

    @property
    def name(self) -> str:
        return "openalex_search"

    @property
    def description(self) -> str:
        return (
            "Search OpenAlex for academic papers across ALL research domains "
            "(CS, Medicine, Biology, Physics, Chemistry, Economics, etc.). "
            "Returns formal publication metadata (venue, DOI) and ready-to-use BibTeX entries. "
            "PREFERRED for paper writing — provides published venue info instead of preprint-only references. "
            "Use arxiv_search only for tracking latest preprints/trends, not for writing references."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Full-text search query (title, abstract, keywords).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 10, max: 50).",
                    "default": 10,
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "citations", "date"],
                    "description": (
                        "'relevance' (default) — best keyword match; "
                        "'citations' — most-cited first; "
                        "'date' — most recently published first."
                    ),
                    "default": "relevance",
                },
                "year_from": {
                    "type": "integer",
                    "description": "Filter papers published in or after this year (e.g. 2020).",
                },
                "year_to": {
                    "type": "integer",
                    "description": "Filter papers published in or before this year.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by research field/domain name(s). "
                        "Examples: ['computer science'], ['medicine', 'biology'], ['physics']. "
                        "Matches against OpenAlex topic/concept labels."
                    ),
                },
                "open_access_only": {
                    "type": "boolean",
                    "description": "If true, return only open-access papers with free PDF. Default: false.",
                    "default": False,
                },
                "type": {
                    "type": "string",
                    "enum": ["article", "preprint", "book", "book-chapter", "dataset", "review"],
                    "description": "Filter by publication type. Default: any.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for pagination (default: 1).",
                    "default": 1,
                },
            },
            "required": ["query"],
        }

    def execute(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        fields: Optional[List[str]] = None,
        open_access_only: bool = False,
        type: Optional[str] = None,
        page: int = 1,
        **kwargs,
    ) -> str:
        with _SEMAPHORE:
            return self._execute(
                query=query, max_results=max_results, sort_by=sort_by,
                year_from=year_from, year_to=year_to, fields=fields,
                open_access_only=open_access_only, type=type, page=page, **kwargs,
            )

    def _execute(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        fields: Optional[List[str]] = None,
        open_access_only: bool = False,
        type: Optional[str] = None,
        page: int = 1,
        **kwargs,
    ) -> str:
        max_results = min(max_results, 50)

        sort_map = {
            "relevance": "relevance_score:desc",
            "citations": "cited_by_count:desc",
            "date": "publication_date:desc",
        }
        sort_param = sort_map.get(sort_by, "relevance_score:desc")

        # Build filter string
        filters = []
        if year_from:
            filters.append(f"publication_year:>{year_from - 1}")
        if year_to:
            filters.append(f"publication_year:<{year_to + 1}")
        if open_access_only:
            filters.append("open_access.is_oa:true")
        if type:
            filters.append(f"type:{type}")

        params: Dict[str, Any] = {
            "search": query,
            "sort": sort_param,
            "per-page": max_results,
            "page": page,
            "select": (
                "id,title,publication_year,publication_date,authorships,"
                "primary_location,open_access,cited_by_count,concepts,"
                "primary_topic,abstract_inverted_index,doi,ids,type"
            ),
        }
        if filters:
            params["filter"] = ",".join(filters)

        try:
            resp = requests.get(_BASE_URL, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return f"Error querying OpenAlex: {e}"

        results = data.get("results", [])
        if not results:
            return f"No papers found on OpenAlex matching '{query}'."

        # Post-filter by fields (concept/topic matching)
        if fields:
            field_lower = [f.lower() for f in fields]
            filtered = []
            for work in results:
                concepts = [c.get("display_name", "").lower() for c in work.get("concepts", [])]
                topic = (work.get("primary_topic") or {}).get("display_name", "").lower()
                if any(f in " ".join(concepts + [topic]) for f in field_lower):
                    filtered.append(work)
            if filtered:
                results = filtered

        total = data.get("meta", {}).get("count", "?")
        header_parts = [f'query: "{query}"', f"sort: {sort_by}", f"results: {len(results)}/{total}"]
        if year_from or year_to:
            header_parts.append(f"years: {year_from or ''}–{year_to or ''}")
        if fields:
            header_parts.append(f"fields: {fields}")
        if page > 1:
            header_parts.append(f"page: {page}")

        lines = [
            "OpenAlex Search Results — " + "  ".join(header_parts),
            "=" * 60,
        ]

        for i, work in enumerate(results, 1):
            title = work.get("title") or "(no title)"
            year = work.get("publication_year", "")
            pub_date = work.get("publication_date", "")

            # Authors
            authorships = work.get("authorships", [])
            authors = [a.get("author", {}).get("display_name", "") for a in authorships[:5]]
            author_str = ", ".join(a for a in authors if a)
            if len(authorships) > 5:
                author_str += f" ... (+{len(authorships) - 5} more)"

            # Venue
            primary_loc = work.get("primary_location") or {}
            source = primary_loc.get("source") or {}
            venue = source.get("display_name", "")

            # DOI / URL
            doi = work.get("doi", "")
            openalex_id = work.get("id", "")
            oa_info = work.get("open_access") or {}
            pdf_url = oa_info.get("oa_url", "")

            # Citations
            cited_by = work.get("cited_by_count", 0)

            # Topic / concepts
            primary_topic = (work.get("primary_topic") or {}).get("display_name", "")
            top_concepts = [c.get("display_name", "") for c in (work.get("concepts") or [])[:4]]

            # Abstract (reconstruct from inverted index)
            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

            entry = [f"[{i}] {title}"]
            entry.append(f"    Published : {pub_date or year}")
            if author_str:
                entry.append(f"    Authors   : {author_str}")
            if venue:
                entry.append(f"    Venue     : {venue}")
            if primary_topic:
                entry.append(f"    Topic     : {primary_topic}")
            elif top_concepts:
                entry.append(f"    Concepts  : {', '.join(top_concepts)}")
            entry.append(f"    Citations : {cited_by}")
            if doi:
                entry.append(f"    DOI       : {doi}")
            if pdf_url:
                entry.append(f"    PDF       : {pdf_url}")
            elif openalex_id:
                entry.append(f"    OpenAlex  : {openalex_id}")
            if abstract:
                entry.append(f"    Abstract  : {abstract}")

            # Generate BibTeX
            bib = _generate_bibtex(work, i)
            if bib:
                entry.append(f"    BibTeX    :\n{bib}")

            lines.append("\n".join(entry))

        return "\n\n---\n\n".join(lines)


def _generate_bibtex(work: dict, index: int) -> str:
    """Generate a BibTeX entry from an OpenAlex work record."""
    import re

    title = work.get("title", "")
    year = work.get("publication_year", "")
    doi = work.get("doi", "")
    authorships = work.get("authorships", [])
    primary_loc = work.get("primary_location") or {}
    source = primary_loc.get("source") or {}
    venue = source.get("display_name", "")
    work_type = work.get("type", "article")

    if not title:
        return ""

    # Build author string: "Last1, First1 and Last2, First2"
    author_names = []
    for a in authorships:
        name = (a.get("author") or {}).get("display_name", "")
        if name:
            author_names.append(name)
    author_str = " and ".join(author_names)

    # Generate citation key: firstauthor_lastname + year
    first_author = author_names[0] if author_names else "unknown"
    last_name = first_author.split()[-1] if first_author else "unknown"
    # Sanitize: keep only ascii letters
    last_name_clean = re.sub(r'[^a-zA-Z]', '', last_name).lower()
    cite_key = f"{last_name_clean}{year}" if year else f"{last_name_clean}"
    # Deduplicate with index
    if index > 1:
        cite_key = f"{cite_key}_{index}"

    bib_type = "article" if work_type in ("article", "review") else "inproceedings"

    lines = [f"      @{bib_type}{{{cite_key},"]
    lines.append(f"        title = {{{title}}},")
    if author_str:
        lines.append(f"        author = {{{author_str}}},")
    if year:
        lines.append(f"        year = {{{year}}},")
    if venue:
        field = "journal" if bib_type == "article" else "booktitle"
        lines.append(f"        {field} = {{{venue}}},")
    if doi:
        lines.append(f"        doi = {{{doi}}},")
    lines.append("      }")

    return "\n".join(lines)


def _reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    try:
        positions: Dict[int, str] = {}
        for word, pos_list in inverted_index.items():
            for pos in pos_list:
                positions[pos] = word
        text = " ".join(positions[i] for i in sorted(positions))
        return text
    except Exception:
        return ""
