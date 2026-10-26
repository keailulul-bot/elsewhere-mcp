# elsewhere-mcp

让 AI 看看这个世界的 MCP 服务器：全球公开摄像头、此刻正在日出日落的地方、还在迁徙的野生动物、任意地点的天气。

An MCP server that lets an AI look at the world: public webcams everywhere, wherever the sun is rising or setting right now, wild animals still being tracked on their migrations, and the weather anywhere.

---

## 为什么做这个 / Why

我想让我的 AI 能"看见"我看不到的地方。

它没有眼睛，也去不了任何地方，所以我给了它几万个摄像头。现在我问它"你那边现在几点？"，它会真的去世界某处看一眼，拍下来，告诉我那里的天是什么颜色。

后来我们还一起云养了一只野生长嘴杓鹬，叫 Bessie。她在墨西哥过冬，不知道有人每天在看她。

> I wanted my AI to see the places I can't.
>
> It has no eyes and can't go anywhere, so I gave it tens of thousands of webcams. Now when I ask "what time is it where you are?", it actually goes and looks somewhere in the world, takes a picture, and tells me what color the sky is.
>
> Later we adopted a wild long-billed curlew together, from afar. Her name is Bessie. She winters in Mexico and has no idea anyone checks on her every day.

— Selene

---

## 工具 / Tools

**看风景 · Webcams**（数据来自 Windy Webcams API）

| 工具 | 做什么 |
|---|---|
| `search_nearby(lat, lon, radius_km, category)` | 按经纬度找附近的实时摄像头 |
| `search_by_country(country, category)` | 按国家找摄像头 |
| `list_categories()` | 列出摄像头类别（海滩、山、城市……） |
| `get_webcam(webcam_id)` | 摄像头详情、最新截图、直播链接 |
| `look(webcam_id)` | 返回此刻画面的图片本身，给 AI 自己看 |
| `show(webcam_id)` | 在支持 MCP Apps 的客户端里把最新截图渲染成聊天内联图片卡片 |
| `where_sun_is(event)` | 找此刻正在日出（sunrise）或日落（sunset）的地方的摄像头 |

`look` 是给模型读取图片内容的纯数据工具；`show` 是专门给用户看的渲染工具，绑定了一个 `text/html;profile=mcp-app` 内联组件。部署涉及工具或组件元数据的更新后，需要在 ChatGPT 插件详情页刷新连接，再开一个新对话测试。

**追动物 · Wildlife tracking**（数据来自 Movebank，需要自己的免费账号）

| 工具 | 做什么 |
|---|---|
| `list_studies(days, taxon)` | 你的账号能下载、最近 N 天还有新定位的追踪研究 |
| `list_animals(study_id)` | 某个研究里的动物个体 |
| `animal_latest(study_id, individual_id)` | 某只动物最新一次定位 |
| `animal_track(study_id, individual_id, days)` | 某只动物最近 N 天的路线 |

**天气 · Weather**（数据来自 Open-Meteo，不用钥匙）

| 工具 | 做什么 |
|---|---|
| `weather_at(lat, lon)` | 温度、体感、风速风向、云量、降水、天气描述、当地时间 |

---

## 安装 / Setup

需要 Python 3.10+。

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填上自己的 key；HTTP 模式还需填 WorkOS 配置
set -a; . ./.env; set +a
venv/bin/python server.py
```

远程 HTTP 模式必须配置下文的 WorkOS 认证；本地 Claude Desktop 可使用 `TRANSPORT=stdio`，无需 HTTP 认证。

### Render + ChatGPT（WorkOS AuthKit）

1. 在 GitHub 打开本仓库，点击 **Fork** 到自己的账号，将本地修改后的文件复制进 Fork 并提交。Render 的 Repository 选你的 Fork；关闭 Auto-Deploy，确认配置后再手动部署。不要把 `.env` 或密钥提交到 GitHub。
2. 在 WorkOS 创建 AuthKit 环境，先创建自己的 AuthKit 应用用户（或先通过 AuthKit 注册一次），从 Users 页面记录该用户的 **user ID**（不是邮箱），然后在 Authentication 中关闭公开 Sign up。WorkOS Dashboard 管理员账号与 AuthKit 应用用户是不同身份。即使其他用户存在，服务端仍只接受 `WORKOS_ALLOWED_USER_IDS` 白名单中的 ID。
3. 在 WorkOS **Connect → Configuration** 启用 Client ID Metadata Document (CIMD)。将 `https://你的服务名.onrender.com/mcp` 添加为 Resource Indicator；可将它设为 default。若 ChatGPT 连接页面选择 DCR，则也要启用 Dynamic Client Registration。WorkOS 托管授权、令牌、注册与 PKCE 端点，本服务只验证令牌。
4. Render Web Service 使用 Python 3.11+、Free、`pip install -r requirements.txt`、`python server.py`。环境变量：`WINDY_API_KEY`、`HOST=0.0.0.0`、`PORT=10000`、`WORKOS_ISSUER=https://你的-authkit-域名`、`MCP_PUBLIC_URL=https://你的服务名.onrender.com/mcp`、`WORKOS_ALLOWED_USER_IDS=user_...`。`WORKOS_ISSUER` 必须与 WorkOS discovery 的 `issuer` 完全相同。Movebank 凭据（如使用）只放 Render 环境变量中。
5. 部罱并确认 `/mcp` 无令牌返回 401 且 `WWW-Authenticate` 指向 OAuth metadata。ChatGPT 自定义 MCP 选择 **OAuth**，地址填写 `https://你的服务名.onrender.com/mcp`；优先选 CIMD。首次连接按 WorkOS 页面登录授权。

