# optic_system

当前仓库是一个正在进行中的光学实验控制系统重构版本。现阶段重点不是完整实验自动化，而是先把底层设备链路、控制层语义和最小 GUI 原型整理正确。

## 约束入口

项目的统一约束文档在 [AGENTS/AGENTS.md](AGENTS/AGENTS.md)。

如果要继续开发、重构或扩展功能，请优先遵守这份文档。  
根目录的 [AGENTS.md](AGENTS.md) 只是一个简短入口，不再单独维护另一套规则。

## 当前阶段

当前已覆盖的主线是：

- 相机 sidecar 连接或自动拉起
- 相机预配置 GUI -> 打开相机 -> 启动视频流
- GUI 实时预览
- 相机参数显示与调整
- 控制层命令 / 事件 / 状态语义
- 最小 LCD 控制集成
- LCD 默认全透显示
- LCD 调试图案显示

当前还不是完整实验系统。以下内容仍然不在当前主线范围内：

- 完整口径搜索
- 完整标定流程
- 波长扫描工作流
- 同步 LCD-相机采集调度
- 数据导出流水线
- GenerMask / 优化工作流

## 目录结构

```text
AGENTS/     统一工程约束与阶段文档
app/        应用入口与系统装配
capture/    帧消费辅助层
control/    命令 / 事件 / 状态 / 控制器
devices/    相机 sidecar 客户端、帧流、LCD 服务
gui/        预览、参数面板、状态面板、LCD 调试面板
old/        旧实现，仅供参考，禁止修改
patterns/   图案与模式生成相关代码
tasks/      未来任务层预留与阶段性脚本
```

各层依赖方向应保持为：

```text
gui -> control -> devices / capture
```

`old/` 不是目标代码结构，不要在里面做修改。

## 当前关键模块

### 相机链路

- `devices/camera_service.py`
  - sidecar RPC 客户端
  - 自动检测或启动 `devices/camera_service_impl.py`
- `devices/frame_stream.py`
  - SUB + shared memory 帧读取
  - raw8/raw16 Bayer 解码
- `capture/preview_worker.py`
  - 后台持续消费帧并回调 controller
- `control/session_controller.py`
  - 统一处理启动、关机、参数更新、LCD 命令

### LCD 链路

- `devices/lcd_backend.py`
  - pygame / SDL 显示后端
- `devices/lcd_service.py`
  - LCD 服务边界
  - 负责把物理单色掩码 `[H, 3W]` 打包为显示 RGB `[H, W, 3]`
- `devices/lcd_debug_patterns.py`
  - 最小调试图案生成

### GUI

- `gui/main_window.py`
  - 主窗口装配
- `gui/camera_panel.py`
  - 相机参数显示与修改
- `gui/preview_panel.py`
  - 实时预览与帧元数据
- `gui/status_panel.py`
  - 相机 / LCD / sidecar 状态
- `gui/lcd_panel.py`
  - LCD 调试按钮

### TLS 链路

- `devices/tls_service.py`
  - `tls_c1` / `TLSC1` 高层 SDK wrapper
  - lazy import，未安装 `tls_c1` 时不破坏基础 import
  - 统一收口 TLS 连接、目标波长、grating、move 和状态查询
- `control/session_controller.py`
  - 处理 TLS 命令
  - 发布 TLS 事件并更新共享状态

当前 TLS 路径已经替换旧的 `pywinauto` GUI 自动化思路。  
新代码中不要再通过 GUI 自动化控制 TLS，也不要把 `SpectrometerAPI` 直接散落到各处。

## 环境要求

### 主 GUI 环境

主 GUI 需要安装：

- `numpy`
- `opencv-python`
- `pyzmq`
- `Pillow`
- `pygame`

仓库里已有 `requirements.txt`：

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### TLS SDK 依赖

