import os

import pytest

from autoshot.resource_index import ResourceIndex

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_app")


@pytest.fixture
def index() -> ResourceIndex:
    return ResourceIndex.from_project(FIXTURE)


def test_finds_all_string_json_and_locales(index):
    assert "main_title" in index.values
    assert set(index.get_values("main_title")) == {"base", "zh_CN", "en_US"}
    assert index.get_values("main_title")["en_US"] == "Home"


def test_module_attribution(index):
    assert "entry" in index.modules["main_title"]


def test_texts_for_matching_dedup(index):
    texts = index.texts_for_matching("main_title")
    assert "首页" in texts and "Home" in texts
    # zh_CN 与 base 值相同应去重
    assert len(texts) == 2


def test_locale_filter(index):
    assert index.texts_for_matching("main_title", "en_US") == ["Home"]


def test_lookup_exact_first(index):
    hits = index.lookup_text("设置")
    assert hits, "应能反查到 settings_title"
    key, locale, value, exact = hits[0]
    assert key == "settings_title" and exact


def test_lookup_fuzzy(index):
    hits = index.lookup_text("网络连接不可用")
    assert any(k == "network_error" and not e for k, _, _, e in hits)


def test_lookup_locale_scoped(index):
    hits = index.lookup_text("Home", locale="zh_CN")
    assert hits == []
