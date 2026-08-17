"""Validate the live feature-to-test traceability artifact and its evidence ownership."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

EXPECTED_MODULES = 28
EXPECTED_TESTS = 413
EXPECTED_XFAILS = 2
DIMENSIONS = (
    "positive",
    "negative",
    "authorization",
    "validation",
    "idempotency",
    "concurrency",
    "external-I/O",
    "mobile",
    "theme",
    "accessibility",
    "configuration",
    "migration",
    "deployment",
    "production",
)
STATUSES = {"C", "XF", "M", "D", "NA"}
LEVELS = {
    "none",
    "pure unit",
    "model/SQLite",
    "form/model SQLite",
    "service/SQLite",
    "request/no-DB",
    "request/SQLite",
    "API/SQLite",
    "command/SQLite",
    "settings subprocess",
    "source assertion",
    "request/markup",
    "executed DOM/Chromium",
    "Compose render",
    "real Playwright browser/SQLite",
    "model/PostgreSQL 17",
    "service/PostgreSQL 17",
    "migration introspection/PostgreSQL 17",
    "historical production observation",
}
MECHANISMS = {
    "pure unit",
    "pure unit with mocked outbound I/O",
    "pure unit compatibility guard",
    "support fixture unit",
    "model/SQLite",
    "form/model SQLite",
    "service/SQLite",
    "service/SQLite with mocked outbound I/O",
    "request/no-DB",
    "request/SQLite",
    "request/markup assertion",
    "request/SQLite with mocked outbound I/O",
    "API/SQLite",
    "API/SQLite with mocked outbound I/O",
    "API/SQLite with simulated inbound webhook",
    "command/SQLite",
    "settings subprocess",
    "source assertion",
    "executed DOM/Chromium",
    "Compose render",
    "real Playwright browser/SQLite",
    "PostgreSQL 17 configuration",
    "migration introspection/PostgreSQL 17",
    "model/PostgreSQL transaction.atomic",
    "model/PostgreSQL separate connections/barrier",
    "request/PostgreSQL separate connections",
    "service/PostgreSQL separate connections/barrier",
    "service/PostgreSQL separate connections/events",
    "service/PostgreSQL separate connections/row lock",
    "service/PostgreSQL fault injection",
}
FEATURE_PATTERN = r"(?:AUTH|WEB|ING|NEWS|SAVE|API|OPS)-\d{3}"
INVARIANT_PATTERN = r"[A-Z]+(?:-[A-Z]+)*-INV-\d{3}"
IDENTITY_PATTERN = (
    r"feeds/tests/test_[^`:\s]+\.py::(?:[A-Za-z_][A-Za-z0-9_]*|<module>)"
    r"::test_[A-Za-z0-9_]+"
)
TASK_PATTERN = re.compile(r"tk-[a-z0-9-]+")
KNOWN_WITAN_TASKS = {
    "tk-add-explicit-api-capabilities-and-replace-replay-69180c",
    "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
    "tk-add-required-postgresql-integration-and-concurre-0719fc",
    "tk-add-structured-observability-health-readiness-an-16ba73",
    "tk-complete-browser-view-form-command-and-api-contr-55d622",
    "tk-create-feature-to-test-traceability-matrix-b382a2",
    "tk-document-and-rehearse-backup-restore-rollback-an-3bf520",
    "tk-enforce-bulkreadmarker-scope-and-uniqueness-inva-db62c6",
    "tk-establish-production-postgresql-backup-and-resto-01b0c1",
    "tk-execute-javascript-behavior-tests-instead-of-sou-141553",
    "tk-extract-shared-article-visibility-and-read-state-6708e0",
    "tk-extract-validated-transactional-commands-for-mut-466545",
    "tk-harden-api-operations-security-and-release-verif-5f790a",
    "tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c",
    "tk-harden-postmark-authentication-payload-limits-an-601f5f",
    "tk-implement-recoverable-linkding-delivery-state-ma-2ec860",
    "tk-make-opml-import-export-atomic-validated-and-rou-df59b7",
    "tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e",
    "tk-reconcile-article-identity-and-concurrent-refres-7a1b44",
    "tk-validate-all-browser-redirect-targets-99209e",
}
REQUIRED_HEADINGS = {
    "## Feature matrix",
    "## Cross-feature invariant matrix",
    "## Viewport, theme, focus, and compact matrix",
    "## Keyboard and accessibility matrix",
    "## External-I/O failure matrix",
    "## PostgreSQL concurrency matrix",
    "## Job, heartbeat, restart, and deployment matrix",
    "## Configuration, migration, backup, restore, and production-smoke matrix",
    "## Complete current-test ledger",
    "## Maintenance and review protocol",
}
INTRO_BASELINE = (
    "Current AST baseline: **82 feature IDs**, **47 invariant IDs**, "
    "**28 app test modules**, **413 app test methods**, and "
    "**2 `expectedFailure` methods**."
)
BROAD_GAP_BASELINE = (
    "Exact broad gaps: 2 expected failures; 20 PostgreSQL 17 integration tests "
    "(0 focused XFs) execute real PostgreSQL race and transaction-boundary evidence; "
    "zero automated canonical launched-Compose tests; one real receipt-backed "
    "production backup and isolated restore but no scheduled/alerted RPO or incident "
    "cutover drill and no independent off-site confirmation; zero automated "
    "current-HEAD production observations; real Chromium covers 375/768/1280 "
    "responsive Login/Today/Week/Feeds/Preferences/OPML pages, a tested-theme/mode "
    "cross-product, clipboard, live-page keyboard/help behavior, and selected DOM "
    "semantic checks; comprehensive scanning, non-Chromium engines, automated "
    "contrast, screen-reader and other manual AT tests remain."
)
CONTRACTS_EXPECTED_FAILURE_SENTENCE = (
    "The current suite contains **2 expected failures**."
)
CONTRACTS_CURRENT_SUITE = (
    "This post-snapshot companion maps the **current suite: 28 test modules, "
    "413 test methods, and 2 expected failures**."
)
CONTRACTS_PROGRESSIVE_EVIDENCE = (
    "| Progressive/a11y/mobile | UI-INV-001–005 | `test_article_actions.py`, "
    "`test_article_actions_browser.py`, `test_browser_redirects.py`, `test_digest_views.py`, "
    "`test_mobile_today_browser.py`, `test_responsive_accessibility_browser.py`, "
    "`test_known_correctness_failures.py`, `test_behavioral_contracts.py` |"
)
PINNED_TEXT = {
    "docs/architecture/current-state.md": (
        "> Snapshot: commit [`a0c62da2913078e0ac0e9e0fe1cfdd69e51a7823`](https://github.com/feoh/daily-firehose/tree/a0c62da2913078e0ac0e9e0fe1cfdd69e51a7823), committed 2026-08-11.",
        "The canonical production checkout was deployed at revision `a0c62da2913078e0ac0e9e0fe1cfdd69e51a7823`",
    ),
    "docs/features/catalog.md": (
        "> Snapshot: commit [`03965d98aa51522a98266df28aa2ba45e80c03e7`](https://github.com/feoh/daily-firehose/tree/03965d98aa51522a98266df28aa2ba45e80c03e7).",
        "| **Total** | **82 IDs** | **72 fact, 8 known-defect, 1 deferred, 1 unknown** |",
        "**15/15** snapshot executable test modules mapped; **191** `def test_...` methods",
        "**8/8** snapshot expected failures mapped",
    ),
    "docs/features/contracts.md": (
        "**current suite: 28 test modules, 413 test",
        "15/191/8 snapshot counts",
    ),
}

# These selected records are independently audited semantic sentinels. The general
# mechanism vocabulary remains structural and does not replace full human review.
AUDITED_LEDGER = {
    5: ("test_api_can_save_article", "primary", "API/SQLite", ("API-009",)),
    6: (
        "test_refresh_api_reports_partial_failures_and_backoff",
        "primary",
        "API/SQLite",
        ("API-016",),
    ),
    8: (
        "test_signed_save_and_go_action_saves_and_redirects",
        "primary",
        "API/SQLite",
        ("API-018", "API-COMPAT-INV-002"),
    ),
    38: (
        "test_read_and_save_forms_use_the_ajax_enhancement_contract",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "UI-INV-001"),
    ),
    50: (
        "test_week_is_inclusive_monday_through_sunday_across_year_boundary",
        "primary",
        "request/SQLite",
        ("WEB-003", "READ-INV-001"),
    ),
    51: (
        "test_month_includes_leap_day_and_excludes_adjacent_months",
        "primary",
        "request/SQLite",
        ("WEB-003", "READ-INV-001"),
    ),
    52: (
        "test_december_month_is_inclusive_without_leaking_into_next_year",
        "primary",
        "request/SQLite",
        ("WEB-003", "READ-INV-001"),
    ),
    53: (
        "test_bulk_cutoff_includes_exact_equality_and_excludes_later_fetches",
        "primary",
        "request/SQLite",
        ("WEB-011", "READ-INV-004"),
    ),
    54: (
        "test_repeated_refresh_updates_one_article_without_resetting_first_seen",
        "primary",
        "service/SQLite with mocked outbound I/O",
        ("ING-005", "DATA-INV-002", "FEED-INV-002"),
    ),
    55: (
        "test_repeated_save_keeps_one_row_and_saved_at_but_refreshes_snapshots",
        "primary",
        "service/SQLite with mocked outbound I/O",
        ("SAVE-001", "SAVE-INV-001"),
    ),
    56: (
        "test_refresh_excludes_inactive_feeds",
        "primary",
        "service/SQLite",
        ("ING-007", "FEED-INV-001"),
    ),
    57: (
        "test_api_soft_delete_preserves_content_and_orm_deletes_cascade",
        "primary",
        "API/SQLite",
        ("ING-002", "API-012", "DATA-INV-004", "FEED-INV-003"),
    ),
    58: (
        "test_opml_reimport_reactivates_existing_feed",
        "primary",
        "service/SQLite",
        ("ING-011", "FEED-INV-004"),
    ),
    59: (
        "test_new_synthetic_newsletter_feed_is_inactive",
        "primary",
        "service/SQLite",
        ("NEWS-001", "NEWS-INV-003"),
    ),
    60: (
        "test_existing_synthetic_newsletter_feed_keeps_active_state",
        "primary",
        "service/SQLite",
        ("NEWS-001", "NEWS-INV-003"),
    ),
    61: (
        "test_public_newsletter_get_creates_no_read_save_preference_or_open_state",
        "primary",
        "request/SQLite",
        ("NEWS-003", "NEWS-005", "NEWS-INV-004"),
    ),
    62: (
        "test_bearer_method_precedes_auth_and_auth_precedes_validation",
        "primary",
        "API/SQLite",
        ("API-003", "API-AUTH-INV-001"),
    ),
    63: (
        "test_token_alias_scheme_casing_and_inactive_principals",
        "primary",
        "API/SQLite",
        ("API-002", "API-AUTH-INV-002"),
    ),
    64: (
        "test_briefing_action_templates_and_article_urls_are_exact",
        "primary",
        "API/SQLite",
        ("API-005", "API-006", "API-CAP-INV-001"),
    ),
    65: (
        "test_legacy_digest_is_method_agnostic_after_session_and_csrf_boundary",
        "primary",
        "request/SQLite",
        ("API-001", "API-COMPAT-INV-001"),
    ),
    66: (
        "test_postmark_persists_invalid_emails_without_model_clean",
        "primary",
        "API/SQLite with simulated inbound webhook",
        ("NEWS-001", "NEWS-INV-005"),
    ),
    67: (
        "test_opml_export_import_round_trip_preserves_category",
        "primary",
        "service/SQLite",
        ("ING-013", "FEED-INV-005"),
    ),
    68: (
        "test_opml_reuses_same_name_category_with_different_slug",
        "primary",
        "service/SQLite",
        ("ING-011", "FEED-INV-006"),
    ),
    69: (
        "test_all_authenticated_get_responses_are_private_no_store",
        "primary",
        "API/SQLite",
        ("UI-INV-004",),
    ),
    70: (
        "test_negative_path_ids_use_auth_and_json_validation_envelopes",
        "primary",
        "API/SQLite",
        ("API-SCHEMA-INV-002",),
    ),
    149: (
        "test_feed_creation_shows_fetch_error_without_writes",
        "primary",
        "request/SQLite with mocked outbound I/O",
        ("ING-001", "ING-003", "WEB-017"),
    ),
    183: (
        "test_admin_cannot_create_or_reassign_a_save_to_a_newsletter",
        "primary",
        "form/model SQLite",
        ("AUTH-005", "SAVE-INV-004"),
    ),
    201: (
        "test_lane_runs_against_postgresql_17",
        "primary",
        "PostgreSQL 17 configuration",
        ("OPS-002",),
    ),
    202: (
        "test_disk_migrations_and_durable_unique_constraints_are_applied",
        "primary",
        "migration introspection/PostgreSQL 17",
        ("OPS-002", "OPS-INV-003"),
    ),
    203: (
        "test_bulk_marker_shapes_are_rejected_by_database_constraints",
        "primary",
        "model/PostgreSQL transaction.atomic",
        ("WEB-019", "READ-INV-006"),
    ),
    204: (
        "test_nullable_period_marker_duplicate_race_commits_one_row",
        "primary",
        "model/PostgreSQL separate connections/barrier",
        ("WEB-019", "API-010", "READ-INV-006"),
    ),
    205: (
        "test_nullable_feed_marker_duplicate_race_commits_one_row",
        "primary",
        "model/PostgreSQL separate connections/barrier",
        ("WEB-019", "API-013", "READ-INV-006"),
    ),
    206: (
        "test_saved_article_duplicate_race_is_stopped_by_unique_constraint",
        "primary",
        "model/PostgreSQL separate connections/barrier",
        ("SAVE-001", "API-009", "SAVE-INV-001"),
    ),
    207: (
        "test_concurrent_postmark_replay_returns_one_issue_without_errors",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("NEWS-001", "API-017", "NEWS-INV-001"),
    ),
    208: (
        "test_postmark_issue_failure_rolls_back_feed_and_article",
        "primary",
        "service/PostgreSQL fault injection",
        ("NEWS-002", "NEWS-INV-002"),
    ),
    209: (
        "test_concurrent_same_guid_refresh_has_one_owning_create_and_superseded_zero_writes",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("ING-005", "FEED-INV-002"),
    ),
    210: (
        "test_concurrent_changed_guids_for_one_url_are_reconciled",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("ING-005", "ING-006", "DATA-INV-001", "FEED-INV-002"),
    ),
    211: (
        "test_older_refresh_failure_cannot_overwrite_newer_success_status",
        "primary",
        "service/PostgreSQL separate connections/events",
        ("ING-007", "FEED-INV-001", "OPS-INV-001"),
    ),
    212: (
        "test_older_success_cannot_overwrite_newer_failure_status",
        "primary",
        "service/PostgreSQL separate connections/events",
        ("ING-007", "FEED-INV-001", "OPS-INV-001"),
    ),
    213: (
        "test_concurrent_preference_get_or_create_returns_one_row",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("API-015",),
    ),
    214: (
        "test_concurrent_category_upsert_returns_one_row_without_errors",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("ING-011", "API-014", "FEED-INV-006"),
    ),
    215: (
        "test_concurrent_opml_feed_upsert_returns_one_row",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("ING-011", "FEED-INV-004"),
    ),
    216: (
        "test_concurrent_newsletter_feed_get_or_create_returns_one_row",
        "primary",
        "service/PostgreSQL separate connections/barrier",
        ("NEWS-001", "NEWS-INV-003"),
    ),
    217: (
        "test_superseded_refresh_failure_has_no_terminal_state_side_effect",
        "primary",
        "service/PostgreSQL separate connections/row lock",
        ("ING-007", "FEED-INV-001", "OPS-INV-001"),
    ),
    218: (
        "test_bad_production_configuration_fails_closed_without_secret_values",
        "primary",
        "settings subprocess",
        ("OPS-001", "OPS-002", "OPS-003"),
    ),
    219: (
        "test_valid_production_settings_preserve_discrete_reserved_characters",
        "primary",
        "settings subprocess",
        ("AUTH-003", "OPS-001", "OPS-002", "OPS-003", "OPS-004"),
    ),
    220: (
        "test_valid_production_database_url_remains_supported",
        "primary",
        "settings subprocess",
        ("OPS-002",),
    ),
    221: (
        "test_development_defaults_remain_zero_configuration",
        "primary",
        "settings subprocess",
        ("OPS-001", "OPS-002", "OPS-003"),
    ),
    222: (
        "test_development_discrete_postgres_preserves_reserved_characters",
        "primary",
        "settings subprocess",
        ("OPS-002",),
    ),
    223: (
        "test_production_deploy_check_has_no_unsilenced_warnings",
        "primary",
        "settings subprocess",
        ("OPS-004", "OPS-009", "OPS-INV-003"),
    ),
    224: (
        "test_canonical_compose_is_fail_closed_and_orders_the_worker",
        "primary",
        "source assertion",
        ("OPS-008",),
    ),
    225: (
        "test_missing_env_defaults_application_services_to_production",
        "primary",
        "Compose render",
        ("OPS-001", "OPS-002", "OPS-008"),
    ),
    226: (
        "test_development_env_and_reserved_password_survive_compose",
        "primary",
        "Compose render",
        ("OPS-002", "OPS-008"),
    ),
    227: (
        "test_direct_http_redirects_to_https",
        "primary",
        "request/no-DB",
        ("OPS-003",),
    ),
    228: (
        "test_forwarded_https_is_trusted_without_a_redirect_loop",
        "primary",
        "request/no-DB",
        ("AUTH-002", "OPS-003"),
    ),
    229: (
        "test_csrf_cookie_is_secure_on_forwarded_https",
        "primary",
        "request/no-DB",
        ("AUTH-003", "OPS-003"),
    ),
    230: (
        "test_form_id_mismatch_keeps_both_cards_without_fetching",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "SAVE-003"),
    ),
    231: (
        "test_pending_actions_disable_and_suppress_duplicate_and_repeated_submits",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012",),
    ),
    232: (
        "test_success_removes_only_the_submitted_card_and_selects_the_survivor",
        "primary",
        "executed DOM/Chromium",
        ("WEB-008", "WEB-012"),
    ),
    233: (
        "test_inline_success_and_error_states_keep_forms_retryable",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "WEB-017"),
    ),
    234: (
        "test_j_and_k_select_and_focus_articles_and_feeds",
        "primary",
        "executed DOM/Chromium",
        ("WEB-013",),
    ),
    235: (
        "test_s_and_m_submit_the_selected_cards_matching_forms",
        "primary",
        "executed DOM/Chromium",
        ("WEB-013",),
    ),
    236: (
        "test_o_opens_the_selected_article",
        "primary",
        "executed DOM/Chromium",
        ("WEB-013",),
    ),
    237: (
        "test_shortcuts_are_suppressed_for_every_editable_element",
        "primary",
        "executed DOM/Chromium",
        ("WEB-013", "WEB-021", "UI-INV-002"),
    ),
    238: (
        "test_help_opens_and_closes_by_button_and_escape_with_focus_restored",
        "primary",
        "executed DOM/Chromium",
        ("WEB-001", "WEB-014", "WEB-021", "UI-INV-002"),
    ),
    239: (
        "test_read_and_save_native_forms_submit_without_javascript",
        "primary",
        "real Playwright browser/SQLite",
        ("WEB-008", "WEB-012", "SAVE-003", "UI-INV-001"),
    ),
    240: (
        "test_form_url_mismatch_keeps_both_cards_without_fetching",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "SAVE-003"),
    ),
    241: (
        "test_response_id_mismatch_keeps_both_cards",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "SAVE-003"),
    ),
    242: (
        "test_response_url_mismatch_keeps_both_cards",
        "primary",
        "executed DOM/Chromium",
        ("WEB-012", "SAVE-003"),
    ),
}

AUDITED_LEVELS = {
    "AUTH-002": "request/no-DB",
    "AUTH-005": "form/model SQLite",
    "ING-002": "API/SQLite",
    "API-002": "API/SQLite",
    "ING-010": "none",
    "WEB-013": "real Playwright browser/SQLite",
    "WEB-014": "real Playwright browser/SQLite",
    "WEB-017": "executed DOM/Chromium",
    "WEB-021": "real Playwright browser/SQLite",
}

AUDITED_MATRIX = {
    "WEB-001": (
        {"positive": "C", "negative": "C", "accessibility": "M"},
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "WEB-012": (
        {"negative": "C", "validation": "C", "accessibility": "C"},
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "WEB-013": (
        {"positive": "C", "negative": "C", "validation": "M", "accessibility": "C"},
        {"tk-add-real-browser-responsive-theme-keyboard-and-a-147e09"},
    ),
    "WEB-014": (
        {"positive": "C", "negative": "C", "accessibility": "C"},
        {"tk-add-real-browser-responsive-theme-keyboard-and-a-147e09"},
    ),
    "WEB-017": (
        {"positive": "C", "negative": "C", "accessibility": "C"},
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "WEB-021": (
        {"positive": "C", "negative": "C", "accessibility": "C"},
        {"tk-add-real-browser-responsive-theme-keyboard-and-a-147e09"},
    ),
    "UI-INV-001": (
        {"negative": "C", "validation": "C", "accessibility": "C"},
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "UI-INV-002": (
        {"negative": "C", "accessibility": "C"},
        {"tk-add-real-browser-responsive-theme-keyboard-and-a-147e09"},
    ),
    "AUTH-003": (
        {"mobile": "NA", "accessibility": "NA", "production": "NA"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "WEB-018": (
        {
            "positive": "C",
            "negative": "C",
            "authorization": "C",
            "validation": "C",
            "mobile": "NA",
            "theme": "NA",
            "accessibility": "NA",
            "configuration": "C",
        },
        set(),
    ),
    "WEB-019": (
        {
            "validation": "C",
            "concurrency": "C",
            "mobile": "NA",
            "theme": "NA",
            "accessibility": "NA",
            "migration": "C",
        },
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "AUTH-005": (
        {"positive": "M", "negative": "C", "validation": "C"},
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "ING-001": (
        {
            "authorization": "M",
            "external-I/O": "C",
            "mobile": "M",
            "accessibility": "M",
            "production": "NA",
        },
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "ING-008": (
        {
            "authorization": "M",
            "mobile": "M",
            "accessibility": "M",
            "production": "NA",
        },
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
            "tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c",
        },
    ),
    "ING-010": (
        {"positive": "M", "negative": "M", "production": "NA"},
        {"tk-add-structured-observability-health-readiness-an-16ba73"},
    ),
    "ING-011": (
        {
            "authorization": "M",
            "mobile": "M",
            "accessibility": "M",
            "production": "NA",
        },
        {
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
        },
    ),
    "ING-012": (
        {"authorization": "M"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "ING-013": (
        {"authorization": "M"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "NEWS-001": (
        {"concurrency": "C", "external-I/O": "C"},
        {"tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e"},
    ),
    "NEWS-INV-001": (
        {"idempotency": "C", "concurrency": "C", "external-I/O": "C"},
        {"tk-make-postmark-newsletter-ingestion-atomic-and-ra-e3d06e"},
    ),
    "NEWS-INV-005": (
        {"external-I/O": "C"},
        {"tk-harden-postmark-authentication-payload-limits-an-601f5f"},
    ),
    "NEWS-003": (
        {"mobile": "M", "accessibility": "M", "production": "NA"},
        {
            "tk-harden-postmark-authentication-payload-limits-an-601f5f",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
        },
    ),
    "NEWS-004": (
        {"mobile": "M", "accessibility": "M", "production": "NA"},
        {
            "tk-harden-postmark-authentication-payload-limits-an-601f5f",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "NEWS-005": (
        {"mobile": "M", "accessibility": "M", "production": "NA"},
        {
            "tk-harden-postmark-authentication-payload-limits-an-601f5f",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "SAVE-002": (
        {"idempotency": "M", "external-I/O": "C", "mobile": "C", "production": "NA"},
        {"tk-implement-recoverable-linkding-delivery-state-ma-2ec860"},
    ),
    "SAVE-003": (
        {
            "authorization": "M",
            "mobile": "C",
            "accessibility": "C",
            "production": "NA",
        },
        {
            "tk-complete-browser-view-form-command-and-api-contr-55d622",
            "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09",
        },
    ),
    "SAVE-004": (
        {"authorization": "M"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "UI-INV-004": (
        {"mobile": "NA", "theme": "NA", "accessibility": "NA"},
        {"tk-harden-api-operations-security-and-release-verif-5f790a"},
    ),
    "UI-INV-005": (
        {
            "positive": "C",
            "negative": "C",
            "authorization": "C",
            "validation": "C",
            "mobile": "NA",
            "theme": "NA",
            "accessibility": "NA",
            "configuration": "C",
        },
        set(),
    ),
    "API-008": (
        {"concurrency": "M", "production": "NA"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "API-011": (
        {"external-I/O": "C", "production": "NA"},
        {"tk-complete-browser-view-form-command-and-api-contr-55d622"},
    ),
    "API-018": (
        {"external-I/O": "M", "production": "NA"},
        {"tk-harden-api-operations-security-and-release-verif-5f790a"},
    ),
    "OPS-002": (
        {"migration": "C", "production": "M"},
        {"tk-establish-production-postgresql-backup-and-resto-01b0c1"},
    ),
    "OPS-005": (
        {"external-I/O": "M", "production": "M"},
        {
            "tk-harden-ingestion-external-i-o-and-data-integrity-1adf3c",
            "tk-harden-postmark-authentication-payload-limits-an-601f5f",
            "tk-implement-recoverable-linkding-delivery-state-ma-2ec860",
            "tk-add-structured-observability-health-readiness-an-16ba73",
        },
    ),
    "OPS-INV-005": (
        {"positive": "M", "negative": "C"},
        {"tk-add-structured-observability-health-readiness-an-16ba73"},
    ),
    "SAVE-INV-003": (
        {"external-I/O": "C"},
        {"tk-implement-recoverable-linkding-delivery-state-ma-2ec860"},
    ),
}


@dataclass(frozen=True)
class TestRecord:
    identity: str
    expected_failure: bool


@dataclass(frozen=True)
class MatrixRecord:
    stable_id: str
    evidence: tuple[str, ...]
    level: str
    dimensions: dict[str, str]
    gap: str


@dataclass(frozen=True)
class LedgerRecord:
    number: int
    identity: str
    classification: str
    mechanism: str
    mapped_ids: tuple[str, ...]
    result: str
    purpose: str


def _is_expected_failure(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Name):
        return decorator.id == "expectedFailure"
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == "expectedFailure"
    if isinstance(decorator, ast.Call):
        return _is_expected_failure(decorator.func)
    return False


def test_inventory(root: Path) -> tuple[dict[str, TestRecord], int]:
    inventory: dict[str, TestRecord] = {}
    paths = sorted((root / "feeds/tests").glob("test_*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        relative = path.relative_to(root).as_posix()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    identity = f"{relative}::<module>::{node.name}"
                    inventory[identity] = TestRecord(
                        identity,
                        any(_is_expected_failure(item) for item in node.decorator_list),
                    )
                continue
            if not isinstance(node, ast.ClassDef):
                continue
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not method.name.startswith("test_"):
                    continue
                identity = f"{relative}::{node.name}::{method.name}"
                inventory[identity] = TestRecord(
                    identity,
                    any(_is_expected_failure(item) for item in method.decorator_list),
                )
    return inventory, len(paths)


def _section(text: str, heading: str, next_heading: str) -> str:
    try:
        return text.split(heading, 1)[1].split(next_heading, 1)[0]
    except IndexError as exc:
        raise AssertionError(f"missing or out-of-order section: {heading}") from exc


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|") or re.match(r"^\|\s*-", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows[1:]


def _parse_evidence(cell: str) -> tuple[str, ...]:
    if cell == "—":
        return ()
    references = tuple(re.findall(rf"`({IDENTITY_PATTERN})`", cell))
    residue = re.sub(rf"`{IDENTITY_PATTERN}`", "", cell).replace("<br>", "").strip()
    if residue or not references or len(references) != len(set(references)):
        raise AssertionError(f"malformed or duplicate exact evidence cell: {cell}")
    return references


def _parse_dimensions(cell: str) -> dict[str, str]:
    parts = cell.split("; ")
    parsed: dict[str, str] = {}
    for part in parts:
        if part.count("=") != 1:
            raise AssertionError(f"malformed dimension: {part}")
        name, status = part.split("=", 1)
        if name not in DIMENSIONS or status not in STATUSES or name in parsed:
            raise AssertionError(f"unknown/duplicate dimension or status: {part}")
        parsed[name] = status
    if tuple(parsed) != DIMENSIONS:
        raise AssertionError(
            f"dimensions must be individual and ordered: got {tuple(parsed)}"
        )
    return parsed


def _parse_matrix(section: str, valid_ids: set[str], label: str) -> list[MatrixRecord]:
    records: list[MatrixRecord] = []
    for cells in _table_rows(section):
        if len(cells) != 5:
            raise AssertionError(f"{label} row must have five cells: {cells}")
        stable_id, evidence_cell, level, dimensions_cell, gap = cells
        if stable_id not in valid_ids:
            raise AssertionError(f"unknown {label} ID: {stable_id}")
        evidence = _parse_evidence(evidence_cell)
        if level not in LEVELS:
            raise AssertionError(f"unknown evidence level: {level}")
        if not evidence and level != "none":
            raise AssertionError(f"{stable_id} has no evidence but level {level}")
        if evidence and level == "none":
            raise AssertionError(f"{stable_id} has evidence but level none")
        dimensions = _parse_dimensions(dimensions_cell)
        if not evidence and any(
            status in {"C", "XF"} for status in dimensions.values()
        ):
            raise AssertionError(
                f"{stable_id} claims covered/expected-failure dimensions without evidence"
            )
        if any(
            status in {"M", "D", "XF"} for status in dimensions.values()
        ) and not TASK_PATTERN.search(gap):
            raise AssertionError(f"{stable_id} gap lacks an explicit Witan task slug")
        records.append(MatrixRecord(stable_id, evidence, level, dimensions, gap))
    ids = [record.stable_id for record in records]
    if len(ids) != len(set(ids)) or set(ids) != valid_ids:
        raise AssertionError(
            f"{label} ID drift: missing={sorted(valid_ids - set(ids))}, "
            f"extra={sorted(set(ids) - valid_ids)}"
        )
    return records


def _parse_id_cell(cell: str, valid_ids: set[str]) -> tuple[str, ...]:
    if cell == "—":
        return ()
    ids = tuple(item.strip() for item in cell.split(","))
    if len(ids) != len(set(ids)):
        raise AssertionError(f"duplicate mapped ID: {cell}")
    unknown = set(ids) - valid_ids
    if unknown:
        raise AssertionError(f"unknown mapped IDs: {sorted(unknown)}")
    return ids


def _parse_ledger(section: str, valid_ids: set[str]) -> list[LedgerRecord]:
    records: list[LedgerRecord] = []
    for cells in _table_rows(section):
        if len(cells) != 7:
            raise AssertionError(f"ledger row must have seven cells: {cells}")
        (
            number_cell,
            identity_cell,
            classification,
            mechanism,
            ids_cell,
            result,
            purpose,
        ) = cells
        match = re.fullmatch(r"T(\d{3})", number_cell)
        if not match:
            raise AssertionError(f"malformed ledger number: {number_cell}")
        identity_match = re.fullmatch(rf"`({IDENTITY_PATTERN})`", identity_cell)
        if not identity_match:
            raise AssertionError(f"malformed ledger identity: {identity_cell}")
        if classification not in {"primary", "support/redundant"}:
            raise AssertionError(f"unknown ledger classification: {classification}")
        if mechanism not in MECHANISMS:
            raise AssertionError(f"unknown ledger mechanism: {mechanism}")
        if result not in {"C", "XF"}:
            raise AssertionError(f"malformed ledger result: {result}")
        mapped_ids = _parse_id_cell(ids_cell, valid_ids)
        if classification == "primary" and not mapped_ids:
            raise AssertionError(f"primary ledger row has no mapped ID: {number_cell}")
        if not purpose or purpose == "—":
            raise AssertionError(
                f"ledger purpose must explain ownership: {number_cell}"
            )
        try:
            number = int(match.group(1))
        except (
            ValueError
        ) as exc:  # Defensive; the regular expression permits digits only.
            raise AssertionError(f"invalid ledger number: {number_cell}") from exc
        records.append(
            LedgerRecord(
                number,
                identity_match.group(1),
                classification,
                mechanism,
                mapped_ids,
                result,
                purpose,
            )
        )
    numbers = [record.number for record in records]
    if numbers != list(range(1, len(records) + 1)):
        raise AssertionError("ledger numbers must be sequential and unique")
    return records


def _stable_ids(root: Path) -> tuple[set[str], set[str]]:
    try:
        catalog = (root / "docs/features/catalog.md").read_text()
        contracts = (root / "docs/features/contracts.md").read_text()
    except OSError as exc:
        raise AssertionError(f"unable to read stable-ID sources: {exc}") from exc
    features = set(re.findall(rf"^### ({FEATURE_PATTERN}) —", catalog, re.MULTILINE))
    invariants = set(
        re.findall(rf"^### ({INVARIANT_PATTERN}) —", contracts, re.MULTILINE)
    )
    if len(features) != 82 or len(invariants) != 47:
        raise AssertionError(
            f"stable-ID baseline drift: features={len(features)}, invariants={len(invariants)}"
        )
    return features, invariants


def _check_pins(root: Path) -> None:
    for relative, required in PINNED_TEXT.items():
        text = (root / relative).read_text()
        for exact in required:
            if exact not in text:
                raise AssertionError(
                    f"pinned snapshot claim changed: {relative}: {exact}"
                )


def _check_specialized_sections(trace: str) -> None:
    forbidden_aliases = (
        "same task",
        "PostgreSQL task",
        "foundational task",
        "foundational/PostgreSQL",
        "ops hardening",
        "both browser tasks",
    )
    for alias in forbidden_aliases:
        if alias in trace:
            raise AssertionError(f"specialized matrix uses task alias: {alias}")
    specialized = trace.split("## Viewport, theme, focus, and compact matrix", 1)[1]
    specialized = specialized.split("## Gap summary and priority", 1)[0]
    for cells in _table_rows(specialized):
        has_gap_status = any(
            re.search(r"(?:^|[ ;])(M|D|XF)(?:$|[ ;,])", cell) for cell in cells
        )
        if has_gap_status and not any(TASK_PATTERN.search(cell) for cell in cells):
            raise AssertionError(f"specialized gap lacks explicit task slug: {cells}")
    if "a0c62da2913078e0ac0e9e0fe1cfdd69e51a7823" not in specialized:
        raise AssertionError(
            "specialized production matrix lacks pinned observation revision"
        )
    if "current-HEAD production observation" not in specialized:
        raise AssertionError(
            "specialized production matrix lacks current-HEAD distinction"
        )
    historical = (
        "| historical production observation | bounded observation belongs only to "
        "revision `a0c62da2913078e0ac0e9e0fe1cfdd69e51a7823` | "
        "C at pinned revision; not current HEAD | Current-HEAD automation M → "
        "`tk-harden-api-operations-security-and-release-verif-5f790a` |"
    )
    current = (
        "| current-HEAD production observation | bounded 2026-08-11 deployment at "
        "`45c97cc`: deploy check, DB connection, Compose health/logs, direct 301, "
        "proxy/public 302, restricted backup, and isolated restore | C manual at "
        "revision | Automate revision-tagged smoke → "
        "`tk-harden-api-operations-security-and-release-verif-5f790a` |"
    )
    if historical not in specialized or current not in specialized:
        raise AssertionError(
            "audited historical/current production observation rows changed"
        )


def validate(root: Path) -> str:
    trace_path = root / "docs/features/test-traceability.md"
    trace = trace_path.read_text()
    if trace.count(INTRO_BASELINE) != 1:
        raise AssertionError("stale rendered 82/44/20/276/2 intro baseline")
    if trace.count(BROAD_GAP_BASELINE) != 1:
        raise AssertionError("stale exact broad gap summary")
    contracts_text = (root / "docs/features/contracts.md").read_text()
    contracts_flat = " ".join(contracts_text.split())
    if CONTRACTS_CURRENT_SUITE not in contracts_flat:
        raise AssertionError("stale contracts current-suite 20/276/2 prose")
    if contracts_flat.count(CONTRACTS_EXPECTED_FAILURE_SENTENCE) != 1:
        raise AssertionError("stale contracts repeated 2-expected-failures sentence")
    if contracts_text.count(CONTRACTS_PROGRESSIVE_EVIDENCE) != 1:
        raise AssertionError("stale contracts progressive/a11y/mobile primary evidence")
    rendered_tasks = set(TASK_PATTERN.findall(trace))
    unknown_tasks = rendered_tasks - KNOWN_WITAN_TASKS
    if unknown_tasks:
        raise AssertionError(f"unknown Witan task slugs: {sorted(unknown_tasks)}")
    missing_headings = sorted(
        heading for heading in REQUIRED_HEADINGS if heading not in trace
    )
    if missing_headings:
        raise AssertionError(f"missing required headings: {missing_headings}")
    _check_pins(root)
    features, invariants = _stable_ids(root)
    all_ids = features | invariants
    feature_section = _section(
        trace, "## Feature matrix", "## Cross-feature invariant matrix"
    )
    invariant_section = _section(
        trace,
        "## Cross-feature invariant matrix",
        "## Viewport, theme, focus, and compact matrix",
    )
    ledger_section = _section(
        trace, "## Complete current-test ledger", "## Maintenance and review protocol"
    )
    feature_records = _parse_matrix(feature_section, features, "feature")
    invariant_records = _parse_matrix(invariant_section, invariants, "invariant")
    responsive_task = "tk-add-real-browser-responsive-theme-keyboard-and-a-147e09"
    for matrix_record in [*feature_records, *invariant_records]:
        has_responsive_gap = any(
            matrix_record.dimensions[dimension] in {"M", "D", "XF"}
            for dimension in ("mobile", "theme", "accessibility")
        )
        if has_responsive_gap and responsive_task not in TASK_PATTERN.findall(
            matrix_record.gap
        ):
            raise AssertionError(
                f"{matrix_record.stable_id} responsive gap lacks exact browser task"
            )
    ledger = _parse_ledger(ledger_section, all_ids)
    ledger_by_number = {record.number: record for record in ledger}
    for number, (name, classification, mechanism, mapped_ids) in AUDITED_LEDGER.items():
        audited_ledger_record = ledger_by_number[number]
        audited_actual = (
            audited_ledger_record.identity.rsplit("::", 1)[-1],
            audited_ledger_record.classification,
            audited_ledger_record.mechanism,
            audited_ledger_record.mapped_ids,
        )
        audited_expected = (name, classification, mechanism, mapped_ids)
        if audited_actual != audited_expected:
            raise AssertionError(
                f"audited ledger semantic expectation changed at T{number:03d}: "
                f"{audited_actual} != {audited_expected}"
            )
    matrix_by_id = {
        record.stable_id: record for record in [*feature_records, *invariant_records]
    }
    for stable_id, expected_level in AUDITED_LEVELS.items():
        if matrix_by_id[stable_id].level != expected_level:
            raise AssertionError(
                f"audited matrix level changed for {stable_id}: "
                f"{matrix_by_id[stable_id].level} != {expected_level}"
            )
    for stable_id, (dimensions, tasks) in AUDITED_MATRIX.items():
        audited_matrix_record = matrix_by_id[stable_id]
        actual_dimensions = {
            dimension: audited_matrix_record.dimensions[dimension]
            for dimension in dimensions
        }
        if actual_dimensions != dimensions:
            raise AssertionError(
                f"audited matrix dimensions changed for {stable_id}: "
                f"{actual_dimensions} != {dimensions}"
            )
        actual_tasks = set(TASK_PATTERN.findall(audited_matrix_record.gap))
        if actual_tasks != tasks:
            raise AssertionError(
                f"audited matrix task mapping changed for {stable_id}: "
                f"{sorted(actual_tasks)} != {sorted(tasks)}"
            )
    if matrix_by_id["ING-010"].evidence:
        raise AssertionError("audited ING-010 must have no primary exact evidence")

    inventory, module_count = test_inventory(root)
    expected_count = sum(record.expected_failure for record in inventory.values())
    baseline = (module_count, len(inventory), expected_count)
    if baseline != (EXPECTED_MODULES, EXPECTED_TESTS, EXPECTED_XFAILS):
        raise AssertionError(
            "current-suite baseline changed; review matrix and baseline explicitly: "
            f"modules={module_count}, tests={len(inventory)}, expectedFailures={expected_count}"
        )
    ledger_by_identity = {record.identity: record for record in ledger}
    if len(ledger_by_identity) != len(ledger) or set(ledger_by_identity) != set(
        inventory
    ):
        raise AssertionError(
            "ledger inventory drift: "
            f"missing={sorted(set(inventory) - set(ledger_by_identity))}, "
            f"extra={sorted(set(ledger_by_identity) - set(inventory))}"
        )
    for identity, source_record in inventory.items():
        ledger_record = ledger_by_identity[identity]
        expected_result = "XF" if source_record.expected_failure else "C"
        if ledger_record.result != expected_result:
            raise AssertionError(
                f"ledger C/XF does not match decorator for {identity}: "
                f"{ledger_record.result} != {expected_result}"
            )

    primary_count = sum(record.classification == "primary" for record in ledger)
    support_count = len(ledger) - primary_count
    summary_match = re.search(
        r"Ledger classification: \*\*(\d+) primary\*\*, "
        r"\*\*(\d+) support/redundant\*\*\.",
        trace,
    )
    if not summary_match or tuple(map(int, summary_match.groups())) != (
        primary_count,
        support_count,
    ):
        raise AssertionError("stale primary/support summary counts")

    mapped_ownership = {
        (record.identity, stable_id)
        for record in ledger
        if record.classification == "primary"
        for stable_id in record.mapped_ids
    }
    for matrix_record in [*feature_records, *invariant_records]:
        evidence_results = {
            ledger_by_identity[identity].result for identity in matrix_record.evidence
        }
        if "XF" in matrix_record.dimensions.values() and "XF" not in evidence_results:
            raise AssertionError(
                f"{matrix_record.stable_id} claims XF without expected-failure evidence"
            )
        for identity in matrix_record.evidence:
            if (identity, matrix_record.stable_id) not in mapped_ownership:
                raise AssertionError(
                    f"matrix evidence lacks primary ledger ownership: "
                    f"{matrix_record.stable_id}: {identity}"
                )
    matrix_evidence = {
        (identity, record.stable_id)
        for record in [*feature_records, *invariant_records]
        for identity in record.evidence
    }
    for ownership in mapped_ownership:
        if ownership not in matrix_evidence:
            raise AssertionError(
                f"primary ledger ownership absent from matrix: {ownership}"
            )

    no_feature_evidence = sorted(
        record.stable_id for record in feature_records if not record.evidence
    )
    no_invariant_evidence = sorted(
        record.stable_id for record in invariant_records if not record.evidence
    )
    for label, actual in (
        ("No-primary feature IDs", no_feature_evidence),
        ("No-primary invariant IDs", no_invariant_evidence),
    ):
        match = re.search(rf"{re.escape(label)}: `([^\n]+)`\.", trace)
        rendered = "`, `".join(actual) if actual else "none"
        if not match or match.group(1) != rendered:
            raise AssertionError(f"stale uncovered-ID summary: {label}")

    _check_specialized_sections(trace)
    return (
        f"traceability ok: {len(features)} features, {len(invariants)} invariants, "
        f"{module_count} modules, {len(inventory)} tests, {expected_count} expected failures, "
        f"{primary_count} primary, {support_count} support/redundant"
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    print(validate(root))


if __name__ == "__main__":
    main()
