# CC Watchdog

**Auto-continue Claude Code after a dropped connection (Windows).** When a session stops with
`API Error: Connection lost mid-response. The response above may be incomplete.`, CC Watchdog types
`继续` ("continue") into that session for you, just as you would by hand.

Claude Code 在终端里遇到「回复到一半断线」时，会停下来等你手动回一句「继续」。CC Watchdog 常驻在托盘里，
盯着本机所有 Claude Code 会话，一旦某个会话因为断线停住，就替你往它的输入框里敲「继续」并回车。

## 为什么需要它

- Claude Code 只会自动重试「还没开始输出就断了」的请求。已经输出了一部分再断，它不会重发（怕把工具调用执行两遍），
  只保留已完成的部分并显示 `... The response above may be incomplete.`。交互模式下只能你自己回 `continue`。
- 官方的 `StopFailure` hook 能感知这种错误，但它的输出和退出码都被忽略，没法让会话继续。
  `asyncRewake` 在 `StopFailure` 上实测（v2.1.304）也唤不醒会话。
- 多个会话并行、人又不在电脑前时，一次网络抖动就能让好几个会话一起停几个小时。

## 工作方式

1. **找会话**：读 `~/.claude/sessions/*.json`，得到每个交互会话的进程、名字、目录和状态。
2. **认断线**：读会话记录 `~/.claude/projects/*/<sessionId>.jsonl` 的末尾。最后一条对话记录如果是
   `isApiErrorMessage` 且 `truncatedAfterOutput` 的报错（或报错文本属于 mid-response 这一类），就算断线。
   余额不足、限流、鉴权失败之类的报错不处理。
3. **发「继续」**：用 Windows 控制台 API（`AttachConsole` + `WriteConsoleInputW`）往该会话所在终端的输入缓冲区
   打字，效果和你手敲完全一样。发之前先读一遍屏幕：输入框里已经有字就不发，免得和你正在打的内容混在一起；
   会话处于忙碌状态时，只有确认输入框在且为空才发。

策略：断线后等 10 秒再发；同一会话若反复断，依次等 10 / 30 / 90 秒，15 分钟内满 3 次就先暂停并弹通知；
超过 1 小时的旧断线不自动处理（界面上可以手动「立即继续」）。

## 要求

- Windows 10 / 11
- Claude Code CLI 运行在终端里（Windows Terminal、PowerShell、cmd 均可）。VS Code 插件里的会话没有控制台，不支持。
- Python 3.11+（界面依赖的 pythonnet 只支持 3.11 及以上）
- 窗口界面使用系统自带的 WebView2（Win11 默认已安装），只在打开窗口时运行，关掉即释放

## 安装

```powershell
git clone https://github.com/Xiaooolong/cc-watchdog.git
cd cc-watchdog
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw -m ccwatchdog --install
```

`--install` 会在开始菜单加一个「CC Watchdog」入口，并设为开机自启（开机后只挂在托盘，不弹窗口）。
之后从开始菜单或托盘图标打开界面；托盘菜单里可以暂停自动继续、关闭开机自启、退出。

## 界面

- 列出本机所有活跃的 Claude Code 会话，以及各自的状态：运行中、空闲、断线倒计时、已发送「继续」、已暂停。
- 每个会话右侧的开关控制它是否自动继续，默认全部开启；顶部总开关可以整体暂停。
- 「立即继续」按钮可以手动给某个断线的会话发「继续」，不受等待时间和次数限制。
- 「最近动态」记录每一次自动或手动发送。

## 配置与数据

都在 `%APPDATA%\cc-watchdog\`：

- `config.json`：`message`（发送的内容，默认「继续」，可改成 `continue`）、`delay`（断线后等待秒数，默认 10）、`max_tries`（窗口期内最多次数，默认 3）、
  `window`（窗口期秒数，默认 900）、`max_age`（超过多少秒的断线不自动处理，默认 3600）
- `events.log`：发送记录

## 局限

- 依赖 Claude Code 的内部文件格式（会话登记、会话记录字段）和终端界面（输入框上下的分隔线）。Claude Code 升级后
  如果这些变了，可能需要跟着调整。
- 以管理员身份运行的终端里的会话，普通权限的 CC Watchdog 连不上，界面上会显示「连不上它的终端」。

## 开发

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests
```

- `dev/cutproxy.py`：本地反向代理，能在流式响应输出到一半时主动断开，用来稳定复现 `Connection lost mid-response`。
- `dev/gen_icon.py`、`dev/make_icon.py`：生成并裁切图标。

## License

MIT
