"""规模档 × rank → 具体 shape。与算子无关,所有算子共用一份。

物化脚本每个算子写一份,但这段推导里没有一个字是算子专属的:
规模档定总元素量级,rank 定摊到几根轴,非对齐尾块靠让某一根轴取 2^n-1。
CLAUDE.md §10.6 把它列为「未落地」,代价是每轮重写一遍并重犯同样的错。

不做 shape_form 的字符串分派:它的取值由设计者自己定
(references/case-design.md 的「轴取值与门禁判据」),脚本不认这些名字。
物化脚本把自己的取值映射成这里的参数,映射关系留在算子那一侧。

本模块不是 CLI。
"""

# 规模档只用与实现无关的数量级阶梯(2^n),不按待验收算子实现的 tiling 阈值挑。
SIZE_NUMEL = {"small": 2 ** 10, "medium": 2 ** 16, "large": 2 ** 20}

# 单维不超过 2^20(生态精度标准 §1.2)。
MAX_DIM = 2 ** 20


def shape_for(rank, size_class, index=0, numel=None, ragged=True):
    """把规模档摊到 rank 根轴上。

    `index` 让同一档里的不同 combo 不至于都是同一个形状:轮流让某一根轴
    取 2^n-1,既制造非对齐尾块,也让 dim_values 有足够多的代表值。

    `numel` 传自定义的档位→元素数映射,用于本机 golden 预算更紧的场合。
    `ragged=False` 产出各轴等长的规整形状。
    """
    total = (numel or SIZE_NUMEL)[size_class]
    per = max(1, round(total ** (1.0 / rank)))
    per = min(per, MAX_DIM)
    shape = [per] * rank
    if ragged:
        shape[index % rank] = max(1, per - 1)
    return shape


class ShapeFormDegenerate(ValueError):
    """这个 rank 与规模档下，该形态产不出与别的形态不同的形状。"""


def shape_for_form(rank, size_class, form, index=0, numel=None):
    """`shape_form` 的四个取值 → 四个两两不同的形状。

    `shape_form` 的取值已经钉死成四个（`_axis_binding.PINNED_AXIS_VALUES`），
    所以这份映射不是算子专属的，放在这里只写一遍。

    每个算子自己映射时最常踩的是 `normal`：`shape_for` 默认 `ragged=True`，
    会让某一根轴取 `2^n-1`——那正是 `unaligned_tail` 的定义，两个形态于是撞成
    同一个形状，重复 combo 门禁退回。真机上为这一条返工过一轮。

    `rank` 大而规模档小的时候各轴只摊得到 1，`normal` 与 `single_element` 也会
    撞上；那是结构性的，抛 `ShapeFormDegenerate`，请写进声明的 `infeasible`。
    """
    if form == "single_element":
        return [1] * rank
    base = shape_for(rank, size_class, index=index, numel=numel, ragged=False)
    if base == [1] * rank:
        raise ShapeFormDegenerate(
            f"rank{rank} 摊 {size_class} 档时各轴只有 1，"
            f"{form} 与 single_element 形状相同；写进 infeasible 并给 why")
    if form == "normal":
        return base
    shape = list(base)
    if form == "empty":
        shape[index % rank] = 0
        return shape
    if form == "unaligned_tail":
        shape[index % rank] = base[index % rank] - 1
        return shape
    raise ValueError(
        f"未知的 shape_form {form!r}，只支持 normal / empty / "
        "single_element / unaligned_tail")


def axis_for(rank, position):
    """轴位置名 → 轴号。

    中间轴在 rank < 3 时与首尾重合,这种组合应当在 decl 的 infeasible 里
    排除掉,本函数不替设计做主,照算不报错。
    """
    table = {"first": 0, "middle": rank // 2, "last": rank - 1}
    if position not in table:
        raise ValueError(
            f"未知的轴位置 {position!r},只支持 {sorted(table)}")
    return table[position]
