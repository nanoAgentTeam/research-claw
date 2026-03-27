---
name: paper-research-form
description: |
  Generate structured CSV literature survey tables. MUST activate this skill (instead of plain-text replies) when the user requests a literature review, paper collection, related work survey, or comparison of papers on a topic.
  生成结构化 CSV 文献调研表格。当用户要求调研论文、收集文献、整理相关工作、对比论文时，必须激活此 skill 生成 CSV 输出，而非纯文字回复。
  Triggers: survey, related work, literature review, paper list, find papers, collect papers, compare papers, 调研, 文献, 找论文, 相关论文, 收集论文, 整理文献, 论文列表, 帮我找论文.
  Output: multi-source search → metadata extraction → 8-column CSV (title, abstract, summary, relevance, authors, year, venue, link) → send to IM.
allowed-tools:
  - web_search
  - web_fetch
  - read_file
  - write_file
  - send_file
---
[SKILL: PAPER RESEARCH FORM — CSV LITERATURE SURVEY]

Generate a literature survey as a CSV file and deliver the file to the user in chat.

Keep this file lean. Load the reference documents only when reaching the matching step.

## Workflow

### 1. Clarify Scope

Confirm these inputs if the user has not already specified them:

- research topic or keywords
- target venues or `any`
- year range
- paper count

Use these defaults when needed:

- year range: last 3 years
- paper count: 10-15

### 2. Search And Verify

Load `references/search-strategy.md` before broad search.

Search multiple sources, then verify metadata for each candidate paper.

Use `web_fetch` on promising results to verify:

- title
- authors
- abstract
- venue
- year
- canonical link

Never fabricate metadata. Skip weak candidates that cannot be verified.

### 3. Build Rows

Load `references/csv-schema.md` before writing any row.

Transform each verified paper into one CSV row using the required 8-column schema. Write the summary in original wording. Rate relevance against the user's actual research goal.

### 4. Merge Or Create Output

Load `references/merge-and-delivery.md` before selecting the output file.

Append by default:

- look for matching `survey_*.csv` files
- pick the newest matching file
- skip duplicate titles
- create a new file only when no match exists or the user requests a new survey direction

### 5. Write CSV

Write the final CSV with:

- UTF-8 BOM
- correct CSV escaping
- rows sorted by relevance first, then year descending

### 6. Deliver File

This step is mandatory.

- Call `send_file` immediately after `write_file`
- Retry once if `send_file` fails
- Do not claim the file was sent unless `send_file` actually returned success
- Do not continue to the report step until `send_file` has actually been called

### 7. Report

After file delivery, report:

- total papers found
- relevance breakdown
- top 3 most relevant papers
- gaps or follow-up searches

## Reference Files

Load only the file needed for the current step:

- `references/search-strategy.md` - query design, source priority, metadata verification, relevance rules
- `references/csv-schema.md` - 8-column schema, field rules, BOM, escaping
- `references/merge-and-delivery.md` - append rules, dedupe, file naming, send flow

## Quality Rules

- Never hallucinate papers
- Never guess metadata
- Keep the published venue version when both preprint and publication exist
- Skip workshop papers unless the user explicitly requests them
- Treat the task as incomplete until `send_file` has been executed
