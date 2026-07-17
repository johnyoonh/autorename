"""Tests for _ai_processing.py."""

import os
import sys
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _ai_processing import (
    DocumentMetadata,
    build_system_prompt,
    pil_to_base64_data_uri,
    get_ai_client,
    extract_metadata,
    _build_combined_text,
)
from _pdf_utils import ExtractionResult


class TestDocumentMetadata:
    def test_valid_metadata(self):
        m = DocumentMetadata(
            company_name="ACME",
            document_date="15.03.2024",
            document_type="ER"
        )
        assert m.company_name == "ACME"
        assert m.document_date == "15.03.2024"
        assert m.document_type == "ER"

    def test_empty_values(self):
        m = DocumentMetadata(
            company_name="",
            document_date="",
            document_type=""
        )
        assert m.company_name == ""


class TestBuildSystemPrompt:
    def test_contains_company_name(self, sample_config):
        prompt = build_system_prompt(sample_config)
        assert "Test Company" in prompt

    def test_contains_invoice_codes(self, sample_config):
        prompt = build_system_prompt(sample_config)
        assert "ER" in prompt
        assert "AR" in prompt

    def test_contains_language(self, sample_config):
        prompt = build_system_prompt(sample_config)
        assert "English" in prompt

    def test_custom_invoice_codes(self, sample_config):
        sample_config["pdf"]["incoming_invoice"] = "EIN"
        sample_config["pdf"]["outgoing_invoice"] = "AUS"
        prompt = build_system_prompt(sample_config)
        assert "EIN" in prompt
        assert "AUS" in prompt

    def test_prompt_extension(self, sample_config):
        sample_config["prompt_extension"] = "Also check for VAT numbers."
        prompt = build_system_prompt(sample_config)
        assert "Also check for VAT numbers." in prompt

    def test_no_company_name(self, sample_config):
        sample_config["company"]["name"] = ""
        prompt = build_system_prompt(sample_config)
        assert "main company" in prompt


class TestPilToBase64DataUri:
    def test_png_format(self, sample_pil_image):
        uri = pil_to_base64_data_uri(sample_pil_image, fmt="PNG")
        assert uri.startswith("data:image/png;base64,")

    def test_jpeg_format(self, sample_pil_image):
        uri = pil_to_base64_data_uri(sample_pil_image, fmt="JPEG")
        assert uri.startswith("data:image/jpeg;base64,")

    def test_non_empty_base64(self, sample_pil_image):
        uri = pil_to_base64_data_uri(sample_pil_image)
        base64_part = uri.split(",")[1]
        assert len(base64_part) > 0


class TestGetAiClient:
    def test_unknown_provider_raises(self, sample_config):
        sample_config["ai"]["provider"] = "unknown_provider"
        with pytest.raises(ValueError, match="Unknown provider"):
            get_ai_client(sample_config)

    def test_missing_api_key_raises(self, sample_config):
        sample_config["ai"]["api_key"] = ""
        with pytest.raises(ValueError, match="API key required"):
            get_ai_client(sample_config)

    def test_ollama_no_api_key_ok(self, sample_config):
        sample_config["ai"]["provider"] = "ollama"
        sample_config["ai"]["api_key"] = ""
        # Should not raise — ollama doesn't need an API key
        client = get_ai_client(sample_config)
        assert client is not None

    @patch("_ai_processing.OpenAI")
    @patch("_ai_processing.instructor")
    def test_openai_client(self, mock_instructor, mock_openai, sample_config):
        client = get_ai_client(sample_config)
        mock_openai.assert_called_once_with(
            api_key="test-key-123", base_url=None, max_retries=2
        )
        assert client is mock_openai.return_value
        mock_instructor.from_openai.assert_not_called()

    @patch("_ai_processing.OpenAI")
    @patch("_ai_processing.instructor")
    def test_ollama_uses_json_mode(self, mock_instructor, mock_openai, sample_config):
        """Ollama must use JSON mode for broadest model compatibility."""
        mock_instructor.from_openai.return_value = MagicMock()
        mock_instructor.Mode.JSON = "JSON"
        sample_config["ai"]["provider"] = "ollama"
        sample_config["ai"]["api_key"] = ""
        get_ai_client(sample_config)
        mock_instructor.from_openai.assert_called_once_with(mock_openai.return_value, mode="JSON")

    @patch("_ai_processing.OpenAI")
    @patch("_ai_processing.instructor")
    def test_gemini_client_uses_base_url(self, mock_instructor, mock_openai, sample_config):
        mock_instructor.from_openai.return_value = MagicMock()
        sample_config["ai"]["provider"] = "gemini"
        get_ai_client(sample_config)
        call_args = mock_openai.call_args
        assert "generativelanguage.googleapis.com" in call_args.kwargs["base_url"]


