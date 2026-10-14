# 算子接入样例

参照本目录开发适配模块，与自测共享。将目录及cpp文件改为自己的算子名，
保持solver_adapter.h不变。提交时test下只放算子目录，每个目录包含：

- `<op>_adapter.cpp`：需要实现的薄适配模块。
- solver_adapter.h：固定协议头。
- README.md：该接口的接入说明。

示例调用公开接口aclsolverSgetrf，使用Host行主序float32输入，返回LU矩阵、
1基int32主元ipiv和实际info。其他算子沿用同一协议，替换真实调用与数据转换。
工程另外提供算子源码、公开头和构建入口，构建应产出libops_solver.so。

## 需要实现的内容

| 接口 | 开发者实现 |
| --- | --- |
| descriptor | 声明算子名、dtype、实际Host/Device归属、布局、info形态和公开函数地址 |
| create_v2 | 检查容量，分配资源和workspace；失败时保留可清理的state |
| reset_v2 | 恢复每轮输入、输出及info，完成布局转换和搬运；不调用目标算子 |
| execute | 调用公开接口，分别返回适配状态和未经改写的算子返回码 |
| readback_v2 | 取回实际输出、info及ipiv/tau，不填写预期值 |
| destroy | 释放资源，兼容空state和部分初始化state |

调用方管理handle与stream，适配不销毁它们；readback前由调用方同步stream。
模块不含main、输入生成或判据，自测main可调用同一模块。
验收方独立生成输入，经同一模块进行精度、确定性与msprof op性能检查。
前置分解和输入准备不计入目标kernel耗时。

## 数据约定

- 传输缓冲固定为Host紧凑行主序；实际接口的Device指针、列主序、padding和批量指针数组由适配转换。
- complex64为交错float32实部和虚部。字节数及附加数组count包含整个batch，批量逐矩阵拼接。
- m/n/k、side/trans、leading dimension按实际公开接口使用；未使用的附加数组长度为0。
- getrf输出LU及ipiv[min(m,n)]；getri输入LU及ipiv[n]，输出逆矩阵。
- geqrf输出QR及tau[min(m,n)]；gqr输入反射子及tau[k]，输出Q。
- mqr输入反射子A(nq×k)、tau[k]及C(m×n)，输出同形C；左乘nq=m，右乘nq=n。
- gels输入A(m×n)、B(m×nrhs)，输出X(n×nrhs)；potrs输入因子及B，输出X(n×nrhs)。
- potrf输出因子；potri及matinvbatched输出逆矩阵。
- potrfBatched、getribatched、matinvbatched返回(batch,) int32 info；potrsBatched及非批量返回标量info。

必须核对签名、参数顺序、内存归属、步长和workspace单位。
奇异用例原样调用；QR的info_probe=bad_param按用例约定执行非法参数调用并返回实际info。
本sgetrf示例支持batch=1、lda=n、m≤8192且m×n≤INT32_MAX，其他实现按其自身约束处理。
示例原样回传info；算子没有报告奇异位置时，验收会判对应info用例失败。
