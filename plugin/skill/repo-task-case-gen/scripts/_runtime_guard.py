"""把「解释器不对 / 环境没加载」翻译成一句可执行的下一步。

裸 Traceback 里没有下一步。真机上两次踩到同一形状的坑：一次是量具用系统
`python3` 跑、缺 torch，另一次是 CANN 环境没加载、torch_npu 的后端扩展把
`import torch` 拖崩。两次给出的都是几十行调用栈，agent 只能换个写法再试。

栈本身不是信息——「换哪个解释器、还缺哪一步」才是。
"""

import contextlib
import sys


# 与「门禁不通过」的 2、3 分开：这不是待验收对象的问题，是跑测姿势的问题。
RUNTIME_EXIT = 4


@contextlib.contextmanager
def runtime_imports(*modules, env_hint="evidence/env.json"):
    """包住需要 torch / torch_npu / atk 的导入，失败时给一句能照着做的提示，而不是抛一堆栈。"""
    try:
        yield
    except (ImportError, RuntimeError, OSError) as exc:
        names = "、".join(modules) or "运行时依赖"
        print(f"✗ 当前解释器导入 {names} 失败：{exc}", file=sys.stderr)
        print(f"  解释器：{sys.executable}", file=sys.stderr)
        print(f"  处置：改用 {env_hint} 的 selected_python 重跑；",
              file=sys.stderr)
        print(f"        仍失败时先加载 {env_hint} 的 cann.set_env 再重跑。",
              file=sys.stderr)
        raise SystemExit(RUNTIME_EXIT) from exc
