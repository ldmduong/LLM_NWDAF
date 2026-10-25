import json
import os
import re
import sys
from typing import Literal, TypedDict

import requests
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from prom_query import query_prometheus


CHAT_MODEL_NAME = "qwen3.5:4b"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
NWDAF_URL = "http://127.0.0.71:8001/nwdaf/command"
PROMETHEUS_METRICS = {
    "active_UEs",
    "amf_ue_registration_state",
    "ue_destination_visits_total",
    "UE_location_report",
}
NWDAF_COMMANDS = {
    ("subscribe", "amf"),
    ("unsubscribe", "amf"),
    ("subscribe", "smf"),
    ("unsubscribe", "smf"),
}

NEGATION_PATTERN = re.compile(
    r"\b(?:don't|do not|never|not|no)\b|(?:không|đừng|chưa|chẳng)",
    re.IGNORECASE,
)
MULTI_REQUEST_PATTERN = re.compile(
    r"\b(?:and|also|then)\b|;|(?:đồng thời|và|cũng|sau đó)",
    re.IGNORECASE,
)

chat_model = ChatOllama(
    model=CHAT_MODEL_NAME,
    temperature=0,
    base_url=OLLAMA_BASE_URL,
)


class IntentDecision(BaseModel):
    tool: Literal["query_prometheus", "nwdaf_subscription_command", "none"]
    metric: Literal[
        "active_UEs",
        "amf_ue_registration_state",
        "ue_destination_visits_total",
        "UE_location_report",
    ] | None = None
    action: Literal["subscribe", "unsubscribe"] | None = None
    target: Literal["amf", "smf"] | None = None
    clarification: str | None = Field(
        default=None,
        description="Vietnamese clarification when the request is unclear or unsupported.",
    )


class ConversationState(TypedDict, total=False):
    user_message: str
    decision: IntentDecision
    status: str
    function_result: object
    answer: str


CLASSIFIER_PROMPT = (
    "You route requests for a 5G network analytics assistant. "
    "Choose exactly one allowed tool based on the user's request. "
    "For analytics, choose query_prometheus and one exact metric: active_UEs, "
    "amf_ue_registration_state, ue_destination_visits_total, or UE_location_report. "
    "For NWDAF subscriptions, choose nwdaf_subscription_command with action "
    "subscribe or unsubscribe and target amf or smf. "
    "Choose tool none and provide a brief Vietnamese clarification when the "
    "request is ambiguous, unsupported, contains multiple requests, or is "
    "negated. Do not execute or infer multiple actions."
)


def build_classifier():
    return chat_model.with_structured_output(IntentDecision)


def generate_answer(messages):
    return chat_model.invoke(messages)


def nwdaf_subscription_command(action: str, target: str) -> dict:
    if (action, target) not in NWDAF_COMMANDS:
        raise ValueError("Unsupported NWDAF action or target")

    response = requests.post(
        NWDAF_URL,
        json={"action": action, "target": target},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def classify_request(state: ConversationState) -> ConversationState:
    message = state["user_message"]
    if NEGATION_PATTERN.search(message):
        return {
            "decision": IntentDecision(
                tool="none",
                clarification="Câu hỏi có ý phủ định. Vui lòng diễn đạt rõ hành động bạn muốn thực hiện.",
            )
        }
    if MULTI_REQUEST_PATTERN.search(message):
        return {
            "decision": IntentDecision(
                tool="none",
                clarification="Vui lòng gửi từng yêu cầu riêng để mình xử lý chính xác.",
            )
        }

    decision = build_classifier().invoke([
        ("system", CLASSIFIER_PROMPT),
        ("human", message),
    ])
    return {"decision": decision}


def validate_and_route(state: ConversationState) -> ConversationState:
    decision = state["decision"]

    if decision.tool == "none":
        return {
            "status": "clarify",
            "answer": decision.clarification
            or "Mình chưa xác định được yêu cầu. Bạn vui lòng diễn đạt rõ hơn.",
        }

    if decision.tool == "query_prometheus":
        if decision.metric not in PROMETHEUS_METRICS:
            return {
                "status": "clarify",
                "answer": "Metric không hợp lệ. Vui lòng nêu loại số liệu cần truy vấn.",
            }
        return {"status": "ready"}

    if (decision.action, decision.target) not in NWDAF_COMMANDS:
        return {
            "status": "clarify",
            "answer": "Lệnh subscription không hợp lệ. Vui lòng nêu rõ AMF/SMF và subscribe/unsubscribe.",
        }

    return {"status": "ready"}


def execute_tool(state: ConversationState) -> ConversationState:
    decision = state["decision"]
    if decision.tool == "query_prometheus":
        result = query_prometheus(decision.metric)
    else:
        result = nwdaf_subscription_command(decision.action, decision.target)
    return {"function_result": result}


def interpret_result(state: ConversationState) -> ConversationState:
    response = generate_answer([
        (
            "system",
            "Bạn diễn giải kết quả analytics hoặc subscription 5G bằng tiếng Việt. "
            "Chỉ dựa trên dữ liệu backend được cung cấp. Nếu dữ liệu rỗng, thiếu "
            "hoặc không đủ để kết luận, hãy nói rõ và không suy đoán.",
        ),
        (
            "human",
            f"Yêu cầu: {state['user_message']}\n"
            f"Kết quả backend: "
            f"{json.dumps(state['function_result'], ensure_ascii=False, default=str)}",
        ),
    ])
    return {"answer": str(response.content), "status": "complete"}


def next_after_validation(state: ConversationState) -> str:
    return "execute_tool" if state["status"] == "ready" else END


def build_graph():
    graph = StateGraph(ConversationState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("validate_and_route", validate_and_route)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("interpret_result", interpret_result)

    graph.add_edge(START, "classify_request")
    graph.add_edge("classify_request", "validate_and_route")
    graph.add_conditional_edges("validate_and_route", next_after_validation)
    graph.add_edge("execute_tool", "interpret_result")
    graph.add_edge("interpret_result", END)
    return graph.compile()


conversation_graph = build_graph()


def run_llm_conversation(user_message: str) -> str:
    if not user_message.strip():
        raise ValueError("Message must not be empty")
    result = conversation_graph.invoke({"user_message": user_message})
    return result["answer"]


if __name__ == "__main__":
    while True:
        try:
            user_question = input("\nEnter your question (Ctrl+C to exit): ").strip()
            if not user_question:
                continue

            print("LLM Answer:", run_llm_conversation(user_question))
        except KeyboardInterrupt:
            print("\nExiting the program...")
            sys.exit(0)
