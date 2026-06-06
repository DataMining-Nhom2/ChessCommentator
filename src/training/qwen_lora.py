import argparse
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.commentary.utils import (
    TRAINING_INSTALL_COMMAND,
    TRAINING_PACKAGES,
    read_jsonl,
    require_packages,
    validate_chat_rows,
)


def as_token_id_list(encoded: Any) -> List[int]:
    if isinstance(encoded, dict) or (hasattr(encoded, "keys") and "input_ids" in encoded.keys()):
        encoded = encoded["input_ids"]
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if encoded and isinstance(encoded[0], list):
        encoded = encoded[0]
    return [int(token_id) for token_id in encoded]


def apply_chat_template_ids(tokenizer, messages: Sequence[Dict[str, str]], add_generation_prompt: bool) -> List[int]:
    encoded = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=add_generation_prompt,
        tokenize=True,
    )
    return as_token_id_list(encoded)


def build_tokenize_fn(tokenizer, max_seq_length: int):
    def tokenize(example: Dict[str, Any]) -> Dict[str, Any]:
        messages = example["messages"]
        prompt_messages = messages[:2]

        prompt_ids = apply_chat_template_ids(tokenizer, prompt_messages, add_generation_prompt=True)
        full_ids = apply_chat_template_ids(tokenizer, messages, add_generation_prompt=False)

        original_full_len = len(full_ids)
        if original_full_len > max_seq_length:
            removed_tokens = original_full_len - max_seq_length
            full_ids = full_ids[-max_seq_length:]
            prompt_len = min(max(0, len(prompt_ids) - removed_tokens), len(full_ids))
        else:
            prompt_len = min(len(prompt_ids), len(full_ids))

        labels = [-100] * prompt_len + full_ids[prompt_len:]
        labels = labels[: len(full_ids)]
        attention_mask = [1] * len(full_ids)

        return {
            "input_ids": full_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }

    return tokenize


class DataCollatorForCausalLM:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def __call__(self, features: Sequence[Dict[str, List[int]]]) -> Dict[str, Any]:
        import torch

        max_len = max(len(feature["input_ids"]) for feature in features)
        pad_id = self.tokenizer.pad_token_id
        batch_input_ids = []
        batch_attention_mask = []
        batch_labels = []

        for feature in features:
            pad_len = max_len - len(feature["input_ids"])
            batch_input_ids.append(feature["input_ids"] + [pad_id] * pad_len)
            batch_attention_mask.append(feature["attention_mask"] + [0] * pad_len)
            batch_labels.append(feature["labels"] + [-100] * pad_len)

        return {
            "input_ids": torch.tensor(batch_input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(batch_attention_mask, dtype=torch.long),
            "labels": torch.tensor(batch_labels, dtype=torch.long),
        }


class TokenizedChatDataset:
    def __init__(self, rows: Sequence[Dict[str, Any]], tokenizer, max_seq_length: int, name: str):
        self.features: List[Dict[str, List[int]]] = []
        tokenize = build_tokenize_fn(tokenizer, max_seq_length)
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            self.features.append(tokenize({"messages": row["messages"]}))
            if index == total or index % 1000 == 0:
                print(json.dumps(
                    {
                        "tokenize_progress": {
                            "dataset": name,
                            "rows": index,
                            "total": total,
                        }
                    },
                    ensure_ascii=False,
                ), flush=True)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, index: int) -> Dict[str, List[int]]:
        return self.features[index]


def print_data_report(
    train_rows: Sequence[Dict[str, Any]],
    eval_rows: Sequence[Dict[str, Any]],
    train_path: Path,
    valid_path: Path,
) -> None:
    print(json.dumps(
        {
            "train": validate_chat_rows(train_rows, train_path),
            "valid": validate_chat_rows(eval_rows, valid_path),
        },
        ensure_ascii=False,
        indent=2,
    ))