`optic_system` 对 [Mr-enthalpy/tls_c1](https://github.com/Mr-enthalpy/tls_c1) 的依赖是可选依赖，不属于基础 GUI 启动必需项。

约定如下：

- 没有 TLS 硬件、没有 vendor DLL、没有安装 `tls_c1` 时，项目基础 import 和无硬件测试仍应可运行。
- 只有在启用 TLS backend 或运行未来 TLS 硬件 smoke 时，才需要安装 `tls_c1`。
- `optic_system` 内部只允许 `devices/tls_service.py` 依赖 `tls_c1`，并且必须通过 lazy import 和高层 `tls_c1` / `TLSC1` facade 访问。

推荐依赖方式一：直接从 GitHub 安装

```powershell
.\.venv\Scripts\pip.exe install git+https://github.com/Mr-enthalpy/tls_c1.git
```

推荐依赖方式二：先 checkout，再以本地 editable 方式安装

```powershell
git clone https://github.com/Mr-enthalpy/tls_c1.git .\third_party\tls_c1
.\.venv\Scripts\pip.exe install -e .\third_party\tls_c1
```

不推荐把 `tls_c1` 源码直接复制进 `optic_system`，也不要把 vendor DLL 提交进本仓库。

如果上游 `tls_c1` 运行时需要本机 SDK/DLL 路径，按其约定在本机设置 `TLS_C1_SDK_DIR`，不要把 DLL 放入本仓库版本控制。

### 相机 sidecar 环境

`devices/camera_service_impl.py` 是硬件侧 sidecar，当前按旧系统约束，默认优先使用 Python 3.8 环境。

如果你的 `pyflycap2` 只装在单独的 Python 3.8 环境里，建议显式设置：

```powershell
$env:PY38_BIN = "C:\Path\To\Python38\python.exe"
```

## 启动方式

### 默认启动

推荐直接运行：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui
```

默认行为：

1. 检查或自动拉起相机 sidecar
2. 先打开 FlyCapture 预配置 GUI
3. 等待你关闭该预配置 GUI
4. 再执行 `OpenCamera`
5. 启动视频流
6. 初始化 LCD，并默认显示全透图案
7. 打开主 GUI

### 关于预配置 GUI

这是当前项目的明确规则，不是可有可无的兼容分支。

原因是：

- 有些相机必须先在 FlyCapture GUI 中完成配置，后续连接才稳定
- `pyflycap2` 的程序接口少于 GUI 能配置的内容

因此默认启动会先执行 `PreConfigGUI`。  
`show_selection()` 会阻塞，直到你手动关闭该窗口后才继续后续流程。  
这里不应该设置自动关闭，也不应该设置很短的 RPC 超时。

如果你非常确定当前相机不需要这一步，才可以显式跳过：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui --skip-preconfigure
```

### 常用启动参数

仅相机，不初始化 LCD：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui --disable-lcd
```

连接已在运行的 sidecar，而不是自动拉起：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui --no-auto-sidecar
```

指定 LCD 显示器索引：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui --lcd-display-index 1
```

指定 LCD 全透 / 全黑码值：

```powershell
.\.venv\Scripts\python.exe -m app.main_gui --lcd-transmissive-code 255 --lcd-opaque-code 0
```

## 关键环境变量

### `PY38_BIN`

指定相机 sidecar 使用的 Python 3.8 解释器。

### `SIDECAR`

覆盖 sidecar 脚本路径。默认是：

```text
devices/camera_service_impl.py
```

### `CAM_BAYER_PATTERN`

指定 raw8/raw16 Bayer pattern。

当前支持：

- `BG`
- `GB`
- `RG`
- `GR`

例如：

```powershell
$env:CAM_BAYER_PATTERN = "GR"
```

如果你发现 raw 预览颜色中 `R/B` 颠倒，这个变量就是优先检查项。

### `TLS_C1_SERIAL`

TLS 硬件 smoke 和后续集成 smoke 使用的设备序列号。默认单元测试不会读取或要求它。

### `TLS_C1_SDK_DIR`

按 `tls_c1` 上游约定提供 TLS vendor SDK / DLL 所在目录。  
这是 TLS 硬件路径的本机环境变量，不应通过提交 DLL 到 `optic_system` 来替代。

### `TLS_C1_SAFE_GRATING`

未来硬件 smoke 使用的安全 grating 编号。默认建议通过环境变量显式提供。

### `TLS_C1_SAFE_WAVELENGTH_NM`

未来硬件 smoke 使用的安全目标波长。默认建议通过环境变量显式提供。

### `TLS_C1_RUN_HARDWARE_TESTS`

只有当这个变量设置为 `1` 时，`tests/test_tls_hardware_smoke.py` 才会运行。

## LCD 表示约定

LCD 的物理表示不是普通彩色图像。

项目中的规范是：

- 物理单色掩码：`[H, 3W]`
- 显示 RGB buffer：`[H, W, 3]`

映射关系：

```text
rgb[y, x, c] = mono[y, 3*x + c]
```

因此：

- 所有物理 mask 推理应基于 `[H, 3W]`
- 只有 `LCDService` 才负责把 `[H, 3W]` 打包成 `[H, W, 3]`

## 当前 GUI 能做什么

主 GUI 当前主要保留三类职责：

- 相机实时预览
- 相机参数显示与调整
- 最小 LCD 调试控制

目前 LCD 调试按钮包括：

- Full Transparent
- Full Opaque
- Center Cross
- Vertical Bars

所有按钮都经过 controller 命令层，不直接操作底层设备对象。

## 调试建议

### 相机

看以下几类可观测信息是否正常：

- 预览是否持续更新
- `seq` / `timestamp` 是否递增
- `max pixel` 是否变化
- 修改曝光 / 增益后画面是否有物理变化
- raw 颜色是否正常，是否存在 `R/B` 交换

### LCD

看以下现象是否正常：

- 启动后是否默认全透
- 全黑是否正确
- Center Cross 是否居中
- Vertical Bars 方向是否正确
- 图案是否拉伸、裁切或子像素映射错误

## 开发说明

- 任何重构优先保持 `gui -> control -> devices/capture` 的方向
- 不要把控制逻辑重新塞回 GUI
- TLS 只能通过 `devices/tls_service.py` 访问 `tls_c1`
- GUI 不要直接调用 `TLSService`
- 不要修改 `old/`
- 新开发默认参考 [AGENTS/AGENTS.md](AGENTS/AGENTS.md)

## TLS 测试

无硬件测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tls_service.py tests/test_tls_controller.py
```

默认这些测试不需要 TLS 硬件，也不需要 vendor DLL。

如果你只是开发 `optic_system` 的 TLS 控制层，而不接真实硬件，通常只需要：

1. 安装本仓库基础依赖；
2. 不安装 `tls_c1` 或者只安装 `tls_c1` 而不配置 `TLS_C1_SDK_DIR`；
3. 运行无硬件测试验证 wrapper 和 controller 语义。

未来硬件 smoke 入口：

```powershell
$env:TLS_C1_SDK_DIR = "C:\Path\To\VendorSDK"
$env:TLS_C1_RUN_HARDWARE_TESTS = "1"
$env:TLS_C1_SERIAL = "YOUR_SERIAL"
$env:TLS_C1_SAFE_GRATING = "1"
$env:TLS_C1_SAFE_WAVELENGTH_NM = "550.0"
.\.venv\Scripts\python.exe -m pytest tests/test_tls_hardware_smoke.py
```

## 当前仓库状态

这个仓库现在更接近“可继续扩展的硬件原型基础”，而不是最终实验软件。

如果后续继续推进，比较自然的方向是：

1. 稳定相机预览和参数行为
2. 稳定 LCD 调试与物理映射
3. 再往上叠加 aperture / calibration / wavelength 等流程
