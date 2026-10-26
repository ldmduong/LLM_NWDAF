import json
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import requests
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langgraph.graph import END, START, StateGraph
from sklearn.metrics.pairwise import cosine_similarity

from prom_query import query_prometheus
from diagnostics import collect_registration_failure_evidence


BASE_DIR = Path(__file__).resolve().parent
INTENT_FILE = BASE_DIR / "intent prompts.txt"
EMBEDDINGS_CACHE = BASE_DIR / "intent_embeddings_ollama.npz"
NWDAF_URL = "http://127.0.0.71:8001/nwdaf/command"
EMBEDDING_MODEL_NAME = "nomic-embed-text"
CHAT_MODEL_NAME = "qwen3.5:4b"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
BATCH_SIZE = 32
MIN_ACTION_SCORE = 0.55
MIN_ANALYTICS_SCORE = 0.45
MIN_SCORE_MARGIN = 0.04

embedding_model = OllamaEmbeddings(
    model=EMBEDDING_MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
)
chat_model = ChatOllama(
    model=CHAT_MODEL_NAME,
    temperature=0,
    base_url=OLLAMA_BASE_URL,
)

INTENT_DEFINITIONS = {
    "AMF Subscribe": {
        "description": "Tạo subscription để NWDAF nhận sự kiện từ AMF.",
        "required_parameters": ["action", "target"],
        "tool": "nwdaf_subscription_command",
        "action": "subscribe",
        "target": "amf",
        "contrast_examples": ["Hủy subscription sự kiện AMF"],
    },
    "AMF Unsubscribe": {
        "description": "Hủy subscription đang hoạt động nhận sự kiện từ AMF.",
        "required_parameters": ["action", "target"],
        "tool": "nwdaf_subscription_command",
        "action": "unsubscribe",
        "target": "amf",
        "contrast_examples": ["Tạo subscription sự kiện AMF"],
    },
    "SMF Subscribe": {
        "description": "Tạo subscription để NWDAF nhận sự kiện từ SMF.",
        "required_parameters": ["action", "target"],
        "tool": "nwdaf_subscription_command",
        "action": "subscribe",
        "target": "smf",
        "contrast_examples": ["Hủy subscription sự kiện SMF"],
    },
    "SMF Unsubscribe": {
        "description": "Hủy subscription đang hoạt động nhận sự kiện từ SMF.",
        "required_parameters": ["action", "target"],
        "tool": "nwdaf_subscription_command",
        "action": "unsubscribe",
        "target": "smf",
        "contrast_examples": ["Tạo subscription sự kiện SMF"],
    },
    "Active_UEs": {
        "description": "Truy vấn số lượng UE đang hoạt động.",
        "required_parameters": ["metric"],
        "tool": "query_prometheus",
        "metric": "active_UEs",
        "contrast_examples": [],
    },
    "UE_location_report": {
        "description": "Truy vấn báo cáo vị trí hoặc thay đổi cell của UE.",
        "required_parameters": ["metric"],
        "tool": "query_prometheus",
        "metric": "UE_location_report",
        "contrast_examples": [],
    },
    "Registration State": {
        "description": "Truy vấn trạng thái đăng ký UE tại AMF.",
        "required_parameters": ["metric"],
        "tool": "query_prometheus",
        "metric": "amf_ue_registration_state",
        "contrast_examples": [],
    },
    "Destination Visits": {
        "description": "Truy vấn các lượt ghé điểm đến mô phỏng của UE.",
        "required_parameters": ["metric"],
        "tool": "query_prometheus",
        "metric": "ue_destination_visits_total",
        "contrast_examples": [],
    },
    "Registration Failure Diagnosis": {
        "description": "Tìm bằng chứng có thời gian liên quan khi UE đăng ký thất bại.",
        "required_parameters": [],
        "tool": "diagnose_registration_failure",
        "contrast_examples": [],
    },
}

ALLOWED_NWDAF_COMMANDS = {
    (definition["action"], definition["target"])
    for definition in INTENT_DEFINITIONS.values()
    if definition["tool"] == "nwdaf_subscription_command"
}
ALLOWED_TOOLS = {
    "nwdaf_subscription_command",
    "query_prometheus",
    "diagnose_registration_failure",
}