class TestExtractMetadataProviderKwargs:
    """Test that provider-specific kwargs are applied correctly."""

    @patch("_ai_processing.get_ai_client")
    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_anthropic_adds_max_tokens(self, mock_prompt, mock_client, sample_config):
        """Anthropic provider includes max_tokens in API call."""
        sample_config["ai"]["provider"] = "anthropic"
        mock_completions = MagicMock()
        mock_completions.create.return_value = DocumentMetadata(
            company_name="Test", document_date="01.01.2024", document_type="ER"
        )
        mock_client.return_value = MagicMock(chat=MagicMock(completions=mock_completions))

        from _ai_processing import extract_metadata_from_text
        extract_metadata_from_text("test text", sample_config)

        call_kwargs = mock_completions.create.call_args[1]
        assert call_kwargs.get("max_tokens") == 1024

    @patch("_ai_processing.get_ai_client")
    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_openai_uses_responses_structured_output(
        self, mock_prompt, mock_client, sample_config
    ):
        """OpenAI uses Responses parsing without Chat Completions parameters."""
        response = MagicMock()
        response.output_parsed = DocumentMetadata(
            company_name="Test", document_date="01.01.2024", document_type="ER"
        )
        response.output = []
        mock_client.return_value.responses.parse.return_value = response

        from _ai_processing import extract_metadata_from_text
        extract_metadata_from_text("test text", sample_config)

        call_kwargs = mock_client.return_value.responses.parse.call_args.kwargs
        assert call_kwargs["model"] == sample_config["ai"]["model"]
        assert call_kwargs["text_format"] is DocumentMetadata
        assert call_kwargs["reasoning"] == {"effort": "low"}
        assert call_kwargs["store"] is False
        assert "temperature" not in call_kwargs
        assert "messages" not in call_kwargs

    @patch("_ai_processing.get_ai_client")
    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_vision_extraction_kwargs(self, mock_prompt, mock_client, sample_config):
        """Vision extraction sends image_url content blocks."""
        from PIL import Image
        response = MagicMock()
        response.output_parsed = DocumentMetadata(
            company_name="Test", document_date="01.01.2024", document_type="ER"
        )
        response.output = []
        mock_client.return_value.responses.parse.return_value = response

        from _ai_processing import extract_metadata_from_images
        images = [Image.new("RGB", (100, 100))]
        extract_metadata_from_images(images, sample_config)

        call_kwargs = mock_client.return_value.responses.parse.call_args.kwargs
        user_msg = call_kwargs["input"][0]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        image_blocks = [
            c for c in user_msg["content"] if c.get("type") == "input_image"
        ]
        assert len(image_blocks) == 1
        assert image_blocks[0]["detail"] == "high"

    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_openai_refusal_is_reported(self, mock_prompt, sample_config):
        """Responses refusals become an actionable extraction error."""
        from _ai_processing import _extract_openai_metadata

        refusal = SimpleNamespace(type="refusal", refusal="Cannot process document")
        message = SimpleNamespace(type="message", content=[refusal])
        response = SimpleNamespace(output_parsed=None, output=[message])
        client = MagicMock()
        client.responses.parse.return_value = response

        with pytest.raises(ValueError, match="OpenAI refused.*Cannot process document"):
            _extract_openai_metadata(client, sample_config, "test input")

    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_openai_missing_parsed_output_is_reported(self, mock_prompt, sample_config):
        """A completed response without parsed metadata fails clearly."""
        from _ai_processing import _extract_openai_metadata

        response = SimpleNamespace(output_parsed=None, output=[])
        client = MagicMock()
        client.responses.parse.return_value = response

        with pytest.raises(ValueError, match="did not contain parsed"):
            _extract_openai_metadata(client, sample_config, "test input")

    @patch("_ai_processing.build_system_prompt", return_value="test prompt")
    def test_older_openai_model_uses_temperature(self, mock_prompt, sample_config):
        """Non-reasoning OpenAI models retain deterministic sampling."""
        from _ai_processing import _extract_openai_metadata

        sample_config["ai"]["model"] = "gpt-4o-mini"
        response = SimpleNamespace(
            output_parsed=DocumentMetadata(
                company_name="Test",
                document_date="01.01.2024",
                document_type="ER",
            ),
            output=[],
        )
        client = MagicMock()
        client.responses.parse.return_value = response

        _extract_openai_metadata(client, sample_config, "test input")

        call_kwargs = client.responses.parse.call_args.kwargs
        assert call_kwargs["temperature"] == 0.0
        assert "reasoning" not in call_kwargs


