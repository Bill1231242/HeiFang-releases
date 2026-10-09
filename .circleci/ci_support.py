"""Small, external verification helpers. Runtime dependencies come from uv.lock."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REQUIRED_STAGES = [
    "inputs", "uv-install", "node-download", "node-checksums", "pnpm-install",
    "source-fetch", "dependencies", "ripgrep", "ripgrep-version", "contract-dependencies",
    "runtime", "postgres-ready", "ruff", "mypy", "log-catalog", "migrations",
    "postgres-vector", "schema-live", "integration-db-isolation", "pytest", "pytest-report", "contracts",
    "conformance-export", "legal-mirror", "doc-pointers", "drift", "untracked",
]
TAPE_SKIP_NODE = "tests/integration/test_demo_tape_db_hydrate.py::test_lv_molihua_tape_db_hydrate_has_ceo_after_team"
TAPE_RELATIVE_PATH = "demos/tapes/lv-molihua-trademark.json"
PAID_SKIP_NODE = "tests/integration/test_seo_live_acceptance.py::test_chat_tools_and_native_reports_with_real_receipts"
PAID_SKIP_REASON = "Paid supplier test requires a separate explicit opt-in and cumulative budget reservation"
REQUIRED_INTEGRATION_NODES = [
    "tests/integration/test_knowledge_storage_lock.py::test_team_scope_lock_serializes_import_and_other_creator_output[dedup-manifest]",
    "tests/integration/test_knowledge_storage_lock.py::test_team_scope_lock_serializes_import_and_other_creator_output[pending-manifest]",
]


def integration_results(collected, reports, tape, paid_opt_in_absent):
    """Only two exact existing optional skips are exempted; neither is passed."""
    integration = [node for node in collected if node.startswith("tests/integration/")]
    passed, allowed, errors = [], [], []
    if not integration:
        errors.append("No integration tests collected")
    if paid_opt_in_absent is not True:
        errors.append("Paid supplier opt-in must be absent; upstream acceptance is outside verification scope")
    for node in REQUIRED_INTEGRATION_NODES:
        if collected.count(node) != 1:
            errors.append(f"Required storage lock case missing or duplicated: {node}")
    for node in integration:
        phases = [report for report in reports if report["nodeid"] == node]
        clean_phases = len(phases) == 3 and {report["when"] for report in phases} == {"setup", "call", "teardown"}
        clean_phases = clean_phases and all(report.get("wasxfail") is None for report in phases)
        if clean_phases and all(report["outcome"] == "passed" for report in phases):
            passed.append(node)
            continue
        call = next((report for report in phases if report["when"] == "call"), None)
        setup = next((report for report in phases if report["when"] == "setup"), None)
        paid_skip = (node == PAID_SKIP_NODE and paid_opt_in_absent is True and len(phases) == 2
                     and {report["when"] for report in phases} == {"setup", "teardown"}
                     and all(report.get("wasxfail") is None for report in phases)
                     and setup["outcome"] == "skipped" and setup.get("skip_reason") == PAID_SKIP_REASON
                     and all(report["outcome"] == "passed" for report in phases if report["when"] == "teardown"))
        if paid_skip:
            allowed.append({"nodeid": node, "reason": PAID_SKIP_REASON})
            continue
        if (clean_phases and node == TAPE_SKIP_NODE and call["outcome"] == "skipped"
                and call.get("skip_reason") == f"tape missing: {tape['path']}" and tape["exists"] is False
                and all(report["outcome"] == "passed" for report in phases if report["when"] != "call")):
            allowed.append({"nodeid": node, "reason": call["skip_reason"]})
        else:
            errors.append(f"Integration test must execute and pass; unauthorized skip/xfail/failure/incomplete phases: {node}")
    return {"integration_collected": integration, "integration_passed": sorted(passed),
            "allowed_integration_skips": allowed, "gate_errors": errors}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def inputs(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha == "0" * 40:
        raise ValueError("Replace SOURCE_COMMIT with the parent's full lowercase source SHA")


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


async def postgres(verify=False):
    import asyncpg

    expected = "postgresql+asyncpg://heifang:heifang@127.0.0.1:5432/heifang"
    if os.environ.get("DATABASE_URL") != expected or os.environ.get("TEST_DATABASE_URL") != expected:
        raise ValueError("Database targets must be the disposable loopback PG service")
    connection = None
    for attempt in range(60):
        try:
            connection = await asyncpg.connect(
                host="127.0.0.1", port=5432, user="heifang", password="heifang",
                database="heifang", timeout=2, command_timeout=10,
            )
            break
        except (OSError, asyncpg.PostgresError, TimeoutError):
            if attempt == 59:
                raise RuntimeError("Disposable PostgreSQL did not become ready") from None
            await asyncio.sleep(1)
    try:
        version = int(await connection.fetchval("SHOW server_version_num"))
        if not 160000 <= version < 170000:
            raise ValueError("Expected a real PostgreSQL 16 server")
        result = {"server_version_num": version, "service": "pgvector/pgvector:pg16"}
        if verify:
            result["extensions"] = dict(
                (row["extname"], row["extversion"]) for row in await connection.fetch(
                    "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
                )
            )
            if set(result["extensions"]) != {"vector", "pg_trgm"}:
                raise ValueError("Migrations must install vector and pg_trgm")
            distance = await connection.fetchval("SELECT '[1,2,3]'::vector <=> '[1,2,3]'::vector")
            if abs(distance) > 0.000001:
                raise ValueError("Real vector operator smoke failed")
            result["vector_operator_smoke"] = "passed"
            result["migration_revisions"] = list(await connection.fetch(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ))
            result["migration_revisions"] = [row["version_num"] for row in result["migration_revisions"]]
        write_json(Path(os.environ["ARTIFACTS"]) / ("postgres-vector.json" if verify else "postgres-ready.json"), result)
        print("Real PostgreSQL 16 verified" + (" with vector, pg_trgm and migration revisions" if verify else ""))
    finally:
        await connection.close()


def reports(artifacts, server_root=None):
    artifacts = Path(artifacts)
    evidence = json.loads((artifacts / "pytest-evidence.json").read_text(encoding="utf-8"))
    cases = list(ET.parse(artifacts / "junit/backend.xml").iter("testcase"))
    if not cases or len(cases) != len(evidence["collected"]) or evidence["gate_errors"] or evidence["pytest_exitstatus"] != 0:
        raise ValueError("Pytest/JUnit/evidence gate failed or is empty")
    tape_path = Path(server_root or Path.cwd()).resolve().parents[1] / TAPE_RELATIVE_PATH
    tape = {"path": str(tape_path), "relative_path": TAPE_RELATIVE_PATH, "exists": tape_path.exists()}
    if evidence["optional_tape"] != tape:
        raise ValueError("Optional tape observation does not match the actual pinned source")
    paid_opt_in_absent = os.environ.get("SEO_LIVE_ACCEPTANCE") is None
    if evidence["paid_supplier_opt_in_absent"] is not True or not paid_opt_in_absent:
        raise ValueError("Paid supplier opt-in must be absent; upstream acceptance is outside verification scope")
    computed = integration_results(evidence["collected"], evidence["reports"], tape, paid_opt_in_absent)
    if computed["gate_errors"] or any(evidence[key] != computed[key] for key in computed if key != "gate_errors"):
        raise ValueError("Integration evidence must match actual passed phases or the two exact optional-skip exceptions")
    skipped = sorted({report["nodeid"] for report in evidence["reports"] if report["outcome"] == "skipped"})
    if evidence["skipped"] != skipped:
        raise ValueError("Skip evidence does not match raw reports")
    if evidence["deselected"] or evidence["collection_skips"]:
        raise ValueError("Deselection or collection skips are forbidden")
    if any(case.find("failure") is not None or case.find("error") is not None for case in cases):
        raise ValueError("JUnit contains test failures/errors")
    allowed = {skip["nodeid"]: skip["reason"] for skip in computed["allowed_integration_skips"]}
    for node in computed["integration_collected"]:
        module, *names = node.split("::")
        classname = ".".join([module.removesuffix(".py").replace("/", "."), *names[:-1]])
        matches = [case for case in cases if (case.get("classname"), case.get("name")) == (classname, names[-1])]
        if len(matches) != 1:
            raise ValueError(f"Integration JUnit case missing or duplicated: {node}")
        skip = matches[0].find("skipped")
        if node in allowed:
            if skip is None or skip.get("type") != "pytest.skip" or skip.get("message") != allowed[node]:
                raise ValueError("JUnit must explicitly record the exact authorized optional-skip reason")
        elif skip is not None:
            raise ValueError(f"JUnit cannot mark a passed integration test skipped/xfail: {node}")
    print(f"JUnit cases={len(cases)}; integration passed={len(computed['integration_passed'])}; "
          f"allowed optional integration skips={len(allowed)}; other skips={len(skipped) - len(allowed)}")
    for node, reason in allowed.items():
        print(f"Allowed integration SKIPPED (not passed): {node}: {reason}")


def clean(source):
    status = command("git", "-C", str(source), "status", "--porcelain", "--untracked-files=all")
    if status is None or status:
        raise ValueError("Disposable source has tracked or untracked drift; inspect drift.log")


def runtime():
    write_json(Path(os.environ["ARTIFACTS"]) / "runtime.json", {
        "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
        "uv": command("uv", "--version"), "node": command("node", "--version"),
        "pnpm": command("pnpm", "--version"), "ripgrep": command("rg", "--version"),
    })


def provenance(source, artifacts, config_root):
    source, artifacts, config_root = Path(source), Path(artifacts), Path(config_root)
    artifacts.mkdir(parents=True, exist_ok=True)
    stages_path = artifacts / "stages.tsv"
    stages = dict(line.split("\t") for line in stages_path.read_text().splitlines()) if stages_path.exists() else {}
    source_sha = command("git", "-C", str(source), "rev-parse", "HEAD") if source.exists() else None
    source_status = command("git", "-C", str(source), "status", "--porcelain", "--untracked-files=all") if source.exists() else None
    runner_exit = (artifacts / "runner-exit-code.txt").read_text().strip() if (artifacts / "runner-exit-code.txt").exists() else None
    evidence_path = artifacts / "pytest-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    evidence_gate_error = "Pytest evidence is missing"
    if evidence:
        try:
            reports(artifacts, source / "apps/server")
            evidence_gate_error = None
        except (ValueError, OSError, KeyError, ET.ParseError) as error:
            evidence_gate_error = str(error)
    files = [".github/workflows/ci.yml", "apps/server/pyproject.toml", "apps/server/uv.lock", "pnpm-lock.yaml",
             "scripts/gen-types.mjs", "scripts/sync-legal-md.mjs", "scripts/check-doc-section-pointers.mjs"]
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files if (source / name).is_file()}
    copy_root = artifacts / "template"
    copy_root.mkdir(exist_ok=True)
    for file in config_root.iterdir():
        if file.is_file():
            (copy_root / file.name).write_bytes(file.read_bytes())
    write_json(artifacts / "provenance.json", {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested_source_sha": os.environ.get("SOURCE_COMMIT"), "actual_source_sha": source_sha,
        "config_repository_sha": os.environ.get("VERIFY_CONFIG_COMMIT"),
        "build_url": os.environ.get("VERIFY_BUILD_URL"), "workflow_id": os.environ.get("VERIFY_WORKFLOW_ID"),
        "source_status": source_status, "source_input_sha256": hashes, "stages": stages,
        "runner_exit_code": runner_exit,
        "pytest_evidence_gate_error": evidence_gate_error,
        "complete_pass": runner_exit == "0" and source_sha == os.environ.get("SOURCE_COMMIT") and source_status == ""
            and all(stages.get(name) == "0" for name in REQUIRED_STAGES) and evidence_gate_error is None,
        "clock_plugin_origin": "E:/AgentCore-tools/windows-description-0.9.98/description_release_testclock.py",
        "clock_plugin_sha256": "39660079f7053faa8a4f7758d7da510fca487c02c9efd15af6c831a3800423b7",
        "proof_scope": "full backend pytest including real PG integration, schema/lint/contracts/export/legal/docs drift; "
            "only the exact missing optional demo-tape and paid supplier opt-in tests may be skipped and are never counted passed",
        "integration_skip_policy": [
            {"nodeid": TAPE_SKIP_NODE, "relative_path": TAPE_RELATIVE_PATH,
             "reason": "tape missing: <absolute pinned-source tape path>", "phase": "call", "xfail_allowed": False},
            {"nodeid": PAID_SKIP_NODE, "reason": PAID_SKIP_REASON, "phase": "setup", "xfail_allowed": False,
             "requires_absent_environment_variable": "SEO_LIVE_ACCEPTANCE"},
        ],
        "paid_supplier_opt_in_absent": evidence.get("paid_supplier_opt_in_absent"),
        "allowed_integration_skips": evidence.get("allowed_integration_skips", []),
        "integration_passed": evidence.get("integration_passed", []),
        "knowledge_storage_lock_passed": [node for node in REQUIRED_INTEGRATION_NODES if node in evidence.get("integration_passed", [])],
    })
    # Do not store private SSH keys, full process environments, node_modules, or the source clone.
    receipts = []
    for file in sorted(artifacts.rglob("*")):
        if file.is_file() and file.name != "SHA256SUMS":
            receipts.append(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(artifacts).as_posix()}")
    (artifacts / "SHA256SUMS").write_text("\n".join(receipts) + "\n", encoding="utf-8")


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    if action in {"wait-db", "verify-db"}:
        asyncio.run(postgres(action == "verify-db"))
    else:
        {"inputs": inputs, "reports": reports, "clean": clean, "runtime": runtime, "provenance": provenance}[action](*args)
