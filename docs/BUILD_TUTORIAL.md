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
| 控制与优化（支撑方向） | whole-body control, model predictive control, trajectory optimization… |

### 2.2 核心主题与泛化词（`recommendations.core_topic_ids` / `generic_keywords`）

- `core_topic_ids`：把上一步定义的主题 id 填进来。**只有命中核心主题的论文才会入选**；
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

默认：总推荐 ≤5；前沿新论文 ≤2；前沿与新期刊合计 ≤3；历史基础论文 ≤1；综述/知识地图 ≤1。历史发现使用滚动 10 年 active-reading window，并偏好近 5 年；超过 10 年的论文只保留为背景谱系，不进入每日推荐。门槛不建议低于默认值，否则页面会“凑数”。

### 2.5 时区与运行时间（`site.timezone`）

```yaml
site:
  name: "你的网站名"
  timezone: "Asia/Shanghai"   # 填你所在时区
  github_repo: "你的GitHub用户名/仓库名"
```

### 2.6 种子论文（可选，用于“经典论文”扩展）

把你认可的代表作加为种子，系统会沿引用/被引/相关做一跳扩展：

```bash
.venv/bin/python -m paper_radar history seed add <arXiv号或DOI>
.venv/bin/python -m paper_radar history discover
```

### 2.7 期刊订阅（可选）

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
你是一位个人阅读助手。你只会收到规则引擎已经选出的 0-5 篇论文。对每篇论文写一段 3-5 句话的 Takeaway，分析这篇论文对用户的价值，必须覆盖两个方面：(1) 论文本身：在什么研究背景下解决什么问题、取得了什么关键成果；(2) 对用户的意义：这篇论文和用户的研究方向有什么关系、用户能拿它来做什么或在它基础上做什么。两方面的内容都要有，但写成一段连贯文字，不要机械罗列条目。只输出一个 JSON 对象：{"analyses":[{"paper_id":"...","takeaway":"..."}]}。要求客观中立，不要使用“经典”“最佳”“最重要”等绝对化评价；引用数和评分只是筛选信号，不是质量标签。
```

**英文版（当前默认，输出英文并保留论文原词）**

```text
You are a personal reading assistant for robotics research. You only receive 0-5 papers already selected by a rule engine. For each paper, write one coherent Takeaway in English (3-5 sentences) analyzing the paper's value to the user, covering two aspects: (1) the paper itself: the research background, the problem it solves, and the key results it reports; (2) its meaning to the user: how it relates to the user's research and what the user can do with it or build on it. Cover both aspects in one flowing paragraph. Use professional terminology exactly as it appears in the paper. Output only one JSON object: {"analyses":[{"paper_id":"...","takeaway":"..."}]}. Be objective and neutral; never use absolute labels such as "classic", "best", or "most important"; citation counts and scores are screening signals, not quality labels.
```

想换成自己的研究方向，把提示词里“robotics research / 腿部机器人…”等表述改成你的领域即可。

## 附录 B：常见问题

- **arXiv 返回 429**：GitHub 共享 IP 偶发限流，系统最多尝试 4 次，退避等待为 5、10、10 分钟（累计最多 25 分钟）；还失败就等下次运行。
- **定时任务偶尔延迟**：GitHub Actions 的 `schedule` 在负载高时会排队，实际运行可能晚几十分钟到几小时，属正常。
- **费用**：GitHub 公共仓库 Actions 免费；DeepSeek/OpenAlex 只有可选功能会用一点配额（导读每天一次、历史发现每周几次）。
- **改用户名**：GitHub 会自动把旧地址 301 到新地址；记得同步更新 `site.github_repo` 和本地 `git remote`。
- **AI 导读没出现**：检查是否配置了 `DEEPSEEK_API_KEY`；接口失败不影响页面发布。

---

祝你也搭出自己的论文日推网站。有问题欢迎在仓库提 Issue。
