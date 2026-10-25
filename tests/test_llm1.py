"""Offline tests for LLM1 intent matching and routing."""

import unittest
from unittest.mock import Mock, patch

import numpy as np

import LLM1


class IntentMatchingTests(unittest.TestCase):
    def setUp(self):
        self.old_intents = LLM1.all_intents
        self.old_categories = LLM1.all_categories
        self.old_embeddings = LLM1.intent_embeddings

    def tearDown(self):
        LLM1.all_intents = self.old_intents
        LLM1.all_categories = self.old_categories
        LLM1.intent_embeddings = self.old_embeddings

    def test_returns_best_example_per_category_in_score_order(self):
        LLM1.all_intents = ["active one", "active two", "AMF subscribe"]
        LLM1.all_categories = ["Active_UEs", "Active_UEs", "AMF Subscribe"]
        LLM1.intent_embeddings = np.array([
            [0.8, 0.6],
            [1.0, 0.0],
            [0.0, 1.0],
        ])
        with patch.object(LLM1, "embedding_model", Mock(embed_query=Mock(return_value=[1.0, 0.0]))):
            example, category, matches = LLM1.get_best_intent_match_rag("query")

        self.assertEqual((example, category), ("active two", "Active_UEs"))
        self.assertEqual([match[0] for match in matches], ["Active_UEs", "AMF Subscribe"])
        self.assertAlmostEqual(matches[0][2], 1.0)

    def test_rejects_invalid_query_embedding(self):
        LLM1.all_intents = ["active"]
        LLM1.all_categories = ["Active_UEs"]
        LLM1.intent_embeddings = np.array([[1.0, 0.0]])
        with patch.object(
            LLM1,
            "embedding_model",
            Mock(embed_query=Mock(return_value=[float("nan"), 0.0])),
        ):
            with self.assertRaisesRegex(ValueError, "invalid query embedding"):
                LLM1.get_best_intent_match_rag("query")


class RoutingTests(unittest.TestCase):
    def state(self, category="AMF Subscribe", score=0.8, runner_up=0.6, query="subscribe AMF"):
        return {
            "user_query": query,
            "category": category,
            "score": score,
            "top_matches": [
                (category, "example", score),
                ("AMF Unsubscribe", "opposite", runner_up),
            ],
        }

    def test_low_score_asks_for_clarification(self):
        result = LLM1.route_intent(self.state(score=0.54))
        self.assertEqual(result["status"], "clarify")

    def test_close_runner_up_asks_user_to_choose(self):
        result = LLM1.route_intent(self.state(score=0.80, runner_up=0.78))
        self.assertEqual(result["status"], "clarify")
        self.assertIn("AMF Unsubscribe", result["answer"])

    def test_negation_and_unsplit_multi_request_do_not_route(self):
        negative = LLM1.route_intent(self.state(query="do not subscribe AMF"))
        multiple = LLM1.route_intent(self.state(query="subscribe AMF and show active UEs"))
        self.assertEqual(negative["status"], "clarify")
        self.assertEqual(multiple["status"], "clarify")

    def test_confident_intent_resolves_only_to_declared_action_and_target(self):
        result = LLM1.route_intent(self.state(score=0.80, runner_up=0.60))
        self.assertEqual(result, {"status": "ready", "action": "subscribe", "target": "amf"})

    def test_invalid_nwdaf_pair_is_rejected_before_http_call(self):
        with patch.object(LLM1.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "Unsupported NWDAF"):
                LLM1.nwdaf_subscription_command("subscribe", "unknown")
        post.assert_not_called()


class MultiRequestTests(unittest.TestCase):
    def setUp(self):
        self.old_embeddings = LLM1.intent_embeddings
        LLM1.intent_embeddings = np.array([[1.0]])

    def tearDown(self):
        LLM1.intent_embeddings = self.old_embeddings

    @staticmethod
    def confident_match(query):
        category = "Active_UEs" if "active" in query.lower() else "Registration State"
        return "example", category, [(category, "example", 0.80), ("other", "other", 0.60)]

    def test_confident_clauses_are_processed_independently(self):
        graph = Mock()
        graph.invoke.side_effect = [
            {"answer": "active result"},
            {"answer": "registration result"},
        ]
        with patch.object(LLM1, "get_best_intent_match_rag", side_effect=self.confident_match):
            answer = LLM1.process_user_query("show active UEs and registration state", graph)

        self.assertEqual(answer, "1. active result\n2. registration result")
        self.assertEqual(graph.invoke.call_count, 2)
        self.assertEqual(graph.invoke.call_args_list[0].args[0], {"user_query": "show active UEs"})
        self.assertEqual(graph.invoke.call_args_list[1].args[0], {"user_query": "registration state"})

    def test_uncertain_clause_requests_clarification_without_execution(self):
        graph = Mock()
        with patch.object(
            LLM1,
            "get_best_intent_match_rag",
            return_value=("example", "Active_UEs", [("Active_UEs", "example", 0.50), ("other", "other", 0.49)]),
        ):
            answer = LLM1.process_user_query("show active UEs and something unclear", graph)

        self.assertIn("Vui lòng gửi từng yêu cầu riêng", answer)
        graph.invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
