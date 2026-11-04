# Axon 20 免 Root RAW 真机验证（2026-11-04）

后续观测：[外部遮挡暗场报告](2026-11-04-axon20-dark-capture.md) 记录另采的 30 帧及其限制。本文保留接口验证时的批次和验收状态。

本次确认：ZTE A2121 / P725A02、MyOS 11.0.18 的公开 Camera2 前摄接口实际输出了 3264×2448 Bayer RAW_SENSOR 和 RAW10。6 批共 32 帧原始 RAW 均已导出并通过电脑端完整性、布局、来源及时间戳检查。获取这些数据不需要 Root。**完整科研验收尚未通过：暗场、稳定均匀照明、真实点光源及波长依赖 PSF 均未采集。**

此报告承接 [此前软件交付报告](2026-11-04-axon20-raw-probe.md)，不改写此前无设备时的验证事实。手机实验独立于 LCD 实验室原型，不支持外推两者光学等价性。

## 设备与采集版本

实机只读属性与用户已提供的 A2121/P725A02 Android 11 固件一致；Bootloader locked=1、verified boot=green。只通过普通 ADB 安装/更新调试 APK、运行普通 Camera2 应用、读取应用文件；未执行解锁、Root、EDL、重启、分区/槽位/HAL 修改、应用清除或卸载。

公开 CameraManager 枚举仅有 ID 0（BACK）和 ID 1（FRONT）；前摄 ID 来自枚举而非硬编码。ID 1 宣告 RAW_SENSOR/RAW10 的 3264×2448 输出及 MANUAL_SENSOR。HAL 诊断中更多内部 ID 不被当作普通应用可访问的相机。FRONT 身份、型号/固件、请求/结果/图像关联构成当前来源证据；传感器具体型号和物理遮挡确认仍未取得。

- 首批 RAW_SENSOR 的实际采集提交：`224b1efb4bbf75231b09ffdbdf1b25f612b11ecb`；源码 SHA256 `c2a9f085265dcd00b1f444e720523326c722ed8cd8957d99ecf45de7ea12de20`。
- 后续五批的实际采集提交：`360df7df23283636458e7422d2a44b6c6947174f`；源码 SHA256 `766a90cca2b5f9a31168a7e20ec96796382ffe077c669e1a51bfb9f4c73f550c`，记录 source_dirty=false。
- 更新 APK 版本 0.2，修复标准 mandatory stream combinations 的结构化序列化；保留原批次元数据原貌。首批有 62 个此标准结构的不透明值，不能把它称为完整无损元数据；新版五批解析报告未发现序列化问题。
- 本报告的后续提交不是新的采集版本。没有发布数据、远程推送或更新 Meta gitlink。

## 实测批次与离线核验

所有批次均为 `scene=unspecified`、`condition_verified_by_operator=false`、ISO100、AE OFF。首批批次首先在应用日志中被观察到，不宣称由主机采集命令发起；后续五批由主机命令发起。曝光检查容差为相对 2%，ISO 为 max(1,1%)；这些帧回读与请求精确相同，容差不是辐射标定精度。

| run_id | 输出 | 帧数 | 请求及实际曝光 | 状态 |
|---|---|---:|---:|---|
| run_1793790678030_deaf376a | RAW_SENSOR + DNG | 10 | 10 ms | 完成，离线核验通过 |
| run_1793791278651_9c6d6654 | RAW10 | 10 | 10 ms | 完成，离线核验通过 |
| run_1793791293401_49b7c663 | RAW_SENSOR + DNG + JPEG | 3 | 10 ms | 完成，同请求/时间戳核验通过 |
| run_1793791306329_eccb5177 | RAW_SENSOR + DNG + YUV | 3 | 10 ms | 完成，同请求/时间戳核验通过 |
| run_1793791320419_3cfc9813 | RAW_SENSOR + DNG | 3 | 1 ms | 完成，离线核验通过 |
| run_1793791334883_bdcd60b6 | RAW_SENSOR + DNG | 3 | 4 ms | 完成，离线核验通过 |

合计 22 帧 RAW_SENSOR、10 帧 RAW10、22 个 DNG、3 帧 JPEG、3 帧 YUV（后两种均为 640×480）。配对使用同一 CaptureRequest；电脑端核对各 image timestamp 与结果 sensor timestamp，及所有原始 plane SHA256。未以另一曝光补拍冒充配对。这里只验证小分辨率伴随流，未证明全分辨率 JPEG/YUV 组合可用，也不是原厂相机 App 的 RGB 处理结果。

每批 capture_complete/run_succeeded 均为 true，帧数完整，RAW 时间戳唯一。逐帧读取原始 buffer 后另存 uint16 NPY、未去马赛克 parity tile 预览和时间统计；未覆盖二进制原始文件。新版五批保留完整可公开读取的 characteristics/request/result，没有发现读取或不透明序列化告警；不可据此宣称掌握 HAL 未公开内部状态。

## 内存布局、Bayer 与位深

| 字段 | RAW_SENSOR | RAW10 |
|---|---|---|
| 输出尺寸 | 3264×2448 | 3264×2448 |
| Android format | 32 | 37 |
| 原始 plane 大小 | 15,980,544 bytes | 9,987,840 bytes |
| rowStride | 6528 bytes | 4080 bytes |
| pixelStride | 2 | 0（packed） |
| 解码 | little-endian uint16，每行按 stride | 每 5 bytes 解出 4 个 10-bit 像素，每行按 stride |
| 首帧 DN 范围 | 62–1023（首批） | 61–1023 |

