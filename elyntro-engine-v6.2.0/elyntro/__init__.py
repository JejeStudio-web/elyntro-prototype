"""Elyntro Engine: a small, auditable task orchestration core."""

from .contracts import Task, TaskRequest, TaskStatus
from .engine import Engine
from .store import SQLiteStore
from .task_executor import StepContext, StepResult, TaskExecutor, TaskStep
from .execution_control import CancellationToken, ExecutionCancelledError, StepTimeoutError
from .planner import ExecutionPlan, PlannedStep, PlannerBackend, PlanningService, RuleBasedPlanner, ToolDefinition
from .adaptive import AdaptationBackend, AdaptationDecision, AdaptationPolicy, AdaptivePlanningLoop, ExecutionFeedback

__all__ = [
    "Engine", "SQLiteStore", "Task", "TaskRequest", "TaskStatus",
    "TaskExecutor", "TaskStep", "StepContext", "StepResult",
    "CancellationToken", "ExecutionCancelledError", "StepTimeoutError",
    "ToolDefinition", "PlannedStep", "ExecutionPlan", "PlannerBackend",
    "RuleBasedPlanner", "PlanningService",
    "AdaptivePlanningLoop", "AdaptationPolicy", "AdaptationDecision", "ExecutionFeedback", "AdaptationBackend",
    "ModelProfile", "ModelRegistry", "EvaluationResult", "EvaluationStore",
    "estimate_cost", "summarize", "ModelRouter", "RoutingRequest", "RoutingDecision",
    "ModelCallError", "ModelCallRequest", "ModelCallResponse", "ModelGateway", "OpenAICompatibleAdapter", "ModelPlannerBackend",
    "EvidenceVerifier", "VerificationReport", "VerificationStatus", "CriterionCheck",
    "PlanIssue", "PlanReviewReport", "PlanQualityReviewer", "PlanRepairBackend",
    "Memory", "MemoryStore", "CognitiveLoop", "CognitiveRunResult",
]

from .model_registry import ModelProfile, ModelRegistry
from .model_evaluation import EvaluationResult, EvaluationStore, estimate_cost, summarize
from .model_router import ModelRouter, RoutingRequest, RoutingDecision

from .model_registry import ModelProfile, ModelRegistry
from .model_evaluation import EvaluationResult, EvaluationStore, estimate_cost, summarize
from .model_router import ModelRouter, RoutingRequest, RoutingDecision

from .model_gateway import (ModelCallError, ModelCallRequest, ModelCallResponse, ModelGateway, OpenAICompatibleAdapter)

from .verifier import EvidenceVerifier, VerificationReport, VerificationStatus, CriterionCheck

from .memory import Memory, MemoryStore

from .cognitive_loop import CognitiveLoop, CognitiveRunResult

from .model_planner import ModelPlannerBackend

from .plan_review import PlanIssue, PlanReviewReport, PlanQualityReviewer, PlanRepairBackend

from .model_critic import ModelPlanCritic

from .benchmark import BenchmarkCase, BenchmarkCaseResult, BenchmarkReport, BenchmarkRunner, basic_text_cases
__all__ += ["BenchmarkCase", "BenchmarkCaseResult", "BenchmarkReport", "BenchmarkRunner", "basic_text_cases"]

from .benchmark_compare import (BenchmarkComparisonRunner, ComparisonReport, ModelComparisonSummary, PairedModelDifference)
__all__ += ["BenchmarkComparisonRunner", "ComparisonReport", "ModelComparisonSummary", "PairedModelDifference"]

from .adaptive_routing import TaskClassification, RoutingPolicy, AdaptiveRoutingResult, TaskClassifier, AdaptiveModelRouter
__all__ += ["TaskClassification", "RoutingPolicy", "AdaptiveRoutingResult", "TaskClassifier", "AdaptiveModelRouter"]

from .routing_feedback import RoutingFeedback, RoutingFeedbackStore, FeedbackAssessment
__all__ += ["RoutingFeedback", "RoutingFeedbackStore", "FeedbackAssessment"]

from .policy_experiments import (
    RoutingObservation, PolicySpec, PolicyMetrics, PolicyExperimentReport,
    PolicyExperimentRunner, PolicyRegistry,
)
__all__ += [
    "RoutingObservation", "PolicySpec", "PolicyMetrics", "PolicyExperimentReport",
    "PolicyExperimentRunner", "PolicyRegistry",
]

from .routed_execution import RoutedTaskRequest, RoutedTaskResult, RoutedExecutionService
__all__ += ["RoutedTaskRequest", "RoutedTaskResult", "RoutedExecutionService"]

