"""Tests for the repository's GitHub Actions workflow."""

import ast
import re
from pathlib import Path
from unittest import TestCase


class CIWorkflowPermissionsTests(TestCase):
    def test_jobs_inherit_only_read_access_to_repository_contents(self):
        workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        permissions_match = re.search(
            r"(?m)^permissions:\n(?P<body>(?:  [^\n]+\n)+)", workflow
        )

        self.assertIsNotNone(permissions_match)
        self.assertEqual(permissions_match.group("body"), "  contents: read\n")
        self.assertNotRegex(workflow, r"(?m)^ +permissions:")


class QualityToolingTests(TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_ci_runs_the_canonical_quality_gate(self):
        workflow = (self.root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn('pip install -e ".[quality]"', workflow)
        self.assertIn("run: ./_check.sh", workflow)

    def test_quality_gate_is_check_only_and_complete(self):
        quality_gate = (self.root / "_check.sh").read_text(encoding="utf-8")

        self.assertIn("ruff format --check .", quality_gate)
        self.assertIn("ruff check .", quality_gate)
        self.assertIn("bandit -c pyproject.toml -r feeds", quality_gate)
        self.assertIn("coverage run -m pytest --reuse-db", quality_gate)
        self.assertIn("python -m sphinx -W --keep-going", quality_gate)
        self.assertIn("python -m build", quality_gate)
        self.assertIn("--no-isolation", quality_gate)
        self.assertIn('"$TMPDIR_QUALITY/source"', quality_gate)
        self.assertIn("python -m twine check", quality_gate)
        self.assertNotIn("--fix", quality_gate)

    def test_auto_fix_remains_a_separate_command(self):
        auto_fix = (self.root / "_lint.sh").read_text(encoding="utf-8")

        self.assertIn("ruff check --fix .", auto_fix)
        self.assertIn("ruff format .", auto_fix)

    def test_quality_configuration_excludes_generated_code(self):
        configuration = (self.root / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('exclude = ["feeds/migrations"]', configuration)
        self.assertIn(
            'exclude_dirs = ["feeds/migrations", "feeds/tests"]', configuration
        )

    def test_package_metadata_matches_the_supported_runtime_matrix(self):
        setup = ast.parse((self.root / "setup.py").read_text(encoding="utf-8"))
        setup_calls = [
            node
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "setuptools"
            and node.func.attr == "setup"
        ]
        self.assertEqual(len(setup_calls), 1)
        metadata = {keyword.arg: keyword.value for keyword in setup_calls[0].keywords}

        self.assertEqual(ast.literal_eval(metadata["python_requires"]), ">=3.10,<3.13")
        self.assertIn(
            "Django>=4.2,<5.2", ast.literal_eval(metadata["install_requires"])
        )
        classifiers = ast.literal_eval(metadata["classifiers"])
        for version in ("3.10", "3.11", "3.12"):
            self.assertIn(f"Programming Language :: Python :: {version}", classifiers)
        self.assertIn("Framework :: Django :: 4.2", classifiers)
        self.assertIn("Framework :: Django :: 5.0", classifiers)
        self.assertIn("Framework :: Django :: 5.1", classifiers)
        self.assertNotIn("Framework :: Django :: 3.2", classifiers)

    def test_ci_covers_supported_matrix_boundaries_without_cross_products(self):
        workflow = (self.root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        rows = re.findall(
            r'- python-version: "([^"]+)"\n\s+django-version: "([^"]+)"'
            r'\n\s+django-spec: "([^"]+)"',
            workflow,
        )

        self.assertCountEqual(
            rows,
            [
                ("3.10", "4.2", "Django>=4.2,<4.3"),
                ("3.10", "5.1", "Django>=5.1,<5.2"),
                ("3.11", "5.0", "Django>=5.0,<5.1"),
                ("3.12", "4.2", "Django>=4.2,<4.3"),
                ("3.12", "5.1", "Django>=5.1,<5.2"),
            ],
        )

    def test_user_documentation_matches_the_supported_runtime_matrix(self):
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        overview = (self.root / "docs/modules/overview.rst").read_text(encoding="utf-8")

        for documentation in (readme, overview):
            self.assertIn("Python 3.10 through 3.12", documentation)
            self.assertIn("Django 4.2 through 5.1", documentation)
            self.assertIn("hard package bounds", documentation)
