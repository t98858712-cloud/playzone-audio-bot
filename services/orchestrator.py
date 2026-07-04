import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("PlayZoneEnterpriseBot")

@dataclass(order=True)
class PriorityTask:
    priority: int  # 0 = إدارة/VIP، 1 = مستخدم عادي
    uid: int = field(compare=False)
    action: Callable[[], Coroutine[Any, Any, Any]] = field(compare=False)
    payload: dict = field(default_factory=dict, compare=False)

class EnterpriseTaskOrchestrator:
    def __init__(self, concurrency_limit: int = 4):
        self.concurrency_limit = concurrency_limit
        self._queue = None
        self._sem = None
        self._workers = []
        self._initialized = False

    def _ensure_initialized(self):
        """ضمان إنشاء أدوات التزامن بأمان داخل الحلقة النشطة والفعالة حالياً"""
        if not self._initialized:
            self._queue = asyncio.PriorityQueue()
            self._sem = asyncio.Semaphore(self.concurrency_limit)
            self._initialized = True
            logger.info("⚡ تم ربط وتهيئة طابور الأولويات الذكي داخل حلقة التزامن النشطة.")

    async def start(self):
        """إطلاق عمال معالجة الطوابير في الخلفية"""
        self._ensure_initialized()
        self._workers = []
        for i in range(self.concurrency_limit):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        logger.info(f"✅ تم إطلاق {len(self._workers)} عمال معالجة بنظام طابور الأولويات والمزامنة السحابية.")

    async def submit(self, task: PriorityTask):
        """حقن مهمة جديدة في نظام الطابور بأمان"""
        self._ensure_initialized()
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

# نسخة عالمية موحدة ومحمية ليعتمد عليها البوت كاملاً
TaskOrchestrator = EnterpriseTaskOrchestrator(concurrency_limit=4)
