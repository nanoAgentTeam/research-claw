"""Tool for searching Google Scholar."""

from typing import Any
from scholarly import scholarly
import asyncio
from loguru import logger

class GoogleScholarTool:
    """
    Tool to search for academic publications on Google Scholar.
    """
    name = "google_scholar_search"
    description = "Search for academic publications on Google Scholar. Provides citations, abstracts, and author info. Warning: Aggressive rate limits apply."

    def to_schema(self) -> dict[str, Any]:
        return self.to_openai_schema()

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query (e.g., 'transformers citations', 'author:Yoshua Bengio')."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default: 5). Keep this low to avoid IP blocks.",
                            "default": 5
                        },
                        "sort_by": {
                             "type": "string",
                             "description": "Sort order: 'relevance' (default) or 'date'.",
                             "enum": ["relevance", "date"],
                             "default": "relevance"
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    def execute(self, query: str, limit: int = 5, sort_by: str = "relevance", **kwargs) -> str:
        """Execute the search."""
        try:
            logger.info(f"Searching Google Scholar: {query} (limit={limit}, sort={sort_by})")

            # Lazy import to prevent background activity during tool registration
            from scholarly import scholarly

            search_query = scholarly.search_pubs(query, sort_by="date" if sort_by == "date" else None)

            results = []
            count = 0

            for pub in search_query:
                if count >= limit:
                    break

                bib = pub.get('bib', {})
                title = bib.get('title', 'Unknown Title')
                author = bib.get('author', 'Unknown Author')
                pub_year = bib.get('pub_year', 'N/A')
                venue = bib.get('venue', 'N/A')
                abstract = bib.get('abstract', 'No abstract available.')
                pub_url = pub.get('pub_url', 'No URL')
                num_citations = pub.get('num_citations', 0)

                results.append(
                    f"Title: {title}\n"
                    f"Authors: {author}\n"
                    f"Year: {pub_year} | Venue: {venue}\n"
                    f"Citations: {num_citations}\n"
                    f"URL: {pub_url}\n"
                    f"Abstract: {abstract}\n"
                )
                count += 1

            if not results:
                return "No results found on Google Scholar."

            return "\n---\n".join(results)

        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "denied" in err_msg.lower():
                logger.error(f"Google Scholar rate limited: {e}")
                return "[ERROR] Google Scholar has rate-limited your IP (403 Forbidden). Please use ArxivSearchTool instead for academic research."
            logger.error(f"Google Scholar search failed: {e}")
            return f"[ERROR] searching Google Scholar: {err_msg}\nTip: Try ArxivSearchTool instead."

    # Removed _search_sync as it's now integrated with lazy load in execute
