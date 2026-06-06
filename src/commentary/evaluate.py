import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from src.commentary.utils import evaluate_commentary_rows, read_jsonl, write_json


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate chess commentary labels or generated predictions.")
    parser.add_argument("--file", default="data/sft/qwen_sft_test.jsonl")
    parser.add_argument(
        "--text-field",
        default="assistant",
        help="Use 'assistant' for SFT labels, or a top-level field such as 'prediction'.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--output", default=None, help="Optional JSON report path.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = read_jsonl(Path(args.file), args.max_samples)
    report = evaluate_commentary_rows(rows, args.text_field)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        write_json(Path(args.output), report)


if __name__ == "__main__":
    main()
