from __future__ import annotations
import asyncio, contextlib, json, struct, uuid
from collections.abc import AsyncGenerator, Callable, Awaitable
import websockets
from websockets.exceptions import ConnectionClosed

# 6480 float32 samples = 0.405s @ 16kHz — one Ditto online-mode chunk
_CHUNK_SAMPLES = 6480
_CHUNK_BYTES_F32 = _CHUNK_SAMPLES * 4  # 4 bytes per float32


def pcm_int16_to_float32(data: bytes) -> bytes:
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[:n * 2])
    return bytes(struct.pack(f"{n}f", *(s / 32768.0 for s in samples)))


class DittoClient:
    def __init__(self, ws_url: str) -> None:
        self._url = ws_url

    async def speak_streaming(
        self,
        image_path: str,
        pcm_int16: bytes,
        on_frame: Callable[[bytes], Awaitable[None]],
        timeout: float = 60.0,
    ) -> int:
        """Stream PCM chunks to Ditto and call on_frame(jpeg) for each frame as it arrives.

        Sends PCM in 6480-sample float32 chunks (online mode chunk size).
        Receives frames concurrently while sending — true streaming.
        Returns total frames received.
        """
        session_id = uuid.uuid4().hex[:12]
        frames_received = 0

        ws = await asyncio.wait_for(
            websockets.connect(self._url, max_size=50 * 1024 * 1024),
            timeout=15.0,
        )
        try:
            # Init
            await ws.send(json.dumps({"image_path": image_path, "session_id": session_id}))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=30.0))
            if resp.get("status") != "ready":
                raise RuntimeError(f"Ditto init failed: {resp}")

            # Convert all PCM to float32
            f32 = pcm_int16_to_float32(pcm_int16)

            # Send chunks + receive frames concurrently
            send_done = asyncio.Event()

            async def send_chunks() -> None:
                # Send in online-mode chunk sizes (6480 samples = 25920 bytes float32)
                for i in range(0, len(f32), _CHUNK_BYTES_F32):
                    chunk = f32[i:i + _CHUNK_BYTES_F32]
                    # Pad last chunk if needed
                    if len(chunk) < _CHUNK_BYTES_F32:
                        chunk = chunk + b'\x00' * (_CHUNK_BYTES_F32 - len(chunk))
                    await ws.send(chunk)
                    await asyncio.sleep(0)  # yield to allow recv task to run
                # Signal end of audio
                await ws.send(json.dumps({"type": "eof"}))
                send_done.set()

            async def recv_frames() -> None:
                nonlocal frames_received
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        wait = 2.0 if not send_done.is_set() else 5.0
                        msg = await asyncio.wait_for(ws.recv(), timeout=wait)
                        if isinstance(msg, bytes):
                            await on_frame(msg)
                            frames_received += 1
                    except asyncio.TimeoutError:
                        continue   # never exit on timeout; only ConnectionClosed ends the loop
                    except ConnectionClosed:
                        break

            await asyncio.gather(send_chunks(), recv_frames())

        finally:
            with contextlib.suppress(Exception):
                await ws.close()

        return frames_received

    async def speak_once(self, image_path: str, pcm_int16: bytes, timeout: float = 600.0) -> list[bytes]:
        """Collect all frames (for test_ditto.py compatibility)."""
        frames: list[bytes] = []
        async def collect(jpeg: bytes) -> None:
            frames.append(jpeg)
        await self.speak_streaming(image_path, pcm_int16, collect, timeout=timeout)
        return frames

    async def speak_from_stream(
        self,
        image_path: str,
        pcm_gen: AsyncGenerator[bytes, None],
        on_frame: Callable[[bytes], Awaitable[None]],
        timeout: float = 120.0,
    ) -> int:
        """True streaming: feed int16 PCM chunks from an async generator and
        receive JPEG frames concurrently as the server produces them.

        Each yielded chunk should be exactly 6480 × 2 = 12960 bytes (int16).
        The server pads shorter final chunks automatically.
        Returns the total number of frames received.
        """
        session_id = uuid.uuid4().hex[:12]
        frames_received = 0

        ws = await asyncio.wait_for(
            websockets.connect(self._url, max_size=50 * 1024 * 1024, max_queue=None),
            timeout=15.0,
        )
        try:
            await ws.send(json.dumps({"image_path": image_path, "session_id": session_id}))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=30.0))
            if resp.get("status") != "ready":
                raise RuntimeError(f"Ditto init failed: {resp}")

            send_done = asyncio.Event()

            async def send_chunks() -> None:
                async for pcm_int16_chunk in pcm_gen:
                    f32 = pcm_int16_to_float32(pcm_int16_chunk)
                    # Trim to exact chunk size; server pads if shorter
                    if len(f32) > _CHUNK_BYTES_F32:
                        f32 = f32[:_CHUNK_BYTES_F32]
                    await ws.send(f32)
                    await asyncio.sleep(0)  # yield to recv task
                await ws.send(json.dumps({"type": "eof"}))
                send_done.set()

            async def recv_frames() -> None:
                nonlocal frames_received
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        wait = 2.0 if not send_done.is_set() else 1.0
                        msg = await asyncio.wait_for(ws.recv(), timeout=wait)
                        if isinstance(msg, bytes):
                            await on_frame(msg)
                            frames_received += 1
                    except asyncio.TimeoutError:
                        if send_done.is_set() and frames_received > 0:
                            break
                        continue
                    except ConnectionClosed:
                        break

            await asyncio.gather(send_chunks(), recv_frames())

        finally:
            with contextlib.suppress(Exception):
                await ws.close()

        return frames_received
