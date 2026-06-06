from typing import Optional

from src.commentary.utils import TRAINING_INSTALL_COMMAND
from src.data.data_pipeline import SYSTEM_PROMPT


def load_tokenizer_and_model(model_name_or_path: str, adapter_path: Optional[str]):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "Missing inference dependencies. Install them with: "
            f"{TRAINING_INSTALL_COMMAND}"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else "auto",
        device_map="auto",
        trust_remote_code=True,
    )

    if adapter_path:
        try:
            from peft import PeftModel
        except ImportError as exc:
            raise SystemExit("peft is required to load a LoRA adapter.") from exc
        model = PeftModel.from_pretrained(model, adapter_path)

    model.eval()
    return tokenizer, model


def generate_commentary_text(
    tokenizer,
    model,
    user_prompt: str,
    max_new_tokens: int = 80,
    temperature: float = 0.2,
) -> str:
    import torch

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    encoded = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_tensors="pt",
    )
    if isinstance(encoded, dict) or (hasattr(encoded, "keys") and "input_ids" in encoded.keys()):
        input_ids = encoded["input_ids"]
        attention_mask = encoded.get("attention_mask")
    else:
        input_ids = encoded
        attention_mask = None

    if not hasattr(input_ids, "to"):
        input_ids = torch.tensor(input_ids, dtype=torch.long)
    if input_ids.ndim == 1:
        input_ids = input_ids.unsqueeze(0)

    device = getattr(model, "device", None)
    if device is None:
        device = next(model.parameters()).device
    input_ids = input_ids.to(device)
    if attention_mask is not None:
        if not hasattr(attention_mask, "to"):
            attention_mask = torch.tensor(attention_mask, dtype=torch.long)
        if attention_mask.ndim == 1:
            attention_mask = attention_mask.unsqueeze(0)
        attention_mask = attention_mask.to(device)

    generation_kwargs = {
        "input_ids": input_ids,
        "max_new_tokens": max_new_tokens,
        "do_sample": temperature > 0,
        "pad_token_id": tokenizer.eos_token_id,
    }
    if attention_mask is not None:
        generation_kwargs["attention_mask"] = attention_mask
    if temperature > 0:
        generation_kwargs["temperature"] = temperature

    with torch.no_grad():
        output_ids = model.generate(**generation_kwargs)

    generated_ids = output_ids[0, input_ids.shape[-1] :]
    return tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
