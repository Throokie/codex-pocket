# ADB 文件入口（App 0.3.1）

固定英文目录，直接使用 `adb push`、`adb pull` 和命令回执，不需要点击文件选择器。需要已授权的USB/无线ADB；不要求root，不开放网络文件服务，不申请全盘存储权限。

## 目录

默认Android主用户路径：

```text
/sdcard/Android/data/io.github.codexpocket.app/files/CodexPocket/
  inbox/       # 待导入的配对JSON、图片
  outbox/      # status.json、result-<request_id>.json；不含凭据
  media/       # 预留给用户手动管理的媒体文件；当前不自动导出聊天图片
```

App负责初始化目录，不能假定ADB可自行创建Android/data下的App根目录。工作配置和配对密钥仍在App私有目录/Android Keystore，不通过上述目录导出。该目录是文件交换入口，不是完整数据库备份。覆盖安装保留数据；卸载会删除App专属目录，卸载前需先pull要保留的文件。多用户/工作资料场景需核对实际用户目录，本轮仅验证主用户。

## 推荐：使用电脑脚本

在源码根运行，Python3标准库即可。必须明确指定设备serial；不会猜测或操作其他连接设备。

```sh
adb devices -l
python3 scripts/pocket-adb.py --serial DEVICE_SERIAL init
python3 scripts/pocket-adb.py --serial DEVICE_SERIAL status
python3 scripts/pocket-adb.py --serial DEVICE_SERIAL import-pairing ./pairing.json
python3 scripts/pocket-adb.py --serial DEVICE_SERIAL import-image ./screenshot.png
python3 scripts/pocket-adb.py --serial DEVICE_SERIAL pull ./pocket-files-new
```

非默认ADB服务可加 `--adb-port PORT`，可用 `--adb /path/to/adb` 指定可执行文件。`paths`离线显示三个目录。`pull`要求新建目的目录，避免覆盖已有本机文件。脚本通过参数数组调用ADB，文件路径不会被拼成shell代码；远端文件使用随机英文名。

`import-pairing`只准备配对预览，不自动请求/允许配对，也不导出旧设备授权。配对仍沿用两端核对短语的流程。`import-image`只放入待发草稿，不自动发消息；需保持正确总控与会话。`prepared`表示准备完成，不表示已配对、上传或投递。

## 直接使用ADB

入口component `io.github.codexpocket.app/.AdbCommandReceiver`；action `io.github.codexpocket.app.ADB_COMMAND`。由 `android.permission.DUMP`保护，只允许具有该系统权限的调用者（通常为已授权ADB shell），普通第三方App不允许调用。初始化示例：

```sh
adb -s DEVICE_SERIAL shell am broadcast --include-stopped-packages --receiver-foreground \
  -n io.github.codexpocket.app/.AdbCommandReceiver \
  -a io.github.codexpocket.app.ADB_COMMAND \
  --es command init --es request_id init_001
adb -s DEVICE_SERIAL pull /sdcard/Android/data/io.github.codexpocket.app/files/CodexPocket/outbox/result-init_001.json
```

可用command：`init`、`status`、`import-pairing`、`import-image`、`reset-history`。import额外传 `--es file example.json`，文件须先push到inbox。仅接受严格英文basename，不能传目录、绝对路径或 `..`。同一个request_id用于核对同一次操作，不要把它复用于不同内容。导入需要App准备界面/草稿，ADB可以启动App；不需要自动化点击。

结果文件schema：`{version:1,request_id,command,status,code,updated_at,...}`。`queued`不是完成，等待`ok`、`prepared`或`error`；文件原子更新。广播命令返回“已接收”也不等于导入完成，始终读回执。超时先查原request_id结果，不盲目重复。配对票据只短时有效，避免长期保留在电脑和共享文件夹；校验成功并复制到私有一次性队列后，App即删除inbox源文件；之后由私有队列准备预览/草稿。凭据不会复制到outbox。配对JSON上限64KiB、图片上限12MiB，图片还须通过真实格式与尺寸校验。

## 范围与限制

这是受控文件导入，不提供任意路径读写、执行命令、导出完整会话或自动批准。图片尺寸/类型与队列受到限额；失败以回执说明，不应把文件存在当成已进入草稿。不同厂商对ADB和Android/data访问可能有额外限制；验证范围见 `VALIDATION.md`，受阻时先核设备授权和回执，不通过root或放宽密钥存储绕过。

平台依据：[Android App专属文件目录](https://developer.android.com/training/data-storage/app-specific)、[AOSP Shell权限声明](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/packages/Shell/AndroidManifest.xml)。

## 导入台账与中断恢复

导入台账最多512条，避免无限增长。脚本从init回执获取history_epoch并生成 `e<epoch>-<UUID>` 请求编号；相同编号与内容不重复准备，换内容拒绝。中断或超时只报告状态，不自动重复UI操作。失败暂存有界并在后续命令检查时清理，原电脑文件仍可用新编号重新导入。

容量满时明确返回history_full。可运行 `python3 scripts/pocket-adb.py --serial DEVICE_SERIAL reset-history` 清理终态导入记录；有在途导入会拒绝。此操作递增epoch，旧编号随后拒绝，不能以删除历史的方式重放旧导入。只清理导入台账及对应暂存，不清设备配对、草稿或用户media。正常导入不需要执行该维护命令。status/init回执包含history_epoch/history_size。

实体OPPO Android16补验：后台/通知栏状态下的广播曾未送达，前台成功，尚未将单个标志与厂商策略确认为唯一根因。电脑脚本去掉非必要的allow-background-activity-starts标志；import先正常打开App再初始化/投递。init/status默认保持无界面，厂商阻拦时可显式加 `--foreground` 打开App再执行。已有目录的pull不依赖广播。普通配对也可走App原有输入界面。