HAL CFA=3（BGGR），静态黑电平四项均 64，whiteLevel=1023；与 10-bit 数值范围相容。RAW_SENSOR 的 16-bit 容器不表示 16-bit ADC；RAW10 的十位打包也不测量有效噪声位数。具体 ADC 精度、增益分解与传感器型号仍未知。

pixelArray=3280×2464，active/preCorrectionActiveArray=(8,8)-(3272,2456)。图像 cropRect=(0,0)-(3264,2448)。首个 DNG 的 CFA=[B,G,G,R]、BitsPerSample=16、whiteLevel=1023、blackLevel=64，默认裁剪 origin=(8,8)、size=3248×2432；原始 buffer 保留全幅，未按 DNG 默认裁剪静默截取。DNG 与 CFA 声明同源，不构成独立颜色靶验证；输出原点/CFA 的物理验证尚待完成。

## 已观察的处理及稳定性限制

`android.sensor.info.lensShadingApplied=true`。请求 shading OFF、lens shading map ON 不足以证明取消 RAW 内的既有校正。

- 单独 RAW_SENSOR 和 RAW10 批次未返回 lensShadingCorrectionMap，也没有 dynamicBlackLevel。
- RAW+JPEG/YUV 两批返回 13×17×4 的全 1 LensShadingMap，dynamicBlackLevel=[63,63,63,63]；全 1 图不能被解释为此前不存在校正。
- 实际 postRawSensitivityBoost=100；ISO100 不是已标定的模拟/数字增益分离。
- 首批 10 帧固定像素时间标准差中位数 0.789 DN、p95=1.578 DN；RAW10 的对应值为 0.919 DN 和 120.620 DN。后者存在明显高变化区域，不能声称场景稳定或仪器噪声良好。统计混有运动、场景和照明变化，不是暗噪声或重复 PSF 测量。
- 预览存在空间场景结构及亮区；未经点光源布置确认，不能称为衍射、PSF 或波长编码。1/4/10 ms 回读通过只证明控制生效，不能替代曝光线性。
- 应用采集页为固定黑底及底部静态文字；OS 栏、自动亮度和厂商 UDC 区域显示状态没有物理验证。未验证屏幕处理对 RAW 的影响。

## 本地证据与复现

原始数据及设备元数据仅保存在忽略的本地输出目录，不加入 Git。相对于实验工作区，证据位于 `outputs/axon20_live_20261104/`：

- `export_initial/original_export.tar`：首批备份，SHA256 `ddb30b9eb2bc655987a451cfabdf13f6625ff11e77b15a3c6ad4a98b21394ae9`。
- `export_followup/original_export.tar`：包含六批的备份，SHA256 `cfc7f86d79fe2d85840b15e635a0b6df1fc761e10b9c01e2d8e45005df307542`。
- `export_followup/device_files/captures/`：原始 plane、图像元数据、请求、完整结果、characteristics、DNG 与运行终态。
- `decoded_run_1793790678030_deaf376a/` 与 `decoded_followup/<run_id>/`：NPY、parity 预览、逐帧及时间统计；`live_validation_summary.json` 为批次汇总。
- `initial_dng_tags.json`、`inventory_public_app.json`、导出内应用日志与清单；构建目录保留签名验证和 build_manifest.json。

命令详见 [工具 README](../../tools/android_raw_probe/README.md)。复现某个导出批次时，对新目录运行 `raw_tools.py decode <run-dir> <new-output-dir>`；不要覆盖既有结果。采集使用 `device.py capture --format RAW10 --exposure-us 10000 --iso 100 --count 10`；配对分别使用 `--format RAW_SENSOR --pair JPEG` 或 `--pair YUV`。默认 unspecified，未经研究人员确认不添加物理条件标记。

此前完整无硬件回归为 398 passed / 3 skipped；后续增加超时标识脱敏和原始导出保留测试后，工具专项 26 passed。Java 修复后重新编译、签名并更新 APK成功，新版五批为实际手机检查。未重复整个无硬件套件，未执行实验室相机/LCD/TLS 测试。

## 第一阶段验收结论与下一步

| 验收项 | 结论 |
|---|---|
| 至少 10 帧实际前摄 RAW | 通过普通 Camera2 来源、文件及帧关联检查，实有 32 帧 |
| Bayer/位布局可解释 | BGGR/10-bit 范围及两类内存布局有证据；物理 CFA 原点和有效 ADC 位数未独立验证 |
| 曝光可控制 | 1/4/10 ms、ISO100 请求与回读通过；受控照明线性未验收 |
| PC 离线读取和保存 | 32 帧完整性/布局解析通过，原始文件与 NPY 均保留 |
| 点光源空间衍射结构 | 未验收，无已确认点光源数据 |
| 重复性及暗场/均匀场 | 未验收，无已确认物理条件数据 |

下一步由研究人员先确认真实照明和摆放：不拆机遮光采集暗场；固定设备/亮度/稳定均匀照明采集配对暗场的多曝光序列；最后在不饱和条件下采集真实单色点光源多帧，记录波长、几何与屏幕状态。三波长重复 PSF 是后续物理机制证据，不应从当前室内场景推断。

继续使用现有免 Root 工具即可完成这些实验。当前没有因公开 RAW 不可用而升级到 Root 的必要证据；不据此排除未来研究更早数据节点的需要。阶段一总体结论为：**接口和数据导出验证成功，物理 PSF/线性验收待研究人员布置后继续。**
