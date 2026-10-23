# HMFMS 用户指南

**Heavy Metal Flux Model in Soil — 配置驱动的农田重金属质量平衡模型**

| 项 | 内容 |
|---|---|
| 文档版本 | 1.0 |
| 对应软件版本 | `0.2.0-alpha`（PEP 440: `0.2.0a1`） |
| 输出契约版本 | `schema_version = 4` |
| 适用读者 | 使用 HMFMS 做农田重金属质量平衡研究的科研人员与研究生 |
| 文档范围 | 安装、配置、输入、运行、输出、观测对比、演示、排错 |

> **重要提示：先读这一条**
>
> HMFMS 当前版本**没有实现淋溶、径流、侵蚀、风蚀、挥发与水分平衡**，这些过程返回零通量。
> 因此模型给出的是土壤重金属累积的**上限**，不是预测值。对 Pb 尤其危险：其主要的输出途径
> 是地表径流，关闭后会被显著高估。运行日志会按元素分别给出警告，但**不能替代使用者的判断**。
>
> 本软件是研究原型，不适用于法规、食品安全或任何需要可追责结论的场景。

---

## 目录

1. 概述
2. 安装与自检
3. 快速开始
4. 工作流与目录结构
5. 模型说明（用户视角）
6. 配置参考
7. 输入数据准备
8. 运行模型
9. 输出参考
10. 观测对比
11. 十年演示场景
12. 排错与 FAQ
13. 最佳实践、引用与许可
- 附录 A 单位换算
- 附录 B 参数键命名
- 附录 C 输出列全清单
- 附录 D 配置字段速查
- 附录 E 错误信息格式
- 附录 F 术语表
- 附录 G 文件树与示例清单
- 附录 H 版本与兼容性

---

# 1. 概述

## 1.1 HMFMS 是什么

HMFMS 是一个**配置驱动、质量守恒、过程可插拔**的农田重金属质量平衡模型。它回答的问题是：

> 给定一块田、一组输入通量（沉降、施肥、灌溉、秸秆还田）和一组输出通量（作物收获、秸秆外运），
> 耕层土壤中 Cd、Pb 等重金属的总量会怎样逐日、逐月、逐年变化？

模型只处理“金属落到田里之后发生了什么”，**不模拟大气扩散、沉降生成、作物生长或水文过程**。
干/湿沉降由 WRF-EMEP 等外部模式提供，HMFMS 负责从外部数据开始，逐日推进土壤储量。

## 1.2 HMFMS 不是什么

- 不是区域大气化学或沉降模型；
- 不是作物生长/物候模型；
- 不是地下水或流域水文模型；
- 不是参数自动率定工具；
- 不是法规风险评估工具。

## 1.3 科学范围

| 维度 | v1 范围 |
|---|---|
| 空间 | 单田块 |
| 土壤 | 单层 0–20 cm（状态量保留土层维度） |
| 时间 | 日步长，显式 Euler |
| 元素 | 任意，由配置列出（示例为 Cd、Pb） |
| 形态 | 土壤重金属**总量**；有效态只作诊断，不参与主状态 |
| 作物吸收 | 收获日一次性移除；固定器官浓度或 BCF |
| 输出 | 日/月/年三张频率表 + 参数溯源 + 可选观测对比 |

## 1.4 三条设计原则

1. **质量守恒优先。** 每个时间步检查
   `期末储量 − 期初储量 = 累积输入 − 累积输出`；残差超过容差即报错终止。
   储量永不裁剪为 0——负储量说明某处算错了，掩盖它比崩溃更危险。
2. **参数可追溯。** 每次运行输出 `resolved_parameters.csv`，逐参数记录取值、单位、
   命中层级、来源与置信度。任何默认值都能回答“它是哪来的”。
3. **元素、作物、器官、肥料品类、水源都是数据。** 代码里没有 `Cd`、没有 `grain`、
   没有 `pig_manure`。新增一种元素或作物，原则上只需增加配置与参数库条目。

## 1.5 软件结构一图流

```
配置与输入文件
   │  装载 + 校验（pydantic / CSV 解析 / 参数解析链）
   ▼
初始状态 M[e,l] = C0 × 容重 × 层厚
   │
   ▼
逐日循环（Δt = 1 d）
   ├─ 取当日驱动与事件
   ├─ 对状态拍快照
   ├─ 所有过程模块读同一快照，返回通量
   ├─ 汇总、非负检查、更新状态
   └─ 闭合检查、写缓冲
   │
   ▼
按 output.interval 聚合 → daily / monthly / annual CSV
   │
   ├─ resolved_parameters.csv
   └─ （独立命令）hmfms validate → 报告 / 图 / 匹配明细
```

## 1.6 版本与兼容性

- 当前版本 `0.2.0-alpha`，输出 `schema_version = 4`。
- 相邻版本之间，**输出列只增不删、不改名**；需要变更时提升 `schema_version`。
- 配置文件对未知字段**直接报错**，不静默忽略。
- 具体版本差异见附录 H。

---

# 2. 安装与自检

## 2.1 系统要求

| 项 | 要求 |
|---|---|
| Python | ≥ 3.10（开发与测试使用 3.13） |
| 操作系统 | Windows / Linux / macOS（本项目在 Windows 上完整测试） |
| 内存 | 常规单田块运行 < 500 MB；十年日序列亦很小 |
| 磁盘 | 代码与示例 < 10 MB；输出取决于频率与年限 |
| 网络 | 仅安装依赖时需要 |

## 2.2 用虚拟环境安装

推荐从仓库根目录安装为 editable 包，这样示例与 `demo` 命令可直接使用。

**Windows（PowerShell）**

```powershell
cd "Heavy Metal Flux Model"
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

**Linux / macOS**

```bash
cd "Heavy Metal Flux Model"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## 2.3 依赖

| 包 | 用途 |
|---|---|
| `numpy` | 数值数组（NetCDF 适配器、测试数据） |
| `pandas` | 可选的下游分析（模型核心不依赖） |
| `PyYAML` | 配置与参数库 |
| `netCDF4` | NetCDF 沉降输入 |
| `matplotlib` | 观测对比图 |
| `pydantic` | 配置结构校验 |
| `pytest` | 测试（`[dev]` 额外依赖） |

## 2.4 安装自检

```bash
python -m pytest
python -m hmfms demo --no-validate
```

预期：

- 测试全部通过（发布版本为 251 个）；
- `demo` 运行 3653 天，Cd 年增量约 `0.0048 mg·kg⁻¹·a⁻¹`；
- 输出目录出现 `daily.csv`、`monthly.csv`、`annual.csv`、`resolved_parameters.csv`。

> **注意**：`hmfms demo` 读取仓库内的 `examples/paddy-10y/`。若只安装了不含 `examples/` 的
> wheel 包，`demo` 不可用；请克隆仓库并以 editable 方式安装，或直接用自己的配置运行 `hmfms run`。

## 2.5 升级、卸载与固定版本

```bash
pip install -e ".[dev]"          # 在仓库内升级
pip uninstall hmfms              # 卸载
```

正式研究请固定到某个 tag（例如 `v0.2.0-alpha`），并在论文/报告中记录版本与配置指纹。

## 2.6 获取帮助

- 命令帮助：`python -m hmfms --help`、`python -m hmfms run --help`；
- 参数默认值与来源：`docs/parameters.md`；
- 问题报告：https://github.com/YYC-JNU/HMFMS/issues
- 报告数值问题时请附上 `daily.csv`、`resolved_parameters.csv`、`model.yaml` 与配置指纹。

---

# 3. 快速开始

## 3.1 五分钟跑通十年演示

```bash
python -m hmfms demo
```

该命令会：

1. 读取 `examples/paddy-10y/model.yaml`；
2. 运行 2015-01-01 至 2024-12-31 共 3653 天；
3. 写日/月/年三张表与参数溯源表；
4. 读取 `examples/paddy-10y/validation.yaml`，执行一次观测对比。

只跑模型：

```bash
python -m hmfms demo --no-validate
```

## 3.2 生成的文件

```
examples/paddy-10y/
├─ output/
│  ├─ daily.csv                 逐日
│  ├─ monthly.csv               逐月
│  ├─ annual.csv                逐年
│  └─ resolved_parameters.csv   参数溯源
└─ validation/
   ├─ report.md                 观测对比报告
   ├─ comparison.png            时间序列 + 1:1 散点
   └─ matched_points.csv        逐点匹配明细
```

运行日志会输出：

- `在未启用淋溶、径流、侵蚀的条件下，本结果为土壤重金属累积的上限。`
- `一般警告：元素 Cd 关闭径流。…`
- `强警告：元素 Pb 关闭径流。…`

以及三张表的路径、配置指纹、天数和期末闭合残差。

## 3.3 最小算例

```bash
python -m hmfms run examples/minimal/model.yaml
```

`examples/minimal` 是一个 10 天合成算例，包含沉降、施肥、灌溉、收获与秸秆事件，
用于检查数据接线与输出列；**数值不代表任何真实田块**。

## 3.4 三个最该先看的输出

1. `daily.csv` 的 `soil_concentration_<元素>_L<层>_mg_kg`：土壤含量时间序列；
2. `daily.csv` 的 `closure_error_<元素>_mg_m2`：闭合残差，应为 0 或 ~1e-15 量级；
3. `resolved_parameters.csv` 的 `tier` / `source`：每个参数到底从哪来。

## 3.5 第一次使用最常见的五个错误

| 现象 | 常见原因 | 处置 |
|---|---|---|
| `ParameterSourceError: <key> 缺失` | 参数库没有该键，事件行也没有覆盖 | 补库条目或加覆盖列；按报错指向的文档小节核对 |
| `缺少必需列 [...]` | 事件表/CSV 列名拼错 | 按报错列出的实际列名修正 |
| `DuplicateEventError` | 同日同作物同器官出现两个收获/秸秆事件 | 删除重复行；一次收获一行一个器官 |
| `FluxImbalanceError` | 收获/移除量大于储量，通常是单位或产量错了 | 检查 `yield` 单位、初始含量、容重与层厚 |
| 结果量级差 10 倍/100 倍 | μg 与 mg、g/ha 与 kg/ha 混淆 | 核对 `units` 声明与 `magnitude_check` 警告 |

---

# 4. 工作流与目录结构

## 4.1 一次运行的五个阶段

| 阶段 | 做什么 | 失败时 |
|---|---|---|
| 0 准备 | 写配置、CSV、参数库 | — |
| 1 装载 | 读 YAML/CSV、校验结构、解析参数、建事件索引 | 立即报错，指出文件:行 |
| 2 初始化 | `M = C0 × 容重 × 层厚`，记录起始储量 | 初始含量缺失即报错 |
| 3 日循环 | 取驱动/事件 → 快照 → 各模块算通量 → 汇总 → 非负与闭合检查 → 更新 | 非负/闭合失败即报错 |
| 4 收尾 | 按频率聚合、写 CSV、写参数溯源 | 磁盘/路径错误 |

观测对比是**独立的第 5 阶段**（`hmfms validate`），不在主循环内，也不读主配置。

## 4.2 快照语义

同一天内，所有过程模块读取**同一份状态快照**，模块的调用顺序不影响结果。
这与“先算收获、再算还田”的显式顺序在数学上等价，但消除了顺序依赖带来的隐式错误。
BCF 收获使用当日快照的土壤总量含量；同日多个器官不会逐个“边扣边算”。

## 4.3 文件职责

| 文件 | 作用 | 是否必需 |
|---|---|---|
| `model.yaml` | 时间、元素、数据接线、输出、闭合容差 | 必需 |
| `field.yaml` | 田块编号、面积、经纬度 | 必需 |
| `soil.csv` | 逐层土壤属性与初始含量 | 必需 |
| `deposition.csv` 或 NetCDF | 逐日干/湿沉降 | 可选 |
| `elements/<E>.yaml` | 吸收模式与按元素诊断 | 可选 |
| `defaults/*.yaml` | 参数库 | 可选（缺参数时才必需） |
| `management/*.csv` | 四张管理事件表 | 各自可选 |
| `validation.yaml` | 观测对比接线 | 仅 validate 需要 |

