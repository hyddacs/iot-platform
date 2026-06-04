# 工业物联网设备监控平台项目说明与迁移部署文档

本文档用于在只有源代码的情况下，从零配置 Python、MySQL、Vue 环境，初始化数据库，启动项目，并将项目迁移到新的服务器或开发机器。

## 1. 项目概览

本项目是一个工业物联网设备监控平台，采用前后端分离架构：

- 后端：FastAPI + SQLAlchemy + MySQL + PyMySQL
- 前端：Vue 3 + Vue Router + Vite
- 数据库：MySQL，使用 SQLAlchemy ORM 建模
- 通信协议：HTTP 主动上报、Modbus TCP 轮询
- 实时能力：WebSocket 推送设备数据、告警、设备在线状态

当前源码路径确认：

- 前端源码位于当前项目目录下的 `./frontend`。
- Python 依赖文件位于当前项目目录下的 `./requirements.txt`。
- 注意文件名是 `requirements.txt`，不是 `requirments.txt`。如果迁移包中出现拼写错误，应先改回 `requirements.txt`，否则 `pip install -r requirements.txt` 会找不到文件。

项目根目录结构：

```text
iot-platform/
├── app/                         # FastAPI 后端源码
│   ├── main.py                  # 应用入口、生命周期、路由注册
│   ├── config.py                # 环境变量和配置
│   ├── database.py              # 数据库连接、建表、兼容字段/索引补齐
│   ├── models.py                # SQLAlchemy ORM 数据模型
│   ├── schemas.py               # Pydantic 请求/响应模型
│   ├── auth.py                  # JWT 登录认证
│   ├── device_secret_manager.py # HTTP 设备密钥缓存与验证
│   ├── websocket_manager.py     # WebSocket 连接和广播
│   ├── sync_modbus_poller.py    # Modbus TCP 同步轮询器
│   ├── routes/                  # API 路由
│   ├── services/                # 业务服务
│   └── utils/                   # 安全、限流等工具
├── frontend/                    # Vue 前端源码
│   ├── src/
│   ├── vite.config.js           # Vite 配置和 /api 代理
│   ├── package.json             # 前端依赖和脚本
│   └── dist/                    # 前端构建产物
├── requirements.txt             # Python 依赖
├── .env                         # 后端本地环境变量
└── README.md
```

## 2. 核心功能

### 2.1 用户认证

相关文件：

- `app/routes/auth.py`
- `app/auth.py`
- `app/models.py` 中的 `User`

功能：

- 用户注册：`POST /api/auth/register`
- 用户登录：`POST /api/auth/token`
- 当前用户信息：`GET /api/auth/me`
- 使用 JWT Bearer Token 保护设备管理、数据查询、告警、监控接口

登录后前端会把 Token 放入请求头：

```http
Authorization: Bearer <access_token>
```

### 2.2 设备管理

相关文件：

- `app/routes/devices.py`
- `app/services/devices.py`
- `app/models.py` 中的 `Device`、`Sensor`

功能：

- 创建设备：`POST /api/devices`
- 设备列表分页/筛选：`GET /api/devices`
- 设备详情：`GET /api/devices/{device_id}`
- 更新设备：`PUT /api/devices/{device_id}`
- 删除设备：`DELETE /api/devices/{device_id}`
- 传感器新增、列表、更新、删除：
  - `POST /api/devices/{device_id}/sensors`
  - `GET /api/devices/{device_id}/sensors`
  - `PUT /api/devices/{device_id}/sensors/{sensor_id}`
  - `DELETE /api/devices/{device_id}/sensors/{sensor_id}`

设备协议类型：

```text
modbus_gateway  # Modbus TCP 网关轮询设备
http_active     # HTTP 主动上报设备
```

设备删除采用软删除：

- `is_active = false`
- `online_status = false`
- `deleted_at = 当前时间`

历史数据和告警记录会保留。

### 2.3 HTTP 主动上报

相关文件：

- `app/routes/data.py`
- `app/device_secret_manager.py`
- `app/services/data_ingest.py`

接口：

```http
POST /api/data/report/http
```

请求示例：

```json
{
  "device_id": "device_001",
  "device_secret": "设备密钥",
  "timestamp": "2026-06-04T10:00:00",
  "data": {
    "temperature": 25.5,
    "humidity": 60.2
  }
}
```

处理流程：

1. 根据 `device_id` 做限流，默认 60 秒 100 次。
2. 使用 `device_secret_manager` 校验设备密钥。
3. 查询设备是否存在、是否未归档、是否启用。
4. 只接收已配置且 `is_monitored = true` 的传感器字段。
5. 字段值必须是数值，布尔值和字符串会被拒绝。
6. 写入 `device_data` 原始 JSON。
7. 写入 `sensor_data_points` 点位明细。
8. 更新分钟聚合表 `sensor_data_agg_min`。
9. 更新小时聚合表 `sensor_data_agg_hour`。
10. 根据上下限阈值生成或恢复告警。
11. 通过 WebSocket 推送设备数据和告警。

