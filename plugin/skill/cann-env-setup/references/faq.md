# cann-env-setup 常见问题（FAQ）

在裸机/新服务器上搭 CANN 算子仓基础环境时反复会遇到的坑，按「现象 → 根因 → 解法」整理。
来源：2026-06 在 **Ascend910 新机（CANN 9.0.0-beta.1）** 上对 cann-env-setup 的端到端实测。

---

## 1. `conda create` 失败 —— Anaconda ToS 未接受

**现象**：`conda create -n xxx python=3.x`（甚至 `conda create --clone base`）直接 exit 失败，
打印一段指向 "...working-with-conda/channels"（removing channels）的文档链接，环境没建出来。

**根因**：**ToS = Anaconda Terms of Service（服务条款）**。conda 的默认通道
（`pkgs/main`、`pkgs/r`，即 `repo.anaconda.com`）由 Anaconda 公司维护；2024 起其商业条款要求
营利组织/大规模使用需同意条款。较新版 conda（24.x+，实测 26.x）在**首次用默认通道前强制要求
显式接受 ToS**，否则 `conda create`/`conda install` 直接报错退出。**纯法律/策略闸门，不是网络或技术问题。**

**解法**（任选其一）:
```bash
# A. 接受条款(本地写标记,一次即可)
conda tos accept

# B. 绕开默认通道,用社区免费源 conda-forge(无此 ToS)
conda create -n xxx python=3.11 -c conda-forge --override-channels
```

> cann-env-setup 的 P3 自动建 env 时应内置 A 或 B，否则在别人的新机上会卡这。

---

## 2. 国内 conda / pip 镜像被封，只能走代理

**现象**：tsinghua / aliyun 的 anaconda、conda-forge、PyPI 镜像返回 **403/404**
（实测：aliyun anaconda 404、tsinghua anaconda 与 conda-forge 403、tsinghua PyPI 也失败）。

**根因**：部分机房网络对这些镜像有访问限制；且 Anaconda ToS 导致镜像下架了 `pkgs/main` 通道。

**解法**：回退**反向隧道代理**：
```bash
# conda 走 http_proxy 环境变量
http_proxy=http://127.0.0.1:58231 https_proxy=http://127.0.0.1:58231 conda create ...
# pip 走 --proxy
pip install pkg --proxy http://127.0.0.1:58231
```
或换其它镜像（NJU / BFSU / SJTU）。隧道建法见仓库 CLAUDE.md(`autossh -R 58231:localhost:7897 ...`)。

---

## 3. `npu-smi info` 报 `dcmi module initialize failed. ret is -8005`

**现象**：
```
DrvMngGetConsoleLogLevel failed. (ret=4)
dcmi module initialize failed. ret is -8005
```

**根因**：NPU 设备节点 `/dev/davinci*` 属 `root:HwHiAiUser`、权限 `660`,**当前用户不在 `HwHiAiUser` 组**，
打不开设备。（NPU 与驱动本身正常，纯权限问题）

**解法**：管理员加组 + 用户**重新登录**（组变更需新会话生效）:
```bash
sudo usermod -aG HwHiAiUser <user>   # 管理员执行
# 之后 <user> 退出重新 ssh 登录
```
> 注：cann-env-setup 的冒烟构建**只编译不跑 NPU**，不受此影响；只有真机跑算子（cann-ops-run run）才需要。

---

## 4. 国际网络慢，miniconda installer 下不动

**现象**：从 `repo.anaconda.com` 下 installer 仅 ~几 KB/s（实测 3 KB/s,130MB 要十几小时）。

**解法**：在快网机器（如本地 Mac）下好 installer,`scp` 到服务器**离线安装**
（installer 安装本身不需网络，只解压自带包）:
```bash
# 本地下载对应架构(注意 aarch64 / x86_64)
curl -fSL -o mc.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
scp mc.sh user@server:/tmp/
ssh user@server 'bash /tmp/mc.sh -b -p ~/miniconda3'
```

---

## 5. 共享磁盘接近满

**现象**：根分区 100%、剩余少且在掉（多用户共用一个文件系统，没有独立 /data）。

**解法**：**最小足迹**搭建——
- 浅克隆 `git clone --depth 1 --single-branch --branch <tag>`（ops-cv 浅克隆仅 ~22M）
- 单算子冒烟验证、跑完即清
- 装完删 installer、`conda clean -a` 清缓存
- 依赖只装核心轻量的（pyyaml/numpy/pytest）,tensorflow 这类巨包按需再说

> 一次完整 cann-env-setup 验证（miniconda + 1 env + ops-cv + 1 算子构建）自占仅 ~1.3G。

---

## 6. 算子仓构建缺 `OP_LOGE_FOR_INVALID_*` 符号

**现象**：算子仓在 master 分支编译，撞 `OP_LOGE_FOR_INVALID_*` 等缺符号。

**根因**：master 是中间态（算子代码升级用了 opbase 新宏，但 opbase pin 落后）。

**解法**：**切到与 CANN 版本配套的 tag**（如 CANN 9.0.0 → 仓 tag `v9.0.0`;CANN 9.0.0-beta.1 → `v9.0.0-beta.1`）。
这正是 cann-env-setup P4（`repo_setup.py`）做的事。opbase 由 CANN 自带的 `libnnopbase.so` 经 `find_package` 提供，无需额外下载。