NEGATION_PATTERN = re.compile(
    r"\b(?:don't|do not|never|not|no)\b|(?:không|đừng|chưa|chẳng)",
    re.IGNORECASE,
)
MULTI_REQUEST_PATTERN = re.compile(
    r"\b(?:and|also|then)\b|;|(?:đồng thời|và|cũng|sau đó)",
    re.IGNORECASE,
)

all_intents: list[str] = []
all_categories: list[str] = []
intent_embeddings: np.ndarray | None = None


class QueryState(TypedDict, total=False):
    user_query: str
    top_matches: list[tuple[str, str, float]]
    best_intent: str
    category: str
    score: float
    action: str
    target: str
    metric: str
    function_result: Any
    answer: str
    status: str


def load_intent_prompts(file_path: Path) -> dict[str, list[str]]:
    intent_categories: dict[str, list[str]] = {}
    category = None

    with file_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line.startswith("##"):
                category = line.lstrip("#").strip()
                intent_categories[category] = []
            elif category and line:
                intent_categories[category].append(line.split(". ", 1)[-1])

    if not intent_categories or not any(intent_categories.values()):
        raise ValueError(f"No intents found in {file_path}")
    return intent_categories


def prepare_intent_embeddings() -> None:
    global all_intents, all_categories, intent_embeddings

    categories = load_intent_prompts(INTENT_FILE)
    all_intents = []
    all_categories = []

    for category, examples in categories.items():
        if category not in INTENT_DEFINITIONS:
            raise ValueError(f"Intent category has no definition: {category}")
        for example in examples:
            all_intents.append(example)
            all_categories.append(category)

    metadata = json.dumps(INTENT_DEFINITIONS, ensure_ascii=False, sort_keys=True)
    signature_hash = hashlib.sha256()
    signature_hash.update(EMBEDDING_MODEL_NAME.encode("utf-8"))
    signature_hash.update(INTENT_FILE.read_bytes())
    signature_hash.update(metadata.encode("utf-8"))
    signature = signature_hash.hexdigest()
    embeddings = None
    cache_loaded = False
    if EMBEDDINGS_CACHE.exists():
        with np.load(EMBEDDINGS_CACHE, allow_pickle=False) as cache:
            if cache["signature"].item() == signature:
                embeddings = cache["vectors"]
                cache_loaded = True

    if embeddings is None:
        vectors = []
        for start in range(0, len(all_intents), BATCH_SIZE):
            batch = all_intents[start:start + BATCH_SIZE]
            documents = [
                f"Intent: {category}. "
                f"Description: {INTENT_DEFINITIONS[category]['description']} "
                f"Example: {example}"
                for category, example in zip(
                    all_categories[start:start + BATCH_SIZE], batch
                )
            ]
            vectors.extend(embedding_model.embed_documents(documents))
        embeddings = np.asarray(vectors, dtype=float)

    if (
        embeddings.ndim != 2
        or embeddings.shape[0] != len(all_intents)
        or not np.isfinite(embeddings).all()
        or np.any(np.linalg.norm(embeddings, axis=1) == 0)
    ):
        raise ValueError("Ollama returned invalid intent embeddings")

    if not cache_loaded:
        np.savez(EMBEDDINGS_CACHE, signature=signature, vectors=embeddings)

    intent_embeddings = embeddings


def get_best_intent_match_rag(
    user_query: str,
    top_k: int = 3,
) -> tuple[str, str, list[tuple[str, str, float]]]:
    if intent_embeddings is None:
        raise RuntimeError("Call prepare_intent_embeddings() before searching")
    if not user_query.strip():
        raise ValueError("Query must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")

    query_vector = np.asarray(embedding_model.embed_query(user_query), dtype=float)
    if (
        query_vector.ndim != 1
        or query_vector.shape[0] != intent_embeddings.shape[1]
        or not np.isfinite(query_vector).all()
        or np.linalg.norm(query_vector) == 0
    ):
        raise ValueError("Ollama returned an invalid query embedding")

    scores = cosine_similarity(query_vector.reshape(1, -1), intent_embeddings)[0]
    best_by_category: dict[str, tuple[str, float]] = {}

    for example, category, score in zip(all_intents, all_categories, scores):
        current = best_by_category.get(category)
        if current is None or score > current[1]:
            best_by_category[category] = (example, float(score))

    top_matches = sorted(
        (
            (category, example, score)
            for category, (example, score) in best_by_category.items()
        ),
        key=lambda match: match[2],
        reverse=True,
    )[:top_k]

    if not top_matches:
        raise ValueError("No intent matches are available")

    category, example, _ = top_matches[0]
    return example, category, top_matches


