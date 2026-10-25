"""Run Elyntro's end-to-end acceptance scenario."""
import json
from elyntro.acceptance import EndToEndAcceptanceHarness

if __name__ == "__main__":
    report = EndToEndAcceptanceHarness.run()
    print(json.dumps(report.to_dict(), indent=2))
    if not report.passed:
        raise SystemExit(1)
