# 手把手：用 AI 搭一个「论文日推网站」

> 这是一个可以直接照着搭的教程。参考实现就是本仓库（Paper Radar）：每天自动精选 0–5 篇论文、每篇配一段 AI 导读、支持“不相关 / 收藏”、自动归档、GitHub Pages 手机随时看，全程零服务器费用。
>
> 所有“研究偏好”都留了空，**请填成你自己的**再跑。

---

## 0. 你需要准备什么

- 一台电脑（macOS / Linux / Windows 均可），Python **3.11**
- 一个 GitHub 账号
- （可选）DeepSeek API Key —— 用于每天给论文写 AI 导读
- （可选）OpenAlex API Key —— 用于构建“历史经典论文 + 期刊”池

## 1. 把仓库变成你的

```bash
git clone https://github.com/EverWhy-lab/paper-radar.git
cd paper-radar
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

> 想保留自己的 git 历史：在 GitHub 上 **Fork** 本仓库，再 clone 你自己的地址即可。

## 2. 填写你的研究偏好（最重要的一步）

所有偏好都在 `config/research_profile.yaml` 里。**把下面每一项改成你的**：

### 2.1 关注主题与关键词（`scoring.topics`）

每行一个主题，`weight` 是权重（越高越优先），`keywords` 是匹配标题/摘要的关键词：

```yaml
topics:
  - id: your_topic_a          # 主题编号，任意英文字符
    label: "你的主题A名称"     # 显示用
    weight: 20                # 建议 10–20
    keywords:
      - 你的关键词1
      - 你的关键词2
```

示例（机器人方向）：

| 主题 | 关键词示例 |
|---|---|
| VLA / 机器人基础模型 | vision-language-action, robot foundation model… |
| 世界模型 / 具身推理 | robot world model, embodied planning, long-horizon manipulation… |
| 人形全身智能 | humanoid loco-manipulation, whole-body imitation… |
| 策略学习与后训练 | diffusion policy, VLA post-training, continual robot learning… |
| 灵巧多模态操作 | dexterous manipulation, tactile policy, bimanual manipulation… |
| 机器人数据与 Sim-to-Real | robot data scaling, cross-embodiment data, sim-to-real… |
| 规划、控制与状态估计（支撑方向） | motion planning, MPC/WBC, trajectory optimization, CBF, InEKF… |

### 2.2 核心主题与泛化词（`recommendations.core_topic_ids` / `generic_keywords`）

- `core_topic_ids`：把 Robot AI 核心主题 id 填进来。普通前沿与期刊 lane 必须命中核心主题；独立的 `model_based_recent` lane 可以接纳不命中 AI core、但有明确机器人语境和强规划/控制/估计方法信号的近期论文；
- `generic_keywords`：这些词太常见，**不能单独作为入选理由**（比如“reinforcement learning”），建议照抄示例。

`robotics_context.positive_terms` 应只放能明确建立机器人语境的词。不要把
`world model`、`foundation model`、`LLM`、`reinforcement learning` 等泛 AI
词单独当作机器人论文的充分条件。

### 2.3 排除词（`scoring.exclusions` / `recommendations.excluded_terms`）

填上你**不想看到**的方向，命中即扣分/排除：

```yaml
exclusions:
  - term: 你不想要的主题
    penalty: 18      # 扣分力度，建议 15–25
```

### 2.4 每日上限与门槛（`recommendations.daily_mix`）

默认：总推荐 ≤5；前沿新论文 ≤2；前沿与新期刊合计 ≤3；方法前沿 ≤1；历史基础论文 ≤1；综述/知识地图 ≤1。方法前沿不是 quota，没有足够好的论文时为 0，而且不占 Robot AI recent 的三篇 ceiling。历史发现使用滚动 10 年 active-reading window，并偏好近 5 年；超过 10 年的论文只保留为背景谱系，不进入每日推荐。门槛不建议低于默认值，否则页面会“凑数”。

`model_based_recent.method_subtopics` 应使用克制、明确的方法族，例如 motion/kinodynamic planning、MPC、WBC、trajectory optimization、safety-critical control 和 robot state estimation。不要用普通的 `planning` 或 `optimization` 作为强信号；所有候选仍必须通过领域语境 gate，避免 building energy、power system 和 process control 混入。

### 2.5 导读读者画像（`llm_analysis.reader_profile`）

DeepSeek 不参与选稿。把读者的主要与次要关注方向写在 YAML，而不是写死在 Python prompt 中：

```yaml
llm_analysis:
  language: "en"
  abstract_char_limit: 3000
  reader_profile:
    primary_focus:
      - 你的核心研究方向
    secondary_focus:
      - 你仍希望持续追踪的方法方向
