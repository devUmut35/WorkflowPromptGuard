"""Tests for the closed-set hosted report localization layer."""

from workflow_prompt_guard.catalog import RULES
from workflow_prompt_guard.localization import ReportLanguage, rule_title, severity_label
from workflow_prompt_guard.models import Severity


def test_every_catalog_rule_has_a_turkish_presentation_title() -> None:
    fallback = "missing translation"

    for rule_id in RULES:
        assert rule_title(rule_id, fallback, ReportLanguage.TURKISH) != fallback


def test_canonical_english_labels_remain_unchanged() -> None:
    for severity in Severity:
        assert severity_label(severity, ReportLanguage.ENGLISH) == severity.value

    metadata = RULES["AI001"]
    assert rule_title(metadata.rule_id, metadata.title, ReportLanguage.ENGLISH) == metadata.title


def test_turkish_severity_labels_keep_turkish_letters() -> None:
    assert severity_label(Severity.INFO, ReportLanguage.TURKISH).capitalize() == "Bilgi"
    assert severity_label(Severity.CRITICAL, ReportLanguage.TURKISH).capitalize() == "Kritik"
