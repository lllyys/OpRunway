"""aclnn_py adapter 接线 + build 配方组装 + 多输出 readback 契约自检（WI-C1/C2/C3 · torch 对标 median 见证）。

跑: cd plugin/acc-common && python3 -m unittest test_aclnn_adapter -v
   （全离线、**不上真机、不需 torch**：用 numpy 假 median golden + mock 出 out_k.bin=golden；
     真机传输原语 _shell/_copy_to/_copy_from 全打桩，绝不发起 ssh/scp/git/build。）

覆盖（全部 op-中立、据 spec/caseset/cfg 字段驱动，**无算子名分支**）：
  · find_aclnn_project：**ops-<族>仓形态**判据（仓根 build.sh + op 子目录 op_host/ + **在 op 子目录下
    有界递归**能找到 aclnn_*.h——`op_api/`、`op_host/op_api/`（PR6429 真实布局）、`op_api/include/` 都认，
    §9.4/§9.6 实测收敛，不再要 per-op build.sh / op_graph）+ 软链守卫 + op_subdir 安全校验 + 缺件 fail-closed
    （报错列出实际扫过的目录 / .h / 跳过的软链）；
  · _aclnn_cfg：关键项缺失 fail-closed、snake_op 从 op_subdir 末段派生、PR-ref/URL 白名单；
  · build 配方组装（§9.6）：git fetch PR head / 仓根 build.sh 六参 / .run --install-path /
    pigz+dos2unix 依赖门 shim / vendor 落地目录补 `_nn`；exec 运行时 env；
  · _run_aclnn_real（mock 传输）：build→deploy→exec→collect 各段脚本经 _shell 发出、out_k.bin 拉回；

安全/provenance 回归（对应本轮审计 7 条 finding，红→绿即防再退化）：
  · High#1 缓存冒充新 PR：验收模式**默认强制重建**；开 REUSE_BUILD=1 也须 provenance stamp
    （仓/ref/head SHA/子路径/op/SoC/vendor/构建参数/符号集/toolkit + .so SHA256）逐项相符；
    「.so 在」≠「被测算子在」→ 必须核到 caseset 要的 aclnn 符号；prov 随证据返回；
  · High#2 PR-ref 白名单落实：只认 40 位 SHA / refs/merge-requests/<N>/head（拒 main / 短 SHA /
    refs/heads/*），fetch 后 rev-parse 与期望 head SHA 比对；
  · High#3 取源干净：强制 remote set-url + reset --hard + clean -ffdx；build 前清 build_out、
    产物要求**恰好一个**本轮新包（不再 `ls *.run | head -n1` 任取）；
  · High#4 路径守卫：caseset 输入与**远端 manifest**（不可信输入）的相对路径过字符白名单 + canonical +
    首段=本 case ID + 逐段拒软链，才敢拼进 `host:path`；
  · High#5 远端副作用守卫：逐段拒软链 + 专用根属主/权限 + 删除目标与 vendor/checkout/setenv 相交即拒；
  · Medium#6 形态核验节点自身逐段查（build.sh / op_host / op_api / aclnn_*.h 是软链 → 拒）；
  · Medium#7 `source set_env.sh` 失败立即退出（不再 `|| true` 吞掉）+ 校 CANN 环境真起来；
  · MODES["aclnn_py"] 已注册且可 dispatch；输入 dtype 白名单据 runner_form 放开 int/bf16；real gate fail-closed；
  · build_multi_output_evidence：读 out_manifest → 逐输出 compute_metrics → evidence.precision.outputs[]
    结构与 caseset 逐输出 policy/standard/tpid 一致（digest 三处对齐）；喂 validator → 精度 pass；篡改→fail；
  · run_workflow：aclnn_py 登记为 acceptance-capable、evidence_grade=acceptance_candidate 不被降级。
"""
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

import aclnn_adapter as A
import gen_cases as GC
import repo_adapter as RA
import run_workflow as W
import validator as V
import _golden_fixture as _gf


def setUpModule():
    _gf.install()


def tearDownModule():
    _gf.uninstall()


# numpy 假 median（不需 torch）：双输出、lower-middle；据字段分派（同 test_gen_cases_multi_output）。
_FAKE_MEDIAN_BODY = '''
def out_shape(in_shapes, attrs):
    shp = tuple(int(d) for d in in_shapes[0])
    dim = attrs.get("dim")
    if dim is None:
        return ()
    d = dim if dim >= 0 else dim + len(shp)
    if attrs.get("keepdim"):
        return shp[:d] + (1,) + shp[d + 1:]
    return shp[:d] + shp[d + 1:]

def golden_fn(inputs, attrs):
    x = np.asarray(inputs[0]); dim = attrs.get("dim")
    if dim is None:
        xs = np.sort(x, axis=None)
        return xs[(x.size - 1) // 2]
    d = dim if dim >= 0 else dim + x.ndim
    order = np.argsort(x, axis=d, kind="stable")
    mid = (x.shape[d] - 1) // 2
    vi = np.take(order, [mid], axis=d)
    vv = np.take_along_axis(x, vi, axis=d)
    if not attrs.get("keepdim"):
        vv = np.squeeze(vv, axis=d); vi = np.squeeze(vi, axis=d)
    return (vv, vi.astype(np.int64))
'''

# 见证用的 PR-ref / 取源 URL / op 子路径（median PR6429，§9.6 实测值）——**只作测试输入**，
# 工具侧一律据 cfg 字段驱动、不认算子身份（律令#0）。
_PR_SHA = "0290d61ac066f9f4e620a3714f5941e82dc4e72a"
_BASE_REPO = "https://gitcode.com/cann/ops-nn.git"
_OP_SUBDIR = "experimental/index/median"


@contextlib.contextmanager
def _env(**kw):
    """临时设/清 env（None = 删），退出还原。"""
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cfg_env(ops_root, **over):
    """一份完整的 aclnn cfg env（零私有默认：机器/路径/子路径/vendor/取源全显式）。"""
    e = {"OPRUNWAY_ACLNN_OPS_DIR": ops_root,
         "OPRUNWAY_ACLNN_OP_SUBDIR": _OP_SUBDIR,
         "OPRUNWAY_TARGET": "local",
         "OPRUNWAY_REMOTE_DIR": "/tmp/oprunway_aclnn_rr",
         "OPRUNWAY_ACLNN_VENDOR_DIR": "/tmp/oprunway_aclnn_vendor",
         "OPRUNWAY_ACLNN_VENDOR_NAME": "customize",
         "OPRUNWAY_ACLNN_BASE_REPO": _BASE_REPO,
         "OPRUNWAY_ACLNN_PR_REF": _PR_SHA,
         "OPRUNWAY_ACLNN_PR_HEAD_SHA": None,     # ref 已是 40 位 SHA → 期望值即它自身
         "OPRUNWAY_ACLNN_SOC": "ascend910_93",
         "OPRUNWAY_SSH_HOST": None,
         "OPRUNWAY_ACLNN_SNAKE_OP": None,
         "OPRUNWAY_ACLNN_OP_DIR": None,          # 缺省不显式给 → 默认落到本次 DUT 的 vendor 内容根
         "OPRUNWAY_ACLNN_PROXY": None,
         "OPRUNWAY_ACLNN_REBUILD": None,
         "OPRUNWAY_ACLNN_REUSE_BUILD": None,     # 验收模式默认强制重建（审计 High#1）
         "OPRUNWAY_ACLNN_REAL": None,
         "OPRUNWAY_NPU_DEVICE": "0"}
    e.update(over)
    return e


def _make_ops_repo(root, op_subdir=_OP_SUBDIR, repo_build=True, op_host=True,
                   op_api=True, header=True, api_rel="op_api"):
    """造一个 ops-<族>仓 checkout 形态：仓根 build.sh + <op_subdir>/{op_host, <api_rel>/aclnn_*.h}。

    `api_rel` = 接口头所在目录**相对 op 子目录**的路径（默认 `op_api`——旧的一层布局；
    真实 PR6429 是 `op_host/op_api`）。工具侧不预设是哪一层，故夹具也把它做成参数。
    """
    if repo_build:
        open(os.path.join(root, "build.sh"), "w").close()
    d = os.path.join(root, *op_subdir.split("/"))
    os.makedirs(d, exist_ok=True)
    if op_host:
        os.makedirs(os.path.join(d, "op_host"), exist_ok=True)
    if op_api:
        api_dir = os.path.join(d, *api_rel.split("/"))
        os.makedirs(api_dir, exist_ok=True)
        if header:
            with open(os.path.join(api_dir, "aclnn_median.h"), "w") as f:
                f.write("aclnnStatus aclnnMedianGetWorkspaceSize(const aclTensor *self, ...);\n")
    return d


#: 见证用的 aclnn 调用变体表（**字段驱动**：按 attr `dim` 是否为 null 选变体，绝无按算子名分派）。
#: V1（审计夹具修）：夹具声明 `runner_form="aclnn_py"` 却没 `call_variants`，撞 gen_cases 的
#: fail-closed 门 → 7 条测试红。生产代码无错，是夹具没跟上「调用形态必须由 spec 显式声明」的新契约。
_FAKE_CALL_VARIANTS = [
    {"when": {"attr": "dim", "is_null": True}, "symbol": "Median",
     "active_attrs": [], "active_outputs": ["values"]},
    {"when": {"attr": "dim", "is_null": False}, "symbol": "MedianDim",
     "active_attrs": ["dim", "keepdim"], "active_outputs": ["values", "indices"]},
]
#: `_required_symbols` 会从 caseset 的 `aclnn_call.symbol` 收出来的集合（用于 .so 符号核验）。
_FAKE_SYMBOLS = ["Median", "MedianDim"]

_DEFAULT = object()     # 「参数未给」哨兵（空列表也是有意义的入参，不能用 `or` 兜默认）


