import argparse
import csv
import gc
import json
import os
import random
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.chess_features.pgn import records_from_pgn
from src.chess_features.stockfish import enrich_records_with_stockfish
from src.commentary.runtime import generate_commentary_text
from src.commentary.utils import (
    CAPTURE_WORDS_RE,
    CASTLE_WORDS_RE,
    CHECK_WORDS_RE,
    ENGINE_SUMMARY_RE,
    EXPLICIT_PROMOTION_RE,
    MATE_WORDS_RE,
    QUALITY_WORDS_RE,
    commentary_issues,
    evaluate_commentary_rows,
    facts_from_user_prompt,
    normalize_text,
    read_jsonl,
    sanitize_commentary_text,
    write_json,
)
from src.data.data_pipeline import SYSTEM_PROMPT, make_user_prompt, split_records_by_game


CURATED_PGNS = {
    "quiet_pawn": "1. f3 e5 *",
    "quiet_knight": "1. Nf3 Nf6 *",
    "quiet_king": (
        '[SetUp "1"]\n'
        '[FEN "8/7k/8/8/8/8/8/K7 b - - 0 1"]\n\n'
        "1... Kg6 *"
    ),
    "kingside_castle": "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. O-O Be7 5. Re1 O-O *",
    "capture_mate": "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# *",
    "morphy_queenside_castle": (
        "1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 "
        "5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7 8. Nc3 c6 "
        "9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 "
        "12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 "
        "15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# *"
    ),
    "promotion_capture": (
        '[SetUp "1"]\n'
        '[FEN "1n6/P7/8/8/8/8/8/k6K w - - 0 1"]\n\n'
        "1. axb8=Q *"
    ),
    "promotion_quiet": (
        '[SetUp "1"]\n'
        '[FEN "8/2P5/8/8/8/8/8/k6K w - - 0 1"]\n\n'
        "1. c8=Q *"
    ),
    "knight_disambiguation": "1. Nf3 Nf6 2. d3 Nc6 3. Nbd2 *",
    "rook_disambiguation": (
        '[SetUp "1"]\n'
        '[FEN "r4r1k/8/8/8/8/8/8/4K3 b - - 0 1"]\n\n'
        "1... Rfe8+ *"
    ),
}

CURATED_WANTED_MOVES = {
    "f3",
    "Nf3",
    "Kg6",
    "O-O",
    "Qxf7#",
    "O-O-O",
    "Rxd7",
    "Rd8#",
    "axb8=Q",
    "c8=Q",
    "Nbd2",
    "Rfe8+",
}


def load_quantized_model(
    model_name_or_path: str,
    adapter_path: str,
    use_fast_tokenizer: bool,
):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    tokenizer = AutoTokenizer.from_pretrained(
        model_name_or_path,
        trust_remote_code=True,
        use_fast=use_fast_tokenizer,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    compute_dtype = (
        torch.bfloat16
        if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        else torch.float16
    )
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )
    device_map = {"": 0} if torch.cuda.is_available() else None

    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        quantization_config=quant_config,
        torch_dtype=compute_dtype,
        device_map=device_map,
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()
    return tokenizer, model


def curated_rows(args: argparse.Namespace) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for suite_name, pgn in CURATED_PGNS.items():
        records = records_from_pgn(pgn, max_moves=None)
        if args.with_stockfish:
            summary = enrich_records_with_stockfish(
                records,
                engine_path=args.stockfish_path or os.getenv("STOCKFISH_PATH"),
                depth=args.stockfish_depth,
                time_limit=args.stockfish_time_limit,
            )
            print(json.dumps(
                {"stage": "stockfish", "suite": suite_name, "summary": summary},
                ensure_ascii=False,
            ), flush=True)
            if not summary.get("attempted"):
                raise SystemExit(summary.get("message", "Stockfish enrichment failed before starting."))

        for record in records:
            if record["san"] not in CURATED_WANTED_MOVES:
                continue
            prompt = make_user_prompt(record)
            rows.append(
                {
                    "suite": suite_name,
                    "move": record["san"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": ""},
                    ],
                }
            )
    if args.max_samples is not None:
        rows = rows[:args.max_samples]
    return rows


