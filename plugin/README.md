<p align="center">
  <img src="docs/assets/logo.png" width="104" alt="社区算子任务 Skill 仓">
</p>

<h1 align="center">社区算子任务 Skill 仓</h1>

<p align="center">
  <b>十三个装给 AI 编程助手的 skill</b><br>
  <sub>社区算子任务　·　开源仓算子样例跑测　·　社区 issue 处理　·　开源仓质量巡检</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/skills-13-2563EB?style=flat-square&labelColor=1F2937" alt="skills 13">
  <img src="https://img.shields.io/badge/plugin-v0.2.0-7C3AED?style=flat-square&labelColor=1F2937" alt="plugin v0.2.0">
  <a href="https://claude.com/claude-code"><img src="https://img.shields.io/badge/Claude_Code-plugin-D97706?style=flat-square&labelColor=1F2937" alt="Claude Code plugin"></a>
  <img src="https://img.shields.io/badge/python-3.8+-059669?style=flat-square&labelColor=1F2937" alt="python 3.8+">
  <a href="https://www.hiascend.com/cann"><img src="https://img.shields.io/badge/CANN-Ascend_NPU-DB2777?style=flat-square&labelColor=1F2937" alt="CANN Ascend NPU"></a>
</p>

<p align="center">
  <a href="#-能力清单"><b>能力清单</b></a>　·　
  <a href="#-典型流程"><b>典型流程</b></a>　·　
  <a href="#-安装"><b>安装</b></a>　·　
  <a href="#-怎么用"><b>怎么用</b></a>　·　
  <a href="#-文档"><b>文档</b></a>　·　
  <a href="#-参与开发"><b>参与开发</b></a>
</p>

> 💡 这些 skill 是装给 Claude Code 这类 AI 编程助手用的，**不是人直接敲的命令行工具**。
> 装好之后用平常说话的方式提要求，助手自己照 skill 里的流程走。

---

<a id="-能力清单"></a>

## 🧭 能力清单

<table>
<thead>
<tr>
  <th align="center">分类</th>
  <th align="left">skill</th>
  <th align="left">能力域</th>
  <th align="center">NPU</th>
</tr>
</thead>
<tbody>

<tr>
  <td rowspan="5" align="center" valign="middle">
    <h3>🧾&nbsp;社区任务<br>辅助</h3>
    <sub>产出算子任务书、测试用例与验收结论</sub>
  </td>
  <td><a href="skill/repo-task-doc-write/"><code>repo-task-doc-write</code></a></td>
  <td>把需求整理成可据以验收的算子任务书，逐轮拍板定死约束<br><sub>工程模式：kernel 直调 · aclnn 工程化 · torch 接口</sub><br><sub>产出：任务书 · 拍板存档 · 门禁封条</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/repo-task-case-gen/"><code>repo-task-case-gen</code></a></td>
  <td>按任务书出 ATK 用例，并在 CPU 上冻结标准答案<br><sub>调用剖面：aclnn 接口 · torch 算子｜用例形态：真张量声明 · 自带件采纳</sub><br><sub>按规模档分层出用例，产出跑测侧直接可用的用例包</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/repo-task-atk-accept/"><code>repo-task-atk-accept</code></a></td>
  <td>在 NPU 上编译部署算子工程，跑精度与性能，出验收结论<br><sub>算子形态：aclnn 接口 · torch 算子注册（sparse 仓）｜工程形态：母仓算子 · 自足工程</sub><br><sub>用例来源：生成侧用例包 · 任务书自带跑测件｜精度基线：torch · CANN 内置实现 · 自带件当场算</sub><br><sub>性能基线：CANN 内置实现 · 跨 dtype 对照 · 任务书绝对指标（含外部量测）</sub></td>
  <td align="center">✅</td>
</tr>
<tr>
  <td><a href="skill/repo-task-blas-case-gen/"><code>repo-task-blas-case-gen</code></a></td>
  <td>按任务书出 CSV 驱动的 GTest 用例与配套自测脚本<br><sub>算子域：ops-blas（aclblas）· ops-sparse（aclsparse）</sub><br><sub>产出：题库 CSV · 自测脚本</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/repo-task-blas-accept/"><code>repo-task-blas-accept</code></a></td>
  <td>拿任务包在 NPU 上跑精度与性能，出证据化验收结论<br><sub>算子域：ops-blas · ops-sparse，按 include 入口头探测</sub><br><sub>输入：<code>&lt;op&gt;_test.csv</code> 加一张 GPU 基线 CSV</sub></td>
  <td align="center">✅</td>
</tr>

