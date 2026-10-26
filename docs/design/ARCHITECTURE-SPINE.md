---
name: codex-pocket
type: architecture-spine
purpose: build-substrate
altitude: feature
paradigm: layered adapter bridge
scope: 手机App、自托管服务与当前桌面跟随适配
status: accepted
created: 2026-10-26
updated: 2026-10-26
binds: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6]
sources: [SPEC.md]
companions: [../../protocol/API.md]
---
# Codex Pocket 架构约束

## Design Paradigm
本地打包界面的Android客户端调用独立HTTP服务，服务只通过桌面适配器访问原执行者；桌面事实、授权和历史属于Codex，桥持有设备授权及投递账目。

## Invariants & Rules

### AD-1 — 原桌面唯一执行所有者 [ADOPTED]
- **Binds:** CAP-1, CAP-2, CAP-3
- **Prevents:** 两执行者同时写同一会话、审批被答到错误请求。
- **Rule:** 只注册follower并查现owner；审批绑定连接epoch+owner+thread+完整请求身份，来源变化失效。native ack不足以判成功，须见owner请求核销；不调用resume或claim owner。无可核操作详情的文件审批只读回电脑。

### AD-2 — 配对与设备授权分离
- **Binds:** CAP-4, CAP-6
- **Prevents:** 扫码即获得永久授权、代理回环被误认为管理员。
- **Rule:** 临时票据随机短时且原子消费；手机仅持poll secret等待电脑核短语确认；设备token高熵、DB存哈希，批准待领取信封加密且短时清理。管理路由独立回环端口且仍需独立凭据，手机路由无admin。撤销检查覆盖排队写入锁内及SSE，IP不作身份。

### AD-3 — 写入账目先于外部动作
- **Binds:** CAP-1, CAP-2, CAP-5
- **Prevents:** 超时重试重复执行、跨设备改绑导致误发。
- **Rule:** 设备+UUID去重键固定目标和正文哈希；取得写锁后重验设备及观察到的controller_id，再登记sending并派发。明确未派发记rejected，断连/超时记unknown；重启sending变unknown。未知不自动重发，清聊天缓存不清账目。

### AD-4 — 有界单向投影 [ADOPTED]
- **Binds:** CAP-3, CAP-5
- **Prevents:** 全量同步冲突、推理/工具秘密外显、任意本机文件泄露。
- **Rule:** 总控仅最近15/30/60条白名单可见消息，其他会话摘要与待处理；原生事件通知并节流增量刷新。图片重编码去元数据、大小像素/总存储有界，回传路径在管理员许可根下；媒体不接受远端URL或客户端文件路径。

### AD-5 — 本地界面和连接代次
- **Binds:** CAP-4, CAP-5, CAP-6
- **Prevents:** 远端页面调用原生桥、旧配对或旧响应污染新连接。
- **Rule:** APK仅加载打包资源origin，外链系统浏览器，JS接口只对白名单路径；设备凭据在Keystore加密存储，JS不获token。准备新配对/取消/退出更换代次，原生保存配对及JS写缓存前验证代次和origin，旧401不撤销新连接。

### AD-6 — 自托管与私有运行数据分离 [ADOPTED]
- **Binds:** CAP-6
- **Prevents:** 源码绑定个人环境或发布时夹带凭据、用户数据。
- **Rule:** app/server/adapters/protocol/docs分工，依赖锁版本；数据目录、个人地址、设备库与签名私钥不入源码包。公网部署和仓库公开独立于本轮开发。管理页面不走手机公开反代。

## Consistency Conventions
| Concern | Convention |
| --- | --- |
| API | protocol/API.md v1，错误为error.code/message；字段和媒体ID按合同 |
| 时间 | UTC Unix秒，用户端按设备时区显示；期限由服务端判断 |
| 状态 | delivered仅桌面接收/请求核销，不等于任务完成；unsupported不伪造按钮 |
| 测试 | 替身与真实桌面、浏览器与Android模拟器、实体手机分别报告 |

## Stack
| Name | Version |
| --- | --- |
| aiohttp | 3.14.5 |
| SQLite | Python运行时内置 |
| cryptography | 50.0.2 |
| Pillow | 13.0.0 |
| qrcode | 8.2 |
| Android compile SDK | 37 |
| JDK | 21.0.12.1 |
| Gradle | 9.6.0 |
| ZXing Embedded | 4.3.0 |

