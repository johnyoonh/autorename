<div align="center">
  <h1>AutoRename-PDF</h1>
  <p><b>Automatically rename PDF files using AI and OCR.</b><br>
  Extracts company names, dates, and document types from any PDF — renames to <code>YYYYMMDD COMPANY DOCTYPE.pdf</code>.</p>
  <p>
    <a href="https://github.com/ptmrio/autorename-pdf/releases"><img src="https://img.shields.io/github/v/release/ptmrio/autorename-pdf" alt="GitHub Release"></a>
    <a href="https://github.com/ptmrio/autorename-pdf/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ptmrio/autorename-pdf" alt="MIT License"></a>
    <a href="https://github.com/ptmrio/autorename-pdf/releases"><img src="https://img.shields.io/github/downloads/ptmrio/autorename-pdf/total" alt="Downloads"></a>
    <a href="https://github.com/ptmrio/autorename-pdf/releases"><img src="https://img.shields.io/badge/platform-Windows-blue?logo=windows" alt="Platform: Windows"></a>
  </p>
</div>

![AutoRename-PDF desktop GUI showing drag-and-drop PDF renaming with AI-extracted metadata](screenshot/autorename-pdf-gui.png)

AutoRename is for people who want a local document-renaming and Downloads-filing
workflow. It owns extraction, naming, grouping, deduplication, and placement in
explicitly allowlisted filesystem roots; it does not manage cloud accounts or
scan remote services through provider APIs.

- **Desktop GUI** — Drag-and-drop files, preview renames before applying, undo if needed
- **5 AI Providers** — OpenAI, Anthropic (Claude), Google Gemini, xAI (Grok), and Ollama for free offline use
- **3-Tier Extraction** — Text extraction (free, instant) + OCR for scanned PDFs + vision for image-only PDFs
- **Windows Integration** — Right-click context menu in Windows Explorer for instant renaming
- **Batch Processing** — Rename hundreds of PDFs at once with automatic company name harmonization
- **Downloads Organizer** — Deduplicate, flatten, group, and file aging downloads into indexed cloud folders
- **Privacy Option** — Run fully offline with Ollama + PaddleOCR — no data leaves your machine

## Quick Start

