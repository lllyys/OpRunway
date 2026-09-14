import pytest

from scripts import platforms


def test_github_new_issue_base() -> None:
    a = platforms.get_adapter("github")
    assert a.new_issue_base("ascend", "ops-transformer") \
        == "https://github.com/ascend/ops-transformer/issues/new"


def test_gitee_new_issue_base() -> None:
    a = platforms.get_adapter("gitee")
    assert a.new_issue_base("ascend", "ops-cv") \
        == "https://gitee.com/ascend/ops-cv/issues/new"


def test_github_supports_labels_query() -> None:
    a = platforms.get_adapter("github")
    assert a.supports_labels_query is True


def test_gitee_does_not_support_labels_query() -> None:
    a = platforms.get_adapter("gitee")
    assert a.supports_labels_query is False


def test_gitcode_new_issue_base() -> None:
    a = platforms.get_adapter("gitcode")
    assert a.new_issue_base("cann", "ops-nn") \
        == "https://gitcode.com/cann/ops-nn/issues/new"


def test_gitcode_does_not_support_labels_query() -> None:
    a = platforms.get_adapter("gitcode")
    assert a.supports_labels_query is False


def test_unknown_platform_raises() -> None:
    with pytest.raises(ValueError, match="unknown platform"):
        platforms.get_adapter("bitbucket")
