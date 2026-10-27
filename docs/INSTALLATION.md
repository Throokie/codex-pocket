# 0.5.0rc1 电脑端安装候选包

这是电脑端 Python 包，不是 Android APK，也不是免 Python 的 `.exe` / `.dmg` 安装器。需要 Python 3.11 或更高版本。客户端仍可使用 0.4.0 Android App；协议保持 v1。

本轮按“能安装”的范围验证安装、资源、检查入口及无桌面时的安全行为，不要求安装、启动或登录 Codex 桌面。安装成功不代表所有平台都能连接 Codex，尤其 Windows 的服务仍不可运行。详见[平台状态](PLATFORMS.md)。

## 下载安装

从开发 PR 的 CI 工作流附件（或若已创建的 `v0.5.0rc1` 预发布）下载 `codex_pocket-0.5.0rc1-py3-none-any.whl` 与 `SHA256SUMS.txt`，核对哈希。哈希只能确认下载完整，本候选包没有正式签名。不要把 Android APK 当电脑安装包。

必须在独立环境安装，避免修改系统 Python；当前顶层 Python 包名 `server` / `adapters` 较通用，共享环境可能与其他包冲突。下列命令在 wheel 所在目录执行。

### Linux / macOS

```sh
python3 -m venv pocket-env
pocket-env/bin/python -m pip install ./codex_pocket-0.5.0rc1-py3-none-any.whl
pocket-env/bin/codex-pocket --version
pocket-env/bin/codex-pocket doctor --json --data "$HOME/.local/share/codex-pocket"
```

### Windows PowerShell

```powershell
py -3 -m venv pocket-env
.\pocket-env\Scripts\python.exe -m pip install .\codex_pocket-0.5.0rc1-py3-none-any.whl
.\pocket-env\Scripts\codex-pocket.exe --version
.\pocket-env\Scripts\codex-pocket.exe doctor --json
```

`py` 不存在时，用你已安装的 Python 3.11+ 解释器代替第一行。无需修改 PowerShell 执行策略，因为这里直接调用 `.exe`，不激活环境。

已经使用 pipx 或 uv 的用户也可用 `pipx install ./codex_pocket-0.5.0rc1-py3-none-any.whl` 或 `uv tool install ./codex_pocket-0.5.0rc1-py3-none-any.whl`，然后运行 `codex-pocket doctor --data "$HOME/.local/share/codex-pocket"`（Windows 不需要 `--data`）。

## 检查能做什么

- `codex-pocket --version`：与安装元数据核对版本。
- `codex-pocket doctor --json --data PATH`：只读检查平台能力及指定目录的元数据；不创建目录、不读凭据、不连接桌面。`implemented-unverified` 表示实现路线存在，不表示当前桌面已验收。


- `codex-pocket serve --help`：查看服务参数。Windows 会在进入服务逻辑前报告不支持。
- `codex-pocket manager`：Linux/macOS 打开已运行服务的本机管理页，不打印管理链接。

不带 `--data` 时，`doctor` 与 `serve` 一样使用当前目录的 `./.local`，并不会检查示例中另选的数据目录。`data_directory.state` 为 `insecure` 时，可能是类型、属主或权限问题。只有本人拥有的真实目录仅权限过宽时，`serve` 才会收紧为 0700 后启动；符号链接、非目录或属主不符都会被拒绝。Windows 上 `data_directory` 固定为 `unsupported`，因为没有可用的私有文件方案。

Windows 上 `doctor` 可运行，但 `codex-pocket serve` 以退出码 **3** 拒绝启动，且不创建数据目录（源码方式 `python -m server` 同样拒绝，退出码为 1）。这是当前明确限制：Windows 上安装成功不代表可以使用。

## Linux / macOS 启动

要实际连接桌面，需要本机已运行、已登录的 Codex 桌面，以及一个已有的总控聊天 ID。Linux 沿用 0.4.0 的连接实现；macOS 的连接实现尚未与真实 Codex 桌面握手验证，可能无法连接。以下命令只是用法示例，不是本轮验收结论。

```sh
pocket-env/bin/codex-pocket serve \
  --data "$HOME/.local/share/codex-pocket" \
  --controller YOUR_CONTROLLER_THREAD_ID \
  --caller-thread YOUR_CONTROLLER_THREAD_ID
```

另开终端，在相同环境中打开管理页：

```sh
POCKET_DATA="$HOME/.local/share/codex-pocket" pocket-env/bin/codex-pocket manager
```

默认手机 API 为 32848、本机管理为 32849，私网自动 TLS 为 32851。管理端只监听电脑回环，不要公开。macOS 尚无自动局域网发现，需要在管理页手动登记地址。跨网线路依赖本机已有的 Tailscale CLI；安装成功不代表自动配对或跨网可达。

原来的 `python -m server` 源码启动方式保留。升级时沿用原数据目录，不自动迁移、不清配对、不重建既有密钥。卸载虚拟环境不会替你删除原服务数据；不要把数据目录放进临时环境。新版对私有文件的检查更严格，旧数据文件权限不符时会拒绝启动，见[私有文件保护的变化](PLATFORMS.md#私有文件保护的变化)。

## 自动化验证口径

安装工作流在构建机生成同一份 wheel 和 sdist，核资源完整及 sdist 重建一致性；三种系统、两种 Python 版本分别在新虚拟环境安装，在仓库目录外运行检查，避免误导入源码。

Linux/macOS 冒烟仅启动隔离的 loopback 服务，桌面入口、工具临时目录全部指向测试空间，确认未连接桌面时消息能力关闭、私有文件为 0600。Windows 验证能安装与诊断，且服务拒绝发生在任何私有状态创建前。任何一项都不能代替真实桌面收发或审批验收。

本轮三种系统的 Python 3.11 / 3.14 安装检查均已通过，详见[该次验证](https://github.com/Throokie/codex-pocket/actions/runs/40473459493)（源码提交 `c086fe3c51cf48c180027047fc02b02909f5d9f4`）。Windows 仅安装和拒绝路径，不能实际使用服务。

运行记录：[Package installation checks](https://github.com/Throokie/codex-pocket/actions/workflows/installable-package.yml)。本轮不发布到 PyPI，不使用正式签名证书，不改动 0.4.0 已发布包。

