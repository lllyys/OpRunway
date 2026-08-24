"""生成器插件模板：让 ATK 逐条产出 must_cover 里的组合，而不是随机采样。

没有它，ATK 按 YAML 的 dtype × shape 分布自己随机组合，生成的用例集和
必测集对不上——`validate_cases.py` 的 C3/C5 会报大面积缺口。

复制这份文件，改三处：
1. 装饰器里的注册名，必须和 YAML 的 `generate` 字段一字不差
2. `TABLE_PATH` 指向本算子物化后的 must_cover
3. `generate()` 里覆写哪几个输入——按 decl.json 的 `parameters` 顺序对号入座

放在 case 文件所在目录，ATK 才会自动加载（只认 `function_*.py` 和
YAML 显式声明的 `generate` 注册名）。
"""

import json
from pathlib import Path

from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY


# 必须在 import 阶段读完：插件目录只在 import 期间临时进 sys.path，
# 延迟到调用时再读会找不到文件。
TABLE_PATH = Path(__file__).with_name("example_materialized.json")
TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))["combos"]


def rebuild_group(case, index, values, dtype):
    """按本条 combo 的实际长度，整组重建一个复合输入。

    组长度来自 YAML 的 tuple_numbers，ATK 每条用例从里面随机抽一个，
    抽完还会洗牌，与 combo 的顺序没有对应关系。 # [L0:group_length.follows_combo_order=False]
    所以逐个改 `case.inputs[i][j].range_values` 只在长度恰好相等时才对；
    per-combo 长度会变的复合组，必须像这样整组重建。 # [L0:group_length.runtime_type=list[InputCaseConfig]]

    组长度下限是 1，空组表达不出来——缺省参数用显式 default token 表示，
    不要拿空组去表达「这个参数这条用例不传」。 # [L0:group_min_length=1]

    本例的算子没有复合组输入，所以这个函数在下面的 generate() 里没有被调用；
    有复合组的算子照抄这一段，把 index 换成该输入在 inputs 里的序号。
    """
    template = case.inputs[index][0]
    case.inputs[index] = [
        template.model_copy(update={"range_values": value, "dtype": dtype})
        for value in values
    ]
    return case


@GENERATOR_REGISTRY.register("example_constraint")
class ExampleGenerator(CaseGenerator):
    MUST_COVER = TABLE

    def _get_case_numbers(self):
        # 用例数就是必测集条数。YAML 里配套写 extra_numbers: 0，
        # 否则 ATK 会在枚举之外再补随机边界用例，覆盖分母就对不上了。
        return len(self.MUST_COVER)

    def generate(self):
        case = super().generate()
        if self.is_gen_extra:
            return case
        combo = self.MUST_COVER[self.index - 1]

        # CaseConfig 可覆写：name、aclnn_name、standard、expected_error_msg
        # InputCaseConfig 可覆写：name、type、dtype、shape、range_values
        # 复合组在运行期是 list[InputCaseConfig]，组长度下限为 1。 # [L0:group_min_length=1]
        case.inputs[0].dtype = combo["dtype"]
        case.inputs[0].shape = combo["shape"]
        case.inputs[1].range_values = combo["dim"]
        case.inputs[2].range_values = combo["keepdim"]

        # 定向用例带 expected_error_msg 时在这里挂上。
        # 预期文本只能来自任务书或基线生态语义，不能抄待验收算子实现的报错串——
        # 那等于让实现自证，而且两个分面的文本不一致就会误判成阻塞。
        if combo.get("expected_error_msg"):
            case.expected_error_msg = combo["expected_error_msg"]
        return case
