import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from google.genai import Client
from pypdf import PdfReader

from config import get_config
from logging_config import get_logger
from task_queue import TaskQueue

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except Exception:
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import fitz
except Exception:
    fitz = None


cfg = get_config()
logger = get_logger("invoice_pipeline")

_MAX_ZIP_BYTES = 200 * 1024 * 1024
_MAX_FILES = 500
_GOLDEN_EXPECTED_COUNT = 43

_api_key = cfg.gemini_api_key or cfg.google_api_key
_genai_client = Client(api_key=_api_key) if _api_key else None


@dataclass
class SchemaField:
    name: str
    type: str
    required: bool


@dataclass
class ExtractedFields:
    fields: dict[str, Any]
    confidence_score: float
    schema_valid: bool
    violations: list[str]
    _extractor_model_used: str
    ocr_text_present: bool
    warning_codes: list[str] = field(default_factory=list)
    date_candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def delivery_date(self) -> str:
        return str(self.fields.get("delivery_date", "") or "")

    @property
    def receiver_name(self) -> str:
        return str(self.fields.get("receiver_name", "") or "")

    @property
    def receiver_location(self) -> str:
        return str(self.fields.get("receiver_location", "") or "")

    @property
    def invoice_number(self) -> str:
        return str(self.fields.get("invoice_number", "") or "")


@dataclass
class DocumentResult:
    status: str
    reason: str
    output_file: str | None
    evidence: dict[str, Any]


@dataclass
class JobSummary:
    job_id: str
    status: str
    started_at: str
    completed_at: str
    counts: dict[str, int]
    outputs: dict[str, Any]
    errors: list[str]


