# 自托管与复现

## 支持环境

当前适配Linux桌面Codex本机Unix IPC；Android API26以上客户端。桌面程序和其本地工具管道必须运行，Codex账号与模型请求留在原桌面。不是云端24小时代理，也未适配Windows named pipe。内部IPC升级后需重新验收。

## 启动服务

Python3.11以上（本次实际Python3.14），先在源码根运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m server --data /你的私有数据目录 \
  --controller 你的总控thread-id --caller-thread 你的桌面调用来源thread-id \
  --media-root /允许手机查看的图片输出目录
```

默认API 127.0.0.1:32848；本机管理127.0.0.1:32849。程序把带能力凭据的管理链接写进私有数据目录“本机管理链接.txt”，不要转发或把文件放进仓库。只需本机浏览器打开此链接，即可生成二维码、确认手机和撤销设备。首次未给controller时可在配对后的设置里选择现有总控；工具调用来源需有效本机会话，建议显式配置。

API可用`--bind`绑定指定私网接口。需要手机外网使用时，用你自己的HTTPS反向代理只转发API端口。SSE路由关闭缓冲，保留Authorization头；不要把管理端口映射出去。勿使用HTTP公网地址，开发APK只允许局域网数字地址。二维码中的URL必须是手机可访问的服务根地址，无用户名密码、查询或路径。

当前服务端不自动创建会话、不自动派单。手机默认总控来自现有已授权会话；管理其他聊天由本人和总控执行。

## 配置导入与配对

1. 在电脑本机管理页检查自动网络。电脑专用直连可在“选择供手机使用的网络”取消；例如 `--exclude-interface eno2` 也可保存该选择，不能据此禁止所有 10 段网络。
2. 生成“手机连接配置”的链接或二维码。App 0.4.0 扫码、点击链接或粘贴后先本地预览，对照短服务身份与 TLS 指纹；旧 App 需先覆盖升级。
3. 点“导入并连接”。同一已配对服务只更新地址；首次连接还需两端核对短语，由电脑点允许。
4. 获得独立设备凭据后选择默认总控。配置链接可复用，不是访问授权；短期配对 ticket 仅导入后领取。

轮换配置链接会失效旧邀请和未完成的相关票据，不撤销已批准设备。手机丢失时在电脑管理页撤销设备。清缓存不删除配对或桌面历史。一次性两分钟二维码/JSON保留为兼容入口；关闭自动发现时使用手动地址模式。详见 CONFIG_IMPORT.md。浏览器备用入口不实现原生配置导入或 NSD，不建议在共享浏览器使用。

## Android构建

```sh
export JAVA_HOME=/你的/JDK21
export ANDROID_HOME=/你的/android-sdk
cd app/android
./build-apk.sh
```

JDK21、compileSdk37（SDK包platforms;android-37.0）、build-tools37.0.0，Gradle wrapper固定版本。构建脚本使用本项目独立开发签名；`.local/debug.keystore`不入源码发布包。个人安装可用开发APK，长期公开分发前请用自己的正式签名，并保管好用于后续升级。不要用他人的KDE/Codex身份或私钥。

Web资源随APK打包，不加载服务器网页；服务端API升级需保持协议v1兼容，未知版本应拒绝配对。

## 校验与停止

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

以SIGTERM停止本进程，桥会取消自己的following并关闭连接，不停止任何Codex任务。SQLite/信封密钥和管理凭据应一致备份；换设备恢复必须重核授权。恢复时未确认的sending变unknown，不自动重复派发。媒体到限后由本人核对后清理，不删除未知发送仍可能使用的图片。

客户端与服务端以根目录LICENSE中的MIT许可共同发布；第三方保留原许可，见THIRD_PARTY.md。发行说明以GitHub Releases为准。

## 0.2.0多网络配置

在电脑管理页“网络与线路”先登记LAN/内网/Tailscale/公网最多6个地址，再生成包含服务公钥及地址列表的新配对二维码。每个地址必须通向同一服务实例/数据库。手机网络设置可检测、自动择优或手动固定；新增地址先在电脑登记。旧配对兼容单地址，启用多线路需重新扫码。详见NETWORKS.md。私有数据目录新增server-identity.pem，须与原数据库共同备份，不能公开或复制到分叉数据库伪装成多主。内部API与管理端口仍分离，本轮不自动改公网或VPN。

## 0.3.0默认自动发现

启动参数沿用原例，不需要填LAN或Tailscale地址。服务默认自动读取本机地址并在32851建立绑定成功私网接口的固定指纹TLS，管理页可直接生成二维码。原HTTP API/管理端仍loopback。手机扫码并允许一次后自动同步；公网可选，mDNS仅同LAN，Tailscale仍须可用。无全局证书放行、无防火墙/路由/ACL自动更改。身份/TLS密钥与DB一起备份，证书沿同key自动续期。`--no-discovery`回到手动模式。更多边界见AUTO_DISCOVERY.md。

## 电脑管理入口

服务运行后，用 `POCKET_DATA=/你的私有数据目录 python3 scripts/open-manager.py` 打开带本机管理凭据的管理页；不要把私有链接发给他人。管理地址为回环32849，与旧32847界面原型不同。本轮App/服务端统一0.4.0；旧配对仍可保留，配置链接需新版App。可自行配置用户服务和桌面菜单，确保沿用相同私有数据目录。
