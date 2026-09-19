"""
PDF processing utilities for text extraction, image rendering, and OCR.
Uses pdfplumber for text, pypdfium2 for page images, PaddleOCR via subprocess.
"""
from __future__ import annotations

import os
import json
import logging
import subprocess
import sys
import tempfile
import shutil
import threading
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


@dataclass
class ExtractionResult:
    """Result of PDF content extraction."""
    text: str = ""                                  # pdfplumber text (always)
    ocr_text: str = ""                              # PaddleOCR text (if run)
    images: list = field(default_factory=list)      # page images (if vision)
    quality_score: float = 0.0
    page_count: int = 0
    sources: list = field(default_factory=list)     # e.g. ["text"], ["text","ocr"], ["text","vision"]
    warnings: list = field(default_factory=list)    # non-fatal issues (OCR failures, etc.)


_MOJIBAKE_MARKERS = (
    "\u00c3",
    "\u00c2",
    "\u00e2\u201a",
    "\u00e2\u20ac\u0153",
    "\u00e2\u20ac",
    "\u00e2\u20ac\u201c",
    "\u00e2\u20ac\u201d",
)


def _open_pdf_stream(pdf_path: str):
    """Open a PDF as a binary stream for libraries that mishandle some paths."""
    return open(os.fspath(pdf_path), "rb")


def _mojibake_marker_count(text: str) -> int:
    return sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)