@dataclass
class JobContext:
    job_id: str
    root_dir: str
    input_dir: str
    ok_dir: str
    ok_confirmed_dir: str
    ok_warnings_dir: str
    review_dir: str
    failed_dir: str
    evidence_dir: str
    run_log_path: str
    summary_path: str
    review_items_path: str
    golden_report_path: str
    output_zip_path: str
    allowed_exts: set[str]
    seen_hashes: set[str]
    used_filenames: set[str]
    source_relpaths: dict[str, str]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _log_line(ctx: JobContext, text: str):
    line = f"{_now_iso()} {text}\n"
    with open(ctx.run_log_path, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info(text)


def _load_schema_fields() -> list[SchemaField]:
    schema_path = cfg.invoice_schema_path
    if not schema_path or not os.path.exists(schema_path):
        raise FileNotFoundError(f"Invoice schema not found: {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    fields = raw.get("fields", []) if isinstance(raw, dict) else []
    if not isinstance(fields, list):
        raise ValueError("Invoice schema must contain a 'fields' list")

    parsed: list[SchemaField] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        ftype = str(item.get("type", "string")).strip().lower()
        required = bool(item.get("required", False))
        if not name:
            continue
        if ftype not in {"string", "number", "integer", "boolean"}:
            raise ValueError(f"Unsupported schema field type for {name}: {ftype}")
        parsed.append(SchemaField(name=name, type=ftype, required=required))

    if len(parsed) != 43:
        raise ValueError(f"Invoice schema must define exactly 43 fields, got {len(parsed)}")

    names = [f.name for f in parsed]
    if len(names) != len(set(names)):
        raise ValueError("Invoice schema has duplicate field names")

    required_core = {
        "delivery_date",
        "receiver_name",
        "receiver_location",
        "invoice_number",
        "ocr_text_present",
        "confidence_score",
        "_extractor_model_used",
    }
    if not required_core.issubset(set(names)):
        missing = sorted(required_core - set(names))
        raise ValueError(f"Invoice schema missing required canonical fields: {missing}")

    return parsed


SCHEMA_FIELDS = _load_schema_fields()
FIELDS_43 = [f.name for f in SCHEMA_FIELDS]
FIELD_TYPES = {f.name: f.type for f in SCHEMA_FIELDS}


def _blank_for_type(ftype: str) -> Any:
    if ftype == "number":
        return 0.0
    if ftype == "integer":
        return 0
    if ftype == "boolean":
        return False
    return ""


def _blank_fields() -> dict[str, Any]:
    return {name: _blank_for_type(ftype) for name, ftype in FIELD_TYPES.items()}


def _safe_stem(name: str) -> str:
    value = re.sub(r"\s+", " ", str(name or "").strip())
    value = re.sub(r'[<>:"/\\|?*]', "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "UNKNOWN"


def _sanitize_filename_token(name: str, max_len: int = 32) -> str:
    token = _safe_stem(name).replace("'", "")
    token = re.sub(r"[\(\)\[\]]", "", token)
    token = re.sub(r"[^A-Za-z0-9\s._-]", "", token)
    token = re.sub(r"\s+", "_", token).strip("._-")
    if not token:
        token = "UNKNOWN"
    return token[:max_len]


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            continue

    m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except Exception:
            return ""

    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", text)
    if m:
        try:
            day = int(m.group(1))
            month = int(m.group(2))
            year_raw = int(m.group(3))
            year = year_raw + 2000 if year_raw < 100 else year_raw
            # Nigeria default: DD/MM/YY unless impossible
            try:
                return datetime.date(year, month, day).isoformat()
            except Exception:
                # fallback only when DD/MM impossible
                return datetime.date(year, day, month).isoformat()
        except Exception:
            return ""

    return ""


def _normalize_invoice_number(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 6 else ""


def _normalize_receiver_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = _safe_stem(raw)
    if text.upper() in {"UNKNOWN", "N/A", "NONE", "NULL"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _normalize_receiver_location(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = _safe_stem(raw)
    if text.upper() in {"UNKNOWN", "N/A", "NONE", "NULL"}:
        return ""
    if not text:
        return ""

    bracket_tokens = re.findall(r"\(([^\)]{2,40})\)", text)
    for token in bracket_tokens:
        candidate = re.sub(r"\d+", "", token).strip(" ,.-")
        if candidate:
            return _sanitize_filename_token(candidate, max_len=24).replace("_", " ")

    split_candidates = re.split(r"[,\-]", text)
    street_words = {
        "street",
        "st",
        "road",
        "rd",
        "avenue",
        "ave",
        "close",
        "cl",
        "junction",
        "house",
        "block",
        "plot",
        "way",
        "expressway",
    }
    for token in split_candidates:
        candidate = re.sub(r"\d+", "", token).strip(" ,.-")
        words = [w for w in re.split(r"\s+", candidate) if w]
        if not words:
            continue
        if any(w.lower() in street_words for w in words):
            continue
        short = " ".join(words[:3])
        if short:
            return _sanitize_filename_token(short, max_len=24).replace("_", " ")

    words = [w for w in re.split(r"\s+", re.sub(r"\d+", "", text)) if w]
    short = " ".join(words[:3])
    return _sanitize_filename_token(short, max_len=24).replace("_", " ") if short else ""


def _normalize_number(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return 0.0
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return 0.0


def _normalize_integer(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return 0


def _normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _normalize_by_key(key: str, value: Any) -> Any:
    if key == "delivery_date":
        return _normalize_date(value)
    if key == "invoice_number":
        return _normalize_invoice_number(value)
    if key == "receiver_name":
        return _normalize_receiver_name(value)
    if key == "receiver_location":
        return _normalize_receiver_location(value)

    ftype = FIELD_TYPES.get(key, "string")
    if ftype == "number":
        return _normalize_number(value)
    if ftype == "integer":
        return _normalize_integer(value)
    if ftype == "boolean":
        return _normalize_boolean(value)
    text = str(value or "").strip()
    return _safe_stem(text) if text else ""


def _coerce_schema_fields(raw_fields: dict[str, Any], *, model_used: str, ocr_text_present: bool) -> dict[str, Any]:
    merged = _blank_fields()
    for key in FIELDS_43:
        if key in raw_fields:
            merged[key] = _normalize_by_key(key, raw_fields.get(key))

    merged["ocr_text_present"] = bool(ocr_text_present)
    merged["_extractor_model_used"] = _safe_stem(model_used or "regex_fallback")

    confidence = merged.get("confidence_score", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    merged["confidence_score"] = max(0.0, min(1.0, confidence))

    if not merged.get("receiver_outlet"):
        rn = merged.get("receiver_name", "")
        rl = merged.get("receiver_location", "")
        if rn and rl:
            merged["receiver_outlet"] = f"{rn} ({rl})"
        elif rn:
            merged["receiver_outlet"] = rn
        else:
            merged["receiver_outlet"] = ""

    return merged


def _validate_fields(fields: ExtractedFields) -> ExtractedFields:
    violations: list[str] = []
    raw_invoice_input = str((fields.fields or {}).get("invoice_number", "") or "")
    raw_invoice_digits = re.sub(r"\D", "", raw_invoice_input)
    candidate = _coerce_schema_fields(
        fields.fields,
        model_used=fields._extractor_model_used,
        ocr_text_present=fields.ocr_text_present,
    )

    if set(candidate.keys()) != set(FIELDS_43):
        violations.append("schema_extraction_failed")

    if not candidate.get("delivery_date"):
        violations.append("missing_delivery_date")

    if not candidate.get("receiver_name") or not candidate.get("receiver_location"):
        violations.append("missing_receiver")

    inv = candidate.get("invoice_number", "")
    if not inv:
        if raw_invoice_digits:
            violations.append("invoice_number_not_6_digits")
        else:
            violations.append("missing_invoice_number")
    elif not re.fullmatch(r"\d{6}", str(inv)):
        violations.append("invoice_number_not_6_digits")

    confidence = candidate.get("confidence_score", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    candidate["confidence_score"] = max(0.0, min(1.0, confidence))

    cleaned_violations = sorted(set([v for v in violations if v]))
    return ExtractedFields(
        fields=candidate,
        confidence_score=float(candidate.get("confidence_score", 0.0)),
        schema_valid=len(cleaned_violations) == 0,
        violations=cleaned_violations,
        _extractor_model_used=str(candidate.get("_extractor_model_used", "regex_fallback")),
        ocr_text_present=bool(candidate.get("ocr_text_present", False)),
    )


def _sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_job_context(job_id: str) -> JobContext:
    root_dir = os.path.join(cfg.workspace_dir, "jobs", job_id)
    input_dir = os.path.join(root_dir, "input")
    ok_dir = os.path.join(root_dir, "artifacts", "OK")
    ok_confirmed_dir = os.path.join(ok_dir, "CONFIRMED")
    ok_warnings_dir = os.path.join(ok_dir, "WARNINGS")
    review_dir = os.path.join(root_dir, "artifacts", "Review")
    failed_dir = os.path.join(root_dir, "artifacts", "Failed")
    evidence_dir = os.path.join(root_dir, "evidence")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(ok_dir, exist_ok=True)
    os.makedirs(ok_confirmed_dir, exist_ok=True)
    os.makedirs(ok_warnings_dir, exist_ok=True)
    os.makedirs(review_dir, exist_ok=True)
    os.makedirs(failed_dir, exist_ok=True)
    os.makedirs(evidence_dir, exist_ok=True)
    return JobContext(
        job_id=job_id,
        root_dir=root_dir,
        input_dir=input_dir,
        ok_dir=ok_dir,
        ok_confirmed_dir=ok_confirmed_dir,
        ok_warnings_dir=ok_warnings_dir,
        review_dir=review_dir,
        failed_dir=failed_dir,
        evidence_dir=evidence_dir,
        run_log_path=os.path.join(root_dir, "run.log"),
        summary_path=os.path.join(root_dir, "summary.json"),
        review_items_path=os.path.join(root_dir, "review_items.json"),
        golden_report_path=os.path.join(root_dir, "golden_verification_report.json"),
        output_zip_path=os.path.join(root_dir, f"job_{job_id}_output.zip"),
        allowed_exts=set(cfg.invoice_allowed_exts),
        seen_hashes=set(),
        used_filenames=set(),
        source_relpaths={},
    )


def _extract_zip_to_input(src_zip: str, ctx: JobContext, allowed_exts: set[str] | None = None) -> tuple[list[str], list[str]]:
    if os.path.getsize(src_zip) > _MAX_ZIP_BYTES:
        raise ValueError(f"Zip exceeds max size ({_MAX_ZIP_BYTES} bytes)")

    ext_filter = allowed_exts or ctx.allowed_exts
    extracted: list[str] = []
    unsupported: list[str] = []

    with zipfile.ZipFile(src_zip, "r") as zf:
        members = [m for m in zf.namelist() if not m.endswith("/")]
        if len(members) > _MAX_FILES:
            raise ValueError(f"Zip contains too many files ({len(members)} > {_MAX_FILES})")

        for name in members:
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if ext not in ext_filter:
                unsupported.append(name)
                continue

            base = os.path.basename(name)
            safe = _sanitize_filename_token(os.path.splitext(base)[0], max_len=64)
            member_id = hashlib.sha256(name.encode("utf-8", errors="ignore")).hexdigest()[:12]
            out_name = f"{safe}__{member_id}.{ext}"
            out_path = os.path.join(ctx.input_dir, out_name)

            with zf.open(name) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            extracted.append(out_path)
            ctx.source_relpaths[out_path] = name

    return extracted, unsupported


def _copy_input_file(input_path: str, ctx: JobContext) -> list[str]:
    safe_name = os.path.basename(input_path)
    out_path = os.path.join(ctx.input_dir, safe_name)
    shutil.copy2(input_path, out_path)
    ctx.source_relpaths[out_path] = safe_name
    return [out_path]


def _extract_text_pdf(path: str) -> tuple[str, dict[str, Any]]:
    details: dict[str, Any] = {"source": "pdf"}
    text_chunks: list[str] = []
    page_count = 0

    try:
        reader = PdfReader(path)
        page_count = len(reader.pages)
        for page in reader.pages:
            text_chunks.append(page.extract_text() or "")
    except Exception:
        pass

    if fitz and pytesseract and Image:
        try:
            doc = fitz.open(path)
            page_count = page_count or len(doc)
            for page_idx in range(min(3, len(doc))):
                pix = doc[page_idx].get_pixmap(dpi=220)
                img_path = os.path.join(os.path.dirname(path), f"_ocr_page_{page_idx}_{uuid.uuid4().hex[:8]}.png")
                pix.save(img_path)
                ocr_text = _ocr_image_variants(img_path)
                if ocr_text:
                    text_chunks.append(ocr_text)
                try:
                    os.remove(img_path)
                except Exception:
                    pass
            doc.close()
        except Exception:
            pass

    details["page_count"] = page_count
    merged = "\n".join([t for t in text_chunks if t]).strip()
    return merged, details


def _ocr_image_variants(path: str) -> str:
    if not (Image and pytesseract):
        return ""
    try:
        img = Image.open(path)
    except Exception:
        return ""

    variants = [img]
    try:
        gray = ImageOps.grayscale(img)
        variants.append(gray)
        if ImageEnhance:
            variants.append(ImageEnhance.Contrast(gray).enhance(1.8))
        if ImageFilter:
            variants.append(gray.filter(ImageFilter.SHARPEN))
            variants.append(gray.filter(ImageFilter.MedianFilter(size=3)))
        variants.append(gray.rotate(1.0, expand=True))
        variants.append(gray.rotate(-1.0, expand=True))
    except Exception:
        pass

    best = ""
    for var in variants:
        try:
            txt = pytesseract.image_to_string(var)
        except Exception:
            txt = ""
        if len(txt) > len(best):
            best = txt
    return best.strip()


def _extract_text_image(path: str) -> tuple[str, dict[str, Any]]:
    details = {
        "source": "image_ocr",
        "ocr_engine": "pytesseract" if pytesseract else "none",
        "page_count": 1,
    }
    return _ocr_image_variants(path), details


def _extract_text(path: str) -> tuple[str, dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _extract_text_pdf(path)
    return _extract_text_image(path)


def _json_from_response_text(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return {}
    return {}


def _best_preprocessed_image(input_path: str, output_path: str) -> str | None:
    if not Image:
        return None
    try:
        img = Image.open(input_path)
        gray = ImageOps.grayscale(img)
        if ImageEnhance:
            gray = ImageEnhance.Contrast(gray).enhance(1.7)
        gray.save(output_path)
        return output_path
    except Exception:
        return None


def _zone_ocr_retry(image_path: str) -> dict[str, str]:
    if not (Image and pytesseract) or not image_path or not os.path.exists(image_path):
        return {"top": "", "mid": "", "bottom": ""}
    try:
        img = Image.open(image_path)
    except Exception:
        return {"top": "", "mid": "", "bottom": ""}

    w, h = img.size
    boxes = {
        "top": (0, 0, w, int(h * 0.25)),
        "mid": (0, int(h * 0.25), w, int(h * 0.62)),
        "bottom": (0, int(h * 0.60), w, h),
    }
    zone_texts: dict[str, str] = {"top": "", "mid": "", "bottom": ""}
    for zone, box in boxes.items():
        try:
            crop = img.crop(box)
            txt = pytesseract.image_to_string(crop)
            if txt.strip():
                zone_texts[zone] = txt.strip()
        except Exception:
            continue
    return zone_texts


def _first_page_image_for_vision(path: str, evidence_dir: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic"}:
        preview_path = os.path.join(evidence_dir, "preprocessed.png")
        created = _best_preprocessed_image(path, preview_path)
        if created:
            return created
        return path

    if ext == ".pdf" and fitz:
        try:
            doc = fitz.open(path)
            if len(doc) == 0:
                return None
            pix = doc[0].get_pixmap(dpi=220)
            out_path = os.path.join(evidence_dir, "preprocessed.png")
            pix.save(out_path)
            doc.close()
            return out_path
        except Exception:
            return None
    return None


def _required_rename_issues(fields: ExtractedFields) -> list[str]:
    issues = []
    if not fields.delivery_date:
        issues.append("missing_delivery_date")
    if not fields.receiver_name or not fields.receiver_location:
        issues.append("missing_receiver")
    inv = fields.invoice_number
    if not inv:
        issues.append("missing_invoice_number")
    elif not re.fullmatch(r"\d{6}", inv):
        issues.append("invoice_number_not_6_digits")
    return issues


def _infer_receiver_from_source_key(source_key: str) -> tuple[str, str]:
    raw = str(source_key or "").strip()
    if not raw:
        return "", ""
    stem = os.path.splitext(os.path.basename(raw))[0]
    tokens = [t for t in re.split(r"[_\-\s]+", stem) if t]
    filtered: list[str] = []
    for t in tokens:
        if re.fullmatch(r"\d{6,}", t):
            continue
        if re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", t):
            continue
        if t.lower() in {"invoice", "scan", "img", "image", "copy", "duplicate"}:
            continue
        filtered.append(t)
    if len(filtered) >= 3:
        name = " ".join(filtered[:2])
        loc = filtered[2]
    elif len(filtered) == 2:
        name, loc = filtered[0], filtered[1]
    elif len(filtered) == 1:
        name, loc = filtered[0], ""
    else:
        return "", ""
    return _normalize_receiver_name(name), _normalize_receiver_location(loc)


def _infer_receiver_from_text(text: str) -> tuple[str, str]:
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    if not lines:
        return "", ""

    name = ""
    location = ""
    for idx, line in enumerate(lines):
        m = re.search(r"\bParty\b[:\-]?\s*(.+)?$", line, flags=re.IGNORECASE)
        if not m:
            continue
        same_line = str(m.group(1) or "").strip()
        if same_line and same_line.lower() not in {"-", ":", "party"}:
            name = same_line
        for j in range(idx + 1, min(idx + 5, len(lines))):
            nxt = lines[j]
            if re.search(r"\b(order no|dispatch|description|quantity|amount|authori[sz]ed)\b", nxt, flags=re.IGNORECASE):
                break
            if not name:
                name = nxt
                continue
            if not location:
                location = nxt
                break
        break

    if not location:
        m_to = re.search(r"\bTo\b\s*[:\-]?\s*([A-Za-z][A-Za-z\s]{1,40})", text or "", flags=re.IGNORECASE)
        if m_to:
            location = m_to.group(1).strip()

    return _normalize_receiver_name(name), _normalize_receiver_location(location)


_STORE_KEYWORDS = {
    "supermarket",
    "hypermart",
    "mart",
    "market",
    "stores",
    "store",
    "shop",
}


def _has_store_keyword(text: str) -> bool:
    t = str(text or "").lower()
    return any(k in t for k in _STORE_KEYWORDS)


def _looks_person_like(text: str) -> bool:
    tokens = [x for x in re.split(r"\s+", str(text or "").strip()) if x]
    if not tokens:
        return False
    if _has_store_keyword(text):
        return False
    if len(tokens) <= 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", t) for t in tokens):
        return True
    return False


def _standardize_pdf_for_evidence(path: str, evidence_dir: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    out_name = "standardized.pdf"
    out_path = os.path.join(evidence_dir, out_name)
    if ext == ".pdf":
        shutil.copy2(path, out_path)
    else:
        _to_pdf_path(path, out_path)
    return out_path


def _extract_date_candidates_from_lines(text: str, region: str = "unknown") -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not text:
        return candidates

    keyword_re = re.compile(r"(received|delivery|delivered|stamp|pod|security|sign|approved|recd)", re.IGNORECASE)
    token_re = re.compile(
        r"(\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b20\d{2}[/-]\d{1,2}[/-]\d{1,2}\b|\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b)",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        has_kw = bool(keyword_re.search(line))
        for match in token_re.findall(line):
            iso = _normalize_date(match)
            if not iso:
                continue
            score = 0
            if has_kw:
                score += 3
            if region == "bottom":
                score += 2
            elif region == "top":
                score += 1
            warnings = []
            if has_kw:
                warnings.append("date_best_score_inferred")
            if region == "bottom":
                warnings.append("date_from_stamp_region")
            if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2}", match.strip()):
                warnings.append("date_ddmmyy_assumed")
            candidates.append(
                {
                    "raw": match,
                    "line": line[:180],
                    "date": iso,
                    "region": region,
                    "score": score,
                    "warnings": warnings,
                }
            )
    return candidates


def _choose_best_delivery_date(
    fields: ExtractedFields,
    full_text: str,
    zone_texts: dict[str, str] | None = None,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    zone_texts = zone_texts or {}
    candidates: list[dict[str, Any]] = []

    # structured candidates first
    for src_key in ["delivery_date", "invoice_date", "dispatch_date", "due_date"]:
        raw = str(fields.fields.get(src_key, "") or "").strip()
        if not raw:
            continue
        iso = _normalize_date(raw)
        if not iso:
            continue
        score = 1
        warnings = []
        if src_key != "delivery_date":
            warnings.append("date_best_score_inferred")
            score += 1
        candidates.append(
            {
                "raw": raw,
                "line": src_key,
                "date": iso,
                "region": "structured",
                "score": score,
                "warnings": warnings,
            }
        )

    candidates.extend(_extract_date_candidates_from_lines(full_text, region="unknown"))
    candidates.extend(_extract_date_candidates_from_lines(zone_texts.get("top", ""), region="top"))
    candidates.extend(_extract_date_candidates_from_lines(zone_texts.get("mid", ""), region="mid"))
    candidates.extend(_extract_date_candidates_from_lines(zone_texts.get("bottom", ""), region="bottom"))

    if not candidates:
        return "", [], []

    # deterministic tie-break: higher score, then bottom/keyword-ish region, then earliest textual order
    region_rank = {"bottom": 3, "structured": 2, "mid": 1, "top": 1, "unknown": 0}
    best = sorted(
        candidates,
        key=lambda c: (int(c.get("score", 0)), region_rank.get(str(c.get("region")), 0)),
        reverse=True,
    )[0]
    return str(best.get("date", "")), list(best.get("warnings", [])), candidates


def _extract_with_gemini_vision(
    image_path: str,
    ocr_text: str,
    hints: dict[str, Any],
    missing_fields: list[str] | None = None,
    model_name: str | None = None,
) -> ExtractedFields | None:
    if not (_genai_client and image_path and os.path.exists(image_path)):
        return None

    model = model_name or cfg.model_name
    missing_hint = ", ".join(missing_fields or [])
    schema_lines = []
    for sf in SCHEMA_FIELDS:
        req = "required" if sf.required else "optional"
        schema_lines.append(f"- {sf.name}: {sf.type} ({req})")

    prompt = (
        "You are extracting fields from an invoice image. Return JSON object only.\n"
        "Never omit keys. Return exactly these 43 keys:\n"
        + "\n".join(schema_lines)
        + "\nCritical rename fields:\n"
        "- receiver_name\n"
        "- receiver_location (short locality only)\n"
        "- invoice_number (digits-only, exactly 6)\n"
        "- delivery_date (YYYY-MM-DD)\n"
        "If a field is unreadable, return empty string for that field.\n"
        + (f"Focus especially on these missing fields: {missing_hint}\n" if missing_hint else "")
        + "OCR support text:\n"
        + (ocr_text[:8000] if ocr_text else "")
        + "\nHints:\n"
        + json.dumps(hints, ensure_ascii=False)
    )

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        contents = [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": image_bytes}},
        ]
        response = _genai_client.models.generate_content(model=model, contents=contents)
        parsed = _json_from_response_text(getattr(response, "text", "") or "")
        if not isinstance(parsed, dict) or not parsed:
            return None
        candidate = ExtractedFields(
            fields=parsed,
            confidence_score=float(parsed.get("confidence_score") or 0.0),
            schema_valid=False,
            violations=[],
            _extractor_model_used=model,
            ocr_text_present=bool((ocr_text or "").strip()),
        )
        return _validate_fields(candidate)
    except Exception:
        return None


def _regex_extract_fields(text: str) -> dict[str, Any]:
    output = _blank_fields()
    output["document_type"] = "invoice"
    output["language"] = "en"

    inv_match = re.search(r"\b(\d{6})\b", text)
    if inv_match:
        output["invoice_number"] = inv_match.group(1)

    date_patterns = [
        r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b",
    ]
    detected_date = ""
    for pat in date_patterns:
        m = re.search(pat, text)
        if not m:
            continue
        if pat.startswith(r"\b(20"):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            detected_date = datetime.date(y, mo, d).isoformat()
            break
        except Exception:
            continue

    output["delivery_date"] = detected_date

    receiver_patterns = [
        r"receiver(?:\s*outlet)?[:\-]\s*(.+)",
        r"outlet[:\-]\s*(.+)",
        r"received by[:\-]\s*(.+)",
    ]
    receiver_line = ""
    for pat in receiver_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            receiver_line = m.group(1).split("\n")[0].strip()
            break

    if receiver_line:
        parts = [p.strip() for p in re.split(r"[,\-]", receiver_line) if p.strip()]
        output["receiver_name"] = parts[0] if parts else receiver_line
        output["receiver_location"] = parts[1] if len(parts) > 1 else receiver_line
        output["receiver_outlet"] = receiver_line

    output["confidence_score"] = 0.45
    output["ocr_text_present"] = bool(text.strip())
    output["_extractor_model_used"] = "regex_fallback"
    return output


def _build_gemini_prompt(text: str, hints: dict[str, Any]) -> str:
    schema_lines = []
    for sf in SCHEMA_FIELDS:
        req = "required" if sf.required else "optional"
        schema_lines.append(f"- {sf.name}: {sf.type} ({req})")

    return (
        "Extract invoice fields and return STRICT JSON only.\n"
        "Do not include explanations.\n"
        "Return exactly these 43 keys:\n"
        + "\n".join(schema_lines)
        + "\nRules:\n"
        "- delivery_date must be YYYY-MM-DD when present\n"
        "- invoice_number digits only\n"
        "- confidence_score between 0 and 1\n"
        "- ocr_text_present must be boolean\n"
        "\nHints:\n"
        + json.dumps(hints, ensure_ascii=False)
        + "\n\nOCR Text:\n"
        + text[:20000]
    )


def extract_invoice_fields(text: str, hints: dict) -> ExtractedFields:
    ocr_text_present = bool((text or "").strip())
    if not ocr_text_present:
        base = _blank_fields()
        base["ocr_text_present"] = False
        base["_extractor_model_used"] = "regex_fallback"
        return _validate_fields(
            ExtractedFields(
                fields=base,
                confidence_score=0.0,
                schema_valid=False,
                violations=["ocr_no_text"],
                _extractor_model_used="regex_fallback",
                ocr_text_present=False,
            )
        )

    if _genai_client:
        prompt = _build_gemini_prompt(text, hints)
        for model_name in [cfg.model_name, "gemini-1.5-pro"]:
            if not model_name:
                continue
            try:
                response = _genai_client.models.generate_content(model=model_name, contents=prompt)
                raw = (getattr(response, "text", "") or "").strip()
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("Extractor output is not a JSON object")
                candidate = ExtractedFields(
                    fields=parsed,
                    confidence_score=float(parsed.get("confidence_score") or 0.0),
                    schema_valid=False,
                    violations=[],
                    _extractor_model_used=model_name,
                    ocr_text_present=ocr_text_present,
                )
                validated = _validate_fields(candidate)
                if validated.schema_valid:
                    return validated
            except Exception:
                continue

    regex_candidate = ExtractedFields(
        fields=_regex_extract_fields(text),
        confidence_score=0.45,
        schema_valid=False,
        violations=[],
        _extractor_model_used="regex_fallback",
        ocr_text_present=ocr_text_present,
    )
    return _validate_fields(regex_candidate)


def _resolve_collision(base_name: str, ctx: JobContext) -> str:
    candidate = base_name
    idx = 2
    while candidate.lower() in ctx.used_filenames:
        stem, ext = os.path.splitext(base_name)
        candidate = f"{stem}_DUP{idx}{ext}"
        idx += 1
    ctx.used_filenames.add(candidate.lower())
    return candidate


def _to_pdf_path(path: str, out_path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        shutil.copy2(path, out_path)
        return out_path
    if not Image:
        raise RuntimeError("Pillow not available for image-to-pdf conversion")
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(out_path, "PDF", resolution=100.0)
    return out_path


def build_output_pdf(input_path: str, normalized_name: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, normalized_name)
    return _to_pdf_path(input_path, out_path)


def _build_ok_filename(fields: ExtractedFields) -> str:
    receiver_name = _sanitize_filename_token(fields.receiver_name, max_len=40)
    receiver_location = _sanitize_filename_token(fields.receiver_location, max_len=24)
    invoice_number = fields.invoice_number
    delivery_date = fields.delivery_date
    return f"{receiver_name}_{receiver_location}_{invoice_number}_{delivery_date}.pdf"


def _build_expected_index(expected_raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(expected_raw, dict):
        return {}

    if isinstance(expected_raw.get("items"), list):
        index: dict[str, dict[str, Any]] = {}
        for item in expected_raw["items"]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("source_id") or "").strip().lower()
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            if key and fields:
                index[key] = fields
        return index

    index = {}
    for key, value in expected_raw.items():
        if not isinstance(value, dict):
            continue
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else value
        if isinstance(fields, dict):
            index[str(key).strip().lower()] = fields
    return index


def _load_expected_labels(path: str) -> dict[str, dict[str, Any]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _build_expected_index(data)
    except Exception:
        return {}


def _normalize_for_compare(key: str, value: Any) -> Any:
    if key == "delivery_date":
        return _normalize_date(value)
    if key == "invoice_number":
        return _normalize_invoice_number(value)
    if key == "receiver_name":
        return _normalize_receiver_name(value).lower()
    if key == "receiver_location":
        return _normalize_receiver_location(value).lower()

    ftype = FIELD_TYPES.get(key, "string")
    if ftype == "number":
        return round(_normalize_number(value), 4)
    if ftype == "integer":
        return _normalize_integer(value)
    if ftype == "boolean":
        return _normalize_boolean(value)
    return _safe_stem(str(value or "")).lower()


def _compare_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for key in FIELDS_43:
        exp = _normalize_for_compare(key, expected.get(key))
        got = _normalize_for_compare(key, actual.get(key))
        if exp != got:
            diffs.append({"field": key, "expected": expected.get(key), "actual": actual.get(key)})
    return diffs


def _build_golden_verification_report(
    ctx: JobContext,
    outputs: list[dict[str, Any]],
    counts: dict[str, int],
    errors: list[str],
) -> dict[str, Any]:
    rename_success_count = 0
    ok_confirmed = 0
    ok_warnings = 0
    warning_counter: collections.Counter[str] = collections.Counter()
    samples: list[str] = []
    for item in outputs:
        if item.get("status") == "ok" and item.get("output_file"):
            rename_success_count += 1
            samples.append(str(item.get("output_file")))
            warnings = ((item.get("evidence") or {}).get("warning_codes") or [])
            if warnings:
                ok_warnings += 1
                warning_counter.update([str(w) for w in warnings])
            else:
                ok_confirmed += 1

    pass_counts = (
        counts.get("total", 0) == _GOLDEN_EXPECTED_COUNT
        and counts.get("ok", 0) == _GOLDEN_EXPECTED_COUNT
        and counts.get("review", 0) == 0
        and counts.get("failed", 0) == 0
    )
    pass_rename = rename_success_count == _GOLDEN_EXPECTED_COUNT

    return {
        "mode": "golden_verify",
        "job_id": ctx.job_id,
        "input_path": cfg.golden_input_path,
        "expected_count": _GOLDEN_EXPECTED_COUNT,
        "processed_count": counts.get("total", 0),
        "counts": counts,
        "ok_confirmed": ok_confirmed,
        "ok_warnings": ok_warnings,
        "rename_success_count": rename_success_count,
        "rename_samples": samples[:10],
        "warning_code_frequency": dict(warning_counter),
        "pass_counts": pass_counts,
        "pass_rename": pass_rename,
        "pass_all": pass_counts and pass_rename,
        "errors": errors,
    }


def process_document(path: str, job_ctx: JobContext) -> DocumentResult:
    basename = os.path.basename(path)
    source_key = job_ctx.source_relpaths.get(path, basename)
    digest = _sha256_file(path)
    if digest in job_ctx.seen_hashes:
        return DocumentResult(
            status="skipped_duplicate",
            reason="hash_identical_duplicate",
            output_file=None,
            evidence={"input_file": basename, "source_key": source_key, "source_id": digest},
        )
    job_ctx.seen_hashes.add(digest)

    try:
        evidence_dir = os.path.join(job_ctx.evidence_dir, digest[:12])
        os.makedirs(evidence_dir, exist_ok=True)
        ext = os.path.splitext(path)[1].lower()
        original_copy_path = os.path.join(evidence_dir, f"original{ext if ext else '.bin'}")
        try:
            shutil.copy2(path, original_copy_path)
        except Exception:
            pass

        text, extraction_meta = _extract_text(path)
        with open(os.path.join(evidence_dir, "ocr_raw.txt"), "w", encoding="utf-8") as f:
            f.write(text or "")

        preview_path = _first_page_image_for_vision(path, evidence_dir)
        standardized_path = _standardize_pdf_for_evidence(path, evidence_dir)
        attempts: list[dict[str, Any]] = []
        hints = {"filename": basename, "source_key": source_key}
        warning_codes: list[str] = []
        zone_texts: dict[str, str] = {"top": "", "mid": "", "bottom": ""}

        # Pass A: OCR text + Gemini extraction.
        fields = extract_invoice_fields(text, hints)
        issues = _required_rename_issues(fields)
        attempts.append(
            {
                "pass": "A",
                "strategy": "ocr_plus_gemini_text",
                "issues": issues,
                "model": fields._extractor_model_used,
                "schema_valid": fields.schema_valid,
            }
        )

        # Pass B: Stronger vision extraction targeting missing rename fields.
        if issues and preview_path:
            b_fields = _extract_with_gemini_vision(
                image_path=preview_path,
                ocr_text=text,
                hints=hints,
                missing_fields=issues,
                model_name="gemini-1.5-pro",
            )
            if b_fields:
                b_issues = _required_rename_issues(b_fields)
                attempts.append(
                    {
                        "pass": "B",
                        "strategy": "vision_targeted",
                        "issues": b_issues,
                        "model": b_fields._extractor_model_used,
                        "schema_valid": b_fields.schema_valid,
                    }
                )
                if len(b_issues) < len(issues):
                    fields = b_fields
                    issues = b_issues

        # Pass C: Zone OCR retry + targeted vision retry.
        if issues and preview_path:
            zone_text = _zone_ocr_retry(preview_path)
            zone_texts = zone_text or {"top": "", "mid": "", "bottom": ""}
            text_c = "\n".join([t for t in [text, zone_texts.get("top", ""), zone_texts.get("mid", ""), zone_texts.get("bottom", "")] if t]).strip()
            c_fields_text = extract_invoice_fields(text_c, hints)
            c_issues = _required_rename_issues(c_fields_text)
            attempts.append(
                {
                    "pass": "C",
                    "strategy": "zone_ocr_plus_text_retry",
                    "issues": c_issues,
                    "model": c_fields_text._extractor_model_used,
                    "schema_valid": c_fields_text.schema_valid,
                }
            )
            if len(c_issues) < len(issues):
                fields = c_fields_text
                issues = c_issues

            if issues:
                c_fields_vision = _extract_with_gemini_vision(
                    image_path=preview_path,
                    ocr_text=text_c,
                    hints=hints,
                    missing_fields=issues,
                    model_name=cfg.model_name,
                )
                if c_fields_vision:
                    cv_issues = _required_rename_issues(c_fields_vision)
                    attempts.append(
                        {
                            "pass": "C2",
                            "strategy": "zone_ocr_plus_vision_retry",
                            "issues": cv_issues,
                            "model": c_fields_vision._extractor_model_used,
                            "schema_valid": c_fields_vision.schema_valid,
                        }
                    )
                    if len(cv_issues) < len(issues):
                        fields = c_fields_vision
                        issues = cv_issues

        # Aggressive delivery-date resolution policy.
        chosen_date, date_warnings, date_candidates = _choose_best_delivery_date(fields, text, zone_texts)
        current_date = _normalize_date(fields.fields.get("delivery_date", ""))
        if chosen_date and (not current_date or current_date != chosen_date):
            if current_date and current_date != chosen_date:
                warning_codes.append("date_best_score_override")
            fields.fields["delivery_date"] = chosen_date
            warning_codes.extend(date_warnings or ["date_best_score_inferred"])
            if date_warnings:
                warning_codes.extend(date_warnings)
        fields.date_candidates = date_candidates
        fields.warning_codes = sorted(set((fields.warning_codes or []) + warning_codes))
        fields = _validate_fields(fields)
        issues = _required_rename_issues(fields)
        source_name, source_loc = _infer_receiver_from_source_key(source_key)
        text_name, text_loc = _infer_receiver_from_text(text)
        patched = False
        receiver_warnings: list[str] = []
        current_name = _normalize_receiver_name(fields.fields.get("receiver_name", ""))
        current_loc = _normalize_receiver_location(fields.fields.get("receiver_location", ""))

        if not current_name:
            fallback_name = text_name or source_name
            if fallback_name:
                fields.fields["receiver_name"] = fallback_name
                patched = True
                receiver_warnings.append("receiver_inferred")

        if not current_loc:
            fallback_loc = text_loc or source_loc
            if fallback_loc:
                fields.fields["receiver_location"] = fallback_loc
                patched = True
                receiver_warnings.append("receiver_location_inferred")

        current_name = _normalize_receiver_name(fields.fields.get("receiver_name", ""))
        current_loc = _normalize_receiver_location(fields.fields.get("receiver_location", ""))

        if text_name and _has_store_keyword(text_name):
            if (not _has_store_keyword(current_name)) or _looks_person_like(current_name):
                fields.fields["receiver_name"] = text_name
                patched = True
                receiver_warnings.append("receiver_party_override")

        if text_loc:
            if _looks_person_like(current_loc) or len(str(current_loc or "").split()) > 3:
                fields.fields["receiver_location"] = text_loc
                patched = True
                receiver_warnings.append("receiver_location_override")

        if patched:
            fields.warning_codes = sorted(set((fields.warning_codes or []) + receiver_warnings))
            fields = _validate_fields(fields)
            issues = _required_rename_issues(fields)

        if float(fields.confidence_score or 0.0) < 0.8:
            fields.warning_codes = sorted(set((fields.warning_codes or []) + ["low_confidence_extraction"]))

        debug_payload = {
            "source_file": basename,
            "source_key": source_key,
            "source_id": digest,
            "attempts": attempts,
            "final_issues": issues,
            "final_fields": fields.fields,
            "warning_codes": fields.warning_codes,
            "date_candidates": fields.date_candidates,
            "chosen_delivery_date": fields.fields.get("delivery_date", ""),
            "meta": extraction_meta,
        }
        with open(os.path.join(evidence_dir, "extraction_debug.json"), "w", encoding="utf-8") as f:
            json.dump(debug_payload, f, indent=2, ensure_ascii=False)

        base_evidence = {
            "input_file": basename,
            "source_key": source_key,
            "source_id": digest,
            "ocr_text_present": bool((text or "").strip()),
            "ocr_text_excerpt": (text or "")[:1000],
            "fields": fields.fields,
            "meta": extraction_meta,
            "warning_codes": fields.warning_codes,
            "debug_paths": {
                "ocr_raw": os.path.relpath(os.path.join(evidence_dir, "ocr_raw.txt"), job_ctx.root_dir).replace("\\", "/"),
                "preprocessed": (
                    os.path.relpath(preview_path, job_ctx.root_dir).replace("\\", "/")
                    if preview_path and os.path.exists(preview_path)
                    else None
                ),
                "original": (
                    os.path.relpath(original_copy_path, job_ctx.root_dir).replace("\\", "/")
                    if os.path.exists(original_copy_path)
                    else None
                ),
                "standardized": os.path.relpath(standardized_path, job_ctx.root_dir).replace("\\", "/"),
                "extraction_debug": os.path.relpath(os.path.join(evidence_dir, "extraction_debug.json"), job_ctx.root_dir).replace("\\", "/"),
            },
        }

        if issues or not fields.schema_valid:
            review_name = _resolve_collision(f"{_sanitize_filename_token(os.path.splitext(basename)[0], max_len=64)}.pdf", job_ctx)
            output = build_output_pdf(path, review_name, job_ctx.review_dir)
            reason = ";".join(sorted(set((fields.violations or []) + issues))) or "schema_extraction_failed"
            return DocumentResult(
                status="review",
                reason=reason,
                output_file=output,
                evidence=base_evidence,
            )

        normalized_name = _resolve_collision(_build_ok_filename(fields), job_ctx)
        target_dir = job_ctx.ok_warnings_dir if fields.warning_codes else job_ctx.ok_confirmed_dir
        output = os.path.join(target_dir, normalized_name)
        shutil.copy2(standardized_path, output)
        return DocumentResult(
            status="ok",
            reason="validated_with_warnings" if fields.warning_codes else "validated",
            output_file=output,
            evidence=base_evidence,
        )
    except Exception as e:
        failed_name = _resolve_collision(f"{_sanitize_filename_token(os.path.splitext(basename)[0], max_len=64)}.pdf", job_ctx)
        output_path = None
        try:
            output_path = build_output_pdf(path, failed_name, job_ctx.failed_dir)
        except Exception:
            output_path = None
        return DocumentResult(
            status="failed",
            reason=f"pipeline_exception:{e}",
            output_file=output_path,
            evidence={
                "input_file": basename,
                "source_key": source_key,
                "source_id": digest,
                "error": str(e),
            },
        )


def _zip_results(ctx: JobContext):
    with zipfile.ZipFile(ctx.output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Keep deterministic top-level folder structure visible even when empty.
        for folder in [
            "artifacts/",
            "artifacts/OK/",
            "artifacts/OK/CONFIRMED/",
            "artifacts/OK/WARNINGS/",
            "artifacts/Review/",
            "artifacts/Failed/",
            "evidence/",
        ]:
            zf.writestr(folder, "")
        for root, _, files in os.walk(ctx.root_dir):
            for file_name in files:
                if file_name == os.path.basename(ctx.output_zip_path):
                    continue
                full = os.path.join(root, file_name)
                arc = os.path.relpath(full, ctx.root_dir)
                zf.write(full, arc)


def enqueue_invoice_job(input_path: str, channel: str, user_id: str) -> str:
    normalized_input = os.path.abspath(str(input_path or "").strip())
    if not normalized_input:
        raise ValueError("input_path is required")

    # Deterministic idempotency key per user/channel/path+mtime for stable retries.
    stat = os.stat(normalized_input)
    idem_seed = f"{user_id}|{channel}|{normalized_input}|{int(stat.st_mtime)}|{stat.st_size}"
    idempotency_key = hashlib.sha256(idem_seed.encode("utf-8")).hexdigest()[:32]

    task_id = _enqueue_via_control_api(
        input_path=normalized_input,
        channel=channel,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )
    if task_id:
        return task_id

    # Fallback if control API process is unavailable.
    queue = TaskQueue()
    return queue.enqueue(
        task_type="invoice_job",
        payload={"input_path": normalized_input, "task_type": "invoice_job"},
        channel=channel,
        user_id=user_id,
        priority=2,
        idempotency_key=idempotency_key,
    )


def _enqueue_via_control_api(
    *,
    input_path: str,
    channel: str,
    user_id: str,
    idempotency_key: str,
) -> str | None:
    base_url = os.getenv("VICTOR_TASK_API_URL", "http://127.0.0.1:8787").rstrip("/")
    url = f"{base_url}/v1/tasks"
    payload = {
        "intent": f"enqueue invoice job for {os.path.basename(input_path)}",
        "channel": channel,
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "risk_level_hint": "medium",
        "context_refs": [input_path],
        "payload": {
            "task_type": "invoice_job",
            "input_path": input_path,
        },
    }
    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning(f"Control API unavailable, fallback to local enqueue: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"Control API enqueue failed unexpectedly, fallback to local queue: {exc}")
        return None

    try:
        data = json.loads(body)
    except Exception:
        logger.warning("Control API returned non-JSON response, fallback to local queue")
        return None

    if not data.get("ok"):
        logger.warning(f"Control API rejected enqueue: {data}")
        return None
    task_id = str(data.get("task_id") or "").strip()
    return task_id or None


def run_invoice_job(payload: dict) -> str:
    if not cfg.invoice_job_enabled:
        return "Invoice job is disabled by configuration."

    task_id = str(payload.get("_task_id") or "")
    job_mode = str(payload.get("mode") or cfg.invoice_mode or "production").strip().lower()
    input_path = str(payload.get("input_path") or "").strip()
    if not input_path and job_mode == "golden_verify":
        input_path = cfg.golden_input_path
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input path not found: {input_path}")

    queue = TaskQueue()
    job_id = uuid.uuid4().hex[:12]
    ctx = _build_job_context(job_id)
    started = _now_iso()
    counts = {"total": 0, "ok": 0, "review": 0, "skipped_duplicate": 0, "failed": 0}
    outputs: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    errors: list[str] = []
    review_reason_counter: collections.Counter[str] = collections.Counter()
    warning_counter: collections.Counter[str] = collections.Counter()

    _log_line(ctx, f"Invoice job started input={input_path}")
    if task_id:
        queue.update_progress(task_id, 5, "Invoice job started")

    paths: list[str] = []
    golden_image_exts = {"png", "jpg", "jpeg"}
    allowed_exts = ctx.allowed_exts
    if job_mode == "golden_verify" and cfg.golden_images_only:
        allowed_exts = golden_image_exts

    if os.path.splitext(input_path)[1].lower() == ".zip":
        paths, unsupported = _extract_zip_to_input(input_path, ctx, allowed_exts=allowed_exts)
        for name in unsupported:
            counts["failed"] += 1
            outputs.append(
                {
                    "input_file": os.path.basename(name),
                    "status": "failed",
                    "reason": "unsupported_file_type",
                    "output_file": None,
                    "evidence": {"source_key": name},
                }
            )
    else:
        ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        if ext not in allowed_exts:
            raise ValueError(f"Unsupported invoice input file type: .{ext}")
        paths = _copy_input_file(input_path, ctx)

    counts["total"] = len(paths)
    if task_id:
        queue.update_progress(task_id, 20, f"Loaded {len(paths)} input file(s)")

    for idx, path in enumerate(paths, start=1):
        result = process_document(path, ctx)
        counts[result.status] = counts.get(result.status, 0) + 1
        item = {
            "input_file": os.path.basename(path),
            "status": result.status,
            "reason": result.reason,
            "output_file": os.path.relpath(result.output_file, ctx.root_dir) if result.output_file else None,
            "evidence": result.evidence,
        }
        outputs.append(item)
        if result.status in {"review", "failed"}:
            review_items.append(item)
            for code in [c for c in str(result.reason or "").split(";") if c]:
                review_reason_counter.update([code.strip()])
        if result.status == "ok":
            warnings = ((result.evidence or {}).get("warning_codes") or [])
            warning_counter.update([str(w) for w in warnings if str(w)])

        if task_id and counts["total"] > 0:
            progress = 20 + int((idx / counts["total"]) * 70)
            queue.update_progress(task_id, min(progress, 95), f"Processed {idx}/{counts['total']} file(s)")

    runtime_status = "SUCCESS" if counts["failed"] == 0 else "PARTIAL_FAILURE"
    if counts["ok"] + counts["review"] == 0 and counts["failed"] > 0:
        runtime_status = "FAILED"

    summary = JobSummary(
        job_id=job_id,
        status=runtime_status,
        started_at=started,
        completed_at=_now_iso(),
        counts=counts,
        outputs={
            "output_zip": os.path.relpath(ctx.output_zip_path, cfg.base_dir),
            "ok_dir": os.path.relpath(ctx.ok_dir, ctx.root_dir),
            "ok_confirmed_dir": os.path.relpath(ctx.ok_confirmed_dir, ctx.root_dir),
            "ok_warnings_dir": os.path.relpath(ctx.ok_warnings_dir, ctx.root_dir),
            "review_dir": os.path.relpath(ctx.review_dir, ctx.root_dir),
            "failed_dir": os.path.relpath(ctx.failed_dir, ctx.root_dir),
            "ok_confirmed": sum(
                1 for x in outputs if x.get("status") == "ok" and not ((x.get("evidence") or {}).get("warning_codes") or [])
            ),
            "ok_warnings": sum(
                1 for x in outputs if x.get("status") == "ok" and ((x.get("evidence") or {}).get("warning_codes") or [])
            ),
            "rename_success_count": sum(1 for x in outputs if x.get("status") == "ok" and x.get("output_file")),
            "review_reason_counts": dict(review_reason_counter),
            "warning_code_counts": dict(warning_counter),
            "items": outputs,
        },
        errors=errors,
    )

    with open(ctx.summary_path, "w", encoding="utf-8") as f:
        json.dump(summary.__dict__, f, indent=2, ensure_ascii=False)

    if review_items:
        with open(ctx.review_items_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "job_id": job_id,
                    "count": len(review_items),
                    "items": review_items,
                    "preview_paths": [item["output_file"] for item in review_items if item.get("output_file")],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

    if job_mode == "golden_verify":
        report = _build_golden_verification_report(
            ctx=ctx,
            outputs=outputs,
            counts=counts,
            errors=errors,
        )
        with open(ctx.golden_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        if not report.get("pass_all"):
            _log_line(
                ctx,
                (
                    "Golden verification failed: "
                    f"pass_counts={report.get('pass_counts')} "
                    f"pass_rename={report.get('pass_rename')}"
                ),
            )
        else:
            _log_line(ctx, "Golden verification passed: operational 43/43 with rename success")

    _zip_results(ctx)
    _log_line(
        ctx,
        (
            f"Invoice job complete: ok={counts['ok']} review={counts['review']} "
            f"skipped={counts['skipped_duplicate']} failed={counts['failed']}"
        ),
    )
    if task_id:
        queue.update_progress(task_id, 100, "Invoice job completed")

    rel_zip = os.path.relpath(ctx.output_zip_path, cfg.base_dir).replace("\\", "/")
    return (
        f"<<SEND_FILE: {rel_zip}>> "
        f"Invoice job complete. Total={counts['total']}, OK={counts['ok']}, Review={counts['review']}, "
        f"Skipped={counts['skipped_duplicate']}, Failed={counts['failed']}."
    )
