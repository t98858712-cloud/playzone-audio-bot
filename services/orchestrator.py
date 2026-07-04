import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("PlayZoneEnterpriseBot")

@dataclass(order=True)
class PriorityTask:
    priority: int  # 0 = إدارة/VIP (أولوية مطلقة)، 1 = مستخدم عادي
    uid: int = field(compare=False)
    action: Callable[[], Coroutine[Any, Any, Any]] = field(compare=False)
    payload: dict = field(default_factory=dict, compare=False)

class EnterpriseTaskOrchestrator:
    def __init__(self, concurrency_limit: int = 4):
        self._queue: asyncio.PriorityQueue[PriorityTask] = asyncio.PriorityQueue()
        self._sem = asyncio.Semaphore(concurrency_limit)
        self._workers = []

    async def start(self):
        """إطلاق عمال معالجة الطوابير في الخلفية"""
        for i in range(self._sem._value):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        logger.info(f"✅ تم إطلاق {len(self._workers)} عمال معالجة بنظام طابور الأولويات.")

    async def submit(self, task: PriorityTask):
        """حقن مهمة جديدة في نظام الطابور"""
        await self._queue.put(task)
        logger.info(f"📥 تم إدراج المهمة للمطلب {task.uid} في طابور الأولويات (مستوى: {task.priority})")

    async def _worker_loop(self, worker_id: int):
        while True:
            task = await self._queue.get()
            try:
                async with self._sem:
                    logger.info(f"⚙️ العامل [{worker_id}] بدأ تنفيذ مهمة المستخدم: {task.uid}")
                    await task.action()
            except Exception as e:
                logger.error(f"❌ خطأ غير معالج أثناء قيام العامل [{worker_id}] بمعالجة مهمة: {e}", exc_info=True)
            finally:
                self._queue.task_done()

# نسخة عالمية موحدة ليعتمد عليها البوت كاملاً
TaskOrchestrator = EnterpriseTaskOrchestrator(concurrency_limit=4)
