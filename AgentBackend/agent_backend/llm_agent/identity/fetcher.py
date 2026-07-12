"""Boot-time identity-JSON hydration from Azure Blob Storage.

Mirrors Node's `avatar-fetcher.ts` and LLmLayer's `identity/_fetcher.py`:
download `<identity_name>.json` from `<container_name>/<folder_path>/`
into this package's directory at process startup.

Behaviour matrix:

    connection_string empty + file exists      → no-op (use local)
    connection_string empty + file missing     → raise (broken boot)
    configured                + file exists    → skip (unless force)
    configured                + file missing   → download
    configured                + force_refresh  → download regardless

Design:
  - Idempotent: skip if file exists (unless IDENTITY_FORCE_REFRESH=true)
  - Atomic write: stream to <name>.json.partial, rename on success.
    A crash mid-download leaves a .partial we delete on the next boot.
  - Streamed chunks (the SDK's `download_blob().chunks()` iterator)
    so very large persona files don't peak memory.
  - Async SDK + `async with` so the underlying httpx pool closes
    cleanly when the lifespan exits.
"""

from __future__ import annotations

import time
from pathlib import Path

from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient

from agent_backend.infra import get_logger

log = get_logger(__name__)

_BUNDLE_DIR = Path(__file__).resolve().parent


async def ensure_identity_json(
    *,
    name: str,
    connection_string: str,
    container_name: str,
    folder_path: str = "",
    force_refresh: bool = False,
) -> None:
    """Download `<name>.json` into the local identity bundle directory.

    Args:
        name: Persona stem (no `.json`). Same value as `IDENTITY_NAME`.
        connection_string: Azure Storage account connection string. If
            empty, hydration is skipped — caller is on its own to make
            the local file exist before `get_identity()` is called.
        container_name: Blob container holding the JSONs.
        folder_path: Optional folder/prefix inside the container.
            Joined with the filename via `/`. Empty = root.
        force_refresh: True → re-download even if the local file is fresh.
    """
    target_path = _BUNDLE_DIR / f"{name}.json"
    azure_configured = bool(connection_string) and bool(container_name)

    # Branch 1 — Azure not configured. Tolerate when the file exists
    # locally (dev workflow), fail loudly otherwise.
    if not azure_configured:
        if target_path.exists():
            log.info(
                "[identity] present locally; Azure storage not configured",
                target=str(target_path),
            )
            return
        raise RuntimeError(
            f"Identity JSON missing at {target_path} and Azure storage "
            "not configured. Set connection_string + container_name "
            "(+ folder_path) in .env, or place the JSON at the path "
            "above before starting."
        )

    # Branch 2 — Azure configured + file already cached.
    if target_path.exists() and not force_refresh:
        size = target_path.stat().st_size
        log.info(
            "[identity] already on disk — skipping download "
            "(set IDENTITY_FORCE_REFRESH=true to re-pull)",
            target=str(target_path),
            bytes=size,
        )
        return

    # Branch 3 — fresh download.
    blob_filename = f"{name}.json"
    folder = (folder_path or "").strip("/")
    blob_name = f"{folder}/{blob_filename}" if folder else blob_filename

    partial = target_path.with_suffix(target_path.suffix + ".partial")
    if partial.exists():
        partial.unlink()

    started = time.monotonic()
    log.info(
        "[identity] downloading from Azure Blob Storage",
        target=str(target_path),
        container=container_name,
        blob=blob_name,
    )

    try:
        async with BlobServiceClient.from_connection_string(
            connection_string
        ) as service:
            blob_client = service.get_blob_client(
                container=container_name, blob=blob_name
            )
            try:
                stream = await blob_client.download_blob()
            except ResourceNotFoundError as exc:
                # Most common operator failure: blob is missing or in a
                # different folder. Surface what IS there so the
                # operator can fix it without spelunking in Azure.
                listing = await _list_folder_preview(
                    service, container_name, folder
                )
                raise RuntimeError(
                    "Identity blob not found in Azure.\n"
                    f"  Account:   {service.account_name}\n"
                    f"  Container: {container_name}\n"
                    f"  Looked at: {blob_name}\n"
                    f"  In folder '{folder or '(root)'}' Azure has: {listing}\n"
                    "  Fix one of:\n"
                    f"    1. Upload the file as '{blob_filename}' there, OR\n"
                    "    2. Set IDENTITY_NAME in .env to match the actual "
                    "blob stem, OR\n"
                    "    3. Set folder_path in .env to where the file lives, OR\n"
                    "    4. Fix the container_name typo."
                ) from exc
            except ClientAuthenticationError as exc:
                raise RuntimeError(
                    "Azure auth failed — connection_string is wrong, the "
                    "account key has been rotated, or the account is "
                    "behind a firewall blocking this host."
                ) from exc
            with partial.open("wb") as fh:
                async for chunk in stream.chunks():
                    fh.write(chunk)
    except Exception:
        # Don't leave half-written files around — they'd survive across
        # boots and confuse the next download attempt.
        if partial.exists():
            partial.unlink()
        raise

    bytes_written = partial.stat().st_size
    partial.rename(target_path)

    log.info(
        "[identity] ready",
        target=str(target_path),
        bytes=bytes_written,
        ms=int((time.monotonic() - started) * 1000),
        container=container_name,
        blob=blob_name,
    )


async def _list_folder_preview(
    service: BlobServiceClient, container_name: str, folder: str
) -> str:
    """Best-effort listing of what's actually in the folder, used to
    build a helpful error message when the expected blob is missing.
    Capped at 20 entries; failures degrade to a marker string."""
    try:
        container_client = service.get_container_client(container_name)
        prefix = f"{folder}/" if folder else None
        names: list[str] = []
        async for b in container_client.list_blobs(name_starts_with=prefix):
            n = b.name
            if prefix and n.startswith(prefix):
                n = n[len(prefix):]
            names.append(n)
            if len(names) >= 20:
                names.append("…")
                break
    except Exception as exc:  # noqa: BLE001
        return f"(listing failed: {exc})"
    if not names:
        return "(none — folder is empty or name is wrong)"
    return ", ".join(names)
