# Automated checks and clean installation

WitnessForge has a GitHub Actions workflow for pushes, pull requests and manual runs. It checks Python 3.12 and 3.13 on Ubuntu and Windows. Each job runs the unit/local integration suite, builds the source distribution and wheel, and installs the wheel into a fresh environment outside the checkout. Workflow dependencies are pinned to immutable official action commits with read-only repository permissions.

The installation probe checks both CLI entry points, a reference scenario, the running HTTP health endpoint and all required packaged browser assets. It uses isolated Python mode and verifies the imported package comes from the new environment, so a working source checkout cannot mask missing installed files.

From this project folder:

```powershell
python -m unittest discover -s tests -v
python -m pip install build
python -m build
python scripts/check_install.py --dist dist
```

Use a clean build-output directory with exactly one wheel and one source archive. The test script creates a temporary virtual environment and installs only the wheel, with no package-index access or third-party runtime dependencies. Python's build frontend may fetch build-system dependencies during the preceding build step.

These workflow jobs test the Python application and package installation. Real Android, OpenBao and Chrome/WebMCP integrations have their own prerequisites and documented commands; this workflow does not silently substitute a mock for an unavailable external runtime. See VALIDATION.md and the integration guide for measured runtime results and any explicit skips.

The workflow follows [GitHub's Python testing guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python). Current local and remote outcomes belong in the validation record, separately from the workflow configuration itself.

A second job, *Lint, security and coverage*, runs ruff, bandit, pip-audit (over the pinned development tools; the lab has no runtime dependencies) and the test suite under coverage.