def nwdaf_subscription_command(action: str, target: str) -> Any:
    if (action, target) not in ALLOWED_NWDAF_COMMANDS:
        raise ValueError("Unsupported NWDAF action or target")

    response = requests.post(
        NWDAF_URL,
        json={"action": action, "target": target},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def retrieve_intent(state: QueryState) -> QueryState:
    best_intent, category, top_matches = get_best_intent_match_rag(
        state["user_query"]
    )
    return {
        "best_intent": best_intent,
        "category": category,
        "score": top_matches[0][2],
        "top_matches": top_matches,
    }


def route_intent(state: QueryState) -> QueryState:
    query = state["user_query"]
    if NEGATION_PATTERN.search(query):
        return {
            "status": "clarify",
            "answer": "Câu hỏi có vẻ chứa ý phủ định. Bạn vui lòng diễn đạt lại hành động muốn thực hiện.",
        }
    if MULTI_REQUEST_PATTERN.search(query):
        return {
            "status": "clarify",
            "answer": "Mình nhận thấy câu hỏi có thể gồm nhiều yêu cầu. Vui lòng gửi từng yêu cầu riêng.",
        }

    category = state["category"]
    definition = INTENT_DEFINITIONS.get(category)
    if (
        definition is None
        or definition.get("tool") not in ALLOWED_TOOLS
        or any(key not in definition for key in definition.get("required_parameters", []))
    ):
        return {
            "status": "unsupported",
            "answer": f"Chưa hỗ trợ nhóm intent: {category}.",
        }
    is_action = definition["tool"] == "nwdaf_subscription_command"
    minimum_score = MIN_ACTION_SCORE if is_action else MIN_ANALYTICS_SCORE
    if state["score"] < minimum_score:
        return {
            "status": "clarify",
            "answer": "Mình chưa đủ chắc chắn về ý định. Bạn vui lòng diễn đạt rõ hơn.",
        }

    top_matches = state["top_matches"]
    if len(top_matches) > 1 and state["score"] - top_matches[1][2] < MIN_SCORE_MARGIN:
        return {
            "status": "clarify",
            "answer": (
                f"Bạn muốn chọn '{category}' hay '{top_matches[1][0]}'? "
                "Vui lòng làm rõ yêu cầu."
            ),
        }

    if is_action:
        action = definition["action"]
        target = definition["target"]
        if (action, target) not in ALLOWED_NWDAF_COMMANDS:
            return {
                "status": "unsupported",
                "answer": "Hành động hoặc target không nằm trong allowlist NWDAF.",
            }
        return {
            "status": "ready",
            "action": action,
            "target": target,
        }

    if definition["tool"] == "diagnose_registration_failure":
        return {"status": "ready", "diagnosis": True}

    if definition["tool"] != "query_prometheus" or "metric" not in definition:
        return {
            "status": "unsupported",
            "answer": f"Chưa hỗ trợ nhóm intent: {category}.",
        }

    return {"status": "ready", "metric": definition["metric"]}


def execute_action(state: QueryState) -> QueryState:
    try:
        if state.get("diagnosis"):
            result = collect_registration_failure_evidence()
        elif "action" in state:
            result = nwdaf_subscription_command(state["action"], state["target"])
        else:
            result = query_prometheus(state["metric"])
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    return {"function_result": result}


def interpret_result(state: QueryState) -> QueryState:
    result = state["function_result"]
    if isinstance(result, dict) and result.get("status") == "error":
        return {
            "answer": f"Backend không thể xử lý yêu cầu: {result.get('error', 'lỗi không xác định')}.",
            "status": "error",
        }
    if isinstance(result, dict) and result.get("status") == "empty":
        health = result.get("scrape_health", {}).get("status", "unknown")
        return {
            "answer": (
                "Không có dữ liệu trong khoảng thời gian truy vấn. "
                f"Tình trạng scrape Prometheus: {health}. Hãy kiểm tra target và khoảng thời gian."
            ),
            "status": "empty",
        }
    if isinstance(result, dict) and result.get("status") == "unverified":
        return {
            "answer": (
                "Có dữ liệu Prometheus nhưng trạng thái scrape chưa được xác nhận; "
                "mình chưa dùng dữ liệu này để kết luận. Hãy kiểm tra trạng thái targets."
            ),
            "status": "unverified",
        }
    response = chat_model.invoke([
        (
            "system",
            "Bạn diễn giải kết quả phân tích mạng 5G bằng tiếng Việt. "
            "Chỉ kết luận từ dữ liệu backend được cung cấp. "
            "Dữ liệu có metadata nguồn, thời gian truy vấn và trạng thái scrape. "
            "Nếu có thông tin vị trí hoặc điểm đến, đó là dữ liệu mô phỏng, "
            "không phải tọa độ địa lý thực. "
            "Với báo cáo chẩn đoán, chỉ tóm tắt bằng chứng, thành phần nghi vấn, "
            "mức độ chắc chắn và bước kiểm tra tiếp theo từ dữ liệu cung cấp. "
            "Không khẳng định nguyên nhân gốc; nếu bằng chứng thiếu hoặc mâu thuẫn, "
            "hãy nói chưa đủ dữ liệu. "
            "Nêu nguồn và thời gian khi có thể. Nếu dữ liệu thiếu hoặc không đủ "
            "để kết luận, hãy nói rõ; không tự suy đoán.",
        ),
        (
            "human",
            f"Câu hỏi: {state['user_query']}\n"
            f"Intent: {state['category']}\n"
            f"Kết quả backend: "
            f"{json.dumps(state['function_result'], ensure_ascii=False, default=str)}",
        ),
    ])
    return {"answer": str(response.content), "status": "complete"}


def next_after_routing(state: QueryState) -> str:
    return "execute_action" if state["status"] == "ready" else END


def build_graph():
    graph = StateGraph(QueryState)
    graph.add_node("retrieve_intent", retrieve_intent)
    graph.add_node("route_intent", route_intent)
    graph.add_node("execute_action", execute_action)
    graph.add_node("interpret_result", interpret_result)

    graph.add_edge(START, "retrieve_intent")
    graph.add_edge("retrieve_intent", "route_intent")
    graph.add_conditional_edges("route_intent", next_after_routing)
    graph.add_edge("execute_action", "interpret_result")
    graph.add_edge("interpret_result", END)
    return graph.compile()


def process_user_query(user_query: str, graph=None) -> str:
    if intent_embeddings is None:
        prepare_intent_embeddings()
    if graph is None:
        graph = build_graph()

    if NEGATION_PATTERN.search(user_query):
        return route_intent({"user_query": user_query})["answer"]

    parts = [part.strip() for part in MULTI_REQUEST_PATTERN.split(user_query) if part.strip()]
    if len(parts) > 1:
        # Only split when every clause independently maps to a supported intent
        # with a clear score and margin. Otherwise ask the user to clarify.
        candidates = [get_best_intent_match_rag(part) for part in parts]
        for _, category, matches in candidates:
            definition = INTENT_DEFINITIONS.get(category)
            if definition is None or not matches:
                return "Mình chưa tách chắc chắn được các ý. Vui lòng gửi từng yêu cầu riêng."
            minimum = MIN_ACTION_SCORE if definition["tool"] == "nwdaf_subscription_command" else MIN_ANALYTICS_SCORE
            if matches[0][2] < minimum:
                return "Mình chưa tách chắc chắn được các ý. Vui lòng gửi từng yêu cầu riêng."
            if len(matches) > 1 and matches[0][2] - matches[1][2] < MIN_SCORE_MARGIN:
                return "Mình chưa tách chắc chắn được các ý. Vui lòng gửi từng yêu cầu riêng."

        answers = []
        for part in parts:
            result = graph.invoke({"user_query": part})
            answers.append(result["answer"])
        return "\n".join(f"{index}. {answer}" for index, answer in enumerate(answers, start=1))

    result = graph.invoke({"user_query": user_query})
    for rank, (category, example, score) in enumerate(result["top_matches"], start=1):
        print(f"{rank}. {category} | {score:.3f} | {example}")
    return result["answer"]


def main() -> None:
    prepare_intent_embeddings()
    graph = build_graph()

    while True:
        try:
            user_question = input("\nEnter your query (Ctrl+C to exit): ").strip()
            if not user_question:
                continue

            answer = process_user_query(user_question, graph)
            print("Response:", answer)
        except KeyboardInterrupt:
            print("\nExiting...")
            sys.exit(0)


if __name__ == "__main__":
    main()
