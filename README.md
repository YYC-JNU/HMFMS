# HMFMS — Heavy Metal Flux Model in Soil

> ## ⚠️ 状态：0.4.0-alpha — 水文过程已部分落地，仍是研究原型
>
> **0.4.0-alpha 新增**：溶解态径流、颗粒态侵蚀、逐日水量诊断列；输出契约升到
> `schema_version=5`。淋溶与简化水量平衡自 0.3 起可用（0.3 未单独发布）。
> 322 个测试通过。发布出来是为了尽早获得反馈，而不是提供一个可以直接用于
> 科研结论的成品。请在使用前读完这段。
>
> **已经能做的**：从配置与输入数据出发，逐日推演单块田 0–20 cm 耕层土壤 Cd / Pb 的总量；
> 输出日/月/年三张频率表、分过程通量、累积收支、质量闭合误差与参数溯源；
> 作物吸收可用固定浓度或 BCF；沉降可读 CSV 或 NetCDF；另带一个独立的观测对比命令、
> 一个十年稻田演示场景与一个水文过程最小算例。
>
> **还不能做的**：风蚀与挥发仍返回零通量；径流只记**溶解态**，颗粒态记在 `erosion` 列，
> 两者不能相加；侵蚀速率按年值均匀摊分，不能解释单次暴雨。
> 径流比例、侵蚀速率与富集比**没有默认值**，必须由用户提供并说明来源。
>
> **还会变的**：配置字段、输出列名、内部 API 都可能在不通知的情况下调整。
> 如果你要用它，建议固定到某个 tag。

配置驱动的农田重金属质量平衡模型。单田块、0–20 cm 耕层、日步长，逐日推进土壤中 Cd / Pb
的总量，输出分过程通量、累积收支与质量闭合误差。

模型不模拟大气扩散与沉降过程：干/湿沉降由 WRF-EMEP 等外部模式提供，HMFMS 只负责
"落到田里之后发生了什么"。

## 文档

- **完整用户指南：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** ——
  安装、配置参考、输入数据、运行、输出列、观测对比、十年演示、排错与附录。
- 参数默认值与来源：[docs/parameters.md](docs/parameters.md)。

## 三条设计原则

1. **质量守恒优先**。每个时间步都检查 `期末储量 − 期初储量 = 累积输入 − 累积输出`，
   残差超过阈值即报错终止。储量永不裁剪为 0——负储量意味着某处算错了，掩盖它比崩溃更危险。
2. **参数全部可追溯**。每次运行输出 `resolved_parameters.csv`，逐参数记录取值、单位、
   命中层级与来源。任何默认值都能回答"它是哪来的"。
