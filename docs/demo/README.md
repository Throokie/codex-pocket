# 隔离模拟演示 / Isolated simulated demo

这是随 App 正式 web 界面复制的独立静态演示，全部内容为固定假数据；浏览器渲染，不是 Android 实机截图，也不连接 Codex、服务端或真实业务。顶部固定中英标识，不能隐藏为业务画面。所有地址使用 desktop.local 占位。网页 fetch 被本地模拟实现阻断，CSP connect-src 为 none；没有真实 API fallback、设备授权、IPC 或后台推送。

## 本地查看

在仓库根执行 `python3 -m http.server 8080 --bind 127.0.0.1`，打开 `http://localhost:8080/docs/demo/index.html`。右上“演示场景”可切换总控、收件箱、审批、原生问题、导入预览、设置、图片草稿和电脑确认。无需账号或任何业务服务。也可进入 `docs/demo` 后运行 `python3 -m http.server 8080`，打开 `http://localhost:8080`。两个静态启动路径均有交互核对。

## 复现截图与主视觉

准备 Node.js、Playwright 与浏览器（例如 `npm install --no-save playwright`，`npx playwright install chromium`），在仓库根运行：

```sh
node docs/demo/verify.cjs
node docs/demo/capture.cjs
node docs/demo/capture-animation.cjs demo-frames
node docs/demo/posters.cjs demo-frames
ffmpeg -framerate 2 -i demo-frames/%03d.png -vf 'scale=720:-2:flags=lanczos' -c:v libx264 -pix_fmt yuv420p -y docs/images/demo.mp4
ffmpeg -framerate 2 -i demo-frames/%03d.png -filter_complex 'scale=720:-2:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse' -y docs/images/demo.gif
```

可选环境变量 `CHROMIUM_PATH` 指向现有 Chromium；默认用 Playwright 自带浏览器。`capture.cjs` 生成八张390×844 CSS像素、2倍密度PNG；`posters.cjs` 生成1600×800 hero、1280×640 social、截图拼板及可选动画关键帧拼板。动画28帧、14秒，所有节奏是剪辑安排，不是延迟或性能测量。脚本只启动临时本地静态服务器。

## 与视觉剧本的实际差异

- 沿用真实 App：审批与问题直接在收件箱卡片中展示，没有虚构独立详情页；03/04通过隔离单条请求展示完整卡片。
- 剧本中的两项可回答请求之外，收件箱另有“回电脑处理”项，因此截图徽标是3。动画仅展示两项可回答请求，徽标是2。
- 正式设置页不显示 Tailscale 直连/DERP，也不编造探测延迟；演示线路只使用占位名称，不赋假延迟。
- 配置预览展示假的短身份/SPKI，没有可用邀请、二维码或设备token。电脑确认只有假的短语和本地模拟反馈；不会产生授权。
- 白板为程序绘制的假草图，不是真实照片；08展示图片草稿，不承诺投递完成。
- 动画复用真实界面和本地夹具，包含打字、模拟回复、批准、选“两者都要”及转交反馈。没有 Android 状态栏、相机实扫或真实模型输出。

`app.js`、`style.css`、`transport.js`、`icons.js` 来自0.4.0 web界面的隔离副本；业务实现未改。`mock-pocket.js` 与 `demo-shell.*` 仅在此目录使用，正式 App 不加载它们。截图与主视觉必须保留模拟标识。
