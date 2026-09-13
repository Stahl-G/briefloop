"""One FIFO writer per owned pipe, with deadlines that include blocked writes."""
from concurrent.futures import Future, TimeoutError
import json
import queue
import threading
import time


class PipeWriter:
    def __init__(self,stream,on_abort):
        self.stream=stream;self.on_abort=on_abort
        self._lock=threading.Lock();self._closed=False;self._queue=queue.Queue(maxsize=32)
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def send(self,message,timeout=5):
        deadline=time.monotonic()+timeout
        frame=json.dumps(message,ensure_ascii=False)+'\n';future=Future()
        with self._lock:
            if self._closed:raise RuntimeError('会话写入通道已关闭；未自动重发')
            try:self._queue.put_nowait((frame,future))
            except queue.Full:raise RuntimeError('会话发送队列已满，请稍后重试') from None
        try:future.result(timeout=max(0,deadline-time.monotonic()))
        except TimeoutError:
            self.abort(RuntimeError('会话发送超时，接收状态未确认；已停止连接，未自动重发'))
            raise TimeoutError('会话发送超时，接收状态未确认；已停止连接，未自动重发') from None

    def abort(self,error=None):
        error=error or RuntimeError('会话连接已关闭；未自动重发')
        with self._lock:
            if self._closed:return
            self._closed=True
            while True:
                try:item=self._queue.get_nowait()
                except queue.Empty:break
                if item is not None and not item[1].done():item[1].set_exception(error)
            self._queue.put_nowait(None)
        # Closing a TextIO pipe while another thread is in write/flush takes its
        # internal I/O lock and can deadlock. Terminate the owned reader first.
        self.on_abort(error)

    def _run(self):
        while True:
            item=self._queue.get()
            if item is None:
                try:self.stream.close()
                except (OSError,ValueError):pass
                return
            frame,future=item
            try:
                with self._lock:
                    if self._closed:raise RuntimeError('会话写入通道已关闭')
                self.stream.write(frame);self.stream.flush()
                if not future.done():future.set_result(None)
            except (OSError,ValueError,RuntimeError) as exc:
                if not future.done():future.set_exception(RuntimeError('会话发送失败，接收状态未确认；未自动重发'))
                self.abort(RuntimeError('会话发送失败，接收状态未确认；未自动重发'))
