import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.chess_features.pgn import records_from_pgn
from src.chess_features.stockfish import enrich_records_with_stockfish
from src.commentary.runtime import generate_commentary_text, load_tokenizer_and_model
from src.commentary.utils import sanitize_commentary_text
from src.data.data_pipeline import SYSTEM_PROMPT, make_user_prompt


def load_pgn_text(path: Optional[str]) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def prompts_from_records(records: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, str]]:
    for record in records:
        yield {
            "move": str(record["san"]),
            "prompt": make_user_prompt(record),
        }


def records_with_optional_stockfish(args: argparse.Namespace) -> List[Dict[str, Any]]:
    records = records_from_pgn(load_pgn_text(args.pgn_file), max_moves=args.max_moves)
    if not args.with_stockfish:
        return records

    summary = enrich_records_with_stockfish(
        records,
        engine_path=args.stockfish_path or os.getenv("STOCKFISH_PATH"),
        depth=args.stockfish_depth,
        time_limit=args.stockfish_time_limit,
    )
    print(json.dumps({"stage": "stockfish", "summary": summary}, ensure_ascii=False), file=sys.stderr)
    if not summary.get("attempted"):
        raise SystemExit(summary.get("message", "Stockfish enrichment failed before starting."))
    return records


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate per-move chess commentary from a PGN.")
    parser.add_argument("--pgn-file", default=None, help="PGN file. Reads stdin if omitted.")
    parser.add_argument("--model-name-or-path", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--adapter-path", default="models/qwen2_5_7b_chess_commentator_lora")
    parser.add_argument("--max-moves", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--sanitize", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--with-stockfish", action="store_true")
    parser.add_argument("--stockfish-path", default=None, help="Path to Stockfish. Defaults to STOCKFISH_PATH.")
    parser.add_argument("--stockfish-depth", type=int, default=8)
    parser.add_argument("--stockfish-time-limit", type=float, default=None)
    parser.add_argument("--output-jsonl", default=None, help="Optional clean JSONL output path.")
    parser.add_argument("--dry-run-prompts", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    prompt_rows = list(prompts_from_records(records_with_optional_stockfish(args)))

    if args.dry_run_prompts:
        for row in prompt_rows:
            print(row["prompt"])
        return

    tokenizer, model = load_tokenizer_and_model(args.model_name_or_path, args.adapter_path)
    output_handle = None
    if args.output_jsonl:
        output_path = Path(args.output_jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_handle = output_path.open("w", encoding="utf-8", newline="\n")

    try:
        for row in prompt_rows:
            raw_commentary = generate_commentary_text(
                tokenizer,
                model,
                row["prompt"],
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
            )
            sanitizer_row = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": row["prompt"]},
                    {"role": "assistant", "content": ""},
                ]
            }
            row["raw_commentary"] = raw_commentary
            row["commentary"] = (
                sanitize_commentary_text(sanitizer_row, raw_commentary)
                if args.sanitize
                else raw_commentary
            )
            line = json.dumps(row, ensure_ascii=False)
            if output_handle is not None:
                output_handle.write(line + "\n")
            print(line)
    finally:
        if output_handle is not None:
            output_handle.close()


if __name__ == "__main__":
    main()
