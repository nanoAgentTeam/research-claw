# Search Strategy

Use this reference when selecting sources, writing queries, and deciding which papers to keep.

## Inputs to Confirm

Confirm or infer these values before broad search:

- Research topic or task definition
- Synonyms or alternate keywords
- Target venues, if any
- Year range
- Desired paper count

Default values when the user does not specify them:

- Year range: last 3 years
- Paper count: 10-15
- Venues: any

## Source Priority

Search broadly first, then verify metadata from the best source.

1. Semantic Scholar for broad discovery and canonical metadata
2. arXiv for recent preprints
3. ACL Anthology for NLP venues
4. OpenReview for ML conference submissions and accepted papers

Prefer the published venue version over the preprint when both are available.

## Query Patterns

Use a small number of focused queries instead of one broad, noisy query.

### Broad topic discovery

- `site:semanticscholar.org "{topic}" {year range}`
- `site:semanticscholar.org "{topic synonym}" {year range}`

### Preprints

- `site:arxiv.org "{topic}" {year}`
- `site:arxiv.org "{topic}" "{method keyword}" {year}`

### NLP venues

- `site:aclanthology.org "{topic}" {year}`
- `site:aclanthology.org "{task keyword}" "{method keyword}" {year}`

### ML venues

- `site:openreview.net "{topic}" {venue} {year}`
- `site:openreview.net "{method keyword}" "{task keyword}" {venue} {year}`

## Selection Rules

Keep only papers that meet the minimum bar:

- Clear title
- Clear abstract or abstract-like summary
- Verifiable year
- Identifiable venue or source
- Working canonical link

Skip these unless the user explicitly requests them:

- Workshop papers
- Slides, posters, blog posts, project pages
- Duplicates of the same work across arXiv and published venues

## Metadata Verification

Verify these fields from the source page before adding a row:

- title
- authors
- year
- venue
- abstract
- canonical link

Never guess missing metadata. If a field cannot be verified, use `Unknown` for venue or authors when necessary and lower confidence in the row selection.

## Relevance Rating

Assign relevance against the user's actual topic, not against the general field.

- `High`: directly addresses the user's task, method, or benchmark
- `Medium`: methodologically related or solves a nearby task
- `Low`: tangentially related background or adjacent application

Sort final results by relevance first, then by year descending.
