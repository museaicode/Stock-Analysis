# 分享图片模板与数据填充

分享图片用于把个股分析和市场复盘转换为适合社交平台传播的 1080px 长图。个股和大盘使用两套独立的信息结构，但共用 MIUSIAI 品牌（顶部品牌栏与底部品牌标题，由 `src/share_image.py` 中的 `PROJECT_BRAND_NAME` 定义）和风险声明。底部的项目地址与小红书区域默认都不显示，仓库也不再内置任何二维码或昵称；只有部署方自行配置后才会渲染对应内容。

## 运行时如何填充

现有通知链路不需要手工组装图片数据：

```text
个股 AnalysisResult
  -> AnalysisResult.to_dict() 结构化 JSON + 稳定 Markdown
  -> share_image 优先读取 JSON，Markdown 兼容回退
  -> 个股决策卡 HTML
  -> wkhtmltoimage / markdown-to-file / Playwright 输出 PNG

大盘 MarketOverview + market_light + LLM 复盘
  -> MarketAnalyzer 生成 market_review_payload + 稳定 Markdown
  -> share_image 优先读取 payload，Markdown 兼容回退
  -> 市场复盘卡 HTML
  -> wkhtmltoimage / markdown-to-file / Playwright 输出 PNG
```

`MARKDOWN_TO_IMAGE_CHANNELS`、`MD2IMG_ENGINE`、`MARKDOWN_TO_IMAGE_MAX_CHARS` 继续控制哪些通知渠道转图、使用哪个引擎以及最大输入长度。转换失败时仍回退为文本通知。

### 版式：card 与 full

`MARKDOWN_TO_IMAGE_LAYOUT` 决定**通知推送图**的正文版式（`build_share_image_html(..., layout=...)`）：

| 版式 | 正文内容 | 适用场景 |
| --- | --- | --- |
| `card`（默认） | 上文所述个股决策卡 / 市场复盘卡，只提炼结论、点位、技术、风险、持仓等模块 | 快速浏览、社交传播 |
| `full` | 完整报告 Markdown 全文（所有章节、表格、检查清单），页眉品牌栏、标题、底部风险提示与已配置的品牌信息保持不变，副标题显示“完整分析报告” | 需要在推送中阅读完整分析 |

