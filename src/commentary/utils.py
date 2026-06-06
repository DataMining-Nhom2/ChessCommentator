import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.data.data_pipeline import (
    CAPTURE_WORDS_RE,
    CHECK_WORDS_RE,
    MATE_WORDS_RE,
    PROMOTION_WORDS_RE,
    normalize_text,
)


CHAT_ROLES = ("system", "user", "assistant")
TRAINING_PACKAGES = ("torch", "transformers", "accelerate", "peft")
TRAINING_INSTALL_COMMAND = "python -m pip install -r requirements-train.txt"


def read_jsonl(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if max_records is not None and len(rows) >= max_records:
                break
    return rows


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_packages(packages: Sequence[str], install_command: str) -> None:
    missing = []
    for package in packages:
        print(json.dumps({"dependency_check": package}, ensure_ascii=False), flush=True)
        try:
            __import__(package)
            print(json.dumps({"dependency_ok": package}, ensure_ascii=False), flush=True)
        except ImportError:
            missing.append(package)

    if missing:
        raise SystemExit(
            "Missing dependencies: "
            + ", ".join(missing)
            + f"\nInstall them with: {install_command}"
        )


def validate_chat_rows(rows: Sequence[Dict[str, Any]], path: Optional[Path] = None) -> Dict[str, Any]:
    role_errors = 0
    empty_assistant = 0
    word_lengths: List[int] = []

    for row in rows:
        messages = row.get("messages")
        if not isinstance(messages, list) or [message.get("role") for message in messages] != list(CHAT_ROLES):
            role_errors += 1
            continue

        assistant_text = str(messages[2].get("content", "")).strip()
        if not assistant_text:
            empty_assistant += 1
        word_lengths.append(len(assistant_text.split()))

    report: Dict[str, Any] = {
        "rows": len(rows),
        "role_errors": role_errors,
        "empty_assistant": empty_assistant,
        "min_words": min(word_lengths) if word_lengths else 0,
        "max_words": max(word_lengths) if word_lengths else 0,
    }
    if path is not None:
        report = {"path": str(path), **report}
    return report


def parse_prompt_fields(content: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for part in content.split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def facts_from_user_prompt(content: str) -> Dict[str, Any]:
    fields = parse_prompt_fields(content)
    return {
        "is_capture": fields.get("Capture") == "Yes",
        "is_check": fields.get("Check") == "Yes",
        "is_checkmate": fields.get("Checkmate") == "Yes",
        "is_castling": fields.get("Castling", "No") != "No",
        "is_promotion": fields.get("Promotion", "No") != "No",
        "winner": fields.get("Winner", "None"),
        "move": fields.get("Move", ""),
        "raw": fields,
    }


def text_from_row(row: Dict[str, Any], text_field: str) -> str:
    if text_field == "assistant":
        return str(row["messages"][2].get("content", ""))
    return str(row.get(text_field, ""))


def evaluate_commentary_rows(rows: Sequence[Dict[str, Any]], text_field: str = "assistant") -> Dict[str, Any]:
    errors: Counter = Counter()
    word_lengths: List[int] = []

    for row in rows:
        messages = row.get("messages", [])
        if len(messages) < 3:
            errors["bad_message_shape"] += 1
            continue

        facts = facts_from_user_prompt(str(messages[1].get("content", "")))
        text = normalize_text(text_from_row(row, text_field))
        word_count = len(text.split())
        word_lengths.append(word_count)

        if not text:
            errors["empty_output"] += 1
        if word_count < 5 or word_count > 60:
            errors["word_count_out_of_range"] += 1
        if not facts["is_capture"] and CAPTURE_WORDS_RE.search(text):
            errors["capture_hallucination"] += 1
        if facts["is_castling"] and CAPTURE_WORDS_RE.search(text):
            errors["castling_capture_error"] += 1
        if facts["is_checkmate"] and not MATE_WORDS_RE.search(text):
            errors["checkmate_missing"] += 1
        if facts["is_check"] and not (CHECK_WORDS_RE.search(text) or MATE_WORDS_RE.search(text)):
            errors["check_missing"] += 1
        if facts["is_promotion"] and not PROMOTION_WORDS_RE.search(text):
            errors["promotion_missing"] += 1

    total = len(rows)
    return {
        "rows": total,
        "text_field": text_field,
        "errors": dict(errors),
        "error_rates": {key: value / total for key, value in errors.items()} if total else {},
        "min_words": min(word_lengths) if word_lengths else 0,
        "max_words": max(word_lengths) if word_lengths else 0,
        "avg_words": sum(word_lengths) / len(word_lengths) if word_lengths else 0,
    }
