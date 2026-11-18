"""Stamp FIRMWARE_GIT_VERSION/FIRMWARE_GIT_COMMIT/FIRMWARE_BUILD_TIME_UTC at
build time, the same way the RP2040 original's CMakeLists.txt does (see its
`git describe --tags --match firmware/v*`/`git rev-parse` calls) — without
these, main.cpp's own `#ifndef` fallbacks leave every version field
"unknown" in the Settings page."""
import datetime
import subprocess

Import("env")


def _git(args, default="unknown"):
    try:
        out = subprocess.run(
            ["git"] + args, capture_output=True, text=True, check=True
        ).stdout.strip()
        return out or default
    except Exception:
        return default


git_version = _git(["describe", "--tags", "--match", "firmware/v*", "--always", "--dirty", "--abbrev=8"])
git_commit = _git(["rev-parse", "--short=8", "HEAD"])
build_time_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _quoted(value):
    return '\\"%s\\"' % value


env.Append(CPPDEFINES=[
    ("FIRMWARE_GIT_VERSION", _quoted(git_version)),
    ("FIRMWARE_GIT_COMMIT", _quoted(git_commit)),
    ("FIRMWARE_BUILD_TIME_UTC", _quoted(build_time_utc)),
])
