# LCD / 真实 UDC 任务管线重设与验证（2026-11-25）

用户明确要求把原 LCD+相机原型的任务链调整为可接入真实 UDC、并保留有实际复用意义的部分。本次实现提交为 **`5d97dc8e1ebbf9d783c38f8f59836e7419b93f98`**，成员分支为 `codex/udc-task-pipeline`。详细职责、操作和限制见[任务管线说明](../task_pipeline.md)。本报告所在的后续文档提交不改变已经部署和测试的源码版本。

## 实现边界

| 处理 | 本次落地 |
|---|---|
| 保留 LCD 特化链 | 原 CapturePlan、物理 mask、PupilProfile、CameraProfile、FlyCapture2 sidecar、TLS/LCD 控制及 RawCapture v3 / Survey v1 保持各自语义 |
| 替换真实 UDC 的任务入口 | `scripts/capture_udc.py`、`tasks/udc/plan.py` 和 `capture.py` 使用既有 Camera2 应用/ADB；无需虚构 pupil、mask、gain-dB 或实验室 RawCapture |
| 复用实测数据操作 | ROI 几何和帧能量中心实现移入 `tasks/measurement/roi.py`、`energy_center.py`；LCD 原有公开导入仍可使用相同实现 |
| 复用已有基础设施 | 原生 RAW 完整性/布局/时间戳/请求回读检查、原始 DN 解码、RunStatusPublisher/Reader；仅新增按已知 run ID 导出能力 |
| 保持平台物理差异 | 手机分析采用 acquired-frame 坐标、记录裁剪奇偶位置；不生成 LCD 的全传感器 Survey 或 SensorEnergyCenterProfile，不把 parity 当作标定 RGB |

每份 UDC 计划只对应一种当前物理条件，允许同条件下有序多曝光和重复参考。更换暗场/照明/波长需另行布置并重新确认。点源计划要求有限正波长、距离和孔径，记录未知带宽；当前任务只记录外部光源，不下发 TLS 控制或动态显示图案。

运行器逐批执行 native capture -> selected-run export -> decode，区分 capture_complete、phone run_succeeded、export_complete 和 decode_complete。请求/返回的格式、曝光、ISO、条件和数量需匹配；已声明保存的 RAW_SENSOR DNG 要实际存在且非空。失败保留手机观测、部分 tar 和状态；已知失败 run 尝试诊断导出，不自动重拍、删首帧、覆盖输出或把诊断数据当成功结果。

`requirements-udc.txt` 仅列手机任务所需 NumPy/Matplotlib；开发测试另需 pytest。UDC 任务导入不要求 HDF5、实验室 SDK、pygame 或 LCD 初始化。没有新增通用后端注册表、跨仓库 RPC、通用调度器、训练或重建实现。

## 本地软件验证

在本成员原有 `.venv`、Python 3.13.5 环境执行完整默认回归，临时目录和 XML 记录位于私有输出目录：

**420 passed, 3 skipped in 17.92 s**。

跳过项为既有 opt-in 硬件检查；本次未执行实验室相机/LCD/TLS 测试。回归包含旧 RawCapture/Survey/Profile/ROI 路径、原生 Android RAW 检查及新增 UDC 任务测试。新增测试覆盖物理确认、禁止输出覆盖、重复参考顺序、无 LCD/pupil/HDF5 依赖、原生完整性/解码、错误控制/帧数、阶段失败保留、传输标识脱敏、ROI 奇偶位置和 acquired-frame 标记。

在本地和实验机运行 `scripts/capture_udc.py --plan plans/udc/dark_example.json --validate-only` 均通过。该入口不连接设备、不生成观测，不被当作模拟硬件成功。

## 既有真机数据的离线重放

使用先前已保存的 `run_1795425851693_75efcfe8`（10 ms、ISO100、新遮挡暗场，10 帧）离线验证新 `load_udc_measurement`：

- 重新核对原始 plane 哈希/字节数/关联并计算全幅均值，与此前 `raw_tools.decode_run` 保存的均值数组**逐像素精确相同**。
- 固定 acquired-frame ROI `(817,613,128,96)` 与原全幅均值的对应切片**逐像素精确相同**，记录 parity origin `[1,1]`。
- 坐标仍标为 `acquired_frame`，`sensor_origin_verified=false`；真实来源仍为原应用采集提交 `360df7df23283636458e7422d2a44b6c6947174f`。

本项是既有数据重读，不是新手机采集、物理原点验证或点源/PSF 证据。未更改原始文件或原有解码输出。

## 实验机独立部署与软件接入检查

将实现提交完整 Git bundle 传到既有实验机，接收方核对 SHA256 `28fa412eb1206968b62b33530d593a423242bd471a2bc92cf3762f58208b5688` 后，在新的 `optic_system-udc-pipeline-5d97dc8` 独立 checkout 建立自己的 `.venv`，用此前已核验的固定 wheels 离线安装。Python 为 3.13.2，依赖检查通过。

在新环境执行 UDC/Camera2 专项：**46 passed in 8.01 s**。这是合成数据和无硬件任务测试，不是完整硬件采集链验收。新入口的计划校验通过；使用现有桌面 ADB，`device.py status` 通过授权连接检查。

部署后逐一核对新目录、原手机目录和旧实验室采集目录的 HEAD 与干净状态。原手机目录仍是 `8fe84c7e3d3c251fe2eae17105659202e6807162`；旧光学采集目录仍是 `18dd598852b1d44ea80b4f6d53e1d2d5656f9e9b`。没有替换旧目录、改变其环境或把新版本冒充历史采集版本。

私有新入口为 `<UDC_ROOT>/local_config/udc-pipeline-shell.cmd`，指向新源码/虚拟环境并复用已配置的 ADB；旧 `axon20-shell.cmd` 保留。执行策略仍为 Restricted，安装通过允许的分步 SSH 命令完成，没有更改策略、系统 PATH 或手机 APK，也没有启动新的物理采集。

## 证据和剩余验证

本地私有 `outputs/udc_pipeline_20261125/` 保存完整回归 XML、离线真机重放 JSON、Git bundle、实验机安装/测试/接入日志与实际执行命令。实验机详细机器路径及日志在私有 `udc-pipeline-20261125` 部署记录中，未提交机器配置、二进制、环境或测量数据。

下一项需要实际物理条件验收：在研究人员确认遮挡/光源/几何后，用新计划执行一次真实 capture/export/decode 并检查故障与重复性。当前只是源码回归、既有观测离线重放和设备授权接入检查；不宣称新版已完成硬件采集验收、曝光辐射标定、独立 PSF 或波长编码验收。

Meta pins 未改，未推送代码或数据。重建成员、冻结模型/测试协议及 LCD 原型科学结论不在本次修改范围。
