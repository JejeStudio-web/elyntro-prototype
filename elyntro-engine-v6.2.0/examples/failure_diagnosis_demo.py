"""Show conservative failure classification and conversion to adaptation feedback."""
from elyntro.failure_diagnosis import FailureDiagnoser


def main() -> None:
    diagnoser = FailureDiagnoser()
    diagnosis = diagnoser.diagnose(
        error=TimeoutError("upstream request timed out"),
        evidence_gaps=(),
    )
    print("Diagnosis:", diagnosis.to_dict())
    print("Feedback:", diagnosis.to_feedback(
        completed_step_ids=("research",), failed_step_id="draft"
    ))


if __name__ == "__main__":
    main()
