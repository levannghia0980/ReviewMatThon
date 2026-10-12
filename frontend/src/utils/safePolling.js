/**
 * safePolling.js
 * Utility polling an toàn với AbortController (timeout) và Exponential Backoff (chống nghẽn mạng).
 */

export function startSafePolling({
  fn,
  interval = 1500,
  maxErrors = 5,
  onError,
  timeoutMs = 4000
}) {
  let isStopped = false;
  let timerId = null;
  let consecutiveErrors = 0;
  let currentController = null;

  const scheduleNext = (delay) => {
    if (isStopped) return;
    timerId = setTimeout(runPoll, delay);
  };

  const runPoll = async () => {
    if (isStopped) return;

    if (currentController) {
      currentController.abort();
    }
    currentController = new AbortController();
    const timeoutHandle = setTimeout(() => {
      if (currentController) currentController.abort();
    }, timeoutMs);

    try {
      const shouldContinue = await fn(currentController.signal);
      clearTimeout(timeoutHandle);
      consecutiveErrors = 0;

      if (isStopped || shouldContinue === false) {
        return;
      }
      scheduleNext(interval);
    } catch (err) {
      clearTimeout(timeoutHandle);
      if (isStopped) return;

      consecutiveErrors += 1;
      // Exponential backoff: Khi lỗi, tăng thời gian đợi thay vì spam dồn dập
      const backoffDelay = Math.min(interval * Math.pow(1.8, consecutiveErrors), 10000);
      
      if (onError) {
        onError(err, consecutiveErrors);
      }

      if (consecutiveErrors >= maxErrors) {
        console.warn(`[SafePolling] Tạm hoãn polling sau ${consecutiveErrors} lần lỗi liên tiếp.`);
        scheduleNext(8000); // Đợi 8s trước khi thăm dò lại một lần
      } else {
        scheduleNext(backoffDelay);
      }
    }
  };

  // Kích hoạt nhịp đầu tiên
  scheduleNext(100);

  return () => {
    isStopped = true;
    if (timerId) clearTimeout(timerId);
    if (currentController) currentController.abort();
  };
}