def selected_test_game_rows(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = read_jsonl(Path(args.processed_file))
    assignment, split_summary = split_records_by_game(
        records,
        seed=args.seed,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
    )

    if args.test_game_ids:
        selected_game_ids = [
            game_id.strip()
            for game_id in args.test_game_ids.split(",")
            if game_id.strip()
        ]
        non_test_games = [
            game_id
            for game_id in selected_game_ids
            if assignment.get(game_id) != "test"
        ]
        if non_test_games:
            raise SystemExit(
                "These game ids are not in the test split: "
                + ", ".join(non_test_games)
            )
    else:
        test_game_ids = sorted(
            game_id for game_id, split in assignment.items() if split == "test"
        )
        if args.test_game_selection == "shortest":
            row_counts = Counter(record["game_id"] for record in records)
            test_game_ids = sorted(test_game_ids, key=lambda game_id: (row_counts[game_id], game_id))
        elif args.test_game_selection == "random":
            random.Random(args.seed).shuffle(test_game_ids)
        selected_game_ids = test_game_ids[: args.test_games]

    selected_set = set(selected_game_ids)
    selected_records = [
        dict(record)
        for record in records
        if record.get("game_id") in selected_set
    ]

    stockfish_summary: Optional[Dict[str, Any]] = None
    if args.with_stockfish:
        stockfish_summary = enrich_records_with_stockfish(
            selected_records,
            engine_path=args.stockfish_path or os.getenv("STOCKFISH_PATH"),
            depth=args.stockfish_depth,
            time_limit=args.stockfish_time_limit,
        )
        print(json.dumps(
            {"stage": "stockfish", "summary": stockfish_summary},
            ensure_ascii=False,
        ), flush=True)
        if not stockfish_summary.get("attempted"):
            raise SystemExit(stockfish_summary.get(
                "message",
                "Stockfish enrichment failed before starting.",
            ))

    rows: List[Dict[str, Any]] = []
    rows_per_game = Counter(record["game_id"] for record in selected_records)
    for record in selected_records:
        prompt = make_user_prompt(record)
        reference = str(record.get("clean_commentary_en", ""))
        rows.append(
            {
                "suite": "test_game",
                "game_id": record.get("game_id"),
                "record_id": record.get("record_id"),
                "raw_index": record.get("raw_index"),
                "ply": record.get("ply"),
                "move": record.get("san"),
                "reference": reference,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": reference},
                ],
            }
        )

    return rows, {
        "test_game_selection": {
            "processed_file": args.processed_file,
            "split_seed": args.seed,
            "train_ratio": args.train_ratio,
            "valid_ratio": args.valid_ratio,
            "requested_games": args.test_games,
            "selection_strategy": args.test_game_selection,
            "selected_game_ids": selected_game_ids,
            "selected_rows": len(rows),
            "rows_per_game": {game_id: rows_per_game[game_id] for game_id in selected_game_ids},
            "split_summary": split_summary,
            "stockfish": stockfish_summary,
        }
    }


