"""Apple Vision helpers for local image OCR and classification.

All imports are optional so non-macOS and environments without PyObjC keep
working. Callers receive warnings instead of hard failures.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field


@dataclass
class AppleVisionResult:
    text: str = ""
    classifications: list[tuple[str, float]] = field(default_factory=list)
    coreml_observations: list[tuple[str, float | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _load_frameworks():
    try:
        import Foundation  # type: ignore
        import Vision  # type: ignore
    except Exception as e:
        return None, None, None, f"Apple Vision unavailable: {e}"

    try:
        import CoreML  # type: ignore
    except Exception:
        CoreML = None

    return Foundation, Vision, CoreML, None


def _perform_requests(handler, requests) -> tuple[bool, str | None]:
    try:
        ok, err = handler.performRequests_error_(requests, None)
    except Exception as e:
        return False, str(e)
    if not ok:
        return False, str(err)
    return True, None


def _top_text_from_observation(observation) -> tuple[str, float | None]:
    candidates = observation.topCandidates_(1)
    if not candidates or len(candidates) == 0:
        return "", None
    candidate = candidates[0]
    text = candidate.string()
    confidence = getattr(candidate, "confidence", lambda: None)()
    return text, confidence


def _extract_classification(observation) -> tuple[str, float | None] | None:
    identifier = getattr(observation, "identifier", lambda: "")()
    if not identifier:
        return None
    confidence = getattr(observation, "confidence", lambda: None)()
    return str(identifier), float(confidence) if confidence is not None else None


def _extract_coreml_observation(observation) -> list[tuple[str, float | None]]:
    """Extract labels from classification or object-detection observations."""
    classification = _extract_classification(observation)
    if classification:
        return [classification]

    labels_method = getattr(observation, "labels", None)
    if not callable(labels_method):
        return []

    labels = []
    for label in labels_method() or []:
        extracted = _extract_classification(label)
        if extracted:
            labels.append(extracted)
    return labels


def analyze_image_with_apple_vision(image_path: str, config: dict) -> AppleVisionResult:
    """Run Apple Vision OCR, built-in classification, and optional Core ML.

    Config:
      image.apple_vision: false/true
      image.apple_vision_recognition_level: "accurate" | "fast"
      image.apple_vision_languages: ["en-US"]
      image.apple_vision_classify: true/false
      image.coreml_model_path: "/path/to/model.mlmodelc"
      image.coreml_max_observations: 10
    """
    result = AppleVisionResult()
    if sys.platform != "darwin":
        result.warnings.append("Apple Vision is only available on Apple platforms")
        return result

    Foundation, Vision, CoreML, import_error = _load_frameworks()
    if import_error:
        result.warnings.append(import_error)
        return result

    image_cfg = config.get("image", {})
    url = Foundation.NSURL.fileURLWithPath_(os.fspath(image_path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})

    requests = []
    text_request = Vision.VNRecognizeTextRequest.alloc().init()
    level = image_cfg.get("apple_vision_recognition_level", "accurate")
    if level == "fast":
        text_request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
    else:
        text_request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    text_request.setUsesLanguageCorrection_(bool(image_cfg.get("apple_vision_language_correction", True)))
    languages = image_cfg.get("apple_vision_languages") or ["en-US"]
    text_request.setRecognitionLanguages_(languages)
    requests.append(text_request)

    classify_request = None
    if image_cfg.get("apple_vision_classify", True):
        try:
            classify_request = Vision.VNClassifyImageRequest.alloc().init()
            requests.append(classify_request)
        except Exception as e:
            result.warnings.append(f"Apple Vision classification unavailable: {e}")

    coreml_request = None
    model_path = image_cfg.get("coreml_model_path", "")
    if model_path:
        if CoreML is None:
            result.warnings.append("Core ML framework unavailable")
        elif not os.path.exists(os.path.expanduser(model_path)):
            result.warnings.append(f"Core ML model not found: {model_path}")
        else:
            try:
                model_url = Foundation.NSURL.fileURLWithPath_(os.path.expanduser(model_path))
                ml_model, err = CoreML.MLModel.modelWithContentsOfURL_error_(model_url, None)
                if err is not None or ml_model is None:
                    result.warnings.append(f"Could not load Core ML model: {err}")
                else:
                    vn_model, err = Vision.VNCoreMLModel.modelForMLModel_error_(ml_model, None)
                    if err is not None or vn_model is None:
                        result.warnings.append(f"Could not create VNCoreMLModel: {err}")
                    else:
                        coreml_request = Vision.VNCoreMLRequest.alloc().initWithModel_(vn_model)
                        requests.append(coreml_request)
            except Exception as e:
                result.warnings.append(f"Core ML setup failed: {e}")

    ok, err = _perform_requests(handler, requests)
    if not ok:
        result.warnings.append(f"Apple Vision analysis failed: {err}")
        return result

    text_lines = []
    for observation in text_request.results() or []:
        text, confidence = _top_text_from_observation(observation)
        if text:
            text_lines.append(text)
    result.text = "\n".join(text_lines)

    if classify_request is not None:
        max_items = int(image_cfg.get("apple_vision_max_classifications", 8))
        min_confidence = float(image_cfg.get("apple_vision_min_confidence", 0.15))
        for observation in (classify_request.results() or [])[:max_items]:
            extracted = _extract_classification(observation)
            if extracted and extracted[1] is not None and extracted[1] >= min_confidence:
                result.classifications.append((extracted[0], extracted[1]))

    if coreml_request is not None:
        max_items = int(image_cfg.get("coreml_max_observations", 10))
        for observation in coreml_request.results() or []:
            extracted = _extract_coreml_observation(observation)
            if extracted:
                result.coreml_observations.extend(extracted)
            elif len(result.coreml_observations) < max_items:
                result.coreml_observations.append((observation.description(), None))
            if len(result.coreml_observations) >= max_items:
                result.coreml_observations = result.coreml_observations[:max_items]
                break

    logging.info(
        "Apple Vision extracted %d text chars, %d classifications, %d Core ML observations",
        len(result.text), len(result.classifications), len(result.coreml_observations),
    )
    return result
