# 第三方组件与来源

当前实现未复制HAPI、Happy或Hermes源码；它们仅作为调研参考。也未包含从本机Codex桌面提取的闭源代码。桌面IPC适配为本项目实现，接口可用性不代表OpenAI官方支持。

| 组件 | 锁定版本 | 许可/原作者来源 |
| --- | --- | --- |
| aiohttp | 3.14.5 | Apache-2.0 · https://github.com/aio-libs/aiohttp |
| cryptography | 50.0.2 | Apache-2.0 / BSD · https://github.com/pyca/cryptography |
| Pillow | 13.0.0 | HPND · https://github.com/python-pillow/Pillow |
| qrcode | 8.2 | BSD · https://github.com/lincolnloop/python-qrcode |
| zeroconf | 0.151.5 | LGPL-2.1-or-later · https://github.com/python-zeroconf/python-zeroconf |
| ifaddr (zeroconf依赖) | 0.2.0 | MIT · https://github.com/pydron/ifaddr |
| ZXing Embedded | 4.3.0 | Apache-2.0 · https://github.com/journeyapps/zxing-android-embedded |
| ZXing Core | 3.5.3 | Apache-2.0 · https://github.com/zxing/zxing |
| AndroidX | 实际解析版本见runtime-dependencies.json | Apache-2.0 · https://android.googlesource.com/platform/frameworks/support |
| Gradle Wrapper | 9.6.0 | Apache-2.0 · https://github.com/gradle/gradle |

Python依赖完整安装版本在requirements.lock；主要Python组件原许可副本见third_party/python。Android transitive依赖清单可按Gradle依赖报告复现。SDK、JDK、模拟器和个人签名密钥不随源码分发。本项目自身采用MIT；第三方许可不受影响，分发二进制时仍应核对应义务。

自动发现使用未修改的python-zeroconf动态Python依赖。官方PyPI于本轮核验稳定版0.151.5、Python>=3.10、LGPL-2.1-or-later；附third_party/python/zeroconf/COPYING与ifaddr/LICENSE.txt。没有将其代码复制进本项目，也不限制用户替换/修改该库。源码分发保留本说明、许可证和上游来源；若后续分发含库的二进制/冻结包，发布者还须核对应源码提供及可替换/重新链接义务，不能将当前源码依赖说明当作所有打包形式的许可审查。官方版本来源：https://pypi.org/project/zeroconf/0.151.5/ 。
