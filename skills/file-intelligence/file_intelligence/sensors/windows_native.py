from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation, SoftwareAsset
from .base import BaseSensor
from .locator import first_executable


class WindowsNativeSensor(BaseSensor):
    sensor_id = "windows_native"
    display_name = "Windows Native Sensor"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("powershell")
        exe = first_executable([configured], ["pwsh.exe", "powershell.exe"])
        configured_nvidia = self.config.get("executables", {}).get("nvidia_smi")
        nvidia_smi = first_executable([configured_nvidia], ["nvidia-smi.exe", "nvidia-smi"])
        return {"powershell": exe, "nvidia_smi": nvidia_smi, "windows": os.name == "nt"}

    def capabilities(self) -> set[Capability]:
        return {
            Capability.FILE_METADATA,
            Capability.STORAGE_USAGE,
            Capability.SOFTWARE_INVENTORY,
            Capability.SYSTEM_INFO,
            Capability.VOLUME_INFO,
            Capability.GPU_STATUS,
            Capability.DISK_HEALTH,
            Capability.PROCESS_ACTIVITY,
        }

    def version(self) -> str | None:
        exe = self.detect()["powershell"]
        if not exe:
            return None
        result = self.runner.run([str(exe), "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"], timeout=10)
        return result.stdout.strip() if result.ok else None

    def source(self) -> dict[str, str]:
        return {"vendor": "Microsoft Windows", "official_url": "https://learn.microsoft.com/powershell/"}

    def health_check(self):
        found = self.detect()
        return self._status(
            version=self.version(),
            available=bool(found["powershell"]) or os.name != "nt",
            health=SensorHealth.HEALTHY if bool(found["powershell"]) or os.name != "nt" else SensorHealth.UNAVAILABLE,
            executable_path=str(found["powershell"]) if found["powershell"] else None,
            details={
                "platform": os.name,
                "mutation_authority": False,
                "nvidia_smi": str(found["nvidia_smi"]) if found["nvidia_smi"] else None,
            },
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability not in self.capabilities():
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        if capability == Capability.FILE_METADATA:
            target = Path(scope)
            if not target.exists():
                return self._failure(capability, scope, "SCOPE_MISSING", "Path does not exist")
            info = target.stat()
            data = {"path": str(target.resolve()), "logical_size": info.st_size, "modified_ns": info.st_mtime_ns, "is_directory": target.is_dir()}
            return self._success(SensorObservation.data(self.sensor_id, capability, scope, data))
        if capability == Capability.STORAGE_USAGE:
            try:
                usage = shutil.disk_usage(scope)
            except OSError as exc:
                return self._failure(capability, scope, "SCOPE_UNAVAILABLE", str(exc))
            data = {"drive": Path(scope).anchor or scope, "total": usage.total, "used": usage.used, "free": usage.free}
            return self._success(SensorObservation.data(self.sensor_id, capability, scope, data))
        if capability == Capability.SOFTWARE_INVENTORY:
            try:
                data = self._software_inventory()
            except (OSError, ImportError) as exc:
                return self._failure(capability, scope, "SENSOR_ERROR", str(exc))
            return self._success(SensorObservation.data(self.sensor_id, capability, scope, data, confidence=0.9))
        if capability == Capability.GPU_STATUS:
            nvidia_smi = self.detect()["nvidia_smi"]
            if not nvidia_smi:
                return self._failure(capability, scope, "EXECUTABLE_MISSING", "nvidia-smi not found")
            fields = "index,name,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu"
            result = self.runner.run(
                [str(nvidia_smi), f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                timeout=float(kwargs.get("timeout", 15)),
            )
            metrics = self._metrics(result)
            if not result.ok:
                return self._failure(capability, scope, "COMMAND_FAILED", result.stderr.strip() or result.exception or "nvidia-smi failed", metrics=metrics)
            rows: list[dict[str, Any]] = []
            keys = fields.split(",")
            for line in result.stdout.splitlines():
                values = [item.strip() for item in line.split(",")]
                if len(values) == len(keys):
                    rows.append(dict(zip(keys, values)))
            raw_ref = self._write_raw("nvidia_smi", result.stdout, ".csv") if result.stdout else None
            return self._success(SensorObservation.data(self.sensor_id, capability, scope, rows, raw_reference=raw_ref, metrics=metrics))
        exe = self.detect()["powershell"]
        if not exe:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "PowerShell not found")
        if capability == Capability.DISK_HEALTH:
            command = (
                "$ErrorActionPreference='Stop';"
                "$d=Get-Disk|Select-Object Number,FriendlyName,SerialNumber,BusType,Size,HealthStatus,OperationalStatus;"
                "$p=Get-PhysicalDisk|Select-Object FriendlyName,SerialNumber,MediaType,BusType,Size,HealthStatus,OperationalStatus;"
                "@{disks=$d;physical_disks=$p}|ConvertTo-Json -Depth 5 -Compress"
            )
        elif capability == Capability.VOLUME_INFO:
            command = (
                "$ErrorActionPreference='Stop';$ve=$null;"
                "try{$v=Get-Volume|Select-Object DriveLetter,FileSystemLabel,FileSystem,DriveType,HealthStatus,Size,SizeRemaining}"
                "catch{$v=@();$ve=$_.Exception.Message};"
                "$p=Get-PSDrive -PSProvider FileSystem|Select-Object Name,Root,Used,Free;"
                "@{volumes=$v;filesystem_drives=$p;volume_error=$ve}|ConvertTo-Json -Depth 5 -Compress"
            )
        elif capability == Capability.SYSTEM_INFO:
            command = (
                "$ErrorActionPreference='Stop';$ci=$null;$cs=$null;"
                "try{$ci=Get-ComputerInfo}catch{};try{$cs=Get-CimInstance Win32_ComputerSystem}catch{};"
                "$cv=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion';"
                "@{computer_name=$env:COMPUTERNAME;windows_product=$cv.ProductName;display_version=$cv.DisplayVersion;"
                "build=$cv.CurrentBuildNumber;architecture=$env:PROCESSOR_ARCHITECTURE;manufacturer=$cs.Manufacturer;"
                "model=$cs.Model;physical_memory=$cs.TotalPhysicalMemory;get_computer_info_available=($null-ne$ci)}|"
                "ConvertTo-Json -Depth 4 -Compress"
            )
        else:
            limit = max(1, min(int(kwargs.get("limit", 200)), 2000))
            command = f"Get-Process|Sort-Object CPU -Descending|Select-Object -First {limit} Id,ProcessName,CPU,WorkingSet64,StartTime|ConvertTo-Json -Depth 4 -Compress"
        result = self.runner.run([str(exe), "-NoProfile", "-NonInteractive", "-Command", command], timeout=float(kwargs.get("timeout", 30)))
        metrics = self._metrics(result)
        if not result.ok:
            text = f"{result.stdout}\n{result.stderr}".casefold()
            code = "ADMIN_REQUIRED" if (("access" in text and "denied" in text) or "permissiondenied" in text or "0x80041003" in text) else "COMMAND_FAILED"
            return self._failure(capability, scope, code, result.stderr.strip() or result.exception or "PowerShell sensor failed", metrics=metrics)
        try:
            raw = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as exc:
            return self._failure(capability, scope, "INVALID_OUTPUT", str(exc), metrics=metrics)
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, self.normalize(raw, capability, scope=scope), metrics=metrics))

    def _software_inventory(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            return []
        import winreg

        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        rows: dict[tuple[str, str | None], dict[str, Any]] = {}
        for hive, path in roots:
            try:
                parent = winreg.OpenKey(hive, path)
            except OSError:
                continue
            with parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        child_name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(parent, child_name) as child:
                            def value(name: str) -> str | None:
                                try:
                                    return str(winreg.QueryValueEx(child, name)[0])
                                except OSError:
                                    return None

                            name = value("DisplayName")
                            if not name:
                                continue
                            version = value("DisplayVersion")
                            asset = SoftwareAsset(
                                name=name,
                                version=version,
                                publisher=value("Publisher"),
                                install_source=value("InstallSource"),
                                install_location=value("InstallLocation"),
                            )
                            rows[(name.casefold(), version)] = {field: getattr(asset, field) for field in asset.__dataclass_fields__}
                    except OSError:
                        continue
        return sorted(rows.values(), key=lambda item: item["name"].casefold())

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        return raw