def load_test_rows(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if args.curated:
        return curated_rows(args), {"source": "curated"}
    if args.test_games or args.test_game_ids:
        return selected_test_game_rows(args)
    if args.with_stockfish:
        raise SystemExit("--with-stockfish is currently supported with --curated PGN/FEN tests only.")
    return read_jsonl(Path(args.test_file), args.max_samples), {"source": args.test_file}


def prediction_row(
    row: Dict[str, Any],
    prediction: str,
    index: int,
    latency_seconds: float,
    raw_prediction: Optional[str] = None,
) -> Dict[str, Any]:
    messages = row["messages"]
    reference = str(row.get("reference", messages[2].get("content", "")))
    issues = commentary_issues(
        {
            "messages": [
                messages[0],
                messages[1],
                {"role": "assistant", "content": reference},
            ],
            "reference": reference,
        },
        prediction,
        text_field="prediction",
    )
    return {
        "index": index,
        "suite": row.get("suite"),
        "game_id": row.get("game_id"),
        "record_id": row.get("record_id"),
        "raw_index": row.get("raw_index"),
        "ply": row.get("ply"),
        "move": row.get("move"),
        "prompt": messages[1]["content"],
        "reference": reference,
        "raw_prediction": raw_prediction if raw_prediction is not None else prediction,
        "prediction": prediction,
        "latency_seconds": latency_seconds,
        "prediction_words": len(prediction.split()),
        "prediction_chars": len(prediction),
        "errors": issues["errors"],
        "warnings": issues["warnings"],
        "messages": [
            messages[0],
            messages[1],
            {"role": "assistant", "content": prediction},
        ],
    }


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv_report(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "index",
        "suite",
        "move",
        "latency_seconds",
        "prediction_words",
        "errors",
        "warnings",
        "prompt",
        "reference",
        "raw_prediction",
        "prediction",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["errors"] = ";".join(row.get("errors", []))
            flat["warnings"] = ";".join(row.get("warnings", []))
            writer.writerow(flat)


def write_markdown_review(path: Path, rows: Sequence[Dict[str, Any]], sample_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    error_rows = [row for row in rows if row.get("errors")]
    warning_rows = [row for row in rows if row.get("warnings")]
    sample_rows = list(rows[:sample_count])
    review_rows = sample_rows
    review_rows += [row for row in error_rows[:sample_count] if row not in review_rows]
    review_rows += [row for row in warning_rows[:sample_count] if row not in review_rows]

    lines = [
        "# Qwen Commentary Test Review",
        "",
        f"Rows generated: {len(rows)}",
        f"Rows with rule errors: {len(error_rows)}",
        f"Rows with semantic warnings: {len(warning_rows)}",
        "",
    ]
    for row in review_rows:
        lines.extend(
            [
                "---",
                f"## Row {row['index']}",
                "",
                f"Suite: `{row.get('suite') or 'test_file'}`",
                f"Move: `{row.get('move') or 'unknown'}`",
                f"Latency: `{row['latency_seconds']:.3f}s`",
                f"Errors: `{', '.join(row.get('errors') or ['none'])}`",
                f"Warnings: `{', '.join(row.get('warnings') or ['none'])}`",
                "",
                "**Prompt**",
                "",
                "```text",
                row["prompt"],
                "```",
                "",
                "**Reference**",
                "",
                "```text",
                row["reference"],
                "```",
                "",
                "**Prediction**",
                "",
                "```text",
                row["prediction"],
                "```",
                "",
            ]
        )
        raw_prediction = row.get("raw_prediction")
        if raw_prediction and raw_prediction != row["prediction"]:
            lines.extend(
                [
                    "**Raw Prediction**",
                    "",
                    "```text",
                    str(raw_prediction),
                    "```",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def performance_summary(
    predictions: Sequence[Dict[str, Any]],
    load_seconds: float,
    generation_seconds: float,
    wall_seconds: float,
) -> Dict[str, Any]:
    latencies = [float(row["latency_seconds"]) for row in predictions]
    words = [int(row["prediction_words"]) for row in predictions]
    return {
        "load_seconds": load_seconds,
        "generation_seconds": generation_seconds,
        "wall_seconds": wall_seconds,
        "samples": len(predictions),
        "samples_per_second": len(predictions) / generation_seconds if generation_seconds else None,
        "seconds_per_sample": generation_seconds / len(predictions) if predictions else None,
        "latency_avg": statistics.mean(latencies) if latencies else None,
        "latency_p50": percentile(latencies, 50),
        "latency_p95": percentile(latencies, 95),
        "prediction_words_avg": statistics.mean(words) if words else None,
        "prediction_words_min": min(words) if words else None,
        "prediction_words_max": max(words) if words else None,
    }


def classification_keyword(classification: str) -> Optional[str]:
    lowered = classification.lower()
    for keyword in ("blunder", "mistake", "inaccuracy", "excellent", "good"):
        if keyword in lowered:
            return keyword
    return None


def metric_counts(
    rows: Sequence[Dict[str, Any]],
    text_field: str,
    expected_fn: Callable[[Dict[str, Any]], bool],
    predicted_fn: Callable[[str, Dict[str, Any]], bool],
) -> Dict[str, Any]:
    tp = fp = fn = tn = 0
    for row in rows:
        facts = facts_from_user_prompt(str(row["messages"][1].get("content", "")))
        text = normalize_text(str(row.get(text_field, "")))
        expected = expected_fn(facts)
        predicted = predicted_fn(text, facts)
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif not expected and predicted:
            fp += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    total = tp + fp + fn + tn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "support": tp + fn,
        "predicted_positive": tp + fp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "false_negative_rate": fn / (fn + tp) if fn + tp else None,
        "accuracy": (tp + tn) / total if total else None,
    }


def exact_engine_metrics(rows: Sequence[Dict[str, Any]], text_field: str) -> Dict[str, Any]:
    expected_engine_rows = 0
    engine_mentioned = 0
    cpl_exact_mentioned = 0
    classification_word_mentioned = 0

    for row in rows:
        facts = facts_from_user_prompt(str(row["messages"][1].get("content", "")))
        if not facts.get("has_cpl"):
            continue

        expected_engine_rows += 1
        text = normalize_text(str(row.get(text_field, "")))
        lowered = text.lower()
        if ENGINE_SUMMARY_RE.search(text) or QUALITY_WORDS_RE.search(text):
            engine_mentioned += 1

        cpl = str(facts.get("cpl") or "").strip()
        if cpl and f"{cpl} cpl" in lowered:
            cpl_exact_mentioned += 1

        keyword = classification_keyword(str(facts.get("classification") or ""))
        if keyword and keyword in lowered:
            classification_word_mentioned += 1

    return {
        "expected_engine_rows": expected_engine_rows,
        "engine_mentioned": engine_mentioned,
        "engine_mention_rate": engine_mentioned / expected_engine_rows if expected_engine_rows else None,
        "cpl_exact_mentioned": cpl_exact_mentioned,
        "cpl_exact_mention_rate": cpl_exact_mentioned / expected_engine_rows if expected_engine_rows else None,
        "classification_word_mentioned": classification_word_mentioned,
        "classification_word_mention_rate": (
            classification_word_mentioned / expected_engine_rows
            if expected_engine_rows
            else None
        ),
    }


def feature_metrics(rows: Sequence[Dict[str, Any]], text_field: str) -> Dict[str, Any]:
    return {
        "text_field": text_field,
        "capture": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("is_capture")),
            lambda text, _facts: bool(CAPTURE_WORDS_RE.search(text)),
        ),
        "check": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("is_check")),
            lambda text, _facts: bool(CHECK_WORDS_RE.search(text) or MATE_WORDS_RE.search(text)),
        ),
        "checkmate": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("is_checkmate")),
            lambda text, _facts: bool(MATE_WORDS_RE.search(text)),
        ),
        "castling": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("is_castling")),
            lambda text, _facts: bool(CASTLE_WORDS_RE.search(text)),
        ),
        "promotion": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("is_promotion")),
            lambda text, _facts: bool(EXPLICIT_PROMOTION_RE.search(text)),
        ),
        "engine_quality": metric_counts(
            rows,
            text_field,
            lambda facts: bool(facts.get("has_cpl")),
            lambda text, _facts: bool(ENGINE_SUMMARY_RE.search(text) or QUALITY_WORDS_RE.search(text)),
        ),
        "engine_exact": exact_engine_metrics(rows, text_field),
    }


