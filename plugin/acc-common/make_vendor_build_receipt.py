#!/usr/bin/env python3
"""产 `vendor_build_receipt` —— DUT vendor ELF 的**出身证明**（Layer 1 确定性脚本）。

## 这份收据要堵的洞

`cpp_extension` 通路里，被测对象（DUT）**不是** Extension 本身——Extension 只是按官方
`NpuExtension` 机制生成的 `torch.ops` 调用桥。真正被测的是**指定来源构建出的 vendor `.so`**
（`libcust_opapi.so`）。

而 **Extension 自己 build/load 成功，一个字都没说被加载的那个 `.so` 是哪来的**。
没有这份收据，加载一个 CANN 内置的同名实现也能跑完全套、报告照样漂亮——验的根本不是被测代码。
这正是仓规 §5.8「FAIL 归因前先核任务书↔PR 对应，再解耦 DUT 与 harness」要防的那件事。

收据把链条补上，三段各锁一环：

    source（被测源码身份） → build（怎么构建的） → artifact（构建出的到底是哪个文件字节）

## 为什么这个产出方必须存在

2026-08-05 在 a3 上实跑 `grep -rl 'oprunway.vendor_build_receipt'` 核过：**真机上没有任何产出方**，
全部命中都是本仓的消费代码、收据本身和日志。Median PR6429 那份收据是**人手写的**。
人手写意味着 `build.returncode: 0` 是一句自报——而这份收据存在的全部意义就是「机器可核」。

## 用法

```bash
python3 make_vendor_build_receipt.py \
  --source-facts <fetch_source 产的 source_facts.json> \
  --build-cwd    <build 在哪跑（通常是被测仓根）> \
  --library      <安装后、真机实际会加载的那个 .so，**须绝对路径**> \
  --out          <receipt.json 落点> \
  [--repo <覆盖派生的仓名>] [--allow-repo-override] \
  -- <build 命令 argv...>
```

`--` 之后的 argv 会**被真正执行**，`build.returncode` 是实测值。

⚠ **本脚本不提供「只记录、不执行」模式**，这是有意的：schema v1 的消费者只校
`build.returncode == 0`，它**分辨不出**这个 0 是实跑来的还是调用方自报的。加一个自报模式而
schema 记不下来，就是「宣称有门其实没门」。真需要支持已构建产物，得先把 schema 升到 v2、
加一个 `build.evidence: "executed" | "self_reported"` 字段让消费者能分辨——那是另一件事。

build 与 install 是两步时，把两步写进同一条命令即可（`bash -c "./build.sh … && ./build_out/*.run --install-path=…"`），
这样 `returncode` 覆盖到 install、`--library` 在脚本 hash 它的时候也已经存在。

## 五条刻意的设计（改之前先读理由）

1. **来源锚只从 `source_facts` 取，绝不自己重算。**
   三级门（`validate_acceptance_state._gate_build_receipt_source_binding`）拿收据的锚与
   `source_facts` 的锚做等值校验。两处各算一遍就是两份实现，迟早分叉；那时门会开始
   报「不相等」而两边其实都对，最后没人信这道门。

2. **`source_facts` 的收货标准与三级门**逐字同一份**。**
   `load_source_facts` 走的不只是 `content_address.read_artifact`（那只核 envelope 摘要自洽 +
   domain，**任何人都能给任意 payload 重算一个自洽摘要**），还调三级门用的**同一个**
   `validate_preparation_state._validate_source_payload`。
   ⚠ 产出方比消费者松，等于让一份降级/残缺的来源先拿到一张 `status=VERIFIED` 的收据、
   到验收阶段才被拦——失败被推迟整整一轮 build+run，而这正是本模块 `self_check` 存在的理由。
   判据只留一份，别在这里分叉。

3. **本地通路做两次「构建树 ↔ 指纹树」对账：构建前一次，构建后一次。**
   `source_facts` 只记摘要不记路径（绝对路径不可移植），所以「你 build 的那棵树，
   是不是当初被指纹的那棵树」本来无人核。这里用**同一个** `fetch_source.compute_root_digest`
   在 `--build-cwd` 下重算**作为校验**（写进收据的值仍取自 `source_facts`），不等即 fail-closed。
   · **构建前**那次回答「什么字节进了这次构建」；
   · **构建后**那次回答「这次构建有没有把被测子树改掉」——若改掉了，收据声称的那份字节
     此刻已不存在，谁也复现不了。
   ⚠ 构建后这一次**没有任何下游会替你做**：编排只在 CP-A 取材跑一次 `fetch_source`
   （`plugin/AGENTS.md`、`acceptance-workflow/SKILL.md` 里 `fetch_source` 只出现在取材那一步），
   之后三级门读的是**同一份**落盘的 `source_facts.json`——拿旧锚比旧锚，永远相等。
   实测（a3，ops-nn median 全量 `build.sh --pkg` + `.run --install-path`）：产物落在仓根
   `build_out/`，`op_subdir` 摘要构建前后不变，所以这道门在真实构建上不会误伤。
   PR 通路两次都做不了——`source_facts` 里根本没有本地路径，如实挂账，别以为它也校了。

4. **`--library` 必须真的被这次 build 动过。**
   构建前后各取一次 `(st_mtime_ns, st_size, sha256)`；文件在 build 前就存在、build 后三项全同
   → fail-closed。少了这一条，`-- /usr/bin/true` 配上一个**预先存在的 CANN 内置** ELF
   照样能产出一份完整收据——那正是本模块开头第 10-11 行说要堵的洞，堵不上收据就没有意义。
   ⚠ 它证明的是「这个文件在构建窗口内被改写过」，**不是**「它由这条 argv 产出」：
   `touch` 一下就能骗过。见下面「边界」。

5. **`artifact.library_path` 记 `realpath`。**
   下游 driver 拿 `os.path.realpath(artifact.library_path)` 与环境变量指的 ELF 比，
   adapter 则拿它与 `vendor.library_path` 做**字符串**全等比。记 realpath 是唯一让两边都过的写法。

## 边界（这份收据不证明什么）

- **不证明 `--build-cwd` 是被测仓**：PR 通路无从对账（见上面第 3 条）。
- **不证明构建输入闭包**：本地锚 `root_digest` 只覆盖 `op_subdir`，不含仓级构建脚本、
  公共头文件、`third_party`。同一个 `root_digest` 完全可能构建出不同的 `.so`。
  子树内 `**/build/**`、`**/build_out/**` 也在 `digest_policy` 的排除段里，那块是已知盲区。
- **不证明 `--library` 由 `--` 后那条 argv 产出**：设计第 4 条只核「构建窗口内被改写过」。
  刻意伪造（`touch` / 另起进程写）仍能通过。要真闭环得让构建系统出 in-toto / SLSA 风格的
  签名 attestation 且**消费者去验签**——光把 schema 升到 v2 加一个
  `build.evidence: "executed" | "self_reported"` 仍然只是另一句可手写的自报，不算数。
- **不证明真机加载的就是它**：driver 与三级门各自再 hash 一次，但两处都**按路径**打开/hash，
  中间可被 rename 换掉（ABA）；且 `ctypes.CDLL(..., RTLD_GLOBAL)` 只保证这个文件被 dlopen，
  **不保证符号解析落在它身上**——`torch_npu` 可能已把 CANN 的同名符号先载进全局命名空间。
  「我 hash 了这个文件」≠「调用进了这个文件」。那两条都是 `cpp_extension_driver` 侧的事，
  本收据管不到，如实挂账。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import content_address
import dut_source
import fetch_source


_SCHEMA = "oprunway.vendor_build_receipt"
_SCHEMA_VERSION = 1
_STATUS_VERIFIED = "VERIFIED"
_SOURCE_DOMAIN = "oprunway/source-facts/v1"


class ReceiptError(RuntimeError):
    """产收据失败。全部 fail-closed：宁可不产，也不产一份说不清来源的收据。

    ⚠ 它是 `RuntimeError` 的**子类**，所以 `except ReceiptError` **接不住**父类。
    `fetch_source.compute_root_digest` / `resolve_op_subdir` 抛的正是裸 `RuntimeError`，
    `dut_source` 抛的是 `ValueError` 子类——这些都必须在**信任边界处窄范围转换**成
    `ReceiptError`，否则 `__main__` 会喷 traceback 而不是 `[receipt] ✗ <人话>`。
    别为了图省事在顶层裸抓 `Exception`：那会把真正的 bug 也伪装成「证据不足」。
    """


def _sha_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as src:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        # ⚠ 不转换的话这条 `OSError` 会穿过 `__main__` 的 `except ReceiptError`
        #   直喷 traceback（见 `ReceiptError` 的 ⚠）。
        raise ReceiptError(f"无法读取 {path} 算 sha256：{exc}") from exc
    return h.hexdigest()


def library_fingerprint(path):
    """`--library` 的身份快照 `(st_mtime_ns, st_size, sha256)`；文件不存在返回 `None`。

    构建前后各取一次，用来回答这份收据最该回答、以前却没人问的那个问题——
    **这个 `.so` 到底是不是这次 build 动出来的**（模块头设计第 4 条）。

    三项一起比而不是只比 sha256：确定性构建产出字节相同的 `.so` 是正常的，
    那时 `mtime_ns` 会变，不该误伤。三项**全同**才判定「构建根本没碰它」。
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReceiptError(f"--library 无法 stat：{path}（{exc}）") from exc
    return (st.st_mtime_ns, st.st_size, _sha_file(path))


