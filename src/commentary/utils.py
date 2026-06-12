import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from src.data.data_pipeline import (
    CAPTURE_WORDS_RE,
    CASTLE_WORDS_RE,
    CHECK_WORDS_RE,
    MATE_WORDS_RE,
    PROMOTION_WORDS_RE,
    normalize_text,
)


CHAT_ROLES = ("system", "user", "assistant")
TRAINING_PACKAGES = ("torch", "transformers", "accelerate", "peft")
TRAINING_INSTALL_COMMAND = "python -m pip install -r requirements-train.txt"

ADVANTAGE_CLAIM_RE = re.compile(
    r"\b("
    r"advantage|edge|lead|ahead|winning|better|equal|"
    r"slight edge|clear edge|small edge|huge lead|solid lead|"
    r"up a pawn|up the pawn|up material|down material|"
    r"looking good"
    r")\b|"
    r"\bup\s+(?:almost\s+|about\s+|roughly\s+)?(?:a|one|two|three|\d+)\s+pawns?\b",
    re.IGNORECASE,
)
DIRECTIONAL_ADVANTAGE_RE = re.compile(
    r"\b("
    r"(?:white|black)\b.{0,50}\b(?:advantage|edge|ahead|winning|looking good|up\s+(?:a|one|two|three|\d+)\s+pawns?)|"
    r"(?:advantage|edge|ahead|winning|favors?|favours?|looking good)\b.{0,50}\b(?:white|black)|"
    r"slight edge|clear edge|small edge|solid advantage|small advantage|slight advantage|"
    r"up\s+(?:almost\s+|about\s+|roughly\s+)?(?:a|one|two|three|\d+)\s+pawns?"
    r")\b",
    re.IGNORECASE,
)
WHITE_ADVANTAGE_RE = re.compile(
    r"\bwhite\b.{0,60}\b(?:advantage|edge|ahead|winning|looking good|up\s+(?:a|one|two|three|\d+)\s+pawns?)|"
    r"\b(?:advantage|edge|ahead|winning|favors?|favours?|looking good)\b.{0,60}\bwhite\b",
    re.IGNORECASE,
)
BLACK_ADVANTAGE_RE = re.compile(
    r"\bblack\b.{0,60}\b(?:advantage|edge|ahead|winning|looking good|up\s+(?:a|one|two|three|\d+)\s+pawns?)|"
    r"\b(?:advantage|edge|ahead|winning|favors?|favours?|looking good)\b.{0,60}\bblack\b",
    re.IGNORECASE,
)
STRICT_MATE_CLAIM_RE = re.compile(r"\b(checkmate|mate|game over)\b", re.IGNORECASE)
WINNER_CLAIM_RE = re.compile(
    r"\b(white|black)\s+(wins|won|is winning|has won)|\b(wins|won)\s+the\s+game\b",
    re.IGNORECASE,
)
GENERIC_TEMPLATE_RE = re.compile(
    r"\bquiet\s+\w+\s+move\s+that\s+improves\s+the\s+position\b|"
    r"\bkeeps\s+(?:the\s+)?(?:game|position)\s+(?:steady|under\s+control)\b",
    re.IGNORECASE,
)
KINGSIDE_CLAIM_RE = re.compile(r"\bking[-\s]?side\b", re.IGNORECASE)
QUEENSIDE_CLAIM_RE = re.compile(r"\bqueen[-\s]?side\b", re.IGNORECASE)
EXPLICIT_PROMOTION_RE = re.compile(r"\b(promote|promotes|promoted|promoting|promotion)\b", re.IGNORECASE)
PROMOTION_ACTOR_CONFUSION_RE = re.compile(
    r"\b(?:with|by)\s+(?:their|the|a|his|her)?\s*queen\b|\bqueen\s+(?:takes|captures|took|captured)\b",
    re.IGNORECASE,
)
MATERIAL_EXCHANGE_RE = re.compile(
    r"\b("
    r"gave\s+up|give\s+up|gives\s+up|"
    r"lost|loses|lose|"
    r"sacrifice|sacrifices|sacrificed|"
    r"trade|trades|traded|exchange|exchanges|exchanged"
    r")\b.{0,40}\b(pawn|knight|bishop|rook|queen|piece|material)\b",
    re.IGNORECASE,
)
PIECE_WORDS = ("pawn", "knight", "bishop", "rook", "queen")
CAPTURED_PIECE_CLAIM_RE = re.compile(
    r"\b(?:takes?|took|captures?|captured|capturing|wins?|won|grabs?|grabbed)\s+(?:the\s+|a\s+)?"
    r"(pawn|knight|bishop|rook|queen)\b",
    re.IGNORECASE,
)
PAWN_CAPTURE_PIECE_TRADE_RE = re.compile(
    r"\b(?:trade|trades|traded|trading|exchange|exchanges|exchanged)\s+(?:pieces|piece|material)\b",
    re.IGNORECASE,
)
UCI_MOVE_RE = re.compile(r"\b[a-h][1-8][a-h][1-8][qrbn]?\b", re.IGNORECASE)
ANNOUNCED_MOVE_RE = re.compile(
    r"\b(?:plays?|played|starts?\s+with|opens?\s+with)\s+"
    r"("
    r"O-O-O|O-O|0-0-0|0-0|"
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|"
    r"[a-h][1-8][a-h][1-8][qrbn]?"
    r")\b",
    re.IGNORECASE,
)
ENGINE_SUMMARY_RE = re.compile(r"\bengine\s+(?:rates|marks|flags|calls|gives)|\bcpl\b", re.IGNORECASE)
QUALITY_WORDS_RE = re.compile(r"\b(blunder|mistake|inaccuracy|excellent)\b", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


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
        "game_over": fields.get("GameOver", "No") == "Yes",
        "has_advantage_after": "AdvantageAfter" in fields,
        "advantage_after": fields.get("AdvantageAfter"),
        "classification": fields.get("Classification", ""),
        "has_cpl": "CPL" in fields,
        "cpl": fields.get("CPL"),
        "best_move_uci": fields.get("BestMoveUCI"),
        "captured_piece": fields.get("CapturedPiece"),
        "from_square": fields.get("From"),
        "to_square": fields.get("To"),
        "move": fields.get("Move", ""),
        "raw": fields,
    }