from .reliable_runtime import (
    DuplicateExecutionError, DailyBudgetExceeded, RuntimeAttempt, ReliableRunResult,
    ExecutionLedger, ReliableRoutedExecutionService,
)
__all__ += [
    "DuplicateExecutionError", "DailyBudgetExceeded", "RuntimeAttempt", "ReliableRunResult",
    "ExecutionLedger", "ReliableRoutedExecutionService",
]

from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
__all__ += ["JobStep", "PersistentJobStore", "TaskVerifierRegistry"]

from .cognitive_jobs import CognitiveJobService
__all__ += ["CognitiveJobService"]

from .durable_workflows import DurableWorkflowService
from .cognitive_workflows import CognitiveWorkflowService
__all__ += ["CognitiveWorkflowService"]

from .failure_diagnosis import FailureCategory, FailureDiagnosis, FailureDiagnoser
__all__ += ["FailureCategory", "FailureDiagnosis", "FailureDiagnoser"]

from .recovery import AdaptiveRecoveryCoordinator, RecoveryProposal
__all__ += ["AdaptiveRecoveryCoordinator", "RecoveryProposal"]

from .persistent_recovery import PersistentRecoveryService
__all__ += ["PersistentRecoveryService"]

from .replan_execution import ReplannedWorkflowAdapter
__all__ += ["ReplannedWorkflowAdapter"]

from .recovery_cycle import RecoveryCycleCoordinator, RecoveryCycleResult
__all__ += ["RecoveryCycleCoordinator", "RecoveryCycleResult"]

from .reconciliation import RecoveryReconciler, ReconciliationReport, ReconciliationFinding
__all__ += ["RecoveryReconciler", "ReconciliationReport", "ReconciliationFinding"]

from .resilience import ResilienceHarness, ResilienceReport, ResilienceScenarioResult
__all__ += ["ResilienceHarness", "ResilienceReport", "ResilienceScenarioResult"]

from .acceptance import AcceptanceReport, EndToEndAcceptanceHarness
__all__ += ["AcceptanceReport", "EndToEndAcceptanceHarness"]

from .acceptance_matrix import AcceptanceCase, AcceptanceMatrix, AcceptanceMatrixReport

from .goal_execution import GoalExecutionService, GoalRun, GoalInputError, build_live_model_from_environment
__all__ += ["GoalExecutionService", "GoalRun", "GoalInputError", "build_live_model_from_environment"]

from .research_evidence import (
    ResearchInputError, SourceEvidence, ClaimAssessment, ClaimAudit,
    ResearchToolRegistry, ClaimEvidenceAuditor,
)
__all__ += [
    "ResearchInputError", "SourceEvidence", "ClaimAssessment", "ClaimAudit",
    "ResearchToolRegistry", "ClaimEvidenceAuditor",
]

from .research_workflow import ResearchWorkflowError, ResearchRun, ResearchWorkflowService
__all__ += ["ResearchWorkflowError", "ResearchRun", "ResearchWorkflowService"]

from .mission import MissionPreparation, MissionService
__all__ += ["MissionPreparation", "MissionService"]

from .evaluation_monitor import RegressionPolicy, ModelHealthAssessment, ModelHealthMonitor
__all__ += ["RegressionPolicy", "ModelHealthAssessment", "ModelHealthMonitor"]

from .governance import (
    RiskLevel, ActionRequest, GovernancePolicy, ResourceLimits, BudgetSnapshot,
    BudgetLedger, PolicyDecision, PolicyEngine,
)
__all__ += [
    "RiskLevel", "ActionRequest", "GovernancePolicy", "ResourceLimits",
    "BudgetSnapshot", "BudgetLedger", "PolicyDecision", "PolicyEngine",
]

from .verification_cycle import VerificationCycle, VerificationCycleResult
__all__ += ["VerificationCycle", "VerificationCycleResult"]

from .verification_cycle import VerificationCycle, VerificationCycleResult
__all__ += ["VerificationCycle", "VerificationCycleResult"]
from .governance import ActionRequest, GovernancePolicy, ResourceLimits, BudgetLedger, PolicyEngine, RiskLevel
__all__ += ["ActionRequest", "GovernancePolicy", "ResourceLimits", "BudgetLedger", "PolicyEngine", "RiskLevel"]

from .runtime_health import HealthFinding, RuntimeHealthReport, RuntimeHealthAuditor
__all__ += ["HealthFinding", "RuntimeHealthReport", "RuntimeHealthAuditor"]

from .operation_journal import OperationJournal, OperationConflictError, InvalidOperationTransition
__all__ += ["OperationJournal", "OperationConflictError", "InvalidOperationTransition"]