- `full` 对单股、多股汇总、大盘复盘推送一致生效；企业微信超 2MB 的长图会被压缩/切图（见 [通知渠道说明](notifications.md#企业微信图片大小限制)）。完整正文超过 `MARKDOWN_TO_IMAGE_MAX_CHARS` 时跳过转图、回退文本。
- Web / 桌面端“分享”按钮（`/api/v1/history/{id}/share-image` 与 `/share-image-html`）固定使用 `card`，不受该配置影响。

分享图需要运行环境提供对应语言字体。官方 Docker 镜像已内置 Noto CJK 字体；Debian/Ubuntu 源码部署使用默认 `wkhtmltoimage` 引擎时，应安装 `wkhtmltopdf fonts-noto-cjk`。如果只安装转图工具而缺少 CJK 字体，中文或韩文可能在 PNG 中消失，只剩数字、英文和边框。
`fonts-noto-cjk` 是 Debian 字体包名，不代表新增日文报告语言。项目的报告输出仍只支持 `REPORT_LANGUAGE=zh|en|ko`；日股个股和日本市场复盘中的日文原生名称由通用 Noto CJK fallback 覆盖，页面语言仍跟随所选报告语言，不会根据 `7203.T` 或 `region=jp` 切换成未支持的 `ja` 输出。

底部品牌区使用以下可选配置，默认全部为空。分享图（Web / 桌面端）与通知推送图（企业微信、Telegram 等转图渠道）共用同一模板，两处行为一致：

```dotenv
# 项目地址：留空时不显示任何地址及“项目地址”标签；仅接受 http:// 或 https://
SHARE_IMAGE_REPO_URL=https://github.com/your-name/your-repo
# 小红书：三项全部留空时不显示小红书区域；只填部分时只显示已填写的内容
SHARE_IMAGE_XIAOHONGSHU_URL=https://example.com/my-xiaohongshu
SHARE_IMAGE_XIAOHONGSHU_HANDLE=@我的账号
SHARE_IMAGE_XIAOHONGSHU_QR_PATH=assets/my-xiaohongshu-qr.png
```

- `SHARE_IMAGE_REPO_URL` 填写后显示为“项目地址”（英文 / 韩文报告为 `Project` / `프로젝트`）加去掉协议头的地址；地址属于 `github.com` 时附带 GitHub 图标。非 `http(s)://` 的值会被忽略并记录 warning。
- 只填 `SHARE_IMAGE_XIAOHONGSHU_HANDLE` 时只显示“小红书@昵称”，不显示二维码；只填二维码路径时只显示二维码；填写 URL 时昵称与二维码会链接到该地址（URL 只接受 `http://` 或 `https://`）。
- 二维码路径支持绝对路径或相对项目根目录路径；冻结桌面后端也会从 PyInstaller 资源目录解析相对路径。文件不存在或不可读时记录 warning 并省略二维码，不会导致分享图生成失败。二维码在转图时以内嵌 Data URI 渲染，不依赖运行时网络。历史配置中的数字 ID（`SHARE_IMAGE_XIAOHONGSHU_ID`）不参与分享图渲染。
- 项目地址与小红书区域都未配置时，底部只保留 MIUSIAI 品牌标题、标语和风险提示，品牌标题占满整行，不留空白占位。
- 以上配置目前只能通过 `.env` / 环境变量设置，Web 设置页不提供对应表单。

## Web 一键分享

浏览器版历史个股报告、市场复盘和完整报告抽屉右上角都会显示“分享”按钮。页面加载报告时不会生成图片；只有用户点击“分享”后，页面才调用 `GET /api/v1/history/{record_id}/share-image` 按需生成图片。

交互分两步，避免异步生成图片导致浏览器的“临时用户激活”过期：

1. 点击“分享”后立即打开预览弹窗，显示“正在生成分享图...”加载状态；图片返回后在弹窗内展示预览。同一记录再次打开弹窗会复用已生成的图片。
2. 弹窗底部按浏览器能力显示操作按钮，复制与系统分享都由弹窗内的这一次新点击同步触发：
   - “下载图片”：始终显示，文件名为 `<报告标题>-<记录 ID>.<扩展名>`，扩展名与实际图片格式一致（JPEG 为 `.jpg`）。
   - “复制图片”：仅在 `navigator.clipboard.write` 与 `ClipboardItem` 均可用时显示。浏览器剪贴板普遍只接受 PNG，JPEG 图片会在写入时于浏览器内转为 PNG（以 Promise 形式交给 `ClipboardItem`，不影响用户激活）；超长图片在部分移动端浏览器上可能因 Canvas 尺寸限制而复制失败，此时会提示失败，可改用下载。
   - “系统分享”：仅在 `navigator.canShare({ files })` 返回 true 时显示，调用 `navigator.share` 打开系统分享面板。

图片生成接口失败、下载 / 复制 / 系统分享失败（包括浏览器拒绝操作的 `NotAllowedError`）都会在页面右下角显示提示；接口失败时会关闭弹窗并展示服务端返回的原因（例如转图工具不可用），可直接再次点击“分享”重试。用户在系统分享面板中主动取消（`AbortError`）不提示。

`share-image` 接口默认返回压缩后的 JPEG（quality 85，`Content-Type: image/jpeg`，下载文件名 `dsa-report-<id>.jpg`）。默认 `wkhtmltoimage` 引擎输出的完整报告长图 PNG 常达数 MB 至数十 MB（1080px 宽），转 JPEG 后体积大幅下降（本地 1080×7095 样例：PNG 30.7MB → JPEG 0.66MB），明显缩短首次打开弹窗的等待时间；Playwright 引擎输出的 PNG 本身压缩率较高，转 JPEG 收益较小（同类样例约减少 28%）。压缩复用企业微信长图使用的 Pillow 公共函数（`src/utils/image_compress.py`），带透明通道时铺白底；若未安装 Pillow、图片无法解码或高度超过 JPEG 单边上限（65500px），接口回退为原始 PNG，此时 `Content-Type` 为 `image/png`、扩展名为 `.png`，前端按实际格式处理。

Electron 桌面端同样展示“分享”按钮，但不依赖额外分发 `wkhtmltoimage`、`markdown-to-file` 或 Playwright。用户点击后，桌面 preload 通过受限 IPC 让主进程打开本地 `GET /api/v1/history/{record_id}/share-image-html`，使用 Electron 自带的隐藏 Chromium 窗口按完整页面高度截图为 PNG，随后打开与浏览器一致的预览弹窗（桌面端图片保持 PNG）。IPC 只接受正整数记录 ID，主进程只允许当前桌面窗口请求本次启动时确定的后端 origin（包括显式配置的局域网 `WEBUI_HOST`），HTML 响应使用 CSP 禁止脚本、外部资源和网络加载。

Web 手工生成不受 `MARKDOWN_TO_IMAGE_CHANNELS` 限制，但服务端仍需配置可用的 `MD2IMG_ENGINE`。桌面端手工生成复用 Electron，不读取 `MD2IMG_ENGINE`。Web 使用 Playwright 时先执行：

```bash
cd apps/dsa-web
npm ci
npx playwright install chromium
```

结构化数据用于精确填充名称、动作、评分、价格、宽度和板块等字段；Markdown 仍负责兼容旧调用以及计划、风险等文本章节。部分历史 JSON 只覆盖其中实际存在的字段，不会清空 Markdown 中已有的行情、技术参考或执行点位。模板不根据分数自行推导操作，也不补造价格或指标。字段为 `N/A`、`-`、空值或没有对应模块时，相关卡片自动隐藏。

## 个股卡字段映射

| 图片区域 | 项目字段 / 生成来源 | 填充规则 |
| --- | --- | --- |
| 股票名称、代码 | `AnalysisResult.name`、`AnalysisResult.code` | 直接读取结构化字段，Markdown 标题仅作回退 |
| 操作、评分、趋势 | `action_label` / `operation_advice`、`sentiment_score`、`trend_prediction`、`confidence_level` | 使用最终校准结果，评分范围 0–100，并标明结论置信度 |
| 核心结论 | `dashboard.core_conclusion.one_sentence` | 没有时隐藏 |
| 市场快照 | `market_snapshot` | 当前/收盘价、涨跌幅、量比、换手率按可用字段展示；数据源进入底部声明 |
| 执行计划 | `dashboard.battle_plan.sniper_points` | 只展示理想/确认买入、止损和首个目标价格；复杂触发条件保留在完整报告 |
| 技术参考 | `dashboard.data_perspective` | 展示均线状态、趋势分、MA5 乖离、支撑和压力；结构化量比/换手已进入快照时不重复展示冗长量能描述，旧记录缺失结构化量能时仍保留 Markdown 兜底 |
| 下一步观察 | `dashboard.phase_decision` | 展示行动窗口、下次检查时间和最多两条观察条件 |
| 催化与风险 | `dashboard.intelligence` | `positive_catalysts` 与 `risk_alerts` 最多各展示 2 条短摘要 |
| 持仓建议 | `core_conclusion.position_advice` | 只区分未持仓和已持仓，仓位、建仓、风控长文保留在完整报告 |

模板支持项目当前的中文、英文和韩文报告标签，海报栏目、指标标签和底部声明跟随报告语言。一个“决策仪表盘”只有一只股票时会自动使用个股卡；包含多只股票时保留多股报告布局，避免错误地把第一只股票当成整份报告。`强烈买入`、`Strong Buy` 等复合动作会保留完整动作标签。

## 大盘卡字段映射

| 图片区域 | 项目字段 / 生成来源 | 填充规则 |
| --- | --- | --- |
| 日期、市场范围 | `MarketOverview.date`、复盘区域 | 生成 A股/美股/港股/日股/韩股市场复盘标题；多市场报告逐段匹配 `market_review_payload.markets` |
| 市场信号 | `market_light.score`、`temperature_label`、`label`、`guidance` | 使用确定性市场灯号结果，不由模板二次评分 |
| 指数表现 | `MarketOverview.indices`、`color_scheme` | 最多展示 4 个主要指数的最新值和涨跌幅；结构化 payload 持久化生成时的 `green_up` / `red_up` 颜色语义 |
| 市场宽度 | `up_count`、`down_count`、`limit_up_count`、`limit_down_count`、`total_amount` | 仅在数据源支持且报告包含结构化数据时展示 |
| 信号拆解 | `market_light.dimensions` | 只展示 `available != false` 的确定性评分；不把不支持的维度占位分 50 当作真实数据 |
| 强弱板块 | `sectors.top`、`sectors.bottom` | 领涨、领跌各展示 Top 3；没有板块榜的市场自动隐藏 |
| 资金观察 | 复盘“资金与情绪”章节 | 提炼涨跌比、增量成交和资金风格，不把成交额或新闻推断伪装成净流入 |
| 重点跟踪 | 复盘“明日交易计划”的关注/回避方向 | 最多各展示 2 个板块或主题；当前 payload 没有 `leader_stocks`，因此不编造重点个股 |
| 明日策略 | 复盘“明日交易计划”章节 | 展示结论、仓位区间和失效条件，不与重点跟踪重复 |
| 风险提示 | 复盘“风险提示”章节 | 最多展示 2 条，过滤重复免责声明 |

## 手工填充或本地预览

模板仍接受项目生成的 Markdown；新链路会额外传入 `AnalysisResult.to_dict()` 或 `market_review_payload`。仅调试兼容回退时，可以准备一份最小个股报告：

```markdown
## 🟢 贵州茅台 (600519)

> 2026-07-31 15:00 | 评分: **72** | 看多

### 📌 核心结论

**买入**: 趋势偏强，等待回踩支撑后分批执行。

| 持仓情况 | 操作建议 |
| --- | --- |
| 空仓者 | 等待回踩确认，不追高。 |
| 持仓者 | 继续持有，跌破止损位退出。 |

### 🎯 作战计划

| 点位类型 | 价格 |
| --- | --- |
| 理想买入点 | 1420-1450 |
| 次优买入点 | 1380-1400 |
| 止损位 | 1350 |
| 目标位 | 1580 |
```

生成 HTML 预览：

```python
from pathlib import Path
from src.share_image import ShareImageBranding, build_share_image_html

markdown_text = Path("reports/example.md").read_text(encoding="utf-8")
branding = ShareImageBranding(
    repo_url="https://github.com/your-name/your-repo",
    xiaohongshu_url="https://example.com/my-xiaohongshu",
    xiaohongshu_handle="@我的账号",
    xiaohongshu_qr_path="assets/my-xiaohongshu-qr.png",
)
html = build_share_image_html(markdown_text, branding=branding)
Path("share-preview.html").write_text(html, encoding="utf-8")
```

按真实运行数据预览时传入结构化结果：

```python
html = build_share_image_html(
    markdown_text,
    structured_payload=analysis_result.to_dict(),
)
```

实际通知转 PNG 仍调用：

```python
from src.md2img import markdown_to_image

png_bytes = markdown_to_image(
    markdown_text,
    structured_payload=analysis_result.to_dict(),
)
```

大盘报告应沿用 `MarketAnalyzer` 生成的“盘面信号、指数结构、板块主线、消息催化、明日交易计划、风险提示”章节；不建议在外部另造一套字段名称，否则模板会按缺失字段处理。

## 视觉与内容边界

- 涨跌颜色优先使用结构化 payload 持久化的 `color_scheme`，旧记录则从最终报告颜色标记恢复；模板不按市场地区硬编码涨跌色。
- 分享图中的买入、止损和目标只保留可扫描的价格或“等待企稳”；完整条件始终保留在原报告中。
- 没有真实价格序列时不绘制伪 K 线；顶部仅保留非数据化的品牌光晕。
- 顶部品牌栏显示 `MIUSIAI | AI 股票分析`（英文 / 韩文报告分别为 `AI Stock Analysis` / `AI 주식 분석`），底部品牌标题显示 `MIUSIAI 股票智能分析系统`；品牌名为固定文案，不提供配置项。
- 项目地址（`SHARE_IMAGE_REPO_URL`）与小红书 URL、昵称、二维码路径（`SHARE_IMAGE_XIAOHONGSHU_*`）均为可选运行时配置，默认全部为空、底部不显示；二维码下只显示昵称，不显示数字 ID。仓库不内置任何项目地址、二维码或昵称兜底。
- 大盘报告在核心模块已成功提取时不重复附加完整 Markdown；额外的详情章节保留在原报告中，分享图只呈现结构化摘要。
- 图片底部固定说明“AI 生成，仅供研究交流，不构成投资建议”。
