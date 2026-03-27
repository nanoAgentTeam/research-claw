# Merge And Delivery

Use this reference after paper rows have been prepared and before reporting back to the user.

## Output File Selection

Append by default instead of creating a new survey every time.

Selection rules:

1. Look for existing `survey_*.csv` files in the project directory
2. Match by topic slug in the filename
3. If multiple matches exist, pick the most recently modified file
4. Create a new file only when no match exists or the user explicitly asks for a new survey direction

## File Naming

Use this naming pattern for new files:

- `survey_{topic_slug}_{YYYYMMDD}.csv`

Examples:

- `survey_rag_20260326.csv`
- `survey_multimodal_reasoning_20260326.csv`

Build `topic_slug` from the user's research topic:

- lowercase
- replace spaces with underscores
- remove punctuation when possible

## Duplicate Handling

Deduplicate before writing and before appending.

Primary duplicate key:

- exact normalized title match

Normalization guidance:

- lowercase
- trim surrounding whitespace
- collapse repeated spaces

When both a preprint and a published version exist, keep the published venue version.

## Write Order

Sort rows before final write:

1. `High` relevance first
2. `Medium`
3. `Low`
4. Within each bucket, newest year first

## Delivery Gate

Do not treat the task as complete after writing the CSV.

Required sequence:

1. Write or update the CSV file
2. Call `send_file` with that exact path
3. If `send_file` fails, retry once
4. If retry still fails, report the failure honestly and include the local path in the text response
5. Only after a real `send_file` result is returned, provide the markdown summary

## Caption Pattern

Use a short caption that tells the user what was sent.

Recommended pattern:

- `文献调研: {topic}, 共 {total} 篇, {high} 篇高相关`

## Final Markdown Summary

After file delivery succeeds, report:

- Total papers found
- Breakdown by relevance
- Top 3 most relevant papers
- Missing coverage or follow-up search suggestions
