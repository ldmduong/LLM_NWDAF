import unittest
from unittest.mock import Mock, patch

import LLM


class ValidationTests(unittest.TestCase):
    def test_rejects_invalid_metric_before_tool_execution(self):
        state = {
            "decision": LLM.IntentDecision(
                tool="query_prometheus",
                metric="active_UEs",
            )
        }
        self.assertEqual(LLM.validate_and_route(state), {"status": "ready"})

        invalid_state = {
            "decision": LLM.IntentDecision(
                tool="query_prometheus",
                metric="active_UEs",
            )
        }
        with patch.object(LLM, "PROMETHEUS_METRICS", set()):
            result = LLM.validate_and_route(invalid_state)
        self.assertEqual(result["status"], "clarify")

    def test_routes_only_allowed_subscription_pair(self):
        valid = {
            "decision": LLM.IntentDecision(
                tool="nwdaf_subscription_command",
                action="subscribe",
                target="amf",
            )
        }
        self.assertEqual(LLM.validate_and_route(valid), {"status": "ready"})

        with patch.object(LLM, "NWDAF_COMMANDS", set()):
            result = LLM.validate_and_route(valid)
        self.assertEqual(result["status"], "clarify")

    def test_negated_and_multiple_requests_are_not_sent_to_classifier(self):
        with patch.object(LLM, "build_classifier") as classifier:
            negative = LLM.classify_request({"user_message": "do not subscribe AMF"})
            multiple = LLM.classify_request(
                {"user_message": "subscribe AMF and show active UEs"}
            )

        self.assertEqual(negative["decision"].tool, "none")
        self.assertEqual(multiple["decision"].tool, "none")
        classifier.assert_not_called()


class GraphTests(unittest.TestCase):
    def test_runs_allowed_metric_then_interprets_result(self):
        decision = LLM.IntentDecision(
            tool="query_prometheus",
            metric="active_UEs",
        )
        classifier = Mock()
        classifier.invoke.return_value = decision
        answer_model = Mock()
        answer_model.content = "Có 4 UE đang hoạt động."

        with (
            patch.object(LLM, "build_classifier", return_value=classifier),
            patch.object(LLM, "query_prometheus", return_value={"count": 4}) as query,
            patch.object(LLM, "generate_answer", return_value=answer_model),
        ):
            result = LLM.build_graph().invoke({"user_message": "show active UEs"})

        query.assert_called_once_with("active_UEs")
        self.assertEqual(result["answer"], "Có 4 UE đang hoạt động.")

    def test_clarification_does_not_execute_a_tool(self):
        decision = LLM.IntentDecision(
            tool="none",
            clarification="Vui lòng làm rõ yêu cầu.",
        )
        classifier = Mock()
        classifier.invoke.return_value = decision

        with (
            patch.object(LLM, "build_classifier", return_value=classifier),
            patch.object(LLM, "query_prometheus") as query,
            patch.object(LLM, "nwdaf_subscription_command") as command,
        ):
            result = LLM.build_graph().invoke({"user_message": "unclear request"})

        self.assertEqual(result["answer"], "Vui lòng làm rõ yêu cầu.")
        query.assert_not_called()
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
