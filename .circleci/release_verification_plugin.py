"""External evidence/skip gate. Does not patch product behavior."""
import json
import os
from pathlib import Path

import pytest
from ci_support import REQUIRED_INTEGRATION_NODES, TAPE_RELATIVE_PATH, integration_results

CLOCK_TARGET = "tests/test_kakou_platform.py::test_public_reference_rates_fill_old_document_without_mutation"
COLLECTED = []
REPORTS = []
DESELECTED = []
COLLECTION_SKIPS = []


def pytest_sessionstart(session):
    if os.environ.get("SEO_LIVE_ACCEPTANCE") is not None:
        raise pytest.UsageError("Paid supplier opt-in must be absent; upstream acceptance is outside verification scope")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    COLLECTED.extend(item.nodeid for item in items)


def pytest_deselected(items):
    DESELECTED.extend(item.nodeid for item in items)


def pytest_collectreport(report):
    if report.skipped:
        COLLECTION_SKIPS.append(report.nodeid)


def pytest_runtest_logreport(report):
    reason = None
    if report.outcome == "skipped" and isinstance(report.longrepr, tuple):
        reason = str(report.longrepr[2]).removeprefix("Skipped: ")
    REPORTS.append({"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
                    "skip_reason": reason, "wasxfail": getattr(report, "wasxfail", None)})


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    tape_path = Path(session.config.rootpath).resolve().parents[1] / TAPE_RELATIVE_PATH
    tape = {"path": str(tape_path), "relative_path": TAPE_RELATIVE_PATH, "exists": tape_path.exists()}
    paid_opt_in_absent = os.environ.get("SEO_LIVE_ACCEPTANCE") is None
    integration = integration_results(COLLECTED, REPORTS, tape, paid_opt_in_absent)
    skipped = sorted({r["nodeid"] for r in REPORTS if r["outcome"] == "skipped"})
    errors = integration["gate_errors"]
    if DESELECTED or COLLECTION_SKIPS:
        errors.append("Deselected tests or collection skips are forbidden")
    if COLLECTED.count(CLOCK_TARGET) != 1:
        errors.append("Authorized clock target missing or duplicated; review final source")
    if os.environ.get("REQUIRE_INTEGRATION_DB") != "1":
        errors.append("REQUIRE_INTEGRATION_DB must be 1")
    result = {
        "collected": COLLECTED, **integration, "optional_tape": tape,
        "paid_supplier_opt_in_absent": paid_opt_in_absent,
        "required_integration_nodes": REQUIRED_INTEGRATION_NODES,
        "skipped": skipped, "deselected": DESELECTED, "collection_skips": COLLECTION_SKIPS,
        "skip_details": [{"nodeid": r["nodeid"], "phase": r["when"], "reason": r["skip_reason"],
                          "wasxfail": r["wasxfail"]} for r in REPORTS if r["outcome"] == "skipped"],
        "reports": REPORTS, "gate_errors": errors, "pytest_exitstatus": int(exitstatus),
        "clock_override_target": CLOCK_TARGET,
    }
    destination = Path(os.environ["ARTIFACTS"]) / "pytest-evidence.json"
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if errors:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter:
            for error in errors:
                reporter.write_line(f"release verification gate: {error}", red=True)
