# Axon 20 实验机最小开发环境部署（2026-11-25）

用户将手机移至既有 Windows 实验机后，明确要求部署必要开发环境。本次为手机 RAW 工具建立独立源码和 Python 环境，准备 ADB、数据目录、使用入口并执行无硬件验证。此为软件部署记录，不是新的照明/PSF 采集或科学验收。

## 范围和版本

- 新部署源码为 `8fe84c7e3d3c251fe2eae17105659202e6807162`，通过完整 Git bundle 传输并独立 clone，在成员内部创建 `codex/axon20-remote-20261125` 分支。origin 记录为本成员仓库；部署后源码工作区干净。不是把源文件复制进另一个仓库。
- 复用实验机已有 Python **3.13.2 x64** 基础解释器，新建本次 checkout 的独立 `.venv`。原先 PATH 中的 Anaconda/Python 3.8 不参与此次工具运行，没有修改其安装。
- ADB 为 **37.0.1-15733141 / protocol 1.0.41**，从此前已用于手机实验的 Windows Platform Tools 单独部署。
- 主要依赖为 NumPy **2.5.4**、Matplotlib **3.11.2**、pytest **9.1.1**；16 个运行/测试及传递依赖全部固定版本，下载 wheels 后离线安装到新 `.venv`，没有复制开发机虚拟环境。
- 此次最小环境覆盖 Python 端开发/测试、既有手机应用控制、导出、RAW 解码和诊断。不包含 APK 重新构建所需 JDK/Android SDK，不重签或更新既有 APK；若以后修改 Java 应用，须按工具 README 的构建依赖及原签名连续性另行准备。
- 旧实验室采集 checkout 仍为 `18dd598852b1d44ea80b4f6d53e1d2d5656f9e9b`，部署前后 HEAD 和干净状态一致，其 `.venv`、相机/LCD/TLS 环境及测量目录未改动。旧目录名称中的历史短 SHA 不被当作实际 HEAD。

本次手机部署源版本和旧实验室采集版本分别记录，不把手机部署当作 Meta gitlink 准入或旧实验重新验收。此前手机数据的实际应用源码仍须从采集记录读取，不能换成此次文档所在提交。

## 可复现部署与系统边界

1. 对当前源码提交生成完整历史 Git bundle，验证其可用；打包固定 ADB 和 Python wheels。
2. 传输 5 个输入，共 42,648,977 bytes。接收方在解包/安装前逐一核对文件大小和 SHA256，包括源码 bundle、ADB zip、wheels zip、版本锁和计划脚本；私有 `transfer_manifest.json` 留在部署证据中。
3. 在新目录独立 clone、创建开发分支和 `.venv`，用 `pip --no-index --find-links ...` 安装固定 wheels；没有从未记录的远端索引临时选择版本。
4. 实验机 PowerShell **5.1** 的现有执行策略为 **Restricted**。直接执行 `.ps1` 被阻止后，改用 SSH 逐步提交允许的安装/哈希检查命令，没有更改系统执行策略或使用 ExecutionPolicy Bypass。
5. 为桌面会话创建私有 `.cmd` 入口，设置当前会话的 Python/ADB 路径并启动本地 ADB，然后打开交互 PowerShell。没有修改系统 PATH、全局 Python、登录启动项或通用调度服务。

机器路径、网络地址、用户配置、私钥、Python wheels、ADB 二进制和部署输出均留在 Git 外。本机私有部署证据为 `outputs/axon20_remote_deploy_20261125/`。实验机路径以其私有 `<UDC_ROOT>` 为准：

| 内容 | 实验机私有位置 |
|---|---|
| 源码与 `.venv` | `<UDC_ROOT>/deploy/optic_system-axon20-8fe84c7/` |
| ADB | `<UDC_ROOT>/tools/android-platform-tools-37.0.1/platform-tools/` |
| 固定输入与版本锁 | `<UDC_ROOT>/incoming/axon20-env-20261125/` |
| 部署和测试日志 | `<UDC_ROOT>/logs/axon20-env-20261125/` |
| 桌面使用入口 | `<UDC_ROOT>/local_config/axon20-shell.cmd` |
| 私有使用说明 | `<UDC_ROOT>/local_config/AXON20_ENV_20261125.txt` |

