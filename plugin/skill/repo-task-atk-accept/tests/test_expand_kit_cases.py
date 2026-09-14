"""dtype 扩展：自带件的生成器把 dtype 写死在模块常量里，扩条数补不上。

**破坏时是静默的**：dtype 轴挑错、或 attr 与 outputs 说的 dtype 对不上时，
ATK 照跑照出报表，通过率也好看，测的却不是声明的那个 dtype。所以这里钉的
不是「能跑」，是「扩出来的用例每一项都自洽」。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import expand_kit_cases as ek  # noqa: E402

DTYPE_MAP = {"fp16": 0, "bf16": 1, "fp32": 2, "complex64": 3}
WANTED = "fp16,bf16,fp32,complex64"


def attr(name, value):
    return {"name": name, "type": "attr", "dtype": "int", "range_values": [value]}


def case(case_id, dtype, dtype_id, m=1000, outputs=None):
    return {
        "id": case_id,
        "name": f"accuracy-addmm-{dtype}-{case_id:03d}",
        "api_type": "aclnn",
        "inputs": [attr("m", m), attr("dtype_id", dtype_id), attr("seed", 7 + case_id)],
        "outputs": outputs or {"0": {"dtype": dtype}},
    }


def two_dtypes():
    return [case(0, "fp32", 2), case(1, "fp32", 2, m=2000), case(2, "complex64", 3)]


def run(tmp_path, cases, extra=(), wrapper=None):
    src = tmp_path / "cases.json"
    payload = cases if wrapper is None else {**wrapper, "cases": cases}
    src.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out" / "cases.json"
    argv = ["expand_kit_cases.py", "--cases", str(src), "--dtype-map",
            json.dumps(DTYPE_MAP), "--dtypes", WANTED, "-o", str(out),
            "--target-total", "0", *extra]
    old = sys.argv
    sys.argv = argv
    try:
        code = ek.main()
    finally:
        sys.argv = old
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, result


def test_expand_fills_missing_dtypes(tmp_path):
    code, out = run(tmp_path, two_dtypes())
    assert code == 0
    dtypes = ek.dtype_counts(out, DTYPE_MAP)
    # fp32 有 2 条，所以补出来的每种也是 2 条。
    assert dtypes == {"fp32": 2, "complex64": 1, "fp16": 2, "bf16": 2}


def test_original_cases_are_untouched(tmp_path):
    """原用例逐字不动，id 也不重排——验收报告要和任务方的自测报告逐条对照。"""
    original = two_dtypes()
    code, out = run(tmp_path, original)
    assert code == 0
    assert out[:3] == original


def test_ids_and_names_stay_unique(tmp_path):
    code, out = run(tmp_path, two_dtypes())
    assert code == 0
    ids = [c["id"] for c in out]
    names = [c["name"] for c in out]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)
    assert min(ids) == 0 and max(ids) == len(out) - 1


def test_attr_and_declared_dtype_agree(tmp_path):
    """扩出来的每条，attr 里的编码与 outputs 声明的 dtype 必须指同一个 dtype。

    对不上时 ATK 不报错：它按 attr 造数、按 outputs 声明比对，两边各说各的。
    """
    code, out = run(tmp_path, two_dtypes())
    assert code == 0
    inverse = {v: k for k, v in DTYPE_MAP.items()}
    for item in out:
        encoded = {i["name"]: i["range_values"][0] for i in item["inputs"]}["dtype_id"]
        assert inverse[encoded] == item["outputs"]["0"]["dtype"]


def test_shapes_are_shared_across_dtypes(tmp_path):
    """同一批 shape 在每个 dtype 上各跑一遍，dtype 之间才可横向比。"""
    code, out = run(tmp_path, two_dtypes())
    assert code == 0

    def shape_of(item):
        return {i["name"]: i["range_values"][0]
                for i in item["inputs"] if i["name"] != "dtype_id"}

    source = [shape_of(c) for c in out if c["outputs"]["0"]["dtype"] == "fp32"]
    for dtype in ("fp16", "bf16"):
        got = [shape_of(c) for c in out if c["outputs"]["0"]["dtype"] == dtype]
        assert got == source


def test_integer_outputs_keep_their_dtype(tmp_path):
    """SpGeMM 那类算子的 CSR 索引输出声明的是整数 dtype，不跟着值的 dtype 走。

    跟着改的话结构输出会被说成浮点，比对器随即按浮点容差比索引。
    """
    cases = [case(0, "fp32", 2, outputs={"0": {"dtype": "int32"},
                                         "2": {"dtype": "fp32"}}),
             case(1, "complex64", 3, outputs={"0": {"dtype": "int32"},
                                              "2": {"dtype": "complex64"}})]
    code, out = run(tmp_path, cases)
    assert code == 0
    assert len(out) == 4
    for item in out:
        assert item["outputs"]["0"]["dtype"] == "int32"
    assert [c["outputs"]["2"]["dtype"] for c in out[2:]] == ["fp16", "bf16"]


def test_dtype_axis_is_inferred_from_the_cases(tmp_path):
    hits = ek.infer_dtype_attr(two_dtypes(), DTYPE_MAP)
    assert [name for name, _ in hits] == ["dtype_id"]


def test_ambiguous_dtype_axis_exits_4(tmp_path):
    """两个 attr 都与 dtype 一一对应时不挑一个像的用，停下来要 --dtype-attr。"""
    cases = [case(0, "fp32", 2), case(1, "complex64", 3)]
    for item, extra in zip(cases, (2, 3)):
        item["inputs"].append(attr("shadow", extra))
    code, _ = run(tmp_path, cases)
    assert code == 4


def test_explicit_dtype_attr_bypasses_inference(tmp_path):
    cases = [case(0, "fp32", 2), case(1, "complex64", 3)]
    for item, extra in zip(cases, (2, 3)):
        item["inputs"].append(attr("shadow", extra))
    code, out = run(tmp_path, cases, extra=["--dtype-attr", "dtype_id"])
    assert code == 0
    assert ek.dtype_counts(out, DTYPE_MAP)["fp16"] == 1


def test_from_dtype_selects_the_copy_source(tmp_path):
    code, out = run(tmp_path, two_dtypes(), extra=["--from-dtype", "complex64"])
    assert code == 0
    # complex64 只有 1 条，所以补出来的每种也只有 1 条。
    assert ek.dtype_counts(out, DTYPE_MAP)["fp16"] == 1


def test_wrapped_payload_keeps_outer_fields(tmp_path):
    wrapper = {"schema_version": 1, "operator": "addmm", "seed": 20260812}
    code, out = run(tmp_path, two_dtypes(), wrapper=wrapper)
    assert code == 0
    assert out["schema_version"] == 1 and out["operator"] == "addmm"
    assert len(out["cases"]) == 7


def test_full_coverage_still_writes_the_copy(tmp_path):
    """一条都不用扩时也写一份到 -o，A1 的流程才不分情况。"""
    cases = [case(0, "fp16", 0), case(1, "bf16", 1),
             case(2, "fp32", 2), case(3, "complex64", 3)]
    code, out = run(tmp_path, cases)
    assert code == 0
    assert out == cases


def test_missing_dtype_in_map_exits_5(tmp_path):
    src = tmp_path / "cases.json"
    src.write_text(json.dumps(two_dtypes()), encoding="utf-8")
    out = tmp_path / "out.json"
    sys.argv = ["expand_kit_cases.py", "--cases", str(src),
                "--dtype-map", json.dumps({"fp32": 2, "complex64": 3}),
                "--dtypes", WANTED, "-o", str(out)]
    assert ek.main() == 5
    assert not out.exists()


@pytest.mark.parametrize("text", ["{}", "[]", "not json"])
def test_unreadable_cases_exit_3(tmp_path, text):
    src = tmp_path / "cases.json"
    src.write_text(text, encoding="utf-8")
    sys.argv = ["expand_kit_cases.py", "--cases", str(src),
                "--dtype-map", json.dumps(DTYPE_MAP),
                "--dtypes", WANTED, "-o", str(tmp_path / "out.json")]
    assert ek.main() == 3


def test_short_of_target_exits_6_and_prints_the_generator_count(tmp_path, capsys):
    """条数不够时不硬凑：复制件同 shape 同 seed，条数涨了信息没涨。

    停下来把「生成脚本该出多少条」算给执行者。dtype 数换了这个值自动跟着变，
    不用谁去重算。
    """
    # 目标 8 ÷ 任务书 4 种 = 每种 2 条；× 生成器自己出的 2 种 = 4。
    code, out = run(tmp_path, two_dtypes(), extra=["--target-total", "8"])
    assert code == 6
    assert out is None
    printed = capsys.readouterr().out
    assert "要出 4 条" in printed and "目标 8" in printed


def test_target_reachable_expands_normally(tmp_path):
    """扩完正好够时照常扩，不为目标条数停下来。"""
    # fp32 2 + complex64 2，目标 8 ÷ 4 种 = 每种 2 条，补两种各 2 条 = 8。
    cases = [case(0, "fp32", 2), case(1, "fp32", 2, m=2000),
             case(2, "complex64", 3), case(3, "complex64", 3, m=2000)]
    code, out = run(tmp_path, cases, extra=["--target-total", "8"])
    assert code == 0
    assert len(out) == 8


def test_overshooting_the_target_is_trimmed(tmp_path, capsys):
    """生成脚本被直接传了目标条数（而不是倒推值）时，多出来的裁掉。

    只判下界的话补完 dtype 就是目标的两倍，而每一步的退出码都是 0。
    """
    cases = ([case(i, "fp32", 2, m=1000 + i) for i in range(4)] +
             [case(4 + i, "complex64", 3, m=2000 + i) for i in range(4)])
    code, out = run(tmp_path, cases, extra=["--target-total", "8"])
    assert code == 0
    assert len(out) == 8
    assert ek.dtype_counts(out, DTYPE_MAP) == {
        "fp32": 2, "complex64": 2, "fp16": 2, "bf16": 2}
    assert "裁掉 4 条" in capsys.readouterr().out


def test_torch_dtype_names_are_accepted(tmp_path):
    """`dtype_map` 照自带件执行器抄来的是 torch 名，用例里是 ATK 词表名。

    两边不归一时 dtype 轴推断不出来，报错说的却是「推断不出来」，方向是错的。
    """
    src = tmp_path / "cases.json"
    src.write_text(json.dumps(two_dtypes()), encoding="utf-8")
    out = tmp_path / "out.json"
    sys.argv = ["expand_kit_cases.py", "--cases", str(src),
                "--dtype-map", json.dumps(
                    {"float16": 0, "bfloat16": 1, "float32": 2, "complex64": 3}),
                "--dtypes", "float16,bfloat16,float32,complex64",
                "-o", str(out), "--target-total", "0"]
    assert ek.main() == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert ek.dtype_counts(got, DTYPE_MAP)["fp16"] == 2


def test_output_dtype_keeps_the_source_spelling(tmp_path):
    """源用例写 `float32` 时补出来的写 `float16`，不混两种风格。"""
    cases = [case(0, "float32", 2, outputs={"0": {"dtype": "float32"}}),
             case(1, "complex64", 3, outputs={"0": {"dtype": "complex64"}})]
    code, out = run(tmp_path, cases)
    assert code == 0
    assert {c["outputs"]["0"]["dtype"] for c in out} == {
        "float32", "complex64", "float16", "bfloat16"}


def test_full_dtype_coverage_but_too_few_cases_exits_6(tmp_path):
    """dtype 补齐了不等于条数够了，两个轴各判各的。"""
    cases = [case(0, "fp16", 0), case(1, "bf16", 1),
             case(2, "fp32", 2), case(3, "complex64", 3)]
    code, _ = run(tmp_path, cases, extra=["--target-total", "1000"])
    assert code == 6


def test_target_total_defaults_to_1000(tmp_path):
    """默认值就是 1000，不给参数也按它判——「skill 默认生成 1000 条」不靠谁记得传。"""
    src = tmp_path / "cases.json"
    src.write_text(json.dumps(two_dtypes()), encoding="utf-8")
    sys.argv = ["expand_kit_cases.py", "--cases", str(src),
                "--dtype-map", json.dumps(DTYPE_MAP), "--dtypes", WANTED,
                "-o", str(tmp_path / "out.json")]
    assert ek.main() == 6


def test_target_total_zero_disables_the_check(tmp_path):
    code, out = run(tmp_path, two_dtypes())
    assert code == 0 and len(out) == 7


def test_generator_cmd_grows_the_case_count_in_one_go(tmp_path):
    """条数不够时本命令自己驱动生成脚本，执行者不分两步跑。

    倒推值是内部量：目标 8 ÷ 4 种 dtype = 每种 2 条，× 生成器自己出的 2 种 = 4，
    生成脚本收到的是 4，用户看到的始终是 8。
    """
    gen = tmp_path / "gen.py"
    gen.write_text(
        "import json,sys,pathlib\n"
        "n=int(sys.argv[1]); d=pathlib.Path(sys.argv[2]); d.mkdir(parents=True,exist_ok=True)\n"
        "cs=[]\n"
        "for i in range(n):\n"
        "    dt,did=('fp32',2) if i%2==0 else ('complex64',3)\n"
        "    cs.append({'id':i,'name':f'g-{dt}-{i}','inputs':["
        "{'name':'m','type':'attr','dtype':'int','range_values':[1000+i]},"
        "{'name':'dtype_id','type':'attr','dtype':'int','range_values':[did]}],"
        "'outputs':{'0':{'dtype':dt}}})\n"
        "(d/'cases.json').write_text(json.dumps(cs))\n",
        encoding="utf-8")
    code, out = run(tmp_path, two_dtypes(), extra=[
        "--target-total", "8",
        "--generator-cmd", f"python3 {gen} {{n}} {{dir}}"])
    assert code == 0
    assert len(out) == 8
    assert ek.dtype_counts(out, DTYPE_MAP) == {
        "fp32": 2, "complex64": 2, "fp16": 2, "bf16": 2}
    # 生成脚本收到的是倒推值 4，不是目标 8。
    assert [c["name"] for c in out][:4] == ["g-fp32-0", "g-complex64-1",
                                            "g-fp32-2", "g-complex64-3"]


def test_generator_without_matching_output_exits_7(tmp_path):
    gen = tmp_path / "gen.py"
    gen.write_text("import sys,pathlib\n"
                   "d=pathlib.Path(sys.argv[2]); d.mkdir(parents=True,exist_ok=True)\n"
                   "(d/'other.json').write_text('[]')\n", encoding="utf-8")
    code, _ = run(tmp_path, two_dtypes(), extra=[
        "--target-total", "8", "--generator-cmd", f"python3 {gen} {{n}} {{dir}}"])
    assert code == 7
