"""Makes the AAR's Java classes and native library agree on JNI.

build_aar.py packs lib.java/sdk/android/libwebrtc.jar as classes.jar. Since WebRTC
moved to jni_zero, that is not enough on its own:

1. The Java side calls generated proxy classes (PeerConnectionFactoryJni and so on),
   which are not in that jar - an app failed at startup with NoClassDefFoundError:
   org/webrtc/PeerConnectionFactoryJni. They are compiled into the per-target jars of
   the same build, so they are taken from there.

2. Every proxy calls a native method on one class, org.jni_zero.GEN_JNI, but each
   generate_jni target compiles only its own part of that class. Taking one part gave
   NoSuchMethodError for the others, so the parts are read with javap and compiled
   into one complete GEN_JNI.

3. The native library must export a symbol for each of those methods. A release
   build exports hashed names (Java_J_N_M...) instead, which the proxies never call
   (UnsatisfiedLinkError); the workflow turns that off, and this checks it.

    python3 add_jni_classes.py OUT_DIR AAR
"""

import io
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile


def wanted(entry: str) -> bool:
    if not entry.endswith(".class") or entry.endswith("/GEN_JNI.class"):
        return False
    if entry.startswith("org/jni_zero/"):
        return True
    if not entry.startswith("org/webrtc/"):
        return False
    return "Jni" in entry.rsplit("/", 1)[-1]


def dynamic_symbols(elf: bytes) -> set:
    """Defined dynamic symbol names of a little-endian ELF32/ELF64 shared library."""
    is64 = elf[4] == 2
    if is64:
        shoff, = struct.unpack_from("<Q", elf, 0x28)
        shentsize, shnum = struct.unpack_from("<HH", elf, 0x3A)
    else:
        shoff, = struct.unpack_from("<I", elf, 0x20)
        shentsize, shnum = struct.unpack_from("<HH", elf, 0x2E)
    sections = []
    for i in range(shnum):
        o = shoff + i * shentsize
        if is64:
            _, sh_type, _, _, offset, size, link, _, _, entsize = struct.unpack_from("<IIQQQQIIQQ", elf, o)
        else:
            _, sh_type, _, _, offset, size, link, _, _, entsize = struct.unpack_from("<IIIIIIIIII", elf, o)
        sections.append((sh_type, offset, size, link, entsize))
    names = set()
    for sh_type, offset, size, link, entsize in sections:
        if sh_type != 11:  # SHT_DYNSYM
            continue
        strtab = sections[link][1]
        for o in range(offset, offset + size, entsize):
            if is64:
                name, _, _, shndx = struct.unpack_from("<IBBH", elf, o)
            else:
                name, = struct.unpack_from("<I", elf, o)
                shndx, = struct.unpack_from("<H", elf, o + 14)
            if name == 0 or shndx == 0:
                continue
            end = elf.index(b"\0", strtab + name)
            names.add(elf[strtab + name:end].decode("ascii", "replace"))
    return names


def jni_symbol(cls: str, method: str) -> str:
    def mangle(s: str) -> str:
        return s.replace("_", "_1").replace("/", "_").replace(".", "_").replace(";", "_2").replace("[", "_3")
    return f"Java_{mangle(cls)}_{mangle(method)}"


def merge_gen_jni(parts: list, workdir: str) -> bytes:
    methods = {}
    for i, data in enumerate(parts):
        d = os.path.join(workdir, f"part{i}", "org", "jni_zero")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "GEN_JNI.class"), "wb") as f:
            f.write(data)
        out = subprocess.run(["javap", "-p", "-cp", os.path.join(workdir, f"part{i}"), "org.jni_zero.GEN_JNI"],
                             check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            m = re.match(r"\s*public static native (\S+) (\w+)\((.*)\);", line)
            if m:
                methods[m.group(2)] = (m.group(1), [t.strip() for t in m.group(3).split(",") if t.strip()])
    src = ["package org.jni_zero;", "", "public class GEN_JNI {"]
    for name in sorted(methods):
        ret, params = methods[name]
        args = ", ".join(f"{t} p{i}" for i, t in enumerate(params))
        src.append(f"  public static native {ret} {name}({args});")
    src.append("}")
    srcdir = os.path.join(workdir, "src", "org", "jni_zero")
    os.makedirs(srcdir, exist_ok=True)
    with open(os.path.join(srcdir, "GEN_JNI.java"), "w") as f:
        f.write("\n".join(src) + "\n")
    classes = os.path.join(workdir, "classes")
    subprocess.run(["javac", "--release", "11", "-d", classes, os.path.join(srcdir, "GEN_JNI.java")], check=True)
    print(f"GEN_JNI: {len(methods)} native methods merged from {len(parts)} parts")
    with open(os.path.join(classes, "org", "jni_zero", "GEN_JNI.class"), "rb") as f:
        return f.read(), sorted(methods)


def main(out_dir: str, aar_path: str) -> int:
    with zipfile.ZipFile(aar_path) as aar:
        entries = {name: aar.read(name) for name in aar.namelist()}
    with zipfile.ZipFile(io.BytesIO(entries["classes.jar"])) as classes:
        merged = {name: classes.read(name) for name in classes.namelist()}

    added = 0
    gen_parts = {}
    if "org/jni_zero/GEN_JNI.class" in merged:
        gen_parts[merged["org/jni_zero/GEN_JNI.class"]] = "classes.jar"
    for root, _, files in os.walk(out_dir):
        for name in files:
            if not name.endswith(".jar"):
                continue
            path = os.path.join(root, name)
            try:
                jar = zipfile.ZipFile(path)
            except zipfile.BadZipFile:
                continue
            with jar:
                for entry in jar.namelist():
                    if entry == "org/jni_zero/GEN_JNI.class":
                        gen_parts.setdefault(jar.read(entry), path)
                        continue
                    if entry in merged or not wanted(entry):
                        continue
                    merged[entry] = jar.read(entry)
                    added += 1
    print(f"added {added} generated JNI proxy classes")

    with tempfile.TemporaryDirectory() as work:
        merged["org/jni_zero/GEN_JNI.class"], natives = merge_gen_jni(list(gen_parts), work)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as classes:
        for name, data in merged.items():
            classes.writestr(name, data)
    entries["classes.jar"] = buffer.getvalue()
    with zipfile.ZipFile(aar_path, "w", zipfile.ZIP_DEFLATED) as aar:
        for name, data in entries.items():
            aar.writestr(name, data)

    failed = False
    if "org/webrtc/PeerConnectionFactoryJni.class" not in merged:
        print("org/webrtc/PeerConnectionFactoryJni.class is missing", file=sys.stderr)
        failed = True
    for entry, data in entries.items():
        if not entry.endswith("/libjingle_peerconnection_so.so"):
            continue
        symbols = dynamic_symbols(data)
        missing = [m for m in natives if jni_symbol("org/jni_zero/GEN_JNI", m) not in symbols]
        print(f"{entry}: {len(natives) - len(missing)} of {len(natives)} GEN_JNI natives exported")
        if missing:
            print(f"  missing, e.g. {missing[:5]}; exported JNI names look like "
                  f"{sorted(s for s in symbols if s.startswith('Java_'))[:5]}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
