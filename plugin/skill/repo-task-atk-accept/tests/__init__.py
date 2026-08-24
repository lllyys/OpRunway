"""让嵌套源与展开产物都能用同一个 `_paths` 模块名。"""

import sys

from . import _paths as _paths_module


sys.modules.setdefault("_paths", _paths_module)