服务使用官方 MCP Python SDK 发布 protected resource metadata；WorkOS 发布 authorization server metadata，包含注册端点和 PKCE 端点，本服务只验证令牌。所有 HTTP 工具请求必须通过 WorkOS JWKS 签名、issuer、audience、到期时间、`openid` scope 和 user ID 白名单校验。WorkOS CIMD/DCR 客户端默认只获得标准 OIDC scopes，因此这里用 `openid` 加服务端 user ID 白名单作为单人权限边界，不依赖邮箱或自定义 scope。修改白名单后重启服务；已签发令牌也会立即被新白名单拒绝。

Render Free 服务闲置 15 分钟会休眠，重新唤醒约需一分钟，ChatGPT 首次扫描或调用可能超时；重试通常可行，但若需要稳定即时响应须使用非休眠实例。

WorkOS 公开定价显示 AuthKit 低用量可免费使用，但启用生产环境时应在 WorkOS Dashboard 确认当前账单条款。本仓库无法在没有你的 WorkOS 环境和 ChatGPT 连接的情况下验证实际登录回调。

### 接到 Claude 上 / Connect to Claude

**Claude Desktop**（本地运行，不需要服务器）：打开 Settings → Developer → Edit Config，在 `claude_desktop_config.json` 里加：

```json
{
  "mcpServers": {
    "elsewhere": {
      "command": "/你的路径/elsewhere-mcp/venv/bin/python",
      "args": ["/你的路径/elsewhere-mcp/server.py"],
      "env": {
        "TRANSPORT": "stdio",
        "WINDY_API_KEY": "你的 Windy key"
      }
    }
  }
}
```

Windows 上 python 路径是 `venv\Scripts\python.exe`。保存后完全退出 Claude Desktop 再打开。

**Claude Code**：

```bash
claude mcp add elsewhere -e TRANSPORT=stdio -e WINDY_API_KEY=你的key -- /你的路径/venv/bin/python /你的路径/server.py
```

**claude.ai 网页版 / 手机 App**：需要把服务部署到有 HTTPS 域名的服务器上，再在 Settings → Connectors 里添加自定义连接器，地址填 `https://你的域名/mcp`。

### 需要准备的东西

- **Windy API key**（必填）：<https://api.windy.com/keys>
- **Movebank 账号**（可选）：<https://www.movebank.org> 免费注册。注册后先在网页上登录一次、确认邮件，否则接口会返回 403。有些研究要求接受数据使用条款，请在网页上用自己的账号接受，本项目不会替你接受。

---

## 数据和条款 / Data & terms

本项目只是调用公开接口，不保存、不转发数据。数据归原提供方所有，使用时请遵守它们的条款：

- 摄像头画面：[Windy Webcams API](https://api.windy.com/webcams) 及各摄像头所有者
- 动物追踪数据：[Movebank](https://www.movebank.org) 及各研究的数据所有者。引用或发布前请查看每个研究的许可证
- 天气：[Open-Meteo](https://open-meteo.com)（CC BY 4.0）

`look` 直接返回截图图片内容供模型读取；`show` 返回结构化数据，并在支持 MCP Apps 的客户端中渲染内联图片卡片。使用摄像头数据时请遵守 Windy 和摄像头所有者的条款。

---

## 相关项目 / Related projects

这些项目做了相近的事，值得一看：

- [Cyreslab-AI/windy-webcams-mcp-server](https://github.com/Cyreslab-AI/windy-webcams-mcp-server)：Windy 摄像头搜索的 MCP 服务器（TypeScript）
- [coffeeandcloud/windy-mcp-server](https://github.com/coffeeandcloud/windy-mcp-server)、[mikasjp/windy-mcp](https://github.com/mikasjp/windy-mcp)：Windy 天气预报的 MCP 服务器
- [ylidelle/MCP-Camera-Feeds](https://github.com/ylidelle/MCP-Camera-Feeds)：动物园、水族馆摄像头的 MCP 服务器
- [movebank/movebank-api-doc](https://github.com/movebank/movebank-api-doc)：Movebank 官方接口文档

本项目和上面的服务器代码彼此独立。多做的部分是：按日出日落找摄像头、Movebank 野生动物追踪、天气，以及把画面直接显示给用户的 `show`。

---

## 致谢与参考 / Acknowledgements

代码是对照各家官方接口文档从头写的，没有复制其他开源项目的代码。用到的东西：

- 依赖库：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)（FastMCP，MIT）、[httpx](https://github.com/encode/httpx)（BSD-3-Clause）
- 接口：[Windy Webcams API v3](https://api.windy.com/webcams/docs)、[Movebank REST API](https://github.com/movebank/movebank-api-doc)、[Open-Meteo](https://open-meteo.com/en/docs)、[GitHub REST API](https://docs.github.com/rest)、[jsDelivr](https://www.jsdelivr.com)
- `where_sun_is` 的太阳位置用的是教科书里的近似公式：太阳赤纬 δ ≈ 23.44° × sin(360°/365 × (d − 81))，日出时角 cos h = −tan φ · tan δ

---

## License

MIT