<tr>
  <td rowspan="3" align="center" valign="middle">
    <h3>🔧&nbsp;开源仓算子<br>样例跑测</h3>
    <sub>验证开源仓算子在真机上能否跑通</sub>
  </td>
  <td><a href="skill/cann-env-setup/"><code>cann-env-setup</code></a></td>
  <td>把一台空机器装成能编译、能跑算子的环境<br><sub>source toolkit · 系统构建依赖 · 仓 tag 对齐 CANN 版本 · conda 依赖 · 单算子冒烟</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/cann-950-feature-scan/"><code>cann-950-feature-scan</code></a></td>
  <td>扫出依赖 Ascend 950 硬件特性的算子，出重点测试清单<br><sub>特性：simt · hif8 · RegBase｜适用于任意 CANN ops 仓</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/cann-ops-run/"><code>cann-ops-run</code></a></td>
  <td>在真机上 build → install → 跑 examples，出跑测台账<br><sub>粒度：单算子 · 单仓全量 · 多仓并发｜含续跑、重测与失败诊断</sub></td>
  <td align="center">✅</td>
</tr>

<tr>
  <td rowspan="2" align="center" valign="middle">
    <h3>💬&nbsp;社区 issue<br>处理</h3>
    <sub>向上游社区上报问题并跟踪至闭环</sub>
  </td>
  <td><a href="skill/cann-issue-report/"><code>cann-issue-report</code></a></td>
  <td>把失败算子整理成上游能受理的 issue 草稿，过目后再提交<br><sub>平台：GitHub · Gitee · GitCode｜半自动提交 · 跨轮去重</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/cann-issue-track/"><code>cann-issue-track</code></a></td>
  <td>跟踪已提的 issue 到闭环<br><sub>查回复 · 读懂修复方案 · 应用并复测 · 通过则关闭并写入 FAQ</sub></td>
  <td align="center">按需</td>
</tr>

<tr>
  <td rowspan="3" align="center" valign="middle">
    <h3>🩺&nbsp;开源仓<br>质量巡检</h3>
    <sub>扫描开源仓文档与社区页面缺陷</sub>
  </td>
  <td><a href="skill/cann-doc-quickstart-check/"><code>cann-doc-quickstart-check</code></a></td>
  <td>只按快速入门文档操作，看新手能否真的跑通<br><sub>禁一切文档以外的探索、绕过与 workaround｜只覆盖 quickstart</sub></td>
  <td align="center">✅</td>
</tr>
<tr>
  <td><a href="skill/cann-doc-tutorial-review/"><code>cann-doc-tutorial-review</code></a></td>
  <td>对照代码静态查证进阶教程与开发指南<br><sub>判据：漏讲 · 讲不清 · 过时 · 对不上代码 · 概念讲错｜每条带证据与确定度</sub></td>
  <td align="center">—</td>
</tr>
<tr>
  <td><a href="skill/cann-page-inspect/"><code>cann-page-inspect</code></a></td>
  <td>巡检昇腾社区 CANN 页面的链接与内容有效性<br><sub>19 个顶部页签含需点开的子页签｜七条判定规则｜产出单文件 HTML 报告</sub></td>
  <td align="center">—</td>
</tr>

</tbody>
</table>

<sub>✅ 要在有 NPU 的机器上跑　　按需 复测算子类问题才要卡　　— 普通机器就行</sub>

---

<a id="-典型流程"></a>

## 🔗 典型流程

四类各自独立，想用哪类用哪类。类里的 skill 也能单独用——上一个的产物落成文件，
下一个读那份文件就行，**不要求你按顺序跑完**。

**社区算子任务** —— 接了一个社区算子开发任务，从需求到验收结论

```text
repo-task-doc-write      把需求写成一份可验收的任务书
repo-task-case-gen       照任务书出测试用例，先在 CPU 上算好标准答案
repo-task-atk-accept     拿算子工程 + 用例，在 NPU 上跑精度与性能，出结论
```

BLAS 类算子把后两个换成 `repo-task-blas-case-gen` 与 `repo-task-blas-accept`。
任务书自带跑测件时（sparse 系列如此），直接用 `repo-task-atk-accept`，不必先出用例。

**开源仓算子样例跑测** —— 拿到一个算子开源仓，从空机器到一张跑测台账

```text
cann-env-setup           把机器装成能编译、能跑算子
cann-950-feature-scan    扫出用到 Ascend 950 新特性、要重点测的算子
cann-ops-run             逐个跑样例，记下通过还是失败
```

**社区 issue 处理** —— 手上有一批要反馈给上游的问题

```text
cann-issue-report        整理成社区能受理的 issue 草稿，你过目后提交
cann-issue-track         跟进回复，社区给了方案就照着改、复测，通过就关掉
```

**开源仓质量巡检** —— 三个各自独立，想查哪样跑哪样

