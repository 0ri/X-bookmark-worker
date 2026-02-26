name: bookmark-digest
description: >
  Fetch, analyze, and deliver Twitter/X bookmarks.
  The llm-task analysis step runs OUTSIDE this pipeline — the calling agent
  invokes llm-task between build-prompt and store steps.

args:
  batch_size:
    default: "5"
  limit:
    default: "50"

steps:
  - id: fetch
    command: python3 -m bookmark_digest fetch --json --limit $limit
    # Output: {"fetched": N, "processed": N, "items": [...]}

  - id: build-prompt
    command: python3 -m bookmark_digest build-llm-task-request --json
    stdin: $fetch.stdout
    # Output: {"prompt": "...", "input": {...}, "schema": {...}}
    # The calling agent passes this to llm-task and receives:
    # {"analyses": [{item_id, category, analysis, buttons, ...}, ...]}

  - id: store
    command: python3 -m bookmark_digest store-analyses --json
    # stdin: llm-task output (provided by calling agent)
    # Output: {"stored": N, "total_input": N}

  - id: deliver
    command: python3 -m bookmark_digest deliver --json --batch-size $batch_size
    # Output: {"delivered": N, "remaining": N, "batch_id": "...", "messages": [...]}