### 2.4 Modbus TCP 轮询

相关文件：

- `app/sync_modbus_poller.py`
- `app/main.py`
- `app/services/alerts.py`

启动后端时，应用生命周期会自动启动 Modbus 轮询线程：

```python
modbus_poller.start(get_db_session=get_db_context)
```

轮询逻辑：

1. 查询协议为 `modbus_gateway`、启用且未归档的设备。
2. 查询这些设备下已监控、配置了 `channel_no` 的传感器。
3. 按 `gateway_ip` 分组，建立或复用 `ModbusTcpClient`。
4. 从 Holding Registers 的 0 地址开始批量读取。
5. 使用传感器的 `channel_no` 映射寄存器位置。
6. 使用 `coeff` 系数换算实际值。
7. 写入原始数据、点位明细和聚合数据。
8. 生成或恢复告警。
9. 通过 WebSocket 推送。

测试设备：

- 如果设备 `is_test_device = true`，轮询器不连接真实网关。
- 系统会生成阈值范围内的模拟数据。
- 可通过 `POST /api/monitor/poller/test-data/{device_id}` 手动插入一条测试数据。

### 2.5 告警生命周期

相关文件：

- `app/services/alerts.py`
- `app/routes/data.py`
- `app/models.py` 中的 `Alert`

告警类型：

```text
LOWER_LIMIT  # 低于下限
UPPER_LIMIT  # 高于上限
```

告警级别：

```text
INFO
WARNING
CRITICAL
```

告警状态：

```text
active     # 正在告警
recovered  # 数据已恢复正常，等待人工确认
resolved   # 已人工处理
```

核心规则：

- 同一设备、同一字段、同一告警类型，在未处理状态下只保留一条 active 告警。
- 持续超限不会刷大量新记录，而是更新当前值、最后触发时间和触发次数。
- 字段恢复到阈值范围内时，active 告警会变为 recovered。
- 人工确认后变为 resolved。

告警接口：

- `GET /api/data/alerts/page`
- `GET /api/data/alerts/summary`
- `GET /api/data/stats`

### 2.6 历史数据与聚合查询

相关文件：

- `app/routes/data.py`
- `app/services/data_ingest.py`

接口：

- 原始历史数据：`GET /api/data/historical`
- 聚合历史数据：`GET /api/data/historical/aggregated`
- 单设备最新数据：`GET /api/data/latest/{device_id}`
- 批量设备最新数据：`GET /api/data/latest`

聚合策略：

- 每次入库时同步更新分钟表 `sensor_data_agg_min`。
- 每次入库时同步更新小时表 `sensor_data_agg_hour`。
- 查询参数 `bucket=minute` 时读取分钟聚合。
- 查询参数 `bucket=hour` 时读取小时聚合。
- 查询参数 `bucket=auto` 时，如果查询时间跨度大于等于 3 天，自动读取小时聚合，否则读取分钟聚合。

### 2.7 WebSocket 实时推送

相关文件：

- `app/routes/ws.py`
- `app/websocket_manager.py`
- `frontend/src/lib/ws.js`

连接地址：

```text
ws://<host>:<port>/api/ws
ws://<host>:<port>/api/ws?device_id=<device_id>
```

消息类型：

```json
{
  "type": "device_data",
  "device_id": "device_001",
  "data": {},
  "timestamp": "2026-06-04T10:00:00"
}
```

```json
{
  "type": "alert",
  "alert": {},
  "timestamp": "2026-06-04T10:00:00"
}
```

```json
{
  "type": "device_status",
  "device_id": "device_001",
  "online": true,
  "timestamp": "2026-06-04T10:00:00"
}
```

前端每 15 秒发送一次：

```json
{
  "type": "ping"
}
```

后端返回：

```json
{
  "type": "pong",
  "timestamp": "..."
}
```

### 2.8 系统监控

相关文件：

- `app/routes/monitor.py`

接口：

- `GET /api/monitor/poller`
- `POST /api/monitor/poller/test-data/{device_id}`

监控内容：

- Modbus 轮询器状态
- 轮询间隔
- 最近轮询时间
- 最近错误
- 网关连接状态
- CPU 使用率
- 内存使用率
- 当前线程数量

## 3. 后端代码说明

### 3.1 `app/main.py`

职责：

- 创建 FastAPI 应用
- 注册 CORS
- 注册 API 路由
- 管理应用生命周期
- 启动数据库初始化
- 启动设备密钥缓存
- 启动 Modbus 轮询器
- 启动设备在线状态检查线程

启动生命周期中的关键操作：

