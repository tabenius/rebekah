"""Output timestamps, distinct from validation of source observation time."""
import time


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
