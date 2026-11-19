"""Single durable consumer with process-owned locking and reconciliation."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from filelock import FileLock, Timeout
import httpx

from . import media
from .adapters import Deferred, Local, OpenAI, ProviderError, Runway
from .store import RuleError


logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, adapters=None):
        self.store = store
        self.stop = Event()
        self.root = store.directory / "assets"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.adapters = (
            adapters
            if adapters is not None
            else {
                "local": Local(),
                "openai": OpenAI(),
                "runway": Runway(store),
            }
        )
        self.thread = Thread(target=self.loop, daemon=True, name="mpt-production")

    def loop(self):
        lock = FileLock(self.store.directory / "worker.lock")
        while not self.stop.is_set():
            try:
                with lock.acquire(timeout=0):
                    self.store.recover()
                    while not self.stop.is_set():
                        if not self.step():
                            self.stop.wait(0.3)
            except Timeout:
                self.stop.wait(1)
            except Exception as exc:
                logger.error("Production queue error (%s)", type(exc).__name__)
                self.stop.wait(1)

    def step(self):
        job = self.store.claim()
        if not job:
            return False
        names = []
        published = False

        def stopped():
            return self.store.cancelled(job["id"], self.stop)

        def progress(value):
            self.store.update_progress(job["id"], value)

        local = job["provider"] == "local"
        try:
            if not job["remote"] and stopped():
                raise ProviderError(
                    "Cancelled before execution", safe=True, cancelled=True
                )
            with TemporaryDirectory(
                prefix=f"work-{job['id']}-", dir=self.store.directory
            ) as scratch:
                result = self.adapters[job["provider"]].run(
                    job, self.root, Path(scratch), stopped, progress
                )
                if result.path:
                    names.append(media.publish(self.root, result.path))
                captions = {}
                for lang, path in result.captions.items():
                    names.append(media.publish(self.root, path))
                    captions[lang] = names[-1]
                published = self.store.complete(
                    job, result.data, names[0] if result.path else None, captions
                )
        except Deferred as exc:
            self.store.defer(job, exc.delay, exc.progress)
        except ProviderError as exc:
            self.store.fail(job, str(exc), safe=exc.safe, cancelled=exc.cancelled)
        except media.Stopped:
            if not (local and self.stop.is_set()):
                self.store.fail(job, "Job stopped", safe=local, cancelled=True)
        except media.MediaError as exc:
            self.store.fail(job, str(exc), safe=local)
        except (httpx.TimeoutException, httpx.TransportError):
            self.store.fail(
                job,
                "Provider response was interrupted. Verify billing before trying again.",
            )
        except RuleError as exc:
            self.store.fail(job, str(exc), safe=local)
        except Exception as exc:
            logger.warning("Production adapter failed (%s)", type(exc).__name__)
            self.store.fail(
                job,
                "Generation did not produce a valid result",
                safe=local,
                retry=local,
            )
        finally:
            if not published:
                for name in names:
                    media.asset_path(self.root, name).unlink(missing_ok=True)
        return True
