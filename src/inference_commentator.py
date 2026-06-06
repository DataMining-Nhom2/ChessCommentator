import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

from src.data.data_pipeline import make_user_prompt
from src.pgn_features import records_from_pgn
from src.qwen_runtime import generate_commentary_text, load_tokenizer_and_model


def load_pgn_text(path: Optional[str]) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def prompts_from_pgn(pgn_text: str, max_moves: Optional[int]) -> Iterable[Dict[str, str]]:
    for record in records_from_pgn(pgn_text, max_moves=max_moves):
        yield {
            "move": record["san"],
            "prompt": make_user_prompt(record),
        }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate per-move chess commentary from a PGN.")
    parser.add_argument("--pgn-file", default=None, help="PGN file. Reads stdin if omitted.")
    parser.add_argument("--model-name-or-path", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--adapter-path", default="models/qwen2_5_7b_chess_commentator_lora")
    parser.add_argument("--max-moves", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--dry-run-prompts", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    prompt_rows = list(prompts_from_pgn(load_pgn_text(args.pgn_file), args.max_moves))

    if args.dry_run_prompts:
        for row in prompt_rows:
            print(row["prompt"])
        return

    tokenizer, model = load_tokenizer_and_model(args.model_name_or_path, args.adapter_path)
    for row in prompt_rows:
        row["commentary"] = generate_commentary_text(
            tokenizer,
            model,
            row["prompt"],
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
        )
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