def _fake_median_spec(op="MedAcl", dtypes=("float32", "int32")):
    matrix = [{"dim": None, "keepdim": False}, {"dim": 0, "keepdim": False},
              {"dim": -1, "keepdim": False}, {"dim": 0, "keepdim": True}]
    return {
        "op": op, "repo": "t", "verify_mode": "numerical", "generalize": True,
        "allow_empty_tensor": False, "attr_matrix": matrix,
        "runner_form": "aclnn_py", "scenario": "torch_ref_aclnn",
        "call_variants": [dict(v) for v in _FAKE_CALL_VARIANTS],
        "precision": {"oracle": "torch", "standard": "torch_allclose",
                      "tolerance_source": "dtype_table", "case_target": 20},
        "params": [
            {"name": "self", "io": "in", "dtype": list(dtypes), "rank": [1, 2, 3]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": None},
            {"name": "keepdim", "io": "attr", "dtype": ["bool"], "default": False},
            {"name": "values", "io": "out", "dtype": ["<from_input>"], "out_role": "value"},
            # `gather_from` 由 spec 显式锚定 gather 源（绝不取 case 的「第一个输入」——那随输入顺序漂移）
            {"name": "indices", "io": "out", "dtype": ["int64"], "out_role": "index",
             "index_of": "values", "gather_from": "self"},
        ],
    }


def _emit_mock_outputs(caseset, work, out_dir):
    """模拟 aclnn_driver：out_k.bin = golden_k.npy（完美 NPU），并落 out_manifest.json。"""
    os.makedirs(out_dir, exist_ok=True)
    produced = []
    for c in caseset["cases"]:
        cid = c["id"]
        os.makedirs(os.path.join(out_dir, cid), exist_ok=True)
        outs = []
        for k, o in enumerate(c["expected"]["outputs"]):
            golden = np.load(os.path.join(work, o["golden_path"]))
            arr = np.ascontiguousarray(golden)
            rel = f"{cid}/out_{k}.bin"
            arr.tofile(os.path.join(out_dir, rel))
            outs.append({"index": k, "role": o["role"], "path": rel,
                         "shape": list(arr.shape), "dtype": str(arr.dtype.name),
                         "nbytes": int(arr.nbytes)})
        produced.append({"case_id": cid, "outputs": outs})
    with open(os.path.join(out_dir, "out_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"op": caseset["op"], "out_dir": out_dir, "produced": produced}, f)


class FindAclnnProjectTest(unittest.TestCase):
    """ops-<族>仓形态判据（§9.4/§9.6 收敛）：仓根 build.sh + op 子目录 op_host/ +
    **在 op 子目录下（有界递归、含 op_host/op_api/）能找到** aclnn_*.h。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aclnn_repo_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_ops_repo_form_ok(self):
        _make_ops_repo(self.root)
        self.assertEqual(A.find_aclnn_project("Median", self.root, _OP_SUBDIR),
                         os.path.realpath(self.root))

    def test_real_pr_layout_header_under_op_host_op_api(self):
        """真实 PR6429 布局：`<op_subdir>/op_host/op_api/aclnn_median.h`，`<op_subdir>/` 下**无** op_api/。
        旧判据钉死一层 → 真 PR 被误判「非域内」硬阻塞（dogfood 实测）。必须过 gate。"""
        d = _make_ops_repo(self.root, api_rel="op_host/op_api")
        self.assertFalse(os.path.exists(os.path.join(d, "op_api")))
        self.assertTrue(os.path.isfile(os.path.join(d, "op_host", "op_api", "aclnn_median.h")))
        self.assertEqual(A.find_aclnn_project("Median", self.root, _OP_SUBDIR),
                         os.path.realpath(self.root))

    def test_header_under_op_api_include_also_ok(self):
        """`op_api/include/aclnn_*.h`（另一种已知落点）同样放行——工具不预设头在哪一层。"""
        _make_ops_repo(self.root, api_rel="op_api/include")
        self.assertEqual(A.find_aclnn_project("Median", self.root, _OP_SUBDIR),
                         os.path.realpath(self.root))

    def test_header_beyond_max_depth_not_counted(self):
        """深度有界：超出 `_HEADER_MAX_DEPTH` 的头不算数（防遍历爆炸；越界即 fail-closed）。"""
        deep = os.path.join(*(["a"] * (A._HEADER_MAX_DEPTH + 1)))
        _make_ops_repo(self.root, api_rel=deep)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("aclnn_*.h", str(e.exception))

    def test_error_lists_scanned_dirs_and_files(self):
        """报错须列出**实际扫到了哪些目录 / .h 文件**——否则用户猜不到接口头该放哪（U1 反馈）。"""
        d = _make_ops_repo(self.root, header=False)
        os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        with open(os.path.join(d, "op_api", "aclnn_median_impl.h"), "w") as f:
            f.write("// impl only\n")
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        msg = str(e.exception)
        self.assertIn("实际扫描", msg)
        self.assertIn("扫过的目录", msg)
        for expect in ("op_api", "op_host", "tests", "aclnn_median_impl.h"):
            self.assertIn(expect, msg)

    def test_no_op_graph_and_no_per_op_build_sh_still_ok(self):
        """§9.4 实测：ops-nn 框架内算子**无** per-op build.sh、**无** op_graph —— 不再要求，须放行。"""
        d = _make_ops_repo(self.root)
        self.assertFalse(os.path.exists(os.path.join(d, "build.sh")))
        self.assertFalse(os.path.exists(os.path.join(d, "op_graph")))
        self.assertEqual(A.find_aclnn_project("Median", self.root, _OP_SUBDIR),
                         os.path.realpath(self.root))

    def test_missing_repo_root_build_sh_fail_closed(self):
        _make_ops_repo(self.root, repo_build=False)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("仓根 build.sh", str(e.exception))

    def test_missing_op_host_fail_closed(self):
        _make_ops_repo(self.root, op_host=False)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("op_host", str(e.exception))

    def test_missing_op_api_fail_closed(self):
        _make_ops_repo(self.root, op_api=False)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("op_api", str(e.exception))

    def test_missing_aclnn_header_fail_closed(self):
        _make_ops_repo(self.root, header=False)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("aclnn_*.h", str(e.exception))

    def test_impl_header_alone_does_not_count(self):
        """只有 aclnn_*_impl.h（内部实现头）不算手写接口头 → fail-closed。"""
        d = _make_ops_repo(self.root, header=False)
        with open(os.path.join(d, "op_api", "aclnn_median_impl.h"), "w") as f:
            f.write("// impl only\n")
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        self.assertIn("aclnn_*.h", str(e.exception))

    def test_symlinked_header_in_nested_layout_not_counted(self):
        """深层布局下的软链头同样不算数（软链能指到仓外另一份源）→ fail-closed，且报错如实列出被跳过的软链。"""
        d = _make_ops_repo(self.root, api_rel="op_host/op_api", header=False)
        outside = tempfile.mkdtemp(prefix="aclnn_outside_")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        src = os.path.join(outside, "aclnn_median.h")
        open(src, "w").close()
        try:
            os.symlink(src, os.path.join(d, "op_host", "op_api", "aclnn_median.h"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可用")
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, _OP_SUBDIR)
        msg = str(e.exception)
        self.assertIn("跳过的软链", msg)
        self.assertIn("op_host/op_api/aclnn_median.h", msg)

    def test_nonexistent_op_subdir_fail_closed(self):
        _make_ops_repo(self.root)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, "experimental/index/nope")
        self.assertIn("op 子目录", str(e.exception))

    def test_symlinked_op_subdir_segment_rejected(self):
        """op 子路径**任一目录段**是软链 → 拒（换靶面：目录段软链绕过 root 守卫）。"""
        _make_ops_repo(self.root)
        real = os.path.join(self.root, "experimental", "index")
        link = os.path.join(self.root, "experimental", "linked")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可用")
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, "experimental/linked/median")
        self.assertIn("符号链接", str(e.exception))

    def test_symlinked_required_nodes_rejected(self):
        """审计 Medium#6：旧逐段守卫只走到 op_path —— 仓根 build.sh / op_host / op_api / aclnn_*.h
        **自身**还能是软链（指向仓外另一份源），形态核验一过下游就当「这是被测 PR 的源」跑。"""
        outside = tempfile.mkdtemp(prefix="aclnn_outside_")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        open(os.path.join(outside, "build.sh"), "w").close()
        os.makedirs(os.path.join(outside, "op_host"), exist_ok=True)
        os.makedirs(os.path.join(outside, "op_api"), exist_ok=True)
        open(os.path.join(outside, "aclnn_median.h"), "w").close()
        for node, src in (("build.sh", "build.sh"),
                          (os.path.join(*_OP_SUBDIR.split("/"), "op_host"), "op_host"),
                          (os.path.join(*_OP_SUBDIR.split("/"), "op_api"), "op_api"),
                          (os.path.join(*_OP_SUBDIR.split("/"), "op_api", "aclnn_median.h"),
                           "aclnn_median.h")):
            root = tempfile.mkdtemp(prefix="aclnn_lnkroot_")
            self.addCleanup(shutil.rmtree, root, ignore_errors=True)
            _make_ops_repo(root)
            target = os.path.join(root, node)
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            try:
                os.symlink(os.path.join(outside, src), target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink 不可用")
            with self.assertRaises(ValueError, msg=node):
                A.find_aclnn_project("Median", root, _OP_SUBDIR)

    def test_bad_op_name_rejected(self):
        _make_ops_repo(self.root)
        for bad in ("-rf", "..", "a/b", "a b"):
            with self.assertRaises(ValueError):
                A.find_aclnn_project(bad, self.root, _OP_SUBDIR)

    def test_bad_op_subdir_rejected(self):
        _make_ops_repo(self.root)
        for bad in ("/abs/path", "a/../../etc", "./a", "a//b", "a/b/", ".",
                    "a/-rf/b", "a b/c", "a;rm -rf/c", ""):
            with self.assertRaises(ValueError, msg=bad):
                A.find_aclnn_project("Median", self.root, bad)

    def test_missing_op_subdir_fail_closed(self):
        _make_ops_repo(self.root)
        with self.assertRaises(ValueError) as e:
            A.find_aclnn_project("Median", self.root, None)
        self.assertIn("OPRUNWAY_ACLNN_OP_SUBDIR", str(e.exception))

    def test_ops_root_must_be_absolute(self):
        with self.assertRaises(ValueError):
            A.find_aclnn_project("Median", "relative/dir", _OP_SUBDIR)


class AclnnCfgTest(unittest.TestCase):
    """cfg 零硬编码 + 缺关键项 fail-closed + 字段派生。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aclnn_cfgroot_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _make_ops_repo(self.root)

    def test_full_cfg_ok_and_snake_op_derived_from_subdir(self):
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
        self.assertEqual(cfg["op_subdir"], _OP_SUBDIR)
        self.assertEqual(cfg["snake_op"], "median")        # 末段派生（字段驱动，非按算子名硬编码）
        self.assertEqual(cfg["soc"], "ascend910_93")
        self.assertIsNone(cfg["host"])                      # local 模式不要 host

    def test_snake_op_explicit_override(self):
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_SNAKE_OP="median_dim")):
            self.assertEqual(A._aclnn_cfg()["snake_op"], "median_dim")

    def test_missing_required_fields_fail_closed(self):
        for key in ("OPRUNWAY_ACLNN_OP_SUBDIR", "OPRUNWAY_REMOTE_DIR", "OPRUNWAY_ACLNN_VENDOR_DIR",
                    "OPRUNWAY_ACLNN_VENDOR_NAME", "OPRUNWAY_ACLNN_BASE_REPO", "OPRUNWAY_ACLNN_PR_REF"):
            with _env(**_cfg_env(self.root, **{key: None})):
                with self.assertRaises(ValueError, msg=key):
                    A._aclnn_cfg()

    def test_remote_target_requires_host(self):
        with _env(**_cfg_env(self.root, OPRUNWAY_TARGET="remote", OPRUNWAY_SSH_HOST=None)):
            with self.assertRaises(ValueError):
                A._aclnn_cfg()

    def test_bad_pr_ref_and_repo_url_rejected(self):
        # 审计 High#2：白名单**只认** 40 位 SHA / refs/merge-requests/<N>/head。
        # 分支名 / 短 SHA / refs/heads/* 若放过，验收会静默跑在另一份代码上。
        for ref in ("-x", "a/../b", "a;rm -rf /", "a b",
                    "main", "master", _PR_SHA[:12], _PR_SHA + "0",
                    "refs/heads/main", "refs/merge-requests/0/head",
                    "refs/merge-requests/6429/merge", "refs/tags/v1"):
            with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_PR_REF=ref)):
                with self.assertRaises(ValueError, msg=ref):
                    A._aclnn_cfg()
        for url in ("ssh://x/y", "https://a.com/x?y=1", "https://a.com/x;rm -rf /", "file:///etc"):
            with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_BASE_REPO=url)):
                with self.assertRaises(ValueError, msg=url):
                    A._aclnn_cfg()

    def test_merge_request_ref_requires_explicit_head_sha(self):
        """mr-ref 是**可移动**引用 → 必须同时给 head SHA，否则「取到的是不是被测 commit」无从核验。"""
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_PR_REF="refs/merge-requests/6429/head")):
            with self.assertRaises(ValueError) as e:
                A._aclnn_cfg()
        self.assertIn("OPRUNWAY_ACLNN_PR_HEAD_SHA", str(e.exception))

    def test_merge_request_ref_accepted_with_head_sha(self):
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_PR_REF="refs/merge-requests/6429/head",
                             OPRUNWAY_ACLNN_PR_HEAD_SHA=_PR_SHA)):
            cfg = A._aclnn_cfg()
        self.assertEqual(cfg["pr_ref"], "refs/merge-requests/6429/head")
        self.assertEqual(cfg["head_sha"], _PR_SHA)

    def test_sha_ref_derives_head_sha_and_rejects_contradiction(self):
        with _env(**_cfg_env(self.root)):
            self.assertEqual(A._aclnn_cfg()["head_sha"], _PR_SHA)
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_PR_HEAD_SHA="b" * 40)):
            with self.assertRaises(ValueError) as e:
                A._aclnn_cfg()
        self.assertIn("不符", str(e.exception))
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_PR_HEAD_SHA="deadbeef")):
            with self.assertRaises(ValueError):
                A._aclnn_cfg()

    def test_bad_soc_vendor_and_paths_rejected_at_paths(self):
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_SOC="x86_thing")):
            with self.assertRaises(ValueError):
                A._aclnn_paths(A._aclnn_cfg())
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_VENDOR_NAME="a b")):
            with self.assertRaises(ValueError):
                A._aclnn_paths(A._aclnn_cfg())
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_VENDOR_DIR="relative/dir")):
            with self.assertRaises(ValueError):
                A._aclnn_paths(A._aclnn_cfg())

    def test_vendor_dir_gets_nn_suffix(self):
        """§9.6：`--vendor_name=customize` 落地目录是 `customize_nn`（自动补 _nn）。"""
        with _env(**_cfg_env(self.root)):
            p = A._aclnn_paths(A._aclnn_cfg())
        self.assertEqual(p["vc"], "/tmp/oprunway_aclnn_vendor/vendors/customize_nn")
        self.assertTrue(p["lib"].endswith("/vendors/customize_nn/op_api/lib/libcust_opapi.so"))

    def test_vendor_dir_intersecting_rm_targets_rejected(self):
        """审计 High#5：`vendor_dir=<rroot>/aclnn_cases` 旧校验能过 → 部署 rm -rf 把刚装的 vendor 删了。"""
        for bad in ("/tmp/oprunway_aclnn_rr/aclnn_cases", "/tmp/oprunway_aclnn_rr/aclnn_out",
                    "/tmp/oprunway_aclnn_rr", "/tmp/oprunway_aclnn_rr/aclnn_cases/sub"):
            with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_VENDOR_DIR=bad)):
                with self.assertRaises(ValueError, msg=bad) as e:
                    A._aclnn_paths(A._aclnn_cfg())
            self.assertIn("相交", str(e.exception), bad)

    def test_shallow_dedicated_roots_rejected(self):
        """`/`、`/tmp`、`//x`、尾斜杠 —— 这些位置上 rm -rf / install 的影响面远超 scratch 目录 → 拒。"""
        for bad in ("/", "/tmp", "//tmp", "/tmp/", "/tmp/./x"):
            for key in ("OPRUNWAY_REMOTE_DIR", "OPRUNWAY_ACLNN_VENDOR_DIR"):
                with _env(**_cfg_env(self.root, **{key: bad})):
                    with self.assertRaises(ValueError, msg=f"{key}={bad}"):
                        A._aclnn_paths(A._aclnn_cfg())


