from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from file_intelligence.intelligence_models import (
    CacheStatus,
    Capability,
    ObservationOutcome,
    SensorHealth,
    SensorObservation,
    SensorStatus,
)
from file_intelligence.aggregate_assets import aggregate_asset_from_storage
from file_intelligence.sensors.base import BaseSensor, CommandResult, CommandRunner, ObservationCache
from file_intelligence.sensors.everything import EverythingSensor
from file_intelligence.sensors.filesystem import FilesystemFallbackSensor
from file_intelligence.sensors.git import GitSensor
from file_intelligence.sensors.github_cli import GitHubCLISensor
from file_intelligence.sensors.policy import StorageSnapshotPolicy, timeline_event_allowed
from file_intelligence.sensors.python_archive import PythonArchiveSensor
from file_intelligence.sensors.registry import CapabilityRegistry, build_default_registry
from file_intelligence.sensors.sevenzip import SevenZipSensor
from file_intelligence.sensors.smartctl import SmartctlSensor
from file_intelligence.sensors.sysinternals import SysinternalsSensor
from file_intelligence.sensors.winget import WingetSensor
from file_intelligence.sensors.wiztree import WizTreeSensor
from file_intelligence.sensors.windows_native import WindowsNativeSensor


def result(
    argv: list[str] | None = None,
    *,
    code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    timeout: bool = False,
    exception: str | None = None,
) -> CommandResult:
    return CommandResult(
        argv=argv or [],
        return_code=code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
        peak_memory_bytes=1024,
        output_bytes=len(stdout.encode()) + len(stderr.encode()),
        timed_out=timeout,
        exception=exception,
    )


class CallbackRunner(CommandRunner):
    def __init__(self, callback: Callable[[list[str]], CommandResult]) -> None:
        self.callback = callback
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs):
        args = [os.fspath(item) for item in argv]
        self.calls.append(args)
        value = self.callback(args)
        value.argv = args
        return value


def executable(temp: Path, name: str) -> Path:
    path = temp / name
    path.write_bytes(b"synthetic")
    return path


class DummySensor(BaseSensor):
    def __init__(self, sensor_id: str, observation: SensorObservation, health: SensorHealth = SensorHealth.HEALTHY):
        super().__init__()
        self.sensor_id = sensor_id
        self.display_name = sensor_id
        self.observation = observation
        self.health = health

    def detect(self):
        return {"available": self.health != SensorHealth.UNAVAILABLE}

    def capabilities(self):
        return {Capability.FILE_SEARCH}

    def health_check(self):
        return self._status(version="1", available=True, health=self.health, executable_path=None, failure_reason=None)

    def collect(self, capability, *, scope, **kwargs):
        return self.observation

    def normalize(self, raw, capability, *, scope):
        return raw

    def version(self):
        return "1"

    def source(self):
        return {"vendor": "test"}


class ModelTests(unittest.TestCase):
    def test_known_aggregate_is_summary_only(self):
        asset = aggregate_asset_from_storage(
            str(Path.cwd() / "node_modules"),
            {"logical_size": 10, "allocated_size": 12, "file_count": 3},
            project_id="project:test",
        )
        self.assertIsNotNone(asset)
        self.assertEqual(asset.role, "PACKAGE_DEPENDENCIES")
        self.assertEqual(asset.internal_indexing, "SUMMARY_ONLY")
        self.assertTrue(asset.rebuildable)

    def test_data_observation_distinguishes_no_data(self):
        obs = SensorObservation.data("s", Capability.FILE_SEARCH, "x", [])
        self.assertEqual(obs.outcome, ObservationOutcome.NO_DATA)
        self.assertEqual(obs.cache_status, CacheStatus.LIVE)

    def test_error_observation_is_not_no_data(self):
        obs = SensorObservation.failure("s", Capability.FILE_SEARCH, "x", "TIMEOUT", "late")
        self.assertEqual(obs.outcome, ObservationOutcome.SENSOR_ERROR)
        self.assertNotEqual(obs.outcome, ObservationOutcome.NO_DATA)

    def test_error_can_carry_last_known_good(self):
        obs = SensorObservation.failure("s", Capability.FILE_SEARCH, "x", "IPC", "bad", last_known_good=[{"path": "old"}])
        self.assertEqual(obs.cache_status, CacheStatus.LAST_KNOWN_GOOD)
        self.assertEqual(obs.normalized_data[0]["path"], "old")

    def test_status_serializes_enum_values(self):
        value = SensorStatus("s", "S", "1", True, SensorHealth.HEALTHY, None, ["X"]).to_dict()
        self.assertEqual(value["health"], "HEALTHY")

    def test_observation_serializes_enum_values(self):
        value = SensorObservation.data("s", Capability.FILE_SEARCH, "x", []).to_dict()
        self.assertEqual(value["outcome"], "NO_DATA")
        self.assertEqual(value["cache_status"], "LIVE")


