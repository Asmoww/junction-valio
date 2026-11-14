"""Fine-tune an open Gemma model into the gateway's request reader, on a Verda GPU instance.

Distillation: the model learns Claude's readings (scripts/reader_data.py) of ~700 kitchen requests, so a small
open model hosted in Europe can do the gateway's reading without sending request text to a US API.

On a Verda GPU instance (one H100 or A100 is plenty for a 4B model; ~10 minutes):

  pip install "transformers>=4.50" "trl>=0.15" "peft>=0.14" datasets accelerate
  huggingface-cli login            # Gemma weights need the licence accepted on Hugging Face
  python scripts/finetune_reader.py --model google/gemma-3-4b-it --out reader-lora

Serve it (OpenAI-compatible, structured output) and point the gateway at it:

  pip install vllm
  vllm serve google/gemma-3-4b-it --enable-lora --lora-modules reader=reader-lora --max-model-len 4096
  # on the gateway: READER=eu EU_LLM_BASE_URL=http://<instance>:8000/v1 EU_LLM_MODEL=reader \\
  #                 EU_LLM_LABEL="Gemma on Verda"
  python scripts/reader_bench.py   # compare with Claude on the held-out test set

Without fine-tuning, the same commands serve the base model (EU_LLM_MODEL=google/gemma-3-4b-it): benchmark both.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemma-3-4b-it")
    ap.add_argument("--out", default="reader-lora")
    ap.add_argument("--epochs", type=float, default=3)
    args = ap.parse_args()

    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(line) for line in open(ROOT / "data" / "reader" / "train.jsonl")]
    human = ROOT / "data" / "reader" / "human.jsonl"  # what people taught (scripts/export_learning_data.py)
    if human.exists():
        taught = [json.loads(line) for line in open(human)]
        rows += taught * 3  # few but real: weigh them up
        print(f"+ {len(taught)} human-taught examples (x3)")
    data = Dataset.from_list([{"messages": r["messages"]} for r in rows])
    trainer = SFTTrainer(
        model=args.model,
        train_dataset=data,
        peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules="all-linear", task_type="CAUSAL_LM"),
        args=SFTConfig(output_dir=args.out, num_train_epochs=args.epochs, per_device_train_batch_size=8,
                       gradient_accumulation_steps=2, learning_rate=2e-4, lr_scheduler_type="cosine", warmup_ratio=0.05,
                       logging_steps=10, save_strategy="no", bf16=True, max_length=2048,
                       assistant_only_loss=False, report_to=[]),
    )
    trainer.train()
    trainer.save_model(args.out)
    print(f"LoRA adapter saved to {args.out}: serve it with vLLM (see the docstring)")


if __name__ == "__main__":
    main()