class BuildRecipeScriptTest(unittest.TestCase):
    """§9.6 实测 build 配方的脚本组装（取源 / 依赖门 / build 六参 / install / 幂等）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aclnn_bscript_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _make_ops_repo(self.root)

    def _script(self, symbols=_DEFAULT, **over):
        syms = list(_FAKE_SYMBOLS) if symbols is _DEFAULT else list(symbols)
        with _env(**_cfg_env(self.root, **over)):
            cfg = A._aclnn_cfg()
            return A._build_install_script(cfg, A._aclnn_paths(cfg), syms)

    def test_fetch_pr_head_from_base_repo(self):
        s = self._script()
        self.assertIn("git init -q", s)
        self.assertIn(f"git fetch --depth 1 origin {_PR_SHA}", s)
        # 审计 High#3：已存在 .git 时**强制**改 origin（配置的 base_repo 不再被静默忽略）
        self.assertIn(f"git remote set-url origin {_BASE_REPO}", s)
        self.assertIn(f"git remote add origin {_BASE_REPO}", s)
        # 审计 High#3：checkout 换成 reset --hard + clean -ffdx（清旧未跟踪文件与 build_out 残留）
        self.assertIn("git reset -q --hard FETCH_HEAD", s)
        self.assertIn("git clean -q -ffdx", s)
        self.assertNotIn("git checkout -q FETCH_HEAD", s)

    def test_fetched_commit_compared_with_expected_head_sha(self):
        """审计 High#2：fetch 后 `rev-parse HEAD` 与 PR facts 的 head SHA 比对，不符即 fail-closed。"""
        s = self._script()
        self.assertIn("GOT_SHA=$(git rev-parse HEAD", s)
        self.assertIn(f'if [ "$GOT_SHA" != {_PR_SHA} ]; then', s)
        self.assertIn("OPRUNWAY_ACLNN_HEAD_MISMATCH", s)
        self.assertIn('echo "OPRUNWAY_ACLNN_HEAD_SHA=$GOT_SHA"', s)

    def test_build_sh_flags_at_repo_root(self):
        """改动⑯前这里断言六个 flag（含 `--no_force`）。现在 `--no_force` 已不是默认实参：
        它不在 ops-cv `build.sh` 的 `SUPPORTED_LONG_OPTS` 里，会直接 `Invalid long option` 退 1。
        `--experimental` 仍在——本用例的 op 子路径就是 `experimental/index/median`，按前缀判据该发。"""
        s = self._script()
        self.assertIn("bash build.sh --pkg --experimental --soc=ascend910_93 "
                      "--ops=median --vendor_name=customize", s)
        self.assertNotIn("--no_force", s)
        # 构建参数**单一事实源**：脚本与 provenance stamp 共用 `_build_args`，杜绝两处漂移
        with _env(**_cfg_env(self.root)):
            self.assertIn(A._build_args(A._aclnn_cfg()), s)

    def test_install_to_user_vendor_dir_never_shared_opp(self):
        s = self._script()
        self.assertIn("build_out/*.run", s)
        self.assertIn("--quiet --install-path=/tmp/oprunway_aclnn_vendor", s)
        self.assertNotIn("/usr/local/Ascend/ascend-toolkit/latest/opp/vendors", s)

    def test_build_out_cleaned_and_exactly_one_new_package_required(self):
        """审计 High#3：旧版 `ls build_out/*.run | head -n1` 任取一个 → 可能装上一轮的包。"""
        s = self._script()
        self.assertIn("rm -rf -- build_out", s)
        self.assertNotIn("head -n1", s)
        self.assertIn("OPRUNWAY_ACLNN_RUNPKG_AMBIGUOUS", s)
        self.assertIn('[ ${#RUNS[@]} -eq 1 ]', s)

    def test_acceptance_default_forces_rebuild(self):
        """审计 High#1：验收模式**默认强制重建**——不给「缓存 .so 冒充新 PR」留口子。"""
        s = self._script()
        self.assertIn('LIB="$VC/op_api/lib/libcust_opapi.so"', s)
        self.assertIn('if [ 0 = 1 ] &&', s)            # reuse=0 → 复用分支恒假
        self.assertIn('rm -rf -- "$VC"', s)            # 重建前清 vendor 内容根

    def test_reuse_requires_matching_provenance_stamp(self):
        """开 REUSE_BUILD=1 也必须逐项校 provenance stamp（仓/ref/SHA/op/SoC/vendor/参数/符号/.so 指纹）。"""
        s = self._script(OPRUNWAY_ACLNN_REUSE_BUILD="1")
        self.assertIn('if [ 1 = 1 ] &&', s)
        self.assertIn('STAMP="$VC/oprunway_build_provenance.txt"', s)
        self.assertIn('[ "$GOT_PROV" = "prov=$WANT" ]', s)
        self.assertIn('[ "$GOT_SO" = "so=$CUR_SO" ]', s)
        self.assertIn("OPRUNWAY_ACLNN_STAMP_MISMATCH", s)
        self.assertIn("OPRUNWAY_ACLNN_BUILD_SKIP", s)
        self.assertEqual(s.count("OPRUNWAY_ACLNN_VENDOR_ELF_SHA256="), 2)
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
        for token in (f"repo={_BASE_REPO}", f"ref={_PR_SHA}", f"sha={_PR_SHA}",
                      f"subdir={_OP_SUBDIR}", "op=median", "soc=ascend910_93",
                      "vendor=customize", "syms=Median,MedianDim"):
            self.assertIn(token, A._prov_prefix(cfg, _FAKE_SYMBOLS), token)

    def test_rebuild_flag_vetoes_reuse(self):
        s = self._script(OPRUNWAY_ACLNN_REUSE_BUILD="1", OPRUNWAY_ACLNN_REBUILD="1")
        self.assertIn('if [ 0 = 1 ] &&', s)            # REBUILD=1 一票否决复用

    def test_required_symbols_checked_in_so(self):
        """审计 High#1：「.so 在」≠「被测算子在」——build/install 后必须核到本次 caseset 要的符号。"""
        s = self._script()
        self.assertIn("oprw_check_syms", s)
        self.assertIn("for s in Median MedianDim; do", s)
        self.assertIn("OPRUNWAY_ACLNN_NOSYM", s)
        with self.assertRaises(ValueError):
            self._script(symbols=[])
        with self.assertRaises(ValueError):
            self._script(symbols=["a;rm -rf /"])

    def test_required_symbols_from_caseset_fields_only(self):
        """符号集**字段驱动**（case 的 aclnn_call.symbol），缺 aclnn_call / 非法符号名 → fail-closed。"""
        cs = {"cases": [{"id": "c0", "aclnn_call": {"symbol": "MedianDim"}},
                        {"id": "c1", "aclnn_call": {"symbol": "Median"}},
                        {"id": "c2", "aclnn_call": {"symbol": "MedianDim"}}]}
        self.assertEqual(A._required_symbols(cs), ["MedianDim", "Median"])   # 去重、保序
        with self.assertRaises(ValueError):
            A._required_symbols({"cases": [{"id": "c0"}]})
        with self.assertRaises(ValueError):
            A._required_symbols({"cases": [{"id": "c0", "aclnn_call": {"symbol": "a b"}}]})
        with self.assertRaises(ValueError):
            A._required_symbols({"cases": []})

    def test_setenv_failure_is_fail_closed(self):
        """审计 Medium#7：`source ... || true` 会吞掉 CANN 加载失败 → 可能跑在另一套 CANN 上。"""
        import re as _re
        for s in (self._script(),
                  self._script_of(A._exec_script), self._script_of(A._perf_script),
                  self._script_of(A._deploy_reset_script)):
            # 旧写法 `source <setenv> || true`（或 `. <setenv> || true`）一律不许再出现
            self.assertEqual([], _re.findall(r"(?:^|\s)(?:source|\.)\s+\S*set_env[^\n]*\|\|\s*true", s))
            # source 的必须是**校过的真身 $ser**：source 原始入口 $se 会重新解析一次软链，
            # 把前面的属主/权限校验作废（TOCTOU 换靶面，2026-07-24 审计 High）。
            self.assertIn('set +u; source "$ser"; rc=$?; set -u', s)
            self.assertNotIn('source "$se"; ', s)
            self.assertIn('[ $rc -eq 0 ] ||', s)
        s = self._script()
        for token in ("oprw_setenv", "OPRUNWAY_ACLNN_SETENV_MISSING", "OPRUNWAY_ACLNN_SETENV_FAIL",
                      "OPRUNWAY_ACLNN_NO_TOOLKIT", "OPRUNWAY_ACLNN_NO_TOOLKIT_LIB"):
            self.assertIn(token, s)

    def test_remote_guards_before_side_effects(self):
        """审计 High#5：远端副作用前逐段拒软链 + 专用根须归当前用户且他人不可写。"""
        s = self._script()
        self.assertIn("oprw_guard_seg", s)
        self.assertIn('oprw_guard_root "$VROOT" vendor_dir', s)
        self.assertIn('oprw_guard_root "$RROOT" remote_dir', s)
        self.assertIn("[ -O ", s)                       # 属主
        self.assertIn("-perm -0020 -o -perm -0002", s)  # 组/他人可写
        d = self._script_of(A._deploy_reset_script)
        self.assertIn('"$RROOT"/?*)', d)                # 删除目标须严格落在工作根之下
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", d)

    def _script_of(self, fn):
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            return fn(cfg, A._aclnn_paths(cfg))

    def test_placeholder_render_is_single_pass_and_fail_closed(self):
        """占位符单遍替换：串行 replace 会二次扫描已替进去的值（base_repo 白名单允许 `@`）。"""
        self.assertEqual(A._render("a@@X@@b", {"@@X@@": "@@Y@@"}), "a@@Y@@b")
        with self.assertRaises(ValueError):
            A._render("a@@UNKNOWN@@b", {})

    def test_deps_gate_shims_pigz_and_dos2unix(self):
        s = self._script()
        self.assertIn("command -v pigz", s)
        self.assertIn("command -v dos2unix", s)
        self.assertIn("exec gzip $a", s)                          # pigz shim 落到 gzip（剥 -p）
        self.assertIn("-p) shift 2 ;;", s)
        self.assertIn(r"sed -i 's/\r$//'", s)                      # dos2unix shim 落到 sed
        self.assertIn('export PATH="$SHIM:$PATH"', s)

    def test_proxy_prefix_only_when_configured(self):
        self.assertNotIn("http_proxy=", self._script())
        s = self._script(OPRUNWAY_ACLNN_PROXY="http://127.0.0.1:58231")
        self.assertIn("http_proxy=http://127.0.0.1:58231 https_proxy=http://127.0.0.1:58231 "
                      "git fetch --depth 1 origin", s)

    def test_failure_sentinels_per_stage_for_root_cause(self):
        """FAIL 先解耦 root-cause：取源/build/install/lib 各自哨兵，不混归。"""
        s = self._script()
        for sentinel in ("OPRUNWAY_ACLNN_GUARD_FAIL", "OPRUNWAY_ACLNN_SETENV_FAIL",
                         "OPRUNWAY_ACLNN_FETCH_FAIL", "OPRUNWAY_ACLNN_HEAD_MISMATCH",
                         "OPRUNWAY_ACLNN_BUILD_FAIL", "OPRUNWAY_ACLNN_RUNPKG_AMBIGUOUS",
                         "OPRUNWAY_ACLNN_INSTALL_FAIL", "OPRUNWAY_ACLNN_NOLIB",
                         "OPRUNWAY_ACLNN_NOSYM", "OPRUNWAY_ACLNN_BUILD_DONE"):
            self.assertIn(sentinel, s)

    def test_exec_script_runtime_env_and_driver(self):
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            s = A._exec_script(cfg, A._aclnn_paths(cfg))
        self.assertIn('VC="$VROOT/vendors/customize_nn"', s)
        self.assertIn('export ASCEND_CUSTOM_OPP_PATH="$VC:${ASCEND_CUSTOM_OPP_PATH:-}"', s)
        self.assertIn("python -m aclnn_runtime.aclnn_driver", s)
        self.assertIn("--device 0", s)
        self.assertIn("OPRUNWAY_ACLNN_EXEC_DONE", s)

    def test_exec_script_declares_dut_vendor_root_to_driver(self):
        """**精度通路的 DUT 传递链**（runner 改动⑪ host 侧对接）：

        driver 默认严格档（`require_custom_vendor=True`），不给 `--dut-lib` / `--dut-vendor-root`
        构造即 fail-closed → 不补这一环，真机精度**一例都跑不起来**。这里钉住：
        命令行确实带 `--dut-vendor-root`，且值就是本段 env 算出的 vendor **内容根** `$VC`
        （= `ASCEND_CUSTOM_OPP_PATH` 首段、= 本次 install 落地目录），不是另拼的第二套路径。
        """
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            s = A._exec_script(cfg, A._aclnn_paths(cfg))
        # ① `$VC` 就是本次 install 的 vendor 内容根（与 _aclnn_paths 的 vc 同一个值）
        self.assertIn('VC="$VROOT/vendors/customize_nn"', s)
        # ② driver 命令行带 DUT 声明，且值 == "$VC"（同一行命令内，不是散落在别处的字样）
        cmd = [ln for ln in s.splitlines() if "aclnn_runtime.aclnn_driver" in ln]
        self.assertEqual(1, len(cmd))
        self.assertIn('--dut-vendor-root "$VC"', cmd[0])
        # ③ 严格档不得被顺手关掉——放行内置实现 = 本项目一直在防的假 PASS
        self.assertNotIn("--allow-builtin-symbols", s)

    def test_exec_script_pins_op_dir_to_this_dut_vendor_root_by_default(self):
        """**签名解析这条口子**：不显式传 `--op-dir`，driver 的 `_header_dirs` 会退到
        `ASCEND_CUSTOM_OPP_PATH`；而 `_ENV_PREAMBLE` 是**追加**语义（`"$VC:${ASCEND_CUSTOM_OPP_PATH:-}"`）
        → 继承来的**旧 vendor 头**仍在搜索列表里，第一个 parse 成功就用 = 本次 install 之外的东西参与验收。

        故缺省必须显式钉 `--op-dir "$VC"`——与 `--dut-vendor-root` 同一个 shell 变量、同一次 install：
        「解析签名的头」与「真正加载的 so」自此同源。
        """
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            s = A._exec_script(cfg, A._aclnn_paths(cfg))
        cmd = [ln for ln in s.splitlines() if "aclnn_runtime.aclnn_driver" in ln]
        self.assertEqual(1, len(cmd))
        self.assertIn('--op-dir "$VC"', cmd[0])
        # 与 DUT 声明同源（同一行、同一个 $VC），不是另拼的第二套路径
        self.assertIn('--dut-vendor-root "$VC"', cmd[0])

    def test_exec_script_respects_explicit_op_dir(self):
        """显式给了就尊重（保留「`OPRUNWAY_ACLNN_OP_DIR` 指源码树 op 目录」的现有用法）——
        默认值只在缺省时生效，绝不覆盖调用方的显式选择。"""
        explicit = "/tmp/oprunway_aclnn_rr/aclnn_src/" + _OP_SUBDIR
        with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_OP_DIR=explicit)):
            cfg = A._aclnn_cfg()
            s = A._exec_script(cfg, A._aclnn_paths(cfg))
        cmd = [ln for ln in s.splitlines() if "aclnn_runtime.aclnn_driver" in ln]
        self.assertEqual(1, len(cmd))
        self.assertIn(f"--op-dir {shlex.quote(explicit)}", cmd[0])
        self.assertNotIn('--op-dir "$VC"', cmd[0])          # 显式值没被默认值顶掉

    def test_explicit_op_dir_must_be_safe_absolute_path(self):
        """显式值会被拼进容器内命令行 → 过 `_check_remote_path`（绝对 / 无 `..` / 无 shell 特殊字符）。
        非法即 fail-closed，绝不带着可疑串发出去（也就不会悄悄退回默认值继续跑）。"""
        for bad in ("relative/dir", "/tmp/../etc", "/tmp/a;rm -rf /", "/tmp/$(id)", "/tmp/-rf", ""):
            with _env(**_cfg_env(self.root, OPRUNWAY_ACLNN_OP_DIR=bad)):
                cfg = A._aclnn_cfg()
                paths = A._aclnn_paths(cfg)
                if not bad:                                  # 空串 = 没给 → 走默认，不报错
                    self.assertIn('--op-dir "$VC"', A._exec_script(cfg, paths))
                    continue
                with self.assertRaises(ValueError, msg=repr(bad)):
                    A._exec_script(cfg, paths)

    def test_runtime_env_has_no_devlib_and_never_overrides_opp_path(self):
        """真机 bug#4 / bug#5（2026-07-24 a3 首跑实测，exec/perf 两条都 100% 必死）：

        · bug#4 `$ASCEND_TOOLKIT_HOME/devlib` 是**链接期 stub 库**，前置进 LD_LIBRARY_PATH → 运行期
          `stub library cannot be used for execution` → `aclInit failed with ACL status 500000`。
          二分证据：只前置 lib64 → aclInit=0；只前置 devlib → stub 错。
        · bug#5 `export ASCEND_OPP_PATH="$VROOT"` 覆盖了 CANN 自己的 opp 根 → 60/60 用例
          `aclnn<Op>GetWorkspaceSize failed with ACL status 561103`（kernel 找不到）。对照证据：
          `=CANN 真根 → 60/60 pass`；`=install-path → 0/60`。custom lib 由 ASCEND_CUSTOM_OPP_PATH
          （`_find_custom_opapi_libs` 来源①）已找到，这行多余且有害。
        """
        import re as _re
        for s in (self._script_of(A._exec_script), self._script_of(A._perf_script)):
            # bug#4：运行期只认 lib64 的真库，devlib 一个字都不许出现。
            self.assertNotIn("devlib", s)
            self.assertIn('export LD_LIBRARY_PATH="$VC/op_api/lib:${ASCEND_TOOLKIT_HOME}/lib64:'
                          '${LD_LIBRARY_PATH:-}"', s)
            # bug#5：绝不覆盖；将来若要加 vendors glob 兜底，只许**追加**语义（形态断言）。
            self.assertNotIn('export ASCEND_OPP_PATH="$VROOT"', s)
            for m in _re.finditer(r"(?m)^\s*export\s+ASCEND_OPP_PATH=(.*)$", s):
                self.assertIn("${ASCEND_OPP_PATH", m.group(1),
                              msg=f"ASCEND_OPP_PATH 只许追加、绝不覆盖：{m.group(0)!r}")


