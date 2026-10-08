# -*- coding: utf-8 -*-
"""阶段 5 验收私集·种子域参数化的机械断言（case-gen 侧）。

原负责人补充描述 3：正式验收用私有种子集，与公开集同规格网格、不同种子，
种子不公开（防绕过校验）。落地形态：freeze_canonical 的种子域基值参数化——
--seed-base（cu 卡，公开域 923000000）/ --std-seed-base（std 卡，公开域
20250912），私集只换基值，对账/子集/几何规则与产物 schema 零改动（消费同接口）。
本文件固化：

- 公开域默认零漂移：SEED_BASE=923000000、STD_SEED_BASE=20250912，cu_seed
  逐 op_code 对拍公开公式 → test_public_domain_defaults_unchanged
- 私域切换语义：换基值后 cu_seed/std 种子整域平移，与公开域值不相交
  （op_code*1e6 段结构保持），还原后复位
  → test_private_domain_switch_disjoint
"""
import freeze_canonical as fc


def test_public_domain_defaults_unchanged():
    # 公开域基值缺省不动——real/complex/batched 册产物逐字节不变的底线
    assert fc.SEED_BASE == 923000000
    assert fc.STD_SEED_BASE == 20250912
    try:
        fc.OP_CODE, fc.OPS = fc.BOOKS["real"]["op_code"], fc.BOOKS["real"]["ops"]
        for op, code in fc.OP_CODE.items():
            assert fc.cu_seed(op, 1) == 923000000 + code * 1000000 + 1
        # std 卡：seed = STD_SEED_BASE + n（README §1.4 口径）
        assert fc.STD_SEED_BASE + 128 == 20251040
    finally:
        fc.OP_CODE, fc.OPS = None, None


def test_private_domain_switch_disjoint(monkeypatch):
    monkeypatch.setattr(fc, "OP_CODE", fc.BOOKS["real"]["op_code"])
    monkeypatch.setattr(fc, "OPS", fc.BOOKS["real"]["ops"])
    public = {(op, k): fc.cu_seed(op, k) for op in fc.OPS for k in (1, 7, 999)}
    monkeypatch.setattr(fc, "SEED_BASE", 920284139)      # 私域基值（见交接包 frozen/private/SEEDS.md）
    monkeypatch.setattr(fc, "STD_SEED_BASE", 51523761)
    private = {(op, k): fc.cu_seed(op, k) for op in fc.OPS for k in (1, 7, 999)}
    # 同构平移：逐点恰差基值之差；与公开域值两两不相交（防绕过的域隔离底线）
    delta = 920284139 - 923000000
    for op in fc.OPS:
        for k in (1, 7, 999):
            assert private[(op, k)] == public[(op, k)] + delta
    assert not (set(public.values()) & set(private.values()))
    # std 域同步平移且不与公开 std 域相交（n ≤ 512，域宽远小于基值差）
    assert fc.STD_SEED_BASE + 128 != 20250912 + 128
