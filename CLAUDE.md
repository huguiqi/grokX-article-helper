# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Grok X 长文助手 — a local Streamlit web app that takes rough draft text, polishes it with Grok (xAI), generates cover/illustration images, posts to X (Twitter) as single tweet or thread, and auto-archives as Markdown + images.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (opens http://localhost:8501)
streamlit run x_grok_poster.py

# Check twitter-cli availability (required for posting)
twitter --help
twitter feed -n 1 --yaml
```

No test suite or linting is configured.

## Architecture

Multi-module app with 4 domain modules + 1 UI entry point:

```
x_grok_poster.py   # Streamlit UI entry point (~280 lines)
config.py          # Config, constants, utility functions (~55 lines)
xai_api.py         # xAI REST API layer + Grok polish + image gen (~130 lines)
publisher.py       # Twitter posting + PostQueue (thread-safe) + queue persistence (~190 lines)
archive.py         # Markdown archiving (~60 lines)
```

### config.py
Loads `.env`, defines `BASE_DIR`, `OUTPUT_ROOT`, `XAI_BASE`, `DEFAULT_MODEL`, `IMAGE_MODEL`. Utility functions: `slugify` (Chinese-aware), `download_image`, `check_twitter_cli`, `get_twitter_version`.

### xai_api.py
Direct `requests` calls to xAI's OpenAI-compatible endpoints — `xai_chat_completion()` for text, `xai_generate_image()` for images. `polish_with_grok()` sends draft + system prompt, expects structured JSON back. `generate_all_images()` iterates prompts and downloads results.

### publisher.py
- `post_single_tweet()` — shells out to `twitter` CLI via subprocess, supports `--reply-to`
- `strip_markdown()` — converts Markdown to plain text for tweet posting
- `split_into_chunks()` — splits polished markdown into ≤140-char tweet segments with image assignment
- `PostQueue` class — thread-safe background posting queue using `threading.Lock` (NOT `st.session_state`)
- Queue persistence: `save_queue_json()` / `load_queue_json()` / `scan_incomplete_queues()` — saves `_queue.json` to archive dir for crash recovery
- Module-level singleton: `post_queue = PostQueue()`

### archive.py
`archive_to_markdown()` saves polished MD + images to `2026/MM.DD/{slug}.md` with slug-based filenames. Optionally appends tweet links with last URL marked as thread entry.

### x_grok_poster.py (UI)
4-step Streamlit flow: input draft → polish → generate images → preview & publish/archive. Uses `@st.fragment(run_every=10)` for partial refresh during thread posting. On startup, scans for incomplete queues and offers resume.

## Key External Dependencies

- **xAI API** (`https://api.x.ai/v1`): Chat completions (model `grok-4.3`) and image generation (`grok-imagine-image-quality`). Auth via `AI_API_KEY` in `.env`.
- **twitter-cli** (`pipx install twitter-cli`): Posting to X. Auth via `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` env vars (Cookie-based, set in shell profile, not in `.env`).

## Archive Format

Posts are saved to `YYYY/MM.DD/{slug}.md` with co-located images (`{slug}_cover.png`, `{slug}_illustration-N.png`). Image references in the markdown use relative filenames. Tweet links are appended at the bottom, with the last URL marked as thread entry.

## Posting Notes

- Twitter standard accounts: ~280 weighted characters per tweet. CJK chars count as 2.
- Thread tweets are posted as self-replies (`--reply-to`). X shows these as nested replies; the full thread is visible from the last tweet.
- Thread posting uses a background queue with 1-10 minute random delays between posts to avoid rate limiting.
- Queue state is persisted to `{slug}_queue.json` for crash recovery.
