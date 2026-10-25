import unittest

from elyntro.governance import (
    ActionRequest, BudgetLedger, GovernancePolicy, PolicyEngine, ResourceLimits, RiskLevel,
)


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = GovernancePolicy(allowed_tools=frozenset({"search", "write_file", "send_email"}))
        self.engine = PolicyEngine(self.policy, BudgetLedger(ResourceLimits(
            max_actions=2, max_total_tokens=100, max_total_cost_usd=1.0)))

    def action(self, **kwargs):
        base = dict(action_id="a1", tool_key="search", description="Search public sources",
                    risk=RiskLevel.LOW, estimated_cost_usd=0.1, estimated_tokens=10)
        base.update(kwargs)
        return ActionRequest(**base)

    def test_low_risk_allow_reserves_estimate(self):
        decision = self.engine.authorize(self.action())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.budget.actions_reserved, 1)
        self.assertEqual(decision.budget.tokens_reserved, 10)

    def test_high_risk_requires_approval_without_reserving(self):
        decision = self.engine.authorize(self.action(risk=RiskLevel.HIGH))
        self.assertEqual(decision.outcome, "require_approval")
        self.assertEqual(decision.budget.actions_reserved, 0)
        approved = self.engine.authorize(self.action(action_id="a2", risk=RiskLevel.HIGH, user_approved=True))
        self.assertTrue(approved.allowed)

    def test_unknown_tool_is_default_denied(self):
        decision = self.engine.authorize(self.action(tool_key="arbitrary_shell"))
        self.assertEqual(decision.outcome, "deny")
        self.assertEqual(decision.budget.actions_reserved, 0)

    def test_explicit_deny_overrides_allowlist(self):
        engine = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"search"}), denied_tools=frozenset({"search"})))
        self.assertEqual(engine.authorize(self.action()).outcome, "deny")

    def test_network_and_external_write_controls(self):
        engine = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"search", "send_email"}),
                                               allow_network=False, allow_external_writes=False))
        self.assertEqual(engine.authorize(self.action(requires_network=True)).outcome, "deny")
        self.assertEqual(engine.authorize(self.action(tool_key="send_email", writes_external_state=True)).outcome, "deny")

    def test_critical_action_denied_by_default_even_if_approved(self):
        decision = self.engine.authorize(self.action(risk=RiskLevel.CRITICAL, user_approved=True))
        self.assertEqual(decision.outcome, "deny")

    def test_cost_budget_blocks_without_reservation(self):
        self.engine.authorize(self.action(action_id="a1", estimated_cost_usd=0.7))
        decision = self.engine.authorize(self.action(action_id="a2", estimated_cost_usd=0.4))
        self.assertEqual(decision.outcome, "budget_exceeded")
        self.assertEqual(decision.budget.actions_reserved, 1)

    def test_action_count_and_token_budgets(self):
        self.engine.authorize(self.action(action_id="a1", estimated_tokens=60))
        second = self.engine.authorize(self.action(action_id="a2", estimated_tokens=50))
        self.assertEqual(second.outcome, "budget_exceeded")
        self.engine.authorize(self.action(action_id="a3", estimated_tokens=10))
        fourth = self.engine.authorize(self.action(action_id="a4", estimated_tokens=1))
        self.assertEqual(fourth.outcome, "budget_exceeded")

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.authorize(self.action(estimated_tokens=True))
        with self.assertRaises(ValueError):
            self.engine.authorize(self.action(estimated_cost_usd=-1))


if __name__ == "__main__":
    unittest.main()
