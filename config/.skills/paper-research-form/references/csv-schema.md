# CSV Schema

Use this reference when transforming verified papers into the output table.

## Required Columns

Write exactly these 8 columns in this order:

1. `title`
2. `abstract_snippet`
3. `summary`
4. `relevance`
5. `authors`
6. `year`
7. `venue`
8. `link`

Do not rename columns. Do not add extra columns.

## Field Rules

### `title`

Use the full verified paper title.

### `abstract_snippet`

Take the first 200 characters of the real abstract and append `...`.

Rules:

- Use the source abstract verbatim for the snippet
- Do not exceed 200 characters before the ellipsis
- If the abstract is shorter than 200 characters, still append `...`

### `summary`

Write 1-2 sentences in original wording that describe the paper's main contribution.

Good summary traits:

- Focus on what the paper introduces, improves, or evaluates
- Mention the task or method family when useful
- Avoid copying the abstract

### `relevance`

Use only one of:

- `High`
- `Medium`
- `Low`

### `authors`

Use `FirstAuthor et al.` when there are multiple authors.

Examples:

- `Zhang et al.`
- `Smith et al.`

For single-author papers, use the single author name.

### `year`

Use a 4-digit publication year.

### `venue`

Use the conference or journal name when known.

Examples:

- `NeurIPS 2024`
- `ICLR 2025`
- `TACL`

If no published venue can be verified, use a source-style fallback such as `arXiv` or `OpenReview`.

### `link`

Prefer canonical links in this order:

1. Semantic Scholar
2. arXiv
3. ACL Anthology
4. DOI or publisher page

## CSV Encoding and Escaping

Always prepend UTF-8 BOM so spreadsheet applications display Chinese correctly.

Formatting rules:

- Quote any field containing commas
- Quote any field containing double quotes
- Quote any field containing newlines
- Escape literal double quotes by doubling them

## Example Header

```csv
title,abstract_snippet,summary,relevance,authors,year,venue,link
```

## Quality Checks Before Writing

Verify all rows satisfy these checks:

- No empty title
- No empty link
- Relevance value is valid
- Year is numeric or intentionally omitted as `Unknown` is not used here
- Summary is original wording