class TestBuildCombinedText:
    def test_text_only(self):
        extraction = ExtractionResult(text="Hello", ocr_text="", sources=["text"])
        assert _build_combined_text(extraction) == "Hello"

    def test_ocr_only(self):
        extraction = ExtractionResult(text="", ocr_text="OCR text", sources=["text", "ocr"])
        assert "OCR text" in _build_combined_text(extraction)

    def test_text_and_ocr(self):
        extraction = ExtractionResult(text="Text", ocr_text="OCR", sources=["text", "ocr"])
        combined = _build_combined_text(extraction)
        assert "Text" in combined
        assert "OCR" in combined
        assert "--- OCR Text ---" in combined

    def test_both_empty(self):
        extraction = ExtractionResult(text="", ocr_text="", sources=["text"])
        assert _build_combined_text(extraction) == ""


class TestExtractMetadata:
    def test_no_content_returns_none(self, sample_config):
        extraction = ExtractionResult(text="", images=[], quality_score=0.0, page_count=0, sources=["text"])
        result = extract_metadata(extraction, sample_config)
        assert result is None

    @patch("_ai_processing.extract_metadata_from_text")
    def test_text_extraction(self, mock_extract, sample_config):
        mock_extract.return_value = DocumentMetadata(
            company_name="ACME", document_date="15.03.2024", document_type="ER"
        )
        extraction = ExtractionResult(
            text="Invoice from ACME", images=[], quality_score=0.8,
            page_count=1, sources=["text"]
        )
        result = extract_metadata(extraction, sample_config)
        assert result.company_name == "ACME"
        mock_extract.assert_called_once()

    @patch("_ai_processing.extract_metadata_from_images")
    def test_vision_extraction(self, mock_extract, sample_config):
        mock_extract.return_value = DocumentMetadata(
            company_name="Globex", document_date="01.01.2024", document_type="AR"
        )
        img = Image.new("RGB", (100, 100))
        extraction = ExtractionResult(
            text="", images=[img], quality_score=0.0,
            page_count=1, sources=["text", "vision"]
        )
        result = extract_metadata(extraction, sample_config)
        assert result.company_name == "Globex"
        mock_extract.assert_called_once()

    @patch("_ai_processing.extract_metadata_from_text_and_images")
    def test_mixed_text_and_images(self, mock_extract, sample_config):
        mock_extract.return_value = DocumentMetadata(
            company_name="Mixed", document_date="01.01.2024", document_type="ER"
        )
        img = Image.new("RGB", (100, 100))
        extraction = ExtractionResult(
            text="Some text", images=[img], quality_score=0.5,
            page_count=1, sources=["text", "vision"]
        )
        result = extract_metadata(extraction, sample_config)
        assert result.company_name == "Mixed"
        mock_extract.assert_called_once()
