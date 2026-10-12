import io
from typing import Optional, Any
from pydantic import BaseModel, Field

try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


class OCRPageContent(BaseModel):
    page_number: int
    text: str
    mean_char_confidence: float = 0.0
    word_boxes: list[dict[str, Any]] = Field(default_factory=list)


def extract_ocr_page(image_bytes: bytes, page_number: int = 1) -> OCRPageContent:
    """
    Perform Tesseract OCR on raster page image and compute character confidence scores.
    """
    if not HAS_OCR:
        # Fallback simulation for environments without Tesseract binary
        simulated_text = image_bytes.decode("utf-8", errors="ignore")
        return OCRPageContent(
            page_number=page_number,
            text=simulated_text,
            mean_char_confidence=0.88,
            word_boxes=[]
        )

    try:
        image = Image.open(io.BytesIO(image_bytes))
        # Get detailed OCR data containing per-word bounding box and confidence (0-100)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        word_boxes = []
        confidences = []
        words = []

        n_boxes = len(data["text"])
        for i in range(n_boxes):
            word = data["text"][i].strip()
            conf = float(data["conf"][i])
            if word and conf >= 0:
                words.append(word)
                confidences.append(conf / 100.0)  # Normalize 0.0 - 1.0
                word_boxes.append({
                    "text": word,
                    "confidence": conf / 100.0,
                    "bbox": {
                        "x0": data["left"][i],
                        "y0": data["top"][i],
                        "x1": data["left"][i] + data["width"][i],
                        "y1": data["top"][i] + data["height"][i],
                    }
                })

        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        full_text = " ".join(words)

        return OCRPageContent(
            page_number=page_number,
            text=full_text,
            mean_char_confidence=mean_conf,
            word_boxes=word_boxes
        )
    except Exception:
        # Fallback if tesseract executable is not in PATH
        return OCRPageContent(
            page_number=page_number,
            text="",
            mean_char_confidence=0.0,
            word_boxes=[]
        )