```text
init_db()
device_secret_manager.load_all_devices(db)
modbus_poller.start(...)
check_device_status_periodically(...)
```

设备在线状态检查每 60 秒执行一次，根据：

```text
当前时间 - last_active_time < offline_threshold
```

判断设备是否在线。

### 3.2 `app/config.py`

职责：

- 读取 `.env`
- 定义应用配置、数据库配置、JWT 配置、Modbus 配置、设备超时配置

关键配置：

```text
DATABASE_URL=mysql+pymysql://user:password@host:3306/iot_platform
SECRET_KEY=change-this-secret
MODBUS_POLL_INTERVAL=3
MODBUS_TIMEOUT=2
MODBUS_PORT=502
DEVICE_TIMEOUT=900
```

### 3.3 `app/database.py`

职责：

- 创建 SQLAlchemy engine
- 创建数据库 session
- 提供 FastAPI 依赖 `get_db`
- 提供后台线程上下文 `get_db_context`
- 初始化数据库表
- 为旧开发库补齐兼容字段和索引

启动时会执行：

```python
Base.metadata.create_all(bind=engine)
ensure_compatible_columns()
ensure_compatible_indexes()
```

正式生产环境建议后续引入 Alembic 管理迁移，目前项目直接依赖 ORM 自动建表。

### 3.4 `app/models.py`

职责：

- 定义所有数据库表结构
- 定义索引、外键、检查约束
- 定义协议、告警类型、告警级别枚举

主要模型：

- `User`：用户表
- `Device`：设备表
- `Sensor`：传感器配置表
- `DeviceData`：设备原始数据表
- `SensorDataPoint`：点位明细数据表
- `SensorDataAggMin`：分钟聚合表
- `SensorDataAggHour`：小时聚合表
- `Alert`：告警记录表

### 3.5 `app/schemas.py`

职责：

- 定义 API 请求和响应数据结构
- 使用 Pydantic 校验输入

典型校验：

- 用户名长度
- 邮箱格式
- 密码最小长度
- 设备协议类型只能是 `modbus_gateway` 或 `http_active`
- Modbus 非测试设备必须提供 `gateway_ip` 和 `slave_id`
- 传感器下限不能大于上限
- 历史查询分页参数限制

### 3.6 `app/services/data_ingest.py`

职责：

- 解析设备上报时间
- 加载监控传感器
- 按传感器配置清洗上报字段
- 写入原始数据、点位数据和聚合数据
- 调用告警服务

重点函数：

- `parse_reported_time`
- `load_monitored_sensors`
- `normalize_payload_by_sensors`
- `persist_device_payload`
- `persist_sensor_points_and_aggregates`
- `upsert_aggregate`

聚合表使用 MySQL 的 `ON DUPLICATE KEY UPDATE` 做增量聚合。

### 3.7 `app/services/alerts.py`

职责：

- 根据上下限阈值生成告警
- 对 active 告警做去重更新
- 将恢复正常的告警标记为 recovered

重点函数：

- `build_threshold_alerts`
- `upsert_active_alerts`
- `resolve_recovered_alerts`

### 3.8 `app/sync_modbus_poller.py`

职责：

- 后台线程周期性轮询 Modbus TCP 设备
- 维护网关连接缓存
- 模拟测试设备数据
- 批量写入数据和告警
- 推送 WebSocket 消息

轮询配置来自 `.env`：

```text
MODBUS_POLL_INTERVAL
MODBUS_TIMEOUT
MODBUS_PORT
```

### 3.9 前端代码

相关目录：

```text
frontend/src/
├── App.vue
├── main.js
├── router/index.js
├── pages/
│   ├── LoginPage.vue
│   ├── DashboardPage.vue
│   ├── DevicesPage.vue
│   ├── HistoryPage.vue
│   └── AlertsPage.vue
└── lib/
    ├── api.js
    ├── auth.js
    └── ws.js
```

前端页面：

- 登录页：用户登录
- 仪表盘：平台统计、设备快照、实时状态
- 设备页：设备和传感器管理
- 历史页：历史数据和趋势查询
- 告警页：告警列表、筛选、处理

`frontend/vite.config.js` 中已经配置开发代理：

```js
proxy: {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    ws: true
  }
}
```

开发模式下前端请求 `/api/...` 会自动转发到后端 `127.0.0.1:8000`。

## 4. 从零安装配置环境

以下步骤以 Linux 服务器或 Ubuntu/Debian 开发环境为例。Windows 可使用 WSL2 执行类似流程。

### 4.1 基础软件要求

建议版本：

- Python：3.10 或 3.11
- MySQL：8.0+
- Node.js：18+ 或 20+
- npm：随 Node.js 安装

检查版本：

```bash
python3 --version
mysql --version
node --version
npm --version
```

### 4.2 安装系统依赖

