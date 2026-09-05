"""
QA-lane test: assert ci.yml has a python-test job that runs on Python source changes.
Fails before the job is added; passes after.
"""
import pathlib
import yaml


_CI_YML = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _load_ci():
    with _CI_YML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_ci_has_python_test_job():
    """ci.yml must define a job whose key contains 'python'."""
    ci = _load_ci()
    jobs = ci.get("jobs", {})
    python_jobs = [k for k in jobs if "python" in k.lower()]
    assert python_jobs, (
        "No Python test job found in ci.yml. "
        "Add a job (e.g. 'python-test') that runs pytest on Python source changes."
    )


def test_python_job_runs_pytest():
    """The python job must invoke pytest somewhere in its steps."""
    ci = _load_ci()
    jobs = ci.get("jobs", {})
    python_jobs = {k: v for k, v in jobs.items() if "python" in k.lower()}
    assert python_jobs, "No Python job found — see test_ci_has_python_test_job"

    for job_name, job in python_jobs.items():
        steps = job.get("steps", [])
        pytest_steps = [
            s for s in steps
            if "pytest" in str(s.get("run", ""))
        ]
        assert pytest_steps, (
            f"Job '{job_name}' has no step that runs pytest. "
            "Add a step: run: python -m pytest tests/ -q"
        )


def test_python_job_has_python_paths_trigger():
    """The workflow on.pull_request.paths must include Python source paths."""
    ci = _load_ci()
    paths = (
        ci.get(True, ci.get("on", {}))  # PyYAML parses bare 'on:' as boolean True
        .get("pull_request", {})
        .get("paths", [])
    )
    py_paths = [p for p in paths if ".py" in p or "requirements" in p]
    assert py_paths, (
        "ci.yml paths filter has no Python entries. "
        "Add '**.py' and 'requirements*.txt' so the Python job triggers on source changes."
    )
