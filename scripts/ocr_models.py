#!/usr/bin/env python3
"""Install, verify and report the neural OCR engine's models.

``documents.ocr.engine: neural`` (or ``auto``) reads scanned pages with four
models run by ONNX Runtime inside the document worker
(``kazma_core/documents/ocr/neural.py``). This installs them:

1. downloads every source pinned in ``kazma_core/documents/ocr/neural_models.py``
   (``SOURCES``) and checks its size and SHA-256 -- and, for EasyOCR's zips,
   the weights file inside;
2. converts EasyOCR's two PyTorch recognizers to ONNX (``torch.onnx.export``,
   weights loaded with ``weights_only=True``) and checks each conversion
   against PyTorch on fixed inputs: the largest difference in any output must
   stay under 1e-3 and the best class must agree everywhere;
3. writes the four ``.onnx`` files and ``manifest.json`` (each file's SHA-256,
   the sources, licenses, the converter's versions) into a staging folder,
   verifies it, then swaps it in place of the model directory.

The conversion needs PyTorch and onnx once, at install time. When this
interpreter has neither, it runs in a throwaway environment through ``uv``
(``--isolated``: nothing is installed into Kazma's own environment). The
document worker never downloads anything.

Usage:
  python scripts/ocr_models.py install [--dir DIR] [--sources DIR]
  python scripts/ocr_models.py verify [--dir DIR]

``--dir`` defaults to ``KAZMA_OCR_MODEL_DIR``, else ``<data dir>/models/ocr``.
``--sources`` reuses (and fills) a folder of downloaded source files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS_PATH = ROOT / "kazma-core" / "kazma_core" / "documents" / "ocr" / "neural_models.py"
#: The converter's versions when it runs in a throwaway environment.
EXPORT_REQUIREMENTS = ("torch==2.14.1", "onnx==1.23.2", "onnxruntime==1.31.0", "numpy==2.5.4")
#: ... and its Python, whatever the server runs: these pins were tested on
#: 3.12 (numpy 2.5 needs 3.12 or later). It took the server's own version, and
#: on a 3.11 install the throwaway environment could not be built
#: (2026-10-15). uv fetches this interpreter when it is missing.
EXPORT_PYTHON = "3.12"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
#: Recognizer architecture per role: EasyOCR "generation 1" (ResNet) and
#: "generation 2" (VGG) networks, hidden size of their LSTMs.
ARCHITECTURES = {"rec_arabic": ("gen1", 512), "rec_latin": ("gen2", 256)}
_DOWNLOAD_TIMEOUT = 120
_MAX_DOWNLOAD = 400 * 1024 * 1024


def _specs():
    """``neural_models`` loaded by path: no kazma_core package import (the
    worker image's build stage has none of Kazma's own dependencies)."""
    spec = importlib.util.spec_from_file_location("kazma_ocr_neural_models", SPECS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, *, sha256: str, size: int) -> Path:
    """The pinned file at ``target``: reused when it already matches, else
    downloaded (size-bounded) and checked."""
    if target.is_file() and target.stat().st_size == size and _sha256(target) == sha256:
        return target
    if not url.startswith("https://"):
        raise SystemExit(f"refusing a non-HTTPS source: {url}")
    partial = target.with_suffix(target.suffix + ".partial")
    print(f"downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, partial.open("wb") as handle:  # nosec B310 - https only, pinned URL and SHA-256
        received = 0
        while chunk := response.read(1 << 20):
            received += len(chunk)
            if received > _MAX_DOWNLOAD:
                raise SystemExit(f"{url} is larger than {_MAX_DOWNLOAD} bytes")
            handle.write(chunk)
    if partial.stat().st_size != size or _sha256(partial) != sha256:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"{url} does not match its pinned size and SHA-256; nothing was installed")
    os.replace(partial, target)
    return target


def _member(archive: Path, member: str, target: Path, sha256: str) -> Path:
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.getinfo(member)
        if info.file_size > _MAX_DOWNLOAD:
            raise SystemExit(f"{member} is larger than {_MAX_DOWNLOAD} bytes")
        with zipped.open(info) as source, target.open("wb") as handle:
            shutil.copyfileobj(source, handle)
    if _sha256(target) != sha256:
        target.unlink(missing_ok=True)
        raise SystemExit(f"{member} in {archive.name} does not match its pinned SHA-256")
    return target


# ── the conversion (PyTorch -> ONNX), run where torch and onnx import ───────
#
# EasyOCR's recognition networks, as in easyocr/model/modules.py, model.py and
# vgg_model.py (EasyOCR 1.7.2, Apache-2.0, (c) JaidedAI): only what the two
# models need, so the conversion does not install EasyOCR itself. The adaptive
# average pool over the feature height is written as the mean it is.

def _networks():
    import torch
    from torch import nn

    class BidirectionalLSTM(nn.Module):
        def __init__(self, input_size: int, hidden_size: int, output_size: int) -> None:
            super().__init__()
            self.rnn = nn.LSTM(input_size, hidden_size, bidirectional=True, batch_first=True)
            self.linear = nn.Linear(hidden_size * 2, output_size)

        def forward(self, x):
            recurrent, _ = self.rnn(x)
            return self.linear(recurrent)

    class VGGFeatures(nn.Module):
        def __init__(self, input_channel: int, output_channel: int) -> None:
            super().__init__()
            c = [output_channel // 8, output_channel // 4, output_channel // 2, output_channel]
            self.ConvNet = nn.Sequential(
                nn.Conv2d(input_channel, c[0], 3, 1, 1), nn.ReLU(True), nn.MaxPool2d(2, 2),
                nn.Conv2d(c[0], c[1], 3, 1, 1), nn.ReLU(True), nn.MaxPool2d(2, 2),
                nn.Conv2d(c[1], c[2], 3, 1, 1), nn.ReLU(True),
                nn.Conv2d(c[2], c[2], 3, 1, 1), nn.ReLU(True), nn.MaxPool2d((2, 1), (2, 1)),
                nn.Conv2d(c[2], c[3], 3, 1, 1, bias=False), nn.BatchNorm2d(c[3]), nn.ReLU(True),
                nn.Conv2d(c[3], c[3], 3, 1, 1, bias=False), nn.BatchNorm2d(c[3]), nn.ReLU(True),
                nn.MaxPool2d((2, 1), (2, 1)),
                nn.Conv2d(c[3], c[3], 2, 1, 0), nn.ReLU(True))

        def forward(self, x):
            return self.ConvNet(x)

    class BasicBlock(nn.Module):
        def __init__(self, inplanes: int, planes: int, downsample=None) -> None:
            super().__init__()
            self.conv1 = nn.Conv2d(inplanes, planes, 3, 1, 1, bias=False)
            self.bn1 = nn.BatchNorm2d(planes)
            self.conv2 = nn.Conv2d(planes, planes, 3, 1, 1, bias=False)
            self.bn2 = nn.BatchNorm2d(planes)
            self.relu = nn.ReLU(inplace=True)
            self.downsample = downsample

        def forward(self, x):
            residual = x if self.downsample is None else self.downsample(x)
            out = self.relu(self.bn1(self.conv1(x)))
            return self.relu(self.bn2(self.conv2(out)) + residual)

    class ResNet(nn.Module):
        def __init__(self, input_channel: int, output_channel: int, layers=(1, 2, 5, 3)) -> None:
            super().__init__()
            blocks = [output_channel // 4, output_channel // 2, output_channel, output_channel]
            self.inplanes = output_channel // 8
            self.conv0_1 = nn.Conv2d(input_channel, output_channel // 16, 3, 1, 1, bias=False)
            self.bn0_1 = nn.BatchNorm2d(output_channel // 16)
            self.conv0_2 = nn.Conv2d(output_channel // 16, self.inplanes, 3, 1, 1, bias=False)
            self.bn0_2 = nn.BatchNorm2d(self.inplanes)
            self.relu = nn.ReLU(inplace=True)
            self.maxpool1 = nn.MaxPool2d(2, 2, 0)
            self.layer1 = self._layer(blocks[0], layers[0])
            self.conv1 = nn.Conv2d(blocks[0], blocks[0], 3, 1, 1, bias=False)
            self.bn1 = nn.BatchNorm2d(blocks[0])
            self.maxpool2 = nn.MaxPool2d(2, 2, 0)
            self.layer2 = self._layer(blocks[1], layers[1])
            self.conv2 = nn.Conv2d(blocks[1], blocks[1], 3, 1, 1, bias=False)
            self.bn2 = nn.BatchNorm2d(blocks[1])
            self.maxpool3 = nn.MaxPool2d(2, (2, 1), (0, 1))
            self.layer3 = self._layer(blocks[2], layers[2])
            self.conv3 = nn.Conv2d(blocks[2], blocks[2], 3, 1, 1, bias=False)
            self.bn3 = nn.BatchNorm2d(blocks[2])
            self.layer4 = self._layer(blocks[3], layers[3])
            self.conv4_1 = nn.Conv2d(blocks[3], blocks[3], 2, (2, 1), (0, 1), bias=False)
            self.bn4_1 = nn.BatchNorm2d(blocks[3])
            self.conv4_2 = nn.Conv2d(blocks[3], blocks[3], 2, 1, 0, bias=False)
            self.bn4_2 = nn.BatchNorm2d(blocks[3])

        def _layer(self, planes: int, count: int):
            downsample = None
            if self.inplanes != planes:
                downsample = nn.Sequential(nn.Conv2d(self.inplanes, planes, 1, 1, bias=False), nn.BatchNorm2d(planes))
            layers = [BasicBlock(self.inplanes, planes, downsample)]
            self.inplanes = planes
            layers += [BasicBlock(planes, planes) for _ in range(1, count)]
            return nn.Sequential(*layers)

        def forward(self, x):
            x = self.relu(self.bn0_1(self.conv0_1(x)))
            x = self.relu(self.bn0_2(self.conv0_2(x)))
            x = self.relu(self.bn1(self.conv1(self.layer1(self.maxpool1(x)))))
            x = self.relu(self.bn2(self.conv2(self.layer2(self.maxpool2(x)))))
            x = self.relu(self.bn3(self.conv3(self.layer3(self.maxpool3(x)))))
            x = self.relu(self.bn4_1(self.conv4_1(self.layer4(x))))
            return self.relu(self.bn4_2(self.conv4_2(x)))

    class ResNetFeatures(nn.Module):
        def __init__(self, input_channel: int, output_channel: int) -> None:
            super().__init__()
            self.ConvNet = ResNet(input_channel, output_channel)

        def forward(self, x):
            return self.ConvNet(x)

    class Recognizer(nn.Module):
        def __init__(self, generation: str, channels: int, num_class: int) -> None:
            super().__init__()
            features = ResNetFeatures if generation == "gen1" else VGGFeatures
            self.FeatureExtraction = features(1, channels)
            self.SequenceModeling = nn.Sequential(
                BidirectionalLSTM(channels, channels, channels), BidirectionalLSTM(channels, channels, channels))
            self.Prediction = nn.Linear(channels, num_class)

        def forward(self, image):
            feature = self.FeatureExtraction(image).permute(0, 3, 1, 2).mean(dim=3)
            return self.Prediction(self.SequenceModeling(feature).contiguous())

    return torch, Recognizer


def export(weights: Path, target: Path, generation: str, channels: int) -> dict:
    """``weights`` (EasyOCR .pth) to ONNX at ``target``, checked against PyTorch."""
    import numpy as np
    import onnxruntime as ort

    torch, Recognizer = _networks()
    state = torch.load(weights, map_location="cpu", weights_only=True)
    state = {(key[7:] if key.startswith("module.") else key): value for key, value in state.items()}
    model = Recognizer(generation, channels, int(state["Prediction.weight"].shape[0]))
    model.load_state_dict(state)
    model.eval()
    torch.onnx.export(
        model, (torch.zeros(1, 1, 64, 256),), str(target), input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch", 3: "width"}, "logits": {0: "batch", 1: "steps"}},
        opset_version=17, dynamo=False,
    )
    session = ort.InferenceSession(str(target), providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(20261015)
    worst = 0.0
    for width in (64, 320, 1024, 1600):
        sample = rng.uniform(-1.0, 1.0, (1, 1, 64, width)).astype(np.float32)
        with torch.no_grad():
            expected = model(torch.from_numpy(sample)).numpy()
        got = session.run(None, {"image": sample})[0]
        worst = max(worst, float(np.abs(expected - got).max()))
        if not (expected.argmax(-1) == got.argmax(-1)).all():
            raise SystemExit(f"the ONNX conversion of {weights.name} disagrees with PyTorch at width {width}")
    if worst > 1e-3:
        raise SystemExit(f"the ONNX conversion of {weights.name} differs from PyTorch by {worst}")
    return {"max_abs_difference": worst, "torch": torch.__version__, "onnxruntime": ort.__version__}


def _can_convert() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "onnx", "onnxruntime", "numpy"))


def _convert(weights: Path, target: Path, role: str) -> dict:
    generation, channels = ARCHITECTURES[role]
    if _can_convert():
        return export(weights, target, generation, channels)
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit(
            "Converting EasyOCR's models needs torch and onnx once. Install them into a separate environment, or "
            "install uv (https://docs.astral.sh/uv/) and run this again: it converts in a throwaway environment.")
    command = [uv, "run", "--isolated", "--no-project", "--python", EXPORT_PYTHON]
    if platform.system() != "Windows":
        command += ["--index", TORCH_CPU_INDEX]  # CPU wheels: no CUDA download
    for requirement in EXPORT_REQUIREMENTS:
        command += ["--with", requirement]
    report = target.with_suffix(".export.json")
    command += ["python", str(Path(__file__).resolve()), "_export", str(weights), str(target), generation,
                str(channels), str(report)]
    print("converting in a throwaway environment: " + " ".join(command[:6]) + " ...", flush=True)
    subprocess.run(command, check=True)  # nosec B603 - fixed arguments, uv from PATH
    result = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)
    return result


def install(target: Path, sources: Path | None) -> int:
    specs = _specs()
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.partial-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    keep = sources is not None
    downloads = Path(sources) if keep else Path(tempfile.mkdtemp(prefix="kazma-ocr-sources-"))
    downloads.mkdir(parents=True, exist_ok=True)
    try:
        files: dict[str, dict] = {}
        for role, name in specs.MODEL_FILES.items():
            source = specs.SOURCES[role]
            fetched = _download(source.url, downloads / Path(source.url).name, sha256=source.sha256,
                                size=source.size)
            entry = {"file": name, "source_url": source.url, "source_sha256": source.sha256,
                     "license": source.license, "origin": source.origin}
            if source.member is None:
                shutil.copyfile(fetched, staging / name)
            else:
                weights = _member(fetched, source.member, downloads / source.member, source.member_sha256)
                entry["conversion"] = _convert(weights, staging / name, role)
            entry["size"] = (staging / name).stat().st_size
            entry["sha256"] = _sha256(staging / name)
            files[role] = entry
        manifest = {
            "version": specs.MODEL_SET_VERSION,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": files,
        }
        (staging / specs.MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        status = specs.verify_model_directory(staging)
        if not status.ready:
            raise SystemExit(f"the staged models do not verify: {status.reason}")
        previous = target.parent / f".{target.name}.previous-{os.getpid()}"
        if target.exists():
            os.replace(target, previous)
        os.replace(staging, target)
        shutil.rmtree(previous, ignore_errors=True)
        print(json.dumps(specs.verify_model_directory(target).to_dict(), indent=1))
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not keep:
            shutil.rmtree(downloads, ignore_errors=True)


def _default_directory() -> Path:
    """The model directory the server uses (its .env is read first)."""
    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.documents.ocr.neural_models import model_directory

    return model_directory()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_export":
        # Internal: the conversion step, run inside the throwaway environment.
        weights, target, generation, channels, report = argv[1:6]
        result = export(Path(weights), Path(target), generation, int(channels))
        Path(report).write_text(json.dumps(result), encoding="utf-8")
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=("install", "verify"))
    parser.add_argument("--dir", help="model directory (default: the server's)")
    parser.add_argument("--sources", help="folder to keep and reuse the downloaded sources")
    args = parser.parse_args(argv)
    target = Path(args.dir) if args.dir else _default_directory()
    if args.action == "install":
        return install(target, Path(args.sources) if args.sources else None)
    status = _specs().verify_model_directory(target)
    print(json.dumps(status.to_dict(), indent=1))
    return 0 if status.ready else 1


if __name__ == "__main__":
    sys.exit(main())
