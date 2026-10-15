# UU + BetterDisplay 自动切屏

在 Mac 上用 UU 远程办公时，提前准备一个清晰的虚拟屏，回到电脑旁再恢复物理显示器。

适合这样的场景：Mac mini 接着多块显示器，离开时锁屏，再从另一台 Mac 用 UU 连入；不希望远程窗口散落到多块物理屏，也不想每天拔线。

这是一个 **macOS 用户级脚本项目**，不是 UU 或 BetterDisplay 官方插件。需要自行配置、编译和授权；首次使用请在电脑旁验证。仓库只发布源码和示例，不含个人配置、日志、截图、预编译应用或第三方应用源码。

## 工作方式

| 场景 | 行为 |
|---|---|
| 本机锁屏 | 开启已有虚拟屏、设为主屏，断开物理屏 |
| UU 远程操作、远程解锁 | 保持虚拟屏，不因远程输入恢复物理屏 |
| 本机实体键盘／鼠标操作 | 先恢复物理屏和原主屏，再关闭虚拟屏 |
| 本地恢复后 UU 仍连接 | 本地使用优先，不重新关屏 |
| 本地唤醒但未解锁，随后再次熄屏 | 确认物理屏先全亮、后全部休眠，再重新准备虚拟屏 |
| 检测到远程活动后断开 | 明确断开约 5 秒后恢复；解锁后未知状态宽限 12 秒 |
| 锁屏且连接状态不可读 | 检测到远程活动后，未知状态宽限 120 秒；尚未连入时保持等待 |
| 切换失败、输入监听失效、程序崩溃 | 恢复物理屏并暂停；恢复失败保留虚拟屏和恢复记录、继续重试 |

切换还需 BetterDisplay 执行和校验，实测通常十几秒，并非瞬间。再次熄屏后的切换会短暂唤醒显示系统，物理屏可能闪亮一下。掉线保护依赖 UU 状态和输入来源判断，不是 UU 官方连接回调。

## 1. 准备

