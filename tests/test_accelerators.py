"""Accelerator selection is portable and never silently ignores a forced device."""
from types import SimpleNamespace

import pytest

from hotpath.benchlib import torch_backend, torch_device, torch_synchronize


class DeviceAPI:
    def __init__(self, available=False):
        self.available = available
        self.synced = []

    def is_available(self):
        return self.available

    def synchronize(self, device=None):
        self.synced.append(device)


def fake_torch(*, cuda=False, xpu=False, mps=False, hip=None):
    return SimpleNamespace(
        cuda=DeviceAPI(cuda),
        xpu=DeviceAPI(xpu),
        mps=DeviceAPI(mps),
        backends=SimpleNamespace(mps=DeviceAPI(mps)),
        version=SimpleNamespace(hip=hip),
    )


def test_device_priority_and_rocm_identity(monkeypatch):
    monkeypatch.delenv("HOTPATH_TORCH_DEVICE", raising=False)
    torch = fake_torch(cuda=True, xpu=True, mps=True, hip="6.3")
    assert torch_device(torch_module=torch) == "cuda"
    assert torch_backend("cuda", torch_module=torch) == "rocm"


@pytest.mark.parametrize("available,expected", [
    ({"xpu": True, "mps": True}, "xpu"),
    ({"mps": True}, "mps"),
    ({}, "cpu"),
])
def test_portable_device_fallback(monkeypatch, available, expected):
    monkeypatch.delenv("HOTPATH_TORCH_DEVICE", raising=False)
    assert torch_device(torch_module=fake_torch(**available)) == expected


def test_forced_device_is_honoured_or_fails(monkeypatch):
    monkeypatch.setenv("HOTPATH_TORCH_DEVICE", "mps")
    assert torch_device(torch_module=fake_torch(mps=True)) == "mps"
    with pytest.raises(RuntimeError, match="not available"):
        torch_device(torch_module=fake_torch())
    with pytest.raises(ValueError, match="unsupported"):
        torch_device("vulkan", torch_module=fake_torch())


@pytest.mark.parametrize("device,api", [("cuda:1", "cuda"), ("xpu:0", "xpu"), ("mps", "mps")])
def test_backend_synchronization(device, api):
    torch = fake_torch(cuda=True, xpu=True, mps=True)
    torch_synchronize(device, torch_module=torch)
    expected = device if api in {"cuda", "xpu"} else None
    assert getattr(torch, api).synced == [expected]


def test_cpu_synchronization_is_a_noop():
    torch = fake_torch(cuda=True, xpu=True, mps=True)
    torch_synchronize("cpu", torch_module=torch)
    assert not torch.cuda.synced and not torch.xpu.synced and not torch.mps.synced
