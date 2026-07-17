---
name: datasource-eval
description: 评测 ani-subs 的 CSS selector 数据源并生成图文报告。跨多部番剧对每个 web selector 源跑完整解析流程 (searchSubjects→…→extractVideo)，用 Animeko 桌面播放器 (VLC) 真实播放每条线路测可播放性/分辨率/码率/起播耗时，多点截图后由 subagent 逐张看图判断博彩广告，最后生成三层报告 (总索引 + 每源线路拆解 + 每线路图文页)，含成功/失败原因与评测日期。当用户要"评测数据源/测试所有源/评估 selector 源/生成数据源报告/看哪些源能用/查数据源广告"时使用。
---

# ani-subs 数据源评测

对 `subs/web/t0…t4/` 下的 CSS selector 数据源做端到端评测并生成图文报告。报告落在 `reports/<评测日期>-css-eval/`。

## 依赖: animeko 仓库的 MCP

本 skill 靠 **animeko `ani` 仓库**里的 `datasource-test-mcp` 工具驱动真实解析与播放。它是一个 stdio MCP server，提供 `search_subjects` / `get_trends` / `get_subject_episodes` / `validate_selector_config` / `selector_resolve_episode` / `probe_video` 等工具。

**定位方式**（脚本 `scripts/lib.py` 自动做，此处说明供排障）:
1. 环境变量 `ANIMEKO_MCP_BIN`（若设置且存在）
2. **sibling 目录** `../ani/tools/datasource-test-mcp/build/install/datasource-test-mcp/bin/datasource-test-mcp`（ani-subs 与 ani 通常同在 `animeko/` 下并列）
3. `~/Projects/animeko/ani/...`

**找不到时**: 需要先构建 MCP，或询问用户 animeko 项目位置。
```bash
# 若已知 animeko/ani 位置, 在 ani 仓库根构建:
cd <animeko>/ani && ./gradlew :tools:datasource-test-mcp:installDist
```
构建产物在 `ani/tools/datasource-test-mcp/build/install/.../bin/datasource-test-mcp`。
`probe_video` 用真实 VLC 播放，需系统装 **VLC 3.0.18**（macOS: `/Applications/VLC.app`）；没装则退化为纯 HTTP 探测（拿不到分辨率/播放耗时）。

**若在 ani-subs 里找不到 sibling `ani` 目录，先问用户 animeko/ani 仓库在哪，不要瞎猜路径。**

脚本还需要 Python 的 Pillow（截图拼图，非必需）；MCP 与 VLC 是硬依赖。

## 流程

所有脚本在 `.claude/skills/datasource-eval/scripts/` 下，第一个参数都是报告目录。

### 1. 选番剧 + 建报告目录

选 3–5 部**近年完结的热门番**（各源收录率高，能区分"源失效"与"没收录这部番"）。用 MCP 拿 subjectId 和 ep1 的 episodeId：
- `get_trends` 看当前热门，或 `search_subjects` 按名字搜 → 拿 `subjectId`
- `get_subject_episodes` → 拿该番 sort=1 那集的 `episodeId`

建报告目录并写 `meta.json`（**评测日期决定目录名**，用今天的日期）:
```bash
DATE=$(date +%Y%m%d)                      # 目录用 YYYYMMDD
R=reports/${DATE}-css-eval
mkdir -p "$R"
# 写 meta.json (评测日期 + 番剧列表), 例:
cat > "$R/meta.json" <<'JSON'
{
  "evalDate": "2026-07-05",
  "subjects": [
    {"subjectId": 329906, "episodeId": 1088220, "name": "间谍过家家"},
    {"subjectId": 400602, "episodeId": 1227087, "name": "葬送的芙莉莲"}
  ]
}
JSON
```
`evalDate` 用 `YYYY-MM-DD`，会写进报告头。名字里别带 `/`（会当路径分隔）。

### 2. 批量解析 + 实播 (慢, 后台跑)

对全部 selector 源 × 每部番，跑全流程解析 + **每条 resolved 线路**全量 VLC 实播（不设条数上限，采集分辨率/码率/起播）+ 单帧广告启发式（仅参考，报告不采信）。断点续跑安全。

```bash
python3 .claude/skills/datasource-eval/scripts/run_eval.py "$R"
```
产物: `$R/subjects/<id>-<名>/{sources/*.json 逐源 trace, summary.json 汇总, driver.log}`。
耗时随线路数线性增长（每条线路实播 ~4s + 解析开销），每部番可达十几分钟。建议 `run_in_background` 并用日志盯进度。

Ani API 的搜索接口冷启动慢且会限流；MCP 工具内部已重试，脚本对超时也会重启 server。

### 3. 深度采样 (为看图判广告)

对**可用源**（跨番至少成功 1 次），按**每部番 × 每条线路全覆盖**重新解析并 VLC 长播 28s、在 0/3/8/15/25s 各截一帧——每条线路都必须有图可供视觉判定，不许抽样。

