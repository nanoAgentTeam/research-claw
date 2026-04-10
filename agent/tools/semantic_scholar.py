from typing import Any, Dict, Optional
from semanticscholar import SemanticScholar
import asyncio
from core.tools.base import BaseTool

class SemanticScholarTool(BaseTool):
    """
    Tool to search for papers on Semantic Scholar.
    Provides citation graphs, influential citations, and detailed metadata.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "semantic_scholar_search"

    @property
    def description(self) -> str:
        return "Search Semantic Scholar for papers. Provides detailed metadata, citation counts, and influence graphs. Better than Google Scholar for finding related work and citation analysis."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g., 'Attention is All You Need', 'author:Yoshua Bengio')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5).",
                    "default": 5
                },
                "get_details": {
                    "type": "boolean",
                    "description": "If true, fetches detailed info (references/citations) for the top result. Use this when you need deep analysis of a specific paper.",
                    "default": False
                }
            },
            "required": ["query"]
        }

    def execute(self, query: str, limit: int = 5, get_details: bool = False, **kwargs) -> str:
        """Execute the search."""
        try:
            logger.info(f"Searching Semantic Scholar: {query} (limit={limit}, details={get_details})")

            # SemanticScholar client is synchronous
            return self._search_sync(query, limit, get_details)

        except Exception as e:
            logger.error(f"Semantic Scholar search failed: {e}")
            return f"Error searching Semantic Scholar: {str(e)}"

    def _search_sync(self, query: str, limit: int, get_details: bool) -> str:
        # Retry mechanism for transient errors (429, 500, etc.)
        max_retries = 3
        retry_delay = 2

        last_error = None

        for attempt in range(max_retries):
            try:
                sch = SemanticScholar(api_key=self.api_key) if self.api_key else SemanticScholar()

                results = sch.search_paper(query, limit=limit)

                if not results:
                    return "No results found on Semantic Scholar."

                output = []

                # If get_details is True, we only fetch details for the FIRST result to avoid rate limits/spam
                if get_details and len(results) > 0:
                    top_paper = results[0]
                    # Re-fetch full details if needed (sometimes search returns partial)
                    # But search_paper usually returns enough. Let's see what we get.
                    # If we need citations/references, we might need to get_paper explicitly.

                    try:
                        paper_id = top_paper.paperId
                        # Fetch detailed fields
                        details = sch.get_paper(paper_id)

                        output.append(f"--- 🔍 DETAILED REPORT FOR TOP RESULT ---")
                        output.append(f"Title: {details.title}")
                        output.append(f"Paper ID: {details.paperId}")
                        output.append(f"Year: {details.year}")
                        output.append(f"Venue: {details.venue}")
                        output.append(f"Authors: {', '.join([a.name for a in details.authors])}")
                        output.append(f"Abstract: {details.abstract}")
                        output.append(f"Citation Count: {details.citationCount}")
                        output.append(f"Influential Citation Count: {details.influentialCitationCount}")

                        # URLs
                        if details.openAccessPdf:
                            output.append(f"PDF URL: {details.openAccessPdf.get('url')}")

                        # Top Citations (Papers that cite this one)
                        citations = details.citations[:5] if details.citations else []
                        if citations:
                            output.append(f"\n[Top 5 Papers Citing This]:")
                            for cit in citations:
                                output.append(f"- {cit.title} ({cit.year})")

                        # Top References (Papers this one cites)
                        references = details.references[:5] if details.references else []
                        if references:
                            output.append(f"\n[Top 5 References]:")
                            for ref in references:
                                output.append(f"- {ref.title} ({ref.year})")

                        output.append("\n" + "="*30 + "\n")

                        # If we got details for top 1, we might just return that or continue with basic list for others?
                        # Let's return just the detailed report if explicitly asked,
                        # plus a brief list of others if they exist.
                        if len(results) > 1:
                            output.append("Other Results:")
                            for p in results[1:]:
                                output.append(f"- {p.title} ({p.year}) - Citations: {p.citationCount}")

                        return "\n".join(output)

                    except Exception as e:
                        logger.warning(f"Failed to fetch details for paper: {e}")
                        # Fallback to basic list

                # Standard List Output
                for item in results:
                    output.append(
                        f"Title: {item.title}\n"
                        f"Paper ID: {item.paperId}\n"
                        f"Year: {item.year}\n"
                        f"Authors: {', '.join([a.name for a in item.authors[:3]]) + ('...' if len(item.authors)>3 else '')}\n"
                        f"Citations: {item.citationCount} (Influential: {item.influentialCitationCount})\n"
                        f"Abstract: {item.abstract[:200]}...\n" # Truncate abstract for list view
                    )

                return "\n---\n".join(output)

            except Exception as e:
                import time
                logger.warning(f"Semantic Scholar search attempt {attempt+1} failed: {e}")
                last_error = e
                # Check for rate limit or server error
                if "429" in str(e) or "50" in str(e) or "Max retries" in str(e):
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    break # Don't retry logic errors

        # If all retries fail
        msg = f"Error searching Semantic Scholar after {max_retries} attempts: {str(last_error)}"
        if not self.api_key:
            msg += "\n\n💡 Hint: Semantic Scholar rate limits are stricter without an API key. Consider providing one or using Google Scholar."
        else:
            msg += "\n\n💡 Hint: Try Google Scholar if the issue persists."

        return msg