def make_training_arguments(args):
    from transformers import TrainingArguments

    kwargs = {
        "output_dir": args.output_dir,
        "num_train_epochs": args.num_train_epochs,
        "learning_rate": args.learning_rate,
        "lr_scheduler_type": args.lr_scheduler_type,
        "warmup_ratio": args.warmup_ratio,
        "weight_decay": args.weight_decay,
        "per_device_train_batch_size": args.per_device_train_batch_size,
        "per_device_eval_batch_size": args.per_device_eval_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "logging_steps": args.logging_steps,
        "save_steps": args.save_steps,
        "eval_steps": args.eval_steps,
        "save_total_limit": args.save_total_limit,
        "max_steps": args.max_steps,
        "bf16": args.bf16,
        "fp16": args.fp16,
        "use_cpu": args.use_cpu,
        "gradient_checkpointing": args.gradient_checkpointing,
        "report_to": args.report_to,
        "remove_unused_columns": False,
    }

    try:
        return TrainingArguments(evaluation_strategy="steps", **kwargs)
    except TypeError:
        return TrainingArguments(eval_strategy="steps", **kwargs)


def load_tokenizer(args: argparse.Namespace):
    print_stage("importing_auto_tokenizer")
    from transformers import AutoTokenizer

    print(json.dumps(
        {
            "tokenizer_load": {
                "model": args.model_name_or_path,
                "use_fast": args.use_fast_tokenizer,
            }
        },
        ensure_ascii=False,
    ), flush=True)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name_or_path,
        trust_remote_code=True,
        use_fast=args.use_fast_tokenizer,
    )
    print_stage("tokenizer_loaded")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    return tokenizer


def build_quantization_config(args: argparse.Namespace):
    import torch
    from transformers import BitsAndBytesConfig

    if not args.load_in_4bit:
        return None
    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        bnb_4bit_use_double_quant=True,
        llm_int8_enable_fp32_cpu_offload=args.bnb_cpu_offload,
    )


def parse_max_memory(value: Optional[str]) -> Optional[Dict[Any, str]]:
    if not value:
        return None

    max_memory: Dict[Any, str] = {}
    for item in value.split(","):
        if "=" not in item:
            raise ValueError(
                "--max-memory must use comma-separated KEY=VALUE pairs, "
                "for example: 0=8GiB,cpu=48GiB"
            )
        key, memory = item.split("=", 1)
        key = key.strip()
        memory = memory.strip()
        max_memory[int(key) if key.isdigit() else key] = memory
    return max_memory


def print_cuda_report() -> None:
    try:
        import torch
    except ImportError:
        return

    report: Dict[str, Any] = {
        "cuda_available": torch.cuda.is_available(),
        "cuda_device_count": torch.cuda.device_count(),
    }
    if torch.cuda.is_available():
        device = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(device)
        report.update(
            {
                "cuda_device": device,
                "cuda_name": props.name,
                "cuda_total_gb": round(props.total_memory / 1024**3, 2),
            }
        )
    print(json.dumps({"cuda": report}, ensure_ascii=False, indent=2))


def preflight_training_device(args: argparse.Namespace) -> None:
    import torch

    if not torch.cuda.is_available():
        if not args.use_cpu:
            raise SystemExit(
                "PyTorch does not see a CUDA GPU, so training would run on CPU and be unusably slow.\n\n"
                "Fix this before training:\n"
                "1. Check GPU visibility:\n"
                "   python -c \"import torch; print(torch.cuda.is_available()); print(torch.version.cuda); "
                "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')\"\n"
                "2. If it prints False, install a CUDA-enabled PyTorch build in this environment.\n"
                "   Example for CUDA 12.1:\n"
                "   pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121\n"
                "3. Then rerun the training command.\n\n"
                "CPU training is not recommended for Qwen2.5-3B/7B. If you only want to force a tiny CPU debug run, "
                "add --use-cpu --no-load-in-4bit --no-bf16 --no-fp16."
            )

        print("Warning: --use-cpu enabled. This is only practical for tiny debug runs.")
        args.bf16 = False
        args.fp16 = False
        args.load_in_4bit = False
        args.device_map = None
        return

    if args.bf16 and not torch.cuda.is_bf16_supported():
        print("Warning: CUDA is available but bf16 is not supported. Switching from bf16 to fp16.")
        args.bf16 = False
        args.fp16 = True