Ubuntu/Debian 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip mysql-server nodejs npm
```

如果系统源里的 Node.js 版本过低，建议使用 NodeSource 或 nvm 安装 Node.js 20。

### 4.3 准备项目代码

将源码复制或拉取到目标目录：

```bash
cd /opt
sudo mkdir -p iot-platform
sudo chown -R $USER:$USER /opt/iot-platform
```

如果使用 Git：

```bash
git clone <your-repo-url> /opt/iot-platform
cd /opt/iot-platform
```

如果是压缩包：

```bash
cd /opt/iot-platform
tar -xf iot-platform.tar.gz --strip-components=1
```

### 4.4 配置 Python 后端环境

```bash
cd /opt/iot-platform
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` 当前依赖：

```text
fastapi
uvicorn[standard]
sqlalchemy
pymysql
python-jose[cryptography]
passlib[bcrypt]
python-multipart
pydantic-settings
pymodbus
websockets
python-dotenv
psutil
```

### 4.5 配置 MySQL

启动 MySQL：

```bash
sudo systemctl enable mysql
sudo systemctl start mysql
sudo systemctl status mysql
```

登录 MySQL：

```bash
sudo mysql
```

创建数据库和用户：

```sql
CREATE DATABASE IF NOT EXISTS iot_platform
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'iot_user'@'localhost' IDENTIFIED BY 'change_this_password';
GRANT ALL PRIVILEGES ON iot_platform.* TO 'iot_user'@'localhost';
FLUSH PRIVILEGES;
```

验证连接：

```bash
mysql -u iot_user -p -h localhost iot_platform
```

### 4.6 配置后端 `.env`

在项目根目录创建 `.env`：

```bash
cd /opt/iot-platform
nano .env
```

示例内容：

```env
APP_NAME=工业物联网设备监控平台
DEBUG=True
HOST=0.0.0.0
PORT=8000

DATABASE_URL=mysql+pymysql://iot_user:change_this_password@localhost:3306/iot_platform
SQL_ECHO=False

SECRET_KEY=replace-with-a-long-random-secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

MODBUS_POLL_INTERVAL=3
MODBUS_TIMEOUT=2
MODBUS_PORT=502
DEVICE_TIMEOUT=900
```

注意：

- 生产环境必须修改 `SECRET_KEY`。
- 生产环境建议设置 `DEBUG=False`。
- `.env` 包含密码和密钥，不要提交到 Git。
- 如果 MySQL 不在本机，把 `localhost` 改为数据库服务器 IP 或域名。

### 4.7 初始化数据库表

项目启动时会自动建表。只要数据库存在，执行后端启动命令即可：

```bash
cd /opt/iot-platform
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

看到类似信息表示启动成功：

```text
Uvicorn running on http://0.0.0.0:8000
数据库初始化完成
```

访问：

```text
http://服务器IP:8000/docs
```

如果希望手动执行建表 SQL，可使用本文第 5 节的完整 SQL。

### 4.8 配置 Vue 前端环境

```bash
cd /opt/iot-platform/frontend
npm install
npm run dev
```

开发访问地址：

```text
http://服务器IP:5173
```

生产构建：

```bash
cd /opt/iot-platform/frontend
npm run build
```

构建产物位于：

```text
frontend/dist/
```

## 5. 数据库建表语句

如果只想让应用自动建表，可跳过本节，只执行：

```sql
CREATE DATABASE IF NOT EXISTS iot_platform
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

如果需要人工初始化完整表结构，按以下 SQL 执行。

```sql
CREATE DATABASE IF NOT EXISTS iot_platform
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE iot_platform;

CREATE TABLE devices (
  id INTEGER NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  device_name VARCHAR(100) NOT NULL,
  protocol_type VARCHAR(14) NOT NULL,
  device_secret VARCHAR(255),
  gateway_ip VARCHAR(64),
  slave_id SMALLINT,
  description TEXT,
  is_active BOOL,
  is_maintenance BOOL,
  is_test_device BOOL,
  online_status BOOL,
  last_active_time DATETIME,
  offline_threshold INTEGER,
  factory VARCHAR(100),
  workshop VARCHAR(100),
  production_line VARCHAR(100),
  device_group VARCHAR(100),
  deleted_at DATETIME,
  created_at DATETIME DEFAULT now(3),
  updated_at DATETIME DEFAULT now(3),
  PRIMARY KEY (id),
  UNIQUE (device_id)
);
CREATE INDEX idx_devices_deleted_at ON devices (deleted_at);
CREATE INDEX idx_devices_device_id ON devices (device_id);
CREATE INDEX idx_devices_grouping ON devices (factory, workshop, production_line, device_group);
CREATE INDEX idx_devices_last_active_time ON devices (last_active_time);
CREATE INDEX idx_devices_online_status ON devices (online_status);
CREATE INDEX idx_devices_protocol_active ON devices (protocol_type, is_active);
CREATE INDEX idx_devices_protocol_type ON devices (protocol_type);
CREATE INDEX ix_devices_id ON devices (id);