```bash
python3 .claude/skills/datasource-eval/scripts/deep_sample.py "$R"
```
产物: `$R/deep/<tier>-<源>/<番>/<线路>/frames/frame_XXs.png` + 每源 `deep.json`。

### 4. subagent 逐张看图判广告 (关键)

这些盗版源常把**博彩广告**（"XXXX.com 赞助发布 棋牌/真人娱乐/捕鱼/投注"横幅，或整屏前贴片）烧录进视频。OCR 对亮背景水印漏报严重，**必须靠视觉判读**。

**不要自己在主循环里读大量截图**（容易在批量时编造结果、且图会因 stale 被清理）。改为**每条线路派一个 Explore subagent** 读它的几帧、返回结构化判定：

- 枚举 `deep/*/deep.json` 里每个 `(源, 线路)` 的 `frame_00s/08s/15s` 路径。**必须覆盖全部 (源, 线路) 组合**——凡实播成功过的线路都要有视觉判定；判定只认 agent 真实读图，`adSuspicion` 等算法启发式绝不能作为判定依据（gen_report 也不读它）。
- 对每条线路 `Agent(subagent_type="Explore")`，prompt 让它 Read 这几帧，按固定格式回 `AD_LEVEL`（none/medium/high）、`POSITION`（top/bottom/fullscreen/none）、`EVIDENCE`（看到的域名或赌博词原文 / "clean anime + subtitles" / 正规台标如 bilibili独播、Muse木棉花）、`PERSISTS`。可 6–8 个并行发。
- 汇总口径: **底/顶横幅水印 = medium，开头整屏前贴片 = high，干净 = none**（subagent 常把持续横幅报成 high，按 POSITION 归一）。

把判定写入 `$R/combined/frames_verdicts.json`（**程序化写，不靠记忆**）:
```json
{
  "_note": "subagent 逐张看图判定. ad: none/medium/high. 键为 '源' 或 '源/线路'.",
  "_channel_level": {
    "叽哔动漫/叽哔1线": {"ad": "medium", "note": "底部博彩横幅 07403.com 赞助发布 棋牌/真人娱乐/捕鱼, 仅片头"},
    "去看吧": {"ad": "none", "note": "干净正片, bilibili 独播台标"}
  }
}
```
键可用**源级**（整源统一，如 `"去看吧"`）或**线路级**（`"源/线路"`，同源不同线路差异大时用，如 omofun 高清线路干净但超快线路是博彩前贴片）。生成报告时线路级优先，未判读的线路显示"未判定"，不会拿不可靠的 OCR 启发式充数。

### 5. 生成报告

```bash
python3 .claude/skills/datasource-eval/scripts/gen_report.py "$R"
```
产物（全部在 `$R/`）:
- `README.md` — 总索引: 评测日期 → **推荐线路**（每源最佳的无广告稳定线路）→ 源级汇总表 → **各源线路拆解**（每线路 × 每部番实播 ✅/❌/— + 失败原因）→ **搜索阶段失败** 与 **解析/匹配阶段失败** 两节带原因。
- `sources/<tier>-<源>.md` — 每源线路拆解页。
- `channels/<tier>-<源>-<线路>.md` — 每线路图文页: 广告判定 + 播放采样画廊 + 媒体信息 + 播放性能 + 跨番实播/失败原因。

改了 `frames_verdicts.json` 后重跑 gen_report 即可刷新，无需重测。

## 报告规范 (gen_report.py 实现此规范)   

报告由 `gen_report.py` **确定性生成**，不手写。以下是它必须产出的结构与约定——**这是标准**：改脚本或换实现时以此为准，保证每次评测产出一致的文档。

**`README.md`（总索引，顺序固定）**:
1. 标题 + `**评测日期: YYYY-MM-DD**`（来自 meta.json）+ 一句话方法说明 + 测试番剧列表。
2. 三个计数：稳定可用 / 部分可用 / 全部失败。
3. `## 🏆 推荐线路` — 每个源挑**一条**最佳线路（无广告 + 有实测数据 + 跨番成功 ≥ n-1 + 画质高 + 起播快），列 源·**线路**·Tier·分辨率·码率·起播·可播 N/n。直接告诉用户播哪条。
4. `## 📶 线路能力分级 (Tier)` — **每条实测线路一行**的最终 tier 表（与 `subs/web` 的 t0–t4 目录分层无关）。列: `源(线路)`（如 `omofun111(独家蓝光X)`，同源多线路拆成多行）· Tier · 分辨率 · 码率 · 编码 · 起播 · 广告 · 每部番 ✅/❌/—。分级标准（从上往下取第一个满足的；无广告只认视觉判定，未判定不算无广告；缺码率/起播数据视为不满足该项）:
   - **T0**（最高优先，该源查询成功客户端可直接选择、无需等待其他源）: 无广告 + 1080P + 码率 ≥ 1.8M + 起播 ≤ 3s
   - **T1**（高优先）: 无广告 + 1080P + 码率 ≥ 1.5M + 起播 ≤ 5s
   - **T2**（普通）: 无广告 + 1080P + 码率 ≥ 1.0M + 起播 ≤ 5s
   - **T3**: 无广告 + 1080P + 码率 ≥ 1.0M + 起播 ≤ 8s
   - **T4**: 1080P
   - **T5**: 无要求
   排序: Tier → 可播番数 → 最佳线路排序键。
