"""Build-input contracts for the CPU-only production dashboard image."""

from pathlib import Path


DOCKERFILE = Path(__file__).parents[1] / "docker" / "Dockerfile.dashboard"


def test_dashboard_routes_torch_to_cpu_wheels_before_transformer_install() -> None:
    # Given the production Dockerfile, dependency resolution must select CPU torch.
    instructions = DOCKERFILE.read_text(encoding="utf-8")

    # When locating the package-source selection and the transformer installation.
    cpu_source = instructions.find("https://download.pytorch.org/whl/cpu")
    transformer_install = instructions.find("spacy download en_core_web_trf")

    # Then CUDA wheels cannot be selected by the later transformer dependency.
    assert 0 <= cpu_source < transformer_install, "Select CPU torch before installing the transformer model"


def test_dashboard_includes_vendored_pipeline_rulesets() -> None:
    # Given the vendored ruleset needed by the enabled WMN pipeline.
    instructions = DOCKERFILE.read_text(encoding="utf-8").splitlines()

    # When examining actual copy instructions rather than comments.
    copies = [line.split() for line in instructions if line.startswith("COPY ")]

    # Then the dashboard image contains the same runtime data as the worker image.
    assert ["COPY", "data/", "data/"] in copies, "Dashboard image must include vendored rulesets"