def model_load_error_message(error: Exception, args: argparse.Namespace) -> str:
    return (
        "Failed to load the base model for QLoRA.\n\n"
        "Most likely cause: Qwen2.5-7B-Instruct in 4-bit does not fit fully in your GPU VRAM, "
        "so Transformers tried to dispatch some modules to CPU/disk.\n\n"
        "Recommended fixes:\n"
        "1. Use a smaller smoke-test model first:\n"
        "   python -m src.training.qwen_lora --model-name-or-path Qwen/Qwen2.5-3B-Instruct "
        "--max-train-samples 256 --max-eval-samples 64 --max-steps 20 "
        "--output-dir models/qwen_smoke_test_lora\n"
        "2. If you still want to try 7B with CPU offload, run:\n"
        "   python -m src.training.qwen_lora --bnb-cpu-offload --max-memory \"0=8GiB,cpu=48GiB\" "
        "--max-train-samples 256 --max-eval-samples 64 --max-steps 20 "
        "--output-dir models/qwen_smoke_test_lora\n"
        "   Adjust 8GiB/48GiB to match your machine. CPU offload can be very slow.\n"
        "3. Train 7B on a GPU with more VRAM, or reduce to Qwen2.5-3B/1.5B for local development.\n\n"
        f"Current model: {args.model_name_or_path}\n"
        f"Original error: {type(error).__name__}: {error}"
    )


def load_base_model(args: argparse.Namespace):
    import torch
    from transformers import AutoModelForCausalLM

    dtype = torch.bfloat16 if args.bf16 else torch.float16 if args.fp16 else "auto"

    try:
        model = AutoModelForCausalLM.from_pretrained(
            args.model_name_or_path,
            quantization_config=build_quantization_config(args),
            torch_dtype=dtype,
            device_map=args.device_map,
            max_memory=parse_max_memory(args.max_memory),
            trust_remote_code=True,
        )
    except ValueError as exc:
        raise SystemExit(model_load_error_message(exc, args)) from exc
    model.config.use_cache = False
    return model


def attach_lora_adapter(model, args: argparse.Namespace):
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training

    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    if args.adapter_init_path:
        adapter_path = Path(args.adapter_init_path)
        if not adapter_path.exists():
            raise SystemExit(f"Adapter checkpoint not found: {adapter_path}")
        model = PeftModel.from_pretrained(model, adapter_path, is_trainable=True)
        model.print_trainable_parameters()
        return model

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=args.lora_target_modules.split(","),
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    return model


def tokenize_dataset(rows: Sequence[Dict[str, Any]], tokenizer, max_seq_length: int, name: str):
    return TokenizedChatDataset(rows, tokenizer, max_seq_length, name)


def print_run_header(args: argparse.Namespace) -> None:
    print(json.dumps(
        {
            "run": {
                "model": args.model_name_or_path,
                "output_dir": args.output_dir,
                "resume_from_checkpoint": args.resume_from_checkpoint,
                "adapter_init_path": args.adapter_init_path,
                "max_seq_length": args.max_seq_length,
                "gradient_accumulation_steps": args.gradient_accumulation_steps,
            }
        },
        ensure_ascii=False,
        indent=2,
    ), flush=True)


def print_tokenizer_report(tokenizer) -> None:
    print(json.dumps(
        {
            "tokenizer": {
                "class": type(tokenizer).__name__,
                "vocab_size": getattr(tokenizer, "vocab_size", None),
                "pad_token_id": tokenizer.pad_token_id,
                "eos_token_id": tokenizer.eos_token_id,
                "padding_side": tokenizer.padding_side,
            }
        },
        ensure_ascii=False,
        indent=2,
    ), flush=True)


def print_stage(name: str) -> None:
    print(json.dumps({"stage": name}, ensure_ascii=False), flush=True)


def write_training_logs(output_dir: str, log_history: Sequence[Dict[str, Any]]) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_path / "training_log.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in log_history:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    fieldnames = [
        "step",
        "epoch",
        "loss",
        "eval_loss",
        "learning_rate",
        "grad_norm",
        "eval_runtime",
        "eval_samples_per_second",
        "eval_steps_per_second",
    ]
    csv_path = output_path / "training_log.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fieldnames) + "\n")
        for row in log_history:
            values = [str(row.get(field, "")) for field in fieldnames]
            handle.write(",".join(values) + "\n")