class CacheTests(unittest.TestCase):
    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cache.json"
            cache = ObservationCache(path)
            cache.remember(SensorObservation.data("s", Capability.FILE_SEARCH, "X", [{"a": 1}]))
            loaded = ObservationCache(path)
            self.assertEqual(loaded.get("s", Capability.FILE_SEARCH, "x"), [{"a": 1}])

    def test_cache_does_not_replace_success_with_error(self):
        cache = ObservationCache()
        cache.remember(SensorObservation.data("s", Capability.FILE_SEARCH, "x", [1]))
        cache.remember(SensorObservation.failure("s", Capability.FILE_SEARCH, "x", "BAD", "bad"))
        self.assertEqual(cache.get("s", Capability.FILE_SEARCH, "x"), [1])


class CommandRunnerTests(unittest.TestCase):
    def test_missing_executable(self):
        value = CommandRunner().run(["definitely_missing_computer_intel_executable"], timeout=1)
        self.assertIsNone(value.return_code)
        self.assertIsNotNone(value.exception)

    def test_timeout(self):
        value = CommandRunner().run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05)
        self.assertTrue(value.timed_out)

    def test_output_size_recorded(self):
        value = CommandRunner().run([sys.executable, "-c", "print('abc')"], timeout=2)
        self.assertTrue(value.ok)
        self.assertGreaterEqual(value.output_bytes, 4)


class EverythingTests(unittest.TestCase):
    def test_unavailable_when_es_missing(self):
        sensor = EverythingSensor({"executables": {}})
        status = sensor.health_check()
        self.assertEqual(status.health, SensorHealth.UNAVAILABLE)

    def test_service_running_does_not_imply_query_usable(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "es.exe")

            def callback(args):
                if "sc.exe" in args[0].lower():
                    return result(stdout="STATE : 4 RUNNING")
                if "-version" in args:
                    return result(stdout="1.1.0.37")
                return result(code=8, stderr="Everything IPC not found")

            sensor = EverythingSensor({"executables": {"es": str(exe)}}, runner=CallbackRunner(callback))
            status = sensor.health_check()
            self.assertEqual(status.health, SensorHealth.SENSOR_ERROR)
            self.assertEqual(status.details["service_status"], "SERVICE_RUNNING")
            self.assertEqual(status.details["query_status"], "SENSOR_FAILURE")

    def test_ipc_failure_keeps_last_known_good(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "es.exe")
            cache = ObservationCache()
            cache.remember(SensorObservation.data("everything", Capability.FILE_SEARCH, raw, [{"path": "old"}]))
            runner = CallbackRunner(lambda args: result(code=8, stderr="Everything IPC not found"))
            sensor = EverythingSensor({"executables": {"es": str(exe)}}, runner=runner, cache=cache)
            obs = sensor.collect(Capability.FILE_SEARCH, scope=raw)
            self.assertEqual(obs.outcome, ObservationOutcome.SENSOR_ERROR)
            self.assertEqual(obs.cache_status, CacheStatus.LAST_KNOWN_GOOD)

    def test_invalid_json_is_sensor_error(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "es.exe")

            def callback(args):
                export = Path(args[args.index("-export-json") + 1])
                export.write_text("{bad", encoding="utf-8")
                return result()

            sensor = EverythingSensor({"executables": {"es": str(exe)}}, runner=CallbackRunner(callback))
            obs = sensor.collect(Capability.FILE_SEARCH, scope=raw)
            self.assertEqual(obs.error["code"], "INVALID_OUTPUT")

    def test_valid_empty_json_is_no_data(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "es.exe")

            def callback(args):
                export = Path(args[args.index("-export-json") + 1])
                export.write_text("[]", encoding="utf-8")
                return result()

            sensor = EverythingSensor({"executables": {"es": str(exe)}}, runner=CallbackRunner(callback))
            obs = sensor.collect(Capability.FILE_SEARCH, scope=raw)
            self.assertEqual(obs.outcome, ObservationOutcome.NO_DATA)


