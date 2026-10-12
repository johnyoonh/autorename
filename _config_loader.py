"""
Configuration loader for YAML files.
Handles v2 config schema, validation, defaults, and old schema detection.
"""
from __future__ import annotations

import os
import re
import sys
import logging
import shlex
import subprocess
from typing import Any

import yaml

# Pattern for environment variable references: ${VAR_NAME}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _openai_key_from_bwa() -> str:
    """Read one credential without evaluating shell output or logging secrets."""
    try:
        result = subprocess.run(
            ["bwa", "env", "OPENAI_API_KEY"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Could not read OpenAI key from bwa; check installation and run bwa unlock.") from None
    if result.returncode != 0:
        raise ValueError("Could not read OpenAI key from bwa; run bwa unlock and check OPENAI_API_KEY.")
    try:
        parts = shlex.split(result.stdout)
    except ValueError:
        parts = []
    prefix = "OPENAI_API_KEY="
    if len(parts) != 2 or parts[0] != "export" or not parts[1].startswith(prefix):
        raise ValueError("Invalid credential output from bwa.")
    key = parts[1][len(prefix):]
    if not key.strip() or any(char.isspace() for char in key):
        raise ValueError("Empty or invalid OpenAI key from bwa.")
    return key


def _load_dotenv_from(config_dir: str) -> None:
    """Load .env file from the same directory as config.yaml (if it exists)."""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(config_dir, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
            logging.debug(f"Loaded .env from {env_path}")
    except ImportError:
        pass  # python-dotenv not installed — rely on OS environment


# Default config values for v2 schema
DEFAULTS = {
    "config_version": 2,
    "ai": {
        "provider": "openai",
        "model": "gpt-5.4",
        "api_key": "",
        "api_key_source": "config",
        "base_url": "",
        "temperature": 0.0,
        "max_retries": 2,
    },
    "pdf": {
        "max_pages": 3,
        "ocr": False,
        "vision": False,
        "text_quality_threshold": 0.3,
        "outgoing_invoice": "AR",
        "incoming_invoice": "ER",
    },
    "image": {
        "apple_vision": True,
        "apple_vision_recognition_level": "accurate",
        "apple_vision_language_correction": True,
        "apple_vision_languages": ["en-US"],
        "apple_vision_classify": True,
        "apple_vision_max_classifications": 8,
        "apple_vision_min_confidence": 0.15,
        "llm_vision": False,
        "screenshot_mode": "auto",
        "coreml_model_path": "",
        "coreml_max_observations": 10,
    },
    "paddleocr": {
        "venv_path": "",
        "language": "en",
        "device": "auto",
        "detection_model": "",
        "det_limit_side_len": 736,
        "cpu_threads": 4,
    },
    "company": {
        "name": "",
    },
    "output": {
        "language": "English",
        "date_format": "%Y%m%d",
        "use_source_timestamp_fallback": True,
        "preserve_source_timestamp_for_images": True,
        "omit_unknown_components": True,
        "skip_when_no_substantial_description": True,
    },
    "organize": {
        "recognition_backends": ["agy", "codex"],
        "recognition_timeout": 60,
        "maximum_files": 100,
    },
    "prompt_extension": "",
}


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} references with environment variable values.

    If the env var is not set, the reference is left unchanged so the user
    sees a clear error (e.g. 'API key required') rather than an empty string.
    """
    def _replace(match):
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        logging.warning(f"Environment variable ${{{var_name}}} is not set")
        return match.group(0)  # leave ${VAR} as-is

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _interpolate_env_vars(config: dict) -> dict:
    """Walk config dict and resolve ${VAR} references in string values."""
    result = {}
    for key, value in config.items():
        if isinstance(value, dict):
            result[key] = _interpolate_env_vars(value)
        elif isinstance(value, str) and "${" in value:
            result[key] = _resolve_env_vars(value)
        else:
            result[key] = value
    return result


def _deep_merge(defaults: dict, overrides: dict) -> dict:
    """Merge overrides into defaults, recursing into nested dicts."""
    result = defaults.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _detect_old_schema(config: dict) -> bool:
    """Detect v1 config schema by checking for old top-level keys."""
    return "openai" in config and "api_key" in config.get("openai", {})


def _print_migration_instructions():
    """Print instructions for migrating from v1 to v2 config schema."""
    msg = """
CONFIG MIGRATION REQUIRED

Your config.yaml uses the old v1 schema.
Please update it to v2 format.

See config.yaml.example for the new schema.

Key changes:
  openai.api_key     -> ai.api_key
  openai.model       -> ai.model
  private_ai.*       -> REMOVED (use ai.provider: "ollama")
  output_language    -> output.language
  date_format        -> output.date_format
  ocr_languages      -> paddleocr.language

New:
  ai.provider supports openai, anthropic, gemini, xai, and ollama.
"""
    print(msg)


def _migrate_extraction_config(config: dict) -> dict:
    """Translate old extraction_mode/ocr_fallback keys to new ocr/vision keys.

    Logs a deprecation warning if old keys are found.
    """
    pdf = config.get("pdf", {})
    mode = pdf.pop("extraction_mode", None)
    fallback = pdf.pop("ocr_fallback", None)

    if mode is None and fallback is None:
        return config

    logging.warning(
        "Deprecated config keys 'extraction_mode' and/or 'ocr_fallback' detected. "
        "Please migrate to 'ocr' and 'vision'. See config.yaml.example."
    )

    if mode == "vision_only":
        pdf.setdefault("ocr", False)
        pdf.setdefault("vision", True)
    elif mode == "text_only":
        pdf.setdefault("ocr", False)
        pdf.setdefault("vision", False)
    else:
        # mode is "auto" or unset — interpret fallback
        if fallback == "paddleocr":
            pdf.setdefault("ocr", "auto")
            pdf.setdefault("vision", False)
        elif fallback == "vision":
            pdf.setdefault("ocr", False)
            pdf.setdefault("vision", "auto")
        else:
            # fallback == "none" or unset
            pdf.setdefault("ocr", False)
            pdf.setdefault("vision", False)

    config["pdf"] = pdf
    return config


def _migrate_paddleocr_config(config: dict) -> dict:
    """Translate old use_gpu boolean to new device enum (auto/cpu/gpu).

    Logs a deprecation warning if the old key is found.
    """
    paddleocr = config.get("paddleocr", {})
    use_gpu = paddleocr.pop("use_gpu", None)

    if use_gpu is None:
        return config

    logging.warning(
        "Deprecated config key 'use_gpu' in paddleocr section. "
        "Please migrate to 'device' (auto/cpu/gpu). See config.yaml.example."
    )
    paddleocr.setdefault("device", "gpu" if use_gpu else "auto")
    config["paddleocr"] = paddleocr
    return config


def _migrate_paddleocr_languages(config: dict) -> dict:
    """Translate old languages list to new language string.

    Logs a deprecation warning if the old key is found.
    """
    paddleocr = config.get("paddleocr", {})
    languages = paddleocr.pop("languages", None)

    if languages is None:
        return config

    logging.warning(
        "Deprecated config key 'languages' (list) in paddleocr section. "
        "Please migrate to 'language' (single string). See config.yaml.example."
    )
    lang = languages[0] if languages else "en"
    paddleocr.setdefault("language", lang)
    config["paddleocr"] = paddleocr
    return config


def load_yaml_config(config_path: str, *, provider: str | None = None) -> dict[str, Any] | None:
    """Load and validate configuration from a YAML file.

    Returns a fully-merged config dict with defaults applied,
    or None if the file doesn't exist.
    Exits with error if old schema is detected.
    """
    if not os.path.exists(config_path):
        logging.warning(f'Config file {config_path} not found')
        return None

    # Load .env from the same directory as config.yaml
    _load_dotenv_from(os.path.dirname(os.path.abspath(config_path)))

    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            raw_config = yaml.safe_load(file)
            if not raw_config:
                logging.error(f'Config file {config_path} is empty')
                return None

            # Detect old v1 schema
            if _detect_old_schema(raw_config):
                _print_migration_instructions()
                sys.exit(1)

            # Migrate old extraction keys before merging
            _migrate_extraction_config(raw_config)
            _migrate_paddleocr_config(raw_config)
            _migrate_paddleocr_languages(raw_config)

            # Merge with defaults
            config = _deep_merge(DEFAULTS, raw_config)

            # Resolve the effective provider before any credential overlay.
            config["ai"]["provider"] = provider or _resolve_env_vars(config["ai"]["provider"])

            # OpenAI environment values supersede legacy YAML literals. CLI
            # overrides are applied by the command handlers after this load.
            if config["ai"]["provider"] == "openai":
                source = config["ai"].get("api_key_source", "config")
                if source not in ("config", "bwa"):
                    raise ValueError("ai.api_key_source must be config or bwa.")
                env_key = os.environ.get("OPENAI_API_KEY")
                env_model = os.environ.get("OPENAI_EVERDAY_MODEL") or os.environ.get("OPENAI_EVERYDAY_MODEL")
                if source == "bwa":
                    config["ai"]["api_key"] = _openai_key_from_bwa()
                elif env_key:
                    config["ai"]["api_key"] = env_key
                if env_model:
                    config["ai"]["model"] = env_model

            # Resolve ${VAR} environment variable references
            config = _interpolate_env_vars(config)

            # Auto-detect PaddleOCR venv path if not set
            if not config["paddleocr"]["venv_path"]:
                if sys.platform == "win32":
                    base = os.environ.get("LOCALAPPDATA", "")
                else:
                    base = os.path.join(os.path.expanduser("~"), ".local", "share")
                if base:
                    config["paddleocr"]["venv_path"] = os.path.join(
                        base, "autorename-pdf", "paddleocr-venv"
                    )

            logging.info(f'Successfully loaded config from {config_path}')
            return config

    except yaml.YAMLError as e:
        logging.error(f'Error parsing YAML config file {config_path}: {e}')
        return None
    except SystemExit:
        raise
    except Exception as e:
        logging.error(f'Error loading config file {config_path}: {e}')
        return None


def load_company_names(yaml_path: str) -> dict[str, list]:
    """Load harmonized company names from YAML file."""
    if not os.path.exists(yaml_path):
        logging.warning(f'Company names file {yaml_path} not found')
        return {}

    try:
        with open(yaml_path, 'r', encoding='utf-8') as file:
            company_names = yaml.safe_load(file)
            if not company_names:
                return {}
            logging.info(f'Successfully loaded {len(company_names)} company name mappings')
            return company_names
    except yaml.YAMLError as e:
        logging.error(f'Error parsing YAML company names file {yaml_path}: {e}')
        return {}
    except Exception as e:
        logging.error(f'Error loading company names file {yaml_path}: {e}')
        return {}