3. **元素、作物、器官、肥料品类、水源都是数据**。代码里没有 `Cd`、没有 `grain`、
   没有 `pig_manure`。加一种元素只需加配置文件。

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"
```

要求 Python ≥ 3.10。依赖：numpy、pandas、PyYAML、netCDF4、matplotlib、pydantic。

## 快速开始

十年稻田演示（2015–2024，3653 天，含观测对比）：

```bash
python -m hmfms demo
python -m hmfms demo --no-validate   # 只跑模型
```

最小算例：

```bash
python -m hmfms run examples/minimal/model.yaml
```

水文过程最小算例（淋溶、溶解态径流、颗粒态侵蚀 + 水量诊断列）：

```bash
python -m hmfms run examples/minimal-water/model.yaml
```

观测对比（只读已有模拟输出，不重跑模型）：

```bash
python -m hmfms validate examples/paddy-10y/validation.yaml
```

`examples/minimal` 是一个 10 天的合成算例，**数值为演示量级，不代表任何真实田块**。
`examples/minimal-water` 专门演示三个水文输出过程与五列水量，README 给出逐日手算过程；
其中的 `kd`、`runoff_fraction` 与 ER 都标注为示例假设，不是文献默认值。
`examples/paddy-10y` 是按文献区间合成的十年稻田场景，参数来源与假设见该目录 README。

## 模型描述

### 状态量

对元素 `e` 与土层 `l`，模型跟踪单位面积储量 `M`（mg·m⁻²）：

```
M[e,l] = C[e,l] · ρ_b[l] · d[l]                                (1)
```

其中 `C` 为土壤总含量（mg·kg⁻¹），`ρ_b` 为容重（kg·m⁻³），`d` 为层厚（m）。
首版只有一层（0–20 cm），但状态量始终保留 `[元素][土层]` 二维结构。

### 时间积分

显式 Euler，步长固定 1 天：

```
M(t+1) = M(t) + Δt · ( Σ F_in − Σ F_out )                       (2)
C(t+1) = M(t+1) / ( ρ_b · d )                                   (3)
```

同一天内所有过程模块读取**同一份状态快照**，因此模块的先后顺序不影响结果。

### 质量闭合

```
closure = M_end − M_start − Σ F_in + Σ F_out                    (4)
```

相对残差要求低于 `1e-8`（可在配置中调整）。注意：通量为零时残差恒等于 0；一旦有通量，
残差是浮点累加的结合次序差异（1e-15 量级），判断标准是**相对**残差，不是绝对等于 0。

### 输入通量

```
F_dep   = F_dry + F_wet                                          (5)
F_fert  = Σ_m  R_m · C_m / 10000                                 (6)
F_irr   = Σ_event  I · C_w                                       (7)
F_straw_return = Σ  Y_straw · C_straw · k / 10000                (8)
```

- `R_m`：施肥量（kg·ha⁻¹），`C_m`：肥料重金属含量（mg·kg⁻¹）
- `I`：灌溉量（mm），`C_w`：灌溉水浓度（mg·L⁻¹）。因 `1 mm = 1 L·m⁻²`，`mm × mg/L = mg/m²`
- `k`：秸秆还田比例（0–1）

若沉降数据给的是"降水中浓度"而非通量，按下式换算（这属于单位换算，不是过程参数化）：

```
F_dep = P · C_dep / 1000                                         (9)
```

### 输出通量

```
F_harvest      = Σ_organ  Y · C_organ · f_removal / 10000        (10)
F_straw_removal = Σ  Y_straw · C_straw · (1 − k) / 10000         (11)
F_lumped       = k_loss · M                                      (12)
```

`f_removal` 为器官被人为带走的比例。`F_lumped` 是**经验综合损失项**，无明确物理含义，
默认关闭，只用于用观测率定。

### 两条内部一致性约束

- **秸秆守恒**：`F_straw_return + F_straw_removal = Y_straw · C_straw / 10000`，
  即还田与外运严格互补，逐日断言。
- **器官不重复处理**：同一 `(作物, 器官)` 不得同时出现在收获表与秸秆表，
  否则同一份质量会被扣两次。装载期直接报错。

### 水文过程（0.3–0.4）

逐日水量按配置比例分配（`hydrology.runoff_fraction`，0–1，无默认值）：

```
excess   = max(0, P + I − ET)                                    (13)
Q_runoff = f_runoff · excess                                     (14)
q_leach  = excess − Q_runoff                                     (15)
```

金属通量：

```
F_leach  = q_leach  · f_exch · C_total / Kd                      (16)
F_runoff = Q_runoff · f_exch · C_total / Kd                      (17)   只记溶解态
F_erosion = (rate / 365) · ER · C_total                          (18)   只记颗粒态
```

- `Kd` 与 `f_exch` 是**元素级**参数（`kd.<元素>` / `f_exch.<元素>`），口径由
  `kd_basis: total|labile` 显式声明；`total` 时 `f_exch = 1`。
- 淋溶与径流共用同一 Kd，两者 `kd_basis` 不一致会在装载期报错。
- **`runoff` 与 `erosion` 不能相加**：前者是溶解态，后者是颗粒态；
  式 21 的“径流颗粒态”与式 22 是同一份质量的两种记账方式，本项目只记后者。
- 侵蚀速率按年值均匀摊到每日：年总量可核验，但单次暴雨事件无法解释。
- `runoff_fraction`、`erosion.rate`、`erosion.er.<元素>` **都没有默认值**，
  缺失即报错；它们必须来自本地观测或明确标注的假设。

### 已实现的过程

| 过程 | 方向 | 触发 |
|---|---|---|
| 大气沉降（干 + 湿） | 输入 | 每日 |
| 施肥 | 输入 | 事件日 |
| 灌溉 | 输入 | 事件日 |
| 秸秆还田 | 输入 | 事件日 |
| 作物收获 | 输出 | 事件日（收获日） |
| 秸秆外运 | 输出 | 事件日 |
| 综合损失（经验，默认关闭） | 输出 | 每日 |
| 淋溶（需气象） | 输出 | 每日 |
| 径流，溶解态（需气象 + 径流比例） | 输出 | 每日 |
| 侵蚀，颗粒态（需年速率 + ER） | 输出 | 每日 |

作物吸收在同一张表里支持两种模式：固定器官浓度（`concentration`）或富集系数
（`bcf`）。模式按元素配置，同一元素只能取一种；详见下文“更多能力”。

### 预留但仍未实现的过程

风蚀与挥发仍**返回零通量**，但输出列始终存在。启用时不需要改动任何下游读取代码。

**这意味着什么，请务必注意**：没有启用径流与侵蚀时，模型给出的是土壤重金属累积的
**上限**，不是预测值。而且对两个元素的影响程度不同——

- Cd 主要通过作物收获离开农田，未启用径流的影响相对有限；
- **Pb 主要通过地表径流离开农田**（实测占其总输出的一半以上），
  不配置 `runoff` 会**显著高估**。

模型在运行日志中会按元素分别给出警告，并说明径流/侵蚀的记账边界。

## 更多能力（0.2–0.4）

### 作物吸收：固定浓度或 BCF

默认 `concentration`：器官含量来自参数库或事件覆盖。要让某个元素的器官含量随土壤含量变化，
为它指向一个元素配置：

```yaml
element_configs:
  Cd: elements/Cd.yaml