class FilesystemTests(unittest.TestCase):
    def test_bounded_search_returns_data(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "needle.txt").write_text("x", encoding="utf-8")
            sensor = FilesystemFallbackSensor()
            obs = sensor.collect(Capability.FILE_SEARCH, scope=raw, search="needle")
            self.assertEqual(obs.outcome, ObservationOutcome.DATA)

    def test_missing_scope_is_sensor_error(self):
        obs = FilesystemFallbackSensor().collect(Capability.FILE_SEARCH, scope="Z:" + os.sep + "missing")
        self.assertEqual(obs.error["code"], "SCOPE_MISSING")

    def test_partial_output_is_marked(self):
        with tempfile.TemporaryDirectory() as raw:
            for index in range(5):
                (Path(raw) / f"{index}.txt").write_text("x", encoding="utf-8")
            obs = FilesystemFallbackSensor().collect(Capability.FILE_SEARCH, scope=raw, max_entries=1)
            self.assertEqual(obs.error["code"], "PARTIAL_OUTPUT")
            self.assertFalse(obs.metrics["complete"])

    def test_storage_has_logical_size(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "x.bin").write_bytes(b"1234")
            obs = FilesystemFallbackSensor().collect(Capability.STORAGE_TREE, scope=raw)
            self.assertEqual(obs.normalized_data["logical_size"], 4)