def text_from_row(row: Dict[str, Any], text_field: str) -> str:
    if text_field == "assistant":
        return str(row["messages"][2].get("content", ""))
    return str(row.get(text_field, ""))


def fallback_commentary(facts: Dict[str, Any]) -> str:
    fields = facts.get("raw", {})
    player = fields.get("Player", "The player")
    move = fields.get("Move") or facts.get("move", "the move")
    move = re.sub(r"^\d+\.(?:\.\.)?\s*", "", str(move)).strip()
    piece = fields.get("Piece", "piece").lower()

    if facts["is_checkmate"]:
        return f"{player} plays {move}, delivering checkmate. The game is over."
    if facts["is_check"]:
        return f"{player} plays {move}, giving check and forcing a response."
    if facts["is_castling"]:
        side = fields.get("Castling", "").lower()
        side_text = f" {side}" if side and side != "yes" else ""
        return f"{player} castles{side_text}, bringing the king to safety."
    if facts["is_promotion"]:
        return f"{player} plays {move}, promoting the pawn and changing the material balance."
    if facts["is_capture"]:
        captured_piece = str(facts.get("captured_piece") or "").strip()
        if captured_piece:
            return f"{player} plays {move}, capturing the {captured_piece.lower()} and changing the position."
        return f"{player} plays {move}, making a capture and changing the position."
    return f"{player} plays {move}, a quiet {piece} move that keeps the position developing."


def engine_summary(facts: Dict[str, Any]) -> str:
    if not facts.get("has_cpl"):
        return ""

    classification = str(facts.get("classification") or "").strip().lower()
    cpl = str(facts.get("cpl") or "").strip()
    cpl_text = f" with {cpl} CPL" if cpl else ""

    if "blunder" in classification:
        return f" Engine flags it as a blunder{cpl_text}."
    if "mistake" in classification:
        return f" Engine marks it as a mistake{cpl_text}."
    if "inaccuracy" in classification:
        return f" Engine marks it as an inaccuracy{cpl_text}."
    if "excellent" in classification:
        return f" Engine rates it excellent{cpl_text}."
    if "good" in classification:
        return f" Engine rates it good{cpl_text}."
    if cpl:
        return f" Engine gives it {cpl} CPL."
    return ""