```

```yaml
# elements/Cd.yaml
element: Cd
crop_uptake:
  mode: bcf          # concentration | bcf
  basis: total       # v1 只支持 total
```

BCF 值取自参数库 `bcf.<作物>.<器官>.<元素>`，或事件行 `<元素>_bcf_override`。
同一元素写两种模式、或使用 `basis: available`，装载期直接报错。
v1 的 BCF 只作用于收获表；秸秆表仍按固定浓度计算，并在运行日志中提示。

### 水文过程配置（0.4）

```yaml
# model.yaml
inputs:
  weather:
    file: weather.csv          # 必需：date、precipitation_mm；可选：et_mm
    units: {precipitation: mm/day, evapotranspiration: mm/day}
hydrology:
  runoff_fraction: 0.2         # 0–1；启用 runoff 时必须给，且不能与 runoff 分离
```

```yaml
# elements/Cd.yaml
element: Cd
leaching: {enabled: true, kd_basis: total}
runoff:   {enabled: true, kd_basis: total}   # 与 leaching 口径必须一致
erosion:  {enabled: true}
```

参数库需要 `kd.<元素>`；`kd_basis: labile` 时还需要 `f_exch.<元素>`；
侵蚀需要 `erosion.rate` 与 `erosion.er.<元素>`。缺任何一个都会报错，不会静默取 0。
完整可运行示例与手算过程见 `examples/minimal-water/`。

### 输出频率、配置指纹与占位模式

`output.interval` 可选 `daily` / `monthly` / `annual` 的任意组合，产物为
`daily.csv` / `monthly.csv` / `annual.csv`。三张表共用同一份列契约：

- 通量列（`_mg_m2_d`）与水量列（`_mm_d`）取**期内日均**；
- 状态、累积与闭合列取**期末值**。

每张表首行为：

```
# schema_version=5 fingerprint=<16hex> frequency=daily
```

指纹按**有效输入**计算：模型配置（去掉沉降取数接线）+ 加载后的沉降序列 + 其余输入文件。
同一配置换机器、换目录、换行风格都不变；任一影响结果的数值改动即变。
`--allow-placeholder` 可用占位值 0 做结构演示，此时所有数值列加 `_PLACEHOLDER` 后缀，
日志逐条列出占位参数——这类结果不能用于任何结论。

### 沉降：CSV 或 NetCDF

CSV 是一等公民。NetCDF 由适配器按田块经纬度最近邻提取后缓存为 CSV：

```yaml
inputs:
  deposition:
    file: grid.nc
    format: netcdf
    units: mg/m2/day
    dry_columns: {Cd: dry_dep_Cd}     # 元素 → 变量名
    wet_columns: {Cd: wet_dep_Cd}
    netcdf:
      cache: extracted.csv
      time_dim: time
      lat_dim: latitude
      lon_dim: longitude
      time_variable: time
      latitude_variable: latitude
      longitude_variable: longitude
