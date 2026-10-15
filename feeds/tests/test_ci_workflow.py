"""Tests for the repository's GitHub Actions workflow."""

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

    def test_package_requires_the_documented_django_floor(self):
        setup = (self.root / "setup.py").read_text(encoding="utf-8")

        self.assertIn('"Django>=4.2"', setup)
        self.assertIn('"Framework :: Django :: 4.2"', setup)
        self.assertNotIn("Django :: 3.2", setup)
