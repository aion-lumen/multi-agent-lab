#!/usr/bin/env python3
"""model_swap.py — LM-Studio model loading/unloading via `lms` CLI.

Used by validator_batch.py to switch between lens models sequentially
(48GB RAM doesn't fit two LLMs of this size at once — Direktive
2026-05-26 hard constraint).

Behavior contract (per Direktive):
- All operations log + return bool. NEVER raise — caller decides what to do.
- swap_to(model_id) is the high-level operation: unload anything, load target,
  WAIT until target shows up as loaded (sync). False on timeout.
- unload_plugin_before_first_lens() called once before first lens-swap to free
  RAM from a possibly-resident plugin-executor (qwen3.6-35b).
- Failure (lms not installed, model not registered, OOM, load-timeout) returns
  False; caller is expected to log a warning and skip the lens (NOT fail tranche).

Public API:
    swap_to(model_id, *, timeout_s=240) -> bool
    unload_all_models(*, timeout_s=60) -> bool
    load_model(model_id, *, timeout_s=180) -> bool
    list_loaded() -> list[str]            # parses `lms ps` IDENTIFIER column
    is_model_loaded(model_id) -> bool     # EXACT match, NOT substring
    wait_for_lens_model_loaded(model_id, *, timeout_s=120) -> bool
    unload_plugin_before_first_lens() -> bool
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time

log = logging.getLogger("model_swap")


def _lms_available() -> bool:
    return shutil.which("lms") is not None


def list_loaded() -> list[str]:
    """Return list of currently loaded model IDENTIFIERs by parsing `lms ps`.

    `lms ps` output (verified 2026-05-26):
        IDENTIFIER          MODEL          STATUS  SIZE     CONTEXT  PARALLEL  DEVICE  TTL
        gemma-4-26b-a4b-it-mlx  gemma-4-26b-a4b-it-mlx  IDLE  15.64 GB  4096  1  Local

    Returns the first column (IDENTIFIER) per data row. Empty list if lms not
    installed, no models loaded, or call fails. Format-Drift in future lms
    versions would break this — caller should not depend on this for
    decision-logic beyond is_model_loaded() exact-match.
    """
    if not _lms_available():
        return []
    try:
        out = subprocess.run(
            ["lms", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("lms ps failed: %s", e)
        return []
    if out.returncode != 0:
        return []
    text = out.stdout or ""
    if "No models" in text:
        return []
    candidates: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        # Skip help/usage blurbs that lms prints after the table
        if line.lstrip().startswith(("To ", "Use ", "Run ")):
            continue
        # Skip header line
        first_token = line.split()[0] if line.split() else ""
        if first_token.upper() in ("IDENTIFIER", "LOADED", "NAME", "TYPE"):
            continue
        candidates.append(first_token)
    return candidates


def is_model_loaded(model_id: str) -> bool:
    """EXACT-match check against the IDENTIFIER column of `lms ps`.

    Substring-match would falsely report success: e.g. 'qwen3-30b' would
    match against a loaded 'qwen3-30b-a3b-thinking-2507' even when the
    actual target was a different qwen3-30b variant. Direktive 2026-05-26
    requires exact match.
    """
    if not model_id:
        return False
    return model_id in list_loaded()


def unload_all_models(*, timeout_s: int = 60) -> bool:
    """Unload every loaded model. Returns True on success or no-op."""
    if not _lms_available():
        log.warning("`lms` CLI not found in PATH — cannot swap models")
        return False
    try:
        out = subprocess.run(
            ["lms", "unload", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("lms unload --all failed: %s", e)
        return False
    if out.returncode != 0:
        log.warning(
            "lms unload --all returncode=%d stderr=%s",
            out.returncode, (out.stderr or "")[:200],
        )
        return False
    return True


def load_model(model_id: str, *, timeout_s: int = 180) -> bool:
    """Load model_id into LM-Studio. Returns True on success.
    Loading can take 20-60s for 16-20 GB models — default timeout 180s."""
    if not _lms_available():
        log.warning("`lms` CLI not found in PATH — cannot load %s", model_id)
        return False
    try:
        out = subprocess.run(
            ["lms", "load", model_id],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("lms load %s failed: %s", model_id, e)
        return False
    if out.returncode != 0:
        log.warning(
            "lms load %s returncode=%d stderr=%s",
            model_id, out.returncode, (out.stderr or "")[:200],
        )
        return False
    return True


def wait_for_lens_model_loaded(model_id: str, *, timeout_s: int = 120,
                               poll_interval_s: float = 2.0) -> bool:
    """Poll `lms ps` until model_id appears as loaded (EXACT match).
    Returns True when confirmed loaded; False on timeout.

    `lms load <id>` returns 0 fire-and-forget (verified 2026-05-26: returns in
    ~5s for a 16-20 GB model that actually needs 30-60s to load). This poll
    bridges that gap — without it, the validator hits the LLM with a request
    before the model is ready, the response is empty/Error → JSONDecodeError.
    """
    if not model_id:
        return False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_model_loaded(model_id):
            log.info("wait_for_lens_model_loaded: %s confirmed loaded", model_id)
            return True
        time.sleep(poll_interval_s)
    log.warning("wait_for_lens_model_loaded: %s NOT loaded within %ds", model_id, timeout_s)
    return False


def swap_to(model_id: str, *, timeout_s: int = 240) -> bool:
    """Unload everything, load model_id, WAIT until model is actually loaded.
    Returns True only when model is confirmed loaded.
    Caller logs + skips lens on False (NEVER fail the tranche per Direktive)."""
    if not model_id:
        log.warning("swap_to called with empty model_id — skipping")
        return False
    log.info("model swap: unload all → load %s", model_id)
    # Unload first (best-effort — if nothing was loaded that's fine)
    unload_all_models(timeout_s=min(60, timeout_s // 4))
    if not load_model(model_id, timeout_s=timeout_s):
        return False
    # `lms load` is fire-and-forget — sync via polling.
    return wait_for_lens_model_loaded(model_id, timeout_s=min(120, timeout_s))


def unload_plugin_before_first_lens() -> bool:
    """Explicit unload of any resident model before the first lens-swap.

    Rationale (Direktive 2026-05-26): the email-classification plugin keeps
    qwen3.6-35b resident as executor. If lens-swap is triggered while that
    model is still loaded, the LM-Studio resource-guardrail blocks the next
    model-load (48 GB total RAM, qwen3.6-35b + any lens > limit). This call
    ensures a clean slate before lens 1.

    Returns True on success or no-op. False only if lms is broken.
    """
    log.info("unload plugin (any resident model) before first lens")
    return unload_all_models(timeout_s=30)


if __name__ == "__main__":
    # Diagnostic: print availability + current loaded models
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"lms available: {_lms_available()}")
    print(f"currently loaded: {list_loaded()}")
    # is_model_loaded exact-match sanity (no actual load triggered)
    print(f"is_model_loaded('qwen3-30b') (substring-trap) → {is_model_loaded('qwen3-30b')}")
    print(f"is_model_loaded('') → {is_model_loaded('')}")
