#!/usr/bin/env python3
# -*- coding: utf-8 -*- vim:shiftwidth=4:expandtab:

import sys
import sysconfig


print("Compiler:", sysconfig.get_config_var('CC'))
print("CFLAGS:", sysconfig.get_config_var('CFLAGS'))

is_debug = hasattr(sys, "gettotalrefcount")  # or sysconfig.get_config_var("Py_DEBUG")
print("Debug Build:", bool(is_debug))

has_pystats = hasattr(sys, "_stats_on")
print("Pystats (Python 3.12+):", has_pystats)

has_jit = bool(sysconfig.get_config_var("Py_JIT")) or "jit" in sys.version.lower()
print("JIT (Python 3.13+):", has_jit)

is_free_threaded_build = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
print("Free-threaded (Python 3.13+):", is_free_threaded_build)

print("Max Unicode:", sys.maxunicode)
print("Unicode width:", "UCS-4 (or flexible/wide)" if sys.maxunicode > 0xFFFF else "UCS-2")
