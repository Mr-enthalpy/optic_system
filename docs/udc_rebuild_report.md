# UDC 本地主线重建执行报告

日期：2026-10-11。工作已在独立本地分支完成；没有执行真实硬件或科研机制实验。

## Git 与工作区

- 分支：`udc-main`。
- 起点：`19bdbe7242ea036d38753e1d91653aa036b97798`（PR #59）。
- 工作区：`C:/Users/hanni/PycharmProjects/optic_system-udc`，独立 Git worktree。
- 原 `master` 保持 `93d06ad`，其未跟踪 `outputs/` 和实验数据未修改。只 fetch 了 origin；检查到远端最新 `1bea821`（#98）。
- 未 reset、强制推送、修改默认分支、删除远端分支/tag 或创建 Meta Repository。
- 全部实现按文件/功能选择性恢复，没有整条 PR cherry-pick。该报告中的实际路径仅是交付记录，不是运行配置。

## 历史资产的复用

| 来源 | 恢复内容与整理 |
|---|---|
| #59 / `19bdbe7` | 保留 camera sidecar、共享内存、preview、LCD/TLS、SessionController、raw capture、file-only monitor、CameraProfile/PupilProfile 基础及无硬件测试 |
| #60 / `3726a72` | 仅恢复 FullFramePSFSurvey 思路及实现；实际选取 `93d06ad` 中已整理的 survey 文件，增加成功/完整状态、局部格式版本及真实元数据复制检查 |
| #66 / `378da02`、#67 / `9ff3f27` | 从 `93d06ad` 选择标定模块，恢复 broadband pass-through、exposure/gain binary search、bar/radius pupil scan、per-band pupil-open calibration 及对应回归；补充实际 LCD 身份/形状、有效像素记录和 wavelength settle |
| #68 / `bd3fc86` | 从 `93d06ad` 分离 sensor energy center；只依赖 survey/coordinate 小工具，保留背景、能量、fallback、逐条中心统计，记录明确像素排除 |
| #71–#79 | 选择显式 IlluminationSpec、frame extent、runtime policy 和当前字段契约；不引入已删除的 numeric wavelength/average_burst/raw-fallback 路径 |
| #90/#91 后期有效像素工作 | 只从 origin/master 恢复纯数组 nearest-valid 坏点校正 primitive；保留原 exposure valid-mask 算法并强化 boolean/shape 检查，不移植 digest/provenance 框架 |
| #98 / `1bea821`（`2b4c1e4`、`ec4e402`） | 恢复 RawCapture row commit integrity、schedule/shape/shared wavelength metadata immutability、完成/失败事实分离和回归；版本号局部常量 v3，无中央 adapter/version registry |

## 删除与拒绝继承

彻底删除 `old/` 的 13 个原型文件，以及无消费者的两个 `patterns/` 文件。采集模块/CLI 更名为 `capture_dataset`，没有旧名称兼容入口。PupilProfile 中无人消费的 sensor-center/recommended-ROI 字段被移除，sensor center 与 ROI 配置分开。

后期离散 PSF layout/patch、component/support promotion、adaptive cluster 数据链及其 exporter/handoff/tests 均未引入新分支。也未引入 mask-family adapter、artifact registry/schema adapter/catalog/bundle/graph、storage/digest 框架或 handoff placeholder。RawCapture 不再写 training-readiness 字段。README、AGENTS、architecture、roadmap 和操作文档均按新主线改写，没有保留旧科研阶段安排。

继续保留真实消费者需要的 monitor、device/sidecar 和 profile 回归测试；没有因清理而改写设备控制框架。唯一 LCD 行为修正是物理初始化必须显式选择 display index/axis，避免自动选择桌面显示器。

## 实际目录与数据流

```text
.github/        独立 CI
app/, gui/      手动控制与预览
control/        commands/events/state/session
 devices/       sidecar、frame stream、LCD、TLS
capture/        preview/frame capture
 diagnostics/   logging、file-only status
 tasks/         capture plan、illumination、runtime、raw writer
 tasks/profiles/ 自动曝光与 pupil 标定
 tasks/measurement/ 小型 JSON/HDF5、coordinate、survey frame 工具
 tasks/psf/     survey、sensor center、dense ROI、显式坏点校正
 scripts/       calibration/capture/inspect/ROI/monitor CLI
 plans/         synthetic 与 opt-in hardware smoke 计划
 tests/         无硬件回归及 opt-in hardware 验证入口
 docs/          当前定位、契约、操作与本报告
```

主线是 RawCapture v3 -> FullFramePSFSurvey v1 -> Sensor Energy Center / 显式 sensor ROI -> dense measured ROI PSF -> response analysis。原始和 full-frame 文件不被 ROI 覆盖。ROI 使用半开 `(x,y,width,height)`，数组 `[H,W]`，大小由实验指定；按原 capture-plan wavelength/display index 选择。返回真实二维强度和来源、显示状态、照明、相机 requested/readback、LCD、extent、ROI/中心来源元数据。归一化、曝光缩放及坏点校正仅作用于显式分析副本；可选 CLI 只保存一条 NPZ，不建立 ROI dictionary hierarchy。

逐波长 capture 现在能实际消费 CameraProfile/PupilProfile，检查依赖和物理 pupil/LCD 身份，应用每波长曝光/gain并保存 readback。writer/survey/center/ROI CLI 拒绝覆盖已有测量。失败或不完整 RawCapture、错误索引/shape/extent、未知格式及越界 ROI 均拒绝。

## Submodule 准备与验收

新 worktree 使用自己的 `.venv`（Python 3.13.5，`include-system-site-packages=false`），依赖从本仓库 requirements 安装。去掉源码中的个人 driver checkout 路径；设备/data/private config 通过已有参数/环境处理。CI 支持 `udc-main`，本地 AGENTS 只规定本仓库边界；未来总控计划/跨仓库验收和兼容 commit 组合由 Meta Repository 与 Submodule pins 持有。本轮无 `.gitmodules` 或其他仓库修改。

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

最终无硬件结果：**341 passed, 3 skipped**。覆盖设备/control/monitor、最小采集、profile 序列化/依赖、标定搜索/pupil 流程、RawCapture失败/commit immutability、synthetic RawCapture -> Survey -> ROI、坐标/index/shape和强度保留。

另从最终 staged Git tree `1a0e0a796229ba0ee805a0562fadd774d54b22ce` 生成独立源码快照，在无相邻仓库的 `standalone-udc-final` 路径，用本仓库环境再次执行完整 pytest：**341 passed, 3 skipped**。独立路径 CLI `capture_dataset --dry-run` 与 `inspect_raw_capture` 成功，2/2 synthetic rows 完成且科学有效性标记保持 false。三个新 CLI 的 `--help` 加载成功；Git diff whitespace 检查通过，源码/脚本/测试没有旧专用 PSF 架构或兄弟仓库内部依赖。

真实 camera/LCD/TLS 测试未执行。已有 opt-in smoke 入口与新 calibration CLI 可供下一轮硬件验收，fake backend 结果不能替代它。

## 下一轮真实实验的边界

没有发现阻止独立维护或 Submodule 使用的代码级阻碍。真实实验仍需安装/验证 vendor packages、选择实际 LCD、确认 full-sensor extent/bit depth、验证曝光/gain/TLS/LCD settling和有效像素，再完成固定显示状态的 wavelength-response重复测量。曝光缩放不等于 gain/source power/sensor spectral-response 校正；RGB/Bayer 响应和辐射控制必须由实验验证。当前没有宣称显示层衍射机制、spectral/material sensing 或商用 UDC 迁移已经通过验收。
