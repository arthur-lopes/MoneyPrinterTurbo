"""Manage an operator-initiated Codex device login over private stdio."""

import asyncio
from contextlib import suppress
import json
import re
from tempfile import TemporaryDirectory

from starlette.concurrency import run_in_threadpool

from . import codex_cli


VERIFY_URL = "https://auth.openai.com/codex/device"
START_TIMEOUT = 15
DEVICE_TIMEOUT = 15 * 60


async def send(process, **message):
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()


async def receive(process, matches):
    while line := await process.stdout.readline():
        message = json.loads(line)
        if isinstance(message, dict) and matches(message):
            return message
    raise EOFError("Codex login process exited")


async def stop(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class DeviceLogin:
    def __init__(self):
        self.status = "idle"
        self.challenge = None
        self.lock = asyncio.Lock()
        self.task = None
        self.process = None
        self.directory = None

    async def cleanup(self):
        if self.process:
            await stop(self.process)
            self.process = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None

    async def start(self):
        async with self.lock:
            if self.challenge:
                return self.challenge
            state = await run_in_threadpool(codex_cli.connection_status)
            if state == "chatgpt":
                return {"status": "connected"}
            binary = codex_cli.executable()
            if not binary:
                raise codex_cli.CodexError("Codex CLI is unavailable on the host")
            self.status = "failed"
            try:
                self.directory = TemporaryDirectory(prefix="mpt-codex-login-")
                self.process = await asyncio.create_subprocess_exec(
                    binary,
                    "-c", 'cli_auth_credentials_store="file"',
                    "-c", 'forced_login_method="chatgpt"',
                    "app-server", "--listen", "stdio://",
                    cwd=self.directory.name,
                    env=codex_cli.child_environment(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async with asyncio.timeout(START_TIMEOUT):
                    code, login_id = await self.request_challenge()
            except asyncio.CancelledError:
                await self.cleanup()
                raise
            except (OSError, ValueError, EOFError, TimeoutError):
                await self.cleanup()
                raise codex_cli.CodexError(
                    "Could not start Codex sign-in. Check that device code login "
                    "is enabled in your ChatGPT security settings."
                ) from None
            self.status = "pending"
            self.challenge = {"status": self.status, "url": VERIFY_URL, "code": code}
            self.task = asyncio.create_task(self.complete(login_id))
            return self.challenge

    async def request_challenge(self):
        process = self.process
        await send(
            process,
            id=1,
            method="initialize",
            params={
                "clientInfo": {
                    "name": "moneyprinter",
                    "title": "MoneyPrinter",
                    "version": "1.0.0",
                }
            },
        )
        if "result" not in await receive(process, lambda m: m.get("id") == 1):
            raise ValueError("Codex initialization rejected")
        await send(process, method="initialized")
        await send(
            process,
            id=2,
            method="account/login/start",
            params={"type": "chatgptDeviceCode"},
        )
        response = await receive(process, lambda m: m.get("id") == 2)
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("Codex device login rejected")
        code = result.get("userCode")
        login_id = result.get("loginId")
        if (
            result.get("type") != "chatgptDeviceCode"
            or result.get("verificationUrl") != VERIFY_URL
            or not isinstance(code, str)
            or not re.fullmatch(r"[A-Z0-9]{4,5}-[A-Z0-9]{4,5}", code)
            or not isinstance(login_id, str)
        ):
            raise ValueError("Invalid Codex device challenge")
        return code, login_id

    async def complete(self, login_id):
        state = "failed"
        try:
            async with asyncio.timeout(DEVICE_TIMEOUT):
                message = await receive(
                    self.process,
                    lambda m: m.get("method") == "account/login/completed"
                    and m.get("params", {}).get("loginId") == login_id,
                )
                if message["params"].get("success") is True:
                    if await run_in_threadpool(codex_cli.connection_status) == "chatgpt":
                        state = "connected"
        except TimeoutError:
            state = "expired"
        except (OSError, ValueError, EOFError):
            pass
        finally:
            await self.cleanup()
            self.challenge = None
            self.status = state

    async def cancel(self):
        async with self.lock:
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
            await self.cleanup()
            self.challenge = None
            if self.status != "connected":
                self.status = "idle"
