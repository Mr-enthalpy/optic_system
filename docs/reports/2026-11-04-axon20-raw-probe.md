# Axon 20 免 Root RAW 验证：软件交付与实机验收状态

后续状态：手机连接后的实际数据与限制见 [真机验证报告](2026-11-04-axon20-live-raw-validation.md)。本文保留最初软件交付时的验证记录。

日期：2026-11-04。设备目标：ZTE A2121 / P725A02，MyOS 11.0.18_A2121，Android 11。

**结论：软件已实现并通过构建及无硬件回归；实机验收尚未完成。实际采集 RAW 为 0 帧，不能宣布前摄 RAW 可用、曝光可控或衍射结构保留。**

## 范围与来源

这是用户独立授权的真实手机公开 Camera2 验证，与 LCD 近瞳原型的数据/结论分开。实现位于本成员 `tools/android_raw_probe/`，不接入实验室相机控制器或 RawCapture 数据链，不修改 reconstruction 或 Meta gitlink。Bootloader、Root、EDL、HAL 修改及系统分区操作均不属于本次任务。

历史证据是用户提供的 properties/camera 日志；这些日志中的前摄 RAW/手动能力及 3264×2448 流配置仅作为候选依据，未被写成本 App 的成功采集。本次没有原始图像、设备采集 commit 或光学条件实测值。未来取得图像后，必须以 App run 的 source_commit、app_source_sha256 和构建清单记录实际采集版本，不能把后续修改版本追认成采集版本。

## 已交付软件

- 最小 Java Camera2 App：枚举所有公开 ID 和完整可读取 characteristics，只允许选择已枚举 FRONT 相机。多个前摄时，自动请求必须明确指定 ID。
- RAW_SENSOR 优先，3264×2448 若被宣告则优先采用；RAW10 独立测试。缺少目标尺寸时明确记录 fallback，缺少 RAW/MANUAL_SENSOR 或请求越界则失败。
- 固定曝光/ISO 请求，记录实际曝光、ISO、AE 状态、frame duration 和全部可读取结果。ISO 不被冒充为独立模拟/数字增益标定。
- 原样 plane buffer、stride、尺寸、格式、时间戳、position/limit/capacity、SHA256；保留完整可读取的 request/result/characteristics。LensShadingMap 数值可序列化；不透明 vendor 对象或读取失败明确报出，不能冒充完整无损 vendor metadata。
- RAW_SENSOR 额外使用 DngCreator；RAW10 保留 packed buffer，并注明 DngCreator.writeImage 不适用。DNG 不替代原始 buffer。
- RAW+JPEG/YUV 使用同一个请求；必须与结果具有同一 sensor timestamp。拒绝组合、丢帧或时间戳错配时保存失败，不另拍一帧冒充同步。
- 采集时固定黑色应用页面，记录亮度设置，保留系统栏和 vendor UDC 显示控制未知这一限制。
- Windows 构建、普通 ADB 安装/控制/原始导出、Python 解码和可视化。原始 DN 不被预览缩放覆盖。
- 逐像素时间标准差、曝光回读检查、经人工标注并带匹配暗场的曝光序列描述性拟合；不会自动宣称衍射或光谱标定成功。

完整命令、文件布局和物理操作协议见 [工具说明](../../tools/android_raw_probe/README.md)。

## 实际执行的验证

| 检查 | 结果 | 能证明什么 |
|---|---|---|
| Android Java 编译 / D8 / APK 打包 | 通过 | 当前源码能生成 API 28+ APK；不能证明设备兼容 |
| APK 签名检查 | 通过，v3 signature | 本地产物签名结构可验证；不是设备实测 |
| 新增解析/追溯测试 | 24 项通过 | 合成数据上的布局、完整性、关联与统计逻辑 |
| 成员无硬件回归 | 398 passed，3 skipped，19.43 秒 | 未发现当前无硬件套件回归 |
| ADB transport 状态 | 0 台，沙箱内外均检查 | 本次无法安装/采集；不推断驱动失效或手机启动状态 |
| APK 安装 / Camera2 枚举 | 未执行 | 等待授权过的普通 ADB 连接 |
| 实际 RAW / DNG / JPEG / YUV | 0 帧 | 没有手机图像或光学验收结论 |

测试涵盖 RAW_SENSOR 行填充/短末行/偏移/大小端，RAW10 五字节四像素及全 1024 数值，截断与哈希错误，前摄来源、请求/结果/图像时间戳、伴随图像哈希、不完整计数、实际曝光偏离、元数据不透明标记、固定像素时间标准差、暗场配对和饱和/未确认照明的拟合门限。

这些 fixture 在测试中明确标记为 synthetic，不是传感器数据，不加入任何手机验收帧数。3 项跳过属于回归套件的未执行检查，不表示光学或硬件通过。

本地 APK、构建与 SDK 来源清单、JUnit 输出保留在忽略的 outputs 目录；没有把二进制、调试密钥、原始系统日志或数据加入 Git。没有调用 Bootloader/Fastboot/Root/EDL，也没有发出手机重启、清除应用数据或卸载命令。

## 第一阶段验收逐项状态

| 用户验收项 | 当前状态 | 仍需证据 |
|---|---|---|
| 至少 10 帧实际前摄 RAW | **未通过：尚无实测** | timestamp 唯一、文件完整、run 终态明确的 >=10 帧 |
| 来自前置屏下摄像头 | 待实测 | CameraManager FRONT 身份、型号/固件、图像/结果关联及物理前摄确认 |
| Bayer 与有效位深可解释 | 解析逻辑通过，设备证据待采集 | CFA、输出坐标原点、位布局、white/black level、实际分布；不将 16 位容器说成 16 位 ADC |
| 曝光可控制 | 待实测 | 同批请求/回读、至少多档曝光的重复暗场和均匀照明数据 |
| 电脑端正确解析和保存 | 合成数据通过，真机待验证 | 导出原始哈希、实际布局、uint16 NPY 与预览检查 |
| 点光源空间结构可检查 | 工具与协议就绪，实测待执行 | 真正点光源、实际照明记录、未饱和重复图像及人工检查 |
| RAW10 / RAW+JPEG/YUV | 待实测 | 各自成功帧或实际失败日志，不根据能力声明推定成功 |

RAW10 失败不会使 RAW_SENSOR 已取得的证据失效，但必须单独记录。DNG 生成失败保留 RAW，并标记 artifact error。`capture_complete`、`run_succeeded`、手动回读检查与科学验收是不同字段，不能互相替代。

## 下一项操作与限制

需要用户将手机置于正常 Android 界面、解锁屏幕并连接 USB，必要时确认调试授权。无需重装驱动或进入 Fastboot。连接出现后可在已有授权内安装 APK，先枚举，之后执行 RAW_SENSOR 单独 10 帧的可用性测试；光源未布置时使用 unspecified，不能冒称暗场或 PSF。

暗场、稳定均匀照明和真实点光源需要研究人员实际布置和确认。手机采集成功仍不证明 RAW 完全未经 ISP，也不证明已经保留科研所需的衍射通道。Root 必要性继续保持未决，只有取得实际公开接口结果后才讨论。
