#!/usr/bin/env python3
"""Phase 5b: fine-tune the Kronos tokenizer then predictor on our MT5 bars.

Reuses the vendor's training loops (optimizer, OneCycle schedule, validation,
checkpointing) and only replaces their dataloader factory, because their dataset
cannot handle more than one instrument. The substitution happens through the
module-level ``create_dataloaders`` hook both vendor scripts call.

Order matters and matches the paper's pipeline:
  1. tokenizer  - adapt the quantiser to forex bar distributions
  2. predictor  - adapt the transformer, using the fine-tuned tokenizer

Leakage: the corpus from phase5_corpus.py stops before the Phase 6 test period,
so nothing here sees the evaluation window.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase5_finetune.py --stage tokenizer
    .venv\\Scripts\\python.exe src\\phase5_finetune.py --stage predictor
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from multi_symbol_dataset import MultiSymbolKlineDataset  # noqa: E402

paths.ensure_dirs()
paths.add_kronos_to_syspath()
sys.path.insert(0, str(paths.VENDOR_KRONOS / "finetune_csv"))

UTC = timezone.utc
CORPUS_ROOT = paths.DATA / "finetune"

DEFAULTS = {
    "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
    "predictor": "NeoQuasar/Kronos-small",
}


def build_config(corpus_dir: Path, save_root: Path, args: argparse.Namespace) -> SimpleNamespace:
    """Config object exposing exactly the attributes the vendor loops read."""
    return SimpleNamespace(
        data_path=str(corpus_dir),
        lookback_window=args.lookback, predict_window=args.predict_window,
        max_context=args.lookback, clip=5.0,
        train_ratio=0.9, val_ratio=0.1, test_ratio=0.0,
        tokenizer_epochs=args.epochs, basemodel_epochs=args.epochs,
        batch_size=args.batch_size, log_interval=args.log_interval,
        num_workers=args.num_workers, seed=args.seed,
        tokenizer_learning_rate=args.tokenizer_lr,
        predictor_learning_rate=args.predictor_lr,
        adam_beta1=0.9, adam_beta2=0.95, adam_weight_decay=0.1,
        accumulation_steps=1, use_comet=False,
        exp_name=args.exp_name, base_save_path=str(save_root),
    )


def make_dataloader_factory(corpus_dir: Path, args: argparse.Namespace):
    """Return a drop-in replacement for the vendor's create_dataloaders."""
    from torch.utils.data import DataLoader

    def create_dataloaders(config):
        common = dict(
            corpus_dir=corpus_dir,
            lookback_window=config.lookback_window,
            predict_window=config.predict_window,
            clip=config.clip, train_ratio=config.train_ratio,
            val_ratio=config.val_ratio, test_ratio=config.test_ratio,
        )
        train_dataset = MultiSymbolKlineDataset(
            data_type="train", seed=config.seed,
            max_windows=args.max_train_windows, **common)
        val_dataset = MultiSymbolKlineDataset(
            data_type="val", seed=config.seed + 1,
            max_windows=args.max_val_windows, **common)
        train_loader = DataLoader(
            train_dataset, batch_size=config.batch_size, shuffle=True,
            num_workers=config.num_workers, pin_memory=True, drop_last=True)
        val_loader = DataLoader(
            val_dataset, batch_size=config.batch_size, shuffle=False,
            num_workers=config.num_workers, pin_memory=True, drop_last=False)
        return train_loader, val_loader, train_dataset, val_dataset, None, None

    return create_dataloaders


def run_tokenizer(corpus_dir: Path, save_root: Path, args: argparse.Namespace) -> Path:
    import finetune_tokenizer as vendor
    import torch
    from model import KronosTokenizer

    config = build_config(corpus_dir, save_root, args)
    vendor.create_dataloaders = make_dataloader_factory(corpus_dir, args)

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    print(f"loading pretrained tokenizer {args.pretrained_tokenizer}")
    tokenizer = KronosTokenizer.from_pretrained(args.pretrained_tokenizer).to(device)

    save_dir = save_root / args.exp_name / "tokenizer"
    save_dir.mkdir(parents=True, exist_ok=True)
    logger = vendor.setup_logging(args.exp_name, str(paths.LOGS / "finetune"), 0)
    vendor.train_tokenizer(tokenizer, device, config, str(save_dir), logger)
    return save_dir / "best_model"


def run_predictor(corpus_dir: Path, save_root: Path, args: argparse.Namespace,
                  tokenizer_path: str) -> Path:
    import finetune_base_model as vendor
    import torch
    from model import Kronos, KronosTokenizer

    config = build_config(corpus_dir, save_root, args)
    vendor.create_dataloaders = make_dataloader_factory(corpus_dir, args)

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    print(f"loading tokenizer  {tokenizer_path}")
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_path).to(device)
    tokenizer.eval()
    for parameter in tokenizer.parameters():
        parameter.requires_grad = False
    print(f"loading predictor  {args.pretrained_predictor}")
    model = Kronos.from_pretrained(args.pretrained_predictor).to(device)

    save_dir = save_root / args.exp_name / "basemodel"
    save_dir.mkdir(parents=True, exist_ok=True)
    logger = vendor.setup_logging(args.exp_name, str(paths.LOGS / "finetune"), 0)
    vendor.train_model(model, tokenizer, device, config, str(save_dir), logger)
    return save_dir / "best_model"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("tokenizer", "predictor", "both"), default="both")
    parser.add_argument("--timeframe", default="5m", choices=sorted(paths.TF_SECONDS))
    parser.add_argument("--exp-name", default="s146_5m")
    parser.add_argument("--pretrained-tokenizer", default=DEFAULTS["tokenizer"])
    parser.add_argument("--pretrained-predictor", default=DEFAULTS["predictor"])
    parser.add_argument("--finetuned-tokenizer", default=None,
                        help="tokenizer to use for the predictor stage")
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--predict-window", type=int, default=96)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--tokenizer-lr", type=float, default=2e-4)
    parser.add_argument("--predictor-lr", type=float, default=4e-5)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--log-interval", type=int, default=50)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-train-windows", type=int, default=40_000,
                        help="cap training windows per epoch to keep runs bounded")
    parser.add_argument("--max-val-windows", type=int, default=4_000)
    args = parser.parse_args(argv)

    corpus_dir = CORPUS_ROOT / args.timeframe
    if not (corpus_dir / "corpus.json").is_file():
        raise SystemExit(f"corpus missing: {corpus_dir}. Run phase5_corpus.py first.")
    save_root = paths.MODELS
    record: dict[str, Any] = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase5b_finetune", "stage": args.stage,
        "timeframe": args.timeframe, "corpus": str(corpus_dir),
        "settings": {key: value for key, value in vars(args).items()},
    }

    tokenizer_path = args.finetuned_tokenizer
    if args.stage in ("tokenizer", "both"):
        produced = run_tokenizer(corpus_dir, save_root, args)
        record["tokenizer_checkpoint"] = str(produced)
        tokenizer_path = str(produced)
        print(f"\ntokenizer checkpoint: {produced}")
    if args.stage in ("predictor", "both"):
        if not tokenizer_path:
            tokenizer_path = str(save_root / args.exp_name / "tokenizer" / "best_model")
        if not Path(tokenizer_path).is_dir():
            raise SystemExit(f"fine-tuned tokenizer not found: {tokenizer_path}")
        produced = run_predictor(corpus_dir, save_root, args, tokenizer_path)
        record["predictor_checkpoint"] = str(produced)
        print(f"\npredictor checkpoint: {produced}")

    report = paths.OUT / f"phase5_finetune_{args.exp_name}_{args.stage}.json"
    report.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"Record: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