- macOS；目前仅有一套 Apple Silicon Mac + 多显示器环境的实际验证，其他机型／系统未保证。
- 安装 [BetterDisplay](https://github.com/waydabber/BetterDisplay) 和 UU 远程，位于 `/Applications`。
- BetterDisplay 需要相应 Pro 功能或试用资格；本项目不包含许可证。
- 安装 Xcode Command Line Tools：`xcode-select --install`。需要可用的 `python3`、Swift 编译器和 codesign。Python 需 3.9 或更高版本。
- 禁用其他自动断屏／虚拟屏工具，避免互相抢控制权。
- 所有要恢复的物理显示器必须接好。**当前批量命令会断开除虚拟主屏外的全部显示器，恢复时会连接 BetterDisplay 可连接的显示器；不要用在需要选择性保留其他屏幕的环境。**

## 2. 先配置 BetterDisplay

1. 在 BetterDisplay 创建一个虚拟屏，手动连接一次，调整到适合远程设备的分辨率和 HiDPI，确认 UU 中显示正常。本脚本复用这个虚拟屏，不创建、不修改其分辨率。
2. 在显示器连接相关设置中，开启“允许断开所有已连接的显示器”。
3. 关闭“无已连接显示器时自动连接所有显示器”，否则最后一块物理屏断开后可能全部亮起。
4. 保留“启动时自动连接所有显示器”和“退出时自动连接所有显示器”，作为恢复途径。
5. 开启 BetterDisplay 登录启动及命令行集成。菜单名称可能随版本变化。
6. 如果重连后物理屏分辨率变化，先设好分辨率和 HiDPI，再启用该屏的“分辨率保护”。

先备好恢复方式：退出 BetterDisplay 应重新连接物理屏。确认这一点后再启用自动切换。

## 3. 下载并填写本地配置

```sh
git clone https://github.com/GrassGan/uu-betterdisplay-switch.git
cd uu-betterdisplay-switch
cp config.example.json config.json
```

读取 BetterDisplay 设备标识（只在本地查看，不要把完整输出发到公开 Issue）：

```sh
/Applications/BetterDisplay.app/Contents/MacOS/BetterDisplay get -identifiers
```

对照显示器名称，将 **每块物理屏** 的 UUID，以及十进制 vendor、model、serial 填入 `config.json`；将虚拟屏定义的 tagID 填到 `virtual_tag_id`。不要填虚拟屏的动态 displayID。

示意格式（数字和 UUID 均为虚构，不能直接使用）：

```json
{
  "physical_displays": [
    {
      "uuid": "11111111-1111-1111-1111-111111111111",
      "hardware_key": "1234:5678:0"
    }
  ],
  "virtual_tag_id": 1
}
```

`hardware_key` 是 `vendor:model:serial`。有多块屏就添加多个对象；同型号且无唯一序列号、无法区分的显示器目前不支持。示例配置故意使用无效 UUID，避免误装。`config.json` 已被 Git 忽略，**不要强制提交它**。

## 4. 编译、安装（默认暂停）

```sh
python3 manage.py install --config config.json
```

安装器先检查物理屏已连接、虚拟屏标识可读取，编译三个小型 Swift 工具，再注册当前用户的 LaunchAgent。它不会立即关闭屏幕。

安装目录：

```text
~/Library/Application Support/UUBetterDisplaySwitch/
```

安装器会在该目录创建 `Restore and Pause.command`、`Enable.command`、`Status.command` 三个入口，建议把恢复入口放到容易找到的位置。

## 5. 授权并启用

在系统设置 → 隐私与安全中添加：

- **辅助功能／设备控制和数据访问**：安装目录中的 `UU Display Switch.app`。用于读取 UU 中文窗口中的被控连接状态。
- **输入监控**：安装目录中的 `input-events`。用于区分实体输入与远程注入输入。

文件选择器可按 `Command+Shift+G`，输入安装目录路径。macOS 如要求密码，请直接在系统对话框输入。不要把密码发到聊天、日志或 Issue。

授权后：

```sh
python3 manage.py restart
# 等约 10 秒
python3 manage.py status
```

确认 `input_listener: true`；UU 未连接时应能看到 `uu: "disconnected"`。`permission-required` 表示状态探针未获授权；`unknown` 表示窗口状态暂时无法识别。保持 UU 主窗口存在，可以最小化。

确认监听及 UU 检测正常后：

```sh
python3 manage.py resume
```

第一次建议在本机依次验证：锁屏 → 等约 20 秒 → 只移动实体鼠标 → 确认物理屏恢复；再验证 UU 连接、远程解锁和断线恢复。最后验证本地唤醒但不解锁、自然再次熄屏的流程。不要把“最终屏幕亮了”直接视为全部成功，要同时检查日志是否发生了失败兜底。

## 6. 日常使用和自启动

- 离开时按 `Control+Command+Q` 锁屏，程序预先准备虚拟屏。
- 回来操作实体键鼠即可恢复物理屏。刚锁屏后的 2 秒内忽略实体事件，避免松开锁屏组合键立即触发恢复。
- **登录 macOS 后**服务自动启动，启动时会在后台打开 UU 界面；BetterDisplay 自身也应开启登录启动。
- 不支持 FileVault 开机前界面；登录前不运行该用户的自动切屏。
- 服务启动不追溯已经发生的锁屏，下一次锁屏才触发。
- 暂停状态会持久保存；失败或手动暂停后，重启不会自动解除保护。

查看状态／主动恢复：

```sh
python3 manage.py status
python3 manage.py restore   # 恢复物理屏并暂停
python3 manage.py resume    # 恢复后解除暂停，下次锁屏生效
```

日志位于安装目录 `state/switcher.log` 和 `state/launchd.log`。正常切屏日志会包含请求来源、成功或错误；`physical_sleep_after_local_return` 表示未解锁再次熄屏分支。运行日志不应公开上传原件，先脱敏。

## 7. 故障排查

| 问题 | 检查 |
|---|---|
| 不切屏 | 是否暂停、输入监听是否正常、是否发生新的锁屏通知 |
| 远程输入误恢复 | 暂停自动化；系统／UU／输入驱动更新可能改变事件来源，需重新验证 |
| 所有屏自动亮回 | 检查 BetterDisplay“无已连接显示器时自动连接所有显示器”是否关闭 |
| 本地屏无法恢复 | 使用恢复入口；退出 BetterDisplay；必要时重新插拔显示器。保留恢复文件，不要先删除安装目录 |
| 改权限后仍失败 | 重启服务，再检查状态；重新编译可能需要重新授权 |
| 物理分辨率重置 | 在 BetterDisplay 调整并开启分辨率保护 |
| 虚拟屏或显示器更换 | 恢复并暂停；更新安装目录中的本地 config.json，重启后重新验证 |
| UU 一直 unknown | 确认主窗口存在、中文界面及辅助功能权限；锁屏时 unknown 是已知限制 |

输入来源规则是在实际 UU 环境中观察得到的，不是认证机制。锁屏通知不是稳定公开 API；系统更新可能影响它。锁屏界面长时间没有远程输入可能触发 120 秒保护。

## 8. 卸载和更新

```sh
python3 manage.py uninstall
```

该命令先恢复物理屏并暂停，再移除后台任务；恢复失败会停止卸载。为保留兜底，它不删除安装目录。确认显示正常、保存好配置后，可在 Finder 手动删除该目录，并从隐私设置移除相应授权。

更新目前采用恢复／卸载／备份配置／删除旧安装目录／重新安装。重编译后可能需要重新授予权限。安装器不会覆盖已有安装，避免覆盖恢复记录或正在运行的工具。

## 开发和隐私

```sh
python3 -m unittest -v test_switcher
python3 manage.py build   # 仅编译到 build/，不安装、不切屏
```

Python 仅使用标准库。Swift 使用系统框架。不上传遥测，不记录按键内容、密码或鼠标坐标；监听器只向本机父进程传递 local/remote、锁屏和心跳事件。UU 和 BetterDisplay 本身的网络行为不属于本脚本。

请勿提交本地 `config.json`、应用构建产物、日志、截图、设备序列号或个人路径。报告问题时只提供脱敏后的错误和软件版本。

MIT 许可证。BetterDisplay 和 UU 的名称、软件及许可归各自权利人所有。