```

### 2.6 时区与运行时间（`site.timezone`）

```yaml
site:
  name: "你的网站名"
  timezone: "Asia/Shanghai"   # 填你所在时区
  github_repo: "你的GitHub用户名/仓库名"
```

### 2.7 种子论文（可选，用于历史论文扩展）

把你认可的代表作加为种子，系统会沿引用/被引/相关做一跳扩展：

```bash
.venv/bin/python -m paper_radar history seed add <arXiv号或DOI>
.venv/bin/python -m paper_radar history discover
```

### 2.8 期刊订阅（可选）

想盯特定期刊？在 `config/research_profile.yaml` 的 `journals.sources` 里按示例填期刊名和 OpenAlex 来源 ID（可在 openalex.org 搜期刊获取）。

## 3. 配置 API Key

**只从环境变量读取，不会写入任何文件：**

```bash
export OPENALEX_API_KEY='你的key'   # 历史发现用
export DEEPSEEK_API_KEY='你的key'   # AI 导读用
```

发布到 GitHub 后，在仓库 **Settings → Secrets and variables → Actions** 里新增同名 Secret。

## 4. 本地先跑通

```bash
.venv/bin/python -m paper_radar run        # 抓 arXiv、选稿、生成页面
.venv/bin/python -m paper_radar serve      # 打开 http://127.0.0.1:8000
```

## 5. 发布到 GitHub

```bash
git add -A && git commit -m "my paper radar" && git push origin main
```

推送后仓库自带的 Actions 会自动生效：

| Workflow | 干什么 |
|---|---|
| `daily-run` | 每天 12:30（北京时间）自动抓取、选稿、导读、发布 |
| `openalex-discover` | 每周一扩充历史池 + 期刊 |
| `openalex-refresh` | 每月刷新引用指标 + 清理旧候选 |
| `feedback` | 处理“不相关/收藏”同步（仅仓库主人有效） |
| `tests` | 代码改动时跑测试 |

最后打开仓库 **Settings → Pages → Source: GitHub Actions**，发布后手机访问：

```text
https://<你的用户名>.github.io/<仓库名>/
```

## 6. 页面交互（可选）

- 每张卡片有 **Save（收藏）** 和 **Not Relevant（不相关）** 按钮；
- 点击后状态会记录在本地，点导航栏「同步反馈」会打开一个预填好的 issue，提交一次即持久生效；
- 历史推荐在 **History** 页，按时间倒序排列。

---

## 附录 A：DeepSeek 导读提示词

项目实际使用的提示词（`src/paper_radar/providers/deepseek.py`），可直接复制修改：

**中文版**

```text
你是研究者的阅读导读助手。规则引擎已经完成选稿；你不参与筛选、排序或质量判定。只能依据提供的 title、abstract 和 metadata，不得编造实验数字、数据集、模型规模、训练资源或硬件，不得把作者 claim 写成独立验证事实。method、survey/review、benchmark/dataset 分别按其文档类型组织重点。每篇写一个约 120–220 个汉字、通常 3 句话且最多 4 句话的连贯段落，并只输出 {"analyses":[{"paper_id":"...","takeaway":"..."}]}。
```

**英文版（当前默认，输出英文并保留论文原词）**

```text
You are a reading-guide assistant. The rule engine has already selected the papers; you do not screen, rank, or judge quality. Use only supplied title, abstract, and metadata; do not invent results, datasets, model scale, training resources, or hardware, and do not present author claims as independently validated. Adapt the guide to method, survey/review, or benchmark/dataset document type. Write one coherent 80–130-word paragraph, usually three sentences and no more than four, preserve paper terminology, and output only {"analyses":[{"paper_id":"...","takeaway":"..."}]}.
```

想换成自己的研究方向，优先修改 `llm_analysis.reader_profile`；中英文 grounding 和文档类型规则会保持一致。

## 附录 B：常见问题

- **arXiv 返回 429**：GitHub 共享 IP 偶发限流，系统最多尝试 4 次，退避等待为 5、10、10 分钟（累计最多 25 分钟）；还失败就等下次运行。
- **定时任务偶尔延迟**：GitHub Actions 的 `schedule` 在负载高时会排队，实际运行可能晚几十分钟到几小时，属正常。
- **费用**：GitHub 公共仓库 Actions 免费；DeepSeek/OpenAlex 只有可选功能会用一点配额（导读每天一次、历史发现每周几次）。
- **改用户名**：GitHub 会自动把旧地址 301 到新地址；记得同步更新 `site.github_repo` 和本地 `git remote`。
- **AI 导读没出现**：检查是否配置了 `DEEPSEEK_API_KEY`；接口失败不影响页面发布。

---

祝你也搭出自己的论文日推网站。有问题欢迎在仓库提 Issue。
