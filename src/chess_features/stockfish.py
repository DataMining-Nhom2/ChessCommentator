from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data.data_pipeline import (
    advantage_label,
    classify_stockfish,
    compute_cpl,
    score_to_cp,
)


def resolve_stockfish_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    clean_path = str(path).strip().strip('"')
    if not clean_path:
        return None
    if clean_path.lower() == "stockfish":
        return clean_path
    return str(Path(clean_path))


def enrich_records_with_stockfish(
    records: List[Dict[str, Any]],
    engine_path: Optional[str],
    depth: int = 8,
    time_limit: Optional[float] = None,
) -> Dict[str, Any]:
    resolved_path = resolve_stockfish_path(engine_path)
    if not resolved_path:
        return {"attempted": False, "message": "No Stockfish path provided."}

    try:
        import chess
        import chess.engine
    except ImportError:
        return {"attempted": False, "message": "python-chess is required for Stockfish enrichment."}

    limit = chess.engine.Limit(depth=depth) if time_limit is None else chess.engine.Limit(time=time_limit)
    engine = chess.engine.SimpleEngine.popen_uci(resolved_path)
    enriched = 0
    failed = 0

    try:
        for record in records:
            fen_before = record.get("fen_before")
            fen_after = record.get("fen_after")
            if not fen_before or not fen_after:
                failed += 1
                record.setdefault("stockfish_errors", []).append("missing_fen")
                continue

            try:
                board_before = chess.Board(fen_before)
                board_after = chess.Board(fen_after)
                before_info = engine.analyse(board_before, limit)
                after_info = engine.analyse(board_after, limit)
                eval_before = score_to_cp(before_info.get("score"))
                eval_after = score_to_cp(after_info.get("score"))
                best_move_uci = None
                if before_info.get("pv"):
                    best_move_uci = before_info["pv"][0].uci()

                cpl = compute_cpl(eval_before, eval_after, str(record.get("current_player")))
                record.update(
                    {
                        "feature_source": "stockfish",
                        "eval_before_cp": eval_before,
                        "eval_after_cp": eval_after,
                        "best_move_uci": best_move_uci,
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
                record.setdefault("stockfish_errors", []).append(
                    f"{type(exc).__name__}:{exc}"
                )
    finally:
        engine.quit()

    return {
        "attempted": True,
        "engine_path": resolved_path,
        "depth": depth,
        "time_limit": time_limit,
        "enriched_rows": enriched,
        "failed_rows": failed,
    }
