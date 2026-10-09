"""External release-test clock isolation; never imported by the shipped product.

The existing public-reference display assertion expects the off-peak Flash
denominator (7 CNY), but calls a real Beijing-time clock at import/runtime.
Only that named test is pinned; all real peak/off-peak pricing tests remain
untouched. This plugin does not change product source or a running API.
"""
import pytest

@pytest.fixture(autouse=True)
def description_release_reference_clock(request,monkeypatch):
    if request.node.nodeid.endswith('test_kakou_platform.py::test_public_reference_rates_fill_old_document_without_mutation'):
        monkeypatch.setattr('heifang.llm.pricing._deepseek_flash_off_peak',lambda:True)
    yield