1. **Download** the latest [release ZIP](https://github.com/ptmrio/autorename-pdf/releases)
2. **Extract** and run `setup.ps1` (right-click → "Run with PowerShell")
3. **Configure** — edit `config.yaml` with your AI provider and API key:
   ```yaml
   ai:
     provider: "openai"       # or anthropic, gemini, xai, ollama
     api_key: "your-key"
   ```
4. **Launch** `autorename-pdf-gui.exe` — or right-click any PDF in Explorer

> `setup.ps1` creates `config.yaml` from the template, adds context menu entries, and optionally installs PaddleOCR for offline OCR of scanned documents.

## Common Questions

**Does it work offline?**
Yes. Use Ollama (free, local AI) + PaddleOCR. No data leaves your machine, no API key needed. See [Ollama Setup](#ollama-setup).

**What types of PDFs does it handle?**
Any PDF: text-based (digital), scanned (OCR), or image-only (vision). All three extraction methods can run together.

**How much does it cost?**
The tool is free and open source. Cloud AI providers charge ~$0.001 per PDF. Ollama is completely free.

**Does it work on macOS or Linux?**
The CLI works cross-platform via Python. The GUI and context menu are Windows-only. See [macOS / Linux](#macos--linux).

<details>
<summary><strong>Table of Contents</strong></summary>

- [Configuration](#configuration)
  - [API Key Setup](#api-key-setup)
  - [Recommended Setups](#recommended-setups)
  - [Provider Models](#provider-models)
  - [Extraction Settings](#extraction-settings)
- [Usage](#usage)
  - [GUI](#gui)
  - [Context Menu](#context-menu)
  - [Command Line](#command-line)
- [Company Name Harmonization](#company-name-harmonization)
- [Ollama Setup](#ollama-setup)
- [macOS / Linux](#macos--linux)
- [Support the Project](#support-the-project)
- [Developer Documentation](#developer-documentation)

</details>

## Configuration

### API Key Setup

There are two ways to configure your API key:

**Method 1 — Directly in `config.yaml`** (simple, portable):

```yaml
ai:
  api_key: "sk-your-actual-key-here"
```

**Method 2 — Via environment variable** (secure, easy to switch providers):

```yaml
ai:
  provider: "openai"
  api_key: "${OPENAI_API_KEY}"
  model: "${OPENAI_EVERDAY_MODEL}"
```

Then create a `.env` file next to your `config.yaml`:

```
OPENAI_API_KEY=sk-your-actual-key-here
OPENAI_EVERDAY_MODEL=gpt-5.4-mini
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

The `${VAR_NAME}` syntax works in any string value in `config.yaml`. A `.env` file placed next to `config.yaml` is loaded automatically without replacing values already exported by your shell. For OpenAI, `OPENAI_API_KEY` and `OPENAI_EVERDAY_MODEL` override legacy YAML values when set; `OPENAI_EVERYDAY_MODEL` is a fallback spelling for the model. Explicit `--model` still takes precedence. Other providers retain their configured values.

> **Tip:** Method 2 lets you store all your API keys in `.env` and switch providers in `config.yaml` by just changing the `${VAR_NAME}` reference. See [OpenAI environment example](docs/openai-environment.example) for a template.

### Recommended Setups

Text extraction (pdfplumber) **always runs** — it's free and instant. OCR and vision are independent add-ons you enable based on your needs.

#### Cloud AI + PaddleOCR (Best Accuracy)

PaddleOCR runs locally for free, cloud AI handles smart extraction. Great for mixed document types.

```yaml
ai:
  provider: "openai"           # or anthropic, gemini, xai
  model: "gpt-5.4"
  api_key: "your-api-key"
pdf:
  ocr: true                    # PaddleOCR enhances scanned docs
  vision: false                # not needed — OCR covers it
```

**Cost:** ~$0.001/PDF. **Requires:** API key + PaddleOCR (~500 MB, installed via `setup.ps1`).

#### Cloud AI + Vision (No Local Setup)

No PaddleOCR needed — the LLM reads page images directly. Best for laptops or low-performance machines.

```yaml
ai:
  provider: "gemini"           # or openai, anthropic, xai
  model: "gemini-3.1-flash-lite"
  api_key: "your-api-key"
pdf:
  ocr: false
  vision: true                 # send page images to LLM
```

**Cost:** ~$0.002/PDF. **Requires:** API key + vision-capable model.

#### Fully Offline (Max Privacy)

Everything runs on your machine. No data leaves your computer, no API keys, no cost.

```yaml
ai:
  provider: "ollama"
  model: "qwen3:4b"            # fast, fits in 3 GB VRAM
  api_key: ""
pdf:
  ocr: true                    # PaddleOCR for scanned docs
  vision: false                # text models are faster
```

**Cost:** Free. **Requires:** [Ollama](#ollama-setup) + PaddleOCR.

### Provider Models

| Provider | Flagship Model | Budget Model |
|----------|---------------|-------------|
| OpenAI | `gpt-5.4` | `gpt-5-mini` |
| Anthropic | `claude-sonnet-4-6` | `claude-haiku-4-5-20251001` |
| Gemini | `gemini-3.1-flash-lite` | `gemini-3-flash-preview` |
| xAI | `grok-4.20-beta-0309-non-reasoning` | — |
| Ollama | `qwen3:8b` | `qwen3:4b` / `llama3.2:3b` |

See `config.yaml.example` for full documentation of all settings.

### Extraction Settings

| Setting | Values | Description |
|---------|--------|-------------|
| `pdf.ocr` | `false` / `true` / `"auto"` | PaddleOCR for scanned PDFs |
| `pdf.vision` | `false` / `true` / `"auto"` | Send page images to LLM |
| `pdf.text_quality_threshold` | `0.0` – `1.0` | Triggers OCR/vision in `"auto"` mode (default: `0.3`) |
| `pdf.max_pages` | integer | Max pages to process per PDF (default: `3`) |
| `image.apple_vision` | `false` / `true` | OCR standalone image files locally with Apple Vision on macOS |
| `image.apple_vision_classify` | `false` / `true` | Add built-in Vision image classification labels as metadata hints |
| `image.coreml_model_path` | path | Optional `.mlmodelc` model used through `VNCoreMLRequest` |
| `image.llm_vision` | `false` / `true` / `"auto"` | Also send standalone image pixels to the configured LLM |
| `image.screenshot_mode` | `false` / `true` / `"auto"` | Add screenshot-specific context for UI screenshots so visible text, tabs, URLs, and headings are prioritized |
| `output.use_source_timestamp_fallback` | `false` / `true` | Use a timestamp already present in the source filename when AI finds no document date |
| `output.preserve_source_timestamp_for_images` | `false` / `true` | Convert screenshot-style timestamps to compact prefixes such as `20240102_142144` for image filenames |
| `output.omit_unknown_components` | `false` / `true` | Omit weak values like `Unknown` instead of writing them into filenames |
| `output.skip_when_no_substantial_description` | `false` / `true` | Leave timestamp-only screenshots unchanged when AI only returns a generic screenshot label |

- **`false`** = disabled (default for both)
- **`true`** = always run alongside text extraction
- **`"auto"`** = run only when text quality falls below threshold

All enabled sources are combined before sending to the AI — maximizing extraction accuracy.

Standalone image files (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.gif`, `.webp`, `.heic`, `.heif`) are supported by the CLI. On macOS, Apple Vision OCR and classification run locally when `image.apple_vision` is enabled. If `image.coreml_model_path` points to a compiled Core ML model, its `VNCoreMLRequest` observations are added as extra hints for the metadata extraction prompt.

## Usage

### macOS Finder

Install two Finder Quick Actions from a stable checkout after running `setup.sh` and configuring `config.yaml`:

```bash
python3 install-finder-actions.py                 # preview installation
python3 install-finder-actions.py --apply         # install
python3 install-finder-actions.py --apply --replace  # back up and replace old actions
```

Select PDFs or supported images in Finder, then choose **Quick Actions** (or **Services**):

- **Preview AutoRename** proposes names without renaming files.
- **Rename with AutoRename** explicitly applies renames in place.

The installer records the checkout path locally and selects its `.venv` or `venv` Python. Use `--repo /path/to/stable/checkout` when running the installer from a temporary development worktree. Reinstall if you move the checkout. Existing actions are preserved unless `--replace` is supplied; replacements keep uniquely named backups in the Services folder.

Both actions use `quickaction-helper.sh`, preserve file extensions, report batch results through macOS notifications, and log diagnostics to `$HOME/Library/Logs/AutoRename-Finder.log`. Preview details appear in that log. Unsupported files are skipped. Provider settings come from the existing local configuration; no routing or inbox action is installed.

### GUI

Launch `autorename-pdf-gui.exe` to open the desktop interface.

- **Drag and drop** PDF files or folders onto the window
- **Dry-run preview** shows proposed renames before applying
- **Undo** reverses the last rename operation
- Supports light and dark themes

### Context Menu

After running `setup.ps1`, right-click in Windows Explorer:

- **Single PDF**: Right-click a PDF → `Auto Rename PDF`
- **Folder of PDFs**: Right-click a folder → `Auto Rename PDFs in Folder`
- **Current Folder**: Right-click folder background → `Auto Rename PDFs in This Folder`

> **Windows 11 Note:** Context menu entries appear under "Show more options" (Shift+F10).

### Command Line

![AutoRename-PDF CLI showing PDF renaming in PowerShell](screenshot/autorename-pdf-cli.png)

```bash
# Rename a single PDF
autorename.exe "C:\path\to\file.pdf"

# Rename a standalone image file
autorename.exe "C:\path\to\receipt.png"

# Preview what would be renamed (no changes made)
autorename.exe --dry-run "C:\path\to\folder"

# Process folders recursively
autorename.exe --recursive "C:\path\to\folder"

# Undo the last rename operation
autorename.exe undo

# Override AI provider/model for one run
autorename.exe --provider anthropic --model claude-sonnet-4-6 "file.pdf"

# Enable vision and/or OCR
autorename.exe --vision --ocr "scanned_document.pdf"

# JSON output (for scripting / GUI integration)
autorename.exe rename --output json "C:\path\to\folder"
```

### Indexed Downloads workflow

Copy `root-structure.yaml.example` to the private `root-structure.yaml`, then
replace the placeholder cloud root and destinations with locally synced paths.
Build the metadata catalog without hashing every file:

```bash
autorename organize index
```

Run `autorename organize intake "$HOME/Downloads" --apply` every five minutes.
The first scan records file size and modification time; a later unchanged scan
performs lazy exact-hash matching, removes the incoming duplicate, and surfaces
the indexed canonical file.

Preview files older than 30 days. Nested ordinary folders are flattened, related
files are grouped, and indexed tags, aliases, and existing filenames determine
the destination. If no destination reaches the confidence threshold, a new
group is created below the configured managed inbox.

```bash
autorename organize run "$HOME/Downloads"
```

Apply a reviewed batch explicitly, or use this form from a scheduler:

```bash
autorename organize run "$HOME/Downloads" --apply
```

Exact duplicate content is removed only after the indexed canonical file is
re-hashed. Its modification time is updated so it appears among recent files.
Every action is journaled and can be reversed with
`autorename organize undo <run-id>`. Agy and sandboxed Codex Luna are optional
tie-breakers for ambiguous destinations; edit `organize.recognition_backends`
to disable either backend.

<details>
<summary><strong>Full CLI Reference</strong></summary>

#### Subcommands

| Subcommand | Description |
|------------|-------------|
| `rename` | Rename PDF/image files (default if omitted) |
| `organize run` | Preview or apply Downloads organization |
| `organize intake` | Detect settled exact duplicates |
| `organize index` | Refresh configured cloud-root metadata |
| `organize status` | Display index freshness and counts |
| `organize undo` | Reverse one organization run |
| `undo` | Reverse file renames using the undo log |
| `config show` | Display current configuration (API keys redacted) |
| `config validate` | Validate configuration and report issues |

#### Rename Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Show what would be renamed without doing it |
| `--recursive`, `-r` | Process folders recursively |
| `--provider` | Override AI provider from config |
| `--model` | Override model from config |
| `--vision` | Enable vision (send page images to LLM) |
| `--ocr` | Enable PaddleOCR |
| `--text-only` | Disable OCR and vision (text extraction only) |
| `--output`, `-o` | Output format: `text` or `json` (default: auto-detect) |
| `--quiet`, `-q` | Suppress non-essential output |
| `--verbose`, `-v` | Show detailed processing info |

#### Undo Options

| Flag | Description |
|------|-------------|
| `--list` | List available undo batches without undoing |
| `--batch <id>` | Undo a specific batch by ID |
| `--all` | Undo all batches (not just the last one) |

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Usage error |
| 3 | Configuration error |
| 4 | No files found |
| 5 | Partial failure (some files failed) |
| 10 | AI provider error |
| 11 | Authentication error |

</details>

## Company Name Harmonization

Standardize company name variations using `harmonized-company-names.yaml`:

```yaml
ACME:
    - "ACME Corp"
    - "ACME Inc."
    - "ACME Corporation"

XYZ:
    - "XYZ Ltd"
    - "XYZ LLC"
    - "XYZ Enterprises"
```

The tool uses fuzzy matching (Jaro-Winkler similarity) to automatically map extracted names to their standardized form, even with OCR typos.

**Quick Tip:** Copy your PDF filenames, paste them into ChatGPT/Claude/Gemini with _"Create a harmonized-company-names.yaml mapping these company name variations to standardized names"_ — then save the result.

<details>
<summary><strong><a id="ollama-setup">Ollama Setup (Free Local AI)</a></strong></summary>

Ollama runs AI models entirely on your machine — no API key, no cloud, no cost per request.

#### Recommended Models

| Model | VRAM | Best for | Download |
|-------|------|----------|----------|
| `qwen3:8b` | ~5 GB | Most PDFs (text extraction) | ~5 GB |
| `qwen3-vl:8b` | ~6 GB | Scanned/image PDFs (vision) | ~6 GB |
| `qwen3:4b` | ~3 GB | Budget GPU / less VRAM | ~2.5 GB |
| `llama3.2:3b` | ~2.5 GB | Minimal hardware | ~2 GB |

#### Setup

1. **Install Ollama:**
   ```powershell
   winget install -e --id Ollama.Ollama
   ```
   Or download from [ollama.com/download](https://ollama.com/download).

2. **Pull a model:**
   ```powershell
   ollama pull qwen3:4b
   ```

3. **Configure** `config.yaml`:
   ```yaml
   ai:
     provider: "ollama"
     model: "qwen3:4b"
     api_key: ""
   ```

**Requirements:** Windows 10 22H2+ or Windows 11. GPU with 6+ GB VRAM recommended (works CPU-only with 16+ GB RAM but slower). See [ollama.com](https://ollama.com) for troubleshooting, GPU setup, and model management.

</details>

<details>
<summary><strong><a id="macos--linux">macOS / Linux (Run from Python Source)</a></strong></summary>

The CLI works cross-platform via Python. The GUI and context menu are Windows-only.

### Automated Setup (Recommended)

```bash
# Clone the repository
git clone https://github.com/ptmrio/autorename-pdf.git
cd autorename-pdf

# Run the setup script
chmod +x setup.sh
./setup.sh
```

The `setup.sh` script will:
- ✓ Check Python 3.11+ installation
- ✓ Create a virtual environment
- ✓ Install all dependencies
- ✓ Create `config.yaml` from template
- ✓ Optionally install PaddleOCR for offline OCR
- ✓ Optionally create a shell alias for easy access

After setup, edit `config.yaml` with your AI provider API key and run:

```bash
source venv/bin/activate
autorename --dry-run invoice.pdf
autorename invoice.pdf
```

### Manual Setup

If you prefer manual installation:

```bash
# Clone and set up
git clone https://github.com/ptmrio/autorename-pdf.git
cd autorename-pdf
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and edit config
cp config.yaml.example config.yaml
# Edit config.yaml with your AI key and provider

# Run
./autorename --dry-run invoice.pdf
./autorename invoice.pdf
```

**Platform notes:**
- **Requires:** Python 3.11 or later
- **macOS:** PaddleOCR venv defaults to `~/Library/Application Support/autorename-pdf/paddleocr-venv`
- **Linux:** PaddleOCR venv defaults to `~/.local/share/autorename-pdf/paddleocr-venv`
- Log files are written to platform-specific application data directories
- All core functionality (text extraction, AI processing, renaming) works cross-platform

</details>

## Support the Project

If AutoRename-PDF saves you time, consider supporting its development:

- ⭐ [Star this repo](https://github.com/ptmrio/autorename-pdf) on GitHub
- 💖 [Sponsor on GitHub](https://github.com/sponsors/ptmrio)
- ☕ [Buy me a coffee on Ko-fi](https://ko-fi.com/spqrk)
- 💛 [Donate via PayPal](https://paypal.me/realSPQRK)

Also check out [PhraseVault](https://phrasevault.app) — a text expander and snippet manager by the same developer.

### Thank You to Our Supporters

- [@claus82](https://github.com/claus82) — Thank you for your generous donation!

<details>
<summary><strong><a id="developer-documentation">Developer Documentation</a></strong></summary>

## Development Setup

**Prerequisites:** Python 3.11+, Git

```bash
git clone https://github.com/ptmrio/autorename-pdf.git
cd autorename-pdf
python -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
cp config.yaml.example config.yaml
```

## Architecture

Functional Python (no classes). Modules prefixed with `_` are internal:

| Module | Purpose |
|--------|---------|
| `autorename-pdf.py` | Entry point, CLI (argparse), orchestration |
| `_ai_processing.py` | OpenAI Responses and compatible providers, structured output (Pydantic) |
| `_pdf_utils.py` | Text extraction (pdfplumber), image rendering (pypdfium2), PaddleOCR bridge |
| `_paddleocr_bridge.py` | Subprocess bridge script for PaddleOCR venv |
| `_document_processing.py` | Company harmonization (rapidfuzz), renaming, undo log |
| `_config_loader.py` | YAML v2 config loading, schema validation, defaults |
| `_utils.py` | Filename validation, constants |

## AI Providers (Technical)

OpenAI uses native [Responses structured output](https://developers.openai.com/api/docs/guides/structured-outputs) through `responses.parse(text_format=DocumentMetadata)` for text, images, and mixed inputs. Response storage is disabled (`store=False`), and OpenAI sampling parameters use the selected model’s defaults. Other providers retain [instructor](https://github.com/jxnl/instructor) for structured Pydantic output.

OpenAI HTTP 401/403 stops the rename batch and returns exit code 11. JSON marks the rejected file as `auth_error` and remaining files as `not_attempted`; earlier successful renames and undo records are preserved. An `invalid_api_key` rejection requires replacing `OPENAI_API_KEY`; a missing `model.request` scope requires correcting the key/project role permissions. A dry run still calls the provider, but does not rename files.

| Provider | SDK | Notes |
|----------|-----|-------|
| `openai` | openai (native) | Responses API, strict schema parsing |
| `anthropic` | anthropic (native) | Uses `instructor.from_anthropic()` — Anthropic's OpenAI compat layer ignores structured output |
| `gemini` | openai (base_url) | Google's OpenAI-compatible endpoint |
| `xai` | openai (base_url) | Grok models |
| `ollama` | openai (base_url) | Local models, no API key needed |

## Testing

```bash
python tests/generate_test_pdfs.py  # synthetic fixtures, ignored by Git
pytest tests/ -v --cov
```

Unit tests mock AI API calls. Business logic (harmonization, date parsing, filename generation) should have >80% coverage.

### Live Tests

```bash
pytest tests/ --run-live -v                       # All available providers
pytest tests/ --run-live --provider ollama -v      # Free, local only
pytest tests/ --run-live --provider openai -v      # OpenAI only
pytest tests/ --run-live --provider anthropic -v   # Anthropic only
```

API keys are loaded from `.env` file (see `.env.example`). Ollama tests require Ollama running locally.

## Building

```bash
python build.py                  # Build everything, sign all
python build.py --nosign         # Build everything, skip signing
python build.py --cli-only       # Build CLI EXE only (skip GUI + packaging)
```

**Build pipeline:** CLI EXE (PyInstaller) → Sign (Azure Trusted Signing) → Tauri GUI → Portable ZIP

**Output** (in `Releases/`): `AutoRename-PDF-Portable-{version}.zip`

## AI-Assisted Development

This repository is **AI-ready** — it includes configuration and skills for [Claude Code](https://claude.ai/code) and compatible AI tools (Cursor, Gemini CLI, Codex CLI, etc.).

**What's included in `.claude/`:**

| Path | Purpose |
|------|---------|
| `CLAUDE.md` | Codebase context — architecture, commands, conventions |
| `.claude/settings.json` | Shared permissions for common dev operations |
| `.claude/skills/` | 4 slash-command skills (see below) |
| `.claude/agents/pdf-debugger.md` | Specialized agent for diagnosing PDF processing issues |

**Available skills:**

| Skill | Description |
|-------|-------------|
| `/rename-pdfs <path>` | Rename PDFs from Python source (dev / cross-platform) |
| `/rename-pdfs-exe <path>` | Rename PDFs using compiled EXE (production / Windows) |
| `/build` | Build EXE + distribution ZIP |
| `/test [pattern]` | Run test suite with coverage |

**For end users** (release ZIP): The `rename-pdfs-exe` skill is included in the ZIP. Copy `.claude/skills/rename-pdfs-exe/` to `~/.claude/skills/` for global availability across all your projects.

The [SKILL.md format](https://docs.anthropic.com/en/docs/claude-code/skills) is an open standard — these skills work with any AI tool that supports it.

## Contributing

We're currently not accepting direct contributions to maintain project consistency. You're welcome to:

- **Open an issue** to report bugs, request features, or ask questions
- **Create your own fork** to customize the tool for your needs
- **Share feedback** about your experience using the tool

</details>

---

MIT License — Made by [Gerhard Petermeir, SPQRK Web Solutions](https://github.com/ptmrio)