def train(args: argparse.Namespace) -> None:
    print_run_header(args)
    if args.adapter_init_path and args.resume_from_checkpoint:
        raise SystemExit(
            "Use either --resume-from-checkpoint or --adapter-init-path, not both.\n"
            "--resume-from-checkpoint restores optimizer/scheduler state too.\n"
            "--adapter-init-path loads only LoRA adapter weights and starts a fresh optimizer."
        )

    print_stage("reading_data")
    train_path = Path(args.train_file)
    valid_path = Path(args.valid_file)
    train_rows = read_jsonl(train_path, args.max_train_samples)
    eval_rows = read_jsonl(valid_path, args.max_eval_samples)
    print_data_report(train_rows, eval_rows, train_path, valid_path)
    print_cuda_report()
    preflight_training_device(args)

    print_stage("loading_tokenizer")
    tokenizer = load_tokenizer(args)
    print_stage("checking_dependencies")
    require_packages(TRAINING_PACKAGES, TRAINING_INSTALL_COMMAND)

    print_stage("importing_trainer")
    from transformers import Trainer

    print_stage("loading_model_and_lora")
    model = attach_lora_adapter(load_base_model(args), args)
    print_stage("tokenizing_train")
    train_dataset = tokenize_dataset(train_rows, tokenizer, args.max_seq_length, "train")
    print_stage("tokenizing_valid")
    eval_dataset = tokenize_dataset(eval_rows, tokenizer, args.max_seq_length, "valid")

    print_stage("building_trainer")
    trainer_kwargs = {
        "model": model,
        "args": make_training_arguments(args),
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
        "data_collator": DataCollatorForCausalLM(tokenizer),
    }
    trainer_params = inspect.signature(Trainer.__init__).parameters
    if "processing_class" in trainer_params:
        trainer_kwargs["processing_class"] = tokenizer
    elif "tokenizer" in trainer_params:
        trainer_kwargs["tokenizer"] = tokenizer

    trainer = Trainer(**trainer_kwargs)
    print_stage("training")
    trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
    print_stage("saving_model")
    trainer.save_model(args.output_dir)
    trainer.save_state()
    write_training_logs(args.output_dir, trainer.state.log_history)
    tokenizer.save_pretrained(args.output_dir)
    print_stage("done")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fine-tune Qwen2.5 Instruct for per-move chess commentary.")
    parser.add_argument("--model-name-or-path", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--train-file", default="data/sft/qwen_sft_train.jsonl")
    parser.add_argument("--valid-file", default="data/sft/qwen_sft_valid.jsonl")
    parser.add_argument("--output-dir", default="models/qwen2_5_7b_chess_commentator_lora")
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--max-train-samples", type=int, default=None)
    parser.add_argument("--max-eval-samples", type=int, default=None)
    parser.add_argument("--validate-data-only", action="store_true")
    parser.add_argument("--check-tokenizer-only", action="store_true")
    parser.add_argument("--use-fast-tokenizer", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--resume-from-checkpoint",
        default=None,
        help="Resume training from a Trainer checkpoint directory, e.g. models/.../checkpoint-1800.",
    )
    parser.add_argument(
        "--adapter-init-path",
        default=None,
        help="Initialize trainable LoRA weights from an adapter checkpoint without loading optimizer/scheduler state.",
    )

    parser.add_argument("--num-train-epochs", type=float, default=2.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lr-scheduler-type", default="cosine")
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--per-device-eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--logging-steps", type=int, default=20)
    parser.add_argument("--eval-steps", type=int, default=300)
    parser.add_argument("--save-steps", type=int, default=300)
    parser.add_argument("--save-total-limit", type=int, default=2)

    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument(
        "--lora-target-modules",
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
    )
    parser.add_argument("--load-in-4bit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--bf16", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--gradient-checkpointing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument(
        "--use-cpu",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Force CPU training for tiny debug runs only. Not recommended for Qwen2.5-3B/7B.",
    )
    parser.add_argument(
        "--max-memory",
        default=None,
        help='Optional max memory map, e.g. "0=8GiB,cpu=48GiB".',
    )
    parser.add_argument(
        "--bnb-cpu-offload",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow bitsandbytes CPU offload for machines that cannot fit the quantized model fully on GPU.",
    )
    parser.add_argument("--report-to", default="none")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.validate_data_only:
        train_path = Path(args.train_file)
        valid_path = Path(args.valid_file)
        train_rows = read_jsonl(train_path, args.max_train_samples)
        valid_rows = read_jsonl(valid_path, args.max_eval_samples)
        print_data_report(train_rows, valid_rows, train_path, valid_path)
        return
    if args.check_tokenizer_only:
        print_run_header(args)
        tokenizer = load_tokenizer(args)
        print_tokenizer_report(tokenizer)
        return
    train(args)


if __name__ == "__main__":
    main()
