"""Record numerical libraries actually loaded by the CPU reference chain."""
import os
import platform


def reference_environment():
    import numpy as np
    import scipy
    from scipy.linalg import lapack
    try:
        from threadpoolctl import threadpool_info
    except ImportError as exc:
        raise RuntimeError("CPU reference capture requires threadpoolctl") from exc
    libraries = threadpool_info()
    blas = [lib for lib in libraries if lib.get("user_api") == "blas"]
    if not blas or any(not lib.get("version") or not lib.get("num_threads") for lib in blas):
        raise RuntimeError("cannot identify loaded BLAS versions and thread counts")
    return {"numpy": np.__version__, "scipy": scipy.__version__,
            "python": platform.python_version(), "machine": platform.machine(),
            "libraries": libraries,
            "thread_environment": {k: os.environ.get(k) for k in
                ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}}
