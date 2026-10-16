"""Renderer entry point: apply the hard allocation cap before library imports."""

import os
import resource

resource.setrlimit(resource.RLIMIT_AS, (int(os.environ["RENDER_MEMORY_BYTES"]),) * 2)
# A crash must not create large, potentially sensitive core dumps.
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

import base64
import gc
import json
import sys

from .errors import InputError
from .rendering import create_chart
from .supervisor import MAX_REPLY_BYTES


def _handle(line: bytes) -> tuple[str, bool]:
    try:
        request = json.loads(line)
        png, metadata = create_chart(request["config"], request["data"])
        reply = json.dumps({"png": base64.b64encode(png).decode("ascii"),
                            "metadata": metadata}, separators=(",", ":"))
        if len(reply) + 1 >= MAX_REPLY_BYTES:
            raise InputError("rendered PNG exceeds the response limit")
        return reply, False
    except MemoryError:
        return '{"error":"renderer memory limit exceeded; simplify the chart","fatal":true}', True
    except InputError as exc:
        return json.dumps({"error": str(exc)}), False
    except Exception:
        return '{"error":"renderer failed; simplify the chart"}', False


def main() -> None:
    for line in sys.stdin.buffer:
        reply, fatal = _handle(line)
        # Figure/canvas cycles retain native pixel buffers until cyclic GC runs.
        # Collect before admitting another chart under the same address-space cap.
        gc.collect()
        sys.stdout.write(reply + "\n")
        sys.stdout.flush()
        del reply, line
        if fatal:
            return


if __name__ == "__main__":
    main()
