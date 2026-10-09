"""Keep migrated application tables out of the per-test schema's public fallback."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import asyncpg

BASE = "postgresql+asyncpg://heifang:heifang@127.0.0.1:5432/heifang"
TEST = BASE + "_it_tests"
NODE = "tests/integration/test_seo_spend.py::test_migration_upgrade_downgrade_matches_tables"
ART = Path(os.environ["ARTIFACTS"])
SOURCE = Path("tests/integration/test_seo_spend.py")


async def prepare():
    assert os.environ["DATABASE_URL"] == os.environ["TEST_DATABASE_URL"] == BASE
    app = await asyncpg.connect(host="127.0.0.1", user="heifang", password="heifang", database="heifang")
    try:
        assert await app.fetchval("SELECT to_regclass('public.seo_spend_reservations')") is not None
        assert not await app.fetchval("SELECT EXISTS (SELECT FROM pg_database WHERE datname='heifang_it_tests')")
        await app.execute("CREATE DATABASE heifang_it_tests OWNER heifang")
    finally:
        await app.close()
    test = await asyncpg.connect(host="127.0.0.1", user="heifang", password="heifang", database="heifang_it_tests")
    try:
        await test.execute("CREATE EXTENSION vector; CREATE EXTENSION pg_trgm")
        assert await test.fetchval("SELECT to_regclass('public.seo_spend_reservations')") is None
        assert await test.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'") == 0
    finally:
        await test.close()


def probe(url, name, expected):
    output = ART / (name + ".xml")
    result = subprocess.run([sys.executable, "-m", "pytest", NODE, "--tb=short", "-ra",
                             "-o", "junit_family=xunit1", "--junitxml=" + str(output)],
                            env={**os.environ, "TEST_DATABASE_URL": url}, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (ART / "logs" / (name + ".log")).write_text(result.stdout)
    print(result.stdout, flush=True)
    cases = list(ET.parse(output).iter("testcase"))
    assert len(cases) == 1 and cases[0].get("name") == NODE.split("::")[1]
    assert result.returncode == expected and cases[0].find("skipped") is None
    failure = cases[0].find("failure")
    if expected:
        assert failure is not None and "assert not True" in failure.get("message", "")
        assert 'has_table("seo_spend_reservations")' in result.stdout
    else:
        assert failure is None and cases[0].find("error") is None


before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
asyncio.run(prepare())
probe(BASE, "migration-public-fallback-negative", 1)
probe(TEST, "migration-isolated-database-positive", 0)
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before
(ART / "integration-database-isolation.json").write_text(json.dumps({
    "complete": True, "source_test_sha256": before, "source_unchanged": True,
    "app_database": "heifang", "test_database": "heifang_it_tests",
    "same_exact_test_on_migrated_public": "expected assertion failure reproduced",
    "same_exact_test_on_clean_public": "passed", "test_public_application_tables": 0,
    "scope": "disposable loopback Circle PostgreSQL only; no production changes",
}, indent=2))