```

维度名、变量名、缓存路径全部来自配置；坐标越界、变量/维度缺失都会报错并列出实际值。
提取后的缓存 CSV 可以直接改 `format: csv` 复用。

### 观测对比（独立命令）

`hmfms validate validation.yaml` 只读已有模拟 CSV 与观测表，不重跑模型，主模型也不认识观测路径。
支持 `exact` / `nearest` / `linear` 三种日期匹配，逐点记录匹配方式；输出
MAE、RMSE、平均相对误差、期末误差、年际趋势误差，以及 `report.md`、`comparison.png`、
`matched_points.csv`。观测点不足以估趋势时，报告写不确定性说明而不是报错。

## 配置结构

```
model.yaml                     主配置：时间范围、元素列表、各输入接线、输出选项、hydrology
field.yaml                     田块：编号、面积、经纬度
soil.csv                       土壤剖面：逐层的深度、容重、pH、有机质、黏粒、CEC、初始含量
deposition.csv                 逐日干/湿沉降通量（或 NetCDF + netcdf 块）
weather.csv                    逐日降水与蒸散（启用淋溶/径流时必需）
elements/<元素>.yaml           可选：吸收模式、淋溶/径流/侵蚀开关与 Kd 口径、按元素诊断
defaults/*.yaml                默认参数库（每条必须带来源）
management/fertilizer_events.csv
management/irrigation_events.csv
management/harvest_events.csv
management/straw_events.csv
validation.yaml                仅 validate 命令使用：观测列名、匹配规则与输出
```

四张事件表的列契约：

| 表 | 必需列 | 可选覆盖列 |
|---|---|---|
| 施肥 | `date`, `fertilizer_id`, `amount` | `<元素>_mg_kg_override` |
| 灌溉 | `date`, `amount`, `water_source` | `<元素>_mg_L_override` |
| 收获 | `date`, `crop_id`, `organ`, `yield`, `removal_fraction` | `<元素>_mg_kg_override` |
| 秸秆 | `date`, `crop_id`, `organ`, `amount`, `return_fraction` | `<元素>_mg_kg_override` |

**一行一个器官**：一次收获的籽粒与秸秆是两行，共用相同的 `(date, crop_id)`。

### 参数优先级

```
事件行覆盖  >  田块实测  >  用户指定的库条目  >  文献默认  >  报错
```

参数缺失不会静默取 0，而是报错并指出是哪个键、哪个文件的哪一行、该看哪份文档。
参数库里 `source` 为空的条目视为无效。

## 输出列

三张频率表（`daily.csv` / `monthly.csv` / `annual.csv`）的列集合是**固定且相同**的，
包含全部已实现与未实现过程的通量列：

```
date
soil_concentration_<元素>_L<层>_mg_kg
soil_mass_<元素>_L<层>_mg_m2
deposition_dry_<元素>_mg_m2_d
deposition_wet_<元素>_mg_m2_d
<12 个过程>_<元素>_mg_m2_d
in_total / out_total / net_flux / cumulative_input / cumulative_output / closure_error
water_precipitation_mm_d
water_evapotranspiration_mm_d
water_irrigation_mm_d
water_runoff_mm_d
water_percolation_mm_d
```

未启用过程的列恒为 0，但不会消失——这样升级版本时下游脚本不必改。
`date` 列的取值随频率变化（`YYYY-MM-DD` / `YYYY-MM` / `YYYY`），列名不变。
另有 `resolved_parameters.csv`（参数溯源）与 validate 的三件产物。

### 从 0.2 / 0.3 升级

- `schema_version` 由 4 升到 **5**：表末新增五列 `water_*_mm_d`。按列名读取的脚本无需修改；
  按列位置读取的必须适配。
- 新增 `inputs.weather`、`hydrology.runoff_fraction` 与元素级 `runoff` / `erosion` 块，均可选；
  不配置时数值与 0.2 相同（十年演示未变）。
- 参数库新增 `kd.<元素>`、`f_exch.<元素>`、`erosion.rate`、`erosion.er.<元素>`，均**无默认值**；
  缺失时装载期报错，不会静默取 0。

## 参数来源

模型中随附的默认参数全部来自公开文献或国家标准，逐条列在
[`docs/parameters.md`](docs/parameters.md)，包含取值、单位、适用条件与出处。

## 测试

```bash
pytest
```

322 个测试，覆盖单位换算、质量守恒（零输入 / 单一过程 / 综合输入输出）、
事件处理、参数解析链、错误信息、输出契约、BCF 模式、NetCDF 适配、观测对比、十年演示，
以及淋溶/径流/侵蚀的公式、参数缺失、口径一致性与水量对账。

## 反馈

欢迎通过 Issue 报告问题或提出建议。如果是数值结果异常，请一并提供 `daily.csv` 与
`resolved_parameters.csv`——后者能说明每个参数到底取了哪个值、来自哪一层。

## 已知限制

1. 单田块、单层，未实现多层垂向迁移。
2. 风蚀与挥发未实现（返回 0）。
3. 径流只记**溶解态**；颗粒态记在 `erosion` 列，两列不可相加。
4. 侵蚀速率按年值均匀摊到每日，不能解释单次暴雨事件。
5. 水量分配是集总比例，不含土壤储水、优先流与降雨强度。
6. `runoff_fraction`、`erosion.rate`、`erosion.er.<元素>` 都没有默认值，
   必须由用户提供并说明来源。
7. 未区分重金属化学形态与有效态动态；`basis: available` 会被拒绝。
8. 作物吸收发生在收获日，不模拟生育期内动态；BCF 模式只作用于收获表。
9. NetCDF 适配器要求一维坐标、三个配置维度、日尺度时间轴；掩码值直接报错。
10. 观测对比只做误差记账，不替使用者判断结果是否可接受。
11. 不使用 Fortran，首版为纯 Python 原型，性能不是当前优化目标。

## 许可

MIT License，见 [LICENSE](LICENSE)。