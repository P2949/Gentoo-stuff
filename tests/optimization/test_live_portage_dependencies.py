#!/usr/bin/env python3
"""Regression tests for fail-closed Portage dependency extraction."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/optimization/pgo/generate-live-portage-dependencies.py"


def _module():
    spec = importlib.util.spec_from_file_location("live_portage_dependencies", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _module()
    assert module.load_choice_review({"records": [{
        "consumer_cpv": "app/c-1", "field": "RDEPEND",
        "expression_sha256": "a" * 64, "selected_branch": 0,
    }]})
    for bad in (
        {"records": []},
        {"records": [{"consumer_cpv": "app/c-1"}]},
        {"records": [{"consumer_cpv": "app/c-1", "field": "RDEPEND", "expression_sha256": "a" * 64, "selected_branch": 0},
                      {"consumer_cpv": "app/c-1", "field": "RDEPEND", "expression_sha256": "a" * 64, "selected_branch": 1}]},
    ):
        try:
            module.load_choice_review(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid choice review was accepted")
    assert module.atoms("dev-libs/a", ()) == ["dev-libs/a"]
    assert module.atoms("foo? ( dev-libs/a ) !foo? ( dev-libs/b )", ()) == [
        "dev-libs/b"
    ]
    try:
        module.atoms("|| ( dev-libs/a dev-libs/b )", ())
    except module.DependencyChoiceError as exc:
        assert "||" in str(exc)
        assert exc.operator == "||" and exc.branch_count == 2
    else:
        raise AssertionError("dependency alternatives must not be flattened")
    assert module.atoms(
        "|| ( dev-libs/a dev-libs/b )", (), matcher=lambda atom: atom.endswith("/b")
    ) == ["dev-libs/b"]
    try:
        module.atoms(
            "|| ( dev-libs/a dev-libs/b )", (), matcher=lambda atom: True
        )
    except module.DependencyChoiceError as exc:
        assert "2 installed alternatives" in str(exc)
    else:
        raise AssertionError("ambiguous installed alternatives must remain unresolved")
    assert module.atoms(
        "|| ( dev-libs/a dev-libs/b )", (),
        choice_selector=lambda operator, branches: 1,
    ) == ["dev-libs/b"]
    print("PASS: Portage dependency alternatives remain unresolved until selected")


if __name__ == "__main__":
    main()
