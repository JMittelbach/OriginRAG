from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from originrag.analysis.llm_backends import clean_ollama_cli_output  # noqa: E402
from originrag.cancellation import Cancellation, TurnCancelled, cancellation_scope, current_cancellation, run_process, urlopen  # noqa: E402


def main() -> int:
    args = parse_args()
    queue_dir = resolve_repo_path(args.queue_dir)
    jobs_dir = queue_dir / "jobs"
    results_dir = queue_dir / "results"
    completed_dir = queue_dir / "completed"
    failed_dir = queue_dir / "failed"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    completed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    print(f"OriginRAG Ollama worker watching {jobs_dir}")
    while True:
        processed = process_once(
            jobs_dir=jobs_dir,
            results_dir=results_dir,
            completed_dir=completed_dir,
            failed_dir=failed_dir,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        )
        if args.once:
            return 0 if processed else 2
        time.sleep(args.poll_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process OriginRAG LLM queue jobs with local Ollama.")
    parser.add_argument("--queue-dir", default="outputs/llm_queue")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def process_once(
    jobs_dir: Path,
    results_dir: Path,
    completed_dir: Path,
    failed_dir: Path,
    base_url: str,
    timeout_seconds: int,
) -> bool:
    def modification_time(path):
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return float('inf')

    job_paths = sorted(jobs_dir.glob("*.json"), key=modification_time)
    for job_path in job_paths:
        try:
            job = read_json(job_path)
        except FileNotFoundError:
            continue
        job_id = str(job.get("job_id") or job_path.stem)
        result_path = results_dir / f"{job_id}.json"
        if result_path.exists():
            archive_job(job_path, completed_dir / f"{job_id}.done.json")
            continue
        running_path = job_path.with_suffix(".running")
        try:
            job_path.replace(running_path)
        except FileNotFoundError:
            continue
        print(f"Processing {job_id} with {job.get('model')}")
        token = Cancellation()
        watcher_done = threading.Event()
        cancel_path = job_path.with_suffix('.cancel')

        def watch_cancel():
            while not watcher_done.is_set():
                if cancel_path.exists():
                    token.cancel()
                    return
                watcher_done.wait(0.1)

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            if cancel_path.exists():
                token.cancel()
            with cancellation_scope(token):
                result = run_ollama_job(job, base_url=base_url, timeout_seconds=timeout_seconds)
                token.commit()
        except TurnCancelled:
            result = {"job_id": job_id, "answer": None, "error": "cancelled", "status": "cancelled"}
        finally:
            watcher_done.set()
            watcher.join(timeout=1)
        write_json_atomic(result_path, result)
        status = "ok" if result.get("answer") else "failed"
        archive_dir = completed_dir if result.get("answer") else failed_dir
        archive_job(running_path, archive_dir / f"{job_id}.done.json")
        print(f"Finished {job_id}: {status}")
        return True
    return False


def run_ollama_job(job: dict, base_url: str, timeout_seconds: int) -> dict:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    job_id = str(job.get("job_id") or "")
    model = str(job.get("model") or "qwen3:30b")
    prompt = str(job.get("prompt") or "")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": llm_num_ctx(), "num_predict": llm_num_predict()},
    }
    try:
        response = post_json(
            f"{base_url.rstrip('/')}/api/generate",
            payload,
            timeout_seconds=timeout_seconds,
        )
        answer = str(response.get("response") or "").strip()
        return {
            "job_id": job_id,
            "model": model,
            "started_at": started_at,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "answer": clean_ollama_cli_output(answer),
            "error": None if answer else "ollama returned no response",
        }
    except Exception as exc:
        cli_answer, cli_error = run_ollama_cli(
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        if cli_answer:
            return {
                "job_id": job_id,
                "model": model,
                "started_at": started_at,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "answer": cli_answer,
                "error": f"http_fallback_used: {exc}",
            }
        return {
            "job_id": job_id,
            "model": model,
            "started_at": started_at,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "answer": None,
            "error": f"{exc}; cli fallback failed: {cli_error}",
        }


def post_json(url: str, payload: dict, timeout_seconds: int) -> dict:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def run_ollama_cli(prompt: str, model: str, timeout_seconds: int) -> tuple[str | None, str | None]:
    try:
        completed = (run_process if current_cancellation.get() else subprocess.run)(
            ["ollama", "run", model],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return None, str(exc)
    answer = clean_ollama_cli_output(completed.stdout)
    if completed.returncode != 0:
        return answer or None, clean_ollama_cli_output(completed.stderr)
    return answer or None, None


def llm_num_predict() -> int:
    raw_value = (
        os.environ.get("PFEMPIRE_OLLAMA_NUM_PREDICT")
        or os.environ.get("HYPORAG_OLLAMA_NUM_PREDICT")
        or "2200"
    )
    try:
        num_predict = int(raw_value)
    except ValueError:
        return 2200
    return max(num_predict, 256)


def llm_num_ctx() -> int:
    raw_value = (
        os.environ.get("PFEMPIRE_OLLAMA_NUM_CTX")
        or os.environ.get("HYPORAG_OLLAMA_NUM_CTX")
        or "65536"
    )
    try:
        num_ctx = int(raw_value)
    except ValueError:
        return 65536
    return max(num_ctx, 2048)


def archive_job(source: Path, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
    except FileNotFoundError:
        return


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
