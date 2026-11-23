"""Host capacity for headless game scheduling."""

import ctypes
import os
import struct
from pathlib import Path


def game_capacity() -> tuple[int, int | None]:
    cores = None
    memory = None
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        size = ctypes.c_uint32()
        # RelationProcessorCore returns one variable-length record per active core.
        kernel.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        if size.value:
            buffer = ctypes.create_string_buffer(size.value)
            if kernel.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
                offset = 0
                count = 0
                while offset + 8 <= size.value:
                    _, length = struct.unpack_from("<II", buffer, offset)
                    if length < 8 or offset + length > size.value:
                        break
                    offset += length
                    count += 1
                if offset == size.value:
                    cores = count

        # MEMORYSTATUSEX starts with two DWORDs followed by seven DWORDLONGs.
        status = ctypes.create_string_buffer(64)
        struct.pack_into("<I", status, 0, len(status))
        if kernel.GlobalMemoryStatusEx(status):
            memory = struct.unpack_from("<Q", status, 8)[0]
    else:
        try:
            topology = set()
            for cpu in Path("/proc/cpuinfo").read_text(encoding="ascii").split("\n\n"):
                fields = dict(line.split(":", 1) for line in cpu.splitlines() if ":" in line)
                fields = {key.strip(): value.strip() for key, value in fields.items()}
                if "physical id" in fields and "core id" in fields:
                    topology.add((fields["physical id"], fields["core id"]))
            cores = len(topology)
        except (OSError, UnicodeError):
            pass
        try:
            memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError):
            pass
    return cores or os.cpu_count() or 1, memory if memory and memory > 0 else None
