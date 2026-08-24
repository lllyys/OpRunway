"""生成侧接口派生与验收裁决门的跨侧集成测试。"""

import sys
import unittest

from _paths import SCRIPTS, require_nested_source

sys.path.insert(0, str(SCRIPTS))

from _policy import load_policy  # noqa: E402


class BuiltinTopologyEndToEndTest(unittest.TestCase):
    # 第二轮实测到的后端：主节点跑待验收算子，cpu 节点从磁盘读真值
    ROUND_TWO_BACKENDS = ["pyaclnn", "cpu"]

    def _interface(self, baseline_backend):
        return {"interface_mode": "aclnn", "execution_backend": "pyaclnn",
                "candidate_symbol": "aclnnBernoulli",
                "baseline_api": "aclnnBernoulli", "mode_source": "任务书 §2",
                "baseline_kind": "cann_builtin",
                "baseline_backend": baseline_backend}

    def test_derive_interface_produces_a_backend_this_gate_accepts(self):
        require_nested_source(self, "生成侧派生到验收侧裁决的交接边界")
        # 真正的端到端：S1 派生什么，S4 就得认什么。
        import derive_interface
        from verdict import check_interface

        self.assertIsNone(derive_interface.check_baseline_shape(
            "cann_builtin", "cpu", "用户指定"))
        check_interface(self._interface("cpu"),
                        {"backends": self.ROUND_TWO_BACKENDS}, load_policy())


if __name__ == "__main__":
    unittest.main()