class SetenvGuardTest(unittest.TestCase):
    """真机 bug#3：`OPRUNWAY_SETENV` 的守卫**真跑 bash**（只跑守卫段，不发 ssh、不碰 CANN）。

    CANN 官方入口 `/usr/local/Ascend/ascend-toolkit/set_env.sh` **恒是软链**，旧的「逐段拒软链」
    直接把官方入口判死，逼每台机器人肉解链、把版本号写死进路径。setenv 是**只读**输入、我们只
    source 不写 → 换靶模型套不上，放宽为 realpath 后再校验（仍要普通可读文件 + 属主/可写位）。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="oprw_setenv_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # 假 toolkit：oprw_setenv 会核 ASCEND_TOOLKIT_HOME 存在且有 lib64。
        self.tk = os.path.join(self.root, "cann-9.0.0")
        os.makedirs(os.path.join(self.tk, "lib64"))
        self.real = os.path.join(self.tk, "set_env.sh")
        with open(self.real, "w", encoding="utf-8") as f:
            f.write(f'export ASCEND_TOOLKIT_HOME={shlex.quote(self.tk)}\n')
        os.chmod(self.real, 0o644)

    def _run(self, path, env=None):
        script = A._SH_GUARDS + f"oprw_setenv {shlex.quote(path)}\n"
        bash = shutil.which("bash") or "/bin/bash"
        # errors="replace"：守卫消息是中文，一旦脚本把多字节字符切坏也要**看得见断言失败**，
        # 而不是让测试自己 UnicodeDecodeError 崩掉（那会掩盖真正的 shell bug）。
        return subprocess.run([bash, "-c", script], capture_output=True, text=True,
                              errors="replace", env=env)

    def _fakebin(self, name, body):
        """造一个「劫持 PATH 用」的假可执行（模拟安全工具缺失 / 失败），返回其所在目录。"""
        d = os.path.join(self.root, "fakebin")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(p, 0o755)
        return d

    def _make_toolkit(self, parent):
        """在 <parent> 下造一份「假 CANN」，返回 set_env.sh 路径。"""
        tk = os.path.join(parent, "cann-x")
        os.makedirs(os.path.join(tk, "lib64"))
        se = os.path.join(tk, "set_env.sh")
        with open(se, "w", encoding="utf-8") as f:
            f.write(f'export ASCEND_TOOLKIT_HOME={shlex.quote(tk)}\n')
        os.chmod(se, 0o644)
        return se

    def test_symlinked_official_entry_is_accepted_after_realpath(self):
        link_dir = os.path.join(self.root, "ascend-toolkit")
        os.makedirs(link_dir)
        link = os.path.join(link_dir, "set_env.sh")
        os.symlink(self.real, link)                       # 官方入口的形态：软链指向具体版本
        r = self._run(link)
        self.assertEqual(0, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_ENV toolkit=", r.stdout)
        self.assertNotIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)

    def test_symlinked_directory_segment_is_accepted(self):
        """路径**中间段**是软链（`/usr/local/Ascend/ascend-toolkit` 常见形态）也不该被判死。"""
        os.symlink(self.tk, os.path.join(self.root, "latest"))
        r = self._run(os.path.join(self.root, "latest", "set_env.sh"))
        self.assertEqual(0, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_ENV toolkit=", r.stdout)

    def test_non_regular_file_still_rejected(self):
        for bad in (self.tk,                                        # 目录
                    os.path.join(self.tk, "nope.sh"),               # 不存在
                    "/dev/null"):                                   # 非普通文件
            r = self._run(bad)
            self.assertEqual(2, r.returncode, msg=f"{bad}: {r.stdout}{r.stderr}")
            self.assertIn("OPRUNWAY_ACLNN_SETENV_MISSING", r.stdout)

    def test_relative_path_still_rejected(self):
        r = self._run("rel/set_env.sh")
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)

    def test_group_or_world_writable_still_rejected(self):
        """真身可被同组/他人写 = 「被人改了内容再被我 source」——realpath 之后仍必须拒。"""
        os.chmod(self.real, 0o666)
        r = self._run(self.real)
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)
        self.assertIn("可被同组/他人写", r.stdout)

    def test_writable_check_follows_symlink_to_real_target(self):
        """软链本身恒 0777——校的必须是 realpath 后的真身，别被软链自身的权限位骗过/误杀。"""
        link = os.path.join(self.root, "entry.sh")
        os.symlink(self.real, link)
        self.assertEqual(0, self._run(link).returncode)               # 真身 0644 → 放行
        os.chmod(self.real, 0o666)
        self.assertEqual(6, self._run(link).returncode)               # 真身 0666 → 拒

    # ── 2026-07-24 审计 High（TOCTOU source 劫持）+ Medium（安全工具失败非 fail-closed）──────

    def test_group_or_world_writable_parent_dir_rejected(self):
        """真身**父目录**可被非可信用户改写 → 校验与 source 之间可 rename 换靶 → 必须拒。

        只校末端文件挡不住这条：文件本身 0644 且属主没问题，攻击者照样能把整个目录项换掉。
        """
        openpar = os.path.join(self.root, "openpar")
        os.makedirs(openpar)
        se = self._make_toolkit(openpar)
        os.chmod(openpar, 0o777)                      # 无 sticky 的 world-writable 父目录
        r = self._run(se)
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)
        self.assertIn("父目录 可被同组/他人写", r.stdout)

    def test_sticky_writable_parent_dir_is_exempt(self):
        """带 sticky 的可写目录（/tmp 的 1777）豁免：非属主无法 rename/unlink 他人条目 → 换靶前提不成立。

        没这条豁免，Linux 上凡是落在 `/tmp` 下的路径会被一律判死（含本套测试的临时目录）。
        """
        openpar = os.path.join(self.root, "stickypar")
        os.makedirs(openpar)
        se = self._make_toolkit(openpar)
        os.chmod(openpar, 0o1777)
        r = self._run(se)
        self.assertEqual(0, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_ENV toolkit=", r.stdout)

    def test_realpath_unavailable_is_fail_closed(self):
        """`realpath`/`readlink` 都不可用 → **拒**，绝不退回未解析的原始路径（那等于没解析）。"""
        emptybin = os.path.join(self.root, "emptybin")
        os.makedirs(emptybin)
        r = self._run(self.real, env={"PATH": emptybin})
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)
        self.assertIn("真身路径解析失败", r.stdout)

    def test_find_failure_is_fail_closed(self):
        """`find` 失败（缺失 / 报错）→ **拒**：旧写法把「查不动」和「未命中」混成一件事 → 放行。"""
        d = self._fakebin("find", "#!/bin/sh\necho 'boom' >&2\nexit 3\n")
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
        r = self._run(self.real, env=env)
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)
        self.assertIn("安全查询失败", r.stdout)

    def test_find_failure_not_treated_as_permission_ok(self):
        """`find` 失败时**不得**被当成「不匹配」：当前用户所有但 0666 的脚本必须仍被拒。

        旧路径正是这么漏的：find 挂了 → 权限查询空串 → `-perm` 那条不触发；`-O` 又短路通过。
        """
        os.chmod(self.real, 0o666)
        d = self._fakebin("find", "#!/bin/sh\nexit 1\n")
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
        r = self._run(self.real, env=env)
        self.assertEqual(6, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_GUARD_FAIL", r.stdout)

    def test_no_variable_name_swallows_cjk_byte(self):
        """`$var` 后**紧跟**中文 → bash 5.x（UTF-8 locale）会把中文首字节吃进变量名 → `unbound variable`。

        实测：`rc=$oprw_qf_rc；...` 在 homebrew bash 5.3 下直接把整段守卫打成 127 退出（守卫形同虚设）。
        中文消息里挨着变量的地方一律写 `${var}`；本条覆盖模块内**全部** shell 模板（含注释行，图省心）。
        """
        import re as _re
        with open(A.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual([], _re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", src))

    def test_official_symlink_entry_still_accepted_when_parents_safe(self):
        """父目录都安全时，官方入口那种软链仍须放行（收紧不能把真机唯一入口判死）。"""
        link_dir = os.path.join(self.root, "ascend-toolkit")
        os.makedirs(link_dir, mode=0o755)
        link = os.path.join(link_dir, "set_env.sh")
        os.symlink(self.real, link)
        r = self._run(link)
        self.assertEqual(0, r.returncode, msg=r.stdout + r.stderr)
        self.assertIn("OPRUNWAY_ACLNN_ENV toolkit=", r.stdout)
        self.assertIn(self.tk, r.stdout)                  # source 的确实是解析后的真身


class PerfPlanStrictBoolTest(unittest.TestCase):
    """审计 Medium：严格档开关**只认真 bool**——`bool("false")` / `bool("0")` 都是 True。

    perf plan 是外部/手工也会写的 JSON：布尔误写成字符串就会把 `allow_builtin_symbols` 悄悄打开、
    关掉 custom-vendor provenance 硬门 → 性能可能测到 CANN 内置同名实现（本项目一直在防的假 PASS）。
    """

    def test_string_truthy_values_raise(self):
        for bad in ("false", "0", "true", "no", "", 0, 1, None, [], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                A._plan_bool({"allow_builtin_symbols": bad}, "allow_builtin_symbols")

    def test_missing_defaults_to_strict_false(self):
        self.assertIs(False, A._plan_bool({}, "allow_builtin_symbols"))
        self.assertIs(False, A._plan_bool({"other": True}, "allow_builtin_symbols"))

    def test_real_bools_pass_through(self):
        self.assertIs(True, A._plan_bool({"allow_builtin_symbols": True}, "allow_builtin_symbols"))
        self.assertIs(False, A._plan_bool({"allow_builtin_symbols": False}, "allow_builtin_symbols"))

    def test_load_perf_plan_rejects_string_bool_early(self):
        """早失败：plan 一读进来就查类型，别等真机跑完才炸。"""
        work = tempfile.mkdtemp(prefix="oprw_plan_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        path = os.path.join(work, A.PERF_PLAN_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"allow_builtin_symbols": "false"}, f)
        with self.assertRaises(ValueError) as e:
            A.load_perf_plan(work)
        self.assertIn("allow_builtin_symbols", str(e.exception))
        with open(path, "w", encoding="utf-8") as f:                  # 真 bool → 正常读
            json.dump({"allow_builtin_symbols": False, "warmup": 3}, f)
        self.assertEqual(3, A.load_perf_plan(work)["warmup"])

    def test_no_loose_truthiness_left_in_module(self):
        """守住不回潮：本模块**代码里**不得再出现 `bool(...)` 调用（配置一律严格解释）。

        走 AST 而非文本匹配——注释/文档串里提 `bool(...)` 是在讲为什么不能用，不该被判违规。
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(A))
        hits = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "bool"]
        self.assertEqual([], hits)


