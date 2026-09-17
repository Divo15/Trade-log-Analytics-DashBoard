"""Worker-owned, bounded caches for deterministic market-data preparation."""
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import pandas as pd


class MarketDataLoader:
    """One instance per run, shared by its sequential sweep combinations.

    Callbacks must return DataFrames and must only load/prepare market data.
    Cache keys describe the operation, version, and all non-frame inputs.
    Positions, executions and simulation state must never be cached here.
    """

    def __init__(self, directory=None, *, memory_limit=128 * 1024**2,
                 disk_limit=2 * 1024**3):
        self.memory_limit = memory_limit
        self.disk_limit = disk_limit
        self.memory_bytes = self.disk_bytes = 0
        self._memory = OrderedDict()
        self._disk = OrderedDict()
        self._partitioned = OrderedDict()
        self._temporary = TemporaryDirectory(prefix="market-cache-", dir=directory)
        self.stats = dict(read_hits=0, read_misses=0, prepared_hits=0, prepared_misses=0,
                          partition_hits=0, partition_misses=0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._memory.clear()
        self._disk.clear()
        self._partitioned.clear()
        self.memory_bytes = self.disk_bytes = 0
        self._temporary.cleanup()

    @staticmethod
    def _key(value):
        return sha256(repr(value).encode()).hexdigest()

    def _remember(self, token, frame):
        """Keep a defensive in-memory copy when it fits the shared RAM budget."""
        size = int(frame.memory_usage(index=True, deep=True).sum())
        if size > self.memory_limit:
            return
        if token in self._memory:
            _, old_size = self._memory.pop(token)
            self.memory_bytes -= old_size
        while self._memory and self.memory_bytes + size > self.memory_limit:
            self.memory_bytes -= self._memory.popitem(last=False)[1][1]
        self._memory[token] = (frame.copy(deep=True), size)
        self.memory_bytes += size

    def read_frame(self, paths, key, load):
        """Cache a file-derived frame, invalidating on path/size/mtime changes."""
        sources = []
        for source in sorted(Path(p).resolve() for p in paths):
            stat = source.stat()
            sources.append((str(source), stat.st_size, stat.st_mtime_ns))
        token = self._key((sources, key))
        memory_token = ("read", token)
        if memory_token in self._memory:
            frame, size = self._memory.pop(memory_token)
            self._memory[memory_token] = (frame, size)
            self.stats["read_hits"] += 1
            return frame.copy(deep=True)
        if token in self._disk:
            path, size = self._disk.pop(token)
            self._disk[token] = (path, size)
            self.stats["read_hits"] += 1
            frame = pd.read_parquet(path)
            self._remember(memory_token, frame)
            return frame
        self.stats["read_misses"] += 1
        frame = load()
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Market-data callbacks must return a DataFrame")
        self._remember(memory_token, frame)
        if self.disk_limit > 0:
            path = Path(self._temporary.name) / (token + ".parquet")
            try:
                frame.to_parquet(path, index=True)
                size = path.stat().st_size
                if size <= self.disk_limit:
                    while self._disk and self.disk_bytes + size > self.disk_limit:
                        _, (old, old_size) = self._disk.popitem(last=False)
                        old.unlink()
                        self.disk_bytes -= old_size
                    if self.disk_bytes + size <= self.disk_limit:
                        self._disk[token] = (path, size)
                        self.disk_bytes += size
                    else:
                        path.unlink()
                else:
                    path.unlink()
            except (ImportError, OSError, ValueError, TypeError):
                # Cache storage is optional; data-loading errors still propagate.
                path.unlink(missing_ok=True)
        return frame

    def partitioned_dataset(self, paths, key, build):
        """Build an immutable partitioned dataset once within the disk budget.

        ``build(destination)`` must create Parquet files below ``destination``.
        The returned directory exists until this loader closes or its cache entry
        is evicted. Source size and modification times invalidate the key.
        """
        sources = []
        for source in sorted(Path(p).resolve() for p in paths):
            stat = source.stat()
            sources.append((str(source), stat.st_size, stat.st_mtime_ns))
        token = self._key((sources, key))
        if token in self._partitioned:
            path, size = self._partitioned.pop(token)
            self._partitioned[token] = (path, size)
            self.stats["partition_hits"] += 1
            return path

        self.stats["partition_misses"] += 1
        estimated_size = sum(source[1] for source in sources)
        if self.disk_limit <= 0 or estimated_size > self.disk_limit:
            return None
        while self._disk and self.disk_bytes + estimated_size > self.disk_limit:
            _, (old, old_size) = self._disk.popitem(last=False)
            old.unlink(missing_ok=True)
            self.disk_bytes -= old_size
        while self._partitioned and self.disk_bytes + estimated_size > self.disk_limit:
            _, (old, old_size) = self._partitioned.popitem(last=False)
            shutil.rmtree(old, ignore_errors=True)
            self.disk_bytes -= old_size
        destination = Path(self._temporary.name) / f"partitioned-{token}"
        try:
            build(destination)
            files = [path for path in destination.rglob("*.parquet") if path.is_file()]
            if not files:
                raise ValueError("Partitioned market-data callback created no Parquet files")
            size = sum(path.stat().st_size for path in files)
            if size > self.disk_limit:
                shutil.rmtree(destination, ignore_errors=True)
                return None
            while self._disk and self.disk_bytes + size > self.disk_limit:
                _, (old, old_size) = self._disk.popitem(last=False)
                old.unlink(missing_ok=True)
                self.disk_bytes -= old_size
            while self._partitioned and self.disk_bytes + size > self.disk_limit:
                _, (old, old_size) = self._partitioned.popitem(last=False)
                shutil.rmtree(old, ignore_errors=True)
                self.disk_bytes -= old_size
            self._partitioned[token] = (destination, size)
            self.disk_bytes += size
            return destination
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def prepare_frame(self, key, frames, prepare):
        """Reuse deterministic preparation using complete input-frame fingerprints."""
        fingerprints = []
        for frame in frames:
            fingerprints.append((tuple(frame.columns), tuple(map(str, frame.dtypes)),
                                 tuple(frame.index.names),
                                 sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()))
        token = ("prepared", self._key((key, fingerprints)))
        if token in self._memory:
            frame, size = self._memory.pop(token)
            self._memory[token] = (frame, size)
            self.stats["prepared_hits"] += 1
            return frame.copy(deep=True)
        self.stats["prepared_misses"] += 1
        frame = prepare()
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Market-data callbacks must return a DataFrame")
        self._remember(token, frame)
        return frame