## 4.4 推荐目录布局

```text
my_field/
├─ model.yaml
├─ field.yaml
├─ soil.csv
├─ deposition.csv
├─ elements/
│  ├─ Cd.yaml
│  └─ Pb.yaml
├─ defaults/
│  └─ params.yaml
├─ management/
│  ├─ fertilizer_events.csv
│  ├─ irrigation_events.csv
│  ├─ harvest_events.csv
│  └─ straw_events.csv
├─ observations.csv
├─ validation.yaml
└─ output/              运行后生成（建议加入 .gitignore）
```

**路径解析规则**：所有相对路径都相对于**声明它的配置文件所在目录**，不受当前工作目录影响。
`model.yaml` 里的路径相对 `model.yaml`；`validation.yaml` 里的路径相对 `validation.yaml`。

## 4.5 最小文件清单

只跑“零输入、零输出”的最小模型，需要：

- `model.yaml`（`time`、`elements`、`field`、`inputs.soil`、`output`）
- `field.yaml`
- `soil.csv`（含 `initial_<元素>`）

其余块都可以省略；省略的过程不会产生通量，但输出列仍然存在。

---

# 5. 模型说明（用户视角）

本章只给用户需要知道的数学与假设；参数默认值与来源见 `docs/parameters.md`。
所有内部计算使用双精度浮点。

## 5.1 状态量与单位

对元素 `e`、土层 `l`：

```text
M[e,l] = C[e,l] × ρ_b[l] × d[l]
```

| 符号 | 含义 | 内部单位 |
|---|---|---|
| `M` | 单位面积储量 | mg·m⁻² |
| `C` | 土壤总含量 | mg·kg⁻¹ |
| `ρ_b` | 容重 | kg·m⁻³ |
| `d` | 层厚 | m |
| `F` | 通量 | mg·m⁻²·d⁻¹ |

例：容重 1300 kg·m⁻³、层厚 0.2 m → 土壤质量 260 kg·m⁻²；
含量 0.35 mg·kg⁻¹ 对应储量 91 mg·m⁻²。

## 5.2 时间积分

显式 Euler，步长固定 1 天：

```text
M(t+1) = M(t) + ΣF_in − ΣF_out
C(t+1) = M(t+1) / (ρ_b × d)
```

- `time.timestep` 只接受 `1D`；其他值直接报错。
- 同一天所有模块读取**同一份状态快照**；模块顺序不影响结果。
- 输入与输出通量都以正值记录，方向由过程所属集合决定，不靠符号。

## 5.3 质量闭合与非负保护

每个时间步：

```text
closure = M_end − M_start − ΣF_in + ΣF_out
```

- 相对残差阈值默认 `1e-8`，可由 `closure_tolerance` 覆盖。
- 通量为零时残差恒为 0；有通量后残差是浮点结合次序差异（~1e-15），判断标准是**相对**残差。
- 若 `M + ΣF_in − ΣF_out < 0`，抛出 `FluxImbalanceError`，报告日期、元素、土层、
  当日储量、各分项通量与当日事件；**不会把储量裁剪为 0**。

## 5.4 过程清单

| 过程 | 方向 | 触发 | 公式（简化） |
|---|---|---|---|
| 大气沉降 | 输入 | 每日 | `F = F_dry + F_wet` |
| 施肥 | 输入 | 事件日 | `Σ R × C_fert / 10000` |
| 灌溉 | 输入 | 事件日 | `Σ I × C_w`（1 mm = 1 L·m⁻²） |
| 秸秆还田 | 输入 | 事件日 | `Σ Y_straw × C_straw × k / 10000` |
| 作物收获 | 输出 | 收获日 | `Σ Y_organ × C_organ × f_removal / 10000` |
| 秸秆外运 | 输出 | 事件日 | `Σ Y_straw × C_straw × (1−k) / 10000` |
| 综合损失 | 输出 | 每日 | `k_loss × M`（经验项；v1 恒为 0，预留） |
| 淋溶 | 输出 | — | v1 恒为 0 |
| 径流 | 输出 | — | v1 恒为 0 |
| 侵蚀 | 输出 | — | v1 恒为 0 |
| 挥发 | 输出 | — | v1 恒为 0 |
| 风蚀/扬尘 | 输出 | — | v1 恒为 0 |

其中：

- `R`：施肥量 kg·ha⁻¹；`C_fert`：肥料含量 mg·kg⁻¹；`10000` 是 m²/ha。
- `I`：灌溉量 mm；`C_w`：灌溉水浓度 mg·L⁻¹。
- `Y`：产量/秸秆量 kg·ha⁻¹；`f_removal`：器官被带走的比例（0–1）；
  `k`：秸秆还田比例（0–1）。
- 沉降默认直接给通量；若给的是降水浓度，需在 IO 层先换算，不由降雨推导沉降。

## 5.5 作物吸收的两种模式

由 `elements/<E>.yaml` 的 `crop_uptake.mode` 选择，**同一元素只能取一种**。

### 5.5.1 `concentration`（默认）

```text
C_organ = 参数库中的 crop.<作物>.<器官>.<元素>
```

器官含量与土壤含量无关；可被收获事件行的 `<元素>_mg_kg_override` 覆盖。
这是示例场景使用的模式。

### 5.5.2 `bcf`

```text
C_organ = BCF<作物,器官,元素> × C_total(当日快照)
```

- BCF 无量纲，值来自参数库 `bcf.<作物>.<器官>.<元素>`，
  或收获事件行 `<元素>_bcf_override`。
- `basis` 只支持 `total`；`available`（有效态为分母）在 v1 明确报错。
- BCF 模块被标记为“依赖状态”，读取的是**当日起始**的土壤含量。
- **v1 边界**：BCF 只作用于收获表；秸秆表仍按固定浓度
  `crop.<作物>.<器官>.<元素>` 计算。两者同时出现时运行期给出提示。

> **关于 BCF 的取值**：BCF 跨田块、作物、老化状态可差 10–100 倍。
> 本项目**不随仓库提供 BCF 默认值**；缺失即报错。必须由用户参数库或事件覆盖给出，
> 并在报告中说明口径（田间老化土壤 / 盆栽 / 新近污染）。

## 5.6 两条内部一致性约束

- **秸秆守恒（INV-2）**：
  `F_straw_return + F_straw_removal = Y_straw × C_straw / 10000`。
  还田与外运从同一个总带出量拆分，逐日数值断言。
- **器官不重复处理（INV-3）**：同一 `(作物, 器官)` 不得同时出现在收获表与秸秆表。
  籽粒等“离开且不回来”的器官走收获；秸秆等“按比例还田”的器官走秸秆表。
  装载期交叉校验，违反即报错。

## 5.7 未实现过程与“累积上限”

淋溶、径流、侵蚀、风蚀、挥发、水分平衡以及综合损失在 v1 返回零通量，但输出列始终存在。
关闭这些过程意味着：

- 土壤中的金属只进不出（除收获与秸秆外运），储量变化是真实变化的**上限**；
- Cd 的主要输出是作物收获，关闭径流的影响相对有限；
- **Pb 的主要输出是地表径流**，关闭后会被显著高估。

运行日志会输出总提示，并按元素配置的 `diagnostics.runoff_disabled_severity`
给出“一般警告”或“强警告”。

## 5.8 适用尺度与不适用场景

适用：

- 单块田、年度到十年尺度的趋势与收支分析；
- 比较不同输入路径（沉降/施肥/灌溉/秸秆）对储量变化的贡献；
- 参数敏感性与情景分析；
- 与稀疏土壤观测做误差对比。

不适用：

- 事件尺度（单次降雨/单次灌溉）的瞬时浓度；
- 流域、区域或网格化模拟；
- 需要法规阈值判定或食品安全结论的场景；
- 多层土壤的垂向迁移（v1 科学上只支持单层，见 7.5）。

---

# 6. 配置参考

## 6.1 配置分层与路径规则

HMFMS 的配置分为四层，各管一件事：

| 层 | 文件 | 管什么 |
|---|---|---|
| 主配置 | `model.yaml` | 时间、元素、数据接线、输出、闭合容差 |
| 田块 | `field.yaml` | 编号、面积、经纬度 |
| 土壤 | `soil.csv` | 逐层属性与初始含量 |
| 元素 | `elements/<E>.yaml` | 吸收模式、按元素诊断（可选） |

另外还有参数库、四张管理事件表、可选的 NetCDF 块与 `validation.yaml`。

**路径规则**：相对路径一律相对于声明它的配置文件所在目录。把整个目录移动到别处，
只要内部相对结构不变，运行结果不受影响。

**未知字段**：所有 YAML 配置都禁止未知字段。拼错的键会立即报错，不会被静默忽略。

## 6.2 `model.yaml` 逐字段说明

### 6.2.1 顶层字段

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | 字符串 | 是 | — | 运行名称，仅元数据 |
| `version` | 字符串 | 是 | — | 配置版本，仅元数据 |
| `time` | 映射 | 是 | — | 见 6.2.2 |
| `elements` | 字符串列表 | 是 | — | 至少一个，不能重复；元素名由用户定义 |
| `element_configs` | 映射 | 否 | 无 | 元素 → `elements/<E>.yaml`；未给出的元素默认 `concentration` |
| `field` | 路径 | 是 | — | 田块配置 |
| `inputs` | 映射 | 是 | — | 至少含 `soil`；`deposition` 可选 |
| `parameters` | 映射 | 否 | 无 | `library: 路径` |
| `management` | 映射 | 否 | 无 | 四张事件表，均可单独省略 |
| `output` | 映射 | 是 | — | 见 6.2.6 |
| `closure_tolerance` | 浮点 | 否 | `1e-8` | 相对闭合残差阈值，必须 > 0 |

### 6.2.2 `time`

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `start` | 日期 | 是 | — | `YYYY-MM-DD`，含当日 |
| `end` | 日期 | 是 | — | `YYYY-MM-DD`，含当日；不得早于 `start` |
| `timestep` | 字符串 | 否 | `1D` | v1 只接受 `1D` |

天数 = `(end − start) + 1`。所有驱动与事件的日期都必须使用同一日历口径（无时区概念）。

### 6.2.3 `inputs.soil`

| 键 | 类型 | 必填 | 默认 | 允许值 |
|---|---|---|---|---|
| `file` | 路径 | 是 | — | 土壤剖面 CSV |
| `units.depth` | 字符串 | 否 | `cm` | `cm` / `m` |
| `units.bulk_density` | 字符串 | 否 | `kg/m3` | `kg/m3` / `g/cm3` |
| `units.concentration` | 字符串 | 否 | `mg/kg` | `mg/kg` / `ug/kg` / `g/kg` |

### 6.2.4 `inputs.deposition`（可选）

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file` | 路径 | 是 | — | CSV 或 NetCDF 源文件 |
| `format` | 字符串 | 否 | `csv` | `csv` / `netcdf` |
| `units` | 字符串 | 是 | — | 见附录 A 的 flux 白名单 |
| `date_column` | 字符串 | 否 | `date` | CSV 日期列名 |
| `dry_columns` | 映射 | 是 | — | 元素 → 干沉降列名/变量名 |
| `wet_columns` | 映射 | 是 | — | 元素 → 湿沉降列名/变量名 |
| `gap_policy` | 字符串 | 否 | `error` | `error` / `interpolate` |
| `magnitude_check` | 映射 | 否 | 无 | 元素 → `{min, max}`，单位 mg·m⁻²·d⁻¹ |
| `netcdf` | 映射 | 条件 | 无 | `format: netcdf` 时必填，见 6.6 |

`dry_columns` 与 `wet_columns` 的元素集合必须与顶层 `elements` 完全一致。
`magnitude_check` 只能包含已声明元素。

### 6.2.5 `parameters` 与 `management`

```yaml
parameters:
  library: defaults/params.yaml