```text
cann-doc-quickstart-check  照快速入门文档跑一遍，看新手能不能上手
cann-doc-tutorial-review   对着代码查进阶教程有没有讲错、漏讲、过时
cann-page-inspect          点一遍昇腾社区 CANN 页面，找坏链和过期内容
```

---

<a id="-安装"></a>

## 📦 安装

把本仓当 Claude Code plugin 加载即可，`.claude-plugin/plugin.json` 已登记全部 13 个
skill，一起生效，不用逐个拷。

<details>
<summary><b>🔗 只想装其中几个？三种拷法</b></summary>

<br>

`<name>` 换成上表任一名字，要几个就各装一次。

```bash
# ① 软链接：跟着本仓更新，路径必须写绝对
ln -s "$PWD/skill/<name>" ~/.claude/skills/<name>

# ② 用户目录：所有项目可用
cp -r skill/<name> ~/.claude/skills/

# ③ 项目目录：只对该项目生效，可随项目一起提交
cp -r skill/<name> <你的项目>/.claude/skills/
```

</details>

大多数 skill 装完就能用，只依赖 python3 标准库。三处例外：

| skill | 还要装 |
|:--|:--|
| `repo-task-case-gen` `repo-task-atk-accept` | ATK（走 submodule 自行构建，**不要 `pip install atk`**） |
| `cann-page-inspect` | `httpx` + `playwright` + 本机 Google Chrome |
| `cann-950-feature-scan` | `jinja2`（缺了会在第一步自动装） |

完整步骤与各 skill 对 NPU、CANN、torch 的要求 → **[安装与依赖](docs/install.md)**

---

<a id="-怎么用"></a>

## 💬 怎么用

装好之后直接说要什么，助手会自己挑 skill。把已知条件在第一条消息里给全，能省几轮问答：

```text
用 repo-task-doc-write 写 Median 算子的任务书。
工程模式：aclnn算子工程化开发
对标基线：torch.median
输出到：/home/user/task/
```

不点名 skill 也行——「帮我看看昇腾社区 CANN 的页面今天有没有坏链」一样会触发。

每一类怎么用、助手会停下来问你什么、你拿到什么，见下面四份使用指导。

---

<a id="-文档"></a>

## 📚 文档

**使用指导**（不含命令，只讲你说什么、它问你什么、你拿到什么）

| 指导 | 覆盖 |
|:--|:--|
| [社区算子任务](docs/guide/community-task.md) | 任务书 → 用例 → NPU 验收结论，含 BLAS 变体 |
| [开源仓算子样例跑测](docs/guide/ops-sample-run.md) | 搭环境 → 挑算子 → 跑样例台账 |
| [社区 issue 处理](docs/guide/issue-workflow.md) | 起草上报 → 跟踪回复 → 应用方案复测 |
| [开源仓质量巡检](docs/guide/quality-inspect.md) | 快速入门体检 · 教程查证 · 社区页面巡检 |

**其他**

| 想知道 | 去读 |
|:--|:--|
| 🔧 装不上，或缺什么依赖 | [安装与依赖](docs/install.md) |
| 📖 某个 skill 的完整运行规则 | 上表点开对应目录的 `SKILL.md` |
| 🗂️ 仓里的文档都放在哪 | [文档地图](docs/README.md) |

---

<a id="-参与开发"></a>

## 🛠️ 参与开发

**本仓的 skill 就是用 Claude Code 这类 agent 工具开发的，规范会自动加载。**
把仓 clone 下来用 agent 打开，`CLAUDE.md` 与行文规范自动进上下文；动手改
`SKILL.md` 那一刻还有个钩子把开发流程注回来。**所以不用先读一堆文档，直接说要做什么。**

| 你想做 | 从哪开始 |
|:--|:--|
| 改一个已有 skill | 先看它的 `skill/<name>/SKILL.md`；有 `CLAUDE.md` 的先读那份——红线与真机事实在里面 |
| 加一个新 skill | [CLAUDE.md](CLAUDE.md) 的「新增一个 skill」四步。**漏掉登记那步会静默装不上**，没有任何报错 |
| 想知道 `SKILL.md` 该怎么写 | [skill 行文规范](.claude/rules/skill-style.md)，末尾有自检清单 |
| 想知道当初为什么这么定 | [架构演进记录](docs/development/architecture-log.md) |

三条硬要求：

- **改动要在真机上跑通才算数**，单元测试通过不算。仓里的测试只覆盖不碰 NPU 的纯逻辑
  （文档解析、平台推断、报告渲染），碰卡那部分一条都没测，也不该补
- 提交前在仓根跑一次 `python3 -m pytest`
- **一条链路上两侧的改动要同时做、两侧都在真机上复跑**——它们靠产物文件握手，
  只改一侧会静默失配
