"""catlass_parse.py 加固单测（stdlib unittest）——15 条对抗门的负例 + 诚实边界固化。

跑: python3 test_catlass_parse.py          （在 acc-common/ 下）
或: python3 -m unittest test_catlass_parse -v

原则：**绝不为了让测试过而削弱防护**。每条负例断言「攻击被挡住」，正例断言「真信号仍读得到」。
⚠ 关键诚实边界（#3）：未传 expected_case_ids/run_nonce 时，本解析器**无法**防整表伪造——
下方 test_forgery_undefended_boundary 显式断言这一点、并注释说明这是**已知边界**、不是 bug。
"""
import os
import tempfile
import unittest

import catlass_parse as P


# ══════════════════════════════════════════════════════════════════════════
# parse_raw_log —— 判「跑没跑对」的源头
# ══════════════════════════════════════════════════════════════════════════
class RawLogHardeningTest(unittest.TestCase):

    def test_last_wins_blocked_failed_then_success(self):
        """#1 Compare failed. 后追 Compare success. → 该 cid 进 conflicts/errors、**不判 ok**。"""
        log = ("[OPRUNWAY_CASE c0] Compare failed. Error count: 7\n"
               "[OPRUNWAY_CASE c0] Compare success.\n"
               "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0\n")
        r = P.parse_raw_log(log)
        self.assertNotIn("c0", r["cases"])            # 绝不 last-wins 成 ok
        self.assertIn("c0", r["conflicts"])
        self.assertTrue(any("conflicting_result" in e for e in r["errors"]))

    def test_duplicate_same_result_also_conflict(self):
        """#1 同一 cid 出现 ≥2 次结果（即便都 ok）→ 记 conflict、不进 cases。"""
        log = ("[OPRUNWAY_CASE c0] Compare success.\n"
               "[OPRUNWAY_CASE c0] Compare success.\n")
        r = P.parse_raw_log(log)
        self.assertNotIn("c0", r["cases"])
        self.assertIn("c0", r["conflicts"])

    def test_ambiguous_single_line(self):
        """#2 一行同含 success 与 failed → 歧义 error、不采信该行。"""
        log = ("[OPRUNWAY_CASE c0] Compare success. also Compare failed.\n"
               "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0\n")
        r = P.parse_raw_log(log)
        self.assertNotIn("c0", r["cases"])
        self.assertEqual(r["ambiguous_lines"], [1])
        self.assertTrue(any("ambiguous_line" in e for e in r["errors"]))

    def test_forgery_undefended_boundary(self):
        """#3【诚实边界】未传 expected_case_ids/run_nonce → **无法**防整表伪造。

        这**不是** bug：解析器本身没有「真集/nonce」可校，只能如实解析文本。
        断言此边界，逼上游必须传 expected_case_ids+run_nonce 才能防伪造（见下一条）。
        docstring 已明写此边界；测试固化之，防日后有人误以为「解析没报错=跑对了」。
        """
        fake = "".join(f"[OPRUNWAY_CASE c{i}] Compare success.\n" for i in range(5)) \
               + "OPRUNWAY_CATLASS_DONE total=5 ok=5 fail=0\n"
        r = P.parse_raw_log(fake)
        # 无从校验 → 5 个伪造 case 确实被当成 ok（已知边界，非防护失效）
        self.assertEqual(len([v for v in r["cases"].values() if v == "ok"]), 5)
        self.assertIsNone(r["expected_match"])   # 未传全集 → 不做全集判定
        self.assertIsNone(r["nonce_ok"])         # 未传 nonce → 不做 nonce 判定

    def test_forgery_defended_with_expected_and_nonce(self):
        """#3 传 expected_case_ids → 因不匹配全集而 error；传 run_nonce → 因 nonce 缺失/不符而 error。"""
        fake = "".join(f"[OPRUNWAY_CASE c{i}] Compare success.\n" for i in range(5)) \
               + "OPRUNWAY_CATLASS_DONE total=5 ok=5 fail=0\n"
        r = P.parse_raw_log(fake, expected_case_ids={"c0", "c1", "REAL2"},
                            run_nonce="NONCE-xyz")
        self.assertFalse(r["expected_match"])
        self.assertIn("c2", r["unknown_cids"])          # 伪造出的 c2/c3/c4 属未知
        self.assertIn("REAL2", r["missing_cids"])       # 真正该有的 REAL2 缺失
        self.assertFalse(r["nonce_ok"])                 # 日志无 OPRUNWAY_RUN_NONCE 行
        self.assertTrue(any("run_nonce_mismatch" in e for e in r["errors"]))

    def test_nonce_match_passes(self):
        """#3 正例：日志带正确 nonce + 全集精确 → expected_match True、nonce_ok True。"""
        log = ("OPRUNWAY_RUN_NONCE NONCE-xyz\n"
               "[OPRUNWAY_CASE c0] Compare success.\n"
               "[OPRUNWAY_CASE c1] Compare failed.\n"
               "OPRUNWAY_CATLASS_DONE total=2 ok=1 fail=1\n")
        r = P.parse_raw_log(log, expected_case_ids={"c0", "c1"}, run_nonce="NONCE-xyz")
        self.assertTrue(r["expected_match"])
        self.assertTrue(r["nonce_ok"])
        self.assertEqual(r["cases"], {"c0": "ok", "c1": "fail"})

    def test_nonce_wrong_value_rejected(self):
        """#3 日志里的 nonce 与传入不符 → nonce_ok False。"""
        log = ("OPRUNWAY_RUN_NONCE WRONG\n"
               "[OPRUNWAY_CASE c0] Compare success.\n"
               "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0\n")
        r = P.parse_raw_log(log, run_nonce="RIGHT")
        self.assertFalse(r["nonce_ok"])

    def test_truncated_case_marker_no_result(self):
        """#4 只有 [OPRUNWAY_CASE] 无结果即截断 → truncated/crashed 被检出。"""
        log = "[OPRUNWAY_CASE c0] m=16 n=16 k=16 running kernel\n"
        r = P.parse_raw_log(log)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["crashed"])
        self.assertIn("c0", r["case_seen"])
        self.assertEqual(r["cases"], {})

    def test_empty_and_whitespace_log_error(self):
        """#5 空 / 纯空白 → errors=['empty_log']（不静默当跑完无异常）。"""
        for s in ("", "   \n\t  \n", None):
            r = P.parse_raw_log(s)
            self.assertIn("empty_log", r["errors"])
            self.assertFalse(r["crashed"])
            self.assertEqual(r["cases"], {})

    def test_ansi_does_not_break_real_success(self):
        """#6 ANSI 夹在 Compare 与 success 之间 → 真成功仍被正确读到。"""
        log = ("[OPRUNWAY_CASE c0] Compare\x1b[32m success.\x1b[0m\n"
               "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0\n")
        r = P.parse_raw_log(log)
        self.assertEqual(r["cases"].get("c0"), "ok")

    def test_cr_embedded_fake_sentinel_not_trusted(self):
        """#6 CR 夹带的伪造哨兵（非整行开头）→ 不被采信。"""
        log = ("progress... 99%\r[OPRUNWAY_CASE evil] Compare success.\n"
               "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0\n")
        r = P.parse_raw_log(log)
        self.assertNotIn("evil", r["cases"])          # 行内嵌入不采信

    def test_inline_embedded_sentinel_rejected(self):
        """#3/#6 哨兵不在行首（前面有别的文本）→ 拒采信。"""
        log = ("some prefix [OPRUNWAY_CASE evil] Compare success.\n"
               "blah OPRUNWAY_CATLASS_DONE\n")
        r = P.parse_raw_log(log)
        self.assertNotIn("evil", r["cases"])
        self.assertFalse(r["done"])                    # DONE 也需整行锚定

    def test_bad_input_no_crash(self):
        """坏输入不崩、仍返回结构化 dict。"""
        for bad in (None, b"\xff\xfe rubbish", "no sentinels here", 12345, ["x"]):
            r = P.parse_raw_log(bad)
            self.assertIn("cases", r)

    def test_good_log_still_parses(self):
        """回归：正常 3-case 成功日志仍全 ok、done、无冲突。"""
        log = ("[OPRUNWAY_CASE c0] running\n[OPRUNWAY_CASE c0] Compare success.\n"
               "[OPRUNWAY_CASE c1] running\n[OPRUNWAY_CASE c1] Compare success.\n"
               "[OPRUNWAY_CASE c2] running\n[OPRUNWAY_CASE c2] Compare failed.\n"
               "OPRUNWAY_CATLASS_DONE total=3 ok=2 fail=1\n")
        r = P.parse_raw_log(log)
        self.assertEqual(r["cases"], {"c0": "ok", "c1": "ok", "c2": "fail"})
        self.assertEqual(r["conflicts"], [])
        self.assertTrue(r["done"])
        self.assertFalse(r["crashed"])
        self.assertEqual(r["success_count"], 2)
        self.assertEqual(r["failed_count"], 1)