management:
  fertilizer_events:
    file: management/fertilizer_events.csv
    units: {amount: kg/ha, concentration: mg/kg}
  irrigation_events:
    file: management/irrigation_events.csv
    units: {amount: mm, concentration: mg/L}
  harvest_events:
    file: management/harvest_events.csv
    units: {yield_rate: kg/ha, concentration: mg/kg}
  straw_events:
    file: management/straw_events.csv
    units: {amount: kg/ha, concentration: mg/kg}
```

四张表都可省略；省略的过程不会产生通量。`date_column` 默认都是 `date`。

### 6.2.6 `output`

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `directory` | 路径 | 是 | — | 输出目录，不存在会自动创建 |
| `interval` | 列表 | 否 | `[daily]` | `daily` / `monthly` / `annual` 的任意组合；重复项自动去重 |

输出频率只影响写哪几张表，不影响模拟结果。列集合固定（见第 9 章）。

## 6.3 `field.yaml`

```yaml
field_id: qiyang_paddy_demo
area_ha: 1.0
location:
  latitude: 26.58
  longitude: 111.85
notes: 可选说明
```

| 键 | 类型 | 必填 | 约束 | 用途 |
|---|---|---|---|---|
| `field_id` | 字符串 | 是 | 非空 | 元数据 |
| `area_ha` | 浮点 | 是 | > 0 | 元数据；所有通量按每平方米计算 |
| `location.latitude` | 浮点 | 是 | −90…90 | NetCDF 最近邻提取 |
| `location.longitude` | 浮点 | 是 | −180…180 | NetCDF 最近邻提取 |
| `notes` | 字符串 | 否 | — | 元数据 |

> `area_ha` 目前不参与任何通量计算。推理与报告请以 `mg·m⁻²` 与 `mg·kg⁻¹` 为准。

## 6.4 `soil.csv`

必需列：

```text
layer_id, top, bottom, bulk_density, ph, organic_matter, clay, cec, initial_<元素>...
```

示例（单层、Cd + Pb）：

```csv
layer_id,top,bottom,bulk_density,ph,organic_matter,clay,cec,initial_Cd,initial_Pb
1,0,20,1300,6.62,2.15,12.0,193,0.35,25.0
```

| 列 | 含义 | 单位（由配置声明） | 说明 |
|---|---|---|---|
| `layer_id` | 土层编号 | — | 整数，唯一 |
| `top` / `bottom` | 层顶 / 层底 | cm 或 m | `bottom > top`；层间不得重叠 |
| `bulk_density` | 容重 | kg·m⁻³ 或 g·cm⁻³ | 必须 > 0 |
| `ph` | pH | 无量纲 | 固定 BCF 不使用；预留给未来 `bcf_pH` |
| `organic_matter` | 有机质 | 无量纲（通常 %） | 同上 |
| `clay` | 黏粒 | 无量纲（通常 %） | 同上 |
| `cec` | 阳离子交换量 | 无量纲（cmol·kg⁻¹ / meq·kg⁻¹） | 同上 |
| `initial_<元素>` | 初始土壤总含量 | mg·kg⁻¹ | 每种元素一列，必需 |

**空单元格回落规则**：`ph`、`organic_matter`、`clay`、`cec`、`bulk_density` 为空时，
会按参数链回落到参数库的 `soil.<属性>`；`initial_<元素>` 为空时回落到
`soil.initial.<元素>`，两者都没有则报错。

> **v1 只支持单层。** `soil.csv` 允许写多层并通过结构校验，但过程通量会按层重复计算，
> 结果没有科学解释意义。请只提供一层（通常 `0–20 cm`）。多层垂向迁移留待后续版本。

## 6.5 `elements/<E>.yaml`

元素配置是**可选**的。未提供时，该元素使用 `concentration` 模式与默认诊断。

```yaml
element: Cd
crop_uptake:
  mode: concentration      # concentration | bcf
  basis: total             # v1 只支持 total
diagnostics:
  runoff_disabled_severity: normal   # normal | strong
  runoff_disabled_note: "径流未启用；Cd 的主要输出是作物收获，累积可能高估约 1/3。"
```

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `element` | 字符串 | 是 | — | 必须与 `element_configs` 的键一致 |
| `crop_uptake.mode` | 字符串 | 否 | `concentration` | `concentration` / `bcf` |
| `crop_uptake.basis` | 字符串 | 否 | `total` | 只接受 `total`；`available` 报错 |
| `diagnostics.runoff_disabled_severity` | 字符串 | 否 | `normal` | `normal` / `strong` |
| `diagnostics.runoff_disabled_note` | 字符串 | 否 | 空 | 日志中的补充说明 |

规则：

- 同一元素只能有一种 `mode`。同时写两种（例如把 `mode` 写成列表）会在装载期报错。
- `basis: available` 会报错并指向未实现说明。
- BCF 模式需要参数库中的 `bcf.<作物>.<器官>.<元素>`，或收获事件行 `<元素>_bcf_override`。
- 在 BCF 模式下使用 `<元素>_mg_kg_override` 会报错；反之亦然。
- 元素配置只影响收获表；秸秆表始终使用 `crop.<作物>.<器官>.<元素>` 固定浓度。

## 6.6 沉降：CSV 与 NetCDF

### 6.6.1 CSV（一等公民）

```csv
date,dry_Cd,wet_Cd,dry_Pb,wet_Pb
2020-01-01,0.00070,0.00050,0.030,0.020
2020-01-02,0.00082,0.00041,0.032,0.018
```

```yaml
inputs:
  deposition:
    file: deposition.csv
    format: csv
    units: mg/m2/day
    date_column: date
    dry_columns: {Cd: dry_Cd, Pb: dry_Pb}
    wet_columns: {Cd: wet_Cd, Pb: wet_Pb}
    gap_policy: error
    magnitude_check:
      Cd: {min: 0.0, max: 0.1}
      Pb: {min: 0.0, max: 1.0}
```

行为：

- `units` 必须在附录 A 的 flux 白名单内；读入时换算到 `mg·m⁻²·d⁻¹`。
- 日期必须唯一；重复日期报错。文件可以包含模拟窗口之外的日期，窗口内按日取值。
- `gap_policy: error`：窗口内缺任何一天即报错；
  `interpolate`：只对**内部缺口**线性插值，首尾缺口仍报错，不外推。
- 空单元格报错，不静默补零；负值报错。
- `magnitude_check` 只用于警告：日总沉降超出区间时提示可能的单位错误或区域不匹配。
  它**不能替代单位声明**。

### 6.6.2 NetCDF（适配器）

```yaml
inputs:
  deposition:
    file: grid.nc
    format: netcdf
    units: mg/m2/day
    dry_columns: {Cd: dry_dep_Cd, Pb: dry_dep_Pb}
    wet_columns: {Cd: wet_dep_Cd, Pb: wet_dep_Pb}
    netcdf:
      cache: extracted.csv
      time_dim: time
      lat_dim: latitude
      lon_dim: longitude
      time_variable: time
      latitude_variable: latitude
      longitude_variable: longitude
      time_units: "days since 2015-01-01"   # 可选；给出时覆盖变量属性
