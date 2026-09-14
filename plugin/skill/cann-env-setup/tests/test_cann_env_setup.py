"""cann-env-setup 纯逻辑单测：CANN 版本解析 + 配套 tag 推导/匹配（无 I/O）。"""
from scripts import detect_env, repo_setup


# ---- 版本解析（从 ASCEND_HOME_PATH basename）----

def test_parse_version_beta_from_basename():
    v = detect_env.parse_cann_version("/usr/local/Ascend/cann-9.0.0-beta.1")
    assert v["core"] == "9.0.0"
    assert v["full"] == "9.0.0-beta.1"
    assert v["prerelease"] == "beta.1"


def test_parse_version_core_only():
    v = detect_env.parse_cann_version("/opt/Ascend/cann-9.0.0")
    assert v["full"] == "9.0.0"
    assert v["prerelease"] is None


def test_parse_version_none():
    assert detect_env.parse_cann_version(None)["full"] is None


# ---- tag 候选推导 ----

def test_tag_candidates_beta_specific_first():
    assert repo_setup.version_to_tag_candidates("9.0.0-beta.1") == [
        "v9.0.0-beta.1", "9.0.0-beta.1", "v9.0.0", "9.0.0",
    ]


def test_tag_candidates_core():
    assert repo_setup.version_to_tag_candidates("9.0.0") == ["v9.0.0", "9.0.0"]


def test_tag_candidates_none():
    assert repo_setup.version_to_tag_candidates(None) == []


# ---- tag 匹配（最具体优先，无匹配返回 None）----

def test_pick_falls_back_to_core_when_beta_tag_absent():
    cands = repo_setup.version_to_tag_candidates("9.0.0-beta.1")
    assert repo_setup.pick_matching_tag(cands, ["v9.0.0", "v8.0.0"]) == "v9.0.0"


def test_pick_prefers_exact_beta_when_present():
    cands = repo_setup.version_to_tag_candidates("9.0.0-beta.1")
    assert repo_setup.pick_matching_tag(cands, ["v9.0.0-beta.1", "v9.0.0"]) == "v9.0.0-beta.1"


def test_pick_none_when_only_master():
    cands = repo_setup.version_to_tag_candidates("9.0.0-beta.1")
    assert repo_setup.pick_matching_tag(cands, ["master", "main"]) is None


# ---- SOC 名 → build.sh 短 soc 串映射 ----

def test_soc_910_93_series():
    f = detect_env.soc_name_to_build_soc
    assert f("Ascend910_9382") == "ascend910_93"   # 910C / 9382
    assert f("Ascend910_9362") == "ascend910_93"
    assert f("Ascend910_5512") == "ascend910_55"


def test_soc_910b_and_910a():
    f = detect_env.soc_name_to_build_soc
    assert f("Ascend910B3") == "ascend910b"
    assert f("Ascend910ProB") == "ascend910b"
    assert f("Ascend910B2C") == "ascend910b"
    assert f("Ascend910A") == "ascend910"
    assert f("Ascend910PremiumA") == "ascend910"


def test_soc_other_families_and_none():
    f = detect_env.soc_name_to_build_soc
    assert f("Ascend950") == "ascend950"
    assert f("Ascend310P3") == "ascend310p"
    assert f("Ascend310B") == "ascend310b"
    assert f(None) is None