class PerfPlanDutDeclarationTest(unittest.TestCase):
    """**性能通路的 DUT 传递链**（与 `_exec_script` 的精度通路配对，两条都得喂到）。

    性能侧不走命令行：`collect_perf` 组的 `remote_plan` 随 `_perf_plan.json` 上送，容器侧
    `perf_msprof.resolve_plan_dut_lib` 解析。严格档（plan 没开 `allow_builtin_symbols`）定不出
    DUT 即 fail-closed → 不补这一环，真机性能**一例都采不到**。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aclnn_perfplan_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _make_ops_repo(self.root)
        self.work = tempfile.mkdtemp(prefix="aclnn_perfwork_")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        # `dims` 含「性能」才会被 select_perf_cases 选中（与 perf_compare 同口径、非按算子名）。
        self.caseset = {"op": "MedAcl", "cases": [
            {"id": "c0", "dims": ["功能", "性能"], "attrs": {},
             "aclnn_call": {"symbol": "MedianDim", "slots": []},
             "inputs": [{"name": "self", "dtype": "float32", "shape": [2], "path": "c0/x1.bin"}]}]}
        # 精度前筛只看 evidence 的 policy+metrics（judge 复用 validator）；behavioral → "na" 视同过筛。
        # 本用例要验的是 DUT 传递链，精度判定形态在别处已覆盖，这里取最省的合法形态。
        self.evidence = [{"case_id": "c0",
                          "precision": {"outputs": [{"policy": {"kind": "behavioral"}, "metrics": {}}]}}]
        self.plan_sent, self.scripts = [], []

        class _R:
            returncode = 0
            stdout = "OPRUNWAY_ACLNN_PERF_DONE\n"
            stderr = ""

        def fake_shell(host, script, **kw):
            self.scripts.append(script)
            return _R()

        def fake_copy_to(host, local, remote, **kw):
            if remote.endswith(A.PERF_PLAN_FILE):
                with open(local, encoding="utf-8") as f:
                    self.plan_sent.append(json.load(f))

        def fake_copy_from(host, remote, local, **kw):
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            with open(local, "w", encoding="utf-8") as f:
                json.dump({"records": []}, f)

        for name, fn in (("_shell", fake_shell), ("_copy_to", fake_copy_to),
                         ("_copy_from", fake_copy_from)):
            orig = getattr(RA, name)
            self.addCleanup(setattr, RA, name, orig)
            setattr(RA, name, fn)

    def _collect(self, plan=None, **env_over):
        with _env(**_cfg_env(self.root, **env_over)):
            cfg = A._aclnn_cfg()
            paths = A._aclnn_paths(cfg)
            effective_plan = {
                "baseline": "torch_npu",
                "torch_baseline": {"api": "torch.foo", "positional": [], "keyword": {}},
            }
            effective_plan.update(plan or {})
            A.collect_perf(cfg, paths, self.caseset, self.work, self.evidence,
                           effective_plan)
        self.assertEqual(1, len(self.plan_sent), "perf plan 未上送 → 后面全白验")
        return self.plan_sent[0], paths

    def test_remote_plan_carries_dut_vendor_root_and_vendor_fields(self):
        sent, paths = self._collect()
        # ① 首选来源②：内容根直给（与 _ENV_PREAMBLE 的 $VC / paths["vc"] 同一个值，不留第二套推导）
        self.assertEqual(paths["vc"], sent["dut_vendor_root"])
        # ② 冗余来源③：adapter cfg 的 vendor_dir + vendor_name（resolve 的第三条链）
        self.assertEqual("/tmp/oprunway_aclnn_vendor", sent["vendor_dir"])
        self.assertEqual("customize", sent["vendor_name"])
        # ③ 两条来源必须**同口径**：`<vendor_dir>/vendors/<name>_nn` == 直给的内容根，绝不打架
        self.assertEqual(sent["dut_vendor_root"],
                         sent["vendor_dir"] + "/vendors/" + sent["vendor_name"] + "_nn")
        # ④ 严格档没被顺手关掉（关了就等于放行 CANN 内置同名实现）
        self.assertIs(False, sent["allow_builtin_symbols"])

    def test_remote_plan_op_dir_defaults_to_this_dut_vendor_root(self):
        """性能侧的**同一条口子**：`remote_plan["op_dir"]` 旧缺省是 `None` → 容器侧
        `_SignatureResolver` 退到 `ASCEND_CUSTOM_OPP_PATH`，把继承来的旧 vendor 头也纳入搜索
        （追加语义所致）→ 签名可能静默取自上次安装。缺省必须填本次 DUT 的 vendor 内容根。
        """
        sent, paths = self._collect()
        self.assertEqual(paths["vc"], sent["op_dir"])
        # 与 DUT 声明同源：解析签名的头 与 真正加载的 so 出自同一次 install
        self.assertEqual(sent["dut_vendor_root"], sent["op_dir"])

    def test_remote_plan_op_dir_explicit_wins(self):
        """plan / env 显式给了就尊重，默认值只在缺省时生效（两条来源都验一遍）。"""
        explicit = "/tmp/oprunway_aclnn_rr/aclnn_src/" + _OP_SUBDIR
        sent, _ = self._collect(plan={"op_dir": explicit})
        self.assertEqual(explicit, sent["op_dir"])
        self.plan_sent.clear()
        other = "/tmp/oprunway_aclnn_rr/hand_picked_headers"
        sent2, _ = self._collect(OPRUNWAY_ACLNN_OP_DIR=other)
        self.assertEqual(other, sent2["op_dir"])

    def test_sent_plan_resolves_to_this_build_dut_lib_under_strict(self):
        """端到端口径核对：容器侧**真拿这份 plan** 去解析，必须解出本次 install 的 `.so`。"""
        from aclnn_runtime import perf_msprof as PM
        sent, paths = self._collect()
        self.assertEqual(os.path.abspath(paths["lib"]),
                         PM.resolve_plan_dut_lib(sent, strict=True))

    def test_plan_without_dut_fields_is_fail_closed_at_collector(self):
        """反证这一环真的载重：把 DUT 字段摘掉，严格档立刻 fail-closed（不静默从 env 猜）。"""
        from aclnn_runtime import perf_msprof as PM
        sent, _ = self._collect()
        stripped = {k: v for k, v in sent.items()
                    if k not in ("dut_lib", "dut_vendor_root", "vendor_dir", "vendor_name")}
        with self.assertRaises(Exception) as e:
            PM.resolve_plan_dut_lib(stripped, strict=True)
        self.assertIn("DUT", str(e.exception))


class CaseRelPathGuardTest(unittest.TestCase):
    """审计 High#4：caseset 输入 / 远端 manifest 的相对路径守卫（拼进 `host:path` 前的唯一闸）。

    传统 scp 是「远端跑 shell」——路径里的空格 / `$(...)` / `;` 就是远端命令注入面；
    本地软链段则能把读写引到工作目录之外。旧代码只做词法 containment，两条都挡不住。
    """

    def test_rejects_injection_escape_and_noncanonical(self):
        cids = ["c0", "c1"]
        for bad in ("/etc/passwd", "c0/../../etc/passwd", "../c0/x.bin", "./c0/x.bin",
                    "c0//x.bin", "c0/x.bin/", "c0/$(id).bin", "c0/`id`.bin",
                    "c0/x;rm -rf /.bin", "c0/x y.bin", "c0/x|y.bin", "c0/x\\y.bin",
                    "c0/-rf", "-c0/x.bin", "c0/.", "c0/..", "c0/", "", None, 3):
            with self.assertRaises(ValueError, msg=repr(bad)):
                A._safe_case_rel(bad, cids, "t")

    def test_requires_two_segments_and_known_case_id_first(self):
        cids = ["c0", "c1"]
        with self.assertRaises(ValueError):
            A._safe_case_rel("x.bin", cids, "t")            # 单段 = 落在 case 目录之外
        with self.assertRaises(ValueError):
            A._safe_case_rel("c9/x.bin", cids, "t")         # 首段不是已校验 case ID
        with self.assertRaises(ValueError):
            A._safe_case_rel("c1/x.bin", cids, "t", expect_cid="c0")   # 跨 case 写入
        self.assertEqual(A._safe_case_rel("c0/x1.bin", cids, "t", expect_cid="c0"), "c0/x1.bin")
        self.assertEqual(A._safe_case_rel("c0/sub/x1.bin", cids, "t"), "c0/sub/x1.bin")

    def test_symlinked_segment_rejected(self):
        base = tempfile.mkdtemp(prefix="aclnn_rel_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        outside = tempfile.mkdtemp(prefix="aclnn_relout_")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        try:
            os.symlink(outside, os.path.join(base, "c0"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可用")
        with self.assertRaises(ValueError) as e:
            A._reject_symlink_rel(base, "c0/x1.bin", "t")
        self.assertIn("符号链接", str(e.exception))
        # 未创建的段天然非软链 → 放行（写侧落点还不存在是正常的）
        A._reject_symlink_rel(base, "c1/x1.bin", "t")


class RunAclnnRealMockedTransportTest(unittest.TestCase):
    """_run_aclnn_real 全流程（_shell/_copy_to/_copy_from 打桩）：build→deploy→exec→collect 各段确实发出。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aclnn_realroot_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _make_ops_repo(self.root)
        self.work = tempfile.mkdtemp(prefix="aclnn_realwork_")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        os.makedirs(os.path.join(self.work, "c0"), exist_ok=True)
        np.zeros(2, dtype=np.float32).tofile(os.path.join(self.work, "c0", "x1.bin"))
        self.caseset = {"op": "MedAcl", "cases": [
            {"id": "c0", "attrs": {},
             "aclnn_call": {"symbol": "MedianDim", "slots": []},
             "inputs": [
                {"name": "self", "dtype": "float32", "shape": [2], "path": "c0/x1.bin"}]}]}
        self.scripts, self.copied_to, self.copied_from = [], [], []

        class _R:
            returncode = 0
            # 真机 build 段回报的 head SHA 必须与期望一致——否则 adapter 拒收「来路不明的 .so」。
            stdout = (f"OPRUNWAY_ACLNN_ENV toolkit=/opt/cann tkver=9.0.1\n"
                      f"OPRUNWAY_ACLNN_HEAD_SHA={_PR_SHA}\n"
                      "OPRUNWAY_ACLNN_BUILD_DONE\nOPRUNWAY_ACLNN_DEPLOY_RESET_DONE\n"
                      "OPRUNWAY_ACLNN_EXEC_DONE\n")
            stderr = ""

        def fake_shell(host, script, **kw):
            self.scripts.append(script)
            return _R()

        def fake_copy_to(host, local, remote, **kw):
            self.copied_to.append((local, remote))

        def fake_copy_from(host, remote, local, **kw):
            self.copied_from.append((remote, local))
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            if local.endswith("out_manifest.json"):
                with open(local, "w", encoding="utf-8") as f:
                    json.dump({"op": "MedAcl", "produced": [
                        {"case_id": "c0", "outputs": [
                            {"index": 0, "role": "value", "path": "c0/out_0.bin",
                             "shape": [2], "dtype": "float32", "nbytes": 8}]}]}, f)
            else:
                with open(local, "wb") as f:
                    f.write(b"\x00" * 8)

        for name, fn in (("_shell", fake_shell), ("_copy_to", fake_copy_to),
                         ("_copy_from", fake_copy_from)):
            orig = getattr(RA, name)
            self.addCleanup(setattr, RA, name, orig)
            setattr(RA, name, fn)

    def test_full_flow_emits_build_deploy_exec_and_collects(self):
        out_dir = os.path.join(self.work, "aclnn_out")
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            A._run_aclnn_real(cfg, self.root, self.caseset, self.work, out_dir)
        # build 段（§9.6 配方）与 exec 段是**两次独立 _shell**（root-cause 解耦、互不混杂）
        build_s = [s for s in self.scripts if "git fetch --depth 1 origin" in s]
        exec_s = [s for s in self.scripts if "aclnn_runtime.aclnn_driver" in s]
        self.assertEqual(len(build_s), 1)
        self.assertEqual(len(exec_s), 1)
        self.assertIn("bash build.sh --pkg --experimental", build_s[0])
        self.assertNotIn("git fetch", exec_s[0])
        # deploy：caseset + 输入 + aclnn_runtime 五文件
        remotes = [r for _, r in self.copied_to]
        self.assertIn("/tmp/oprunway_aclnn_rr/aclnn_cases/caseset.json", remotes)
        self.assertIn("/tmp/oprunway_aclnn_rr/aclnn_cases/c0/x1.bin", remotes)
        for fn in ("__init__.py", "base.py", "acl_consts.py", "aclnn_runner.py", "aclnn_driver.py"):
            self.assertIn(f"/tmp/oprunway_aclnn_rr/aclnn_runtime/{fn}", remotes)
        # collect：manifest + out_k.bin 落地
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "out_manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "c0", "out_0.bin")))

    def test_local_mode_rejects_rroot_intersecting_work_dir(self):
        """local 模式下 rroot 与 work_dir 相交 → 拒（部署会 rm -rf 其子目录，防静默误删）。"""
        with _env(**_cfg_env(self.root, OPRUNWAY_REMOTE_DIR=os.path.join(self.work, "rr"))):
            cfg = A._aclnn_cfg()
            with self.assertRaises(ValueError) as e:
                A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                  os.path.join(self.work, "aclnn_out"))
        self.assertIn("相交", str(e.exception))
        self.assertEqual(self.scripts, [])          # 一条命令都没发出

    def test_malicious_input_path_rejected_before_any_scp(self):
        """审计 High#4：case 输入的 `path` 是拼进 `host:path` 的串 → 必须在**发起 scp 之前**拦下。"""
        for bad in ("/etc/passwd", "c0/../../etc/passwd", "c0/$(id).bin",
                    "c0/x;rm -rf /.bin", "c9/x1.bin", "x1.bin"):
            self.copied_to.clear()
            cs = json.loads(json.dumps(self.caseset))
            cs["cases"][0]["inputs"][0]["path"] = bad
            with _env(**_cfg_env(self.root)):
                cfg = A._aclnn_cfg()
                with self.assertRaises(ValueError, msg=bad):
                    A._run_aclnn_real(cfg, self.root, cs, self.work,
                                      os.path.join(self.work, "aclnn_out"))
            self.assertNotIn(bad, [r for _, r in self.copied_to], bad)

    def test_malicious_remote_manifest_path_rejected(self):
        """远端 manifest 是**不可信输入**：其 `path`/`case_id` 必须过与输入侧同一套守卫。"""
        bad_recs = [
            {"case_id": "c0", "outputs": [{"index": 0, "path": "/etc/shadow"}]},
            {"case_id": "c0", "outputs": [{"index": 0, "path": "c0/../../etc/x"}]},
            {"case_id": "c0", "outputs": [{"index": 0, "path": "c0/$(id).bin"}]},
            {"case_id": "c0", "outputs": [{"index": 0, "path": "c9/out_0.bin"}]},
            {"case_id": "../c0", "outputs": [{"index": 0, "path": "c0/out_0.bin"}]},
            {"case_id": "c9", "outputs": [{"index": 0, "path": "c9/out_0.bin"}]},
        ]
        for rec in bad_recs:
            manifest = {"op": "MedAcl", "produced": [rec]}

            def evil_copy_from(host, remote, local, _m=manifest, **kw):
                self.copied_from.append((remote, local))
                os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
                if local.endswith("out_manifest.json"):
                    with open(local, "w", encoding="utf-8") as f:
                        json.dump(_m, f)
                else:
                    with open(local, "wb") as f:
                        f.write(b"\x00" * 8)

            RA._copy_from = evil_copy_from
            self.copied_from.clear()
            with _env(**_cfg_env(self.root)):
                cfg = A._aclnn_cfg()
                with self.assertRaises(ValueError, msg=repr(rec)):
                    A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                      os.path.join(self.work, "aclnn_out_evil"))
            # 只拉过 manifest 本身，没按恶意路径去拉任何产物
            self.assertEqual([r for r, _ in self.copied_from
                              if not r.endswith("out_manifest.json")], [], repr(rec))

    def test_build_stage_rejects_unverifiable_head_sha(self):
        """审计 High#1/#2：build 段没回报可核验的 head SHA → 不接受来路不明的 .so。"""
        class _NoSha:
            returncode = 0
            stdout = "OPRUNWAY_ACLNN_BUILD_DONE\n"       # 缺 HEAD_SHA 行
            stderr = ""

        RA._shell = lambda host, script, **kw: (self.scripts.append(script), _NoSha())[1]
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            with self.assertRaises(RuntimeError) as e:
                A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                  os.path.join(self.work, "aclnn_out"))
        self.assertIn("head SHA", str(e.exception))
        self.assertEqual(len(self.scripts), 1)          # deploy/exec 均未被发出

    def test_provenance_returned_for_evidence(self):
        """「这份证据由哪个 commit 的 .so 产出」必须留痕，否则证据无从追溯。"""
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            prov = A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                     os.path.join(self.work, "aclnn_out"))
        self.assertEqual(prov["head_sha"], _PR_SHA)
        self.assertEqual(prov["pr_ref"], _PR_SHA)
        self.assertEqual(prov["base_repo"], _BASE_REPO)
        self.assertEqual(prov["op_subdir"], _OP_SUBDIR)
        self.assertEqual(prov["snake_op"], "median")
        self.assertEqual(prov["soc"], "ascend910_93")
        self.assertEqual(prov["symbols"], ["MedianDim"])
        self.assertFalse(prov["build_reused"])          # 验收模式默认强制重建
        self.assertEqual(prov.get("toolkit"), "/opt/cann")
        self.assertEqual(prov.get("toolkit_version"), "9.0.1")

    def test_missing_aclnn_call_fail_closed_before_any_command(self):
        """缺 `aclnn_call` → 无法核验 .so 里有没有被测算子 → 一条命令都不发。"""
        cs = json.loads(json.dumps(self.caseset))
        cs["cases"][0].pop("aclnn_call")
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            with self.assertRaises(ValueError) as e:
                A._run_aclnn_real(cfg, self.root, cs, self.work,
                                  os.path.join(self.work, "aclnn_out"))
        self.assertIn("aclnn_call", str(e.exception))
        self.assertEqual(self.scripts, [])

    def test_deploy_reset_sentinel_required(self):
        """部署清目录被远端守卫拦下（无 DEPLOY_RESET_DONE）→ 立即抛，不继续 exec。"""
        class _R2:
            returncode = 0
            stdout = (f"OPRUNWAY_ACLNN_HEAD_SHA={_PR_SHA}\nOPRUNWAY_ACLNN_BUILD_DONE\n"
                      "OPRUNWAY_ACLNN_GUARD_FAIL vendor_dir 路径段是软链: /x\n")
            stderr = ""

        RA._shell = lambda host, script, **kw: (self.scripts.append(script), _R2())[1]
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            with self.assertRaises(RuntimeError) as e:
                A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                  os.path.join(self.work, "aclnn_out"))
        self.assertIn("部署清目录失败", str(e.exception))

    def test_build_failure_raises_before_exec(self):
        class _Bad:
            returncode = 3
            stdout = "OPRUNWAY_ACLNN_BUILD_FAIL\n"
            stderr = ""

        def bad_shell(host, script, **kw):
            self.scripts.append(script)
            return _Bad()

        RA._shell = bad_shell
        with _env(**_cfg_env(self.root)):
            cfg = A._aclnn_cfg()
            with self.assertRaises(RuntimeError) as e:
                A._run_aclnn_real(cfg, self.root, self.caseset, self.work,
                                  os.path.join(self.work, "aclnn_out"))
        self.assertIn("build/install 失败", str(e.exception))
        self.assertEqual(len(self.scripts), 1)      # exec/deploy 均未被发出


