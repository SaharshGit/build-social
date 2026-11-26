# Notes for Claude

## Canva calls (they return a lot of data)
- `edit-design` returns the whole page after every call (about 25k tokens). Make fewer, bigger calls: add all
  shapes and text for a page in one call, and do all formatting in one more. Don't make small corrective calls
  one element at a time.
- Use `read-design` with `filter.fields` and `filter.page_indices` (and `element_ids` when possible) instead of
  reading whole pages. Ask for thumbnails only for the pages being checked.
