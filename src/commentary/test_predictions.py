import argparse
import gc
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.commentary.runtime import generate_commentary_text
from src.commentary.utils import evaluate_commentary_rows, read_jsonl, write_json


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


def prediction_row(row: Dict[str, Any], prediction: str, index: int) -> Dict[str, Any]:
    messages = row["messages"]
    return {
        "index": index,
        "prompt": messages[1]["content"],
        "reference": messages[2]["content"],
        "prediction": prediction,
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and evaluate Qwen commentary predictions on SFT test rows.")
    parser.add_argument("--test-file", default="data/sft/qwen_sft_test.jsonl")
    parser.add_argument("--max-samples", type=int, default=200)
    parser.add_argument("--model-name-or-path", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--adapter-path", default="models/qwen2_5_3b_chess_commentator_lora")
    parser.add_argument("--output-jsonl", default="reports/qwen_test200_predictions.jsonl")
    parser.add_argument("--output-report", default="reports/qwen_test200_report.json")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--use-fast-tokenizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--progress-every", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    rows = read_jsonl(Path(args.test_file), args.max_samples)
    print(json.dumps({"stage": "loaded_test_rows", "rows": len(rows)}, ensure_ascii=False), flush=True)

    tokenizer, model = load_quantized_model(
        args.model_name_or_path,
        args.adapter_path,
        args.use_fast_tokenizer,
    )
    print(json.dumps({"stage": "model_loaded"}, ensure_ascii=False), flush=True)

    predictions: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        prompt = row["messages"][1]["content"]
        text = generate_commentary_text(
            tokenizer,
            model,
            prompt,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
        )
        predictions.append(prediction_row(row, text, index))

        if index == len(rows) or index % args.progress_every == 0:
            print(json.dumps(
                {
                    "stage": "generated",
                    "rows": index,
                    "total": len(rows),
                    "last_prediction": text,
                },
                ensure_ascii=False,
            ), flush=True)

    output_jsonl = Path(args.output_jsonl)
    write_jsonl(output_jsonl, predictions)
    report = evaluate_commentary_rows(predictions, text_field="prediction")
    write_json(Path(args.output_report), report)

    print(json.dumps(
        {
            "stage": "done",
            "predictions": str(output_jsonl),
            "report": args.output_report,
            "summary": report,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
