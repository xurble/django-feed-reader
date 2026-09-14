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