## Capability → Architecture Map
| Capability | Lives in | Governed by |
| --- | --- | --- |
| CAP-1, CAP-2 | server/app, adapters/desktop, app/web | AD-1, AD-3, AD-4 |
| CAP-3 | server media, Android upload, desktop native turn | AD-1, AD-4 |
| CAP-4 | server/store, admin, Android pairing/storage | AD-2, AD-5 |
| CAP-5 | server event projection, app/web cache | AD-3, AD-4, AD-5 |
| CAP-6 | build/scripts/docs | AD-5, AD-6 |

## Deferred
- iOS构建分发：需对应平台和签名条件；不影响当前Android协议。
- 操作系统终止进程后的推送：需按实体手机和通道选择验证，当前可见后台连接不保证常驻。
- 内部桌面IPC后续版本：以能力探测和受控失败处理，发布升级前重验。
- 开源许可证与公开仓库：用户计划之后发布；先保留依赖许可和不含秘密的源码包。

## 验证边界

0.1.0开发基线按本轮架构静态门和实际核心路径接入验收。完整证据及未验项见[验证记录](../VALIDATION.md)。内部接口、实体手机和公网体验不作稳定性承诺。撤销设备的客户端状态同步属于AD-5同一连接代次约束，须同时满足旧连接事件不得污染新连接、当前凭据核销后界面立即失效。

## 0.2.0网络路径补充

同逻辑服务可有最多6个明确origin，P256 pin挑战绑定nonce和本机管理员登记的完整origin；不从网络响应扩充。通道仍须HTTPS，debug私网HTTP不宣称抗MITM。RouteManager单进程共享策略，路径代次与凭据代次分开，旧grant只拒绝不清新状态，配置条件提交。SSE应用限制不降低线路健康；流槽prepare也纳入finally。切线不重放写请求、不取消在途写，未知结果保留原账目。实际边界见NETWORKS和VALIDATION。

## 0.3.0自动发现补充

电脑每15秒获取本机可用私网接口与Tailscale Self地址，成功绑定TLS后才发布。原手动地址保留；自动地址与手动地址分别管理，总上限6个。手机扫码固定服务签名公钥与TLS SPKI。公开/discovery仅返回nonce绑定的签名manifest，不接收Bearer；只有签名、revision、TLS pin全部通过后才能更新managed地址。NSD只是临时候选，不授予访问权、不直接持久化发现端点。旧无pin配对不自动升级信任。

证书到期前同密钥续期，重载原SSLContext；Android逐连接验证有效期及固定SPKI。TLS策略缓存以完整origin+pin为键、上限16，不能跨身份复用。地址列表、revision与identity allowlist在单SQLite事务内更新。切换保持逻辑服务身份、草稿和原有写入不重放约束。

保留至少一个稳定可达种子时自动同步；所有种子失效且无法接收局域网广播时，需恢复Tailscale/网络或重新扫码。AVD未验证全失联后的广播恢复；实体手机、路由器隔离、真实Wi-Fi/蜂窝切换及OEM后台仍为发布前实机门。

## 0.3.1 ADB文件入口补充

App专属外部目录CodexPocket/inbox、outbox、media为稳定英文文件交换面；设备凭据/密钥留私有存储。DUMP保护Receiver，只接固定命令、严格basename与有界数据，原子stage+内部台账成功后消费入口。MainActivity从内部一次性队列取内容，不信Intent payload。图片只入草稿、配对只预览；prepared不表示投递或授权。操作有结果文件，失败保留来源，processing中断不自动重放。有限导入台账通过显式epoch维护拒绝旧ID，维护不触配对或用户媒体。电脑CLI必须显式serial，不自动选连接设备。

## 0.4.0配置邀请与接口排除补充

可复用配置URI是公开信任引导，不是device grant。明确Import前本地预览；同pin已配对服务沿signed discovery刷新，首次/不同服务用独立域签名短ticket后仍claim+电脑短语approve。config_id/nonce/revision/expiry纳入验证，轮换事务只拒旧issued/pending，不撤approved。预览与手机设置显示短身份及实际线路；稳定逻辑URL仍只用于内部身份/缓存。

取消范围覆盖native请求、已prepare未交JS的候选与JS pairing代次；同服务取消不能迟到commit网络，claim取消不能遗留隐藏payload。新深链不覆盖在途导入。电脑网卡排除是按接口持久偏好，成功绑定/广播/manifest须一致撤回该地址，不按10/8封禁。0.4实体/光学扫码/移动数据与AVD发布门详见VALIDATION，不以受控UI测试代替。
