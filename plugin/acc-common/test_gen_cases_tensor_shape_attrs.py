#!/usr/bin/env python3
"""N7 生产接缝：rank0 / atomic attrs / cyclic axes / layout 的确定性测试。"""

import copy
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import gen_cases as GC
import tensor_shape_attrs as TSA


class Rank0PlannerSeamTest(unittest.TestCase):
    def test_rank0_empty_singleton_have_distinct_tags_and_classes(self):
        self.assertEqual(GC._shape_tag(()), "rank0")
        self.assertEqual(GC._shape_tag((0,)), "0")
        self.assertEqual(GC._shape_tag((1,)), "1")
        self.assertEqual(GC._shape_class(()), GC.SHAPE_CLASS_RANK0)
        self.assertEqual(GC._shape_class((0,)), GC.SHAPE_CLASS_EMPTY)
        self.assertEqual(GC._shape_class((1,)), GC.SHAPE_CLASS_ALL_UNIT)

    def test_explicit_rank_zero_to_eight_has_a_witness_per_rank(self):
        ranks = GC._allowed_ranks([{
            "name": "x", "io": "in", "rank": list(range(9)),
        }])
        self.assertEqual(ranks, frozenset(range(9)))
        regular, _large = GC._shape_ladder(ranks)
        witnessed = {len(shape) for shape in regular}
        self.assertEqual(witnessed, set(range(9)))
        self.assertIn((), regular)

    def test_unconstrained_legacy_shape_ladder_does_not_gain_rank0(self):
        regular, _large = GC._shape_ladder(None)
        self.assertNotIn((), regular)
        self.assertNotIn(0, {len(shape) for shape in regular})


if __name__ == "__main__":
    unittest.main()
