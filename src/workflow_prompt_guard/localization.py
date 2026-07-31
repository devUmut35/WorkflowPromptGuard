"""Small, closed-set localization helpers for hosted issue reports."""

from __future__ import annotations

from enum import Enum

from workflow_prompt_guard.models import Severity


class ReportLanguage(str, Enum):
    """Supported hosted-report languages."""

    ENGLISH = "en"
    TURKISH = "tr"


FORM_LANGUAGE_HEADING = "### Rapor dili / Report language"
FORM_LANGUAGE_OPTIONS = {
    "Türkçe": ReportLanguage.TURKISH,
    "English": ReportLanguage.ENGLISH,
}

_TURKISH_SEVERITIES = {
    Severity.INFO: "bilgi",
    Severity.LOW: "düşük",
    Severity.MEDIUM: "orta",
    Severity.HIGH: "yüksek",
    Severity.CRITICAL: "kritik",
}

_TURKISH_RULE_TITLES = {
    "AI001": "Güvenilmeyen içerik, yazma yetkili bir ajana ulaşıyor",
    "AI002": "Gizli bilgi, ajanın güven alanına açılıyor",
    "AI003": "Ajan çıktısı çalıştırılabilir koda ulaşıyor",
    "AI004": "Ajan güvenlik koruması devre dışı",
    "AI005": "Ajan yetenekleri sınırlandırılmamış",
    "AI006": "Güvenli çıktı kapsamı fazla geniş",
    "AI007": "Ajan, ayrıcalıklı bir adımla aynı işi paylaşıyor",
    "AI008": "Güvenilmeyen aktörler sınırsız ajan çalıştırması tetikleyebiliyor",
    "GA001": "Ayrıcalıklı workflow, pull request kodunu çalıştırıyor",
    "GA002": "Güvenilmeyen ifade doğrudan bir betiğe ekleniyor",
    "GA003": "Harici action, değiştirilebilir bir referans kullanıyor",
    "GA004": "Ajan workflow token izinleri en az ayrıcalık ilkesine uymuyor",
}


def severity_label(severity: Severity, language: ReportLanguage) -> str:
    """Return a localized, lowercase severity label."""

    if language is ReportLanguage.TURKISH:
        return _TURKISH_SEVERITIES[severity]
    return severity.value


def rule_title(rule_id: str, fallback: str, language: ReportLanguage) -> str:
    """Return a catalog-backed localized finding title when one is available."""

    if language is ReportLanguage.TURKISH:
        return _TURKISH_RULE_TITLES.get(rule_id, fallback)
    return fallback
