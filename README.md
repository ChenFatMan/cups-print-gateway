# Linux Print Gateway

一个最小可运行的远程打印网关 MVP：Web Server 负责上传、任务状态、文件存储和 Agent API；Linux Agent 主动轮询 Server，调用本机转换工具和 CUPS 完成真实打印。

## 本地安装

Conda 方式：

```bash
conda env create -f environment.yml
conda activate print-gateway
node -v
npm install
npm run build
```

venv 方式：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
node -v
npm install
npm run build
```

`npm run build` 要求 Node.js `20.19+`。如果看到 `You are using Node.js 16...` 或 `CustomEvent is not defined`，说明当前 shell 没有使用 conda 环境里的 Node，先执行 `conda activate print-gateway`，再用 `which node && node -v` 确认版本。

完整部署、依赖安装、Linux 打印机连接和排错步骤见：

[docs/setup-and-printer.md](docs/setup-and-printer.md)

## 启动 Server

```bash
PRINT_GATEWAY_AGENT_TOKEN=dev-agent-token \
python main.py server
```

默认监听 `http://127.0.0.1:8000`。

前端使用 React/Vite，构建产物输出到 `src/print_gateway/web/dist`，由 FastAPI 直接托管。修改前端后需要重新执行：

```bash
npm run build
```

## 启动 Agent

```bash
PRINT_GATEWAY_AGENT_TOKEN=dev-agent-token \
python main.py agent --server http://127.0.0.1:8000
```

只跑一轮便于调试：

```bash
python main.py agent --server http://127.0.0.1:8000 --once
```

## 配置（环境变量）

Server 和 Agent 都通过环境变量配置，常用项：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `PRINT_GATEWAY_AGENT_TOKEN` | Agent 与 Server 之间的共享令牌，两端必须一致 | `dev-agent-token` |
| `PRINT_GATEWAY_HOST` | Server 监听地址 | `127.0.0.1` |
| `PRINT_GATEWAY_PORT` | Server 监听端口 | `8000` |
| `PRINT_GATEWAY_DATA` | 数据目录（SQLite 与文件存储） | `data` |
| `PRINT_GATEWAY_MAX_UPLOAD_BYTES` | 单个上传文件大小上限（字节） | `20971520`（20 MB） |
| `PRINT_GATEWAY_LEASE_SECONDS` | 任务租约有效期 | `300` |
| `PRINT_GATEWAY_SERVER` | Agent 连接的 Server 地址 | `http://127.0.0.1:8000` |
| `PRINT_GATEWAY_AGENT_ID` | Agent 标识 | `linux-workstation` |

上传超过大小上限会返回 `413`，且不会把整份文件读入内存。上线前务必修改 `PRINT_GATEWAY_AGENT_TOKEN`。

## Linux 打印机检查

Server 不直接连接打印机。Linux 工作站必须先能通过 CUPS 打印：

```bash
lpstat -p
lpoptions -p <printer_name> -l
lp -d <printer_name> /usr/share/cups/data/testprint
```

## 验证

```bash
python -m pytest
npm run build
```
