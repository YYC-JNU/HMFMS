# 默认参数与来源

本文件列出随仓库提供的默认参数，以及必须由用户自行提供的参数。

三条规则：

1. **每条默认值都有出处**。参数库中 `source` 为空的条目会被忽略并告警。
2. **默认值只是起点**。任何正式模拟都应当用本地实测值覆盖它们。
3. **单位与口径必须对齐**。同一份文献里的数值换个口径就可能差一个数量级，见文末的"口径提示"。

---

## 1. 随仓库提供的默认值

`examples/minimal/defaults/soil_params.yaml` 中的条目：

| 参数键 | 默认值 | 单位 | 适用条件 | 来源 |
|---|---|---|---|---|
| `soil.ph` | 6.62 | — | 湖南祁阳稻田，n = 56，2019 | J. Hazard. Mater. 2025, 499:140030, Table S4 |
| `fert.compound_npk.Cd` | 1.31 | mg/kg | 湖南祁阳，NPK 复合肥 | 同上, Table S2 |
| `fert.pig_manure.Cd` | 1.63 | mg/kg | 湖南祁阳，猪粪（鲜重） | 同上, Table S2 |
| `crop.rice.grain.Cd` | 0.25 | mg/kg | 湖南祁阳稻田稻米 | 同上, Table S4 |
| `crop.rice.straw.Cd` | 0.78 | mg/kg | 湖南祁阳稻田秸秆 | 同上, Table S4 |
| `irr.canal.Cd` | 0.00013 | mg/L | 浙江稻区地表水（= 0.13 μg/L） | Fu et al. 2021, J. Hazard. Mater. 412:125203，引述 Shi et al. 2019 |

> 这些值只用于让示例算例能跑起来，**不代表任何具体田块**。

## 2. 必须由用户提供的参数

以下参数没有随附默认值——它们要么田块特异，要么公开数据不足以支撑一个可信的默认值。
缺失时模型会报错并指出键名与位置，不会静默取 0。

| 参数键 | 含义 | 为什么没有默认值 |
|---|---|---|
| `soil.bulk_density` | 容重（kg·m⁻³） | 田块特异，建议直接填入土壤三普或剖面实测 |
| `soil.organic_matter` / `soil.clay` / `soil.cec` | 有机质、黏粒、CEC | 同上 |
| `soil.initial.<元素>` | 土壤初始含量（mg·kg⁻¹） | 只能来自实测 |
| `fert.<品类>.Pb` | 肥料 Pb 含量 | 品类间差异达数十倍，给单一值会误导 |
| `crop.<作物>.<器官>.Pb` | 作物器官 Pb 含量 | 同上 |
| `bcf.<作物>.<器官>.<元素>` | 富集系数（器官 mg/kg ÷ 土壤 mg/kg） | 田间与盆栽值差 1–2 个数量级；仅当该元素切到 `bcf` 模式时需要 |
| `irr.<水源>.Pb` | 灌溉水 Pb 浓度 | 缺乏可核验的区域调查值 |
| `k_loss` | 综合损失系数（d⁻¹） | 经验项，只能由本地观测率定 |

### 2.1 切换到 BCF 模式

默认的吸收模式是 `concentration`（器官含量取固定值）。要让某个元素的器官含量随土壤含量变化，
在 `model.yaml` 里为该元素指向一个元素配置文件：

```yaml
elements:
  - Cd
  - Pb
element_configs:
  Cd: elements/Cd.yaml          # 只有 Cd 切到 BCF；Pb 保持 concentration
```

```yaml
# elements/Cd.yaml
element: Cd
crop_uptake:
  mode: bcf                     # concentration | bcf，二选一
  basis: total                  # v1 只支持 total；available 会报错
```

然后在参数库里给出 `bcf.rice.grain.Cd` 等条目，或在收获事件表用 `Cd_bcf_override` 列覆盖。
同一元素同时写两种模式、或使用 `basis: available`，都会在装载期直接报错。
注意 v1 的 BCF 只作用于收获表；秸秆表仍按 `crop.<作物>.<器官>.<元素>` 的固定浓度计算。

### 2.2 使用 NetCDF 沉降

格点 NetCDF 不需要先手工转 CSV：把 `format` 设为 `netcdf`，并给出维度名与变量名。

```yaml
inputs:
  deposition:
    file: grid.nc
    format: netcdf
    units: mg/m2/day
    dry_columns: {Cd: dry_dep_Cd}      # 元素 → 变量名
    wet_columns: {Cd: wet_dep_Cd}
    netcdf:
      cache: extracted.csv             # 提取产物
      time_dim: time
      lat_dim: latitude
      lon_dim: longitude
      time_variable: time
      latitude_variable: latitude
      longitude_variable: longitude
```

适配器按 `field.yaml` 的 `location` 做最近邻提取，并把单点序列写成与直接 CSV 完全
相同的格式。之后可以直接把 `format` 改为 `csv`、`file` 指向 `extracted.csv` 复用。
坐标超出网格范围、变量或维度缺失时，模型会直接报错并列出实际存在的名字与范围。

## 3. Cd 与 Pb 的量级参考

用于判断自己的输入是否在合理范围内（换算为日均通量）：

| 途径 | Cd | Pb | 说明 |
|---|---|---|---|
| 大气沉降 (mg·m⁻²·a⁻¹) | 0.06 – 0.84，全国均值 0.49 | 4.1 – 30.2，全国均值 20.6 | 分区域差异大，西北最低、四川盆地最高 |

