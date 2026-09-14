import io
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import submit


def test_submit_github_via_gh_cli(tmp_path: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("body content\n", encoding="utf-8")
    fake_completed = MagicMock()
    fake_completed.returncode = 0
    fake_completed.stdout = "https://github.com/o/r/issues/42\n"
    fake_completed.stderr = ""
    with patch.object(submit.subprocess, "run", return_value=fake_completed) as mocked:
        url = submit.submit_github(
            owner="o", repo="r",
            title="t", body_file=draft,
            labels=["bug", "ops-failure"],
        )
    assert url == "https://github.com/o/r/issues/42"
    args = mocked.call_args[0][0]
    assert args[:2] == ["gh", "issue"]
    assert "create" in args
    assert "--repo" in args and "o/r" in args
    assert "--label" in args


def test_submit_github_failure_raises(tmp_path: Path) -> None:
    draft = tmp_path / "d.md"; draft.write_text("x")
    fake = MagicMock(returncode=1, stdout="", stderr="auth required\n")
    with patch.object(submit.subprocess, "run", return_value=fake):
        with pytest.raises(RuntimeError, match="gh issue create failed"):
            submit.submit_github(owner="o", repo="r", title="t",
                                  body_file=draft, labels=[])


def test_submit_gitee_via_urllib() -> None:
    # labels prefetch 和 GET-verify 拿到的也是这个 dict（非 list）→ fail-open + warning
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({
        "html_url": "https://gitee.com/o/r/issues/I0001",
        "number": 1,
    }).encode("utf-8")
    fake_response.__enter__ = lambda s: s
    fake_response.__exit__ = lambda s, *a: None
    with patch.object(submit.urllib_request, "urlopen", return_value=fake_response):
        url = submit.submit_gitee(owner="o", repo="r",
                                    title="t", body="b",
                                    labels=["bug"], token="tok123")
    assert url == "https://gitee.com/o/r/issues/I0001"


def test_submit_gitcode_via_urllib() -> None:
    """labels prefetch fail-open（mock 返回非 list dict）→ POST 用原 labels → GET-verify silent。"""
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({
        "html_url": "https://gitcode.com/cann/ops-nn/issues/2749",
        "number": 2749,
    }).encode("utf-8")
    fake_response.__enter__ = lambda s: s
    fake_response.__exit__ = lambda s, *a: None
    captured = {}

    def fake_urlopen(req, timeout=30):
        # 只记录 POST 调用；labels prefetch / GET-verify 是 GET，跳过
        if not isinstance(req, str) and req.get_method() == "POST":
            captured["url"] = req.full_url
            captured["data"] = req.data
        return fake_response

    with patch.object(submit.urllib_request, "urlopen", side_effect=fake_urlopen):
        url = submit.submit_gitcode(owner="cann", repo="ops-nn",
                                     title="t", body="b",
                                     labels=["bug", "p1"], token="tok123")
    assert url == "https://gitcode.com/cann/ops-nn/issues/2749"
    # API host + auth via access_token query param
    assert captured["url"].startswith(
        "https://api.gitcode.com/api/v5/repos/cann/ops-nn/issues?access_token=tok123")
    payload = json.loads(captured["data"].decode("utf-8"))
    # labels MUST be CSV string, never a list (GitCode 422s on arrays)
    assert payload["labels"] == "bug,p1"
    assert payload["title"] == "t"
    assert payload["body"] == "b"


def test_submit_gitcode_no_labels_omits_field() -> None:
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({
        "html_url": "https://gitcode.com/o/r/issues/1"}).encode("utf-8")
    fake_response.__enter__ = lambda s: s
    fake_response.__exit__ = lambda s, *a: None
    captured = {}

    def fake_urlopen(req, timeout=30):
        if not isinstance(req, str) and req.get_method() == "POST":
            captured["data"] = req.data
        return fake_response

    with patch.object(submit.urllib_request, "urlopen", side_effect=fake_urlopen):
        submit.submit_gitcode(owner="o", repo="r", title="t", body="b",
                               labels=[], token="tok")
    payload = json.loads(captured["data"].decode("utf-8"))
    assert "labels" not in payload


def test_submit_gitcode_http_error_raises() -> None:
    # mock 让每次 urlopen 都抛 HTTPError，labels prefetch fail-open + POST 抛 RuntimeError
    with patch.object(submit.urllib_request, "urlopen",
                       side_effect=urllib.error.HTTPError(
                           url="x", code=403, msg="forbidden", hdrs=None, fp=None)):
        with pytest.raises(RuntimeError, match="GitCode API"):
            submit.submit_gitcode(owner="o", repo="r", title="t",
                                    body="b", labels=[], token="tok")


def test_submit_gitee_http_error_raises() -> None:
    with patch.object(submit.urllib_request, "urlopen",
                       side_effect=urllib.error.HTTPError(
                           url="x", code=401, msg="unauthorized", hdrs=None, fp=None)):
        with pytest.raises(RuntimeError, match="Gitee API"):
            submit.submit_gitee(owner="o", repo="r", title="t",
                                  body="b", labels=[], token="tok")


# ---- P0 加固后的新测试 ----

def test_submit_gitcode_filters_nonexistent_labels() -> None:
    """labels prefetch 返回 list 时，过滤掉上游不存在的 label。"""
    labels_resp = MagicMock()
    labels_resp.read.return_value = json.dumps(
        [{"name": "bug"}, {"name": "enhancement"}]
    ).encode("utf-8")
    labels_resp.__enter__ = lambda s: s
    labels_resp.__exit__ = lambda s, *a: None

    post_resp = MagicMock()
    post_resp.read.return_value = json.dumps({
        "html_url": "https://gitcode.com/o/r/issues/1",
        "number": 1,
    }).encode("utf-8")
    post_resp.__enter__ = lambda s: s
    post_resp.__exit__ = lambda s, *a: None

    captured = {}

    def fake_urlopen(req, timeout=30):
        url = req if isinstance(req, str) else req.full_url
        if "/labels?" in url:
            return labels_resp
        if not isinstance(req, str) and req.get_method() == "POST":
            captured["url"] = url
            captured["data"] = req.data
        return post_resp

    with patch.object(submit.urllib_request, "urlopen", side_effect=fake_urlopen):
        submit.submit_gitcode(owner="o", repo="r", title="t", body="b",
                               labels=["bug", "ops-failure", "soc:ascend950"],
                               token="tok")
    payload = json.loads(captured["data"].decode("utf-8"))
    # 只有 "bug" 在仓库 labels 里 → POST 只剩 "bug"
    assert payload["labels"] == "bug"


def test_submit_gitcode_drops_all_labels_omits_field() -> None:
    """所有 labels 都不在上游 → labels 字段不写入 payload。"""
    labels_resp = MagicMock()
    labels_resp.read.return_value = json.dumps(
        [{"name": "bug"}]
    ).encode("utf-8")
    labels_resp.__enter__ = lambda s: s
    labels_resp.__exit__ = lambda s, *a: None

    post_resp = MagicMock()
    post_resp.read.return_value = json.dumps({
        "html_url": "https://gitcode.com/o/r/issues/1",
        "number": 1,
    }).encode("utf-8")
    post_resp.__enter__ = lambda s: s
    post_resp.__exit__ = lambda s, *a: None

    captured = {}

    def fake_urlopen(req, timeout=30):
        url = req if isinstance(req, str) else req.full_url
        if "/labels?" in url:
            return labels_resp
        if not isinstance(req, str) and req.get_method() == "POST":
            captured["data"] = req.data
        return post_resp

    with patch.object(submit.urllib_request, "urlopen", side_effect=fake_urlopen):
        submit.submit_gitcode(owner="o", repo="r", title="t", body="b",
                               labels=["soc:ascend950", "ops-failure"],
                               token="tok")
    payload = json.loads(captured["data"].decode("utf-8"))
    assert "labels" not in payload


def test_get_existing_labels_returns_set_on_success() -> None:
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps(
        [{"name": "bug"}, {"name": "p1"}, {"name": "wontfix"}]
    ).encode("utf-8")
    fake_response.__enter__ = lambda s: s
    fake_response.__exit__ = lambda s, *a: None
    with patch.object(submit.urllib_request, "urlopen", return_value=fake_response):
        labels = submit.get_existing_labels(
            platform="gitcode", owner="o", repo="r", token="tok")
    assert labels == {"bug", "p1", "wontfix"}


def test_get_existing_labels_returns_none_on_http_error() -> None:
    with patch.object(submit.urllib_request, "urlopen",
                       side_effect=urllib.error.HTTPError(
                           url="x", code=404, msg="not found", hdrs=None, fp=None)):
        labels = submit.get_existing_labels(
            platform="gitcode", owner="o", repo="r", token="tok")
    assert labels is None


def test_get_existing_labels_returns_none_on_non_list_response() -> None:
    """API 返回非 list（例如错误对象）时 fail-open 返回 None。"""
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps(
        {"error_code": 401, "error_message": "unauthorized"}
    ).encode("utf-8")
    fake_response.__enter__ = lambda s: s
    fake_response.__exit__ = lambda s, *a: None
    with patch.object(submit.urllib_request, "urlopen", return_value=fake_response):
        labels = submit.get_existing_labels(
            platform="gitcode", owner="o", repo="r", token="tok")
    assert labels is None


def test_format_http_error_parses_body_for_code_and_hint() -> None:
    body = json.dumps({
        "error_code": 422,
        "error_message": "Validation failed: label not found",
    }).encode("utf-8")
    e = urllib.error.HTTPError(
        url="x", code=400, msg="bad", hdrs=None, fp=io.BytesIO(body))
    msg = submit._format_http_error("GitCode", e)
    assert "HTTP 400" in msg
    assert "error_code=422" in msg
    assert "label not found" in msg
    # 422 命中 hint
    assert "labels 必须是 CSV 字符串" in msg


def test_format_http_error_falls_back_when_body_empty() -> None:
    e = urllib.error.HTTPError(
        url="x", code=403, msg="forbidden", hdrs=None, fp=None)
    msg = submit._format_http_error("GitCode", e)
    assert "HTTP 403" in msg
    assert "forbidden" in msg
    # 403 命中 hint
    assert "issues:write" in msg