def _maybe_fix_mojibake(text: str) -> str:
    """Repair common UTF-8/legacy-codepage mojibake when it is clearly better."""
    if not text or _mojibake_marker_count(text) == 0:
        return text

    original_markers = _mojibake_marker_count(text)
    for legacy_encoding in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(legacy_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired and _mojibake_marker_count(repaired) < original_markers:
            logging.info("Applied mojibake repair heuristic to extracted PDF text")
            return repaired
    return text


def _extract_text_with_pdfplumber(pdf_path: str, max_pages: int, repair: bool = False) -> str:
    """Extract text while isolating per-page parser failures."""
    all_text = []
    with _open_pdf_stream(pdf_path) as stream:
        with pdfplumber.open(
            stream,
            unicode_norm="NFC",
            raise_unicode_errors=False,
            repair=repair,
        ) as pdf:
            pages_to_read = min(max_pages, len(pdf.pages))
            for i in range(pages_to_read):
                try:
                    page = pdf.pages[i]
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        all_text.append(f"Page {i + 1}:\n{page_text}")
                except Exception as e:
                    logging.warning(f"Error extracting text from page {i + 1} of {pdf_path}: {e}")
    return "\n\n".join(all_text)


def extract_text(pdf_path: str, max_pages: int = 3) -> tuple:
    """Extract text using pdfplumber. Returns (text, quality_score)."""
    try:
        text = _extract_text_with_pdfplumber(pdf_path, max_pages, repair=False)
    except Exception as e:
        logging.warning(f"Primary text extraction failed for {pdf_path}: {e}")
        try:
            text = _extract_text_with_pdfplumber(pdf_path, max_pages, repair=True)
            logging.info(f"Recovered text extraction via pdfplumber repair mode: {pdf_path}")
        except Exception as repair_error:
            logging.error(f"Error extracting text from {pdf_path}: {repair_error}")
            return "", 0.0

    text = _maybe_fix_mojibake(text)
    quality = assess_text_quality(text)
    logging.info(f"Extracted text quality: {quality:.2f}, length: {len(text)} chars")
    logging.debug(f"Full extracted text ({len(text)} chars):\n{text}")
    return text, quality


def assess_text_quality(text: str) -> float:
    """Score 0.0-1.0 based on text characteristics.

    Heuristics: character count, alphanumeric ratio, average word length.
    """
    if not text or not text.strip():
        return 0.0

    # Character count score (0-0.4)
    char_count = len(text.strip())
    char_score = min(char_count / 500, 1.0) * 0.4

    # Alphanumeric ratio (0-0.3)
    alnum_count = sum(1 for c in text if c.isalnum())
    total_non_space = sum(1 for c in text if not c.isspace())
    alnum_ratio = alnum_count / total_non_space if total_non_space > 0 else 0
    alnum_score = alnum_ratio * 0.3

    # Average word length (0-0.3) — very short or very long words indicate garbage
    words = text.split()
    if words:
        avg_word_len = sum(len(w) for w in words) / len(words)
        # Ideal average word length is 3-8 characters
        if 3 <= avg_word_len <= 8:
            word_score = 0.3
        elif 2 <= avg_word_len <= 12:
            word_score = 0.15
        else:
            word_score = 0.05
    else:
        word_score = 0.0

    return min(char_score + alnum_score + word_score, 1.0)


def render_pages_to_images(pdf_path: str, max_pages: int = 3, scale: float = 2.0) -> list[Image.Image]:
    """Render PDF pages to PIL images using pypdfium2 v5."""
    images = []
    pdf = None
    stream = None
    try:
        stream = _open_pdf_stream(pdf_path)
        pdf = pdfium.PdfDocument(stream, autoclose=False)
        pages_to_render = min(max_pages, len(pdf))
        if pages_to_render == 0:
            logging.warning(f"PDF has 0 pages: {pdf_path}")
            return images

        for i in range(pages_to_render):
            page = None
            bitmap = None
            try:
                page = pdf[i]
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
                images.append(pil_image)
            except Exception as page_error:
                logging.warning(f"Error rendering page {i + 1} from {pdf_path}: {page_error}")
            finally:
                if bitmap is not None and hasattr(bitmap, "close"):
                    bitmap.close()
                if page is not None:
                    page.close()
    except Exception as e:
        logging.error(f"Error rendering pages from {pdf_path}: {e}")
    finally:
        if pdf is not None:
            pdf.close()
        if stream is not None:
            stream.close()
    return images


def _get_bridge_script_path() -> str:
    """Get path to the PaddleOCR bridge script, handling PyInstaller."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "_paddleocr_bridge.py")


def _get_paddleocr_python(config: dict) -> str | None:
    """Get path to python executable inside PaddleOCR venv."""
    venv = config.get("paddleocr", {}).get("venv_path", "")
    if not venv:
        # Platform-specific default venv location
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", "")
        else:
            base = os.path.join(os.path.expanduser("~"), ".local", "share")
        if base:
            venv = os.path.join(base, "autorename-pdf", "paddleocr-venv")

    if not venv:
        return None

    # Platform-specific venv layout: Scripts/python.exe (Win) vs bin/python (Unix)
    if sys.platform == "win32":
        python = os.path.join(venv, "Scripts", "python.exe")
    else:
        python = os.path.join(venv, "bin", "python")
    return python if os.path.isfile(python) else None


def _paddleocr_available(config: dict) -> bool:
    """Check if PaddleOCR venv exists and is functional."""
    return _get_paddleocr_python(config) is not None


def is_searchable_pdf(pdf_path: str, threshold: float = 0.3, check_pages: int = 3) -> tuple[bool, float, str]:
    """Check whether a PDF has an adequate embedded text layer.

    Args:
        pdf_path: Path to the PDF file.
        threshold: Minimum text quality score (0.0 - 1.0) to be considered searchable.
        check_pages: Number of pages to inspect.

    Returns:
        tuple of (is_searchable: bool, quality_score: float, extracted_text: str)
    """
    text, quality = extract_text(pdf_path, max_pages=check_pages)
    return quality >= threshold, quality, text


def ocr_pages_detailed(images: list[Image.Image], config: dict) -> list[dict]:
    """Save images as temp files, pipe paths to bridge script, collect structured OCR results."""
    python = _get_paddleocr_python(config)
    if not python:
        logging.error("PaddleOCR python not found")
        return []

    bridge_src = _get_bridge_script_path()
    paddleocr_cfg = config.get("paddleocr", {})
    lang = paddleocr_cfg.get("language", "en")
    device = paddleocr_cfg.get("device", "auto")
    det_model = paddleocr_cfg.get("detection_model", "")
    det_limit = paddleocr_cfg.get("det_limit_side_len", 736)
    cpu_threads = paddleocr_cfg.get("cpu_threads", 4)

    tmp_dir = tempfile.mkdtemp(prefix="autorename_ocr_")
    results = []
    try:
        # PyInstaller isolation: when frozen, the bridge script lives in _MEIPASS.
        meipass = getattr(sys, '_MEIPASS', None)
        env = os.environ.copy()
        if meipass:
            # Copy bridge script out of _MEIPASS to avoid polluting child sys.path
            bridge = os.path.join(tmp_dir, os.path.basename(bridge_src))
            shutil.copy2(bridge_src, bridge)

            # Reset DLL search order (defense-in-depth)
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)

            # Clean PATH and PyInstaller env vars
            meipass_norm = os.path.normpath(meipass)
            path_dirs = env.get("PATH", "").split(os.pathsep)
            path_dirs = [d for d in path_dirs if os.path.normpath(d) != meipass_norm]
            env["PATH"] = os.pathsep.join(path_dirs)
            for var in ("_MEIPASS2", "PYTHONPATH", "PYTHONHOME"):
                env.pop(var, None)
        else:
            bridge = bridge_src

        cmd = [python, bridge, lang, "--device", device,
               "--det-limit", str(det_limit),
               "--cpu-threads", str(cpu_threads)]
        if det_model:
            cmd.extend(["--det-model", det_model])

        logging.info(f"Starting PaddleOCR (lang={lang}, device={device})")

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            env=env,
        )

        def _drain_stderr():
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                low = line.lower()
                if ("downloading" in low or "fetching" in low
                        or "download complete" in low):
                    logging.warning(f"PaddleOCR: {line}")
                else:
                    logging.info(f"PaddleOCR: {line}")
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        for i, img in enumerate(images):
            tmp_path = os.path.join(tmp_dir, f"page_{i}.png")
            img.save(tmp_path)
            try:
                proc.stdin.write(tmp_path + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    logging.warning(f"PaddleOCR bridge returned empty line for page {i + 1}")
                    results.append({"status": "error", "message": "Empty response from bridge", "text": "", "lines": []})
                    continue
                result = json.loads(line)
                if result.get("status") == "ok":
                    results.append({
                        "status": "ok",
                        "text": result.get("text", ""),
                        "lines": result.get("lines", []),
                    })
                else:
                    logging.warning(f"PaddleOCR error on page {i + 1}: {result.get('message', 'unknown')}")
                    results.append({
                        "status": "error",
                        "message": result.get("message", "unknown"),
                        "text": "",
                        "lines": [],
                    })
            except (json.JSONDecodeError, BrokenPipeError, OSError) as e:
                logging.warning(f"PaddleOCR bridge communication error on page {i + 1}: {e}")
                results.append({"status": "error", "message": str(e), "text": "", "lines": []})
                break  # Bridge is dead, no point sending more pages

        try:
            proc.stdin.close()
            proc.wait(timeout=30)
        except Exception as e:
            logging.warning(f"PaddleOCR process cleanup: {e}")
            proc.kill()
            proc.wait(timeout=5)

        stderr_thread.join(timeout=5)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


def ocr_with_paddleocr(images: list[Image.Image], config: dict) -> str:
    """Save images as temp files, pipe paths to bridge script, collect OCR text."""
    results = ocr_pages_detailed(images, config)
    all_text = [
        f"Page {i + 1}:\n{r['text']}"
        for i, r in enumerate(results)
        if r.get("status") == "ok" and r.get("text")
    ]
    return "\n\n".join(all_text)


def _load_unicode_font(pdf) -> str:
    """Find and load an available Unicode TTF font in FPDF, returning the family name."""
    candidates = [
        ("C:/Windows/Fonts/malgun.ttf", "Malgun"),
        ("C:/Windows/Fonts/arial.ttf", "Arial"),
        ("C:/Windows/Fonts/segoeui.ttf", "SegoeUI"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "Arial"),
    ]
    for path, family in candidates:
        if os.path.isfile(path):
            try:
                pdf.add_font(family, "", path)
                return family
            except Exception as e:
                logging.debug(f"Failed to load font candidate {path}: {e}")
                continue
    return "Helvetica"


def ocr_pdf_to_searchable(
    pdf_path: str,
    output_path: str,
    config: dict,
    max_pages: int = 0,
    scale: float = 2.0,
) -> dict:
    """Perform OCR on a PDF and save a new searchable PDF with invisible text overlay.

    Args:
        pdf_path: Path to source PDF.
        output_path: Path to destination PDF (can be same as pdf_path for atomic replacement).
        config: Configuration dictionary (paddleocr settings, etc.).
        max_pages: Maximum pages to process (0 = all pages).
        scale: Rendering scale for OCR and page background image (default 2.0).

    Returns:
        dict with keys:
            "success": bool,
            "pages_processed": int,
            "total_pages": int,
            "output_path": str,
            "text_quality": float,
            "error": str or None,
    """
    if not _paddleocr_available(config):
        return {
            "success": False,
            "pages_processed": 0,
            "total_pages": 0,
            "output_path": output_path,
            "text_quality": 0.0,
            "error": "PaddleOCR is not installed or venv not found",
        }

    from fpdf import FPDF, TextMode

    pdf_doc = None
    stream = None
    try:
        stream = _open_pdf_stream(pdf_path)
        pdf_doc = pdfium.PdfDocument(stream, autoclose=False)
        total_pages = len(pdf_doc)
        if total_pages == 0:
            return {
                "success": False,
                "pages_processed": 0,
                "total_pages": 0,
                "output_path": output_path,
                "text_quality": 0.0,
                "error": "PDF has 0 pages",
            }

        pages_to_process = min(max_pages, total_pages) if max_pages > 0 else total_pages

        page_images = []
        page_sizes_pt = []
        for i in range(pages_to_process):
            page = pdf_doc[i]
            w_pt, h_pt = page.get_size()
            page_sizes_pt.append((w_pt, h_pt))
            pil_img = page.render(scale=scale).to_pil()
            page_images.append(pil_img)
    finally:
        if pdf_doc:
            pdf_doc.close()
        if stream:
            stream.close()

    ocr_results = ocr_pages_detailed(page_images, config)

    tmp_dir = tempfile.mkdtemp(prefix="autorename_searchable_")
    try:
        pdf_out = FPDF(unit="pt")
        font_family = _load_unicode_font(pdf_out)

        for i, (pil_img, (w_pt, h_pt)) in enumerate(zip(page_images, page_sizes_pt)):
            pdf_out.add_page(format=(w_pt, h_pt))

            img_tmp_path = os.path.join(tmp_dir, f"page_bg_{i}.jpg")
            pil_img.save(img_tmp_path, format="JPEG", quality=90)
            pdf_out.image(img_tmp_path, x=0, y=0, w=w_pt, h=h_pt)

            pdf_out.text_mode = TextMode.INVISIBLE
            page_data = ocr_results[i] if i < len(ocr_results) else {}
            lines = page_data.get("lines", [])

            for line in lines:
                text = line.get("text", "")
                box = line.get("box")
                if not text or not box:
                    continue

                if isinstance(box[0], (int, float)) and len(box) == 4:
                    xmin, ymin, xmax, ymax = box
                elif len(box) >= 4 and isinstance(box[0], (list, tuple)):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    xmin, ymin, xmax, ymax = min(xs), min(ys), max(xs), max(ys)
                else:
                    continue

                x_pt = xmin / scale
                y_pt = ymin / scale
                w_box_pt = max((xmax - xmin) / scale, 1.0)
                h_box_pt = max((ymax - ymin) / scale, 1.0)

                font_size = max(4.0, min(h_box_pt * 0.85, 72.0))
                pdf_out.set_font(font_family, size=font_size)
                pdf_out.set_xy(x_pt, y_pt)

                if font_family == "Helvetica":
                    clean_text = text.encode("latin-1", "replace").decode("latin-1")
                else:
                    clean_text = text
                pdf_out.cell(w=w_box_pt, h=h_box_pt, text=clean_text, border=0)

        is_in_place = os.path.abspath(output_path) == os.path.abspath(pdf_path)
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if is_in_place:
            temp_out = os.path.join(tmp_dir, "output.pdf")
            pdf_out.output(temp_out)
            os.replace(temp_out, output_path)
        else:
            pdf_out.output(output_path)

        _, new_quality = extract_text(output_path, max_pages=min(pages_to_process, 3))

        return {
            "success": True,
            "pages_processed": pages_to_process,
            "total_pages": total_pages,
            "output_path": output_path,
            "text_quality": new_quality,
            "error": None,
        }
    except Exception as e:
        logging.error(f"Failed to generate searchable PDF for {pdf_path}: {e}")
        return {
            "success": False,
            "pages_processed": 0,
            "total_pages": total_pages if "total_pages" in locals() else 0,
            "output_path": output_path,
            "text_quality": 0.0,
            "error": str(e),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _should_run_step(setting, quality: float, threshold: float) -> bool:
    """Determine if an optional extraction step (OCR/vision) should run.

    setting: False (disabled), True (always), or "auto" (run when quality < threshold).
    """
    if setting is True or setting == "true":
        return True
    if setting is False or setting == "false" or not setting:
        return False
    # "auto" — run only when text quality is below threshold
    return quality < threshold


def extract_content(pdf_path: str, config: dict) -> ExtractionResult:
    """Main extraction entry point. Text always runs; OCR and vision are independent add-ons."""
    pdf_cfg = config.get("pdf", {})
    max_pages = pdf_cfg.get("max_pages", 3)
    threshold = pdf_cfg.get("text_quality_threshold", 0.3)
    ocr_setting = pdf_cfg.get("ocr", False)
    vision_setting = pdf_cfg.get("vision", False)

    sources = []
    warnings = []

    # Step 1: Always run pdfplumber text extraction
    text, quality = extract_text(pdf_path, max_pages)
    sources.append("text")

    # Step 2: Determine if OCR / vision should run
    run_ocr = _should_run_step(ocr_setting, quality, threshold)
    run_vision = _should_run_step(vision_setting, quality, threshold)

    ocr_text = ""
    images = []

    # Step 3: Render images if needed for OCR or vision
    # Use lower scale for OCR-only (detection model resizes internally anyway)
    if run_ocr or run_vision:
        ocr_scale = 1.5 if (run_ocr and not run_vision) else 2.0
        images = render_pages_to_images(pdf_path, max_pages, scale=ocr_scale)
        if not images:
            logging.warning(f"No images rendered from {pdf_path}")
            warnings.append("Could not render page images")
            run_ocr = False
            run_vision = False

    # Step 4: PaddleOCR
    if run_ocr:
        if _paddleocr_available(config):
            try:
                ocr_text = ocr_with_paddleocr(images, config)
            except Exception as e:
                logging.warning(f"PaddleOCR failed, continuing without OCR: {e}")
                warnings.append(f"PaddleOCR failed: {e}")
                ocr_text = ""
            if ocr_text.strip():
                sources.append("ocr")
            else:
                if not warnings:  # Don't duplicate if we already logged a failure
                    logging.warning("PaddleOCR returned empty text")
                    warnings.append("PaddleOCR returned no text")
        else:
            logging.warning("PaddleOCR requested but not available")
            warnings.append("PaddleOCR not installed — run setup.ps1 to install")

    # Step 5: Vision — keep images in result
    if run_vision:
        sources.append("vision")
    else:
        images = []  # Don't pass images if vision not requested

    return ExtractionResult(
        text=text,
        ocr_text=ocr_text,
        images=images,
        quality_score=quality,
        page_count=max_pages,
        sources=sources,
        warnings=warnings,
    )
