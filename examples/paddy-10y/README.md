# 十年稻田演示（T13）

一键运行：

```bash
python -m hmfms demo            # 跑模型 + 观测对比
python -m hmfms demo --no-validate
```

## 场景

- 单田块、0–20 cm、日步长，2015-01-01 至 2024-12-31（3653 天）。
- 土壤与作物参数取自 `PARAMS_SOURCE.md` §13.4（祁阳稻田 2019）：
  pH 6.62、黏粒 12%、CEC 193、有机质 2.15%、初始 Cd 0.35 / Pb 25 mg/kg。
- 年沉降 Cd 0.673、Pb 20.6 mg·m⁻²·a⁻¹；施肥与秸秆量取自 §13.6。
- 灌溉 5 × 220 mm/a，Cd 0.13 μg/L（§1.4.1）。
- 水稻：籽粒 6.7 t/ha、秸秆 6.7 t/ha、秸秆还田率 0.8。

## 已知假设

Pb 的器官浓度（籽粒 0.05、秸秆 0.60 mg/kg）与灌溉水 Pb 浓度（0）没有可核验的
区域实测，是为了让演示闭合而设的**场景假设**，不是文献默认值。参数库中已标注来源。

## 预期结果

- Cd 年增量约 0.0048 mg·kg⁻¹·a⁻¹，在 `equations.md` §7 的 0.0027 三倍以内。
- 关闭淋溶/径流/侵蚀，结果为**累积上限**；日志按元素给出强度不同的警告
  （Cd 一般、Pb 强）。
- 输出：`output/daily.csv`、`monthly.csv`、`annual.csv`、`resolved_parameters.csv`；
  `validation/report.md`、`comparison.png`、`matched_points.csv`。