新手机数据目录单独配置在实验机的数据盘，位于源码树之外。实际绝对路径和解释器位置记录在私有 verification JSON 与使用说明中；不在源码中硬编码，也不移动既有手机数据。

## 实际验证

- Git bundle 接收/clone 成功，新源码 HEAD 精确匹配上述部署提交，成员分支明确、源码干净。
- 新 `.venv` 使用实验机 Python 3.13.2；固定依赖安装成功，`pip check` 返回 **No broken requirements found**。
- 新 ADB 可执行文件版本回读成功；RAW 工具 CLI 导入和 `--help` 成功。
- 在新 checkout 的新 `.venv` 执行 `python -m pytest -q tests/test_android_raw_probe.py`，日志/临时目录显式位于私有部署日志区：**26 passed in 8.44 s**。包含合成 RAW 布局/完整性/解码、元数据关联、曝光描述性诊断和失败语义测试；不是手机实测数据。
- 未重跑全仓库测试，没有执行实验室相机/LCD/TLS 测试或任何新手机采集。没有据软件测试声明辐射线性、未处理传感器输出、衍射或波长依赖。

## ADB 会话与手机接入

初始 ADB 能看到 USB 手机，但返回 unauthorized/authorizing。用户确认授权后仍未立即形成可复用连接。进一步观察：同一 SSH 会话内 ADB 进程存在并至少保持 3 s；该会话结束后，下一次 SSH 会话开始时 ADB 进程数为 0。不能把一次短 SSH 命令启动的 ADB 当作持久后台服务。

因此桌面入口在实验机实际交互会话内启动 ADB，让 SSH 客户端复用其本地连接；不自动修改或复用原光学实验的调度任务，也没有删除/重建 ADB 私钥、撤销手机既有授权或清除应用数据。

用户从实验机桌面启动入口并确认“始终允许 USB 调试”后，实际检查通过：

- ADB 连接为 authorized，型号回读 ZTE A2121；既有 RAW Probe 为 versionName=0.2、versionCode=2。
- `run-as` 可读取应用私有 captures 目录，观察到 **19 个既有批次**。只核对目录和最新运行元数据，没有把它们重新导出或重新核验为新数据。
- 最新保留批次为 `run_1795425864887_f6507f76`，状态 completed、10 帧，实际应用源码仍为 `360df7df23283636458e7422d2a44b6c6947174f`、source_dirty=false，应用源码 SHA256 为 `766a90cca2b5f9a31168a7e20ec96796382ffe077c669e1a51bfb9f4c73f550c`，与此前曝光响应报告一致。
- 结束上述 SSH 检查后，再建立新的 SSH 连接，观察到同一个 ADB 进程仍在桌面 session 1 运行；新连接中的 `device.py status` 再次通过授权连接检查。执行策略仍为 Restricted。

因此已完成“新宿主环境 + 桌面 ADB + SSH 客户端 + 既有手机应用私有文件访问”的接入 smoke check。没有采集新图像、更新 APK 或删除应用数据；也没有验证注销桌面、重启或断电后的自动恢复。使用时保留该桌面环境入口窗口，重启后重新启动入口并检查手机状态。

## 证据及剩余范围

私有记录包括 bundle 和输入 SHA256、固定版本锁、分步安装命令及日志、`pip-check.txt`、`pytest.txt`、安装版本清单、ADB 生命周期观察和逐次 verification JSON。失败的初始命令/执行策略检查及未授权检查均保留，没有改写成成功。

可在本地桌面入口中运行 `python tools/android_raw_probe/device.py --adb $env:UDC_PHONE_ADB status` 检查连接，后续物理采集仍按[工具操作说明](../../tools/android_raw_probe/README.md)逐次确认真实条件。既有[曝光响应报告](2026-11-23-axon20-exposure-response.md)的空间不均匀、首帧偏高和科学限制保持成立；点光源光路尚待研究人员布置与记录。

此次仅部署独立手机工具环境及记录验证，不更新 Meta pins，不推送数据或提交。手机应用与原始观测保留。
