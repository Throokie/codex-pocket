# 0.4.0 验证范围




下表记录本地验收范围。仓库另有只读权限、不含正式签名的 GitHub Actions：[Python 测试](https://github.com/Throokie/codex-pocket/actions/workflows/python-tests.yml) · [Android 检查](https://github.com/Throokie/codex-pocket/actions/workflows/android-tests.yml)。远端结果以各次运行记录为准。

| 范围 | 内容 |
|---|---|
| 自动化测试 | Python 95 项、Java 165 项，另含 10000 事件合并测试（本地运行；远端状态见上述 Actions） |
| 模拟器 | 最终 APK 在 API 33 AVD 上跑通 15 个导入、取消、迁移场景 |
| 真机（0.4.0） | 一台 Android 16 设备覆盖安装后配对保留；关闭 Wi-Fi 后经蜂窝网络 + Tailscale 成功读取总控和聊天状态 |
| 真机（早期版本） | 曾观察到桌面消息更新同步到手机；曾验证从手机发送的图片能被桌面聊天识别。0.4.0 尚未在同一条件下重测 |
| 命令审批 | 已有接口实现和替身（mock）测试；**尚未**用真实触发的原生命令审批做端到端验收 |
| 尚未验证 | 模型新生成的图片自动回传到手机；相机光学扫码；所有已知地址同时失联时的重新发现；Doze 下的行为 |

以上结果不构成通用的延迟指标，也不代表所有设备都已完整通过。

## 实物与演示分开

README中截图和GIF来自浏览器渲染的App内置界面，使用隔离的模拟数据与桥，不连接真实桌面；它们不构成Android网络、审批或相机实测。

最终开发APK SHA256：`973b59f55a79361ba03004bb01d32aeea00f869254995a7ac94f0679dad2a2f7`。最终APK内六件Web资源与发布源文件一致。开发签名证书SHA256：`c7b5f3a4766547e85b1158dd30eb246c696ed690fc2bc3377e487f0d18f7695f`。

详细安全边界见[安全说明](SECURITY.md)，本地复验命令见[自托管](SELF_HOST.md)。
