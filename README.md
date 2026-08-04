# key-cli

`key-cli` 是 Clavis 唯一拥有的 `key` 普通 CLI。它不依赖 quickshell 的 C++ library，
也不是 daemon，不创建常驻 socket 服务。

职责包括 Shell 启动和 dev/native 协调、IPC 路由、audio/record/cast/clipboard/weather
命令、release/current/rollback 管理、日志和实例记录，以及受控的 Clavis 官方组件
source provider。系统监测由独立的 `keytop` 提供；`key top` 仅作兼容转发。

## 构建、运行和安装

```bash
./setup.sh doctor
./setup.sh configure
./setup.sh build
./setup.sh test
./setup.sh run -- version --json
```

默认源码安装使用 `/usr/local/bin/key`、`/usr/local/libexec/key/` 和
`/usr/local/share/key-cli/`。所有路径由 CMake GNUInstallDirs 和 XDG 环境变量决定，
可用于 AUR：

```bash
CMAKE_INSTALL_PREFIX=/usr DESTDIR="$pkgdir" ./setup.sh install
```

卸载使用安装 manifest 精确删除。本仓库的测试使用临时 HOME/XDG；不会覆盖用户的
Niri、Zsh、Fcitx5 或当前 Clavis release。

## 组件 provider

第一阶段只允许明确 registry 中的 `keytop`、`clavis-zsh-theme` 和
`clavis-fcitx5-theme`。`--source` 可以指定本地 checkout；更新使用
`git pull --ff-only`，检测到本地修改会拒绝，绝不自动执行 `git reset --hard` 或
`git clean`。任意 URL 不会被当作安装脚本执行。

## 与 Clavis Shell 的关系

Shell release 只包含 QML、assets、保留的 native QML plugins 和 runtime metadata。
`key-cli` 注册/验证 `.partial` runtime，再原子切换 `current`；release 内没有第二份
`bin/key`。Shell 可通过 `$CLAVIS_KEY ipc call ...` 调用稳定 CLI。
`key shell` 会把当前实际执行的 `key` 绝对路径传入 Shell；这避免系统安装、源码开发
构建和旧用户级 launcher 并存时误选已经失效的 release 内 `bin/key`。开发构建的天气
provider 也从 `key` 自身位置发现，不依赖 Shell 的当前工作目录。

## Zsh 主题委托

`key-cli` 不读取或解析 Zsh 配置，也不保存主题状态；它只把参数安全地透传给已安装的
`zsh-theme` 命令：

```bash
key theme zsh list
key theme zsh status
key theme zsh status --json
key theme zsh show path
key theme zsh hide git language
key theme zsh toggle duration
key theme zsh reset
```

未安装 `clavis-zsh-theme` 时会明确提示安装组件。Keytop 仍由 Quickshell 直接调用，
不会通过 `key` 重新实现系统监测。

Shell completion 位于安装后的 `share/key-cli/completions/`，包含 Bash 的
`key.bash` 和 Zsh 的 `_key`；发行版可以将它们分别接入自己的 completion 目录。

## 未来 AUR

AUR 包可将 `key-cli` 安装到 `/usr`，并把 `keytop`、Shell 和两个主题作为独立包依赖
或可选组件。在线 update provider 在未有签名 artifact 实现前保持拒绝状态，不伪装
成 pacman 或 AUR helper。
