from typing import Optional

from src.commentary_utils import TRAINING_INSTALL_COMMAND
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
    input_ids = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
    ).to(model.device)

    generation_kwargs = {
        "input_ids": input_ids,
        "max_new_tokens": max_new_tokens,
        "do_sample": temperature > 0,
        "pad_token_id": tokenizer.eos_token_id,
    }
    if temperature > 0:
        generation_kwargs["temperature"] = temperature

    with torch.no_grad():
        output_ids = model.generate(**generation_kwargs)

    generated_ids = output_ids[0, input_ids.shape[-1] :]
    return tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
