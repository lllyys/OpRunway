# 环境、部署与执行门禁

## 目录

- 无效用例
- 部署门禁
- 中止

## 无效用例

只剔除原始错误明确表明参数或 attr 无法形成调用的用例。

环境、绑定、合法输入错误和原因不明的失败不得剔除。

记录 `id`、`stage`、`cause`、原始 `reason` 和本轮日志路径。

运行期发现无效用例时加入黑名单，不改用例 JSON，不重新运行 `atk case`。

## 部署门禁

只用公开构建方式，并为本轮生成新的 `.run` 包。

安装本轮包，加载其 `set_env.bash`，再运行 `check_soc_binding.py`，落盘 `-o evidence/soc_binding.json`。

pyaclnn 必须指向本轮 vendor 内准确的 `.so`：

```bash
export ATK_CUSTOM_OPP_PATH=<absolute-candidate-library>
```

预检和冒烟后都运行 `check_opapi_binding.py`。

冒烟启用 `--cpp_func_signature_type_path`。

标准绑定不匹配时，按 [plugin-authoring.md] 的运行时契约提供最小适配器；不得用猜测修正。

ATK CLI 退出码为 0 不代表冒烟通过。

必须确认报告成功数为 1、失败数为 0、精度结果存在。

加载路径必须属于本轮安装的 vendor。

## 中止

标准构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败时停止。

保留命令、退出码、日志和已绑定用例规格。

不要修改待验收算子源码。

不要阅读待验收算子实现来补充失败原因——归因结论只能落在可观测证据上。
接口声明、README 和构建脚本不在此列，读它们排查绑定和构建问题是正常的。

可以读 ATK 源码理解失败机制，但结论必须落在可观测证据上，不能只引源码。
