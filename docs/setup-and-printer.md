# 部署与打印机连接说明

本文说明怎么启动服务、服务器需要安装什么、Linux 工作站怎么连接打印机。

## 1. 机器角色

推荐分成两台机器：

- **Server 机器**：跑 Web 服务、React 前端、SQLite、文件存储和任务队列。可以是云服务器、办公室内网机器或同一台 Linux 工作站。
- **Linux 打印工作站**：跑 Agent，能访问局域网打印机，负责文件转换、CUPS 打印机查询和真实打印。

只有 Linux 打印工作站必须安装 CUPS、打印机驱动、LibreOffice 等打印/转换依赖。Server 如果不直接打印，不需要安装 CUPS。

## 2. Server 机器依赖

最低要求：

- Python 3.11 或更高版本。
- Node.js 20.19 或更高版本和 npm，用于构建 React 前端。
- 能保存本地文件和 SQLite 数据库的磁盘空间。

Ubuntu / Debian 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm
```

macOS / Homebrew 示例：

```bash
brew install python@3.12 node
```

安装项目依赖：

Conda 方式：

```bash
cd /path/to/打印机远程打印
conda env create -f environment.yml
conda activate print-gateway
which node
node -v
npm install
npm run build
```

venv 方式：

```bash
cd /path/to/打印机远程打印
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
which node
node -v
npm install
npm run build
```

如果 `node -v` 显示 `v16...`，当前 shell 没有使用满足 Vite 要求的 Node。使用 conda 时应先 `conda activate print-gateway`；使用系统环境时需要安装 Node.js `20.19+` 或 `22.12+`。

启动 Server：

```bash
PRINT_GATEWAY_AGENT_TOKEN=change-this-agent-token \
PRINT_GATEWAY_HOST=0.0.0.0 \
PRINT_GATEWAY_PORT=8000 \
python main.py server
```

浏览器访问：

```text
http://<server-ip>:8000
```

Web 页面不需要登录。上线前必须修改 `PRINT_GATEWAY_AGENT_TOKEN`，Agent 和 Server 必须使用同一个值。

## 3. Linux 打印工作站依赖

Ubuntu / Debian 推荐安装：

```bash
sudo apt update
sudo apt install -y \
  cups \
  cups-client \
  cups-filters \
  avahi-daemon \
  printer-driver-all \
  libreoffice \
  poppler-utils \
  imagemagick \
  ghostscript
```

启动 CUPS 和 mDNS 发现：

```bash
sudo systemctl enable --now cups
sudo systemctl enable --now avahi-daemon
```

确认 CUPS 可用：

```bash
lpstat -r
```

如果输出类似 `scheduler is running`，说明 CUPS 服务已启动。

## 4. 连接网络打印机

优先使用 IPP Everywhere / AirPrint。假设打印机 IP 是 `192.168.1.50`：

```bash
sudo lpadmin -p office_printer -E \
  -v ipp://192.168.1.50/ipp/print \
  -m everywhere
```

设置默认打印机：

```bash
sudo lpoptions -d office_printer
```

查看打印机：

```bash
lpstat -p
lpstat -v
lpoptions -p office_printer -l
```

打印测试页：

```bash
lp -d office_printer /usr/share/cups/data/testprint
```

如果 IPP 不可用，但打印机支持 9100 端口：

```bash
sudo lpadmin -p office_printer -E \
  -v socket://192.168.1.50:9100 \
  -m everywhere
```

如果厂商要求 PPD 或专用驱动：

```bash
sudo lpadmin -p office_printer -E \
  -v socket://192.168.1.50:9100 \
  -P /path/to/printer.ppd
```

也可以打开 CUPS 管理页面：

```text
http://localhost:631
```

## 5. USB 打印机

连接 USB 打印机后查看设备：

```bash
lpinfo -v
```

如果看到类似 `usb://...` 的地址，用它添加：

```bash
sudo lpadmin -p usb_printer -E \
  -v 'usb://<device-uri>' \
  -m everywhere
```

如果 `everywhere` 不支持该设备，需要安装厂商 Linux 驱动或 PPD。

## 6. 启动 Agent

Agent 运行在能打印的 Linux 工作站上。

```bash
cd /path/to/打印机远程打印
conda activate print-gateway

PRINT_GATEWAY_AGENT_TOKEN=change-this-agent-token \
python main.py agent \
  --server http://<server-ip>:8000 \
  --agent-id linux-workstation
```

只测试一轮：

```bash
PRINT_GATEWAY_AGENT_TOKEN=change-this-agent-token \
python main.py agent \
  --server http://<server-ip>:8000 \
  --agent-id linux-workstation \
  --once
```

Agent 启动时会：

- 向 Server 注册。
- 同步 CUPS 打印机列表。
- 轮询待转换或待打印任务。
- 调用 LibreOffice / ImageMagick / Poppler 转 PDF 和预览。
- 调用 `lp` 提交打印任务。

## 7. 常用环境变量

Server：

```bash
PRINT_GATEWAY_AGENT_TOKEN=change-this-agent-token
PRINT_GATEWAY_HOST=0.0.0.0
PRINT_GATEWAY_PORT=8000
PRINT_GATEWAY_DATA=/var/lib/print-gateway
PRINT_GATEWAY_MAX_UPLOAD_BYTES=20971520
```

`PRINT_GATEWAY_MAX_UPLOAD_BYTES` 控制单个上传文件的大小上限，默认 20 MB（`20971520` 字节）。超过上限会返回 `413`。

Agent：

```bash
PRINT_GATEWAY_AGENT_TOKEN=change-this-agent-token
PRINT_GATEWAY_SERVER=http://<server-ip>:8000
PRINT_GATEWAY_AGENT_ID=linux-workstation
```

## 8. 最小验收流程

1. 在 Linux 工作站执行 `lp -d office_printer /usr/share/cups/data/testprint`，确认本机能直接打印。
2. 启动 Server，浏览器打开 `http://<server-ip>:8000`。
3. 启动 Agent，确认 Server 页面里能看到已同步打印机。
4. 上传 PDF。
5. 等待任务进入可确认状态。
6. 选择打印机和参数，点击确认打印。
7. 确认任务状态、CUPS job id、事件日志和文件清理状态。

## 9. 排错

看不到打印机：

```bash
lpstat -p
lpinfo -v
sudo systemctl status cups
```

不能打印：

```bash
lp -d office_printer /usr/share/cups/data/testprint
lpstat -o
lpstat -W completed -o
```

Office 转 PDF 失败：

```bash
which libreoffice
which soffice
libreoffice --headless --version
```

图片或预览失败：

```bash
which magick
which convert
which pdftoppm
```

Agent 连不上 Server：

```bash
curl http://<server-ip>:8000/api/tasks
```

返回 JSON 说明 Server 可访问。确认 Agent 的 `PRINT_GATEWAY_AGENT_TOKEN` 和 Server 的 `PRINT_GATEWAY_AGENT_TOKEN` 完全一致。
