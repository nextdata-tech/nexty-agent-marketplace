"""Put the nxd monorepo source ahead of the installed wheel, when it is newer.

WHY THIS EXISTS. The installed wheel is `nxd.core` v0.41.26. The monorepo source
is v0.41.14x, and the difference matters here: `local/duckdb/storage` — and with
it the `DuckDbOutput` context type the transform binds — does not exist in the
wheel. `storage_context_type_for_driver` has no duckdb case there, so a DuckDB
output port is not constructible at all and the transform can only be proven
with NO ports and its own env-var plumbing.

With the source on `sys.path` first, the Python layer overlays the wheel while
still using its compiled Rust extension, so no build step is required.

HOW TO POINT IT AT A MONOREPO. Set `NXD_MONOREPO_ROOT` to a checkout of the nxd
monorepo:

    NXD_MONOREPO_ROOT=~/projects/nxd python3 examples/e2e/transform_main.py

There is deliberately NO default search path. An earlier version hardcoded the
author's own absolute checkout with a `~/projects/nxd` fallback, which made the
proof unreproducible for everyone else *and* silently weaker: on a machine
without that directory the overlay simply did not happen, the older wheel
answered, and the run still printed green. A guess that fails quietly is worse
than no guess, and this file is the one place that decides what a green E2E run
actually proves.

It is also a cross-repo reference, which the pack's own rule forbids by default
(AGENTS.md, Safety: "no files required outside this repo"). Requiring an explicit
env var keeps that dependency opt-in and visible rather than baked in.

Deliberately NOT silent. Which nxd answered is load-bearing for what a green run
proves, so `describe()` reports the version and where it came from, and the
scripts print it. A run that quietly fell back to the wheel would prove less than
its output claims.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Env var naming a monorepo checkout. No fallback and no search: see the module
#: docstring — a silent miss makes a green run mean less than it appears to.
MONOREPO_ROOT_ENV = "NXD_MONOREPO_ROOT"

_SOURCE_SUBDIRS = ("components/nxd_py/core", "components/nxd_py/data_product")


def monorepo_root() -> Path | None:
    """The configured monorepo checkout, or None when the env var is unset."""
    raw = os.environ.get(MONOREPO_ROOT_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _monorepo_source_paths() -> list[Path]:
    root = monorepo_root()
    if root is None:
        return []
    paths = [root / sub for sub in _SOURCE_SUBDIRS]
    if all(p.is_dir() for p in paths):
        return paths
    # Set but wrong is a MISTAKE, not a fallback. Someone who exported the
    # variable meant to use the source tree, so failing loudly beats handing
    # them a wheel-backed run that proves less than they think it does.
    raise RuntimeError(
        f"{MONOREPO_ROOT_ENV}={root} does not look like an nxd monorepo: "
        f"expected {', '.join(_SOURCE_SUBDIRS)} beneath it. Fix the path, or "
        f"unset the variable to run against the installed wheel."
    )


def use_monorepo_nxd() -> bool:
    """Prepend the monorepo nxd source to `sys.path`. True if it was found.

    Must run BEFORE the first `import nxd`: once the wheel's `nxd` package is
    imported, prepending a path does nothing.
    """
    if "nxd" in sys.modules:
        raise RuntimeError(
            "nxd was already imported; call use_monorepo_nxd() before any "
            "`import nxd`, or the path change has no effect and the run "
            "silently uses the older wheel."
        )
    paths = _monorepo_source_paths()
    if not paths:
        # Not an error — running against the wheel is a legitimate mode, and
        # `--prove-block` and the replay paths do not need duckdb ports. But it
        # is announced, because the caller is about to be handed a weaker proof
        # than the one the scripts advertise.
        print(
            f"  note: {MONOREPO_ROOT_ENV} is unset — running against the "
            f"installed wheel. Port-binding proofs need the monorepo source; "
            f"set {MONOREPO_ROOT_ENV}=/path/to/nxd to enable them.",
            file=sys.stderr,
        )
        return False
    for path in reversed(paths):
        sys.path.insert(0, str(path))
    return True


def describe() -> str:
    """One line naming which nxd is live and whether it supports duckdb ports.

    Reports the version of `nxd.data_product` specifically, NOT of `nxd.core`,
    and the distinction is not pedantry. `nxd` is a namespace package spanning
    several `__path__` entries; only `data_product` ships a `version.py`, while
    `nxd.core`'s number lives in the compiled Rust extension and is written to
    stderr at import ("Loading nxd.core v…") without being exposed as any
    attribute — so it cannot be read back here.

    This previously claimed the number it found WAS `nxd.core`'s, which was
    wrong in both modes: it printed the `data_product` version (0.41.148) while
    the extension announced a different one (0.41.143). A line whose entire job
    is saying which tree answered must not misattribute the version it prints.

    `DuckDbOutput` availability is the load-bearing signal anyway — it is the
    capability the port-binding proof actually needs — and that is probed
    directly rather than inferred from a version.
    """
    import nxd.core  # noqa: PLC0415  - after path setup, on purpose

    version = "unknown"
    version_source = ""
    for entry in getattr(sys.modules["nxd"], "__path__", []):
        candidate = Path(entry) / "version.py"
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split("=", 1)[1].split("#")[0].strip().strip('"\'')
                version_source = str(candidate.parent)
                break
        if version != "unknown":
            break

    try:
        from nxd.core.context import DuckDbOutput  # noqa: F401,PLC0415

        duck = "DuckDbOutput available"
    except ImportError:
        duck = "NO DuckDbOutput (wheel too old; ports cannot be bound)"
    source = "monorepo source" if _monorepo_source_paths() else "installed wheel"
    where = f" from {version_source}" if version_source else ""
    return (
        f"nxd.data_product {version}{where} ({source}) — {duck}"
        f"\n  (nxd.core's own version is printed by its extension at import; "
        f"it is not readable from Python.)"
    )
