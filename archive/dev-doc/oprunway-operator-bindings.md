# 算子输入绑定的历史记录

本文件记录具体算子在某一轮验收里的一次性输入绑定事实，**是历史留档，不是运行时依据**。

原先它放在 `plugin/skills/acceptance-workflow/reference/` 下随 plugin 分发，2026-08-13 移出。理由：通用
产物里出现具体算子名与上游 URL 不利于泛化；已验收的算子极少回验，这类绑定近乎零复用；而真要回验时，官方
bundle 本来就该由调用方通过 `--task-cases-root` 重新提供——绑定关系的权威是调用方，不是本仓。

移出后，SKILL 步骤 4 的规则收敛为纯粹的形式：调用方给了官方 bundle 就用它作完整分母，没给就跳过该步，
文中不再出现任何算子名。

## GaussianBlur

调用方指定的官方 bundle：

`https://gitcode.com/cann/cann-ops-competitions/tree/master/04_tasks/01_community-task-2026/docs/202607/self_test_case/gaussian_blur/`

三文件 bundle：169 个官方 case 全量跑 accuracy，任务书 S1 作为第 170 个 performance case。其官方用例不含
K13，因此不得自行把 K13 加入本轮分母。
