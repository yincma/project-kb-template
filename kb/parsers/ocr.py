from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float | None = None


def ocr_dependency_status(engine: str = "rapidocr") -> dict[str, Any]:
    preferred = (engine or "rapidocr").lower()
    modules = ["rapidocr", "rapidocr_onnxruntime"] if preferred == "rapidocr" else [preferred, "rapidocr"]
    errors: list[str] = []
    for module_name in modules:
        try:
            __import__(module_name)
            return {"available": True, "engine": module_name, "error": None}
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    return {"available": False, "engine": preferred, "error": "; ".join(errors)}


class OCRProcessor:
    def __init__(self, engine: str = "rapidocr") -> None:
        self.engine = engine or "rapidocr"
        self._runner = None
        self._engine_module = None

    def ocr_image(self, image: Any) -> OCRResult:
        runner = self._load_runner()
        normalized = _normalize_image_input(image)
        raw_result = runner(normalized)
        return _parse_ocr_result(raw_result)

    def _load_runner(self):
        if self._runner is not None:
            return self._runner

        errors: list[str] = []
        for module_name in _candidate_modules(self.engine):
            try:
                module = __import__(module_name, fromlist=["RapidOCR"])
                self._runner = module.RapidOCR()
                self._engine_module = module_name
                return self._runner
            except Exception as exc:
                errors.append(f"{module_name}: {exc}")
        raise RuntimeError("No local OCR engine is available. " + "; ".join(errors))


def _candidate_modules(engine: str) -> list[str]:
    normalized = (engine or "rapidocr").lower()
    if normalized in {"rapidocr", "rapidocr_onnxruntime"}:
        return ["rapidocr", "rapidocr_onnxruntime"]
    return [normalized, "rapidocr", "rapidocr_onnxruntime"]


def _normalize_image_input(image: Any) -> Any:
    if isinstance(image, (str, bytes, bytearray)):
        if isinstance(image, str):
            return image
        try:
            import numpy as np
            from PIL import Image

            pil_image = Image.open(BytesIO(bytes(image))).convert("RGB")
            return np.array(pil_image)
        except Exception:
            return bytes(image)
    return image


def _parse_ocr_result(raw_result: Any) -> OCRResult:
    if isinstance(raw_result, tuple) and raw_result:
        raw_result = raw_result[0]

    if hasattr(raw_result, "txts"):
        texts = [str(text) for text in (getattr(raw_result, "txts") or []) if str(text).strip()]
        scores = _score_list(getattr(raw_result, "scores", None))
        return OCRResult(text="\n".join(texts), confidence=_average(scores))

    texts: list[str] = []
    scores: list[float] = []
    if isinstance(raw_result, list):
        for item in raw_result:
            text, score = _parse_ocr_item(item)
            if text:
                texts.append(text)
            if score is not None:
                scores.append(score)
    elif raw_result:
        texts.append(str(raw_result))

    return OCRResult(text="\n".join(texts), confidence=_average(scores))


def _parse_ocr_item(item: Any) -> tuple[str | None, float | None]:
    if isinstance(item, dict):
        text = item.get("text") or item.get("rec_text") or item.get("label")
        return (str(text).strip() if text else None, _to_float(item.get("score") or item.get("confidence")))

    if isinstance(item, (tuple, list)):
        if len(item) >= 3:
            return (str(item[1]).strip(), _to_float(item[2]))
        if len(item) >= 2:
            return (str(item[0]).strip(), _to_float(item[1]))
        if len(item) == 1:
            return (str(item[0]).strip(), None)

    text = str(item).strip() if item is not None else None
    return (text, None)


def _score_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [score for item in value if (score := _to_float(item)) is not None]
    score = _to_float(value)
    return [score] if score is not None else []


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