def load_source_facts(path):
    """读 `source_facts.json` 并返回 payload；envelope 自洽 **且** 载重字段合三级门的契约。

    两道，缺一不可：

    ① `content_address.read_artifact` 而不是裸 `json.load`——核 envelope 的 digest 与 domain。
       一份被手改过的 source_facts 在这里就该拦下，而不是等到三级门才发现锚对不上：
       那时报的错会指向 build receipt，把人引到错的地方查。

    ② `validate_preparation_state._validate_source_payload`——**与三级门同一个函数**。
       ⚠ 只做 ① 是不够的：内容寻址摘要**不具备真实性**，谁都能给任意 payload 重算一个自洽的
       envelope。只核 ① 的话，一份 `completeness.reasons` 非空、key_files 没绑锚、
       warnings 与载重事实对不上的 payload 照样能在这里产出 `status=VERIFIED` 的收据，
       直到验收阶段才被三级门（`validate_acceptance_state._find_source_facts` 调的正是这个
       validator）拦下——白烧一整轮 build+run。产出方不许比消费者松。
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ReceiptError(f"--source-facts 不是存在的普通文件：{path}")
    directory, name = os.path.dirname(path), os.path.basename(path)
    try:
        payload = content_address.read_artifact(directory, name, _SOURCE_DOMAIN)
    except content_address.ContentAddressError as exc:
        raise ReceiptError(f"source_facts 不是自洽的 {_SOURCE_DOMAIN} envelope：{exc}") from exc

    completeness = payload.get("completeness") if isinstance(payload, dict) else None
    completeness = completeness if isinstance(completeness, dict) else {}
    if completeness.get("status") != "complete" or completeness.get("reasons") != []:
        # 先单独报这一条：它是最常见的失败，且值得一句人话，而不是被淹在通用契约错误里。
        raise ReceiptError(
            f"source_facts.completeness.status={completeness.get('status')!r}、"
            f"reasons={completeness.get('reasons')!r} —— 须 complete 且 reasons 为空。\n"
            f"  → 取材本身没走完，据它产的收据锚不可信。先把取材做完。")
    # 惰性 import：与三级门 `_find_source_facts` 同一写法，不给本模块多加一条顶层依赖。
    import validate_preparation_state
    try:
        validate_preparation_state._validate_source_payload(payload)
    except content_address.ContentAddressError as exc:
        raise ReceiptError(
            f"source_facts 载重字段不合三级门的契约（产出方与消费者用同一份判据）：{exc}") from exc
    return payload


def _url_has_userinfo(value):
    """`scheme://userinfo@host/…` 形态即判「带用户凭据」。

    只认 `://` 形式：scp 式 `git@host:path` 的 `@` 前面是用户名、不含任何密钥，
    拦它会把合法的 SSH remote 全部误伤。而 `https://user:pw@host/…`（密码）与
    `https://<token>@host/…`（PAT，连冒号都没有）都落在 `://` 形式里，一并拦下。
    """
    if not isinstance(value, str) or "://" not in value:
        return False
    return "@" in value.split("://", 1)[1].split("/", 1)[0]


def derive_repo(payload, kind):
    """从 source_facts 派生仓名，返回 `(repo, repo_source)`；派生不出返回 `(None, None)`。

    ⚠ **两条通路的形态本来就不同**（PR 记 `owner/repo`、本地记 remote URL），这里
    **不做归一化**。CP-F 拿 `directive.source_identity.repo` 与首轮
    `runner_binding.base_source_repo` 做**逐字**比对，归一化只会制造「两边看着一样、
    比起来不等」的新坑；而且随手归一化本身能制造 fail-open——丢掉 host 就会把不同 forge
    上的同名仓合并成一个。派生值是什么，起草 directive 时就逐字抄什么（脚本末尾会打印）。
    形态差异改为在 `source.repo_source` 里**如实记账**，不靠改写字符串抹平。

    ⚠ 本函数**只派生、不判凭据**：派生值可用不可用是调用方的策略（给了 `--repo` 时，
    一个带凭据的派生值只是「用不上」，不该连 build 都不让跑）。凭据判定见
    `assert_repo_has_no_credentials`。
    """
    if kind == dut_source.PULL_REQUEST:
        facts = payload.get("pr")
        repo = facts.get("source_repo") if isinstance(facts, dict) else None
        origin = "pr.source_repo"
    else:
        facts = payload.get("local_checkout")
        git = facts.get("git") if isinstance(facts, dict) else None
        repo = git.get("remote_url") if isinstance(git, dict) else None
        origin = "local_checkout.git.remote_url"
    if not isinstance(repo, str) or not repo.strip():
        return None, None
    return repo.strip(), origin


def assert_repo_has_no_credentials(repo, origin):
    """派生出的仓名要真被写进收据之前，先确认它不带用户凭据。

    ⚠ `fetch_source.probe_local_git` 取的是 `git config --get remote.origin.url` 的
    **原值、全程无脱敏**，所以 `https://user:token@gitcode.com/…` 会一路进
    source_facts → 收据 `source.repo` → 终端打印 →
    `render_acceptance_markdown` 的「源码仓」一行 → **人读的验收报告 .md**。
    这直接撞仓规 §2「token、密码、私钥连本地 ignored 文件都不得写」。

    ⚠ **不做脱敏后照用**：脱敏会改字节，而 CP-F 对 `repo` 是逐字比对，
    等于换个方式制造 BLOCK。要继续就显式给一个干净的 `--repo`。
    """
    if not _url_has_userinfo(repo):
        return
    # ⚠ **刻意不回显原值**：把带凭据的 URL 打进报错信息，报错本身就成了第二处泄漏点。
    raise ReceiptError(
        f"{origin} 是一个**带用户凭据**的 URL（`scheme://…@host/…`），拒绝写进收据。\n"
        f"  收据的 source.repo 会落盘、会被打印、还会渲进人读验收报告的「源码仓」一行；\n"
        f"  把 token/密码带进去违反仓规 §2。此处刻意不回显原值——回显就是再泄漏一次。\n"
        f"  → 用 --repo 显式给一个不含凭据的仓名（如 `cann/ops-nn`）。\n"
        f"  → 源头在 `git config --get remote.origin.url`，那份 source_facts.json 里\n"
        f"    也留着同一个值，请一并处置。")


def assert_build_tree_matches_fingerprint(payload, kind, build_cwd, *, stage="构建前"):
    """本地通路：核「你 build 的那棵树」就是「当初被指纹的那棵树」。

    `source_facts` 只记摘要不记仓根路径（绝对路径不可移植、跨工作区无法命中），
    所以这条对应关系本来**无人核**——`--build-cwd` 指到另一份 checkout 上照样能产收据。

    这里用**同一个** `fetch_source.compute_root_digest`（不是另写一份）在 `--build-cwd`
    下重算，与 `source_facts` 的值比对。写进收据的值仍取自 `source_facts`——
    重算只作校验，不作派生。

    ⚠ PR 通路**做不了**这一步：`source_facts` 里没有任何本地路径，没有对照物。
    别看到这个函数就以为两条通路都校了。

    `stage` 决定报错措辞，两次调用语义不同（模块头设计第 3 条）：

    · `"构建前"`：回答「什么字节进了这次构建」——build 错了树 / 树在取材后被改过；
    · `"构建后"`：回答「这次构建有没有把被测子树改掉」。

    ⚠ 构建后那次**必须由本脚本做，没有下游会接手**。曾经这里写着「构建后的漂移由下游
    接住：再跑一次 `fetch_source` 就会得到不同的 root_digest」——**那句话是错的**，
    已删。编排层只在 CP-A 取材跑一次 `fetch_source`，build 之后再没有任何一步重新取材；
    三级门读的是同一份落盘的 `source_facts.json`，拿旧锚比旧锚，永远相等。
    那个「救援」从不发生。

    ⚠ 就算真在构建后重跑，仍有它查不出的漂移，别把这道门当成万能：
    `build` / `build_out` 等路径段在 `digest_policy` 里被排除（in-tree build 会让摘要
    随每次构建漂移，取材↔构建间的校验会永远失败，所以排除是有意的），往那些目录里写
    生成源再编译它，摘要不变；软链在摘要里只记 `os.readlink()` 的目标**字符串**，
    目标文件内容变了摘要也不变；构建期间临时改源码、编完恢复同样查不出——快照就是快照。
    """
    if kind != dut_source.LOCAL_CHECKOUT:
        return None
    local = payload.get("local_checkout")
    if not isinstance(local, dict):
        raise ReceiptError(
            "source_facts.local_checkout 缺失或不是 JSON object，无法核构建树")
    op_subdir, recorded = local.get("op_subdir"), local.get("root_digest")
    if not isinstance(op_subdir, str) or not op_subdir:
        raise ReceiptError("source_facts.local_checkout.op_subdir 缺失，无法核构建树")
    policy = local.get("digest_policy")
    if policy != fetch_source.digest_policy():
        raise ReceiptError(
            f"source_facts.local_checkout.digest_policy 不是本工具支持的策略，"
            f"重算出来的摘要与它不可比：\n  收据里：{policy!r}\n  支持的：{fetch_source.digest_policy()!r}")
    try:
        actual = fetch_source.compute_root_digest(build_cwd, op_subdir)
    except (RuntimeError, OSError) as exc:
        # ⚠ `resolve_op_subdir` / `compute_root_digest` 抛的是**裸 RuntimeError**（子目录不存在、
        #   scandir 失败、遇到 FIFO/socket）。`ReceiptError` 是它的子类，接不住父类——
        #   不在这里转换，用户看到的就是 traceback 而不是 `[receipt] ✗ <人话>`。
        raise ReceiptError(
            f"在 {build_cwd}/{op_subdir} 重算被测子树摘要失败（{stage}）：{exc}") from exc
    if actual == recorded:
        return actual
    if stage == "构建前":
        raise ReceiptError(
            f"`--build-cwd` 下的被测子树与 source_facts 记录的**不是同一份字节**：\n"
            f"  source_facts.local_checkout.root_digest = {recorded}\n"
            f"  在 {build_cwd}/{op_subdir} 重算            = {actual}\n"
            f"  → 你要么 build 错了树，要么这棵树在取材之后被改过。\n"
            f"    收据若照产，它会声称构建自一份并不存在于此处的源码。fail-closed。")
    excluded = "、".join(fetch_source.digest_policy()["excluded_segment_names"])
    raise ReceiptError(
        f"**这次 build 把被测子树改掉了**：构建前与 source_facts 一致，构建后已经不是同一份字节：\n"
        f"  source_facts.local_checkout.root_digest = {recorded}\n"
        f"  build 跑完后在 {build_cwd}/{op_subdir} 重算 = {actual}\n"
        f"  → 收据会声称「这个 .so 构建自 {str(recorded)[:12]}…」，而那份字节此刻已不存在，\n"
        f"    谁也复现不了；而且**没有下游会发现**（编排只在 CP-A 取材跑一次 fetch_source）。\n"
        f"  → 改成 out-of-tree 构建，或让生成物落进已排除的路径段（{excluded}）。fail-closed。")


def run_build(build_cwd, argv):
    """真跑 build，返回 returncode。**不捕获 stdout/stderr**——构建日志要直给人看。

    ⚠ 非 0 也**照常返回**而不是抛：调用方紧接着就用真实 returncode 报错
    （`main` 在 build 之后**第一件事**就是查它），这样报错里能带上真实退出码，
    而不是笼统一句「构建失败」。`self_check` 里那道同样的检查是对**任意**收据的
    契约自检，不是这条路径的兜底，两处都留着。
    """
    print(f"[receipt] 执行 build（cwd={build_cwd}）：{' '.join(argv)}", flush=True)
    try:
        run = subprocess.run(argv, cwd=build_cwd, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiptError(f"build 命令无法执行：{exc}") from exc
    print(f"[receipt] build 退出码 = {run.returncode}", flush=True)
    return run.returncode


def build_receipt(payload, kind, repo, build_cwd, argv, returncode, library,
                  *, repo_source, library_sha256, existed_before_build):
    """组装收据 dict。锚**只从 payload 取**（见模块头设计第 1 条）。

    `library_sha256` 由调用方传入而不是这里现算：它必须是**构建后那次快照**里的同一个值，
    现算会再读一遍文件，两次读之间文件若被换掉，收据记的 sha 就和刚校验过的那份对不上。

    ⚠ 新增键都落在 `source.repo_source` / `artifact.existed_before_build` 上，安全性核过：
    三处消费者（`cpp_extension_adapter._validate_vendor_build_receipt`、
    `dut_source.validate_build_receipt_source`、
    `validate_acceptance_state._gate_build_receipt_source_binding`）对收据一律**按键 `.get` 取**，
    没有 exact-key-set 校验；CP-F 那个严格键集加在 `directive.source_identity` 上，不是收据。
    """
    try:
        _, anchor_field, anchor_value = dut_source.identity(payload, where="source_facts")
    except dut_source.DutSourceError as exc:
        raise ReceiptError(f"source_facts 的来源锚不合法，无法组装收据：{exc}") from exc
    # `repo_source` 如实标注 repo 这一项的**强度**：事实派生 vs 操作者自报。
    # 不记的话，`--repo` 一给，「事实派生」就被无声换成「操作者自报」，而 CP-F 那道
    # 逐字比对的门看不出差别——它比的就不再是事实了。
    source = {"repo": repo, "repo_source": repo_source, anchor_field: anchor_value}
    if kind != dut_source.PULL_REQUEST:
        # ⚠ PR 通路**不写** `dut_source` 键——与 `fetch_source` 同口径：缺省即 pull_request，
        #   写了反而让既有 PR 收据的字节形态发生变化。
        source["dut_source"] = kind
    return {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "status": _STATUS_VERIFIED,
        "source": source,
        "build": {
            "argv": list(argv),
            "cwd": os.path.realpath(build_cwd),
            "returncode": returncode,
        },
        "artifact": {
            # realpath：driver realpath 后比、adapter 字符串全等比，记 realpath 两边才都过
            "library_path": os.path.realpath(library),
            "library_sha256": library_sha256,
            # 收据能落盘就意味着「构建窗口内这个文件被改写过」已经过关（设计第 4 条）。
            # 这一项进一步区分「build 从无到有产出了它」与「build 覆盖了一份已存在的」——
            # 后者更容易混进上一轮的残留，报告里值得看得见。
            "existed_before_build": existed_before_build,
        },
    }


def self_check(receipt, payload):
    """**产出方自己先过一遍消费者的门。**

    ⚠ 不是多余：收据的下一站是真机跑测，那里失败要等一整轮 build+run 才看得到。
    在这里过一遍 `dut_source.validate_build_receipt_source`（三处消费者用的同一个函数），
    形态错当场就报，且报的是「产收据时就错了」而不是「验收时对不上」。
    """
    kind = dut_source.of(payload, where="source_facts")
    try:
        dut_source.validate_build_receipt_source(
            receipt["source"], expected_kind=kind, where="生成的 receipt.source")
    except dut_source.DutSourceError as exc:
        raise ReceiptError(f"自检未过——生成的收据自己就不合消费者的契约：{exc}") from exc
    if receipt["build"]["returncode"] != 0:
        raise ReceiptError(
            f"build 退出码 {receipt['build']['returncode']} ≠ 0，**不产收据**。\n"
            f"  消费者三处都硬校 `build.returncode == 0`，产一份注定被拒的收据只会\n"
            f"  把失败推迟到验收阶段才暴露。请先修构建。")


def assert_out_is_writable(path, ap, conflicts=()):
    """**在 build 之前**确认 `--out` 落得下去，并拒绝它与输入文件同名。

    ⚠ 这条不是洁癖，是这两个设计决定交叉出来的最贵失败模式：参数校验号称全部前置，
    但 `--out` 的可写性直到 `atomic_write`（build 之后）才被摸到；而本脚本又
    **没有「只记录不执行」模式**。于是 `--out` 打错一个字或目录只读 = 几十分钟 build
    全部作废、必须整轮重跑。

    同名检查一并前置：`--out == --library` 会把 JSON 原子替换掉被测 ELF（直接毁掉被测物），
    `--out == --source-facts` 会毁掉锚的对照物。
    """
    out_real = os.path.realpath(path)
    for flag, other in conflicts:
        if other is not None and os.path.realpath(other) == out_real:
            ap.error(f"--out 与 {flag} 指向同一个文件（{out_real}）：写收据会把它覆盖掉")
    parent = os.path.dirname(out_real) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        probe_fd, probe = tempfile.mkstemp(prefix=".vendor-build-receipt-probe-", dir=parent)
        os.close(probe_fd)
        os.unlink(probe)
    except OSError as exc:
        ap.error(f"--out 的目录写不进去：{parent}（{exc}）"
                 f" —— 先修好落点，别等 build 跑完几十分钟才发现")


def atomic_write(path, receipt):
    """原子写；异常时不留半截收据（半截收据比没有更坏——它看着像一份真的）。"""
    parent = os.path.abspath(os.path.dirname(path) or ".")
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".vendor-build-receipt-", dir=parent)
    except OSError as exc:
        raise ReceiptError(f"收据落点不可写：{parent}（{exc}）") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            fd = -1
            json.dump(receipt, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise ReceiptError(f"写收据失败：{path}（{exc}）") from exc
    finally:
        if fd != -1:
            os.close(fd)
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


def main(argv):
    ap = argparse.ArgumentParser(
        description="产 vendor_build_receipt：真跑 build，把「被测来源→构建→安装 ELF」锁成一份机器可核的收据")
    ap.add_argument("--source-facts", required=True, metavar="PATH",
                    help="fetch_source 产的 source_facts.json。**来源锚的唯一真源**，不另算")
    ap.add_argument("--build-cwd", required=True, metavar="DIR", help="build 命令的工作目录")
    ap.add_argument("--library", required=True, metavar="SO",
                    help="安装后、真机实际会加载的那个 .so，**须绝对路径**"
                         "（build 跑完才 hash，且必须真被这次 build 动过）")
    ap.add_argument("--out", required=True, metavar="JSON", help="收据落点")
    ap.add_argument("--repo", default=None,
                    help="覆盖从 source_facts 派生的仓名。⚠ 要与 CP-F directive 的 "
                         "source_identity.repo 逐字一致（那边是字节比对、不归一化）")
    ap.add_argument("--allow-repo-override", action="store_true",
                    help="允许 --repo 与 source_facts 派生出的仓名**不一致**。"
                         "收据会记 repo_source=\"operator\"，如实标注这一项是操作者自报")
    ap.add_argument("build_argv", nargs=argparse.REMAINDER,
                    help="`--` 之后是 build 命令 argv，会被真正执行")
    a = ap.parse_args(argv)

    build_argv = a.build_argv[1:] if a.build_argv[:1] == ["--"] else a.build_argv
    # 参数校验**全部前置到跑 build 之前**：build 动辄几十分钟，跑完才发现参数错是纯浪费，
    # 且失败点会落在「已经改了机器状态之后」。
    if not build_argv:
        ap.error("缺 build 命令：用 `-- <argv...>` 给出。本脚本不接受「只记录不执行」")
    if not os.path.isdir(a.build_cwd):
        ap.error(f"--build-cwd 不是目录：{a.build_cwd}")
    if not os.path.isabs(a.library):
        # ⚠ 相对路径按**调用方 cwd** 解析，不是 `--build-cwd`（`subprocess.run(cwd=…)` 不改
        #   本进程 cwd）。写 `--library build_out/x.so` 的人几乎都以为是相对构建目录的，
        #   运气不好就 hash 到调用方 cwd 下的同名文件——静默错绑。与
        #   `cpp_extension_driver._require_env_path` 同一口径：要绝对路径。
        ap.error(f"--library 须是绝对路径（相对路径按调用方 cwd 解析、不是 --build-cwd）：{a.library}")
    assert_out_is_writable(a.out, ap, conflicts=(("--library", a.library),
                                                 ("--source-facts", a.source_facts)))

    payload = load_source_facts(a.source_facts)
    try:
        kind = dut_source.of(payload, where="source_facts")
    except dut_source.DutSourceError as exc:
        raise ReceiptError(f"source_facts 的来源判别式不合法：{exc}") from exc

    derived_repo, derived_origin = derive_repo(payload, kind)
    if derived_repo and _url_has_userinfo(derived_repo):
        if not a.repo:
            assert_repo_has_no_credentials(derived_repo, derived_origin)   # 必抛
        # 给了 --repo：派生值只是**用不上**，不该连 build 都不让跑。它也不参与下面的
        # 冲突比对——否则会强迫操作者为一个本来就不可用的值去加 --allow-repo-override。
        derived_repo, derived_origin = None, None
    if a.repo:
        if derived_repo and derived_repo != a.repo and not a.allow_repo_override:
            # ⚠ `--repo` 无条件优先 = 把「事实派生」无声换成「操作者自报」，而 CP-F 拿这个值
            #   与首轮 runner_binding.base_source_repo 做逐字比对——那道门比的就不再是事实。
            raise ReceiptError(
                f"--repo 与 source_facts 派生出的仓名不一致：\n"
                f"  --repo（操作者自报）      = {a.repo!r}\n"
                f"  {derived_origin}（事实派生）= {derived_repo!r}\n"
                f"  → 默认不许静默替换。确实要换（本地通路派生出的是带 host/.git 后缀的\n"
                f"    remote URL，而 CP-F directive 那边通常写 `owner/repo`），\n"
                f"    显式加 --allow-repo-override，收据会记 repo_source=\"operator\"。")
        repo = a.repo
        repo_source = "operator" if (derived_repo or None) != a.repo else derived_origin
    else:
        repo, repo_source = derived_repo, derived_origin
    if not repo:
        ap.error("source_facts 里派生不出仓名（PR 通路取 pr.source_repo、本地取 "
                 "local_checkout.git.remote_url），请显式给 --repo")

    verified = assert_build_tree_matches_fingerprint(payload, kind, a.build_cwd)
    if verified:
        print(f"[receipt] ✓ 构建树与 source_facts 指纹一致：root_digest={verified[:12]}…")
    else:
        print("[receipt] ⚠ PR 通路无法核「构建树 ↔ 指纹树」（source_facts 里没有本地路径）")

    # build 之前先给 `--library` 拍一张身份快照（不存在 → None）。构建后要拿它比对，
    # 回答「这个 .so 到底是不是这次 build 动出来的」。见模块头设计第 4 条。
    before = library_fingerprint(a.library)
    returncode = run_build(a.build_cwd, build_argv)
    if returncode != 0:
        # 先报退出码再查产物：build 失败时「library 不存在」只是它的后果，
        # 先报后果会把人引去查 install。
        raise ReceiptError(
            f"build 退出码 {returncode} ≠ 0，**不产收据**。\n"
            f"  消费者三处都硬校 `build.returncode == 0`，产一份注定被拒的收据只会\n"
            f"  把失败推迟到验收阶段才暴露。请先修构建。")
    if not os.path.isfile(a.library):
        raise ReceiptError(
            f"--library 在 build 之后仍不是存在的普通文件：{a.library}\n"
            f"  → 若 install 是独立一步，把它并进 `--` 后的同一条命令里"
            f"（如 `bash -c \"./build.sh … && ./build_out/*.run --install-path=…\"`）。")
    after = library_fingerprint(a.library)
    if before is not None and after == before:
        raise ReceiptError(
            f"`--library` 在这次 build 前后**一个字节都没动**"
            f"（mtime_ns / size / sha256 三项全同）：{a.library}\n"
            f"  → 这份收据的核心主张是「这个 .so 构建自那份被测源码」。构建根本没碰它，\n"
            f"    那它可能是上一轮的残留、甚至是 CANN 内置的同名实现——正是本收据要堵的洞。\n"
            f"  → 增量构建没重链接：清掉产物重跑；install 是独立一步：并进 `--` 后的同一条命令。\n"
            f"  fail-closed。")
    print(f"[receipt] ✓ --library 在构建窗口内被改写"
          f"（{'覆盖了已存在的文件' if before is not None else 'build 从无到有产出'}）")
    # 构建后再核一次被测子树：build 若把 op_subdir 改掉，收据声称的那份字节此刻已不存在。
    # **没有下游会做这件事**——编排只在 CP-A 取材跑一次 fetch_source。
    if assert_build_tree_matches_fingerprint(payload, kind, a.build_cwd, stage="构建后"):
        print("[receipt] ✓ build 没有改动被测子树（构建后重算摘要仍与 source_facts 一致）")

    receipt = build_receipt(
        payload, kind, repo, a.build_cwd, build_argv, returncode, a.library,
        repo_source=repo_source, library_sha256=after[2],
        existed_before_build=before is not None)
    self_check(receipt, payload)
    atomic_write(a.out, receipt)

    # 词表→字段名的映射只有 `dut_source.ANCHOR_FIELD` 一份，别在这里自建第二份：
    # 这行的输出正是给人抄进 CP-F directive 的，词表一改就是无声分叉。
    anchor_field = dut_source.ANCHOR_FIELD[kind]
    print(f"[receipt] → {os.path.abspath(a.out)}")
    print(f"           dut_source = {kind}")
    print(f"           {anchor_field} = {receipt['source'][anchor_field]}")
    print(f"           library_sha256 = {receipt['artifact']['library_sha256']}")
    print()
    print("下一步：")
    print(f"  export OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT={os.path.abspath(a.out)}")
    print(f"  export OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY={receipt['artifact']['library_path']}")
    print()
    # ⚠ 这一行是给 CP-F 起草 directive 用的：那边 `source_identity.repo` 与本收据的
    #   `repo` 做**逐字**比对、不归一化，写法差一个字符（带不带 host、大小写、.git 后缀）
    #   就会 BLOCK。把下面这行原样抄过去。
    print(f"  CP-F directive 的 source_identity.repo 必须逐字写成：{repo!r}")
    print(f"  （repo_source={repo_source!r}"
          f"{'——操作者自报，不是从 source_facts 派生的事实' if repo_source == 'operator' else ''}）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]) or 0)
    except ReceiptError as ex:
        print(f"[receipt] ✗ {ex}", file=sys.stderr)
        sys.exit(1)
