"""
Helpers for launching SUMO: locating the binary, writing per-run configs and
running a simulation.
"""
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

try:
    from tqdm import tqdm
except ImportError:  # progress bar is optional
    tqdm = None

_MACOS_SUMO_BIN = "/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo/bin"

# SUMO's step log line, e.g. "Step #1234.00 (1ms ~= 1000.00*RT, ...)".
_STEP_RE = re.compile(r"Step #(\d+(?:\.\d+)?)")

# Config elements that live under <report> rather than <output>.
_REPORT_ELEMENTS = {"log", "message-log", "error-log"}


def find_sumo_binary(name: str = "sumo") -> str:
    """
    Locate a SUMO executable (`sumo`, `sumo-gui`, `netconvert`, ...).

    Lookup order:
      1. $SUMO_BINARY (only for name == "sumo"),
      2. $SUMO_HOME/bin/<name>,
      3. <name> on $PATH,
      4. the default macOS EclipseSUMO framework location.
    """
    if name == "sumo" and os.environ.get("SUMO_BINARY"):
        return os.environ["SUMO_BINARY"]

    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = os.path.join(sumo_home, "bin", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    on_path = shutil.which(name)
    if on_path:
        return on_path

    candidate = os.path.join(_MACOS_SUMO_BIN, name)
    if os.path.isfile(candidate):
        return candidate

    raise FileNotFoundError(
        f"Could not find the SUMO executable '{name}'. Set SUMO_HOME (or "
        f"SUMO_BINARY) or put it on PATH."
    )


def write_hourly_edgedata_additional(additional_path: str, output_file: str,
                                     period: int = 3600, data_id: str = "hourly") -> None:
    """
    Write an additional file that makes SUMO emit edgedata aggregated over
    `period` seconds into `output_file`.

    SUMO resolves `output_file` relative to the additional file's directory,
    so `output_file` is converted to a path relative to `additional_path`.
    """
    add_dir = os.path.dirname(os.path.abspath(additional_path))
    rel_output = os.path.relpath(os.path.abspath(output_file), add_dir)
    root = ET.Element("additional")
    ET.SubElement(root, "edgeData", id=data_id, file=rel_output, period=str(period))
    os.makedirs(add_dir, exist_ok=True)
    ET.ElementTree(root).write(additional_path, encoding="UTF-8", xml_declaration=True)


def write_run_config(base_cfg_path: str, out_cfg_path: str, outputs: dict,
                     extra_additional_files: Iterable[str] = (),
                     remove_additional_files: Iterable[str] = ()) -> None:
    """
    Copy a .sumocfg, redirecting outputs and optionally adding additional files.

    Parameters
    ----------
    base_cfg_path : str
        The config to start from.
    out_cfg_path : str
        Where to write the new config. Must be in the same directory as
        `base_cfg_path`, because SUMO resolves relative paths in a config
        against the config's own directory.
    outputs : dict
        {element name: value}, e.g. {"tripinfo-output": "outputs/x.xml",
        "log": "outputs/x.log"}. Values are relative to the config directory.
        Elements missing from the base config are created.
    extra_additional_files : iterable of str
        Paths (relative to the config directory) appended to
        <additional-files>.
    remove_additional_files : iterable of str
        Additional files to drop from the base config (matched by path or
        basename), e.g. outputs that a derived run must not overwrite.
    """
    base_dir = os.path.dirname(os.path.abspath(base_cfg_path))
    if os.path.dirname(os.path.abspath(out_cfg_path)) != base_dir:
        raise ValueError("out_cfg_path must be in the same directory as base_cfg_path")

    tree = ET.parse(base_cfg_path)
    root = tree.getroot()

    def section(name: str) -> ET.Element:
        element = root.find(name)
        if element is None:
            element = ET.SubElement(root, name)
        return element

    for element_name, value in outputs.items():
        element = root.find(f".//{element_name}")
        if element is None:
            parent = section("report" if element_name in _REPORT_ELEMENTS else "output")
            element = ET.SubElement(parent, element_name)
        element.set("value", value)

    extra = list(extra_additional_files)
    remove = set(remove_additional_files)
    additional = root.find(".//additional-files")
    if additional is not None or extra:
        if additional is None:
            additional = ET.SubElement(section("input"), "additional-files")
        existing = [p.strip() for p in additional.get("value", "").split(",") if p.strip()]
        kept = [p for p in existing if p not in remove and os.path.basename(p) not in remove]
        additional.set("value", ",".join(kept + extra))

    tree.write(out_cfg_path, encoding="UTF-8", xml_declaration=True)


def _cfg_time(cfg_path: str, name: str) -> Optional[float]:
    """Read <begin>/<end> from a .sumocfg, or None if absent."""
    try:
        element = ET.parse(cfg_path).getroot().find(f".//{name}")
    except (ET.ParseError, OSError):
        return None
    if element is None or element.get("value") is None:
        return None
    return float(element.get("value"))


def _progress_default() -> bool:
    """Show a progress bar by default in terminals and IDE run consoles."""
    if tqdm is None:
        return False
    env = os.environ.get("SUMO_PROGRESS")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "")
    return sys.stdout.isatty() or os.environ.get("PYCHARM_HOSTED") == "1"


