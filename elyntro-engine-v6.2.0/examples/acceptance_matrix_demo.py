"""Run Elyntro's positive and negative acceptance matrix."""
import json
from elyntro.acceptance_matrix import AcceptanceMatrix

if __name__ == "__main__":
    report = AcceptanceMatrix.run()
    print(json.dumps(report.to_dict(), indent=2))
    if not report.passed:
        raise SystemExit(1)
