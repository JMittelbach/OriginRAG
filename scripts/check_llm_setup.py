from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.analysis.ollama_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    check_server,
    generate_text,
    list_models,
    model_available,
)


def main() -> int:
    args = parse_args()
    ok, error = check_server(base_url=args.base_url, timeout=args.timeout)
    base_url = args.base_url or DEFAULT_BASE_URL
    print("OriginRAG LLM setup check")
    print(f"- Ollama base URL: {base_url}")
    print(f"- Ollama reachable: {ok}")
    if error:
        print(f"- Error: {error}")
        print_next_steps(args.model)
        return 1

    models, model_error = list_models(base_url=args.base_url, timeout=args.timeout)
    if model_error:
        print(f"- Could not list models: {model_error}")
        print_next_steps(args.model)
        return 1
    print(f"- Installed models: {', '.join(models) if models else 'none'}")
    has_model = model_available(args.model, models)
    print(f"- Required model {args.model}: {'installed' if has_model else 'missing'}")
    if not has_model:
        print_next_steps(args.model)
        return 2

    if args.test_call:
        print("- Test call: loading the model and running a small generation...", flush=True)
        result = generate_text(
            "/no_think\nReply with exactly: OriginRAG local LLM OK",
            model=args.model,
            base_url=args.base_url,
            temperature=0.0,
            timeout=args.call_timeout,
            num_predict=256,
        )
        print(f"- Test call performed: {result.call_performed}")
        print(f"- Test call model: {result.model}")
        if result.error:
            print(f"- Test call error: {result.error}")
            return 3
        print(f"- Test call response: {result.text}")
        if "OriginRAG" not in str(result.text or ""):
            print("- Test call error: response did not contain expected health-check token")
            return 3
    else:
        print("- Test call: skipped (use --test-call to run one tiny prompt)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local Ollama/Qwen setup for OriginRAG.")
    parser.add_argument("--model", default="qwen3:30b")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--call-timeout", type=int, default=60)
    parser.add_argument("--test-call", action="store_true")
    return parser.parse_args()


def print_next_steps(model: str) -> None:
    print("")
    print("Next commands:")
    print("  ollama serve")
    print(f"  ollama pull {model}")


if __name__ == "__main__":
    raise SystemExit(main())
