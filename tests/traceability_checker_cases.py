from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import override

from scripts.check_test_traceability import validate

ROOT = Path(__file__).resolve().parents[1]


class TraceabilityCheckerMutationTests(unittest.TestCase):
    """Prove that each advertised structural guard rejects representative drift."""

    temporary: tempfile.TemporaryDirectory[str] | None = None
    root: Path = Path()

    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in (
            "docs/architecture",
            "docs/features",
            "feeds/tests",
        ):
            shutil.copytree(ROOT / relative, self.root / relative)

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def assert_rejected(self, pattern: str) -> None:
        with self.assertRaisesRegex(AssertionError, pattern):
            validate(self.root)

    def test_rejects_new_top_level_test_function(self) -> None:
        path = self.root / "feeds/tests/test_api.py"
        path.write_text(
            path.read_text() + "\n\ndef test_new_top_level_contract():\n    pass\n"
        )
        self.assert_rejected("baseline changed")

    def test_rejects_duplicate_ledger_number(self) -> None:
        self.mutate("docs/features/test-traceability.md", "| T002 |", "| T001 |")
        self.assert_rejected("sequential and unique")

    def test_rejects_malformed_ledger_result(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        text = path.read_text()
        text, count = re.subn(r"(\| T001 \|.*?\| )C( \|)", r"\1PASS\2", text, count=1)
        self.assertEqual(count, 1)
        path.write_text(text)
        self.assert_rejected("malformed ledger result")

    def test_rejects_stale_primary_support_summary(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "Ledger classification: **238 primary**, **9 support/redundant**.",
            "Ledger classification: **239 primary**, **8 support/redundant**.",
        )
        self.assert_rejected("stale primary/support")

    def test_rejects_wrong_primary_ownership(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T001 |"))
        cells = lines[index].split("|")
        cells[5] = " AUTH-004 "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("ownership")

    def test_rejects_swapped_c_and_expected_failure(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T001 |"))
        cells = lines[index].split("|")
        cells[6] = " XF "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("does not match decorator")

    def test_rejects_removed_feature_exact_evidence(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| WEB-002 |")
        )
        cells = lines[index].split("|")
        references = cells[2].strip().split("<br>")
        self.assertGreater(len(references), 1)
        cells[2] = f" {'<br>'.join(references[1:])} "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("primary ledger ownership absent from matrix")

    def test_rejects_unknown_mapped_id(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T001 |"))
        cells = lines[index].split("|")
        cells[5] = " UNKNOWN-001 "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("unknown mapped IDs")

    def test_rejects_unknown_mechanism(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T001 |"))
        cells = lines[index].split("|")
        cells[4] = " magic integration "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("unknown ledger mechanism")

    def test_rejects_unknown_dimension(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "positive=C; negative=M; authorization=C",
            "happy=C; negative=M; authorization=C",
        )
        self.assert_rejected("unknown/duplicate dimension")

    def test_rejects_snapshot_hash_mutation(self) -> None:
        path = self.root / "docs/features/catalog.md"
        snapshot = "03965d98aa51522a98266df28aa2ba45e80c03e7"
        path.write_text(path.read_text().replace(snapshot, "0" * 40))
        self.assert_rejected("pinned snapshot claim changed")

    def test_recognizes_qualified_expected_failure(self) -> None:
        path = self.root / "feeds/tests/test_api.py"
        path.write_text(
            path.read_text()
            + "\n\n@unittest.expectedFailure\ndef test_new_qualified_expected_failure():\n"
            + "    raise AssertionError\n"
        )
        self.assert_rejected("expectedFailures=11")

    def test_rejects_stale_intro_feature_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "**82 feature IDs**",
            "**81 feature IDs**",
        )
        self.assert_rejected("stale rendered 82/44/19/247/10 intro")

    def test_rejects_stale_intro_invariant_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "**44 invariant IDs**",
            "**43 invariant IDs**",
        )
        self.assert_rejected("stale rendered 82/44/19/247/10 intro")

    def test_rejects_stale_intro_module_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "**19 app test modules**",
            "**18 app test modules**",
        )
        self.assert_rejected("stale rendered 82/44/19/247/10 intro")

    def test_rejects_stale_intro_method_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "**247 app test methods**",
            "**246 app test methods**",
        )
        self.assert_rejected("stale rendered 82/44/19/247/10 intro")

    def test_rejects_stale_intro_expected_failure_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "**10 `expectedFailure` methods**",
            "**9 `expectedFailure` methods**",
        )
        self.assert_rejected("stale rendered 82/44/19/247/10 intro")

    def test_rejects_stale_broad_expected_failure_count(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "Exact broad gaps: 10 expected failures;",
            "Exact broad gaps: 9 expected failures;",
        )
        self.assert_rejected("stale exact broad gap summary")

    def test_rejects_missing_postgresql_execution_evidence(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "execute real PostgreSQL race and transaction-boundary evidence",
            "describe PostgreSQL coverage",
        )
        self.assert_rejected("stale exact broad gap summary")

    def test_rejects_missing_live_browser_matrix_evidence(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "real Chromium covers 375/768/1280 responsive shared/auth pages",
            "real Chromium covers responsive pages",
        )
        self.assert_rejected("stale exact broad gap summary")

    def test_rejects_stale_contracts_module_count(self) -> None:
        self.mutate(
            "docs/features/contracts.md",
            "current suite: 19 test modules",
            "current suite: 18 test modules",
        )
        self.assert_rejected("stale contracts current-suite 19/247/10 prose")

    def test_rejects_stale_contracts_method_count(self) -> None:
        self.mutate(
            "docs/features/contracts.md", "247 test\nmethods", "246 test\nmethods"
        )
        self.assert_rejected("stale contracts current-suite 19/247/10 prose")

    def test_rejects_stale_contracts_expected_failure_count(self) -> None:
        self.mutate(
            "docs/features/contracts.md", "10 expected failures", "9 expected failures"
        )
        self.assert_rejected("stale contracts current-suite 19/247/10 prose")

    def test_rejects_missing_progressive_browser_primary_evidence(self) -> None:
        self.mutate(
            "docs/features/contracts.md",
            "`test_article_actions_browser.py`, ",
            "",
        )
        self.assert_rejected("stale contracts progressive/a11y/mobile primary evidence")

    def test_rejects_unknown_syntactic_witan_slug(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "tk-create-feature-to-test-traceability-matrix-b382a2",
            "tk-not-a-real-task-123abc",
        )
        self.assert_rejected("unknown Witan task slugs")

    def test_rejects_valid_but_semantically_wrong_mechanism(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T005 |"))
        cells = lines[index].split("|")
        cells[4] = " request/no-DB "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited ledger semantic expectation")

    def test_rejects_executed_dom_mislabeled_as_live_browser(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T038 |"))
        cells = lines[index].split("|")
        cells[4] = " real Playwright browser/SQLite "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited ledger semantic expectation")

    def test_rejects_valid_but_semantically_wrong_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| API-008 |")
        )
        lines[index] = lines[index].replace("concurrency=M", "concurrency=C", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_valid_but_semantically_wrong_task(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| AUTH-003 |")
        )
        lines[index] = lines[index].replace(
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
            1,
        )
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix task mapping changed")

    def test_rejects_changed_historical_production_status(self) -> None:
        self.mutate(
            "docs/features/test-traceability.md",
            "C at pinned revision; not current HEAD",
            "D historical; not current HEAD",
        )
        self.assert_rejected("audited historical/current production")

    def test_rejects_stale_second_contracts_expected_failure_count(self) -> None:
        self.mutate(
            "docs/features/contracts.md",
            "The current suite contains **10 expected failures**:",
            "The current suite contains **9 expected failures**:",
        )
        self.assert_rejected("stale contracts repeated 10-expected-failures sentence")

    def test_rejects_wrong_t149_mocked_outbound_mechanism(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T149 |"))
        cells = lines[index].split("|")
        cells[4] = " request/SQLite "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited ledger semantic expectation")

    def test_rejects_postgresql_race_disguised_as_fault_injection(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| T204 |"))
        cells = lines[index].split("|")
        cells[4] = " service/PostgreSQL fault injection "
        lines[index] = "|".join(cells)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited ledger semantic expectation")

    def test_rejects_stale_postgresql_concurrency_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| WEB-019 |")
        )
        lines[index] = lines[index].replace("concurrency=C", "concurrency=M", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_ing001_external_io_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| ING-001 |")
        )
        lines[index] = lines[index].replace("external-I/O=C", "external-I/O=M", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_ing001_authorization_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| ING-001 |")
        )
        lines[index] = lines[index].replace("authorization=M", "authorization=NA", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_news001_external_io_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| NEWS-001 |")
        )
        lines[index] = lines[index].replace("external-I/O=C", "external-I/O=NA", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_news_inv_001_external_io_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| NEWS-INV-001 |")
        )
        lines[index] = lines[index].replace("external-I/O=C", "external-I/O=NA", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_news_inv_005_external_io_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| NEWS-INV-005 |")
        )
        lines[index] = lines[index].replace("external-I/O=C", "external-I/O=NA", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_ui_inv_004_responsive_dimensions(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| UI-INV-004 |")
        )
        lines[index] = lines[index].replace("mobile=NA", "mobile=M", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("responsive gap lacks exact browser task")

    def test_rejects_wrong_ui_inv_005_responsive_task(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| UI-INV-005 |")
        )
        lines[index] = lines[index].replace(
            "`tk-validate-all-browser-redirect-targets-99209e`",
            "`tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`",
            1,
        )
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix task mapping changed")

    def test_rejects_wrong_auth005_positive_dimension(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| AUTH-005 |")
        )
        lines[index] = lines[index].replace("positive=M", "positive=C", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_wrong_authenticated_adapter_authorization(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| SAVE-004 |")
        )
        lines[index] = lines[index].replace("authorization=M", "authorization=NA", 1)
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("audited matrix dimensions changed")

    def test_rejects_feature_responsive_gap_without_exact_browser_task(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| AUTH-001 |")
        )
        lines[index] = lines[index].replace(
            ", `tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`", "", 1
        )
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("responsive gap lacks exact browser task")

    def test_rejects_invariant_responsive_gap_without_exact_browser_task(self) -> None:
        path = self.root / "docs/features/test-traceability.md"
        lines = path.read_text().splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("| UI-INV-001 |")
        )
        lines[index] = lines[index].replace(
            ", `tk-add-real-browser-responsive-theme-keyboard-and-a-147e09`", "", 1
        )
        path.write_text("\n".join(lines) + "\n")
        self.assert_rejected("responsive gap lacks exact browser task")


if __name__ == "__main__":
    unittest.main()