5. `## 全部可用源` — 一行一源，按广告轻→成功率排序，含每部番 ✅/❌ 列 + 广告等级（`*` 表示另有更脏线路）。**分辨率/码率/起播列 = 该源"最佳线路"的实测值**，并列出最佳线路名。最佳线路定义（逐级比较，高优先在前）: ①无广告（视觉判定；未判定排最后，不吃算法启发式）②分辨率高 ③码率高 ④起播快；只在真实播出来过的线路中选。分辨率取该线路跨番实测**众数**，码率/起播取**中位数**。
6. `## 各源线路拆解` — 每个可用源一个小节，表格**每条线路 × 每部番**（列 = 番剧简称），单元格 ✅/❌/—，外加广告/分辨率/码率/起播/失败原因列，`⭐` 标记总表所用最佳线路。**这一节让主报告自包含，不必点进源页**。
7. `## 🔒 搜索阶段失败` — searchSubjects 就失败的源，表格 源·Tier·原因（验证码 / 域名失效 / 403 等）。
8. `## ❌ 解析/匹配阶段失败` — 搜到但后续步骤失败的源，表格 源·Tier·失败阶段·原因（selector 失配 / js 伪链接 / 视频解析失败 / 播放探测失败等）。

**`sources/<tier>-<源>.md`（源页）**: 顶部推荐线路一句话 + `## 线路拆解`（每线路 × 每部番，同 README 第 5 节）+ `## 跨番解析`。

**`channels/<tier>-<源>-<线路>.md`（线路页）**: 结论行 + `## 广告判定`（视觉判读结论）+ `## 播放采样`（0/3/8/15/25s 截图画廊，图文并茂）+ `## 媒体信息`（分辨率/编码/帧率/音频/时长/码率）+ `## 播放性能`（打开/起播/首帧/卡顿）+ `## 跨番实播`（每番 结果 + 失败原因）。

**符号约定（全报告统一）**:
- 广告：`无`/`轻`/`中`/`重`；源级 `*` = 该源另有更脏线路。
- 数值口径：分辨率 = 该线路跨番实测众数；码率/起播 = 跨番实测中位数（VLC 实播采集，含 deep 长播数据）。
- 列级 `-` = 该线路无此项数据（从未成功播放，故无分辨率/码率/起播/广告）。
- 番格 `✅` = 可播；`❌` = 解析到但播放失败；`—` = 该番没解析到这条线路（线路名轮换）。
- 失败原因归纳成人话（"WebView 未匹配到视频 URL""视频 URL 返回 403(防盗链)""站点需人机验证"…），不暴露裸异常类名。

**广告判定来源**: 只用 `frames_verdicts.json` 的视觉判读（subagent 看图），线路级优先于源级；未判读的线路显示"未判定"而非拿 OCR 启发式充数。

## 报告怎么读

- **推荐线路**: 直接告诉用户播哪个源的哪条线路（无广告 + 跨番稳定 + 画质好）。
- **线路能力分级 (T0–T5)**: 给客户端排优先级用的硬指标分级（见上节标准），T0 = 查询成功即可直接播、无需等待其他源。注意与 `subs/web` 的 t0–t4 **目录**分层是两回事。
- **可播证据**: ✅ 含 run_eval 快测与 deep 长播两路实播成功；deep 失败不记 ❌（二次解析可能只是 URL 过期）。
- **广告等级**: 无/中/重；`*` 表示该源另有更脏线路。博彩水印是合规风险重点。
- **失败原因**: "搜索阶段失败"（验证码/域名失效 → 基本没救）vs "解析阶段失败"（selector 失配/js 伪链接 → 配置可修）——给维护者明确修复方向。
- 单元格 `-` = 该线路无此项数据（从未成功播放）; 番格 `—` = 该番没解析到这条线路（线路名轮换）, `❌` = 解析到但播放失败, `✅` = 可播。

## 排障

- **找不到 MCP**: 见上"依赖"节，构建或问用户 animeko/ani 位置。
- **全部源搜索失败**: 可能 Ani API 限流/元数据源问题，或番剧 subjectId/episodeId 填错——先用 MCP `get_subject_episodes` 核对。
- **probe_video 拿不到分辨率**: 系统没装 VLC，或视频 URL 已过期（重跑 deep_sample 会重新解析）。
- **广告判定全"未判定"**: `frames_verdicts.json` 没写或键名对不上（源名/线路名要与 deep.json 完全一致）。