CREATE TABLE users (
  id INTEGER NOT NULL AUTO_INCREMENT,
  username VARCHAR(50) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(100) NOT NULL,
  is_active BOOL,
  created_at DATETIME DEFAULT now(3),
  updated_at DATETIME DEFAULT now(3),
  PRIMARY KEY (id),
  UNIQUE (username),
  UNIQUE (email)
);
CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_username ON users (username);
CREATE INDEX ix_users_id ON users (id);

CREATE TABLE sensors (
  id INTEGER NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  field_en VARCHAR(50) NOT NULL,
  field_cn VARCHAR(50) NOT NULL,
  unit VARCHAR(20),
  channel_no INTEGER,
  coeff FLOAT,
  lower_threshold FLOAT,
  upper_threshold FLOAT,
  is_monitored BOOL,
  created_at DATETIME DEFAULT now(3),
  updated_at DATETIME DEFAULT now(3),
  PRIMARY KEY (id),
  CONSTRAINT chk_sensors_threshold_range CHECK (
    lower_threshold IS NULL OR upper_threshold IS NULL OR lower_threshold <= upper_threshold
  ),
  FOREIGN KEY(device_id) REFERENCES devices (device_id)
    ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_sensors_device_id ON sensors (device_id);
CREATE INDEX idx_sensors_device_monitored ON sensors (device_id, is_monitored);
CREATE INDEX ix_sensors_id ON sensors (id);
CREATE UNIQUE INDEX uk_sensors_device_channel_no ON sensors (device_id, channel_no);
CREATE UNIQUE INDEX uk_sensors_device_field_en ON sensors (device_id, field_en);

CREATE TABLE device_data (
  id BIGINT NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  reported_at DATETIME,
  data_json JSON NOT NULL,
  created_at DATETIME DEFAULT now(3),
  PRIMARY KEY (id),
  FOREIGN KEY(device_id) REFERENCES devices (device_id)
    ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_device_data_device_reported_time ON device_data (device_id, reported_at);
CREATE INDEX idx_device_data_device_time ON device_data (device_id, created_at);
CREATE INDEX ix_device_data_id ON device_data (id);

CREATE TABLE sensor_data_points (
  id BIGINT NOT NULL AUTO_INCREMENT,
  device_data_id BIGINT,
  device_id VARCHAR(100) NOT NULL,
  sensor_id BIGINT NOT NULL,
  field_en VARCHAR(100) NOT NULL,
  value FLOAT NOT NULL,
  unit VARCHAR(50),
  reported_at DATETIME NOT NULL,
  created_at DATETIME DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX idx_created_at ON sensor_data_points (created_at);
CREATE INDEX idx_device_data_id ON sensor_data_points (device_data_id);
CREATE INDEX idx_device_time ON sensor_data_points (device_id, reported_at);
CREATE INDEX idx_field_time ON sensor_data_points (device_id, field_en, reported_at);
CREATE INDEX idx_sensor_time ON sensor_data_points (sensor_id, reported_at);
CREATE INDEX ix_sensor_data_points_id ON sensor_data_points (id);

CREATE TABLE sensor_data_agg_min (
  id BIGINT NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  sensor_id BIGINT NOT NULL,
  field_en VARCHAR(100) NOT NULL,
  bucket_time DATETIME NOT NULL,
  avg_value FLOAT,
  min_value FLOAT,
  max_value FLOAT,
  first_val FLOAT,
  last_val FLOAT,
  count_value INTEGER NOT NULL,
  created_at DATETIME DEFAULT now(),
  updated_at DATETIME DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX idx_bucket_time ON sensor_data_agg_min (bucket_time);
CREATE INDEX idx_device_minute ON sensor_data_agg_min (device_id, bucket_time);
CREATE INDEX idx_field_minute ON sensor_data_agg_min (device_id, field_en, bucket_time);
CREATE INDEX ix_sensor_data_agg_min_id ON sensor_data_agg_min (id);
CREATE UNIQUE INDEX uk_sensor_minute ON sensor_data_agg_min (sensor_id, bucket_time);

CREATE TABLE sensor_data_agg_hour (
  id BIGINT NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  sensor_id BIGINT NOT NULL,
  field_en VARCHAR(100) NOT NULL,
  bucket_time DATETIME NOT NULL,
  avg_value FLOAT,
  min_value FLOAT,
  max_value FLOAT,
  first_val FLOAT,
  last_val FLOAT,
  count_value INTEGER NOT NULL,
  created_at DATETIME DEFAULT now(),
  updated_at DATETIME DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX idx_bucket_time ON sensor_data_agg_hour (bucket_time);
CREATE INDEX idx_device_hour ON sensor_data_agg_hour (device_id, bucket_time);
CREATE INDEX idx_field_hour ON sensor_data_agg_hour (device_id, field_en, bucket_time);
CREATE INDEX ix_sensor_data_agg_hour_id ON sensor_data_agg_hour (id);
CREATE UNIQUE INDEX uk_sensor_hour ON sensor_data_agg_hour (sensor_id, bucket_time);

CREATE TABLE alerts (
  id BIGINT NOT NULL AUTO_INCREMENT,
  device_id VARCHAR(100) NOT NULL,
  sensor_field_en VARCHAR(50) NOT NULL,
  sensor_field_cn VARCHAR(50) NOT NULL,
  current_value FLOAT NOT NULL,
  threshold_value FLOAT NOT NULL,
  alert_type VARCHAR(11) NOT NULL,
  alert_level VARCHAR(8),
  is_resolved BOOL,
  status VARCHAR(20),
  first_triggered_at DATETIME,
  last_triggered_at DATETIME,
  occurrence_count INTEGER,
  recovered_at DATETIME,
  resolved_at DATETIME,
  resolved_by INTEGER,
  resolve_note VARCHAR(255),
  created_at DATETIME DEFAULT now(3),
  updated_at DATETIME DEFAULT now(3),
  PRIMARY KEY (id),
  CONSTRAINT chk_alerts_resolved_time CHECK (
    (is_resolved = FALSE AND resolved_at IS NULL) OR (is_resolved = TRUE)
  ),
  FOREIGN KEY(device_id) REFERENCES devices (device_id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  FOREIGN KEY(resolved_by) REFERENCES users (id)
    ON DELETE SET NULL ON UPDATE CASCADE
);
CREATE INDEX idx_alerts_active_lookup ON alerts (device_id, sensor_field_en, alert_type, status, is_resolved);
CREATE INDEX idx_alerts_device_field_resolved ON alerts (device_id, sensor_field_en, is_resolved);
CREATE INDEX idx_alerts_device_time ON alerts (device_id, created_at);
CREATE INDEX idx_alerts_status_time ON alerts (status, last_triggered_at);
CREATE INDEX idx_alerts_type_resolved_time ON alerts (alert_type, is_resolved, last_triggered_at);
CREATE INDEX idx_alerts_unresolved_time ON alerts (is_resolved, created_at);
CREATE INDEX ix_alerts_id ON alerts (id);
```

## 6. 首次启动和功能验证

### 6.1 启动后端

```bash
cd /opt/iot-platform
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问：

```text
http://服务器IP:8000/
http://服务器IP:8000/health
http://服务器IP:8000/docs
```

### 6.2 注册用户

使用 Swagger UI：

```text
http://服务器IP:8000/docs
```

调用：

```http
POST /api/auth/register
```

请求体：

```json
{
  "username": "admin",
  "email": "admin@example.com",
  "password": "123456"
}
```

也可以使用 curl：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@example.com","password":"123456"}'
```

### 6.3 登录获取 Token

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=123456"
```

返回示例：

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

### 6.4 启动前端

```bash
cd /opt/iot-platform/frontend
npm run dev
```

访问：

```text
http://服务器IP:5173
```

### 6.5 创建 HTTP 主动上报设备

登录后可以在前端创建设备，也可以调用接口。

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/devices \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "http_device_001",
    "device_name": "HTTP测试设备001",
    "protocol_type": "http_active",
    "description": "主动上报测试设备",
    "is_active": true,
    "offline_threshold": 900,
    "sensors": [
      {
        "field_en": "temperature",
        "field_cn": "温度",
        "unit": "℃",
        "lower_threshold": 0,
        "upper_threshold": 80,
        "is_monitored": true
      }
    ]
  }'
```

响应中会包含自动生成的 `device_secret`。

### 6.6 上报 HTTP 设备数据

```bash
curl -X POST http://127.0.0.1:8000/api/data/report/http \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "http_device_001",
    "device_secret": "<device_secret>",
    "timestamp": "2026-06-04T10:00:00",
    "data": {
      "temperature": 26.5
    }
  }'
```

### 6.7 创建 Modbus 测试设备

```bash
curl -X POST http://127.0.0.1:8000/api/devices \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "modbus_test_001",
    "device_name": "Modbus测试设备001",
    "protocol_type": "modbus_gateway",
    "is_test_device": true,
    "is_active": true,
    "gateway_ip": "127.0.0.1",
    "slave_id": 1,
    "sensors": [
      {
        "field_en": "pressure",
        "field_cn": "压力",
        "unit": "MPa",
        "channel_no": 0,
        "coeff": 0.1,
        "lower_threshold": 0,
        "upper_threshold": 100,
        "is_monitored": true
      }
    ]
  }'
```

手动插入测试数据：

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/poller/test-data/modbus_test_001 \
  -H "Authorization: Bearer <token>"
```

## 7. 迁移到新环境的完整流程

本节用于从旧服务器迁移到新服务器。

### 7.1 旧环境导出代码

如果使用 Git 管理，建议直接在新环境拉取同一分支。

如果没有 Git，可打包源码。建议排除虚拟环境、node_modules、缓存和构建产物：

```bash
cd /path/to/iot-platform
tar --exclude='venv' \
    --exclude='frontend/node_modules' \
    --exclude='__pycache__' \
    --exclude='.git' \
    -czf iot-platform-source.tar.gz .
```

将压缩包复制到新服务器：

```bash
scp iot-platform-source.tar.gz user@new-server:/opt/
```

### 7.2 旧环境导出数据库

只导出项目数据库：

```bash
mysqldump -u root -p \
  --databases iot_platform \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --default-character-set=utf8mb4 \
  > iot_platform.sql
```

压缩导出文件：

```bash
gzip iot_platform.sql
```

复制到新服务器：

```bash
scp iot_platform.sql.gz user@new-server:/opt/
```

### 7.3 新环境安装基础软件

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip mysql-server nodejs npm nginx
```

### 7.4 新环境导入代码

```bash
cd /opt
sudo mkdir -p iot-platform
sudo chown -R $USER:$USER /opt/iot-platform
tar -xzf iot-platform-source.tar.gz -C /opt/iot-platform
cd /opt/iot-platform
```

### 7.5 新环境恢复数据库

启动 MySQL：

```bash
sudo systemctl enable mysql
sudo systemctl start mysql
```

导入备份：

```bash
gunzip -c /opt/iot_platform.sql.gz | mysql -u root -p
```

如果旧库使用的是 root，而新环境准备使用独立用户，需要授权：

```sql
CREATE USER IF NOT EXISTS 'iot_user'@'localhost' IDENTIFIED BY 'change_this_password';
GRANT ALL PRIVILEGES ON iot_platform.* TO 'iot_user'@'localhost';
FLUSH PRIVILEGES;
```

验证表：

```bash
mysql -u iot_user -p -e "USE iot_platform; SHOW TABLES;"
```

### 7.6 新环境重建后端虚拟环境

不要迁移旧服务器的 `venv`，应在新服务器重新创建：

```bash
cd /opt/iot-platform
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 7.7 新环境重建前端依赖

不要迁移旧服务器的 `frontend/node_modules`，应重新安装：

```bash
cd /opt/iot-platform/frontend
npm install
npm run build
```

### 7.8 新环境配置 `.env`

```bash
cd /opt/iot-platform
nano .env
```

生产环境示例：

```env
APP_NAME=工业物联网设备监控平台
DEBUG=False
HOST=0.0.0.0
PORT=8000

DATABASE_URL=mysql+pymysql://iot_user:change_this_password@localhost:3306/iot_platform
SQL_ECHO=False

SECRET_KEY=replace-with-a-long-random-secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

MODBUS_POLL_INTERVAL=3
MODBUS_TIMEOUT=2
MODBUS_PORT=502
DEVICE_TIMEOUT=900
```

如果 `SECRET_KEY` 改变，旧登录 Token 会失效，用户重新登录即可。

### 7.9 后端生产运行方式

开发方式：

```bash
cd /opt/iot-platform
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

生产建议使用 systemd 托管。

创建服务文件：

```bash
sudo nano /etc/systemd/system/iot-platform.service
```

内容：

```ini
[Unit]
Description=Industrial IoT Platform FastAPI Service
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=/opt/iot-platform
Environment=PATH=/opt/iot-platform/venv/bin
ExecStart=/opt/iot-platform/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

授权目录：

```bash
sudo chown -R www-data:www-data /opt/iot-platform
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable iot-platform
sudo systemctl start iot-platform
sudo systemctl status iot-platform
```

查看日志：

```bash
journalctl -u iot-platform -f
```

### 7.10 Nginx 反向代理和前端部署

前端构建：

```bash
cd /opt/iot-platform/frontend
npm run build
```

Nginx 配置：

```bash
sudo nano /etc/nginx/sites-available/iot-platform
```

内容：

```nginx
server {
    listen 80;
    server_name your-domain-or-server-ip;

    root /opt/iot-platform/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ws {
        proxy_pass http://127.0.0.1:8000/api/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/iot-platform /etc/nginx/sites-enabled/iot-platform
sudo nginx -t
sudo systemctl reload nginx
```

访问：

```text
http://your-domain-or-server-ip
```

### 7.11 防火墙和端口

开发模式常用端口：

```text
8000  # FastAPI
5173  # Vite
3306  # MySQL，仅内网或本机开放
502   # Modbus TCP，取决于网关网络
```

生产模式通常只开放：

```text
80
443
```

如果使用 UFW：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

MySQL 不建议对公网开放。

## 8. 迁移检查清单

迁移前确认：

- 已备份源码。
- 已备份 `.env`，但不要把它上传到公开仓库。
- 已备份 MySQL 数据库。
- 已记录 MySQL 用户、库名和连接地址。
- 已记录服务器防火墙规则。
- 已记录真实 Modbus 网关 IP、端口和网络连通性。
- 已记录前端访问域名或服务器 IP。

迁移后确认：

- `python3 --version` 正常。
- `node --version` 正常。
- `mysql --version` 正常。
- `.env` 中 `DATABASE_URL` 可以连接数据库。
- `pip install -r requirements.txt` 成功。
- `npm install` 成功。
- `npm run build` 成功。
- `uvicorn app.main:app --host 0.0.0.0 --port 8000` 能启动。
- `http://服务器IP:8000/health` 返回 healthy。
- `http://服务器IP:8000/docs` 可访问。
- 前端页面可访问。
- 登录接口可用。
- 设备列表可加载。
- HTTP 设备可上报数据。
- Modbus 测试设备可生成测试数据。
- WebSocket 连接正常。
- 告警列表和历史数据可查询。

## 9. 常见问题排查

### 9.1 后端启动时报数据库连接失败

检查 `.env`：

```env
DATABASE_URL=mysql+pymysql://iot_user:change_this_password@localhost:3306/iot_platform
```

验证 MySQL 登录：

```bash
mysql -u iot_user -p -h localhost iot_platform
```

检查 MySQL 是否运行：

```bash
sudo systemctl status mysql
```

### 9.2 `ModuleNotFoundError`

通常是没有进入虚拟环境或依赖没有安装：

```bash
cd /opt/iot-platform
source venv/bin/activate
pip install -r requirements.txt
```

### 9.3 前端接口 404 或请求不到后端

开发环境检查：

- 后端是否运行在 `127.0.0.1:8000`
- `frontend/vite.config.js` 代理是否正确
- 前端是否通过 `npm run dev` 启动

生产环境检查：

- Nginx `/api/` 是否代理到 `127.0.0.1:8000`
- 后端 systemd 服务是否运行
- 浏览器控制台 Network 中的请求地址是否正确

### 9.4 WebSocket 连接失败

检查 Nginx 是否配置了 Upgrade 头：

```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

检查后端接口：

```text
/api/ws
```

### 9.5 HTTP 设备上报返回认证失败

检查：

- `device_id` 是否存在。
- 设备是否未归档。
- 设备是否启用。
- 设备协议是否为 `http_active`。
- `device_secret` 是否正确。
- 后端是否重启后正常加载了设备密钥缓存。

### 9.6 上报成功但历史数据为空

检查：

- 上报字段是否在传感器配置中存在。
- 传感器 `is_monitored` 是否为 true。
- 上报字段值是否是数值。
- 查询时间范围是否包含 `reported_at`。

### 9.7 Modbus 设备没有数据

检查：

- 设备协议是否为 `modbus_gateway`。
- 设备是否启用且未归档。
- 非测试设备是否配置了 `gateway_ip` 和 `slave_id`。
- 传感器是否配置了 `channel_no`。
- Modbus 网关 IP 是否可达。
- 网关端口是否为 `.env` 中的 `MODBUS_PORT`，默认 502。
- `GET /api/monitor/poller` 中网关状态和错误信息。

### 9.8 告警没有生成

检查：

- 传感器是否配置了 `lower_threshold` 或 `upper_threshold`。
- 设备是否处于维护模式，维护模式会抑制告警。
- 上报字段是否匹配传感器 `field_en`。
- 上报值是否真的超出阈值。

## 10. 生产环境建议

安全建议：

- 使用强密码创建 MySQL 用户。
- 生产环境设置 `DEBUG=False`。
- 修改 `SECRET_KEY` 为足够长的随机字符串。
- 不要提交 `.env`。
- MySQL 不要开放公网访问。
- 使用 HTTPS。
- 限制服务器 SSH 登录来源。

运维建议：

- 使用 systemd 托管后端。
- 使用 Nginx 托管前端和反向代理 API。
- 定期执行 MySQL 备份。
- 监控磁盘空间，`device_data` 和 `sensor_data_points` 会持续增长。
- 根据数据量定期归档历史明细数据。
- 后续建议引入 Alembic 管理数据库迁移。

备份建议：

```bash
mysqldump -u root -p \
  --databases iot_platform \
  --single-transaction \
  --default-character-set=utf8mb4 \
  | gzip > /backup/iot_platform_$(date +%F).sql.gz
```

恢复建议先在测试库验证，再导入生产库。
