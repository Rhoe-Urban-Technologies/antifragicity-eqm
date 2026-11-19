import os
import xml.etree.ElementTree as ET

import pytest

import sumo_runner as sr

BASE_CFG = """<configuration>
    <input>
        <net-file value="net.xml"/>
        <additional-files value="a.add.xml, static.hourly.add.xml"/>
    </input>
    <output>
        <tripinfo-output value="trips.xml"/>
    </output>
</configuration>"""


def _value(cfg_path, name):
    return ET.parse(cfg_path).getroot().find(f".//{name}").get("value")


def test_write_run_config(tmp_path):
    base = tmp_path / "base.sumocfg"
    base.write_text(BASE_CFG)
    out = tmp_path / "mod.sumocfg"
    sr.write_run_config(str(base), str(out),
                        outputs={"tripinfo-output": "outputs/t.xml", "log": "outputs/run.log"},
                        extra_additional_files=["outputs/hourly.add.xml"],
                        remove_additional_files=["static.hourly.add.xml"])
    assert _value(out, "tripinfo-output") == "outputs/t.xml"
    assert _value(out, "log") == "outputs/run.log"
    assert ET.parse(out).getroot().find("report/log") is not None
    assert _value(out, "additional-files") == "a.add.xml,outputs/hourly.add.xml"


def test_write_run_config_requires_same_directory(tmp_path):
    base = tmp_path / "base.sumocfg"
    base.write_text(BASE_CFG)
    (tmp_path / "sub").mkdir()
    with pytest.raises(ValueError):
        sr.write_run_config(str(base), str(tmp_path / "sub" / "mod.sumocfg"), outputs={})


def test_hourly_additional_path_is_relative_to_additional(tmp_path):
    add = tmp_path / "outputs" / "hourly.add.xml"
    sr.write_hourly_edgedata_additional(str(add), str(tmp_path / "outputs" / "h.xml"), period=900)
    edge_data = ET.parse(add).getroot().find("edgeData")
    assert edge_data.get("file") == "h.xml"
    assert edge_data.get("period") == "900"


def test_find_sumo_binary_env_override(monkeypatch):
    monkeypatch.setenv("SUMO_BINARY", "/custom/sumo")
    assert sr.find_sumo_binary() == "/custom/sumo"


def test_find_sumo_binary_from_sumo_home(tmp_path, monkeypatch):
    binary = tmp_path / "bin" / "sumo"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.delenv("SUMO_BINARY", raising=False)
    monkeypatch.setenv("SUMO_HOME", str(tmp_path))
    assert sr.find_sumo_binary() == str(binary)


def test_find_sumo_binary_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("SUMO_BINARY", raising=False)
    monkeypatch.setenv("SUMO_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sr, "_MACOS_SUMO_BIN", str(tmp_path / "nowhere"))
    with pytest.raises(FileNotFoundError):
        sr.find_sumo_binary()


def test_run_sumo_progress_bar(tmp_path, monkeypatch, capsys):
    # Fake SUMO: rewrites its step log with "\r" like the real one, plus a normal line.
    fake = tmp_path / "sumo"
    fake.write_text("#!/bin/sh\n"
                    "printf 'Loading net... done.\\n'\n"
                    "printf 'Step #0.00 (x)\\rStep #50.00 (x)\\rStep #100.00 (x)\\n'\n"
                    "printf 'Simulation ended at time: 100.00\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SUMO_BINARY", str(fake))
    cfg = tmp_path / "run.sumocfg"
    cfg.write_text(BASE_CFG)

    bars = []

    class FakeBar:
        def __init__(self, total, **kwargs):
            self.total, self.n, self.written = total, 0, []
            bars.append(self)

        def update(self, k):
            self.n += k

        def write(self, line):
            self.written.append(line)

        def close(self):
            pass

    monkeypatch.setattr(sr, "tqdm", FakeBar)
    sr.run_sumo(str(cfg), begin=0, end=100, progress=True)
    bar, = bars
    assert bar.total == 100 and bar.n == 100
    assert bar.written == ["Loading net... done.", "Simulation ended at time: 100.00"]


@pytest.mark.parametrize("env, pycharm, expected", [
    (None, None, False), (None, "1", True), ("1", None, True), ("0", "1", False),
])
def test_progress_default(monkeypatch, env, pycharm, expected):
    monkeypatch.setattr(sr, "tqdm", object())
    monkeypatch.setattr(sr.sys.stdout, "isatty", lambda: False)
    for name, value in (("SUMO_PROGRESS", env), ("PYCHARM_HOSTED", pycharm)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert sr._progress_default() is expected