来源：Land 2023, 12(6):1240, Table 3（2000–2021 中国 9 大区）。

作物吸收的文献区间跨度很大，取决于土壤是否为新近污染：

| 作物 | 器官 | 富集系数（器官 mg/kg ÷ 土壤 mg/kg） |
|---|---|---|
| 水稻 | 籽粒（盆栽，新加 Cd） | 0.30 – 1.11 |
| 水稻 | 籽粒（田间，老化后） | 0.007 – 0.13 |
| 小麦 | 籽粒（田间） | 0.03 – 0.40 |

来源：J. Integr. Agric. 2014（10.1016/S2095-3119(14)60926-6）；Ecotoxicol. Environ. Saf. 2022（10.1016/j.ecoenv.2022.114244）；Environ. Sci. 2020, 41(3):1482–1490。

**这两组相差一个数量级以上不是数据错误**，而是"新加入的 Cd"与"老化后的 Cd"在有效性上的真实差异。
用错会让作物移除量高估或低估十倍。建议用本地观测率定。

## 4. 口径提示

代入前请核对这四项，它们是最常见的错误来源：

1. **pH 的测定方法**。水浸提（水土比 2.5:1）与 0.01 mol/L CaCl₂ 浸提的数值差 0.5–1.0 个单位，
   不可互换。参数库的 `applicability` 字段应写明用的是哪一种。
2. **干重 / 鲜重**。作物器官与粪肥含量必须注明；两者可差 5–20 倍。
3. **CEC 的单位**。`cmol·kg⁻¹` 与 `meq·kg⁻¹` 数值相同，但与 `mmol·kg⁻¹` 不同。
4. **沉降是通量还是浓度**。`mg·m⁻²·d⁻¹` 是通量；`μg·L⁻¹` 是浓度，需要乘以降水量才能得到通量。

## 5. 占位模式（仅用于结构演示）

缺失参数默认会直接报错。加 `--allow-placeholder` 可让模型用占位值 0 继续跑，但：

- 输出 CSV 的每一个数值列都带 `_PLACEHOLDER` 后缀（`date` 除外）；
- 参数溯源表中对应行记为 `tier=0`、`tier_label=PLACEHOLDER`；
- 日志逐条列出所有占位参数。

这类结果只用于检查配置结构与输出列，**不能用于任何结论**。

注意：容重、层厚这类必须有正值的量即使占位也仍会触发校验错误——占位模式解决的是
"参数缺失"，不是"输入结构无效"。

## 6. 观测对比（validate）

`hmfms validate validation.yaml` 只读**已经生成**的模拟 CSV 与观测表，不重跑模型，
也不需要主模型配置。全部接线都在这个文件里：

```yaml
element: Cd
layer_id: 1
simulation:
  file: output/daily.csv
  date_column: date
observations:
  file: observations.csv
  date_column: sample_date
  value_column: cd_mg_kg
  units: mg/kg
match:
  rule: nearest       # exact | nearest | linear
  tolerance_days: 30  # 非 exact 规则必须给出
metrics:
  trend_min_points: 3
output:
  directory: validation
```

- 模拟数值列由 `element` 与 `layer_id` 从输出契约推导；观测列名必须显式写出。
- 匹配规则：`exact` 缺日期报错；`nearest` 超出容差报错；`linear` 在相邻模拟日之间插值，
  区间跨度超容差报错。每个匹配点的 `method` 与 `sim_date` 写入 `matched_points.csv`。
- 指标：MAE、RMSE、平均相对误差、期末误差、年际趋势误差（模拟与观测斜率的差，mg/kg/a）。
- 匹配点少于 `trend_min_points` 或没有跨至少两个日历年时，报告给出不确定性说明，
  不强行计算趋势。
- 产物：`report.md`、`comparison.png`、`matched_points.csv`。

## 7. 引用

1. Liu P., Xu D., Zhu Q. 等 (2025). Model-based spatio-temporal cadmium health risk assessment by
   rice consumption and management at regional scale in China. *J. Hazard. Mater.*, 499: 140030.
   DOI: 10.1016/j.jhazmat.2025.140030
2. (2023). Input Flux and the Risk of Heavy Metal(Loid) of Agricultural Soil in China: Based on
   Spatiotemporal Heterogeneity from 2000 to 2021. *Land*, 12(6): 1240. DOI: 10.3390/land12061240
3. Shi T., Ma J., Wu F. 等 (2019). Mass balance-based inventory of heavy metals inputs to and
   outputs from agricultural soils in Zhejiang Province, China. *Sci. Total Environ.*, 649: 1269–1280.
   DOI: 10.1016/j.scitotenv.2018.08.414
4. Fu Y. 等 (2021). Cadmium concentration and its typical input and output fluxes in agricultural
   soil. *J. Hazard. Mater.*, 412: 125203.
5. (2014). Variation of Cd concentration in various rice cultivars and derivation of cadmium
   toxicity thresholds for paddy soil by species-sensitivity distribution. *J. Integr. Agric.*
   DOI: 10.1016/S2095-3119(14)60926-6
6. (2022). Selection of rice and maize varieties with low cadmium accumulation and derivation of
   soil environmental thresholds in karst. *Ecotoxicol. Environ. Saf.*, 114244.
   DOI: 10.1016/j.ecoenv.2022.114244