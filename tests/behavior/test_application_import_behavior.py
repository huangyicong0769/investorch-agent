"""Each executable surface must import successfully in a fresh interpreter."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["investorch.agents", "investorch.web.server", "investorch.__main__"])
def test_application_entry_imports_without_prior_module_initialization(module):
    result = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
