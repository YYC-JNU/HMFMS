"""配置指纹。

指纹回答一个很具体的问题：**两次运行的有效输入是不是同一份？**

因此它按“模型实际用到的数值”计算，而不是按原始文件字节：

* 模型配置先规范化，并**去掉沉降接线块**（文件路径、维度名、缓存路径都是
  取数方式，不是数据本身）；
* 沉降用加载后的**内部单位序列**参与哈希，因此 NetCDF 与等价的直接 CSV
  会得到同一指纹；
* 其余输入文件按内容哈希，文本统一按 LF 归一，二进制按原字节流式读取。

同一份配置换机器、换目录、换行风格，指纹不变；任何影响结果的数值变了，
指纹就变。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

from hmfms.config.models import ModelConfig
from hmfms.io.deposition import DepositionSeries

FINGERPRINT_LENGTH = 16
CHUNK_SIZE = 1 << 20

# 文本输入按 LF 归一后再哈希：同一份配置在 Windows（CRLF）与 Linux（LF）
# 上必须得到同一指纹。二进制文件（如 NetCDF）按原字节流式读取。
TEXT_SUFFIXES = frozenset({".yaml", ".yml", ".csv", ".txt", ".json", ".md"})


def _display_name(path: Path, base: Path) -> str:
    """优先用相对路径，使指纹不随项目所在目录变化。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def model_material(model: ModelConfig) -> bytes:
    """模型配置的规范化表示。

    只去掉沉降的**取数接线**（源文件、格式、列名、维度名）——这些不改变
    模型看到的数值；`units` / `gap_policy` / `magnitude_check` 仍然计入指纹。
    沉降数值本身由 :func:`deposition_material` 承担。
    """
    payload = model.model_dump(mode="json")
    inputs = payload.get("inputs")
    if isinstance(inputs, dict):
        deposition = inputs.get("deposition")
        if isinstance(deposition, dict):
            for key in (
                "file",
                "format",
                "date_column",
                "dry_columns",
                "wet_columns",
                "netcdf",
            ):
                deposition.pop(key, None)
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def deposition_material(series: DepositionSeries) -> bytes:
    """沉降序列的规范化表示：日期、干/湿、元素、内部单位数值。"""
    lines: list[str] = []
    for element in series.elements:
        for kind, values in (("dry", series.dry[element]), ("wet", series.wet[element])):
            for day in sorted(values):
                lines.append(f"{day.isoformat()},{kind},{element},{values[day]!r}")
    return "\n".join(lines).encode("utf-8")


def _update_with_path(digest, path: Path) -> None:
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        return
    digest.update(path.stat().st_size.to_bytes(8, "big"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)


def config_fingerprint(
    files: Iterable[Path],
    base: Path,
    *,
    omitted: Iterable[Path] = (),
    overrides: Mapping[Path, bytes] | None = None,
    extra: Mapping[str, bytes] | None = None,
) -> str:
    """对本次运行的有效输入做稳定哈希，返回 16 位十六进制指纹。"""
    base = Path(base).resolve()
    omitted_resolved = {Path(path).resolve() for path in omitted}
    override_map = {
        Path(path).resolve(): data for path, data in (overrides or {}).items()
    }

    materials: list[tuple[str, bytes | Path]] = []
    for candidate in files:
        resolved = Path(candidate).resolve()
        if resolved in omitted_resolved:
            continue
        name = _display_name(resolved, base)
        materials.append((name, override_map.get(resolved, resolved)))
    for name, data in (extra or {}).items():
        materials.append((name, data))

    digest = hashlib.sha256()
    for name, payload in sorted(materials, key=lambda item: item[0]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        if isinstance(payload, Path):
            _update_with_path(digest, payload)
        else:
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()[:FINGERPRINT_LENGTH]