class ModeDispatchTest(unittest.TestCase):
    def test_registered_in_repo_adapter_modes(self):
        self.assertIn("aclnn_py", RA.MODES)
        self.assertIs(RA.MODES["aclnn_py"], A.run_aclnn_py)

    def test_supported_np_form_dispatch(self):
        cpp = RA.supported_np("cpp")
        acl = RA.supported_np("aclnn_py")
        self.assertNotIn("int32", cpp)          # cpp（runner v1）int 仍 Track C
        self.assertIn("int32", acl)             # aclnn_py 放开 int
        self.assertIn("int64", acl)             # indices 必需
        self.assertIn("bfloat16", acl)
        with self.assertRaises(ValueError):
            RA.supported_np("bogus_form")

    def test_run_aclnn_py_refuses_defect(self):
        with self.assertRaises(ValueError):
            A.run_aclnn_py({"op": "X", "cases": []}, "/tmp/x", defect_cases=["c0"])

    def test_run_aclnn_py_real_gated(self):
        """未设 OPRUNWAY_ACLNN_REAL → fail-closed（不误触发 ssh/git/build）。"""
        spec = _fake_median_spec(op="MedGate", dtypes=("float32",))
        _gf.place_golden(_gf.root(), "MedGate", body=_FAKE_MEDIAN_BODY)
        work = tempfile.mkdtemp(prefix="aclnn_gate_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        cs = GC.gen_cases(spec, work)
        ops_root = tempfile.mkdtemp(prefix="aclnn_gateroot_")
        self.addCleanup(shutil.rmtree, ops_root, ignore_errors=True)
        _make_ops_repo(ops_root)                       # 形态合规 → 过 find_aclnn_project，止于 real gate
        with _env(**_cfg_env(ops_root)):
            with self.assertRaises(RuntimeError) as e:
                A.run_aclnn_py(cs, work)
        self.assertIn("未启用", str(e.exception))

    def test_run_aclnn_py_fail_closed_on_non_ops_repo_form(self):
        """DUT 非 ops 仓形态（缺仓根 build.sh）→ 在 real gate **之前**就 fail-closed，不硬塞。"""
        spec = _fake_median_spec(op="MedForm", dtypes=("float32",))
        _gf.place_golden(_gf.root(), "MedForm", body=_FAKE_MEDIAN_BODY)
        work = tempfile.mkdtemp(prefix="aclnn_form_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        cs = GC.gen_cases(spec, work)
        ops_root = tempfile.mkdtemp(prefix="aclnn_formroot_")
        self.addCleanup(shutil.rmtree, ops_root, ignore_errors=True)
        _make_ops_repo(ops_root, repo_build=False)
        with _env(**_cfg_env(ops_root)):
            with self.assertRaises(ValueError) as e:
                A.run_aclnn_py(cs, work)
        self.assertIn("非 ops-<族>仓形态", str(e.exception))


class MultiOutputEvidenceTest(unittest.TestCase):
    """build_multi_output_evidence：readback + 逐输出 compute_metrics + 结构/digest 一致 + validator 折叠。"""

    def setUp(self):
        self.spec = _fake_median_spec(op="MedAcl", dtypes=("float32", "int32"))
        _gf.place_golden(_gf.root(), "MedAcl", body=_FAKE_MEDIAN_BODY)
        self.work = tempfile.mkdtemp(prefix="aclnn_ev_")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.cs = GC.gen_cases(self.spec, self.work)
        self.out_dir = os.path.join(self.work, "aclnn_out")
        _emit_mock_outputs(self.cs, self.work, self.out_dir)
        self.ev = RA.build_multi_output_evidence(self.cs, self.work, self.out_dir)

    def _ev_by_cid(self):
        return {e["case_id"]: e for e in self.ev}

    def test_evidence_outputs_structure_matches_caseset(self):
        by_cid = self._ev_by_cid()
        for c in self.cs["cases"]:
            e = by_cid[c["id"]]
            exp_outs = c["expected"]["outputs"]
            ev_outs = e["precision"]["outputs"]
            self.assertEqual(len(ev_outs), len(exp_outs), c["id"])
            for eo, xo in zip(ev_outs, exp_outs):
                # 逐输出 policy/standard/tpid 三处一致（digest 对齐、放宽即被逮）
                self.assertEqual(eo["role"], xo["role"], c["id"])
                self.assertEqual(eo["standard"], xo["standard"], c["id"])
                self.assertEqual(eo["policy"], xo["policy"], c["id"])
                self.assertEqual(eo["tolerance_policy_id"], xo["tolerance_policy_id"], c["id"])
                self.assertIn("metrics", eo)
                self.assertIn("provenance", eo)

    def test_index_metrics_use_gather(self):
        """index 输出走 index_value_consistency（gather 值一致）→ metrics 含 gathered_max_abs_err、mismatch=0。"""
        for e in self.ev:
            for eo in e["precision"]["outputs"]:
                if eo["role"] == "index":
                    self.assertEqual(eo["policy"]["kind"], "index_value_consistency")
                    self.assertIn("gathered_max_abs_err", eo["metrics"])
                    self.assertEqual(eo["metrics"]["mismatch"], 0)   # out=golden → 完美一致

    def test_validator_pass_end_to_end(self):
        envelope = {"op": self.spec["op"], "repo_mode": "aclnn_py",
                    "evidence_grade": "acceptance_candidate", "evidence": self.ev}
        v = V.validate(self.spec, self.cs, envelope)
        self.assertEqual(v["overall"]["verdict"], "pass", v["overall"])

    def test_tampered_value_out_fails(self):
        """把某 by-dim case 的 value out.bin 改坏 → 重算 metrics mismatch>0 → validator fail。"""
        byd = next(c for c in self.cs["cases"] if len(c["expected"]["outputs"]) == 2)
        o0 = byd["expected"]["outputs"][0]
        golden = np.load(os.path.join(self.work, o0["golden_path"]))
        bad = np.ascontiguousarray(golden).astype(golden.dtype)
        flat = bad.reshape(-1)
        flat[0] = flat[0] + golden.dtype.type(100)      # 明显超差
        bad.tofile(os.path.join(self.out_dir, byd["id"], "out_0.bin"))
        ev = RA.build_multi_output_evidence(self.cs, self.work, self.out_dir)
        envelope = {"op": self.spec["op"], "repo_mode": "aclnn_py",
                    "evidence_grade": "acceptance_candidate", "evidence": ev}
        v = V.validate(self.spec, self.cs, envelope)
        self.assertEqual(v["overall"]["verdict"], "fail")

    def test_global_case_single_output(self):
        """全局 median case（dim=None）→ outputs 长度 1（无 index），evidence 亦长度 1。"""
        by_cid = self._ev_by_cid()
        globals_ = [c for c in self.cs["cases"] if c["attrs"].get("dim") is None]
        self.assertTrue(globals_, "无全局 case")
        for c in globals_:
            self.assertEqual(len(by_cid[c["id"]]["precision"]["outputs"]), 1, c["id"])


class RunWorkflowNotDowngradedTest(unittest.TestCase):
    def test_aclnn_py_is_acceptance_capable(self):
        self.assertTrue(W._acceptance_capable("aclnn_py"))
        self.assertIn("aclnn_py", W._REAL_MACHINE_MODES)

    def test_acceptance_candidate_grade_not_downgraded(self):
        """run_workflow 只在 adapter 自报 grade != acceptance_candidate 时降级；aclnn_py 报 acceptance_candidate → 不降。"""
        grade = "acceptance_candidate"
        # 复刻 run_workflow.run :174 的降级判据
        is_acceptance = W._acceptance_capable("aclnn_py")
        downgraded = is_acceptance and isinstance(grade, str) and grade and grade != W._ACCEPTANCE_GRADE
        self.assertFalse(downgraded)
        self.assertEqual(grade, W._ACCEPTANCE_GRADE)

    def test_mock_still_not_acceptance(self):
        self.assertFalse(W._acceptance_capable("mock"))
        self.assertFalse(W._acceptance_capable("catlass_mock"))


if __name__ == "__main__":
    unittest.main()
