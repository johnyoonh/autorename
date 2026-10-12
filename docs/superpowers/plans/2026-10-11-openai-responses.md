# OpenAI Responses migration

Goal: use native OpenAI Responses structured parsing for document metadata while keeping the existing schema, filename rules, other provider implementations, and preview/apply behavior.

1. Add synthetic HTTP transport regressions proving `/v1/responses` receives text and image inputs plus strict metadata schema, no server-side response storage, and the configured model. Cover refusal/incomplete responses and authentication failure without retries.
2. In `_ai_processing.py`, route OpenAI text/image/mixed extraction to `responses.parse(text_format=DocumentMetadata)`. Use bounded SDK transient retries; reject absent parsed metadata. Retain instructor for other providers. Set the tested SDK minimum in `requirements.txt`.
3. In `_config_loader.py`, prefer `OPENAI_API_KEY` and `OPENAI_EVERDAY_MODEL` for OpenAI only. Accept `OPENAI_EVERYDAY_MODEL` as a fallback spelling. Existing process environment wins over adjacent `.env`; existing CLI overrides remain last. Retain legacy YAML fallback when variables are absent. Update examples and documentation.
4. Preserve a regression for CLI batch authorization failure. Mark the failed file and unattempted remainder explicitly, stop calls, preserve earlier success/undo state, and return AUTH_ERROR. Sanitize provider errors instead of printing credentials or document content.
5. Run focused tests, normalization/pipeline tests, and full synthetic suite. Obtain code review; repair findings. Re-run a real folder preview using the task checkout and existing external config. Do not apply renames while credentials or proposed names remain unresolved.
6. Commit and push only task changes. Keep the synchronization checkout unchanged. Report source verification separately from installed command and real-provider success.
