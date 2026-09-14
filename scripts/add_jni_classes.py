"""Adds the generated JNI proxy classes that build_aar.py leaves out of the AAR.

build_aar.py packs lib.java/sdk/android/libwebrtc.jar as classes.jar. Since WebRTC
moved to jni_zero, the Java side calls generated proxy classes (PeerConnectionFactoryJni
and so on), and those are not in that jar: an app using the AAR fails at startup with
NoClassDefFoundError: org/webrtc/PeerConnectionFactoryJni. They are compiled into the
per-target jars of the same build, so they are taken from there.

    python3 add_jni_classes.py OUT_DIR AAR
"""

import io
import os
import sys
import zipfile


def wanted(entry: str) -> bool:
    if not entry.endswith(".class"):
        return False
    if entry.startswith("org/jni_zero/"):
        return True
    if not entry.startswith("org/webrtc/"):
        return False
    base = entry.rsplit("/", 1)[-1]
    return "Jni" in base or "GEN_JNI" in base


def main(out_dir: str, aar_path: str) -> int:
    with zipfile.ZipFile(aar_path) as aar:
        entries = {name: aar.read(name) for name in aar.namelist()}
    with zipfile.ZipFile(io.BytesIO(entries["classes.jar"])) as classes:
        merged = {name: classes.read(name) for name in classes.namelist()}

    added = 0
    for root, _, files in os.walk(out_dir):
        for name in files:
            if not name.endswith(".jar"):
                continue
            try:
                jar = zipfile.ZipFile(os.path.join(root, name))
            except zipfile.BadZipFile:
                continue
            with jar:
                for entry in jar.namelist():
                    if entry in merged or not wanted(entry):
                        continue
                    merged[entry] = jar.read(entry)
                    added += 1

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as classes:
        for name, data in merged.items():
            classes.writestr(name, data)
    entries["classes.jar"] = buffer.getvalue()
    with zipfile.ZipFile(aar_path, "w", zipfile.ZIP_DEFLATED) as aar:
        for name, data in entries.items():
            aar.writestr(name, data)

    print(f"added {added} generated JNI classes")
    if "org/webrtc/PeerConnectionFactoryJni.class" not in merged:
        print("org/webrtc/PeerConnectionFactoryJni.class is still missing", file=sys.stderr)
        # Say where it does exist, if anywhere, so the next attempt knows where to look.
        for root, _, files in os.walk(out_dir):
            for name in files:
                path = os.path.join(root, name)
                if "PeerConnectionFactoryJni" in name:
                    print("found file:", path, file=sys.stderr)
                elif name.endswith((".jar", ".srcjar")):
                    try:
                        with zipfile.ZipFile(path) as z:
                            hits = [e for e in z.namelist() if "PeerConnectionFactoryJni" in e]
                    except zipfile.BadZipFile:
                        continue
                    if hits:
                        print("found in", path, hits, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
