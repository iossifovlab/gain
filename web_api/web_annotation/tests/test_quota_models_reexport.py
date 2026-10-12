"""The quota models live in quota_models and are re-exported by models."""
import importlib

import pytest

QUOTA_CLASS_NAMES = [
    "Quota",
    "QuotaSnapshot",
    "AnonymousUserQuota",
    "SessionQuota",
    "UserQuota",
    "DailyQuotaRefreshLog",
    "MonthlyQuotaRefreshLog",
]


@pytest.mark.parametrize("name", QUOTA_CLASS_NAMES)
def test_models_reexports_the_quota_class_object(name: str) -> None:
    models = importlib.import_module("web_annotation.models")
    quota_models = importlib.import_module("web_annotation.quota_models")

    assert getattr(models, name) is getattr(quota_models, name)