def per_game_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("game_id") or row.get("suite") or "unknown")].append(row)

    summary: Dict[str, Any] = {}
    for game_id, game_rows in grouped.items():
        error_count = sum(len(row.get("errors") or []) for row in game_rows)
        warning_count = sum(len(row.get("warnings") or []) for row in game_rows)
        latencies = [float(row.get("latency_seconds") or 0.0) for row in game_rows]
        summary[game_id] = {
            "rows": len(game_rows),
            "first_move": game_rows[0].get("move"),
            "last_move": game_rows[-1].get("move"),
            "error_count": error_count,
            "warning_count": warning_count,
            "avg_latency_seconds": statistics.mean(latencies) if latencies else None,
        }
    return summary


def write_prediction_artifacts(
    args: argparse.Namespace,
    predictions: Sequence[Dict[str, Any]],
    load_seconds: float,
    generation_seconds: float,
    wall_seconds: float,
    extra_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    output_jsonl = Path(args.output_jsonl)
    write_jsonl(output_jsonl, predictions)
    write_csv_report(Path(args.output_csv), predictions)
    write_markdown_review(Path(args.output_review_md), predictions, args.review_samples)

    report = evaluate_commentary_rows(predictions, text_field="prediction")
    report["feature_metrics"] = feature_metrics(predictions, text_field="prediction")
    report["per_game"] = per_game_summary(predictions)
    if predictions and "raw_prediction" in predictions[0]:
        report["raw_prediction_eval"] = evaluate_commentary_rows(
            predictions,
            text_field="raw_prediction",
        )
        report["raw_feature_metrics"] = feature_metrics(
            predictions,
            text_field="raw_prediction",
        )
    report["performance"] = performance_summary(
        predictions,
        load_seconds=load_seconds,
        generation_seconds=generation_seconds,
        wall_seconds=wall_seconds,
    )
    if extra_report:
        report.update(extra_report)
    report["outputs"] = {
        "predictions_jsonl": str(output_jsonl),
        "predictions_csv": args.output_csv,
        "review_markdown": args.output_review_md,
    }
    write_json(Path(args.output_report), report)
    return report


def normalize_prediction_source_row(row: Dict[str, Any]) -> Dict[str, Any]:
    messages = row.get("messages")
    if isinstance(messages, list) and len(messages) >= 2:
        reference = str(row.get("reference", ""))
        return {
            **row,
            "messages": [
                messages[0],
                messages[1],
                {"role": "assistant", "content": reference},
            ],
            "reference": reference,
        }

    prompt = str(row.get("prompt", ""))
    reference = str(row.get("reference", ""))
    return {
        **row,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": reference},
        ],
        "reference": reference,
    }


