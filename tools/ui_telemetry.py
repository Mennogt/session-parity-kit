"""Динамическое число воркеров и телеметрия в панели."""

from __future__ import annotations

import pathlib

# ---------- 1. Пул: масштабирование по значению в meta ----------
pool = pathlib.Path("worker/pool.py")
t = pool.read_text(encoding="utf-8")

if "_scale_loop" not in t:
    t = t.replace(
        """        self._sweepers = [
            asyncio.create_task(self._sweep_jobs()),
            asyncio.create_task(self._sweep_profiles()),
        ]""",
        """        self._sweepers = [
            asyncio.create_task(self._sweep_jobs()),
            asyncio.create_task(self._sweep_profiles()),
            asyncio.create_task(self._scale_loop()),
        ]""",
    )
    t = t.replace(
        """    async def _sweep_jobs(self) -> None:""",
        '''    async def _scale_loop(self) -> None:
        """Подстраивать число воркеров под значение в meta.

        Панель пишет желаемое количество в `worker_target`, пул читает его
        раз в три секунды: новые задачи добавляются, лишние снимаются.
        """
        while not self.stop.is_set():
            await asyncio.sleep(3)
            try:
                raw = self.store.meta_get("worker_target")
                desired = max(1, min(64, int(raw))) if raw else len(self.workers)
            except Exception:
                continue
            while len(self.workers) < desired:
                worker = Worker(
                    self.s, self.store, self.queue, self.profiles, self.flow,
                    self.provider, index=len(self.workers), governor=self.governor,
                )
                self.workers.append(worker)
                self._tasks.append(asyncio.create_task(worker.run(self.stop)))
                log(logr, 20, "воркер добавлен", workers=len(self.workers))
            while len(self.workers) > desired:
                self.workers.pop()
                task = self._tasks.pop()
                task.cancel()
                log(logr, 20, "воркер снят", workers=len(self.workers))

    async def _sweep_jobs(self) -> None:''',
        1,
    )
    pool.write_text(t, encoding="utf-8")
print("пул масштабируется:", "_scale_loop" in t)

# ---------- 2. API: телеметрия и управление ----------
ui = pathlib.Path("api/ui.py")
u = ui.read_text(encoding="utf-8")

if "/ui/system" not in u:
    u = u.replace("import asyncio\nimport json", "import asyncio\nimport json\nimport os", 1)
    u = u.replace("import httpx", "import httpx\nimport psutil", 1)
    SYSTEM = '''@router.get("/ui/system", include_in_schema=False)
async def system(request: Request) -> JSONResponse:
    """Живая телеметрия: CPU, память, потоки, входы в минуту, воркеры."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"ready": False})

    rss = threads = 0
    cpu = 0.0
    processes = 0
    for proc in psutil.process_iter(["cmdline", "memory_info", "num_threads"]):
        cmd = " ".join(proc.info["cmdline"] or [])
        if "uvicorn" in cmd or "worker.main" in cmd:
            processes += 1
            rss += proc.info["memory_info"].rss
            threads += proc.info["num_threads"]
            try:
                cpu += proc.cpu_percent(interval=None)
            except Exception:
                pass

    memory = psutil.virtual_memory()
    target = await asyncio.to_thread(container.store.meta_get, "worker_target")
    success = await asyncio.to_thread(container.store.recent_successes, 60.0)
    workers = await asyncio.to_thread(container.store.active_workers, 30.0)
    depth = await container.queue.depth()
    cpu_count = os.cpu_count() or 1
    return JSONResponse({
        "ready": True,
        "cpu_percent": round(psutil.cpu_percent(interval=None) / cpu_count, 1),
        "cpu_total_percent": round(psutil.cpu_percent(interval=None), 1),
        "cpu_count": cpu_count,
        "ram_used_mb": round(memory.used / 1048576),
        "ram_total_mb": round(memory.total / 1048576),
        "service_rss_mb": round(rss / 1048576, 1),
        "service_threads": threads,
        "processes": processes,
        "logins_per_min": success,
        "workers_active": workers,
        "workers_target": int(target) if target else None,
        "queue_depth": depth,
    })


@router.post("/ui/workers", include_in_schema=False)
async def set_workers(request: Request) -> JSONResponse:
    """Задать желаемое число воркеров (пул подстроится за ~3 секунды)."""
    body = await request.json()
    try:
        count = max(1, min(64, int(body.get("count") or 1)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "нужно целое число"}, status_code=400)
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"error": "сервис не готов"}, status_code=503)
    await asyncio.to_thread(container.store.meta_set, "worker_target", str(count))
    return JSONResponse({"workers_target": count, "note": "применится в течение 3 секунд"})


@router.get("/ui/overview", include_in_schema=False)'''
    u = u.replace('@router.get("/ui/overview", include_in_schema=False)', SYSTEM, 1)
    ui.write_text(u, encoding="utf-8")
print("телеметрия:", "/ui/system" in u, "| управление:", "/ui/workers" in u)
