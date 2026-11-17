# Model setup

## Choose models in the web app

Open **Models** in the chat header or Quill editor, or **Settings → Models & runtimes**,
to see downloaded and loaded models, select a model, download a catalogue model,
or run a short speed test under a model's **Details**. The compact **Overview** shows
installed models and a small set of suggestions. Hardware, runtime, access and
configured-default settings are under **Advanced**. Completed jobs disappear;
active downloads and errors remain visible. Filter **Installed** or
**Downloads** to see the full corresponding list. **Selected** means
chosen for future replies; **Installed** means weights are present in the configured
runtime; **Running** is reported by that runtime and includes GPU/CPU placement
when available. A configured default without installed weights is not shown as
active or ready. An offline runtime is reported as unknown, not proof of absence.
Opening the panel never downloads, loads or switches a model.

The panel detects RAM and supported GPU memory on the web-service host. Apple
Silicon uses one shared memory pool, never RAM plus a separate VRAM allowance.
Available-memory estimates are refreshed periodically. Fit labels reserve memory
for the system and estimate a range for one 16K-context request. Architecture,
quantisation, runtime version, other processes and context/concurrency can change
actual requirements. Unknown GPUs and remote Ollama servers receive no GPU-fit
guarantee; Colibri's streamed models are not assessed from download size.
Suggestions choose from a small general-purpose and coding shortlist, favouring
models with current memory headroom. They are candidates, not measured quality or
speed rankings. See the [Qwen3.5](https://ollama.com/library/qwen3.5) and
[Qwen coding](https://ollama.com/library/qwen3-coder) model information.

**Catalogue** searches the public Ollama catalogue as you type; an exact
`model:tag` or `namespace/model:tag` can also be entered. In the **Installed** view,
typing only filters local inventory and makes no catalogue request. Online search
sends only your search text, never documents or conversations. Results verify a
local-weight manifest and its size; this does not certify model quality, licence,
task suitability or compatibility with your installed Ollama version. Downloads
still need confirmation and the same account permissions as before. A changed
manifest for a newly discovered model requires a fresh search and confirmation.
Network failures leave installed models accessible. Cloud-only entries are not
offered as local downloads.
Quill also has a **Models** button on its start page. Inside **Project chat**,
the model selector can override the model for a single request without changing
the shared selection.

The catalogue includes the optional Ollama
[Qwen3-Coder 30B model](https://ollama.com/library/qwen3-coder:30b), a coding-focused
model with a download of approximately 19 GB, plus inference memory. It is not
required for prose rewriting or for the project-chat interface. Downloading and
using it still require an explicit choice; the existing Qwen default is retained.
Locally, no account is required. On a shared server, every signed-in user can
inspect, select and test installed models or restore the configured default.
Stopping app-managed runtimes and deleting unused downloads require administrator
or model-manager access. **Delete download** asks for confirmation and removes
only model weights, never papers or manuscripts. The active/default model,
loaded models and externally managed Colibri services are protected. Downloads
removed this way require downloading again; deletion cannot be undone. On a
shared Ollama instance, deletion also affects clients outside OriginRAG.
Hosts can restrict downloads to specific accounts with
`ORIGINRAG_MODEL_DOWNLOAD_USERS=username1,username2` in `.env`. When set,
only these accounts may start or cancel downloads, including accounts without
an administrator role. Other administrators cannot download. An empty value
disables shared-server downloads. Selecting or testing a model never downloads
missing weights; local single-user operation is unchanged.
Hosts may additionally allow named accounts to manage runtimes with
`ORIGINRAG_MODEL_MANAGE_USERS=username1,username2`. This does not grant general
administrator privileges or download permission.
**Model selection is shared across accounts**, not a per-user preference.
Changes apply to new OriginRAG chat requests on that host, including paper
summaries, and Quill proofreading. Terminal configuration is unchanged.

The selection survives a web-service restart. **Use configured default**
restores the existing `.env` configuration. Active requests prevent model
switches. Downloads run in the background and can be cancelled. Cancelling an
Ollama download disconnects this app's pull request; another client pulling
the same model may keep the download running.

Speed is measured on the host. Ollama reports **decode tokens/s**; Colibri
reports **whole-request tokens/s**, including prompt processing. These are
different measurements, not directly comparable rankings. Neither measures
the complete retrieval-and-answer workflow.

### GPU preference and CPU fallback

OriginRAG leaves device placement to its local Ollama server. Ollama prefers
supported GPUs and runs on the CPU using system RAM when no supported GPU is
available. GPU detection happens on the machine running Ollama, not in the
browser. OriginRAG does not force CPU-only inference or a fixed GPU layer count.

A GPU being present does not guarantee that a model and its context fit entirely
in VRAM. Depending on available memory, inference can use both GPU and CPU;
loading can also fail if resources are insufficient. Check `ollama ps` on the
model host to distinguish GPU, CPU and mixed placement. Leave supported GPU
backends visible in a production service that should use acceleration. Hiding
CUDA alone does not imply CPU-only execution: a Vulkan-enabled runtime may still
use the GPU through Vulkan.

Concurrent requests share model weights but require separate context memory.
Test the intended context length before increasing `OLLAMA_NUM_PARALLEL`, and
keep a bounded queue. Model-server parallelism alone does not enable concurrent
OriginRAG chat pipelines: the web application also coordinates turns and corpus
updates. See [Ollama's hardware guide](https://docs.ollama.com/gpu) and
[concurrency documentation](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

Some architectures have stricter runtime limits. Ollama 0.40.7 forces Qwen3.5
(`qwen35` / `qwen35moe`) to one processing slot even when
`OLLAMA_NUM_PARALLEL=2` is configured. Simultaneous requests are queued, not
generated in parallel. Extra free VRAM does not remove this compatibility limit;
check runtime logs before assuming that a parallelism setting took effect.

### Separate Ollama runtimes

A host can serve specific models through a separate local Ollama installation
without replacing its shared system service. Configure exact model names in
`.env`, for example:

```dotenv
ORIGINRAG_OLLAMA_MODEL_URLS='{"qwen3.5:35b":"http://127.0.0.1:11435"}'
```

Restart the web service after changing this setting. Models installed at the
mapped endpoint appear in **Models & runtimes** alongside the usual Ollama
models. Only the named models use that endpoint. A mapped model never silently
falls back to another server. These host-configured endpoints must use loopback
HTTP addresses and cannot be supplied by web users. Download permissions remain
unchanged. Install a compatible Ollama version and supervise its service
separately. Context length and simultaneous requests require GPU memory beyond
the model file size.

### Reuse a warm model across pipeline stages

Different context sizes can make Ollama recreate a model runner between query
preparation and answer generation. On a host with sufficient memory, an optional
per-model context floor lets these stages reuse the same runner:

```dotenv
ORIGINRAG_OLLAMA_RUNNER_CONTEXTS='{"http://127.0.0.1:11435":{"qwen3.5:35b":16384}}'
```

Use the exact model name and endpoint from your installation, and restart the
web service after changing `.env`. The setting only affects matching Ollama
HTTP requests. It never reduces a larger requested context, changes the model,
shortens the evidence, or skips answer checks. Colibri and CLI calls are
unaffected. Unset it to keep the usual per-stage context sizes.

Requests without an explicit context use the configured value. Choose a value
at least as large as the server's usual context: the client cannot discover and
preserve an unknown larger server default.

The floor also reserves that context for short requests, so test memory use on
the target host first. Cold loading, expired runners and genuinely larger
contexts can still require loading. This improves runner reuse, not the model's
decoding speed or support for parallel requests.

### Evidence and context budgets

Answer selection adapts to the configured model context instead of stopping at
twelve papers. It reserves room for instructions, conversation history, source
metadata, the answer and a possible repair. The web app uses this automatically;
`--trace-token-budget` sets an explicit evidence-text ceiling for CLI answers.
Automatic mode allows up to 8,000 estimated evidence tokens, subject to available
context. It does not increase the model's configured context or change the model.

Selection compares a wider, bounded candidate pool before deciding what fits.
The final evidence set must satisfy both text and serialized-context budgets,
and every selected report remains part of the origin audit. Token counts are
estimates; the fully rendered prompt is checked again before generation.
Oversized prompts fail explicitly rather than silently removing audited evidence.

The generic fallback has no eight-paper citation cap. It retains a citeable
excerpt from each selected paper; named-report and bounded-comparison answers
keep their narrower scope. This does not guarantee that retrieval found every
relevant study. Context accounting and omission counts are saved with each run.

### Optional Colibri runtime

[Colibri](https://github.com/JustVugg/colibri) streams experts of supported
mixture-of-experts models from storage. It is not a runtime for arbitrary
Ollama model files. A smaller expert cache reduces process memory but can
make generation slower; the operating system may also cache model files.

Install the optional runtime on the same machine as the web app:

```bash
python scripts/setup_colibri.py
```

This builds pinned Colibri sources and creates a separate Python environment
under the ignored `runtime/colibri` directory. It requires Git, Make, a C
compiler and internet access. On macOS, install the Xcode command-line tools
and `libomp` for multithreading. Model weights are downloaded only when you
confirm a download in the web panel.

The initial Colibri catalogue contains:

- **OLMoE 1B/7B:** approximately 4.2 GB download and 7.4 GB converted weights.
  Allow at least 15 GB free during setup. Its 4K context and limited answer
  quality make it a smoke-test model, not a recommended RAG default.
- **Qwen3-Coder 30B/A3B:** approximately 19.4 GB, a preconverted container.
  This integration configures a 16K context. The runtime remains experimental;
  evaluate your own evidence prompts before relying on this model.
- **GLM-5.3:** approximately 419 GB on disk, a Colibri INT4 conversion, with a
  16K context in this integration. Install this large checkpoint with Colibri's
  resumable, checksum-verifying downloader. The complete download manifest must
  match the pinned repository revision before OriginRAG offers it as installed.

Only complete, verified model installations appear as available. Colibri
starts on demand on a private loopback port with an internal API key. An
unused loaded Colibri process can be stopped from the panel after restoring
the default. OriginRAG does not unload other clients' Ollama models.

Oversized prompts and truncated Colibri answers produce explicit errors.
Evidence is not silently removed to fit a smaller context. Constrained JSON
tasks are not enabled for this Colibri adapter and return an explicit error.
Scientific evidence thresholds, retrieval and citation checks are unchanged.

Host configuration can override `ORIGINRAG_MODELS_DIR`,
`ORIGINRAG_COLIBRI_HOME` and `ORIGINRAG_COLIBRI_PYTHON`. These are administrator
settings, not user-supplied URLs or executable paths. Runtime files, weights
and the selected-model settings remain outside Git.

### Shared GLM service and direct API access

An optional loopback-only gateway lets OriginRAG and terminal/API clients use
one GLM process rather than loading separate copies. Its OpenAI-compatible
endpoints are `/v1/models` and `/v1/chat/completions` (streaming supported).
All endpoints, including `/health`, require the private API key.

Create a private JSON configuration outside Git:

```json
{
  "root": "/absolute/path/to/OriginRAG",
  "colibri": "/absolute/path/to/colibri",
  "models": "/absolute/path/to/models/colibri",
  "state": "/absolute/path/to/private/glm-service",
  "port": 8766,
  "gpu_policy": "auto",
  "idle_seconds": 120
}
```

Using the Python environment that contains OriginRAG:

```bash
python -m originrag.colibri_service --config /path/to/config.json start
python -m originrag.colibri_service --config /path/to/config.json status
python -m originrag.colibri_service --config /path/to/config.json chat
python -m originrag.colibri_service --config /path/to/config.json stop
```

`start` survives terminal disconnection; it is not an operating-system boot
service. The gateway loads GLM on the first request and unloads it after two
idle minutes. It admits one active generation and one waiting request, with
a 30-second queue wait. Direct chat keeps conversation history until exit.
Disconnecting or cancelling a client request cancels the corresponding upstream
generation. The shared service is stopped with its own `stop` command, not by
the web panel's app-managed-runtime control. A GLM speed test allows up to
30 minutes because cold starts and storage-backed generation can be slow.

For a CUDA build, automatic admission requires no foreign GPU compute jobs,
at most 5% GPU utilisation and at least 36 GB free VRAM. Dense layers are
offloaded too, with a 16 GB expert-cache budget and additional headroom for
dense weights, workspace and a reserve. Otherwise the same weights run on CPU,
with a 64 GB RAM budget and eight compute threads. These are engine budgets,
not hard OS resource limits. Weight files and filesystem caches consume
additional storage/memory. A GPU startup failure is retried once on CPU;
failed generations are not silently retried or switched to another model.

Availability checks are not GPU reservations. Other applications can start
after a check. There is no live CPU/GPU migration in the middle of an answer.
Between requests, foreign GPU compute jobs trigger unloading of our GPU
engine; the next startup checks availability again. Idle unloading releases
GPU allocations without stopping other users' processes.

Connect OriginRAG to the gateway through host-only `.env` settings:

```dotenv
ORIGINRAG_GLM_SERVICE_URL=http://127.0.0.1:8766
ORIGINRAG_GLM_API_KEY_FILE=/absolute/path/to/private/glm-service/api.key
```

Restart the web service, then choose **Settings → Models & runtimes → Colibri
→ GLM-5.3 → Use model**. The shared service controls its own memory budget;
the panel's per-process cache choice does not change that budget. Choosing a
model applies to new requests on the shared host, not only your account.
Selecting GLM does not change the configured Ollama default.

From another computer, forward the API over SSH instead of exposing it:

```bash
ssh -N -L 8766:127.0.0.1:8766 your-user@your-server
```

Use `http://127.0.0.1:8766/v1` as the client base URL, `glm-5.3` as the model,
and the service key from a private local file or secret store. Never commit,
paste into shared chat, or include the key in a URL. Requests may be slow while
weights load or stream from storage. No throughput is guaranteed.

## Configure the default

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
