"""ADB-only installation, capture and export. No root, reboot, bootloader or system writes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time

PACKAGE = "org.udc.rawprobe"


class Device:
    def __init__(self, adb: Path, serial: str | None = None):
        self.adb = str(adb.resolve())
        probe = subprocess.run([self.adb, "devices"], capture_output=True, text=True, timeout=15, check=True)
        transports = [line.split()[:2] for line in probe.stdout.splitlines()[1:] if len(line.split()) >= 2]
        connected = [s for s, state in transports if state == "device" and (serial is None or s == serial)]
        if len(connected) != 1:
            states = [state for _, state in transports]
            raise RuntimeError(f"Need exactly one authorized ADB transport; matching={len(connected)}, states={states}. Device identifiers are not printed.")
        self.prefix = [self.adb, "-s", connected[0]]
        self.selected_serial = connected[0]

    def call(self, args: list[str], timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(self.prefix + args, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"ADB {args[0]} timed out after {timeout}s; device identifier withheld. Check phone confirmation and installed package state before retrying.") from None
        if check and result.returncode:
            raise RuntimeError((result.stderr.decode("utf-8", "replace") + result.stdout.decode("utf-8", "replace")).replace(self.selected_serial, "<device>"))
        return result

    def shell(self, args: list[str], check: bool = True) -> str:
        # ADB joins shell arguments; quote every token before the remote shell sees it.
        return self.call(["shell", " ".join(shlex.quote(a) for a in args)], check=check).stdout.decode("utf-8", "replace")

    def runs(self) -> set[str]:
        result = self.shell(["run-as", PACKAGE, "ls", "files/captures"], check=False)
        return {line.strip() for line in result.splitlines() if re.fullmatch(r"run_\d+_[a-f0-9]{8}", line.strip())}


def export(device: Device, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "original_export.tar"
    # Preserve binary stdout directly; never route image bytes through a PowerShell text pipeline.
    with archive.open("xb") as stream:
        try:
            result = subprocess.run(device.prefix + ["exec-out", "run-as", PACKAGE, "tar", "-cf", "-", "-C", "files", "captures", "inventories"], stdout=stream, stderr=subprocess.PIPE, timeout=240)
        except subprocess.TimeoutExpired:
            raise TimeoutError("ADB export timed out; device identifier withheld, partial archive retained") from None
    if result.returncode:
        raise RuntimeError("Export failed; partial archive retained: " + result.stderr.decode("utf-8", "replace"))
    destination = output / "device_files"
    destination.mkdir()
    seen = set()
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()) or member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe or duplicate archive entry; original archive retained")
            seen.add(member.name)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, path.open("xb") as sink:
                    while data := source.read(1024 * 1024):
                        sink.write(data)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "export.json").write_text(json.dumps({"schema_version": 1, "archive": archive.name, "sha256": digest, "export_unix_seconds": time.time(), "transport": "adb exec-out run-as (debug app), no root", "device_identifiers_recorded": False}, indent=2), encoding="utf-8")
    logs = device.shell(["logcat", "-d", "-v", "threadtime", "UdcRawProbe:I", "*:S"], check=False)
    (output / "app_logcat.txt").write_text(logs, encoding="utf-8")
    print(json.dumps({"export": str(output.resolve()), "archive_sha256": digest}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", type=Path, required=True)
    p.add_argument("--serial", help="Optional local selection; not stored in capture/export metadata")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    install = commands.add_parser("install"); install.add_argument("apk", type=Path)
    commands.add_parser("enumerate")
    capture = commands.add_parser("capture")
    capture.add_argument("--camera-id")
    capture.add_argument("--format", choices=["RAW_SENSOR", "RAW10"], default="RAW_SENSOR")
    capture.add_argument("--pair", choices=["none", "JPEG", "YUV"], default="none")
    capture.add_argument("--exposure-us", type=int, required=True)
    capture.add_argument("--iso", type=int, required=True)
    capture.add_argument("--count", type=int, default=10)
    capture.add_argument("--scene", choices=["unspecified", "dark", "flat", "point"], default="unspecified")
    capture.add_argument("--condition-verified", action="store_true", help="Operator explicitly confirms the physical scene; never infer from filename")
    capture.add_argument("--note", default="")
    capture.add_argument("--wait-seconds", type=int, default=180)
    exp = commands.add_parser("export"); exp.add_argument("output", type=Path)
    args = p.parse_args()
    device = Device(args.adb, args.serial)
    if args.command == "status":
        print("Exactly one authorized ADB transport selected (identifier withheld).")
    elif args.command == "install":
        # -r preserves app data. Never uninstall or clear captured measurements.
        print(device.call(["install", "-r", "-g", str(args.apk.resolve())], timeout=120).stdout.decode("utf-8", "replace"))
    elif args.command == "enumerate":
        print(device.shell(["am", "start", "-W", "-n", PACKAGE + "/.MainActivity"]))
    elif args.command == "export":
        export(device, args.output)
    elif args.command == "capture":
        if not 1 <= args.count <= 100 or args.exposure_us <= 0 or args.iso <= 0:
            raise ValueError("Invalid count, exposure or ISO")
        before = device.runs()
        command = ["am", "start", "-W", "-n", PACKAGE + "/.MainActivity", "--ez", "auto", "true", "--es", "raw_format", args.format, "--es", "pair", args.pair, "--el", "exposure_us", str(args.exposure_us), "--ei", "iso", str(args.iso), "--ei", "count", str(args.count), "--es", "scene", args.scene, "--es", "note", args.note, "--ez", "condition_verified", str(args.condition_verified).lower()]
        if args.camera_id is not None: command += ["--es", "camera_id", args.camera_id]
        print(device.shell(command))
        deadline = time.monotonic() + args.wait_seconds
        chosen = None
        while time.monotonic() < deadline:
            new = device.runs() - before
            if len(new) > 1: raise RuntimeError("Multiple new runs; cannot safely choose one")
            if new: chosen = next(iter(new))
            if chosen:
                value = device.shell(["run-as", PACKAGE, "cat", "files/captures/" + chosen + "/run.json"], check=False)
                try: state = json.loads(value)
                except json.JSONDecodeError: state = {}
                if state.get("status") and state["status"] != "running":
                    print(json.dumps({k: state.get(k) for k in ("run_id", "status", "capture_complete", "run_succeeded", "completed_frames", "failure_type", "failure_message")}, indent=2))
                    if not state.get("run_succeeded"): raise SystemExit(2)
                    return
            time.sleep(1)
        raise TimeoutError("No terminal run state observed; export retained logs and partial files. A timeout is not a successful capture.")


if __name__ == "__main__":
    main()
