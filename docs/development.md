# AutoRename-PDF

PDF/image auto-renamer using AI + OCR. Extracts company name, date, and document type from PDFs or standalone images, renames to `YYYYMMDD COMPANY DOCTYPE.ext`.

## Development Setup

**Always use a virtual environment.** Never install dependencies globally.

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
```

## Commands

All commands assume the venv is activated.

- **Run**: `autorename [options] <files_or_folders>`
- **Dry run**: `autorename --dry-run <files_or_folders>`
- **Undo**: `autorename undo`
- **Test**: `pytest tests/ -v --cov`
- **Build EXE**: `python build.py`
- **Install deps**: `pip install -r requirements.txt`

## AI Tool Integration

- `.claude/` is committed to git — skills, agents, and shared settings are available to all developers.
- `settings.local.json` is gitignored (personal overrides).
- **Skill (dev)**: `/rename-pdfs <path>` — Python source, requires venv. For development and macOS/Linux.
- **Skill (prod)**: `/rename-pdfs-exe <path>` — compiled EXE, Windows only. For production/automation.
- Both skills dry-run first, then ask for confirmation before renaming.
- **CLI JSON mode**: `autorename rename <path> --output json`
- **Exit codes**: 0=success, 1=error, 2=usage, 3=config, 4=no files, 5=partial, 10=provider, 11=auth
- **Subcommands**: `rename`, `undo`, `config show`, `config validate`
- **Primary usage**: GUI app (Tauri) or Windows context menu via compiled EXE from release ZIP.

## Architecture

Functional Python (no classes). Modules prefixed with `_` are internal:

| Module | Purpose |
|--------|---------|
| `autorename-pdf.py` | Entry point, CLI (argparse), orchestration |
| `_ai_processing.py` | OpenAI Responses plus instructor-compatible providers, Pydantic output |
| `_pdf_utils.py` | Text extraction (pdfplumber), image rendering (pypdfium2), PaddleOCR bridge, standalone image extraction |
| `_apple_vision.py` | Optional macOS Apple Vision OCR/classification and VNCoreMLRequest hints |
| `_paddleocr_bridge.py` | Subprocess bridge script for PaddleOCR venv |
| `_document_processing.py` | Company harmonization (rapidfuzz), renaming, undo log |
| `_config_loader.py` | YAML v2 config loading, schema validation, defaults |
| `_utils.py` | Filename validation, constants |

## Key Rules

- **NEVER commit `config.yaml`** — contains API keys. Only commit `config.yaml.example`.
- **NEVER commit `harmonized-company-names.yaml`** — user-specific data. Only commit the `.example`.
- Platform: Windows-only (context menu EXE via PyInstaller)
- Config: YAML-based (`config.yaml`), v2 schema — see `config.yaml.example`
- Python 3.11+, type hints encouraged
- Company name matching uses Jaro-Winkler similarity (rapidfuzz library)
- Date parsing uses dateparser with DMY locale
- OCR: PaddleOCR via isolated subprocess venv (optional, installed by setup.ps1); Apple Vision for standalone images on macOS when PyObjC is installed

## AI Providers

Supports 5 providers via `ai.provider` config key:

| Provider | SDK | Notes |
|----------|-----|-------|
| `openai` | openai (native) | Default |
| `anthropic` | anthropic (native) | Uses `instructor.from_anthropic()` |
| `gemini` | openai (base_url) | Google's OpenAI-compatible endpoint |
| `xai` | openai (base_url) | Grok models |
| `ollama` | openai (base_url) | Local models, no API key needed |

OpenAI uses native Responses structured output with response storage disabled. Other providers use `instructor` for structured Pydantic output. Anthropic uses native SDK because their OpenAI compat layer ignores structured output.

## Three-Tier Extraction

1. **Tier 1**: pdfplumber text extraction (free, instant)
2. **Tier 2a**: PaddleOCR via subprocess (free, ~2-5s/page, if installed)
3. **Tier 2b**: Vision mode — page images sent to LLM (~$0.0001/page)
4. **Image files**: Apple Vision OCR/classification locally on macOS, optional VNCoreMLRequest model hints, optional LLM vision pixels

Controlled by `pdf.ocr` (false/true/"auto") and `pdf.vision` (false/true/"auto").

## Environment Variables

Config values support `${VAR_NAME}` syntax for environment variable references.
A `.env` file next to `config.yaml` is loaded automatically via `python-dotenv`.

## Testing

Tests in `tests/` using pytest. Unit tests mock AI API calls. Live integration tests (`--run-live`) call real providers.
Business logic (harmonization, date parsing, filename generation) should have >80% coverage.

### Live Tests

```bash
pytest tests/ --run-live -v                       # All available providers
pytest tests/ --run-live --provider ollama -v      # Free, local only
pytest tests/ --run-live --provider openai -v      # OpenAI only
pytest tests/ --run-live --provider anthropic -v   # Anthropic only
```

API keys are loaded from `.env` file (see `.env.example`). Ollama tests require Ollama running locally.

## Build & Distribution

`build.py` creates signed EXE via PyInstaller, packages as ZIP with setup.ps1.
`setup.ps1` installs context menu entries and optionally PaddleOCR (~500MB).

**GUI platform support**: Windows only. The Tauri GUI invokes the CLI as a sidecar binary (compiled EXE). Cross-platform would require PyInstaller builds for each target OS — the TypeScript/Rust code is already platform-agnostic.

## Repository safety and ownership


## Ownership
- This repository owns generic document OCR verification/execution, canonical naming, classification, route-readiness state, collision safety, audit/cache behavior, and generic routing mechanics.
- Do not commit personal cloud paths, account identifiers, scheduling policy, or private destination mappings here. Supply those through external routing configuration.

## Safety
- Preview is the default for new processing and routing workflows; mutations require an explicit apply action.
- Do not infer processing completion from a filename alone. Route readiness requires verified searchable text, a canonical filename, sufficient classification confidence, and no review condition.
- Do not resolve same-name files by blindly adding numeric suffixes in the normalization/routing path. Hash candidates; exact duplicates and different-content collisions remain non-destructive review outcomes.
- Do not automatically delete duplicate source files.

## Command surface
- `autorename` is the canonical command family. `autorename-pdf` is a compatibility name during migration.
- Generic reusable routes are `process` and `route`; `organize` remains the Downloads-specific compatibility workflow.
- Keep `commands.toml` synchronized with PATH-visible command changes.

## Verification
- Use synthetic public fixtures only.
- Run the focused normalization/pipeline tests and the existing repository test suite after processing changes.