```

| 键 | 必填 | 说明 |
|---|---|---|
| `cache` | 是 | 提取后的单点 CSV 路径；不得与源文件相同 |
| `time_dim` / `lat_dim` / `lon_dim` | 是 | 三个维度的**实际名字** |
| `time_variable` / `latitude_variable` / `longitude_variable` | 是 | 三个坐标/时间变量的实际名字 |
| `time_units` | 否 | 时间变量无 `units` 属性时给出；给出时覆盖文件属性 |

适配器规则：

- 按 `field.yaml` 的 `location` 选**最近邻**格点，不插值。
- 经度兼容 0–360 与 -180–180 两种制式。
- 坐标必须是一维；通量变量必须恰好包含三个配置维度（顺序不限）。
- 时间轴必须是日尺度；同一日期出现多步直接报错。
- 掩码值（fill value）、负值、非有限值直接报错。
- 坐标超出网格范围时报错，并给出请求坐标与实际 `lat`/`lon` 范围。
- 缓存写**源单位**、`repr` 精度，列名与直接 CSV 完全一致；可以把 `format` 改成
  `csv`、`file` 指向缓存复用。
- 主循环始终只读缓存 CSV；降雨不参与沉降计算。

## 6.7 四张管理事件表

四张表都是**事件表**，不是日序列：只写真正发生的那几天。没有事件的日子不需要行。
事件日期落在模拟区间外时，记录警告并忽略（不静默丢弃）。

### 6.7.1 施肥 `fertilizer_events.csv`

必需列：`date`、`fertilizer_id`、`amount`；可选：`event_id`、`<元素>_mg_kg_override`、`note`。

```csv
event_id,date,fertilizer_id,amount,Cd_mg_kg_override,Pb_mg_kg_override,note
F001,2020-03-12,compound_npk,150,,2.5,Cd 取参数库；Pb 取事件覆盖
```

- `amount`：施用量，默认 `kg/ha`；也可配置为 `t/ha`。
- 含量优先取事件覆盖列；否则查库 `fert.<fertilizer_id>.<元素>`。
- 同一天、同一 `fertilizer_id` 可以有多行，会**累加**并给出提示（可能是重复录入）。
- `note` 仅供人阅读，模型忽略。

### 6.7.2 灌溉 `irrigation_events.csv`

必需列：`date`、`amount`、`water_source`；可选：`event_id`、`<元素>_mg_L_override`。

```csv
event_id,date,amount,water_source,Cd_mg_L_override,Pb_mg_L_override
I001,2020-07-01,50,canal,,0.002
I002,2020-07-01,30,canal,,0.002
```

- `amount`：灌溉水深，默认 `mm`；`1 mm = 1 L·m⁻²`。
- 同一天多条记录会**累加**（一天灌两次是常态）。
- 浓度优先取覆盖列；否则查库 `irr.<water_source>.<元素>`。

### 6.7.3 收获 `harvest_events.csv`

必需列：`date`、`crop_id`、`organ`、`yield`、`removal_fraction`；
可选：`event_id`、`<元素>_mg_kg_override`（浓度模式）、`<元素>_bcf_override`（BCF 模式）。

```csv
event_id,date,crop_id,organ,yield,removal_fraction,Cd_mg_kg_override,Pb_mg_kg_override
H001,2020-10-05,rice,grain,6500,1.0,,0.05
```

- **一行一个器官**。一次收获的籽粒与秸秆是两行，共用相同的 `(date, crop_id)`。
- 判重键是 `(date, crop_id, organ)`；同一天同一作物同一器官出现两行 → `DuplicateEventError`。
- `removal_fraction` 必须在 `[0, 1]`；籽粒通常为 1.0。
- 含量来源：
  - 浓度模式：事件 `<元素>_mg_kg_override` → 库 `crop.<crop_id>.<organ>.<元素>`；
  - BCF 模式：事件 `<元素>_bcf_override` → 库 `bcf.<crop_id>.<organ>.<元素>`；
  - 缺失即报错。
- 同一 `(crop_id, organ)` 不得同时出现在收获表与秸秆表（见 6.7.4）。

### 6.7.4 秸秆 `straw_events.csv`

必需列：`date`、`crop_id`、`organ`、`amount`、`return_fraction`；
可选：`event_id`、`<元素>_mg_kg_override`。

```csv
event_id,date,crop_id,organ,amount,return_fraction,Cd_mg_kg_override,Pb_mg_kg_override
S001,2020-10-20,rice,straw,6700,0.8,,0.6
```

- 一个事件同时产生两项通量：
  - 还田 = `amount × C × return_fraction / 10000`（输入）；
  - 外运 = `amount × C × (1 − return_fraction) / 10000`（输出）。
- `return_fraction` ∈ `[0, 1]`；`0` 表示全外运，`1` 表示全还田。
- 判重键同样是 `(date, crop_id, organ)`。
- 秸秆含量始终取库 `crop.<crop_id>.<organ>.<元素>` 或事件覆盖；
  **不支持 BCF 模式**。若该元素在收获表用了 BCF，运行期会提示这一边界。
- 与收获表的交叉校验：同一 `(crop_id, organ)` 不能两边都写，否则同一份质量会被扣两次。

## 6.8 参数库 `defaults/*.yaml`

### 6.8.1 条目结构

```yaml
parameters:
  - key: fert.compound_npk.Cd
    value: 1.31
    unit: mg/kg
    confidence: medium
    applicability: 湖南祁阳，NPK 复合肥
    source: "J. Hazard. Mater. 2025, 499:140030, Table S2"

  - key: irr.canal.Cd
    value: 0.00013
    unit: mg/L
    confidence: low
    applicability: 浙江稻区地表水（0.13 μg/L）
    source: "Fu et al. 2021, J. Hazard. Mater. 412:125203 引述 Shi 2019"
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | 是 | 参数键；同一库内不得重复 |
| `value` | 是 | 数值 |
| `unit` | 是 | 单位；必须与参数的量纲匹配 |
| `source` | 是 | 来源；**空字符串视为无效条目**，会被忽略并告警 |
| `confidence` | 否 | `low` / `medium` / `high`，默认 `low` |
| `applicability` | 否 | 适用条件（区域、作物、口径） |

### 6.8.2 参数键的命名空间

| 前缀 | 用途 | 示例 |
|---|---|---|
| `soil.*` | 土壤属性与初始含量 | `soil.ph`、`soil.initial.Cd` |
| `fert.<品类>.<元素>` | 肥料重金属含量 | `fert.pig_manure.Cd` |
| `irr.<水源>.<元素>` | 灌溉水浓度 | `irr.canal.Cd` |
| `crop.<作物>.<器官>.<元素>` | 器官固定浓度 | `crop.rice.grain.Cd` |
| `bcf.<作物>.<器官>.<元素>` | 富集系数（无量纲） | `bcf.rice.grain.Cd` |

### 6.8.3 五级解析链

| tier | 名称 | 来源 | 说明 |
|---|---|---|---|
| 1 | `event-override` | 事件行覆盖列 | 最高优先级 |
| 2 | `local-measured` | `soil.csv` / 田块实测 | 空单元格才继续向下 |
| 3 | `library-selected` | 用户点名使用的库条目 | 事件表点名的品类/水源/作物 |
| 4 | `literature-default` | 参数库默认条目 | `source` 必须非空 |
| 0 | `PLACEHOLDER` | 占位模式 | 仅 `--allow-placeholder`；值 0 |

- 同一参数只解析一次，结果写入 `resolved_parameters.csv`。
- 库文件可以不存在；但若某个参数真的需要回落到库，而库中无该键，则报错。
  若在 `model.yaml` 中显式配置了 `parameters.library` 而文件不存在，装载期直接报错。
- 单位换算只使用内置白名单；库中单位与目标量纲不匹配会报错。
- **BCF 没有随仓库默认值。** 若要用 BCF，必须自己在库中写 `bcf.*` 条目，或在事件行覆盖。

## 6.9 `validation.yaml`（仅观测对比）

该文件**只被 `hmfms validate` 读取**，主模型完全不认识它。

```yaml
element: Cd
layer_id: 1

simulation:
  file: output/daily.csv
  date_column: date

observations:
  file: observations.csv
  date_column: date
  value_column: cd_mg_kg
  units: mg/kg

match:
  rule: nearest          # exact | nearest | linear
  tolerance_days: 30

metrics:
  trend_min_points: 3

output:
  directory: validation
  # report_name: report.md
  # figure_name: comparison.png
  # points_name: matched_points.csv
```

| 键 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `element` | 是 | — | 模拟列由元素与土层推导 |
| `layer_id` | 是 | — | ≥ 1 |
| `simulation.file` | 是 | — | 已生成的 `daily.csv` |
| `simulation.date_column` | 否 | `date` | 模拟日期列 |
| `observations.file` | 是 | — | 观测 CSV |
| `observations.date_column` | 是 | — | 观测日期列 |
| `observations.value_column` | 是 | — | 观测数值列 |
| `observations.units` | 否 | `mg/kg` | concentration 白名单 |
| `match.rule` | 否 | `exact` | `exact` / `nearest` / `linear` |
| `match.tolerance_days` | 条件 | 0 | 非 `exact` 时必须 > 0 |
| `metrics.trend_min_points` | 否 | 3 | 至少 3 个匹配点才估趋势 |
| `output.directory` | 是 | — | 输出目录 |
| `output.report_name` | 否 | `report.md` | 必须以 `.md` 结尾 |
| `output.figure_name` | 否 | `comparison.png` | 必须以 `.png` 结尾 |
| `output.points_name` | 否 | `matched_points.csv` | 必须以 `.csv` 结尾 |

补充规则：

- 模拟数值列固定为 `soil_concentration_<element>_L<layer_id>_mg_kg`；
  若输出是占位模式，列名会带 `_PLACEHOLDER`，validate 会因找不到列而报错。
- 观测日期必须唯一；同日多条请先合并。
- 三个输出文件名不能相同；输出路径不得覆盖模拟或观测输入文件。
- 所有路径相对 `validation.yaml` 所在目录。

## 6.10 配置校验与常见错误

| 检查 | 触发条件 | 结果 |
|---|---|---|
| 未知字段 | YAML 中出现 schema 未定义的键 | pydantic 报错 |
| 时间步长 | `timestep` ≠ `1D` | 报错 |
| 日期顺序 | `end < start` | 报错 |
| 元素重复/空 | `elements` 有重复或空串 | 报错 |
| 元素配置错挂 | `element_configs` 含未声明元素 | 报错 |
| 沉降元素不一致 | `dry_columns`/`wet_columns` 与 `elements` 不同 | 报错 |
| 沉降格式块 | `format: netcdf` 无 `netcdf`，或 CSV 带 `netcdf` | 报错 |
| 输出频率 | 不在 `daily/monthly/annual` 内或为空 | 报错 |
| 土壤层 | `bottom ≤ top`、层重叠、`layer_id` 重复 | 报错 |
| 事件必需列 | 缺 `date`/`id`/`amount`/`yield` 等 | 报错并列实际列 |
| 事件日期 | 落在模拟窗口外 | 警告并忽略 |
| 重复收获/秸秆 | 同日同作物同器官两行 | `DuplicateEventError` |
| 器官两处处理 | 同 `(作物,器官)` 同时在收获与秸秆表 | 报错 |
| 参数库重复键 | 同一库中 `key` 重复 | 报错 |
| 参数库空来源 | `source` 为空 | 该条目被忽略并告警 |
| 参数缺失 | 所有层级都没有值 | `ParameterSourceError` |
| 负储量 | 当日移除量大于储量 | `FluxImbalanceError` |
| 闭合超限 | 相对残差 > `closure_tolerance` | 报错 |

## 6.11 完整示例

仓库提供两套可直接运行的配置：

| 示例 | 用途 | 特点 |
|---|---|---|
| `examples/minimal` | 最小纵切 | 10 天，Cd + Pb，含沉降/施肥/灌溉/收获/秸秆 |
| `examples/paddy-10y` | 十年演示 | 2015–2024，日/月/年输出，含元素配置与观测对比 |

最小 `model.yaml` 骨架：

```yaml
name: my-field
version: "0.1.0"

time:
  start: "2020-01-01"
  end: "2020-12-31"
  timestep: "1D"

elements: [Cd, Pb]
field: field.yaml

inputs:
  soil:
    file: soil.csv
    units: {depth: cm, bulk_density: kg/m3, concentration: mg/kg}

output:
  directory: output
  interval: [daily, monthly, annual]

closure_tolerance: 1.0e-8
```

把 `examples/minimal` 复制到自己的工作目录，逐步替换 `soil.csv`、`deposition.csv`、
`management/*.csv`、`defaults/*.yaml`，是最稳妥的起步方式。

---

# 7. 输入数据准备

## 7.1 时间轴

- 所有日期使用 `YYYY-MM-DD`，不含时间与时区。
- `start` 与 `end` 都**包含**在模拟区间内；闰年按日历天数处理。
- 沉降必须覆盖窗口内每一天（或显式允许插值）；事件只写发生日。
- 事件日期在窗口外：警告并忽略，不会静默改变结果。
- 观测对比的观测日期不受模拟窗口限制，但必须能匹配到模拟日期（见第 10 章）。

## 7.2 单位与量级

模型内部单位固定；所有输入单位必须声明且在白名单内。

| 量 | 常用输入单位 | 内部单位 | 换算示例 |
|---|---|---|---|
| 沉降通量 | `mg/m2/day`、`g/ha/day`、`g/ha/yr`、`kg/ha/yr` | mg·m⁻²·d⁻¹ | `1 g/ha/day = 0.1 mg/m²/day` |
| 土壤/物料含量 | `mg/kg`、`ug/kg`、`g/kg` | mg·kg⁻¹ | `1000 ug/kg = 1 mg/kg` |
| 水浓度 | `mg/L`、`ug/L`、`μg/L`、`ng/L` | mg·L⁻¹ | `1 ug/L = 0.001 mg/L` |
| 灌溉水深 | `mm`、`cm`、`m` | mm | `1 cm = 10 mm` |
| 施用量/产量 | `kg/ha`、`t/ha` | kg·ha⁻¹ | `1 t/ha = 1000 kg/ha` |
| 容重 | `kg/m3`、`g/cm3` | kg·m⁻³ | `1.3 g/cm3 = 1300 kg/m3` |
| 层厚 | `cm`、`m` | m | `20 cm = 0.2 m` |

几个必须记住的等式：

```text
kg/ha × mg/kg ÷ 10000 = mg/m²        （施肥、产量、秸秆）
mm × mg/L              = mg/m²        （灌溉；1 mm = 1 L/m²）
mm × μg/L ÷ 1000       = mg/m²        （降水浓度换算）
g/ha × 0.1             = mg/m²
```

> 量级检查（`magnitude_check`）只能抓 100 倍级错误，抓不到 10 倍级错误。
> 真正的第一道防线是**单位声明**与**量级常识**。

## 7.3 沉降 CSV 的准备

- 一行一天；日期唯一。
- 每个元素两列：干、湿；列名必须与 `dry_columns`/`wet_columns` 完全一致。
- 文件可以包含窗口外的日期；窗口内必须覆盖（`gap_policy: error`）或允许内部插值。
- 空白单元格、负值、非数字都会报错；不静默补零。
- 建议同时配置 `magnitude_check`，用文献区间做第二道网。
- 如果只有“降水中浓度”，先在 IO 层用 `P × C / 1000` 换算成通量，再写入 CSV；
  不要把它当作模型内部的湿沉降参数化。

## 7.4 NetCDF 的准备清单

在把 NetCDF 交给模型之前，逐项确认：

- [ ] 经度、纬度是**一维坐标变量**，维度名与配置一致；
- [ ] 干/湿通量变量存在，且维度恰好是配置的三个维度（顺序不限）；
- [ ] 时间变量是一维，`units` 属性存在（或配置 `time_units`）；
- [ ] 时间轴是日尺度，同一日期不重复；
- [ ] 坐标范围覆盖田块经纬度（注意 0–360 与 -180–180 的差异）；
- [ ] 没有掩码/填充值，或已补全；
- [ ] 通量单位与配置 `units` 一致；
- [ ] `cache` 指向一个独立的 CSV，不与源文件同名。

## 7.5 土壤剖面的准备

- **只写一层**（v1 科学上只支持单层）。通常 `top=0`、`bottom=20`（cm）。
- 容重使用**干容重**，单位与配置一致；不要用湿容重或比重。
- pH 注明测定方法；水浸提与 CaCl₂ 浸提不可互换。
- 有机质、黏粒按百分数填写（如 2.15、12.0）；CEC 使用 cmol·kg⁻¹ / meq·kg⁻¹。
- `initial_<元素>` 是**土壤总量**，不是有效态；不要混用 BCR/EDTA 提取值。
- 若某项没有实测，可以留空并依赖参数库；但初始含量没有实测时必须显式给出。
- 这些属性目前只被存储；固定 BCF 不使用 pH/黏粒/有机质/CEC，它们预留给未来的 `bcf_pH`。

## 7.6 管理事件的准备

- `fertilizer_id`、`water_source`、`crop_id`、`organ` 都是**数据**：必须在参数库或事件覆盖中能解析。
- 作物与器官命名要一致：同一作物在所有表中使用同一个字符串。
- 收获表一行一个器官；不要把籽粒与秸秆写在同一行。
- 秸秆若在秸秆表中处理，就不要再写进收获表（反之亦然）。
- `removal_fraction`、`return_fraction` 用 0–1 的小数，不要写百分数（80% 写成 0.8）。
- 同一天多次灌溉可以多行；同一天同一品类的施肥也会累加，但请确认不是重复录入。
- 事件量按“整季”填写时，模拟窗口也必须覆盖整个事件日；否则会被忽略。

## 7.7 参数值的准备

每个库条目至少回答四个问题：

1. **键**：属于哪个元素、作物、器官、品类或水源？
2. **值**：数值是多少？
3. **单位**：与模型内部单位如何换算？
4. **适用条件**：哪个区域、哪种土壤、鲜重还是干重、盆栽还是田间？

`source` 为空会被视为无效条目；`applicability` 不是装饰，它决定了这个值能否用于你的田块。

> **BCF 特别提醒**：跨场景可差 10–100 倍。不要因为“别人用了 0.1”就直接套用；
> 用本地“器官浓度 / 土壤浓度”反算，或至少做区间敏感性分析。
> 本项目不提供 BCF 默认值。

## 7.8 跑十年之前的数据质控清单

- [ ] 时间轴覆盖完整，起始与结束日期正确；
- [ ] 所有单位已声明，且与文件内容一致；
- [ ] 沉降元素集合与 `elements` 完全一致；
- [ ] 土壤初始含量是**总量**，不是有效态；
- [ ] 四张事件表的日期、作物、器官、品类、水源都能解析；
- [ ] 收获/秸秆没有同一器官重复处理；
- [ ] 参数库每条都有 `source` 与适用条件；
- [ ] 先用 `examples/minimal` 跑通，再替换成自己的数据；
- [ ] 用 `--allow-placeholder` 只做结构演示，不把结果当正式结果。

## 7.9 合成数据与实测数据的边界

- `examples/minimal` 与 `examples/paddy-10y` 都是**合成/演示场景**，不是实测观测。
- 十年演示的 Pb 器官浓度与灌溉水 Pb 是明确标注的假设（见该目录 README）。
- 若要把演示结果写进论文，必须替换为本地实测参数，并重新做量级与闭合检查。
- 观测对比中的 `observations.csv` 才是“观测”；不要把模拟输出反过来当观测。

---

# 8. 运行模型

## 8.1 命令总览

```bash
python -m hmfms --help
python -m hmfms run <model.yaml> [--allow-placeholder]
python -m hmfms validate <validation.yaml>
python -m hmfms demo [--no-validate]
```

| 命令 | 作用 | 是否运行模型 |
|---|---|---|
| `run` | 按主配置运行并写输出 | 是 |
| `validate` | 用观测表对比已有模拟输出 | 否 |
| `demo` | 运行十年演示并做观测对比 | 是 + 对比 |

## 8.2 正常运行

```bash
python -m hmfms run path/to/model.yaml
```

终端会打印：

- 每张频率表的路径；
- `resolved_parameters.csv` 路径；
- 配置指纹；
- 天数；
- 每个元素的初始/期末含量、年增量与闭合残差。

输出目录不存在会自动创建；同名文件会被**覆盖**。建议每次运行使用独立目录，
或把 `output/` 加入版本控制的忽略列表。

## 8.3 占位模式（`--allow-placeholder`）

只在“检查配置结构与输出列”时使用：

```bash
python -m hmfms run model.yaml --allow-placeholder
```

行为：

- 缺失参数取 0，不再报错；
- 参数溯源表对应行 `tier = 0`、`tier_label = PLACEHOLDER`；
- 所有数值列加 `_PLACEHOLDER` 后缀（`date` 除外）；
- 日志逐条列出占位参数。

占位模式解决的是“参数缺失”，不是“输入结构无效”。容重为 0、层厚为 0、
储量被扣穿等情况仍会报错。**占位结果不能用于任何结论。**

## 8.4 输出频率

```yaml
output:
  directory: output
  interval: [daily, monthly, annual]
```

- `daily`：每天一行，标签 `YYYY-MM-DD`；
- `monthly`：每月一行，标签 `YYYY-MM`；
- `annual`：每年一行，标签 `YYYY`；
- 只写被选中的频率表；列集合三张表完全相同。

聚合口径见第 9 章。

## 8.5 日志与警告

运行日志分三类：进度、警告、错误。**警告不会中止运行**，但必须读完。

| 日志片段 | 含义 | 建议 |
|---|---|---|
| `在未启用淋溶、径流、侵蚀的条件下…上限` | 水文过程全关 | 结果只能当上限 |
| `一般警告：元素 X 关闭径流` | 该元素对径流不敏感 | 记录到报告 |
| `强警告：元素 X 关闭径流` | 主要输出途径缺失 | 必须显式声明高估风险 |
| `元素 X 使用 bcf 模式，但秸秆表仍按固定浓度处理` | BCF 只覆盖收获表 | 确认秸秆参数口径 |
| `占位模式：N 个参数使用占位值 0` | 缺参数被占位 | 不可用于结论 |
| `日沉降通量超出配置区间` | 可能单位/区域不匹配 | 核对 `units` 与文献区间 |
| `日期 … 落在模拟区间之外，已忽略` | 事件日期越界 | 修正日期或扩大窗口 |
| `同一天同一品类的施肥事件…已累加` | 可能重复录入 | 确认是否两条真实事件 |
| `NetCDF 沉降已提取…` | 适配器完成 | 如需可见，配置日志级别 INFO |

## 8.6 三类主要异常

### 8.6.1 `ParameterSourceError`

表示某个参数在所有层级都找不到值：

```text
ParameterSourceError: fert.pig_manure.Cd 缺失
  来源需求: fertilizer_events.csv:12 品类 'pig_manure' 的 Cd 含量
  依据:     docs/parameters.md §1–2（肥料重金属含量）
  已尝试:   事件级覆盖：未提供 / 田块实测：未提供 / ...
  处置:
    - 在事件行填入 Cd_mg_kg_override 覆盖值
    - 在参数库中新增键 'fert.pig_manure.Cd' 并写明来源
```

处置顺序：先看事件行能否覆盖；再补参数库；不要用占位模式“跑通”正式结果。

### 8.6.2 `DuplicateEventError`

```text
DuplicateEventError: 同一天出现 2 条 harvest 事件
  文件: harvest_events.csv
  日期: 2021-10-05（crop_id=rice, organ=grain）
  行号: 7, 23
  处置: 单季只能收获一次；若确为两季，请拆成两个作物季（crop_id 不同）
```

收获与秸秆的判重键是 `(日期, 作物季, 器官)`。同一天同作物不同器官是正常的。

### 8.6.3 `FluxImbalanceError`

```text
FluxImbalanceError: 2021-10-05 元素 Cd 土层 1
  当日储量: ...
  输入合计: ...
    + deposition: ...
  输出合计: ...
    - harvest: ...
  更新后储量: -...
  当日事件:
    - harvest: H2021
  处置: 检查各分项通量的参数与单位；储量不得裁剪为 0
```

常见原因：产量单位写成 `t/ha` 却按 `kg/ha` 理解、初始含量过低、器官含量过高、
或同一天把籽粒与秸秆重复计入。

## 8.7 配置指纹与可复现性

每张表首行与 `resolved_parameters.csv` 都带 16 位指纹：

```text
# schema_version=4 fingerprint=a6122a95154c1a48 frequency=daily
```

指纹按**有效输入**计算：

- 模型配置（去掉沉降取数接线：文件、格式、列名、维度名）；
- 加载后的沉降内部序列；
- 其余输入文件内容（文本按 LF 归一）。

因此：

- 同一配置连跑两次 → 指纹相同、输出逐字节一致；
- 换机器/换目录/换行风格 → 指纹不变；
- 改任何一个影响结果的数值 → 指纹改变；
- 只改沉降来源格式（CSV ↔ NetCDF）但数值相同 → 指纹不变；
- 改输出目录或频率（它们写在 `model.yaml`）→ 指纹改变。

复现实验时，请把指纹写进报告，而不是只写“用了模型”。

## 8.8 性能与批量运行

- 单田块十年（3653 天）在普通笔记本上为秒级到十秒级；
- 纯 Python、单线程，不支持 MPI；
- 多田块/多情景：为每个田块准备独立目录与配置，用 shell/Python 循环调用 `hmfms run`；
- 不建议把多个田块塞进一个 `soil.csv` 多层结构（v1 不支持）。

示例（PowerShell）：

```powershell
Get-ChildItem cases\*\model.yaml | ForEach-Object {
    python -m hmfms run $_.FullName
}
```

---

# 9. 输出参考

## 9.1 文件清单

| 文件 | 何时生成 | 内容 |
|---|---|---|
| `daily.csv` | `interval` 含 `daily` | 逐日状态与通量 |
| `monthly.csv` | `interval` 含 `monthly` | 逐月聚合 |
| `annual.csv` | `interval` 含 `annual` | 逐年聚合 |
| `resolved_parameters.csv` | 每次运行 | 参数溯源 |
| `report.md`、`comparison.png`、`matched_points.csv` | 仅 `validate` | 观测对比 |

## 9.2 首行元信息

频率表：

```text
# schema_version=4 fingerprint=<16位十六进制> frequency=daily
```

参数溯源表：

```text
# schema_version=4 fingerprint=<16位十六进制>
```

- `schema_version`：输出契约版本；当前为 4。
- `fingerprint`：本次运行有效输入的指纹。
- `frequency`：该表的频率。

读取 CSV 时请跳过以 `#` 开头的行；不要把首行当表头。

## 9.3 列契约

三张频率表的列集合**完全相同**，顺序固定：

```text
date

# 每个元素，按 elements 顺序：
soil_concentration_<元素>_L<层>_mg_kg     （每个土层一列）
soil_mass_<元素>_L<层>_mg_m2              （每个土层一列）

# 每个元素：
deposition_dry_<元素>_mg_m2_d
deposition_wet_<元素>_mg_m2_d

# 每个元素，按 FLUX_PROCESSES 顺序：
deposition_<元素>_mg_m2_d
fertilization_<元素>_mg_m2_d
irrigation_<元素>_mg_m2_d
straw_return_<元素>_mg_m2_d
harvest_<元素>_mg_m2_d
straw_removal_<元素>_mg_m2_d
lumped_loss_<元素>_mg_m2_d
leaching_<元素>_mg_m2_d
runoff_<元素>_mg_m2_d
erosion_<元素>_mg_m2_d
volatilization_<元素>_mg_m2_d
dust_emission_<元素>_mg_m2_d
in_total_<元素>_mg_m2_d
out_total_<元素>_mg_m2_d
net_flux_<元素>_mg_m2_d
cumulative_input_<元素>_mg_m2
cumulative_output_<元素>_mg_m2
closure_error_<元素>_mg_m2
```

列数（单层）：`1 + 元素数 × 22`。Cd + Pb 单层 = 45 列。

| 列组 | 含义 | 单位 |
|---|---|---|
| `soil_concentration_*` | 土壤总含量 | mg·kg⁻¹ |
| `soil_mass_*` | 单位面积储量 | mg·m⁻² |
| `deposition_dry/wet_*` | 干/湿沉降分项 | mg·m⁻²·d⁻¹ |
| `<过程>_*` | 该过程通量（跨土层求和） | mg·m⁻²·d⁻¹ |
| `in_total` / `out_total` / `net_flux` | 输入/输出/净通量 | mg·m⁻²·d⁻¹ |
| `cumulative_input/output` | 期初至当日的累积 | mg·m⁻² |
| `closure_error` | 质量闭合残差 | mg·m⁻² |

**不要按列位置读取**；请按列名。未实现过程的列始终存在且为 0。

## 9.4 日/月/年三张表的口径

| 列类型 | 日表 | 月表 | 年表 |
|---|---|---|---|
| 通量列（以 `_mg_m2_d` 结尾） | 当日值 | 期内**日均** | 期内**日均** |
| `soil_concentration` / `soil_mass` | 当日末 | 期末值 | 期末值 |
| `cumulative_input/output` | 当日末 | 期末值 | 期末值 |
| `closure_error` | 当日残差 | 期末残差 | 期末残差 |

- `date` 标签：日表 `YYYY-MM-DD`，月表 `YYYY-MM`，年表 `YYYY`。
- 期总质量 = 日均通量 × 期内天数；天数由标签唯一确定（月份长度、闰年）。
- 月表的 `net_flux` 定义为“月均输入 − 月均输出”，与日表 `net_flux` 的月均值一致。
- 月表/年表可以严格由日表重聚合；不需要额外列。

## 9.5 `resolved_parameters.csv`

列：

```text
key, value, unit, dimension, tier, tier_label, source, confidence, note
```

| 列 | 说明 |
|---|---|
| `key` | 参数键 |
| `value` | 内部单位下的数值；分类选择行留空 |
| `unit` | 内部单位（如 `mg/kg`、`mg/m2/day`、`1`） |
| `dimension` | 量纲（flux / concentration / dimensionless / choice…） |
| `tier` | 0–4，见 6.8.3 |
| `tier_label` | `PLACEHOLDER` / `event-override` / `local-measured` / `library-selected` / `literature-default` / `local-config` |
| `source` | 文件:行、文献来源或“占位” |
| `confidence` | `low` / `medium` / `high` |
| `note` | 补充说明；吸收模式行写 `mode; basis=...` |

吸收模式行示例：

```text
crop_uptake.mode.Cd,,1,choice,2,local-config,elements/Cd.yaml,high,bcf; basis=total
```

`value` 为空不是错误：模式是分类选择，不是数值。

## 9.6 占位模式下的列名

启用 `--allow-placeholder` 时：

- `date` 不变；
- 其余所有数值列追加 `_PLACEHOLDER`；
- 列数与顺序不变；
- 参数溯源表行 `tier=0`、`tier_label=PLACEHOLDER`。

下游脚本看到 `_PLACEHOLDER` 应拒绝把结果用于正式分析。

## 9.7 下游读取示例

```python
import pandas as pd

df = pd.read_csv("output/daily.csv", comment="#")
# 或显式跳过首行：pd.read_csv(..., skiprows=1)

cd = df[["date",
         "soil_concentration_Cd_L1_mg_kg",
         "harvest_Cd_mg_m2_d",
         "closure_error_Cd_mg_m2"]]
print(cd.tail())
```

聚合校验：

```python
monthly = (df.assign(month=df["date"].str[:7])
             .groupby("month")
             .agg({"harvest_Cd_mg_m2_d": "mean",
                   "soil_concentration_Cd_L1_mg_kg": "last"}))
```

期总移除 = `harvest_Cd_mg_m2_d` 的月均 × 当月天数。

## 9.8 版本与兼容性

| schema_version | 变化 |
|---|---|
| 1 | 初始输出契约 |
| 2 | 增加干/湿沉降分项列 |
| 3 | 参数溯源表增加 `note` 列 |
| 4 | 增加月/年表、指纹与频率元信息、占位后缀 |

兼容规则：

- 列只增不删、不改名；需要变更时提升 `schema_version`。
- 消费方应检查 `schema_version` 与列名，而不是假设列数固定。
- `_PLACEHOLDER` 后缀是条件性的；正式结果不应出现该后缀。

---

# 10. 观测对比

## 10.1 设计原则

观测对比是一个**独立命令**，只读已经生成的模拟 CSV 与用户观测表：

- 不重跑模型；
- 不读主配置；
- 主模型运行时不认识任何观测路径。

这样做的好处是：模拟结果是否被观测“影响”不再需要逐行审查；观测表格式变化也不会波及核心计算。

## 10.2 配置

`validation.yaml` 的完整字段见 6.9。最小示例：

```yaml
element: Cd
layer_id: 1
simulation:
  file: output/daily.csv
  date_column: date
observations:
  file: observations.csv
  date_column: date
  value_column: cd_mg_kg
  units: mg/kg
match:
  rule: nearest
  tolerance_days: 30
output:
  directory: validation
```

## 10.3 日期匹配规则

观测日期与模拟日期通常不对齐。模型不替用户猜，必须显式选择规则。

| 规则 | 行为 | 失败条件 |
|---|---|---|
| `exact` | 观测日期必须存在于模拟序列 | 缺失日期 → 报错 |
| `nearest` | 取最近的模拟日期 | 距离 > `tolerance_days` → 报错 |
| `linear` | 在相邻两个模拟日期之间线性插值 | 超出模拟范围，或区间跨度 > `tolerance_days` → 报错 |

细节：

- 如果观测日期与模拟日期完全相同，三种规则都记为 `exact`。
- `nearest` 遇到等距时取**较早**的模拟日期（确定性行为）。
- `linear` 的 `sim_date` 记录为 `左日期~右日期`；`nearest`/`exact` 记录具体日期。
- `linear` 的容差约束是**插值区间跨度**，不是观测点到端点的距离。
- 终点之外不外推；首尾缺口无法插值。
- 非 `exact` 规则必须给正的 `tolerance_days`；容差是科学选择，代码不提供默认值。

示例：

| 观测日期 | 模拟序列 | 规则 | 结果 |
|---|---|---|---|
| 2020-01-02 | 有 2020-01-02 | 任意 | method=exact，sim=当日值 |
| 2020-01-04 | 最后一天 2020-01-03 | nearest, 容差 2 | method=nearest，sim=01-03 值 |
| 2020-01-02 | 01-01 与 01-03 | linear, 容差 2 | method=linear，取两点中点值 |
| 2020-01-10 | 最后一天 01-03 | nearest, 容差 2 | 报错（距离 7 > 2） |

## 10.4 指标定义

设匹配点 `i = 1..N`，`sim_i`、`obs_i`，残差 `r_i = sim_i − obs_i`。

| 指标 | 定义 | 单位 | 说明 |
|---|---|---|---|
| `n_pairs` | 匹配点数量 | — | 必须 > 0 |
| MAE | `mean(|r_i|)` | mg/kg | 平均绝对误差 |
| RMSE | `sqrt(mean(r_i²))` | mg/kg | 均方根误差 |
| 平均相对误差 | `mean(|r_i| / |obs_i|)`，仅 `obs_i ≠ 0` | 小数 | 报告中同时给百分比与参与点数 |
| 期末误差 | 最后一个匹配点的 `r` | mg/kg | “最后”按观测日期排序 |
| 期末相对误差 | `r_last / obs_last`（`obs_last ≠ 0`） | 小数 | 观测为 0 时为 `—` |
| 年际趋势（模拟） | 模拟值对时间的 OLS 斜率 | mg/kg/a | 时间轴按 365.25 天/年 |
| 年际趋势（观测） | 观测值对时间的 OLS 斜率 | mg/kg/a | 同上 |
| 年际趋势误差 | 模拟斜率 − 观测斜率 | mg/kg/a | 不是期末差 |

趋势计算条件：

- 匹配点 ≥ `metrics.trend_min_points`（默认 3）；
- 匹配日期跨至少 **2 个日历年**。

不满足时：

- 趋势相关列为 `—`；
- 报告写入“观测点不足以估计年际趋势”的不确定性说明；
- 命令**正常结束**，不报错。

## 10.5 三件产物

### `matched_points.csv`

```text
obs_date,obs_value_mg_kg,sim_value_mg_kg,residual_mg_kg,method,sim_date
```

- `method`：`exact` / `nearest` / `linear`；
- `residual`：`sim − obs`；
- `sim_date`：实际使用的模拟日期或区间。

这是最值得逐行检查的文件：任何异常匹配都会在这里暴露。

### `report.md`

包含：

- 模拟输出的 schema 版本与配置指纹；
- 观测文件、元素/土层、匹配规则与容差；
- 匹配点数与各方式计数；
- 指标表；
- 不确定性说明；
- “主模型未读取观测数据”的声明。

### `comparison.png`

两栏：

- 左：时间序列（模拟折线 + 观测点）；
- 右：模拟 vs 观测的 1:1 散点，标题给出 MAE/RMSE。

## 10.6 不确定性说明

报告会明确指出：

- 趋势不足以估计时的原因（点少或未跨年）；
- 相对误差只统计观测非零点，并给出参与数量；
- 趋势是普通最小二乘斜率，未提供置信区间；
- 观测对比只做误差记账，是否可接受由使用者判断。

这些说明不是套话：观测点少、观测口径不一致、模型结果为上限，都会让“低误差”产生误导。

## 10.7 如何负责任地解读

1. **先看匹配明细**：`nearest`/`linear` 占比过高时，误差里混入了时间错位。
2. **区分偏差与离散**：MAE 小但期末误差大，可能是趋势方向错了；RMSE 远大于 MAE，说明有离群点。
3. **不要用趋势误差替代机制判断**：3–4 个点的斜率对单个点非常敏感。
4. **把模型上限说清楚**：关闭径流/淋溶时，模拟值系统性偏高是预期行为，不是“模型验证通过”。
5. **报告指纹与版本**：没有指纹的对比无法复现。

## 10.8 用十年演示做一次对比

```bash
python -m hmfms validate examples/paddy-10y/validation.yaml
```

演示的观测表有 4 个点（2015/2019/2022/2024），使用 `nearest`、容差 30 天；
预期 4 个点全部 `exact`，MAE 约 `0.0025`、RMSE 约 `0.0030 mg/kg`。
这只是演示数据，不代表模型在真实田块上的验证结果。

---

# 11. 十年演示场景

## 11.1 场景构成

`examples/paddy-10y` 是一个按文献区间合成的稻田场景，用于演示完整工作流与守恒收口。

| 项 | 值 | 来源/说明 |
|---|---|---|
| 时间 | 2015-01-01 至 2024-12-31 | 3653 天 |
| 土壤 | 0–20 cm，容重 1300 kg·m⁻³ | 祁阳稻田区间 |
| pH / 有机质 / 黏粒 / CEC | 6.62 / 2.15% / 12.0% / 193 | 同上 |
| 初始 Cd / Pb | 0.35 / 25.0 mg·kg⁻¹ | Cd 为文献均值；Pb 为演示假设 |
| 年沉降 Cd / Pb | 0.673 / 20.6 mg·m⁻²·a⁻¹ | 祁阳 Cd；全国 Pb 均值 |
| 干/湿比例 | 60% / 40% | 演示假设 |
| 施肥 | 复合肥 263、猪粪 1516 kg·ha⁻¹·a⁻¹ | 祁阳稻田 |
| 灌溉 | 5 × 220 mm·a⁻¹，水源 canal | 演示假设 |
| 水稻 | 籽粒 6700、秸秆 6700 kg·ha⁻¹·a⁻¹ | 祁阳稻田 |
| 秸秆还田率 | 0.8 | 祁阳稻田 |

参数库中的每条都有 `source` 与 `applicability`；Pb 的器官浓度与灌溉水 Pb 明确标注为
“演示假设”，不是文献默认值。

## 11.2 一键运行

```bash
python -m hmfms demo
```

或只跑模型：

```bash
python -m hmfms demo --no-validate
```

产物：

```text
examples/paddy-10y/output/
├─ daily.csv
├─ monthly.csv
├─ annual.csv
└─ resolved_parameters.csv
examples/paddy-10y/validation/
├─ report.md
├─ comparison.png
└─ matched_points.csv
```

## 11.3 预期结果与自检

| 检查 | 预期 |
|---|---|
| 日表行数 | 3653 |
| 月表行数 | 120 |
| 年表行数 | 10 |
| Cd 初始 → 期末 | 0.350 → 约 0.3979 mg·kg⁻¹ |
| Cd 年增量 | 约 **0.00478 mg·kg⁻¹·a⁻¹** |
| Pb 初始 → 期末 | 25.0 → 约 25.92 mg·kg⁻¹ |
| 闭合相对残差 | ~1e-13（远低于 1e-8） |
| 观测对比 | 4 点，MAE 约 0.0025、RMSE 约 0.0030 mg/kg |

量级自检的基准是项目采用的端点 `0.0027 mg·kg⁻¹·a⁻¹`：
本演示的 0.00478 在 3 倍以内，说明单位与系数没有差一个数量级。

## 11.4 演示中的假设（必须知道）

- Pb 籽粒 0.05 mg/kg、秸秆 0.60 mg/kg 没有区域实测，是演示假设；
- 灌溉水 Pb 浓度按 0 处理，也是演示假设；
- 关闭淋溶/径流/侵蚀，结果是**累积上限**；
- 观测表是为演示构造的 4 个点，不是独立验证数据。

因此演示结果**只能用于检查工作流**，不能作为该田块的预测值或论文结论。

## 11.5 把演示改成自己的田块

建议按以下顺序替换，每步都先跑一次短窗口：

1. 复制 `examples/paddy-10y` 到自己的工作目录；
2. 替换 `field.yaml`（经纬度、面积、编号）；
3. 替换 `soil.csv`（只保留一层，初始含量用实测总量）；
4. 替换沉降：CSV 或 NetCDF；确认元素集合与 `elements` 一致；
5. 替换四张事件表：作物、器官、品类、水源命名保持一致；
6. 替换参数库：逐条写 `source` 与 `applicability`；不要直接沿用演示值；
7. 决定吸收模式：默认 `concentration`；用 BCF 时先做区间敏感性；
8. 先用 1 年窗口跑通，再扩到十年；
9. 检查日志中的上限提示、按元素警告、量级警告；
10. 把配置指纹写入报告。

## 11.6 可复现性检查

同一场景连跑两次，模型输出应逐字节一致。PowerShell 示例：

```powershell
python -m hmfms demo --no-validate
Get-FileHash examples\paddy-10y\output\*.csv -Algorithm SHA256

python -m hmfms demo --no-validate
Get-FileHash examples\paddy-10y\output\*.csv -Algorithm SHA256
```

两次的哈希应完全相同。`resolved_parameters.csv` 也必须一致；
若不一致，先检查是否有未声明的随机性、文件被并发修改或输出了占位结果。

---

# 12. 排错与 FAQ

## 12.1 错误速查表

| 错误/现象 | 最可能原因 | 处置 |
|---|---|---|
| `ParameterSourceError` | 参数库无键、事件无覆盖、库条目 `source` 为空 | 补库或加覆盖列；检查 `source` |
| `FileNotFoundError` | 路径相对位置写错 | 路径相对**配置文件**目录 |
| `ValidationError` / `extra_forbidden` | 配置键拼错或多了字段 | 对照第 6 章；删除未知键 |
| `缺少必需列 [...]` | 事件表/CSV 列名不一致 | 用报错里的“实际列”修正 |
| `DuplicateEventError` | 同日同作物同器官两行 | 删除重复行；不同器官才可同日 |
| `FluxImbalanceError` | 移除量 > 储量；单位或初始值错 | 检查产量单位、器官含量、初始含量 |
| `UnitError` | 单位不在白名单 | 见附录 A；换合法单位或先换算 |
| 闭合超阈值 | 浮点/实现异常，通常不是舍入 | 检查是否改过代码或并发写文件 |
| `format: netcdf` 报缺块 | 没写 `netcdf` 配置 | 补 `cache` 与六个名字 |
| `缺少维度/变量` | 维度名/变量名与文件不符 | 按报错列出的实际名字改配置 |
| 坐标超出网格 | 田块经纬度不在数据范围 | 核对范围与经度制式 |
| 时间轴同日多步 | 数据是小时/6 小时尺度 | 先聚合到日，或换日尺度数据 |
| `validate` 找不到列 | 模拟输出是占位模式 | 用正式运行结果；去掉 `_PLACEHOLDER` |
| `validate` 匹配超容差 | 观测日期离模拟太远 | 增大容差或改 `nearest`/`linear` |
| 输出被覆盖 | 多次运行同一目录 | 每次用独立输出目录 |

## 12.2 数值异常诊断

### 闭合残差不是 0

- 通量为零时应当严格为 0；
- 有通量时为 1e-15 量级的浮点结合差异，相对残差 ~1e-16，属正常；
- 若相对残差接近或超过 1e-8，说明状态被外部修改、代码被改动或输出被拼接。
  先重跑同一配置确认；仍异常再查是否有并发写同一输出目录。

### 年增量比预期大/小一个数量级

按以下顺序排查：

1. 沉降单位：`g/ha/day` 与 `mg/m2/day` 是否混淆；
2. 产量/施肥单位：`t/ha` 是否被当成 `kg/ha`；
3. 含量单位：`μg/kg` 是否被当成 `mg/kg`；
4. 土壤质量：容重与层厚是否正确（0–20 cm、1300 → 260 kg·m⁻²）；
5. 是否把有效态含量当成总量；
6. 是否把多个田块的输入叠加到同一配置。

### Pb 的累积明显偏高

这是**预期行为**，不一定是 bug：

- 径流是 Pb 的主要输出途径，v1 未实现；
- 关闭径流后 Pb 累积会被显著高估；
- 日志应对 Pb 给出“强警告”。

若要用 Pb 做结论，必须先实现或引入径流项，或明确声明结果为上限。

## 12.3 NetCDF 常见问题

| 现象 | 原因 | 处置 |
|---|---|---|
| 变量找不到 | 配置用了通用名，文件是机构自定义名 | 用 `dataset.variables` 的实际名字 |
| 维度找不到 | 同上 | 用 `dataset.dimensions` 的实际名字 |
| 变量维度不符 | 有额外维度或不是三个配置维度 | 先 squeeze/选择，或换变量 |
| 坐标不是一维 | 使用了 2D 经纬度 | v1 不支持；先转成一维规则网格 |
| 时间无法解析 | 缺 `units` 或日历特殊 | 配 `time_units`，或先在外部转换 |
| 同一天多步 | 时间分辨率高于日 | 先聚合到日 |
| 掩码值报错 | 海洋/缺测填充 | 补全或换格点；不要静默补零 |
| 缓存被覆盖 | `cache` 与源文件同名 | 换独立 CSV 路径 |

## 12.4 `validate` 常见问题

| 现象 | 原因 | 处置 |
|---|---|---|
| 缺少列 | 模拟列名由 `element/layer` 推导，输出不含该列 | 检查 `element`、`layer_id` 与输出频率 |
| 观测日期重复 | 同日多条观测 | 先合并（均值/中位/按深度） |
| 匹配超容差 | 容差太小或规则不合适 | 调大容差或改 `linear` |
| 趋势为 `—` | 点少或未跨 2 个日历年 | 这是预期行为；补点或只报告短期误差 |
| 相对误差为 `—` | 所有观测值都为 0 | 检查观测值；相对误差无定义 |
| 输出覆盖输入 | `output.directory` 与输入同目录且重名 | 换输出目录或文件名 |

## 12.5 FAQ

**Q1：能一次跑多块田吗？**
不能。v1 是单田块模型。请为每块田准备独立目录与配置，用脚本循环调用。

**Q2：能加第三种元素吗？**
可以。把元素加入 `elements`，在 `soil.csv` 增加 `initial_<E>`，在沉降/事件/参数库中提供对应键。
代码不含元素名。

**Q3：能加新的作物或器官吗？**
可以。作物、器官是事件表与参数库中的**数据**；只要命名一致，不需要改代码。
但作物吸收仍只在收获日发生。

**Q4：为什么结果叫“上限”？**
淋溶、径流、侵蚀等输出过程返回 0，土壤中的金属只进不出。真实系统中这些过程会带走一部分金属，
所以模拟储量高于真实值。

**Q5：为什么仓库不提供 BCF 默认值？**
BCF 跨田块、作物、老化状态可差 10–100 倍。给一个“出厂值”会被误用到不适用的田块。
请从本地观测反算，或显式选择文献区间并做敏感性分析。

**Q6：能用有效态作 BCF 分母吗？**
v1 不支持，`basis: available` 会直接报错。有效态只作诊断，不进入主状态。

**Q7：月表的通量是月总量吗？**
不是。月表通量是**月均日通量**；月总量 = 月均 × 当月天数。这样列名与单位保持一致。

**Q8：怎么改输出频率？**
改 `output.interval`，例如 `[annual]` 只写年表。列集合不变。

**Q9：能只重跑观测对比吗？**
可以。`hmfms validate validation.yaml` 只读已有模拟 CSV，不重跑模型。

**Q10：两次运行结果会完全一样吗？**
同一配置、同一输入、同一版本 → 是；输出含首行在内逐字节一致。
配置指纹可用于确认两次运行是否用了同一套有效输入。

**Q11：同一天能收获两次吗？**
同一作物同一器官不能；不同器官（如籽粒与秸秆）可以，但通常秸秆走秸秆表。

**Q12：可以写多层土壤吗？**
结构上可以，但 v1 科学上只支持单层；多层会导致通量按层重复。请只写一层。

**Q13：支持 Windows 吗？**
支持。开发与测试主要在 Windows 完成；Linux/macOS 使用相同的 Python 命令。

**Q14：性能如何？**
十年单田块为秒级到十秒级；纯 Python、单线程，不支持 MPI。

**Q15：怎么引用？**
见 13.3。当前没有期刊论文，请引用软件名称、版本、仓库与 tag。

## 12.6 日志与报告 bug

- 警告与错误输出到 stderr；
- 默认不显示 INFO 级消息（例如 NetCDF 提取详情）；需要时可在调用方配置 Python `logging`；
- 报告 bug 请附：
  1. `model.yaml`（或最小复现配置）；
  2. `daily.csv`；
  3. `resolved_parameters.csv`；
  4. 配置指纹与完整报错文本。

---

# 13. 最佳实践、引用与许可

## 13.1 最低报告清单

一份可复核的运行报告至少包含：

- [ ] 软件版本与 tag（如 `v0.2.0-alpha`）；
- [ ] `schema_version` 与配置指纹；
- [ ] 模拟时间范围、元素、土层；
- [ ] 输入数据来源与单位；
- [ ] 参数溯源表（或至少关键参数的 tier/source）；
- [ ] 闭合残差的最大相对值；
- [ ] 关闭了哪些过程，以及“结果为上限”的声明；
- [ ] 观测对比的匹配规则、匹配点数与指标；
- [ ] 已知限制与不确定性。

## 13.2 建议的敏感性分析

至少对以下参数做区间分析：

1. **BCF**：田间区间与盆栽区间分别跑；若用固定浓度，则对器官浓度做区间；
2. **秸秆还田率**：0.3 / 0.55 / 0.8 三个端点；
3. **沉降**：区域下限/均值/上限；
4. **灌溉水浓度**：缺实测时做 0 与文献上限两情景；
5. **肥料品类**：尤其是磷肥 Pb，品类间可差数十倍。

报告时给出区间，而不是只给一个点估计。

## 13.3 引用

当前没有正式期刊论文。建议引用软件本身：

> HMFMS — Heavy Metal Flux Model in Soil, version 0.2.0-alpha,
> https://github.com/YYC-JNU/HMFMS (tag: v0.2.0-alpha).

方法描述可引用模型采用的公开文献与参数来源（见 `docs/parameters.md`），
并在方法中写明：单田块、0–20 cm、日步长、关闭水文过程、结果为累积上限。

## 13.4 许可

MIT License。见仓库根目录 `LICENSE`。

---

# 附录 A 单位换算

## A.1 允许的单位（白名单）

| 量纲 | 允许单位 | 内部单位 |
|---|---|---|
| `flux` | `mg/m2/day`、`g/ha/day`、`g/ha/yr`、`g/ha/a`、`kg/ha/yr` | mg·m⁻²·d⁻¹ |
| `concentration` | `mg/kg`、`ug/kg`、`g/kg` | mg·kg⁻¹ |
| `water_concentration` | `mg/L`、`ug/L`、`μg/L`、`ng/L` | mg·L⁻¹ |
| `water_depth` | `mm`、`cm`、`m` | mm |
| `application_rate` | `kg/ha`、`t/ha` | kg·ha⁻¹ |
| `bulk_density` | `kg/m3`、`g/cm3` | kg·m⁻³ |
| `thickness` | `m`、`cm` | m |
| `dimensionless` | `-`、`1` | 原值 |

不在白名单内的单位会在装载期报 `UnitError`，错误文本列出允许值。

## A.2 常用换算

| 从 | 到 | 系数 |
|---|---|---|
| `g/ha` | `mg/m²` | × 0.1 |
| `kg/ha` | `mg/m²`（乘以 mg/kg 后） | ÷ 10000 |
| `t/ha` | `kg/ha` | × 1000 |
| `g/cm3` | `kg/m3` | × 1000 |
| `cm` | `m` | ÷ 100 |
| `ug/L` | `mg/L` | ÷ 1000 |
| `ng/L` | `mg/L` | ÷ 1,000,000 |
| `mm` 降水 | `L/m²` | × 1 |

## A.3 基础等式

```text
M[mg/m²] = C[mg/kg] × ρ_b[kg/m³] × d[m]
F[mg/m²/day] = R[kg/ha] × C[mg/kg] / 10000
F[mg/m²/day] = I[mm] × C_w[mg/L]
F[mg/m²/day] = P[mm] × C_dep[μg/L] / 1000
```

---

# 附录 B 参数键命名

| 键 | 含义 | 单位 |
|---|---|---|
| `soil.bulk_density` | 容重 | kg·m⁻³ |
| `soil.ph` | pH | 1 |
| `soil.organic_matter` | 有机质 | 1（通常 %） |
| `soil.clay` | 黏粒 | 1（通常 %） |
| `soil.cec` | 阳离子交换量 | 1（cmol·kg⁻¹） |
| `soil.initial.<E>` | 初始土壤总量 | mg·kg⁻¹ |
| `fert.<品类>.<E>` | 肥料含量 | mg·kg⁻¹ |
| `irr.<水源>.<E>` | 灌溉水浓度 | mg·L⁻¹ |
| `crop.<作物>.<器官>.<E>` | 器官固定浓度 | mg·kg⁻¹ |
| `bcf.<作物>.<器官>.<E>` | 富集系数 | 1（无量纲） |

事件覆盖列：

| 表 | 覆盖列 |
|---|---|
| 施肥 | `<E>_mg_kg_override` |
| 灌溉 | `<E>_mg_L_override` |
| 收获（浓度模式） | `<E>_mg_kg_override` |
| 收获（BCF 模式） | `<E>_bcf_override` |
| 秸秆 | `<E>_mg_kg_override` |

> 本项目不提供 `bcf.*` 默认值。缺失即报错。

---

# 附录 C 输出列全清单

对每个元素 `E`、每个土层 `L`：

```text
date

soil_concentration_<E>_L<层>_mg_kg
soil_mass_<E>_L<层>_mg_m2

deposition_dry_<E>_mg_m2_d
deposition_wet_<E>_mg_m2_d

deposition_<E>_mg_m2_d
fertilization_<E>_mg_m2_d
irrigation_<E>_mg_m2_d
straw_return_<E>_mg_m2_d
harvest_<E>_mg_m2_d
straw_removal_<E>_mg_m2_d
lumped_loss_<E>_mg_m2_d
leaching_<E>_mg_m2_d
runoff_<E>_mg_m2_d
erosion_<E>_mg_m2_d
volatilization_<E>_mg_m2_d
dust_emission_<E>_mg_m2_d

in_total_<E>_mg_m2_d
out_total_<E>_mg_m2_d
net_flux_<E>_mg_m2_d
cumulative_input_<E>_mg_m2
cumulative_output_<E>_mg_m2
closure_error_<E>_mg_m2
```

- 单层列数：`1 + 22 × 元素数`。
- 未实现过程的列恒为 0。
- 占位模式下，除 `date` 外全部追加 `_PLACEHOLDER`。
- 三张频率表列集合相同，只有 `date` 取值格式与聚合口径不同。

---

# 附录 D 配置字段速查

## D.1 `model.yaml`

```yaml
name: my-field                 # 必填
version: "0.1.0"               # 必填，元数据
time:
  start: "2020-01-01"          # 必填
  end: "2020-12-31"            # 必填
  timestep: "1D"               # 只接受 1D
elements: [Cd, Pb]             # 非空、不重复
element_configs:               # 可选
  Cd: elements/Cd.yaml
field: field.yaml              # 必填
inputs:
  soil:
    file: soil.csv
    units: {depth: cm, bulk_density: kg/m3, concentration: mg/kg}
  deposition:                  # 可选
    file: deposition.csv
    format: csv                # csv | netcdf
    units: mg/m2/day
    date_column: date
    dry_columns: {Cd: dry_Cd, Pb: dry_Pb}
    wet_columns: {Cd: wet_Cd, Pb: wet_Pb}
    gap_policy: error          # error | interpolate
    magnitude_check:           # 可选
      Cd: {min: 0.0, max: 0.1}
parameters:                    # 可选
  library: defaults/params.yaml
management:                    # 四块均可选
  fertilizer_events: {file: management/fertilizer_events.csv,
                      units: {amount: kg/ha, concentration: mg/kg}}
  irrigation_events: {file: management/irrigation_events.csv,
                      units: {amount: mm, concentration: mg/L}}
  harvest_events:    {file: management/harvest_events.csv,
                      units: {yield_rate: kg/ha, concentration: mg/kg}}
  straw_events:      {file: management/straw_events.csv,
                      units: {amount: kg/ha, concentration: mg/kg}}
output:
  directory: output            # 必填
  interval: [daily, monthly, annual]
closure_tolerance: 1.0e-8
```

## D.2 `validation.yaml`

```yaml
element: Cd
layer_id: 1
simulation: {file: output/daily.csv, date_column: date}
observations:
  file: observations.csv
  date_column: date
  value_column: cd_mg_kg
  units: mg/kg
match: {rule: nearest, tolerance_days: 30}
metrics: {trend_min_points: 3}
output: {directory: validation}
```

## D.3 其余文件

| 文件 | 必需内容 |
|---|---|
| `field.yaml` | `field_id`、`area_ha`、`location.latitude/longitude` |
| `soil.csv` | `layer_id, top, bottom, bulk_density, ph, organic_matter, clay, cec, initial_<E>` |
| `elements/<E>.yaml` | `element`；`crop_uptake` 与 `diagnostics` 可选 |
| `management/*.csv` | 见 6.7 的必需列 |
| `defaults/*.yaml` | `key, value, unit, source`；`confidence/applicability` 可选 |

---

# 附录 E 错误信息格式

| 异常 | 何时出现 | 关键字段 |
|---|---|---|
| `ParameterSourceError` | 参数在所有层级缺失 | 键、来源需求（文件:行）、文档小节、已尝试层级、处置建议 |
| `DuplicateEventError` | 同日同作物同器官重复 | 类型、文件、日期、明细、两个行号、处置 |
| `FluxImbalanceError` | 更新后储量 < 0 | 日期、元素、土层、储量、输入/输出分项、当日事件 |
| `UnitError` | 单位/量纲不在白名单 | 量纲、单位、允许值 |
| `FileNotFoundError` | 配置/数据文件不存在 | 路径 |
| pydantic `ValidationError` | 配置结构不合法 | 字段路径、原因 |
| `ValueError` | 事件列缺失、日期格式、重复日期等 | 具体文件/行与原因 |

错误信息的设计目标是：**五分钟内能定位并修复**。若报错缺少文件/行/键，属于缺陷，请报告。

---

# 附录 F 术语表

| 术语 | 含义 |
|---|---|
| 状态量 / 储量 `M` | 单位面积土壤中某元素的质量，mg·m⁻² |
| 土壤总量 `C` | 土壤中某元素各形态含量之和，mg·kg⁻¹ |
| 通量 `F` | 单位面积单位时间的质量流，mg·m⁻²·d⁻¹ |
| 快照 | 当日开始时状态的一份副本，所有模块共享 |
| 闭合残差 | 期末 − 期初 − 累积输入 + 累积输出 |
| 事件表 | 按日期列出的管理操作，不是日序列 |
| 参数溯源 | 每个参数的取值、单位、层级、来源、置信度 |
| tier | 参数命中层级：1 事件、2 实测、3 选定库、4 文献、0 占位 |
| 配置指纹 | 有效输入的 16 位哈希，用于判断两次运行是否同源 |
| 占位模式 | 缺参数取 0 的结构演示模式，输出不可用于结论 |
| 累积上限 | 关闭输出过程时的结果解释，不是预测值 |
| BCF | 器官含量 ÷ 土壤总量，无量纲 |
| `f_removal` | 器官被带走的比例 |
| `k` / `return_fraction` | 秸秆还田比例 |
| 匹配方式 | 观测日期对到模拟日期的方式：exact / nearest / linear |

---

# 附录 G 文件树与示例

```text
HMFMS/
├─ src/hmfms/
│  ├─ cli.py                 run / validate / demo
│  ├─ validation.py          指标、报告、图
│  ├─ demo.py                十年演示入口
│  ├─ config/                配置 schema 与装载
│  ├─ core/                  状态、积分器、注册表、事件/驱动契约
│  ├─ io/                    沉降、事件、观测、输出、指纹
│  ├─ parameters/            参数库与五级解析
│  ├─ schemes/               通量模块
│  └─ utils/units.py         单位白名单与换算
├─ tests/                    251 个测试
├─ examples/
│  ├─ minimal/               10 天最小算例
│  └─ paddy-10y/             十年稻田演示
├─ docs/
│  ├─ USER_GUIDE.md          本文件
│  └─ parameters.md          参数默认值与来源
├─ pyproject.toml
├─ README.md
└─ LICENSE
```

示例入口：

```bash
python -m hmfms run examples/minimal/model.yaml
python -m hmfms demo
python -m hmfms validate examples/paddy-10y/validation.yaml
```

---

# 附录 H 版本与兼容性

## H.1 版本历史

| 版本 | 内容 | 测试 | schema |
|---|---|---|---|
| 0.1.0-alpha | 骨架、沉降、参数链、施肥、灌溉、收获（浓度）、秸秆 | 176 | 2 |
| 0.2.0-alpha | 新增 BCF、输出契约、NetCDF、观测对比、十年演示 | 251 | 4 |

## H.2 从 0.1 迁移到 0.2

- 既有 `model.yaml`、`field.yaml`、`soil.csv`、事件表**可以直接使用**；
  新字段都是可选的（`format` 默认 `csv`，`element_configs` 可省略）。
- `daily.csv` 的列没有改名；参数溯源表新增 `note` 列。
- 新增 `monthly.csv` / `annual.csv`（若 `interval` 包含）。
- 首行新增 `fingerprint` 与 `frequency`；读取时跳过 `#` 行。
- 新增 `--allow-placeholder`、`validate`、`demo` 命令。
- 如果旧脚本按列位置读取，请改为按列名读取。

## H.3 兼容承诺与边界

- 输出列只增不删、不改名；变更时提升 `schema_version`。
- 配置对未知字段报错；升级后若报 `extra_forbidden`，说明使用了已删除/拼错的键。
- `0.2.0-alpha` 仍是 pre-release；正式研究请固定 tag，并在报告中写版本与指纹。