# Natural Language Interface for 5G Testbed
This repository implements a **Natural Language Query Interface** for a 5G testbed, enabling users to interact with network analytics and control functions using conversational queries.  
The system translates natural language into analytics queries and network commands, bridging users and 5G monitoring components.

## Components
### LLM1.py — Intent Processing & Orchestration
    • Converts user queries into semantic embeddings
    • Matches queries to predefined 5G analytics and control intents (RAG-style)
    • Dispatches actions to:
        ◦ NWDAF (AMF/SMF subscribe & unsubscribe)
        ◦ Prometheus analytics queries
    • Uses an LLM to interpret and verbalize query results
### prom_query.py — Prometheus Analytics Backend
    • Queries Prometheus for NWDAF-exposed metrics
    • Supported analytics include:
        ◦ Active UE count
        ◦ UE location change reports
        ◦ AMF UE registration state
        ◦ UE destination/visit patterns
    • Cleans, deduplicates, and formats results for LLM consumption
## Supported Capabilities
    • Natural language queries over live 5G network metrics
    • Event-driven analytics via NWDAF subscriptions
    • Unified interface for control-plane events and performance metrics
## Typical Flow
    1. User submits a natural language query
    2. Query is matched to an intent using embeddings
    3. Corresponding NWDAF or Prometheus action is executed
    4. Results are summarized in natural language

## RAG/LLM setup on Ubuntu 22.04

The feature branch uses Python 3.10 or newer, Ollama, and the dependencies in
`requirements.txt`. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install and start Ollama separately, then download the models configured in
`LLM1.py` (`nomic-embed-text` and `qwen3.5:4b`). To use a different Ollama
host, set `OLLAMA_BASE_URL`. Set `PROMETHEUS_URL` (default
`http://127.0.0.1:9090`) and optionally `PROMETHEUS_QUERY_WINDOW_MINUTES`
(default `60`) for analytics. Run the RAG interface with:

```bash
source .venv/bin/activate
python LLM1.py
```

`setup.sh` targets Ubuntu 22.04 and installs the repository's Python packages
in `.venv` alongside the existing 5G testbed dependencies. It installs system
packages and configures services, so run it on a machine intended for the full
testbed by running `bash setup.sh`. The Python RAG/LLM interface can be
installed with the commands above without running that full system installer.

Intent routing uses top candidates, minimum similarity and score margin checks,
and an explicit allowlist before executing backend actions. The embedding cache
is regenerated when the prompt file, intent definitions, or embedding model
changes. Analytics responses include the Prometheus source, query time range,
and scrape target health. Empty data and backend failures are reported without
asking the LLM to infer missing evidence.

`visualize.py` builds charts only from successful backend result envelopes and
includes source and query time. The legacy mobility animation scripts use
invented coordinates and must be described as simulations, not geographic
measurements. To query an allowlisted metric and build its chart from the live
backend, call `visualize.query_and_plot(metric)` from Python; the returned
Matplotlib figure can be displayed or saved by the caller.

The RAG interface also supports UE registration failure triage. It scans recent
UE, Free5GC, and NWDAF log files for registration-related error evidence, masks
SUPI-like identifiers, and includes AMF/NWDAF scrape health. It reports missing
or conflicting evidence for follow-up and does not claim a root cause from log
patterns alone.