def run_sumo(cfg_path: str, net_file: Optional[str] = None,
             begin: Optional[int] = None, end: Optional[int] = None,
             extra_args: Iterable[str] = (), stream_output: bool = True,
             progress: Optional[bool] = None, progress_desc: str = "SUMO") -> None:
    """
    Run SUMO with `cfg_path`, optionally overriding the network and horizon.
    Raises RuntimeError if SUMO exits with a non-zero code.

    With `stream_output`, SUMO's output is printed as it runs. If `progress` is
    true (default: when tqdm is installed and stdout is a terminal, the
    PyCharm run console, or $SUMO_PROGRESS=1; $SUMO_PROGRESS=0 disables it), the
    "Step #..." lines are turned into a tqdm progress bar over simulated time
    and all other lines are printed above it.
    """
    command = [find_sumo_binary(), "-c", cfg_path]
    if net_file is not None:
        command += ["--net-file", os.path.abspath(net_file)]
    if begin is not None:
        command += ["--begin", str(begin)]
    if end is not None:
        command += ["--end", str(end)]
    command += list(extra_args)

    if not stream_output:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO exited with code {result.returncode}:\n{result.stderr[-2000:]}")
        return

    print("Running SUMO command:")
    print(" ".join(command))

    if progress is None:
        progress = _progress_default()
    t_begin = begin if begin is not None else (_cfg_time(cfg_path, "begin") or 0.0)
    t_end = end if end is not None else _cfg_time(cfg_path, "end")
    bar = None
    if progress and tqdm is not None and t_end is not None and t_end > t_begin:
        bar = tqdm(total=int(t_end - t_begin), desc=progress_desc, unit="sim s",
                   dynamic_ncols=True, smoothing=0.05)

    # Stream SUMO output line by line. SUMO rewrites the step log with "\r";
    # text mode turns that into separate lines.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if bar is None:
                    print(line, end="")
                    continue
                match = _STEP_RE.search(line)
                if match:
                    done = min(int(float(match.group(1)) - t_begin), bar.total)
                    bar.update(done - bar.n)
                elif line.strip():
                    bar.write(line.rstrip())
        process.wait()
    finally:
        if bar is not None:
            bar.close()
    if process.returncode != 0:
        raise RuntimeError(f"SUMO exited with code {process.returncode}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a SUMO config with a progress bar.")
    parser.add_argument("cfg", help="path to the .sumocfg")
    parser.add_argument("--begin", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    args, passthrough = parser.parse_known_args()
    run_sumo(args.cfg, begin=args.begin, end=args.end, extra_args=passthrough,
             progress=True, progress_desc=os.path.basename(args.cfg))
