import argparse
import csv
import json
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SYSTEM_PROMPT = (
    "You are a real-time chess commentator. Be concise, natural, and factual. "
    "Do not invent captures, checks, checkmates, winners, or advantages."
)

EXPECTED_INPUT_KEYS = {
    "MoveNumber",
    "Move",
    "CurrentPlayer",
    "Phase",
    "Classification",
    "MateThreat",
    "Captured",
    "MoveType",
    "Check",
    "Checkmate",
    "GameOver",
    "GameOverReason",
    "Winner",
}

PIECE_NAMES = {
    "K": "King",
    "Q": "Queen",
    "R": "Rook",
    "B": "Bishop",
    "N": "Knight",
    "P": "Pawn",
}

PROMOTION_RE = re.compile(r"=([QRBN])")
CAPTURE_WORDS_RE = re.compile(
    r"\b("
    r"take|takes|taking|taken|took|"
    r"captures|capture|captured|capturing|"
    r"recapture|recaptures|recaptured|recapturing|"
    r"grab|grabs|grabbed|grabbing|"
    r"snag|snags|snagged|snagging|"
    r"snatch|snatches|snatched|snatching|"
    r"snaps?\s+off|snapped\s+off|snapping\s+off|"
    r"picks?\s+off|picked\s+off|picking\s+off|"
    r"wins\s+(?:a|the)?\s*(?:pawn|knight|bishop|rook|queen|piece)"
    r")\b",
    re.IGNORECASE,
)
CHECK_WORDS_RE = re.compile(r"\b(check|checks|checked|checking)\b", re.IGNORECASE)
MATE_WORDS_RE = re.compile(r"\b(checkmate|mate|game over|wins|won)\b", re.IGNORECASE)
CASTLE_WORDS_RE = re.compile(
    r"\b(castle|castles|castled|castling|king safety|king to safety)\b",
    re.IGNORECASE,
)
PROMOTION_WORDS_RE = re.compile(
    r"\b(promote|promotes|promoted|promoting|promotion|queen|rook|bishop|knight)\b",
    re.IGNORECASE,
)
MISTAKE_WORDS_RE = re.compile(
    r"\b(blunder|mistake|error|slip|bad move|serious miss|inaccuracy)\b",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def yes_no(value: Any) -> bool:
    return str(value).strip().lower() == "yes"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_input_fields(input_text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for part in (input_text or "").split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def load_raw_dataset(path: Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}")
    if max_records is not None:
        data = data[:max_records]
    return data


def infer_piece_from_san(san: str) -> str:
    if san.startswith(("O-O", "0-0")):
        return "King"
    first = san[:1]
    if first in PIECE_NAMES and first != "P":
        return PIECE_NAMES[first]
    return "Pawn"


def infer_san_features(san: str) -> Dict[str, Any]:
    clean_san = normalize_text(san)
    is_castling = clean_san.startswith(("O-O", "0-0"))
    castle_side = None
    if clean_san.startswith(("O-O-O", "0-0-0")):
        castle_side = "queenside"
    elif is_castling:
        castle_side = "kingside"

    promotion_match = PROMOTION_RE.search(clean_san)
    is_capture = "x" in clean_san
    is_checkmate = "#" in clean_san
    is_check = "+" in clean_san or is_checkmate
    is_promotion = promotion_match is not None

    if is_castling:
        primary_move_type = f"castle_{castle_side}"
    elif is_promotion and is_capture:
        primary_move_type = "promotion_capture"
    elif is_promotion:
        primary_move_type = "promotion"
    elif is_capture:
        primary_move_type = "capture"
    elif is_check:
        primary_move_type = "check"
    else:
        primary_move_type = "quiet"

    return {
        "piece": infer_piece_from_san(clean_san),
        "is_capture": is_capture,
        "is_check": is_check,
        "is_checkmate": is_checkmate,
        "is_castling": is_castling,
        "castle_side": castle_side,
        "is_promotion": is_promotion,
        "promotion_piece": PIECE_NAMES.get(promotion_match.group(1)) if promotion_match else None,
        "is_quiet": not any([is_capture, is_check, is_castling, is_promotion]),
        "primary_move_type": primary_move_type,
    }


def build_base_records(raw_rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    parsed_rows = [parse_input_fields(str(row.get("input", ""))) for row in raw_rows]
    records: List[Dict[str, Any]] = []
    game_id_num = 0
    prev_ply: Optional[int] = None
    segment_starts: List[int] = []

    for raw_index, (raw, fields) in enumerate(zip(raw_rows, parsed_rows)):
        ply = safe_int(fields.get("MoveNumber"))
        if raw_index == 0 or (prev_ply is not None and ply <= prev_ply):
            if raw_index != 0:
                game_id_num += 1
            segment_starts.append(raw_index)

        current_player = fields.get("CurrentPlayer", "")
        san = fields.get("Move", "")
        san_features = infer_san_features(san)
        expected_player = "White" if ply % 2 == 1 else "Black"
        validation_errors: List[str] = []

        missing_keys = sorted(EXPECTED_INPUT_KEYS - set(fields))
        if missing_keys:
            validation_errors.append("missing_input_keys:" + ",".join(missing_keys))
        if current_player and current_player != expected_player:
            validation_errors.append(
                f"current_player_parity_mismatch:expected_{expected_player}"
            )
        if yes_no(fields.get("Check")) != san_features["is_check"]:
            validation_errors.append("raw_check_mismatch_san")
        if yes_no(fields.get("Checkmate")) != san_features["is_checkmate"]:
            validation_errors.append("raw_checkmate_mismatch_san")

        game_id = f"game_{game_id_num:06d}"
        record = {
            "record_id": f"{game_id}_ply_{ply:03d}_{raw_index:06d}",
            "raw_index": raw_index,
            "game_id": game_id,
            "ply": ply,
            "fullmove_number": (ply + 1) // 2,
            "current_player": current_player,
            "san": san,
            "phase_raw": fields.get("Phase"),
            "classification_raw": fields.get("Classification"),
            "mate_threat_raw": fields.get("MateThreat"),
            "game_over_raw": fields.get("GameOver"),
            "game_over_reason_raw": fields.get("GameOverReason"),
            "winner_raw": fields.get("Winner"),
            "raw_move_type": fields.get("MoveType"),
            "raw_captured": fields.get("Captured"),
            "raw_output": raw.get("output", ""),
            "raw_instruction": raw.get("instruction", ""),
            "feature_source": "san_only",
            "replay_status": "not_attempted",
            "validation_errors": validation_errors,
            "uci": None,
            "from_square": None,
            "to_square": None,
            "captured_piece": None,
            "is_en_passant": False,
            "fen_before": None,
            "fen_after": None,
            "eval_before_cp": None,
            "eval_after_cp": None,
            "best_move_uci": None,
            "cpl": None,
            "eval_swing": None,
            "advantage_after": None,
            "classification_stockfish": None,
            "is_partial_segment": False,
        }
        record.update(san_features)
        records.append(record)
        prev_ply = ply

    segment_starts.append(len(records))
    partial_segments = 0
    for idx in range(len(segment_starts) - 1):
        start, end = segment_starts[idx], segment_starts[idx + 1]
        if start >= end:
            continue
        last = records[end - 1]
        is_partial = (
            str(last.get("game_over_raw")).lower() == "no"
            and str(last.get("game_over_reason_raw")).lower() == "game continues"
        )
        if is_partial:
            partial_segments += 1
            for record in records[start:end]:
                record["is_partial_segment"] = True

    summary = {
        "inferred_segments": max(0, len(segment_starts) - 1),
        "partial_segments": partial_segments,
    }
    return records, summary


def piece_name_from_chess(piece: Any) -> Optional[str]:
    if piece is None:
        return None
    return PIECE_NAMES.get(piece.symbol().upper())


def replay_with_python_chess(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        import chess  # type: ignore
    except ImportError:
        for record in records:
            record["replay_status"] = "skipped_missing_dependency"
        return {
            "attempted": False,
            "available": False,
            "success_rows": 0,
            "failed_rows": 0,
            "skipped_rows": len(records),
            "pass_rate": 0.0,
            "message": "python-chess is not installed; install requirements.txt to enable replay.",
        }

    success_rows = 0
    failed_rows = 0
    skipped_rows = 0
    failures: List[Dict[str, Any]] = []

    by_game: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_game[record["game_id"]].append(record)

    for game_id, game_records in by_game.items():
        board = chess.Board()
        game_failed = False
        for record in game_records:
            if game_failed:
                record["replay_status"] = "skipped_after_replay_failure"
                skipped_rows += 1
                continue

            san = record["san"]
            try:
                fen_before = board.fen()
                move = board.parse_san(san)
                moved_piece = board.piece_at(move.from_square)
                captured_piece = None
                is_en_passant = board.is_en_passant(move)
                is_capture = board.is_capture(move)
                if is_capture:
                    if is_en_passant:
                        captured_piece = "Pawn"
                    else:
                        captured_piece = piece_name_from_chess(board.piece_at(move.to_square))

                record.update(
                    {
                        "feature_source": "replay",
                        "replay_status": "success",
                        "fen_before": fen_before,
                        "uci": move.uci(),
                        "piece": piece_name_from_chess(moved_piece),
                        "from_square": chess.square_name(move.from_square),
                        "to_square": chess.square_name(move.to_square),
                        "is_capture": is_capture,
                        "captured_piece": captured_piece,
                        "is_castling": board.is_castling(move),
                        "castle_side": (
                            "kingside"
                            if board.is_kingside_castling(move)
                            else "queenside"
                            if board.is_queenside_castling(move)
                            else None
                        ),
                        "is_promotion": move.promotion is not None,
                        "promotion_piece": PIECE_NAMES.get(
                            chess.piece_symbol(move.promotion).upper()
                        )
                        if move.promotion
                        else None,
                        "is_en_passant": is_en_passant,
                    }
                )
                board.push(move)
                record["fen_after"] = board.fen()
                record["is_check"] = board.is_check()
                record["is_checkmate"] = board.is_checkmate()
                record["is_quiet"] = not any(
                    [
                        record["is_capture"],
                        record["is_check"],
                        record["is_castling"],
                        record["is_promotion"],
                    ]
                )
                success_rows += 1
            except Exception as exc:  # pragma: no cover - exception type depends on python-chess
                failed_rows += 1
                game_failed = True
                error = f"replay_failed:{type(exc).__name__}:{exc}"
                record["replay_status"] = "failed"
                record["validation_errors"].append(error)
                failures.append(
                    {
                        "game_id": game_id,
                        "raw_index": record["raw_index"],
                        "ply": record["ply"],
                        "san": san,
                        "fen_before": board.fen(),
                        "error": error,
                    }
                )

    attempted_rows = success_rows + failed_rows
    pass_rate = success_rows / attempted_rows if attempted_rows else 0.0
    return {
        "attempted": True,
        "available": True,
        "success_rows": success_rows,
        "failed_rows": failed_rows,
        "skipped_rows": skipped_rows,
        "pass_rate": pass_rows_ratio(success_rows, failed_rows),
        "failures": failures,
    }


def pass_rows_ratio(success_rows: int, failed_rows: int) -> float:
    attempted = success_rows + failed_rows
    return success_rows / attempted if attempted else 0.0


def score_to_cp(score: Any) -> Optional[int]:
    if score is None:
        return None
    return score.white().score(mate_score=10000)


def compute_cpl(eval_before_cp: Optional[int], eval_after_cp: Optional[int], current_player: str) -> Optional[int]:
    if eval_before_cp is None or eval_after_cp is None:
        return None
    player_before = eval_before_cp if current_player == "White" else -eval_before_cp
    player_after = eval_after_cp if current_player == "White" else -eval_after_cp
    return max(0, player_before - player_after)


def classify_stockfish(cpl: Optional[int]) -> Optional[str]:
    if cpl is None:
        return None
    if cpl <= 10:
        return "excellent"
    if cpl <= 20:
        return "good"
    if cpl <= 50:
        return "inaccuracy"
    if cpl <= 150:
        return "mistake"
    return "blunder"


def advantage_label(eval_cp: Optional[int]) -> Optional[str]:
    if eval_cp is None:
        return None
    if eval_cp >= 600:
        return "White winning"
    if eval_cp >= 250:
        return "White clear advantage"
    if eval_cp >= 80:
        return "White slight advantage"
    if eval_cp <= -600:
        return "Black winning"
    if eval_cp <= -250:
        return "Black clear advantage"
    if eval_cp <= -80:
        return "Black slight advantage"
    return "Equal"


def enrich_with_stockfish(
    records: List[Dict[str, Any]],
    engine_path: Optional[str],
    depth: int,
    min_replay_pass_rate: float,
    replay_summary: Dict[str, Any],
) -> Dict[str, Any]:
    if not engine_path:
        return {"attempted": False, "message": "No Stockfish path provided."}
    if not replay_summary.get("available"):
        return {"attempted": False, "message": "Replay is unavailable; skipping Stockfish."}
    if replay_summary.get("pass_rate", 0.0) < min_replay_pass_rate:
        return {
            "attempted": False,
            "message": (
                f"Replay pass rate {replay_summary.get('pass_rate', 0.0):.4f} "
                f"is below threshold {min_replay_pass_rate:.4f}."
            ),
        }

    try:
        import chess  # type: ignore
        import chess.engine  # type: ignore
    except ImportError:
        return {"attempted": False, "message": "python-chess engine support unavailable."}

    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    enriched = 0
    failed = 0
    try:
        for record in records:
            if record.get("replay_status") != "success":
                continue
            try:
                board_before = chess.Board(record["fen_before"])
                board_after = chess.Board(record["fen_after"])
                limit = chess.engine.Limit(depth=depth)
                before_info = engine.analyse(board_before, limit)
                after_info = engine.analyse(board_after, limit)
                eval_before = score_to_cp(before_info.get("score"))
                eval_after = score_to_cp(after_info.get("score"))
                best_move = None
                if before_info.get("pv"):
                    best_move = before_info["pv"][0].uci()
                cpl = compute_cpl(eval_before, eval_after, record["current_player"])
                record.update(
                    {
                        "feature_source": "stockfish",
                        "eval_before_cp": eval_before,
                        "eval_after_cp": eval_after,
                        "best_move_uci": best_move,
                        "cpl": cpl,
                        "eval_swing": None
                        if eval_before is None or eval_after is None
                        else eval_after - eval_before,
                        "advantage_after": advantage_label(eval_after),
                        "classification_stockfish": classify_stockfish(cpl),
                    }
                )
                enriched += 1
            except Exception as exc:  # pragma: no cover - engine failures are environment-specific
                failed += 1
                record["validation_errors"].append(
                    f"stockfish_failed:{type(exc).__name__}:{exc}"
                )
    finally:
        engine.quit()

    return {"attempted": True, "enriched_rows": enriched, "failed_rows": failed, "depth": depth}


def has_winner_mismatch(record: Dict[str, Any], text: str) -> bool:
    winner = str(record.get("winner_raw") or "").strip().lower()
    lowered = text.lower()
    if winner == "white":
        return bool(re.search(r"\bblack\s+(wins|won|checkmates)\b", lowered))
    if winner == "black":
        return bool(re.search(r"\bwhite\s+(wins|won|checkmates)\b", lowered))
    if winner in {"none", "", "draw"}:
        return False
    return False


def validate_commentary(record: Dict[str, Any], text: str) -> List[str]:
    text = normalize_text(text)
    errors: List[str] = []
    word_count = len(text.split())

    if not text:
        errors.append("empty_commentary")
    if word_count < 5 or word_count > 60:
        errors.append("word_count_out_of_range")
    if not record.get("is_capture") and CAPTURE_WORDS_RE.search(text):
        errors.append("capture_hallucination")
    if record.get("is_castling") and CAPTURE_WORDS_RE.search(text):
        errors.append("castling_capture_error")
    if record.get("is_castling") and not CASTLE_WORDS_RE.search(text):
        errors.append("castling_missing")
    if record.get("is_checkmate") and not MATE_WORDS_RE.search(text):
        errors.append("checkmate_missing")
    if record.get("is_check") and not (CHECK_WORDS_RE.search(text) or MATE_WORDS_RE.search(text)):
        errors.append("check_missing")
    if record.get("is_promotion") and not PROMOTION_WORDS_RE.search(text):
        errors.append("promotion_missing")
    if str(record.get("classification_raw") or "").lower().startswith("blunder") and not MISTAKE_WORDS_RE.search(text):
        errors.append("blunder_missing")
    if has_winner_mismatch(record, text):
        errors.append("winner_mismatch")
    return errors


def classification_phrase(record: Dict[str, Any]) -> str:
    classification = str(
        record.get("classification_stockfish") or record.get("classification_raw") or ""
    ).lower()
    if "blunder" in classification:
        return " It is a serious blunder and gives the opponent a clear chance."
    if "mistake" in classification:
        return " It is a mistake and the opponent gets useful chances."
    if "inaccuracy" in classification:
        return " It is a small inaccuracy, but the game is still playable."
    if "excellent" in classification:
        return " It is an excellent move and keeps the position under control."
    if "good" in classification:
        return " It is a good move and keeps the game steady."
    return " It keeps the position playable."


def build_deterministic_commentary(record: Dict[str, Any]) -> str:
    side = record["current_player"]
    san = record["san"]
    winner = record.get("winner_raw")
    castle_side = record.get("castle_side")

    if record.get("is_checkmate"):
        game_winner = winner if winner and winner != "None" else side
        text = f"{side} plays {san}, checkmate. The game is over and {game_winner} wins."
    elif record.get("is_castling"):
        side_text = f" {castle_side}" if castle_side else ""
        text = f"{side} castles{side_text} with {san}, moving the king to safety."
    elif record.get("is_promotion") and record.get("is_capture") and record.get("is_check"):
        piece = record.get("promotion_piece") or "a new piece"
        text = f"{side} plays {san}, capturing while promoting to a {piece} and giving check."
    elif record.get("is_promotion") and record.get("is_capture"):
        piece = record.get("promotion_piece") or "a new piece"
        text = f"{side} plays {san}, capturing and promoting to a {piece}."
    elif record.get("is_promotion") and record.get("is_check"):
        piece = record.get("promotion_piece") or "a new piece"
        text = f"{side} plays {san}, promoting to a {piece} with check."
    elif record.get("is_promotion"):
        piece = record.get("promotion_piece") or "a new piece"
        text = f"{side} plays {san}, promoting to a {piece}."
    elif record.get("is_capture") and record.get("is_check"):
        captured = record.get("captured_piece")
        target = f" the {captured.lower()}" if captured else ""
        text = f"{side} plays {san}, capturing{target} with check."
    elif record.get("is_capture"):
        captured = record.get("captured_piece")
        target = f" the {captured.lower()}" if captured else ""
        text = f"{side} plays {san}, capturing{target} cleanly."
    elif record.get("is_check"):
        text = f"{side} plays {san}, giving check and forcing a response."
    else:
        piece = record.get("piece") or infer_piece_from_san(san)
        text = f"{side} plays {san}, a quiet {piece.lower()} move that improves the position."

    text += classification_phrase(record)

    if record.get("advantage_after"):
        text += f" The evaluation says {record['advantage_after']}."
    elif record.get("game_over_raw") == "Yes" and winner and winner not in {"None", "Draw"}:
        text += f" {winner} wins the game."
    elif winner == "Draw":
        text += " The game is drawn."

    return normalize_text(text)


def rewrite_commentary(
    records: List[Dict[str, Any]],
    require_replay_success: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    cleaned_rows: List[Dict[str, Any]] = []
    quarantine_rows: List[Dict[str, Any]] = []
    source_counts: Counter = Counter()
    old_error_counts: Counter = Counter()
    final_error_counts: Counter = Counter()

    for record in records:
        if require_replay_success and record.get("replay_status") != "success":
            error = f"replay_not_success:{record.get('replay_status')}"
            final_error_counts[error] += 1
            row = dict(record)
            row.update(
                {
                    "clean_commentary_en": None,
                    "rewrite_source": "quarantine_replay_status",
                    "old_commentary_errors": [],
                    "final_commentary_errors": [error],
                }
            )
            quarantine_rows.append(row)
            continue

        old_output = normalize_text(record.get("raw_output", ""))
        old_errors = validate_commentary(record, old_output)
        for error in old_errors:
            old_error_counts[error] += 1

        if not old_errors:
            clean_text = old_output
            rewrite_source = "raw"
        else:
            clean_text = build_deterministic_commentary(record)
            rewrite_source = "deterministic_draft"

        final_errors = validate_commentary(record, clean_text)
        for error in final_errors:
            final_error_counts[error] += 1

        row = dict(record)
        row.update(
            {
                "clean_commentary_en": clean_text,
                "rewrite_source": rewrite_source,
                "old_commentary_errors": old_errors,
                "final_commentary_errors": final_errors,
            }
        )

        if final_errors:
            quarantine_rows.append(row)
        else:
            cleaned_rows.append(row)
            source_counts[rewrite_source] += 1

    summary = {
        "clean_rows": len(cleaned_rows),
        "quarantine_rows": len(quarantine_rows),
        "rewrite_source_counts": dict(source_counts),
        "old_error_counts": dict(old_error_counts),
        "final_error_counts": dict(final_error_counts),
    }
    return cleaned_rows, quarantine_rows, summary


def build_audit_report(
    raw_rows: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    segment_summary: Dict[str, Any],
    replay_summary: Dict[str, Any],
    stockfish_summary: Dict[str, Any],
    rewrite_summary: Dict[str, Any],
    split_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    inputs = [parse_input_fields(str(row.get("input", ""))) for row in raw_rows]
    outputs = [str(row.get("output", "")) for row in raw_rows]
    moves = [row.get("Move", "") for row in inputs]

    raw_move_type_counts = Counter(row.get("MoveType", "") for row in inputs)
    raw_captured_counts = Counter(row.get("Captured", "") for row in inputs)
    phase_counts = Counter(row.get("Phase", "") for row in inputs)
    classification_counts = Counter(row.get("Classification", "") for row in inputs)
    player_counts = Counter(row.get("CurrentPlayer", "") for row in inputs)
    input_key_counts = Counter(key for row in inputs for key in row)
    exact_rows = Counter(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in raw_rows)
    input_counts = Counter(str(row.get("input", "")) for row in raw_rows)
    outputs_by_input: Dict[str, set] = defaultdict(set)
    for row in raw_rows:
        outputs_by_input[str(row.get("input", ""))].add(str(row.get("output", "")))

    noncapture_output_capture_words = sum(
        "x" not in move and bool(CAPTURE_WORDS_RE.search(output))
        for move, output in zip(moves, outputs)
    )
    capture_output_missing_words = sum(
        "x" in move and not bool(CAPTURE_WORDS_RE.search(output))
        for move, output in zip(moves, outputs)
    )

    audit = {
        "samples": len(raw_rows),
        "top_level_keys": sorted(raw_rows[0].keys()) if raw_rows else [],
        "input_keys": dict(input_key_counts),
        "players": dict(player_counts),
        "phase": dict(phase_counts),
        "classification": dict(classification_counts),
        "raw_move_type": dict(raw_move_type_counts),
        "raw_captured_top": dict(raw_captured_counts.most_common(20)),
        "san_flags": {
            "capture_x": sum("x" in move for move in moves),
            "check_plus": sum("+" in move for move in moves),
            "mate_hash": sum("#" in move for move in moves),
            "castle": sum(move.startswith(("O-O", "0-0")) for move in moves),
            "promotion": sum("=" in move for move in moves),
        },
        "suspect_counts": {
            "movetype_capture_total": sum(
                row.get("MoveType", "").lower() == "capture" for row in inputs
            ),
            "movetype_capture_without_x": sum(
                row.get("MoveType", "").lower() == "capture" and "x" not in row.get("Move", "")
                for row in inputs
            ),
            "captured_nonempty_without_x": sum(
                bool(row.get("Captured")) and "x" not in row.get("Move", "") for row in inputs
            ),
            "noncapture_output_capture_words": noncapture_output_capture_words,
            "capture_output_missing_capture_words": capture_output_missing_words,
            "raw_check_mismatch_san": sum(
                yes_no(row.get("Check")) != (("+" in row.get("Move", "")) or ("#" in row.get("Move", "")))
                for row in inputs
            ),
            "raw_checkmate_mismatch_san": sum(
                yes_no(row.get("Checkmate")) != ("#" in row.get("Move", "")) for row in inputs
            ),
        },
        "segments": segment_summary,
        "duplicates": {
            "exact_duplicate_rows": sum(count - 1 for count in exact_rows.values() if count > 1),
            "duplicate_inputs_extra": sum(count - 1 for count in input_counts.values() if count > 1),
            "inputs_with_multiple_outputs": sum(
                1 for values in outputs_by_input.values() if len(values) > 1
            ),
        },
        "feature_counts": {
            "source": dict(Counter(record["feature_source"] for record in records)),
            "replay_status": dict(Counter(record["replay_status"] for record in records)),
            "is_capture": sum(bool(record.get("is_capture")) for record in records),
            "is_castling": sum(bool(record.get("is_castling")) for record in records),
            "is_check": sum(bool(record.get("is_check")) for record in records),
            "is_checkmate": sum(bool(record.get("is_checkmate")) for record in records),
            "is_promotion": sum(bool(record.get("is_promotion")) for record in records),
            "is_quiet": sum(bool(record.get("is_quiet")) for record in records),
        },
        "replay": replay_summary,
        "stockfish": stockfish_summary,
        "rewrite": rewrite_summary,
    }
    if split_summary is not None:
        audit["sft_split"] = split_summary
    return audit


def make_user_prompt(record: Dict[str, Any]) -> str:
    move_prefix = f"{record['fullmove_number']}." if record["current_player"] == "White" else f"{record['fullmove_number']}..."
    fields = [
        f"Move: {move_prefix} {record['san']}",
        f"Player: {record['current_player']}",
        f"Piece: {record.get('piece') or infer_piece_from_san(record['san'])}",
    ]
    if record.get("from_square") and record.get("to_square"):
        fields.append(f"From: {record['from_square']}")
        fields.append(f"To: {record['to_square']}")
    fields.extend(
        [
            f"Phase: {record.get('phase_raw')}",
            f"Classification: {record.get('classification_stockfish') or record.get('classification_raw')}",
            f"Capture: {'Yes' if record.get('is_capture') else 'No'}",
        ]
    )
    if record.get("captured_piece"):
        fields.append(f"CapturedPiece: {record['captured_piece']}")
    fields.extend(
        [
            f"Check: {'Yes' if record.get('is_check') else 'No'}",
            f"Checkmate: {'Yes' if record.get('is_checkmate') else 'No'}",
            f"Castling: {record.get('castle_side') or 'No'}",
            f"Promotion: {record.get('promotion_piece') or 'No'}",
            f"GameOver: {record.get('game_over_raw')}",
            f"Winner: {record.get('winner_raw')}",
        ]
    )
    if record.get("advantage_after"):
        fields.append(f"AdvantageAfter: {record['advantage_after']}")
    if record.get("cpl") is not None:
        fields.append(f"CPL: {record['cpl']}")
    if record.get("best_move_uci"):
        fields.append(f"BestMoveUCI: {record['best_move_uci']}")
    return " | ".join(fields)


def split_records_by_game(
    records: Sequence[Dict[str, Any]],
    seed: int,
    train_ratio: float = 0.7,
    valid_ratio: float = 0.2,
    max_attempts: int = 1000,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    test_ratio = 1.0 - train_ratio - valid_ratio
    if train_ratio <= 0 or valid_ratio <= 0 or test_ratio <= 0:
        raise ValueError(
            "Split ratios must be positive and train_ratio + valid_ratio must be less than 1."
        )

    games = sorted({record["game_id"] for record in records})
    required_features = ["is_capture", "is_castling", "is_checkmate", "is_promotion", "is_quiet"]
    global_feature_presence = {
        feature: any(bool(record.get(feature)) for record in records) for feature in required_features
    }

    def assign_for_seed(current_seed: int) -> Dict[str, str]:
        shuffled = games[:]
        random.Random(current_seed).shuffle(shuffled)
        train_end = int(len(shuffled) * train_ratio)
        valid_end = train_end + int(len(shuffled) * valid_ratio)
        assignment: Dict[str, str] = {}
        for game in shuffled[:train_end]:
            assignment[game] = "train"
        for game in shuffled[train_end:valid_end]:
            assignment[game] = "valid"
        for game in shuffled[valid_end:]:
            assignment[game] = "test"
        return assignment

    def coverage_ok(assignment: Dict[str, str]) -> Tuple[bool, List[str]]:
        missing: List[str] = []
        for split in ("valid", "test"):
            split_records = [record for record in records if assignment[record["game_id"]] == split]
            for feature, required in global_feature_presence.items():
                if required and not any(bool(record.get(feature)) for record in split_records):
                    missing.append(f"{split}:{feature}")
        return not missing, missing

    best_assignment = assign_for_seed(seed)
    best_missing: List[str] = []
    used_seed = seed
    for offset in range(max_attempts):
        candidate_seed = seed + offset
        candidate = assign_for_seed(candidate_seed)
        ok, missing = coverage_ok(candidate)
        if offset == 0:
            best_missing = missing
        if ok:
            best_assignment = candidate
            best_missing = []
            used_seed = candidate_seed
            break

    split_counts = Counter(best_assignment[record["game_id"]] for record in records)
    game_counts = Counter(best_assignment.values())
    feature_counts: Dict[str, Dict[str, int]] = {}
    for split in ("train", "valid", "test"):
        split_records = [record for record in records if best_assignment[record["game_id"]] == split]
        feature_counts[split] = {
            "rows": len(split_records),
            "games": sum(1 for value in best_assignment.values() if value == split),
            "is_capture": sum(bool(record.get("is_capture")) for record in split_records),
            "is_castling": sum(bool(record.get("is_castling")) for record in split_records),
            "is_checkmate": sum(bool(record.get("is_checkmate")) for record in split_records),
            "is_promotion": sum(bool(record.get("is_promotion")) for record in split_records),
            "is_quiet": sum(bool(record.get("is_quiet")) for record in split_records),
        }

    summary = {
        "requested_seed": seed,
        "used_seed": used_seed,
        "ratios": {
            "train": train_ratio,
            "valid": valid_ratio,
            "test": test_ratio,
        },
        "games": len(games),
        "row_counts": dict(split_counts),
        "game_counts": dict(game_counts),
        "feature_counts": feature_counts,
        "coverage_missing": best_missing,
        "leakage_game_ids": [],
    }
    return best_assignment, summary


def build_sft_rows(records: Sequence[Dict[str, Any]], assignment: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    splits: Dict[str, List[Dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    for record in records:
        split = assignment[record["game_id"]]
        row = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": make_user_prompt(record)},
                {"role": "assistant", "content": record["clean_commentary_en"]},
            ]
        }
        splits[split].append(row)
    return splits


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_replay_failures(path: Path, failures: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["game_id", "raw_index", "ply", "san", "fen_before", "error"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for failure in failures:
            writer.writerow({key: failure.get(key) for key in fieldnames})


def validate_sft_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    assistant_texts = [row["messages"][2]["content"] for row in rows]
    lengths = [len(text.split()) for text in assistant_texts]
    return {
        "rows": len(rows),
        "empty_outputs": sum(not normalize_text(text) for text in assistant_texts),
        "min_words": min(lengths) if lengths else 0,
        "max_words": max(lengths) if lengths else 0,
        "sample_outputs": assistant_texts[:20],
    }


def run_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    raw_path = Path(args.raw)
    processed_dir = Path(args.processed_dir)
    sft_dir = Path(args.sft_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    sft_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = load_raw_dataset(raw_path, max_records=args.max_records)
    records, segment_summary = build_base_records(raw_rows)
    if args.no_replay and not args.allow_san_only:
        raise ValueError("--no-replay is only allowed together with --allow-san-only.")

    replay_summary = replay_with_python_chess(records) if not args.no_replay else {
        "attempted": False,
        "available": False,
        "success_rows": 0,
        "failed_rows": 0,
        "skipped_rows": len(records),
        "pass_rate": 0.0,
        "message": "Replay disabled by --no-replay.",
    }
    if not args.allow_san_only and not replay_summary.get("available"):
        raise RuntimeError(
            "python-chess replay is required for training artifacts. "
            "Install requirements.txt, or pass --allow-san-only for debug-only outputs."
        )

    stockfish_summary: Dict[str, Any]
    if args.with_stockfish:
        stockfish_summary = enrich_with_stockfish(
            records=records,
            engine_path=args.stockfish_path or os.getenv("STOCKFISH_PATH"),
            depth=args.stockfish_depth,
            min_replay_pass_rate=args.min_replay_pass_rate,
            replay_summary=replay_summary,
        )
    else:
        stockfish_summary = {"attempted": False, "message": "Disabled; pass --with-stockfish to enable."}

    cleaned_rows, quarantine_rows, rewrite_summary = rewrite_commentary(
        records,
        require_replay_success=not args.allow_san_only,
    )
    if not cleaned_rows:
        raise RuntimeError("No replay-successful and label-clean rows are available for SFT.")

    assignment, split_summary = split_records_by_game(
        cleaned_rows,
        seed=args.seed,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
    )
    sft_rows = build_sft_rows(cleaned_rows, assignment)
    split_summary["sft_validation"] = {
        split: validate_sft_rows(rows) for split, rows in sft_rows.items()
    }

    audit_report = build_audit_report(
        raw_rows=raw_rows,
        records=records,
        segment_summary=segment_summary,
        replay_summary=replay_summary,
        stockfish_summary=stockfish_summary,
        rewrite_summary=rewrite_summary,
        split_summary=split_summary,
    )

    write_json(processed_dir / "data_audit_report.json", audit_report)
    write_jsonl(processed_dir / "move_records.jsonl", records)
    write_jsonl(processed_dir / "commentary_rewritten_en.jsonl", cleaned_rows)
    write_jsonl(processed_dir / "commentary_quarantine.jsonl", quarantine_rows)
    write_replay_failures(
        processed_dir / "replay_failures.csv",
        replay_summary.get("failures", []),
    )
    write_jsonl(sft_dir / "qwen_sft_train.jsonl", sft_rows["train"])
    write_jsonl(sft_dir / "qwen_sft_valid.jsonl", sft_rows["valid"])
    write_jsonl(sft_dir / "qwen_sft_test.jsonl", sft_rows["test"])

    return audit_report


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build cleaned per-move chess commentary records and Qwen SFT JSONL files."
    )
    parser.add_argument(
        "--raw",
        default="data/raw/chess_commentary_cleaned_combined.json",
        help="Raw instruction/input/output JSON array.",
    )
    parser.add_argument(
        "--processed-dir",
        default="data/processed",
        help="Directory for processed audit and intermediate JSONL files.",
    )
    parser.add_argument(
        "--sft-dir",
        default="data/sft",
        help="Directory for train/valid/test SFT JSONL files.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Base seed for game-level split.")
    parser.add_argument("--train-ratio", type=float, default=0.7, help="Game-level train split ratio.")
    parser.add_argument("--valid-ratio", type=float, default=0.2, help="Game-level validation split ratio.")
    parser.add_argument("--max-records", type=int, default=None, help="Optional debug limit.")
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="Skip python-chess replay. Requires --allow-san-only and is debug-only.",
    )
    parser.add_argument(
        "--allow-san-only",
        action="store_true",
        help="Allow SAN-only debug outputs when replay is unavailable or disabled. Do not use for final SFT.",
    )
    parser.add_argument(
        "--with-stockfish",
        action="store_true",
        help="Run Stockfish enrichment after successful replay.",
    )
    parser.add_argument(
        "--stockfish-path",
        default=None,
        help="Path to Stockfish engine. Defaults to STOCKFISH_PATH.",
    )
    parser.add_argument("--stockfish-depth", type=int, default=8, help="Stockfish depth.")
    parser.add_argument(
        "--min-replay-pass-rate",
        type=float,
        default=0.98,
        help="Minimum replay pass rate required before Stockfish enrichment.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    report = run_pipeline(args)
    summary = {
        "samples": report["samples"],
        "segments": report["segments"],
        "feature_counts": report["feature_counts"],
        "rewrite": report["rewrite"],
        "sft_split": report["sft_split"],
        "outputs": {
            "audit": str(Path(args.processed_dir) / "data_audit_report.json"),
            "move_records": str(Path(args.processed_dir) / "move_records.jsonl"),
            "commentary": str(Path(args.processed_dir) / "commentary_rewritten_en.jsonl"),
            "train": str(Path(args.sft_dir) / "qwen_sft_train.jsonl"),
            "valid": str(Path(args.sft_dir) / "qwen_sft_valid.jsonl"),
            "test": str(Path(args.sft_dir) / "qwen_sft_test.jsonl"),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
