# Model setup

OriginRAG uses [Ollama](https://ollama.com/) for local text generation.
Choose a model for your hardware, configure it in `.env`, and restart the app.

## Hardware guide

These starting estimates refer to total memory on an Apple Silicon Mac or
CPU-only machine, with one active answer and no other memory-heavy applications.
Actual requirements depend on context length and corpus size.

| Memory | Suggested starting model | Initial context |
| --- | --- | --- |
| 8 GB | Metadata search without generation | — |
| 16 GB | `qwen3:4b-q4_K_M` or `qwen3:8b-q4_K_M` | 8–16K |
| 24–32 GB | `qwen3:14b-q4_K_M` | 16K |
| 48–64 GB or more | `qwen3:30b-a3b-q4_K_M` or `qwen3:32b-q4_K_M` | 16–32K |

Model downloads require additional disk space; see
[Ollama's model sizes](https://ollama.com/library/qwen3/tags).
Download size does not represent runtime memory use.

On a GPU server, check VRAM separately from host RAM. CPU offload can be slower.
On Apple Silicon, leave unified memory available for the operating system
and corpus index.

Longer context and concurrent requests increase memory use. The default
65,536-token context may need reducing on smaller machines, but leave enough
room for instructions, evidence, conversation history and the answer.
See [Ollama's context guide](https://docs.ollama.com/context-length) and
[memory guidance](https://docs.ollama.com/faq).

## Configure a model

Download the model you chose, for example:

```bash
ollama pull qwen3:8b-q4_K_M
```

Replace these entries in `.env` (do not append duplicate keys):

```dotenv
ORIGINRAG_OLLAMA_MODEL=qwen3:8b-q4_K_M
ORIGINRAG_OLLAMA_PLANNER_MODEL=qwen3:8b-q4_K_M
ORIGINRAG_OLLAMA_ANSWER_MODEL=qwen3:8b-q4_K_M
ORIGINRAG_QWEN_MODEL=qwen3:8b-q4_K_M
ORIGINRAG_QUEUE_MODEL=qwen3:8b-q4_K_M
ORIGINRAG_OLLAMA_NUM_CTX=16384
```

Use the same model across these settings to avoid loading multiple models.
The default chat and web app read `ORIGINRAG_QWEN_MODEL`; set it even when
choosing another model family.

After using `setup-model --model ...` or `--balanced`, check these entries:
the helper preserves existing planner, answer-model and context overrides.

Restart your terminal chat or web service after editing `.env`. Already
exported environment variables take precedence over the file; remove stale
overrides if the running process still selects the old model.

```bash
./originrag doctor --model qwen3:8b-q4_K_M
./terminal
```

## Check your setup

Ask a representative question and run `ollama ps` in another terminal to inspect
the loaded model, context and CPU/GPU placement. Monitor memory pressure and
swap; use a smaller setup if swapping becomes heavy. On shared servers, do not
unload other users' models.

Answer quality varies between models. Check citations, extracted study details
and handling of missing evidence before relying on a new configuration.
`doctor --test-call` checks basic generation, not full-answer memory use.
For measured examples, see the
[recorded memory observations](getting_started.md#model-profiles-and-recorded-memory).

## Backend settings

In terminal chat, `/backend ollama` selects the generic Ollama configuration,
including models such as [Llama](https://ollama.com/library/llama3.1:8b) or
[Mistral](https://ollama.com/library/mistral:7b).

| Setting | Configuration |
| --- | --- |
| `ollama` | `ORIGINRAG_OLLAMA_ANSWER_MODEL` and the Ollama endpoint |
| `qwen` | `ORIGINRAG_QWEN_MODEL` and optional Qwen endpoint override |
| `deepseek` | `ORIGINRAG_DEEPSEEK_MODEL` and optional DeepSeek endpoint override |

All three connect to an Ollama API endpoint.
