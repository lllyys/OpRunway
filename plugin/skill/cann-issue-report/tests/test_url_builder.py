import pytest

from scripts import url_builder


def test_build_prefilled_url_github_short() -> None:
    res = url_builder.build_prefilled_url(
        platform="github", owner="o", repo="r",
        title="Bug: X", body="some body\nline2",
        labels=["bug", "ops-failure"],
    )
    assert res.url is not None
    assert "https://github.com/o/r/issues/new?" in res.url
    assert "title=Bug%3A%20X" in res.url
    assert "labels=bug%2Cops-failure" in res.url
    assert res.degraded is False


def test_build_prefilled_url_gitee_omits_labels_query() -> None:
    res = url_builder.build_prefilled_url(
        platform="gitee", owner="o", repo="r",
        title="t", body="b", labels=["bug"],
    )
    assert "labels=" not in res.url
    assert "https://gitee.com/o/r/issues/new?" in res.url


def test_build_prefilled_url_exceeds_limit_degrades() -> None:
    huge_body = "X" * 10000
    res = url_builder.build_prefilled_url(
        platform="github", owner="o", repo="r",
        title="t", body=huge_body, labels=[],
    )
    assert res.degraded is True
    assert res.url == "https://github.com/o/r/issues/new"  # bare URL


def test_url_length_threshold_configurable() -> None:
    res = url_builder.build_prefilled_url(
        platform="github", owner="o", repo="r",
        title="t", body="b" * 100, labels=[],
        max_url_bytes=50,
    )
    assert res.degraded is True