class WizTreeTests(unittest.TestCase):
    def test_eula_is_manual_gate(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "WizTree64.exe")
            sensor = WizTreeSensor({"executables": {"wiztree": str(exe)}})
            self.assertEqual(sensor.health_check().health, SensorHealth.MANUAL_STEP_REQUIRED)

    def test_drive_root_blocked(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "WizTree64.exe")
            sensor = WizTreeSensor({"executables": {"wiztree": str(exe)}, "sensors": {"wiztree": {"license_accepted": True}}})
            root = str(Path(raw).anchor)
            if root:
                obs = sensor.collect(Capability.STORAGE_TREE, scope=root)
                self.assertEqual(obs.error["code"], "FULL_DRIVE_SCAN_BLOCKED")

    def test_malformed_export(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            exe = executable(root, "WizTree64.exe")

            def callback(args):
                token = next(arg for arg in args if arg.startswith('/export="'))
                path = Path(token[len('/export="') : -1])
                path.write_text("Bad,Columns\n1,2\n", encoding="utf-8")
                return result()

            sensor = WizTreeSensor(
                {"executables": {"wiztree": str(exe)}, "sensors": {"wiztree": {"license_accepted": True}}},
                runner=CallbackRunner(callback),
            )
            obs = sensor.collect(Capability.STORAGE_TREE, scope=raw)
            self.assertEqual(obs.error["code"], "MALFORMED_EXPORT")


class SevenZipTests(unittest.TestCase):
    def test_corrupt_archive(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            exe = executable(root, "7za.exe")
            archive = root / "bad.zip"
            archive.write_bytes(b"bad")
            sensor = SevenZipSensor({"executables": {"sevenzip": str(exe)}}, runner=CallbackRunner(lambda args: result(code=2, stderr="Data error")))
            obs = sensor.collect(Capability.ARCHIVE_INSPECTION, scope=str(archive))
            self.assertEqual(obs.error["code"], "CORRUPT_ARCHIVE")

    def test_parser_builds_archive_card(self):
        with tempfile.TemporaryDirectory() as raw:
            archive = Path(raw) / "a.zip"
            archive.write_bytes(b"x")
            sensor = SevenZipSensor()
            parsed = sensor.normalize("Path = report.pdf\nSize = 12\n\nPath = model.cas\nSize = 8\n", Capability.ARCHIVE_INSPECTION, scope=str(archive))
            self.assertEqual(parsed["card"]["member_count"], 2)
            self.assertEqual(parsed["card"]["unpacked_size"], 20)


class PythonArchiveTests(unittest.TestCase):
    def test_zip_fallback_lists_without_extracting(self):
        with tempfile.TemporaryDirectory() as raw:
            archive = Path(raw) / "sample.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("docs/report.txt", "report")
            sensor = PythonArchiveSensor()
            obs = sensor.collect(Capability.ARCHIVE_INSPECTION, scope=str(archive))
            self.assertEqual(obs.outcome, ObservationOutcome.DATA)
            self.assertEqual(obs.normalized_data["card"]["member_count"], 1)
            self.assertFalse((Path(raw) / "docs").exists())

    def test_unsupported_format_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sample.rar"
            path.write_bytes(b"not a supported archive")
            obs = PythonArchiveSensor().collect(Capability.ARCHIVE_INSPECTION, scope=str(path))
            self.assertEqual(obs.error["code"], "UNSUPPORTED_ARCHIVE_FORMAT")


class GitTests(unittest.TestCase):
    def test_non_repo(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "git.exe")
            runner = CallbackRunner(lambda args: result(code=128, stderr="not a git repository"))
            obs = GitSensor({"executables": {"git": str(exe)}}, runner=runner).collect(Capability.GIT_STATUS, scope=raw)
        self.assertEqual(obs.error["code"], "NOT_A_REPOSITORY")

    def test_safe_directory_is_invocation_local(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "git.exe")

            def callback(args):
                if "--is-inside-work-tree" in args:
                    return result(stdout="true\n")
                if "rev-parse" in args and "HEAD" in args:
                    return result(stdout="a" * 40 + "\n")
                return result(stdout="")

            runner = CallbackRunner(callback)
            sensor = GitSensor({"executables": {"git": str(exe)}}, runner=runner)
            sensor.collect(Capability.GIT_STATUS, scope=raw)
            self.assertTrue(all("safe.directory=" in " ".join(call) for call in runner.calls))
            self.assertTrue(all("--global" not in call for call in runner.calls))

    def test_repo_card_counts_dirty_files(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "git.exe")

            def callback(args):
                joined = " ".join(args)
                if "rev-parse --is-inside-work-tree" in joined:
                    return result(stdout="true\n")
                if "status --porcelain" in joined:
                    return result(stdout=" M a.py\n?? b.py\n")
                if "branch --show-current" in joined:
                    return result(stdout="main\n")
                if "rev-parse HEAD" in joined:
                    return result(stdout="abc\n")
                if "remote get-url" in joined:
                    return result(stdout="https://example/repo.git\n")
                if " log " in f" {joined} ":
                    return result(stdout="abc\t2026-01-01T00:00:00Z\tmsg\n")
                return result()

            obs = GitSensor({"executables": {"git": str(exe)}}, runner=CallbackRunner(callback)).collect(Capability.GIT_STATUS, scope=raw)
            self.assertTrue(obs.normalized_data["dirty"])
            self.assertEqual(obs.normalized_data["modified_count"], 1)
            self.assertEqual(obs.normalized_data["untracked_count"], 1)


class GitHubTests(unittest.TestCase):
    def test_old_version_auth_not_called(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "gh.exe")
            runner = CallbackRunner(lambda args: result(stdout="gh version 2.96.0\n"))
            status = GitHubCLISensor({"executables": {"gh": str(exe)}}, runner=runner).health_check()
            self.assertEqual(status.health, SensorHealth.DEGRADED)
            self.assertFalse(any(args[1:3] == ["auth", "status"] for args in runner.calls))

    def test_unauthenticated(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "gh.exe")

            def callback(args):
                return result(stdout="gh version 2.97.0\n") if "--version" in args else result(code=1)

            status = GitHubCLISensor({"executables": {"gh": str(exe)}}, runner=CallbackRunner(callback)).health_check()
            self.assertEqual(status.health, SensorHealth.AUTH_UNAVAILABLE)


class WingetTests(unittest.TestCase):
    def test_unavailable(self):
        status = WingetSensor({"executables": {}}).health_check()
        self.assertEqual(status.health, SensorHealth.UNAVAILABLE)

    def test_source_agreement_is_manual(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "winget.exe")
            sensor = WingetSensor({"executables": {"winget": str(exe)}}, runner=CallbackRunner(lambda args: result(code=1, stderr="source agreement required")))
            obs = sensor.collect(Capability.SOFTWARE_INVENTORY, scope="machine")
            self.assertEqual(obs.error["code"], "MANUAL_STEP_REQUIRED")


class SmartctlTests(unittest.TestCase):
    def test_admin_required(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "smartctl.exe")
            sensor = SmartctlSensor({"executables": {"smartctl": str(exe)}}, runner=CallbackRunner(lambda args: result(code=1, stderr="Access is denied; administrator rights required")))
            obs = sensor.collect(Capability.DISK_HEALTH, scope="machine")
            self.assertEqual(obs.error["code"], "ADMIN_REQUIRED")

    def test_nvme_card(self):
        payload = {
            "device": {"name": "/dev/sda", "protocol": "NVMe"},
            "model_name": "Disk",
            "user_capacity": {"bytes": 100},
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {"temperature": 42, "percentage_used": 7, "critical_warning": 0},
        }
        card = SmartctlSensor().normalize(payload, Capability.DISK_HEALTH, scope="/dev/sda")
        self.assertEqual(card["health"], "PASS")
        self.assertEqual(card["wear_life"], 93.0)

    def test_scan_device_type_is_forwarded(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "smartctl.exe")

            def callback(args):
                if "--scan" in args:
                    return result(stdout='{"devices":[{"name":"/dev/sda","type":"nvme"}]}')
                return result(stdout='{"device":{"name":"/dev/sda","protocol":"NVMe"},"smart_status":{"passed":true}}')

            runner = CallbackRunner(callback)
            sensor = SmartctlSensor({"executables": {"smartctl": str(exe)}}, runner=runner)
            obs = sensor.collect(Capability.DISK_HEALTH, scope="machine")
            self.assertEqual(obs.outcome, ObservationOutcome.DATA)
            self.assertIn("-d", runner.calls[1])
            self.assertIn("nvme", runner.calls[1])


class WindowsNativeTests(unittest.TestCase):
    def test_nvidia_smi_is_passive_and_normalized(self):
        with tempfile.TemporaryDirectory() as raw:
            exe = executable(Path(raw), "nvidia-smi.exe")
            runner = CallbackRunner(lambda args: result(stdout="0, NVIDIA RTX 5070, 581.29, 12227, 100, 44, 3\n"))
            sensor = WindowsNativeSensor({"executables": {"nvidia_smi": str(exe)}}, runner=runner)
            obs = sensor.collect(Capability.GPU_STATUS, scope="machine")
            self.assertEqual(obs.outcome, ObservationOutcome.DATA)
            self.assertEqual(obs.normalized_data[0]["name"], "NVIDIA RTX 5070")
            self.assertIn("--query-gpu=index,name,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu", runner.calls[0])


class SysinternalsTests(unittest.TestCase):
    def test_eula_gate_never_executes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in ("FindLinks64.exe", "Junction64.exe", "handle64.exe", "sigcheck64.exe"):
                executable(root, name)
            runner = CallbackRunner(lambda args: result())
            config = {"executables": {"sysinternals_dir": str(root)}, "sensors": {"sysinternals": {"eula_accepted": False}}}
            sensor = SysinternalsSensor(config, runner=runner)
            obs = sensor.collect(Capability.HARDLINK_RESOLUTION, scope=str(root / "x"))
            self.assertEqual(obs.error["code"], "MANUAL_STEP_REQUIRED")
            self.assertEqual(runner.calls, [])

    def test_diagnostic_always_disabled_without_explicit_workflow(self):
        obs = SysinternalsSensor().collect(Capability.FILE_ACTIVITY_TRACE, scope="x")
        self.assertEqual(obs.error["code"], "DIAGNOSTIC_DISABLED")


class RegistryTests(unittest.TestCase):
    def test_configured_capability_preference_is_honored(self):
        registry = build_default_registry(
            {"capability_preferences": {"FILE_SEARCH": ["filesystem_fallback", "everything"]}}
        )
        self.assertEqual(registry.providers(Capability.FILE_SEARCH)[0].sensor_id, "filesystem_fallback")

    def test_fallback_after_sensor_error(self):
        registry = CapabilityRegistry({Capability.FILE_SEARCH: ["primary", "fallback"]})
        registry.register(DummySensor("primary", SensorObservation.failure("primary", Capability.FILE_SEARCH, "x", "BAD", "bad")))
        registry.register(DummySensor("fallback", SensorObservation.data("fallback", Capability.FILE_SEARCH, "x", [{"ok": True}])))
        obs = registry.request(Capability.FILE_SEARCH, scope="x")
        self.assertEqual(obs.sensor_id, "fallback")
        self.assertEqual(obs.upstream_errors[0]["sensor_id"], "primary")

    def test_no_data_stops_fallback(self):
        registry = CapabilityRegistry({Capability.FILE_SEARCH: ["primary", "fallback"]})
        registry.register(DummySensor("primary", SensorObservation.data("primary", Capability.FILE_SEARCH, "x", [])))
        registry.register(DummySensor("fallback", SensorObservation.data("fallback", Capability.FILE_SEARCH, "x", [{"wrong": True}])))
        obs = registry.request(Capability.FILE_SEARCH, scope="x")
        self.assertEqual(obs.sensor_id, "primary")
        self.assertEqual(obs.outcome, ObservationOutcome.NO_DATA)

    def test_unavailable_provider_is_skipped(self):
        registry = CapabilityRegistry({Capability.FILE_SEARCH: ["primary", "fallback"]})
        registry.register(DummySensor("primary", SensorObservation.data("primary", Capability.FILE_SEARCH, "x", []), SensorHealth.UNAVAILABLE))
        registry.register(DummySensor("fallback", SensorObservation.data("fallback", Capability.FILE_SEARCH, "x", [{"ok": True}])))
        obs = registry.request(Capability.FILE_SEARCH, scope="x")
        self.assertEqual(obs.sensor_id, "fallback")

    def test_capability_unavailable_is_error(self):
        registry = CapabilityRegistry({Capability.FILE_SEARCH: []})
        obs = registry.request(Capability.FILE_SEARCH, scope="x")
        self.assertEqual(obs.outcome, ObservationOutcome.SENSOR_ERROR)
        self.assertEqual(obs.error["code"], "CAPABILITY_UNAVAILABLE")


class PolicyTests(unittest.TestCase):
    def test_user_request_runs_snapshot(self):
        self.assertTrue(StorageSnapshotPolicy().should_run(reason="USER_REQUEST", last_success=datetime.now(timezone.utc)))

    def test_daily_maintenance_does_not_run_storage_snapshot(self):
        self.assertFalse(StorageSnapshotPolicy().should_run(reason="FILE_MAINTENANCE", last_success=None))

    def test_weekly_cadence(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(StorageSnapshotPolicy().should_run(reason="PERIODIC", last_success=now - timedelta(days=8), now=now))
        self.assertFalse(StorageSnapshotPolicy().should_run(reason="PERIODIC", last_success=now - timedelta(days=1), now=now))

    def test_package_level_events_are_suppressed(self):
        self.assertFalse(timeline_event_allowed("SOFTWARE_UPDATED", package_level=True))
        self.assertTrue(timeline_event_allowed("SOFTWARE_UPDATED", package_level=False))


if __name__ == "__main__":
    unittest.main()