# ══════════════════════════════════════════════════════════════════════════
# parse_msprof_csv —— 性能证据的源头
# ══════════════════════════════════════════════════════════════════════════
_GOOD = ("Op Name,OP Type,Task Type,Task Duration(us)\n"
         "k950,MatMul,AI_CORE,42.0\n"
         "k950,MatMul,AI_CORE,44.0\n"
         "k950,MatMul,AI_CORE,40.0\n")


class MsprofHardeningTest(unittest.TestCase):

    def test_good_csv_still_ok(self):
        """回归：干净 kernel-only CSV 仍 ok、统计正确、0 blocked/0 invalid。"""
        r = P.parse_msprof_csv(_GOOD)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 3)
        self.assertEqual(r["median_us"], 42.0)
        self.assertEqual(r["min_us"], 40.0)
        self.assertEqual(r["blocked_rows"], 0)
        self.assertEqual(r["invalid_count"], 0)

    def test_memcpy_sdma_row_blocked_rowlevel(self):
        """#7 含 H2D/D2H/Memcpy/SDMA 行 → 逐行拒、不进 median；blocked_rows 有计数。"""
        csv = (_GOOD + "memcpy_h2d,Memcpy,SDMA,832.5\n"
                       "dma_d2h,Memcpy,SDMA,900.0\n")
        r = P.parse_msprof_csv(csv)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 3)              # 只剩 3 个 AI_CORE 行
        self.assertEqual(r["blocked_rows"], 2)
        self.assertEqual(r["median_us"], 42.0)       # 832.5/900 未污染
        self.assertNotIn(832.5, r["durations"])

    def test_nonkernel_name_blocked_even_if_ai_core(self):
        """#7 名字含明确非 kernel 子串（memcpy）→ 即便 Task Type=AI_CORE 也 block。"""
        csv = _GOOD + "Memcpy_async_op,MatMul,AI_CORE,777.0\n"
        r = P.parse_msprof_csv(csv)
        self.assertEqual(r["blocked_rows"], 1)
        self.assertNotIn(777.0, r["durations"])

    def test_e2e_header_substring_rejected(self):
        """#7 真实列名 H2D Duration(us)（不是精确 'h2d'）→ 子串命中、整表拒。"""
        for col in ("H2D Duration(us)", "Host Duration(us)", "aclrtDuration(us)",
                    "D2H(us)", "Memcpy Duration(us)"):
            csv = f"Op Name,{col},Task Duration(us)\nk,5.0,42.0\n"
            r = P.parse_msprof_csv(csv)
            self.assertFalse(r["ok"], f"应拒 e2e 列 {col}")
            self.assertIn("kernel-only", r["reason"])

    def test_duplicate_duration_column_error(self):
        """#10 重复 Task Duration(us) 列 → error（拒后者覆盖前者）。"""
        csv = ("Op Name,Task Type,Task Duration(us),Task Duration(us)\n"
               "k,AI_CORE,42.0,9.0\n")
        r = P.parse_msprof_csv(csv)
        self.assertFalse(r["ok"])
        self.assertIn("重复列名", r["reason"])

    def test_invalid_values_fail_closed(self):
        """#11 NaN/inf/负数/字符串 → ok=False（reason 暴露污染），不静默跳过。"""
        for bad in ("NaN", "inf", "-5", "abc", "1e999"):
            csv = _GOOD + f"k950,MatMul,AI_CORE,{bad}\n"
            r = P.parse_msprof_csv(csv)
            self.assertFalse(r["ok"], f"含 {bad} 应 fail-closed")
            self.assertGreaterEqual(r["invalid_count"], 1)
            self.assertTrue(r["invalid_rows"])

    def test_empty_cell_tolerated_not_invalid(self):
        """#11 边界：空 duration cell = 结构性缺口，跳过、不算污染（仍 ok）。"""
        csv = _GOOD + "k950,MatMul,AI_CORE,\n"
        r = P.parse_msprof_csv(csv)
        self.assertTrue(r["ok"])
        self.assertEqual(r["invalid_count"], 0)
        self.assertEqual(r["count"], 3)

    def test_kernel_name_exact_not_prefix(self):
        """#9 kernel_name 精确等值：'k950' 命中 k950 行；前缀 'k' 不再误命中。"""
        csv = ("Op Name,Task Type,Task Duration(us)\n"
               "k950,AI_CORE,42.0\n"
               "k950_evil_twin,AI_CORE,999.0\n")
        r = P.parse_msprof_csv(csv, kernel_name="k950")
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 1)              # 只 k950，不含 evil_twin
        self.assertNotIn(999.0, r["durations"])
        r2 = P.parse_msprof_csv(csv, kernel_name="k")   # 前缀不再命中
        self.assertFalse(r2["ok"])

    def test_kernel_regex_controlled(self):
        """#9 受控 regex 入口：fullmatch，非行内子串。"""
        csv = ("Op Name,Task Type,Task Duration(us)\n"
               "k950,AI_CORE,42.0\n"
               "prefix_k950_suffix,AI_CORE,999.0\n")
        r = P.parse_msprof_csv(csv, kernel_regex=r"k9\d+")
        self.assertEqual(r["count"], 1)              # 只 k950 fullmatch
        self.assertNotIn(999.0, r["durations"])

    def test_bytes_input_ok(self):
        """#13 bytes 入参 → 归一 decode、不崩。"""
        r = P.parse_msprof_csv(_GOOD.encode("utf-8"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 3)

    def test_giant_quoted_field_no_crash(self):
        """#13/#14 超大引号字段 → 结构化 error，不崩（_csv.Error 被兜住）。"""
        big = 'Op Name,Task Type,Task Duration(us)\n"' + "A" * 2_000_000 + '",AI_CORE,1.0\n'
        r = P.parse_msprof_csv(big)
        self.assertFalse(r["ok"])
        self.assertIn("reason", r)

    def test_empty_csv_error(self):
        """空 CSV → ok=False。"""
        self.assertFalse(P.parse_msprof_csv("")["ok"])
        self.assertFalse(P.parse_msprof_csv("   \n  ")["ok"])

    def test_non_str_bytes_input_structured_error(self):
        """#13 非 str/bytes 入参 → 结构化 error，不崩。"""
        r = P.parse_msprof_csv(12345)
        self.assertFalse(r["ok"])
        self.assertIn("类型", r["reason"])

    def test_oversize_bytes_rejected(self):
        """#14 超字节上限 → 结构化 error。"""
        r = P.parse_msprof_csv("x" * (P._MAX_CSV_BYTES + 1))
        self.assertFalse(r["ok"])
        self.assertIn("上限", r["reason"])


# ══════════════════════════════════════════════════════════════════════════
# parse_msprof_text / parse_msprof_path —— 拆显式入口（#12）
# ══════════════════════════════════════════════════════════════════════════
class PathReadingTest(unittest.TestCase):

    def test_parse_text_does_not_auto_read_path(self):
        """#12 传一个**存在的文件路径字符串**给 parse_msprof_csv/text → 不得自动读取该文件。"""
        fd, path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(_GOOD)                        # 文件里是合法 CSV
            # 传路径字符串（无换行、短、存在）→ 旧版会自动读；新版当文本解析 → 无表头/无时长列
            r = P.parse_msprof_csv(path)
            self.assertFalse(r["ok"])                 # 没把文件读进来
            self.assertNotEqual(r.get("count"), 3)
        finally:
            os.remove(path)

    def test_parse_path_reads_within_root(self):
        """parse_msprof_path 在 root 内 → 正常读。"""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "op.csv")
        try:
            with open(path, "w") as f:
                f.write(_GOOD)
            r = P.parse_msprof_path(path, root=d)
            self.assertTrue(r["ok"])
            self.assertEqual(r["count"], 3)
        finally:
            os.remove(path)
            os.rmdir(d)

    def test_parse_path_rejects_outside_root(self):
        """#12 越出 root → 拒（防目录穿越/任意本地文件读取）。"""
        fd, path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(_GOOD)
            r = P.parse_msprof_path(path, root="/etc")
            self.assertFalse(r["ok"])
            self.assertIn("越界", r["reason"])
        finally:
            os.remove(path)

    def test_parse_path_missing_file_real_error(self):
        """#12 打不开的文件 → 保留真实错误、不静默折叠成空 CSV。"""
        r = P.parse_msprof_path("/no/such/file/really.csv")
        self.assertFalse(r["ok"])
        self.assertIn("读文件失败", r["reason"])


