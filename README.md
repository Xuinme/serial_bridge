# 串口桥接

串口桥接（Serial Bridge Manager）是一款 Windows 串口转 TCP 管理工具，可将物理 COM 端口共享给多个外部终端或 AI 客户端，同时管理多个独立连接。

当前源码版本：**2.3.2**。本次仅发布源码，安装包将在后续发布。

## 主要功能

- 多连接管理，每个连接独立配置串口、波特率、监听地址和 TCP 端口。
- 支持外部 Telnet、原始 TCP 客户端和内置轻量 Telnet 终端。
- 客户端输入写入串口，设备真实输出广播给所有客户端，匹配到的设备回显按来源着色。
- 自动分配 TCP 端口，从 `9999` 开始避开已配置和系统占用的端口。
- COM 端口在应用内独占；串口断开或读写失败后自动停止，不自动重连。
- 中文工作台、明暗主题、五种强调色、字体设置和系统托盘。
- 可选原始通信日志，默认关闭，每个连接每次运行分别保存。

## 运行环境

Windows，Python 3.11 或更新版本，需包含 tkinter。运行依赖为 pyserial、pystray 和 Pillow。

## 从源码启动

在 PowerShell 中执行：

```powershell
git clone https://github.com/Xuinme/serial_bridge.git
cd serial_bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r serial_bridge\requirements.txt
.\.venv\Scripts\python.exe serial_bridge\main.py
```

以上命令直接使用虚拟环境内的 Python，无需激活虚拟环境。

## 使用方法

1. 将设备连接到电脑，确认实际串口对应的设备。
2. 启动程序，新建连接并扫描、选择 COM 端口。
3. 设置波特率（常用 `115200`）、TCP 端口和监听地址。
4. 启动连接，通过内置 Telnet 或外部客户端接入对应 TCP 端口。
5. 使用结束后停止连接，或退出程序以释放资源。

本机连接示例：

```powershell
telnet localhost 9999
```

Windows 需启用 Telnet 客户端功能，也可以使用 PuTTY 的 Raw 模式或 MobaXterm 的 Telnet 会话。

监听 `127.0.0.1` 时仅供本机连接；监听 `0.0.0.0` 时可接受其他设备连接，需要允许相应防火墙访问。TCP 服务没有内置认证或加密，适用于可信网络，不应直接暴露到互联网。

## 命令行模式

将 `COM9` 替换为已经确认的设备串口：

```powershell
.\.venv\Scripts\python.exe serial_bridge\main.py --cli COM9 115200 9999
```

按 `Ctrl+C` 停止。

## 数据流与窗口行为

```text
物理设备 ↔ COM 串口 ↔ 串口桥接 ↔ TCP 客户端
                                  ├─ 外部终端
                                  ├─ AI / 原始 TCP 客户端
                                  └─ 内置 Telnet
```

程序不伪造本地输入回显，终端正文以设备真实输出为准。仅对短时间内匹配到的可打印回显进行来源着色，控制字符及 ESC/CSI 序列不参与来源归因。Telnet 的 CRLF 和 CR NUL 输入规范化为单个串口 CR。

最小化到托盘后，正在运行的连接仍继续工作。Pause 只暂停显示，不释放串口。退出程序会停止连接并清理资源。

通信日志可能包含命令、凭据、地址和设备输出，请保存在受控本地目录，不要提交到仓库。

## 目录结构

```text
serial_bridge/
├─ main.py            # 图形界面与命令行入口
├─ bridge.py          # 串口、TCP 和 Telnet 桥接核心
├─ requirements.txt   # 运行依赖
├─ app/               # 会话管理、控制器、配置和日志
├─ ui/                # 工作台、终端、主题与托盘
├─ assets/            # 程序图标
└─ tests/             # 测试及辅助脚本
installer/            # Inno Setup 安装脚本
VERSION               # 产品版本号唯一来源
RELEASE_NOTES_2.3.2.md # 当前版本更新说明
```

## 测试

以下为不访问真实串口的部分测试，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe serial_bridge\tests\test_ansi_parser.py
.\.venv\Scripts\python.exe serial_bridge\tests\test_session_manager.py
.\.venv\Scripts\python.exe serial_bridge\tests\test_tcp_ports.py
.\.venv\Scripts\python.exe serial_bridge\tests\test_traffic_log.py
```

真实设备回显、终端行为及串口热拔插仍需硬件验证。未确认设备身份时，请勿运行真实串口辅助脚本。

## 版本与安装包

版本号由根目录 `VERSION` 统一管理。正式 Windows 安装包采用 Nuitka standalone 与 Inno Setup 构建，后续将在本仓库 Releases 页面发布。

详细更新见 [v2.3.2 更新说明](RELEASE_NOTES_2.3.2.md)。
