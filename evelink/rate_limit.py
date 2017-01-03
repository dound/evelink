"""Rate limit compliance."""
import collections
import threading
import time


class RateLimit(object):
    """Tracks a single rate limit.

    Enforced via a bucket mechanism. Initially, the bucket is full. A drop is
    removed whenever the rate limited action occurs. If no drops remain, an
    action will be blocked until a drop is available for it. A drop is returned
    when an action completes.

    This data structure is thread-safe.
    """
    def __init__(self, limit, window_secs):
        self.limit = limit  # deque doesn't expose maxlen in 2.6
        self.window = window_secs
        self.recents = collections.deque(maxlen=limit)
        self.__lock = threading.Lock()
        self.__cond = threading.Condition(self.__lock)

    def reserve(self):
        """Call this BEFORE you start using a slot. It will block (not return)
        until the rate limit allows another slot to be used.
        """
        with self.__lock:
            self.__forget_old()
            while len(self.recents) == self.limit:
                # cannot proceed until the least recent slot has been released
                while self.recents[0] is None:
                    self.__cond.wait()

                # compute how long we need to sleep until the least recent slot
                # is no longer in our rate limit window
                epoch_okay = self.recents[0] + self.window
                secs_to_wait = epoch_okay - time.time()
                time.sleep(secs_to_wait + 1e-3)
                self.__forget_old()
            # record that a slot is being used
            self.recents.append(None)

    def release(self, ts_finished, does_not_count=False):
        """Call this after a slot is no longer in use.

        If does_not_count is True, then the slot does not count against the
        rate limit.
        """
        # record the finish time in the leftmost pending slot
        with self.__lock:
            if does_not_count:
                self.recents.remove(None)
            else:
                for i, epoch in enumerate(self.recents):
                    if epoch is None:
                        self.recents[i] = ts_finished
                        break
            self.__cond.notify()

    def __forget_old(self):
        """Forget all the uses more than <window> ago.

        Must call while holding the lock.
        """
        now = time.time()
        threshold = now - self.window
        recents = self.recents
        while recents and recents[0] is not None and recents[0] < threshold:
            recents.popleft()


class RateLimitManager(object):
    """Enforces request and error rate limits for the EVE Online XML API."""
    def __init__(self):
        self.request_limit = RateLimit(30, 1)
        self.error_limit = RateLimit(300, 180)

    def block_until_can_start_new_request(self):
        """Call this BEFORE you start a new request. It will block (not return)
        until ALL rate limits allow another request.
        """
        start = time.time()
        self.request_limit.reserve()
        self.error_limit.reserve()  # pessimistic assumption for safety
        return time.time() - start

    def finished(self, success):
        """Call this AFTER you finish a request."""
        ts_finished = time.time()
        self.request_limit.release(ts_finished)
        self.error_limit.release(ts_finished, does_not_count=success)