# ══════════════════════════════════════════════════════════════════════════
# profile_hit_gate —— 命中门（hit=True ⟺ pending=False，绝不并存）
# ══════════════════════════════════════════════════════════════════════════
class ProfileHitGateTest(unittest.TestCase):

    def test_no_name_column_not_hit(self):
        """#8 仅表头 + 一个正数、无 kernel 名列 → 不得 hit=True（pending 但非 hit）。"""
        csv = "Task Type,Task Duration(us)\nAI_CORE,42.0\n"
        g = P.profile_hit_gate(csv)
        self.assertFalse(g["hit"])
        self.assertTrue(g["pending"])

    def test_hit_and_pending_never_coexist(self):
        """#8 不变量：任何路径下 hit=True 与 pending=True 不并存。"""
        cases = [
            "Task Type,Task Duration(us)\nAI_CORE,42.0\n",               # 无名列
            "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n",  # 有名列、无 expected
            "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n",  # 有名列、有 expected
            "",                                                          # 解析失败
        ]
        for i, csv in enumerate(cases):
            exp = "k950" if i == 2 else None
            g = P.profile_hit_gate(csv, expected_kernel=exp)
            self.assertFalse(g["hit"] and g["pending"],
                             f"case {i}: hit 与 pending 不得并存 → {g}")

    def test_exact_expected_kernel_hits(self):
        """#9 expected_kernel 精确命中 → hit=True、pending=False。"""
        csv = "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n"
        g = P.profile_hit_gate(csv, expected_kernel="k950")
        self.assertTrue(g["hit"])
        self.assertFalse(g["pending"])
        self.assertEqual(g["matched"], ["k950"])

    def test_prefix_expected_kernel_does_not_hit(self):
        """#9 前缀 expected_kernel 不再误命中（k950 ≠ 'k9'）。"""
        csv = "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n"
        g = P.profile_hit_gate(csv, expected_kernel="k9")
        self.assertFalse(g["hit"])

    def test_symbol_unknown_pending_not_hit(self):
        """#8 有名列但符号未预知（expected None）→ 回填名、pending=True、hit=False。"""
        csv = "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n"
        g = P.profile_hit_gate(csv)
        self.assertFalse(g["hit"])
        self.assertTrue(g["pending"])
        self.assertIn("k950", g["observed_kernels"])

    def test_expected_regex_controlled_hit(self):
        """#9 受控 regex 命中门：fullmatch。"""
        csv = "Op Name,Task Type,Task Duration(us)\nk950,AI_CORE,42.0\n"
        g = P.profile_hit_gate(csv, kernel_regex=r"k9\d+")
        self.assertTrue(g["hit"])
        self.assertFalse(g["pending"])

    def test_parse_fail_not_hit(self):
        """解析失败（e2e 口径）→ hit=False、pending=False。"""
        csv = "Op Name,H2D(us),Task Duration(us)\nk,5.0,42.0\n"
        g = P.profile_hit_gate(csv)
        self.assertFalse(g["hit"])
        self.assertFalse(g["pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