def append_engine_summary(facts: Dict[str, Any], text: str) -> str:
    text = normalize_text(text)
    if not text or not facts.get("has_cpl") or ENGINE_SUMMARY_RE.search(text):
        return text

    summary = engine_summary(facts)
    if not summary:
        return text

    combined = normalize_text(text + summary)
    if len(combined.split()) <= 60:
        return combined
    return text


def sentence_chunks(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    return [chunk.strip() for chunk in SENTENCE_SPLIT_RE.split(text) if chunk.strip()]


def clean_move_token(move: str) -> str:
    move = re.sub(r"^\d+\.(?:\.\.)?\s*", "", str(move or "").strip())
    return move.strip().strip(".,;:!?")


def expected_move_tokens(facts: Dict[str, Any]) -> Set[str]:
    fields = facts.get("raw", {})
    san = clean_move_token(str(facts.get("move") or fields.get("Move") or ""))
    tokens = {san, san.rstrip("+#")}
    from_square = str(facts.get("from_square") or fields.get("From") or "")
    to_square = str(facts.get("to_square") or fields.get("To") or "")
    if from_square and to_square:
        tokens.add((from_square + to_square).lower())
    normalized = set()
    for token in tokens:
        if not token:
            continue
        normalized.add(token.lower())
        normalized.add(token.lower().replace("0", "o"))
    return normalized


def has_wrong_move_mention(facts: Dict[str, Any], text: str) -> bool:
    expected = expected_move_tokens(facts)
    if not expected:
        return False

    for match in UCI_MOVE_RE.finditer(text):
        token = match.group(0).lower()
        if token in expected:
            continue
        window = text[max(0, match.start() - 24) : match.end() + 24].lower()
        if "best" in window and token == str(facts.get("best_move_uci") or "").lower():
            continue
        return True

    for match in ANNOUNCED_MOVE_RE.finditer(text):
        token = clean_move_token(match.group(1)).lower().replace("0", "o")
        if token not in expected:
            return True
    return False


def has_wrong_captured_piece(facts: Dict[str, Any], text: str) -> bool:
    if not facts.get("is_capture"):
        return False
    captured_piece = str(facts.get("captured_piece") or "").strip().lower()
    if not captured_piece:
        return False

    for match in CAPTURED_PIECE_CLAIM_RE.finditer(text):
        claimed_piece = match.group(1).lower()
        if claimed_piece != captured_piece:
            return True

    return captured_piece == "pawn" and bool(PAWN_CAPTURE_PIECE_TRADE_RE.search(text))


def has_move_reference(facts: Dict[str, Any], text: str) -> bool:
    if not normalize_text(text):
        return False

    expected = expected_move_tokens(facts)
    lowered = text.lower()
    for token in expected:
        if token and re.search(rf"\b{re.escape(token)}\b", lowered, re.IGNORECASE):
            return True

    if facts.get("is_castling"):
        return bool(CASTLE_WORDS_RE.search(text))
    if facts.get("is_capture"):
        return bool(CAPTURE_WORDS_RE.search(text))

    to_square = str(facts.get("to_square") or "").lower()
    piece = str(facts.get("raw", {}).get("Piece") or "").lower()
    if to_square and re.search(rf"\b(?:to|on|at|towards?)\s+{re.escape(to_square)}\b", lowered):
        if not piece or piece == "pawn" or piece in lowered:
            return True
    return False


def has_advantage_contradiction(facts: Dict[str, Any], text: str) -> bool:
    advantage_after = str(facts.get("advantage_after") or "").strip().lower()
    if not advantage_after:
        return False
    if advantage_after == "equal":
        return bool(DIRECTIONAL_ADVANTAGE_RE.search(text))
    if advantage_after.startswith("white"):
        return bool(BLACK_ADVANTAGE_RE.search(text))
    if advantage_after.startswith("black"):
        return bool(WHITE_ADVANTAGE_RE.search(text))
    return False


def has_wrong_castle_side(facts: Dict[str, Any], text: str) -> bool:
    if not facts.get("is_castling"):
        return False

    side = str(facts.get("raw", {}).get("Castling", "")).lower()
    if side == "queenside" and KINGSIDE_CLAIM_RE.search(text):
        return True
    if side == "kingside" and QUEENSIDE_CLAIM_RE.search(text):
        return True
    return False


def sanitize_commentary_text(row: Dict[str, Any], text: str) -> str:
    messages = row.get("messages", [])
    if len(messages) < 2:
        return normalize_text(text)

    facts = facts_from_user_prompt(str(messages[1].get("content", "")))
    chunks = sentence_chunks(text)
    if not chunks:
        return append_engine_summary(facts, fallback_commentary(facts))

    kept_chunks: List[str] = []
    for chunk in chunks:
        if has_wrong_move_mention(facts, chunk):
            continue
        if has_wrong_captured_piece(facts, chunk):
            continue
        if has_advantage_contradiction(facts, chunk):
            continue
        if (not facts["is_capture"] or facts["is_castling"]) and CAPTURE_WORDS_RE.search(chunk):
            continue
        if not facts["is_capture"] and MATERIAL_EXCHANGE_RE.search(chunk):
            continue
        if has_wrong_castle_side(facts, chunk):
            continue
        if facts["is_promotion"] and PROMOTION_ACTOR_CONFUSION_RE.search(chunk):
            continue
        if not facts.get("has_advantage_after") and ADVANTAGE_CLAIM_RE.search(chunk):
            continue
        if not facts["is_check"] and not facts["is_checkmate"] and CHECK_WORDS_RE.search(chunk):
            continue
        if not facts["is_checkmate"] and STRICT_MATE_CLAIM_RE.search(chunk):
            continue
        if str(facts.get("winner", "None")) == "None" and not facts.get("game_over") and WINNER_CLAIM_RE.search(chunk):
            continue
        kept_chunks.append(chunk)

    cleaned = normalize_text(" ".join(kept_chunks))
    if len(cleaned.split()) < 5:
        return append_engine_summary(facts, fallback_commentary(facts))
    if has_wrong_move_mention(facts, cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if has_wrong_captured_piece(facts, cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if has_advantage_contradiction(facts, cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if not has_move_reference(facts, cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if facts["is_castling"] and not CASTLE_WORDS_RE.search(cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if facts["is_promotion"] and not EXPLICIT_PROMOTION_RE.search(cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if facts["is_checkmate"] and not MATE_WORDS_RE.search(cleaned):
        return append_engine_summary(facts, fallback_commentary(facts))
    if facts["is_check"] and not (CHECK_WORDS_RE.search(cleaned) or MATE_WORDS_RE.search(cleaned)):
        return append_engine_summary(facts, fallback_commentary(facts))
    return append_engine_summary(facts, cleaned)


def token_set(text: str) -> Set[str]:
    return set(re.findall(r"[a-zA-Z0-9+#=O-]+", normalize_text(text).lower()))


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def commentary_errors(facts: Dict[str, Any], text: str) -> List[str]:
    errors: List[str] = []
    word_count = len(text.split())

    if not text:
        errors.append("empty_output")
    if word_count < 5 or word_count > 60:
        errors.append("word_count_out_of_range")
    if has_wrong_move_mention(facts, text):
        errors.append("wrong_move_mention")
    if has_wrong_captured_piece(facts, text):
        errors.append("captured_piece_mismatch")
    if has_advantage_contradiction(facts, text):
        errors.append("advantage_contradiction")
    if not has_move_reference(facts, text):
        errors.append("missing_move_reference")
    if not facts["is_capture"] and CAPTURE_WORDS_RE.search(text):
        errors.append("capture_hallucination")
    if not facts["is_capture"] and MATERIAL_EXCHANGE_RE.search(text):
        errors.append("material_exchange_hallucination")
    if facts["is_castling"] and CAPTURE_WORDS_RE.search(text):
        errors.append("castling_capture_error")
    if facts["is_castling"] and not CASTLE_WORDS_RE.search(text):
        errors.append("castling_missing")
    if has_wrong_castle_side(facts, text):
        errors.append("castle_side_mismatch")
    if facts["is_checkmate"] and not MATE_WORDS_RE.search(text):
        errors.append("checkmate_missing")
    if facts["is_check"] and not (CHECK_WORDS_RE.search(text) or MATE_WORDS_RE.search(text)):
        errors.append("check_missing")
    if facts["is_promotion"] and not PROMOTION_WORDS_RE.search(text):
        errors.append("promotion_missing")
    if facts["is_promotion"] and not EXPLICIT_PROMOTION_RE.search(text):
        errors.append("promotion_action_missing")
    if facts["is_promotion"] and PROMOTION_ACTOR_CONFUSION_RE.search(text):
        errors.append("promotion_actor_confusion")
    return errors


def commentary_warnings(row: Dict[str, Any], facts: Dict[str, Any], text: str, text_field: str) -> List[str]:
    warnings: List[str] = []
    messages = row.get("messages", [])
    reference = normalize_text(str(row.get("reference", "")))
    if not reference and text_field == "assistant" and len(messages) >= 3:
        reference = normalize_text(str(messages[2].get("content", "")))

    if not facts["is_check"] and not facts["is_checkmate"] and CHECK_WORDS_RE.search(text):
        warnings.append("check_claim_without_prompt")
    if not facts["is_checkmate"] and STRICT_MATE_CLAIM_RE.search(text):
        warnings.append("mate_claim_without_prompt")
    if str(facts.get("winner", "None")) == "None" and not facts.get("game_over") and WINNER_CLAIM_RE.search(text):
        warnings.append("winner_claim_without_prompt")
    if not facts.get("has_advantage_after") and ADVANTAGE_CLAIM_RE.search(text):
        warnings.append("advantage_claim_without_prompt")
    if not facts.get("has_cpl") and ENGINE_SUMMARY_RE.search(text):
        warnings.append("engine_quality_claim_without_prompt")
    if facts.get("has_cpl"):
        classification = str(facts.get("classification") or "").lower()
        important_quality = any(token in classification for token in ("blunder", "mistake", "inaccuracy", "excellent"))
        if important_quality and not (ENGINE_SUMMARY_RE.search(text) or QUALITY_WORDS_RE.search(text)):
            warnings.append("engine_quality_missing")
    if GENERIC_TEMPLATE_RE.search(text):
        warnings.append("generic_template_phrase")
    if text_field != "assistant" and reference:
        if normalize_text(text).lower() == reference.lower():
            warnings.append("exact_reference_copy")
        elif jaccard_similarity(text, reference) >= 0.82:
            warnings.append("high_reference_overlap")
    return warnings


def commentary_issues(row: Dict[str, Any], text: str, text_field: str = "assistant") -> Dict[str, Any]:
    messages = row.get("messages", [])
    if len(messages) < 3:
        return {"errors": ["bad_message_shape"], "warnings": []}

    facts = facts_from_user_prompt(str(messages[1].get("content", "")))
    normalized = normalize_text(text)
    return {
        "errors": commentary_errors(facts, normalized),
        "warnings": commentary_warnings(row, facts, normalized, text_field),
    }


def evaluate_commentary_rows(rows: Sequence[Dict[str, Any]], text_field: str = "assistant") -> Dict[str, Any]:
    errors: Counter = Counter()
    warnings: Counter = Counter()
    word_lengths: List[int] = []

    for row in rows:
        messages = row.get("messages", [])
        if len(messages) < 3:
            errors["bad_message_shape"] += 1
            continue

        text = normalize_text(text_from_row(row, text_field))
        word_lengths.append(len(text.split()))
        issues = commentary_issues(row, text, text_field)
        errors.update(issues["errors"])
        warnings.update(issues["warnings"])

    total = len(rows)
    return {
        "rows": total,
        "text_field": text_field,
        "errors": dict(errors),
        "error_rates": {key: value / total for key, value in errors.items()} if total else {},
        "warnings": dict(warnings),
        "warning_rates": {key: value / total for key, value in warnings.items()} if total else {},
        "min_words": min(word_lengths) if word_lengths else 0,
        "max_words": max(word_lengths) if word_lengths else 0,
        "avg_words": sum(word_lengths) / len(word_lengths) if word_lengths else 0,
    }
