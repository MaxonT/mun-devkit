# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""Read what mdk needs from an ELF file: what it is built for, which shared
libraries it asks for, and which glibc it needs. Pure Python; the file is
untrusted input and every offset is checked against its size."""

import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EM_NAMES = {3: "x86", 40: "arm", 62: "x86-64", 183: "aarch64", 243: "riscv"}
ET_NAMES = {1: "relocatable", 2: "executable", 3: "position-independent executable or shared object", 4: "core"}
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 15, 29
SHT_GNU_VERNEED = 0x6FFFFFFE
READ_LIMIT = 256 * 1024 * 1024


class NotElf(ValueError):
    """The file is not an ELF file mdk can read; the message says why."""


def is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def _cstring(data: bytes, offset: int, limit: int = 4096) -> str:
    if offset < 0 or offset >= len(data):
        raise NotElf("a string lies outside the file")
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        raise NotElf("an unterminated string")
    return data[offset:end].decode("utf-8", "replace")


def read(path: Path) -> Dict[str, Any]:
    """Describe the ELF file at `path`:
    class (32/64), endianness, machine, type, interpreter, needed libraries,
    run paths, the glibc symbol versions it requires and the highest of them."""
    size = Path(path).stat().st_size
    with open(path, "rb") as stream:
        data = stream.read(min(size, READ_LIMIT))
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise NotElf("no ELF header")
    info: Dict[str, Any] = {"size": size, "bits": {1: 32, 2: 64}.get(data[4]),
                            "endian": {1: "little", 2: "big"}.get(data[5])}
    if info["bits"] is None or info["endian"] is None:
        raise NotElf("an invalid ELF identification")
    order = "<" if info["endian"] == "little" else ">"
    etype, machine = struct.unpack_from(order + "HH", data, 16)
    info["machine"] = EM_NAMES.get(machine, f"machine {machine}")
    info["type"] = ET_NAMES.get(etype, f"type {etype}")
    info.update({"interpreter": None, "needed": [], "runpath": [], "glibc_versions": [], "glibc": None,
                 "static": True})
    if info["bits"] != 64 or len(data) < 64:
        return info                      # enough to say what it is; not a MUN program anyway
    phoff, shoff = struct.unpack_from(order + "QQ", data, 32)
    phentsize, phnum, shentsize, shnum = struct.unpack_from(order + "HHHH", data, 54)
    loads: List[Tuple[int, int, int]] = []
    dynamic: Optional[Tuple[int, int]] = None
    for index in range(phnum):
        at = phoff + index * phentsize
        if phentsize < 56 or at + 56 > len(data):
            raise NotElf("program headers lie outside the file")
        ptype, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from(order + "IIQQQQ", data, at)
        if ptype == PT_LOAD:
            loads.append((vaddr, offset, filesz))
        elif ptype == PT_DYNAMIC:
            dynamic = (offset, filesz)
        elif ptype == PT_INTERP:
            info["interpreter"] = _cstring(data, offset)

    def file_offset(address: int) -> int:
        for vaddr, offset, filesz in loads:
            if vaddr <= address < vaddr + filesz:
                return offset + (address - vaddr)
        raise NotElf("an address outside every loaded segment")

    if dynamic is not None:
        info["static"] = False
        offset, filesz = dynamic
        if offset + filesz > len(data):
            raise NotElf("the dynamic section lies outside the file")
        entries = [struct.unpack_from(order + "qQ", data, offset + i * 16) for i in range(filesz // 16)]
        table = next((value for tag, value in entries if tag == DT_STRTAB), None)
        if table is not None:
            strings = file_offset(table)
            for tag, value in entries:
                if tag == DT_NULL:
                    break
                if tag == DT_NEEDED:
                    info["needed"].append(_cstring(data, strings + value))
                elif tag in (DT_RPATH, DT_RUNPATH):
                    info["runpath"] += [part for part in _cstring(data, strings + value).split(":") if part]
    # Symbol versions required: the .gnu.version_r section, when the section
    # headers are present (a stripped executable keeps them).
    for index in range(shnum):
        at = shoff + index * shentsize
        if shentsize < 64 or at + 64 > len(data):
            break
        _name, stype, _flags, _addr, offset, ssize, link, count = struct.unpack_from(order + "IIQQQQII", data, at)
        if stype != SHT_GNU_VERNEED:
            continue
        link_at = shoff + link * shentsize
        if link_at + 64 > len(data):
            break
        strings = struct.unpack_from(order + "Q", data, link_at + 24)[0]
        position = offset
        for _ in range(min(count, 256)):
            if position + 16 > len(data):
                break
            _version, aux_count, _file, aux, following = struct.unpack_from(order + "HHIII", data, position)
            aux_at = position + aux
            for _ in range(min(aux_count, 4096)):
                if aux_at + 16 > len(data):
                    break
                _hash, _flags2, _other, name, aux_next = struct.unpack_from(order + "IHHII", data, aux_at)
                info["glibc_versions"].append(_cstring(data, strings + name))
                if not aux_next:
                    break
                aux_at += aux_next
            if not following:
                break
            position += following
        _ = ssize
    versions = sorted({tuple(int(part) for part in name[6:].split("."))
                       for name in info["glibc_versions"]
                       if name.startswith("GLIBC_") and name[6:].replace(".", "").isdigit()})
    info["glibc_versions"] = sorted(set(info["glibc_versions"]))
    info["glibc"] = ".".join(map(str, versions[-1])) if versions else None
    return info