def postprocess_prediction_file(args: argparse.Namespace, wall_start: float) -> None:
    source_rows = read_jsonl(Path(args.postprocess_jsonl), args.max_samples)
    predictions: List[Dict[str, Any]] = []
    started = time.perf_counter()

    for fallback_index, source_row in enumerate(source_rows, start=1):
        row = normalize_prediction_source_row(source_row)
        raw_prediction = str(
            source_row.get("raw_prediction")
            or source_row.get("raw_commentary")
            or source_row.get("prediction")
            or source_row.get("commentary")
            or ""
        )
        prediction = sanitize_commentary_text(row, raw_prediction) if args.sanitize else raw_prediction
        predictions.append(
            prediction_row(
                row,
                prediction,
                int(source_row.get("index") or fallback_index),
                float(source_row.get("latency_seconds") or 0.0),
                raw_prediction=raw_prediction,
            )
        )

    postprocess_seconds = time.perf_counter() - started
    stored_generation_seconds = sum(
        float(row.get("latency_seconds") or 0.0)
        for row in predictions
    )
    report = write_prediction_artifacts(
        args,
        predictions,
        load_seconds=0.0,
        generation_seconds=stored_generation_seconds or postprocess_seconds,
        wall_seconds=stored_generation_seconds or (time.perf_counter() - wall_start),
        extra_report={
            "postprocess": {
                "source_jsonl": args.postprocess_jsonl,
                "sanitize": args.sanitize,
                "postprocess_seconds": postprocess_seconds,
                "performance_from_stored_latency": bool(stored_generation_seconds),
            }
        },
    )
    print(json.dumps(
        {
            "stage": "done",
            "mode": "postprocess",
            "predictions": args.output_jsonl,
            "csv": args.output_csv,
            "report": args.output_report,
            "review": args.output_review_md,
            "summary": report,
        },
        ensure_ascii=False,
        indent=2,
    ))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and evaluate Qwen commentary predictions on SFT test rows.")
    parser.add_argument("--test-file", default="data/sft/qwen_sft_test.jsonl")
    parser.add_argument("--processed-file", default="data/processed/commentary_rewritten_en.jsonl")
    parser.add_argument("--max-samples", type=int, default=200)
    parser.add_argument("--curated", action="store_true", help="Run curated PGN/FEN smoke tests instead of SFT test rows.")
    parser.add_argument("--test-games", type=int, default=None, help="Run full games from the test split instead of flat SFT rows.")
    parser.add_argument("--test-game-ids", default=None, help="Comma-separated test game ids. Overrides --test-games selection.")
    parser.add_argument(
        "--test-game-selection",
        choices=("first", "shortest", "random"),
        default="first",
        help="How to choose test games when --test-game-ids is not provided.",
    )
    parser.add_argument("--select-only", action="store_true", help="Write selected rows and report without loading the model.")
    parser.add_argument("--seed", type=int, default=42, help="Split seed used by the data pipeline.")
    parser.add_argument("--train-ratio", type=float, default=0.7, help="Train split ratio used by the data pipeline.")
    parser.add_argument("--valid-ratio", type=float, default=0.2, help="Validation split ratio used by the data pipeline.")
    parser.add_argument("--postprocess-jsonl", help="Sanitize and re-evaluate an existing predictions JSONL without loading the model.")
    parser.add_argument("--model-name-or-path", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--adapter-path", default="models/qwen2_5_3b_chess_commentator_lora")
    parser.add_argument("--output-jsonl", default="reports/qwen_test200_predictions.jsonl")
    parser.add_argument("--output-csv", default="reports/qwen_test200_predictions.csv")
    parser.add_argument("--output-report", default="reports/qwen_test200_report.json")
    parser.add_argument("--output-review-md", default="reports/qwen_test200_review.md")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--use-fast-tokenizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--sanitize", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--with-stockfish", action="store_true")
    parser.add_argument("--stockfish-path", default=None, help="Path to Stockfish. Defaults to STOCKFISH_PATH.")
    parser.add_argument("--stockfish-depth", type=int, default=8)
    parser.add_argument("--stockfish-time-limit", type=float, default=None)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--review-samples", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    wall_start = time.perf_counter()
    args = parse_args(argv)
    if args.postprocess_jsonl:
        postprocess_prediction_file(args, wall_start)
        return

    rows, load_report = load_test_rows(args)
    print(json.dumps(
        {
            "stage": "loaded_test_rows",
            "rows": len(rows),
            "source": "curated" if args.curated else ("test_games" if args.test_games else args.test_file),
            "load_report": load_report,
        },
        ensure_ascii=False,
    ), flush=True)

    if args.select_only:
        write_jsonl(Path(args.output_jsonl), rows)
        selection_report = {
            "rows": len(rows),
            **load_report,
            "outputs": {
                "selected_rows_jsonl": args.output_jsonl,
                "selection_report": args.output_report,
            },
        }
        write_json(Path(args.output_report), selection_report)
        print(json.dumps(
            {
                "stage": "done",
                "mode": "select_only",
                "selected_rows": args.output_jsonl,
                "report": args.output_report,
                "summary": selection_report,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    load_start = time.perf_counter()
    tokenizer, model = load_quantized_model(
        args.model_name_or_path,
        args.adapter_path,
        args.use_fast_tokenizer,
    )
    load_seconds = time.perf_counter() - load_start
    print(json.dumps({"stage": "model_loaded", "load_seconds": round(load_seconds, 3)}, ensure_ascii=False), flush=True)

    predictions: List[Dict[str, Any]] = []
    generation_start = time.perf_counter()
    for index, row in enumerate(rows, start=1):
        prompt = row["messages"][1]["content"]
        row_start = time.perf_counter()
        text = generate_commentary_text(
            tokenizer,
            model,
            prompt,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
        )
        latency_seconds = time.perf_counter() - row_start
        prediction = sanitize_commentary_text(row, text) if args.sanitize else text
        predictions.append(prediction_row(row, prediction, index, latency_seconds, raw_prediction=text))

        if index == len(rows) or index % args.progress_every == 0:
            print(json.dumps(
                {
                    "stage": "generated",
                    "rows": index,
                    "total": len(rows),
                    "latency_seconds": round(latency_seconds, 3),
                    "error_count": len(predictions[-1].get("errors", [])),
                    "warning_count": len(predictions[-1].get("warnings", [])),
                    "last_prediction": prediction,
                },
                ensure_ascii=False,
            ), flush=True)
    generation_seconds = time.perf_counter() - generation_start

    report = write_prediction_artifacts(
        args,
        predictions,
        load_seconds=load_seconds,
        generation_seconds=generation_seconds,
        wall_seconds=time.perf_counter() - wall_start,
        extra_report={"sanitize": args.sanitize, **load_report},
    )

    print(json.dumps(
        {
            "stage": "done",
            "predictions": args.output_jsonl,
            "csv": args.output_csv,
            "report": args.output_report,
            "review": args.output_review_md,
            "summary": report,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
