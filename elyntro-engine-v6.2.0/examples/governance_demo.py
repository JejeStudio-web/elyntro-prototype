"""Demonstrate action policy decisions and bounded resource reservations."""
from elyntro.governance import (
    ActionRequest, BudgetLedger, GovernancePolicy, PolicyEngine, ResourceLimits, RiskLevel,
)


def main() -> None:
    policy = GovernancePolicy(
        allowed_tools=frozenset({"research", "send_message"}),
        allow_network=True,
        allow_external_writes=True,
        max_single_action_cost_usd=0.50,
    )
    engine = PolicyEngine(policy, BudgetLedger(ResourceLimits(
        max_actions=5, max_total_tokens=20_000, max_total_cost_usd=1.00)))

    requests = [
        ActionRequest("act-1", "research", "Search public sources", RiskLevel.LOW,
                      estimated_cost_usd=0.10, estimated_tokens=1500, requires_network=True),
        ActionRequest("act-2", "send_message", "Send a message externally", RiskLevel.HIGH,
                      estimated_cost_usd=0.05, estimated_tokens=300, writes_external_state=True),
        ActionRequest("act-3", "send_message", "Send after user approval", RiskLevel.HIGH,
                      estimated_cost_usd=0.05, estimated_tokens=300, writes_external_state=True,
                      user_approved=True),
        ActionRequest("act-4", "run_shell", "Execute an unregistered shell action", RiskLevel.LOW),
    ]
    for request in requests:
        print(engine.authorize(request).to_dict())
    print("Final budget:", engine.budget.snapshot().to_dict())


if __name__ == "__main__":
    main()
